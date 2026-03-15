#!/usr/bin/env python3
"""
Sequential Job Queue Runner — v7 (Continuous Loop + Signal Chat)
================================================================
Runs ALL jobs sequentially in a continuous loop with zero GPU idle time.
The full queue (scrape → infra → analysis → think → market → report)
runs back-to-back. HA observations interleaved every ~50 jobs.
When the cycle finishes, it immediately starts the next one.

Signal Chat: Between every job, checks for incoming Signal messages from
the owner. If found, processes each with the LLM (including shell command
tool use) and replies before continuing with the next scheduled job.
This gives the owner interactive access without any GPU concurrency.

No idle windows, no daytime/nightly distinction, no pauses.
Scrape-only jobs skip the Ollama pre-flight check (no LLM needed).
No openclaw dependency — signal-cli runs as standalone systemd service.

GPU idle detection (for --daytime mode only):
  Checks GPU clock via sysfs (pp_dpm_sclk) + Ollama /api/ps.
  - GPU clock at 1000MHz → idle (model loaded but not computing)
  - GPU clock at 1500-2000MHz → actively generating → busy
  - No model loaded → definitely idle

Usage:
    python3 queue-runner.py              # Continuous loop (default)
    python3 queue-runner.py --dry-run    # Show queue order without running
    python3 queue-runner.py --once       # Run one cycle and exit
    python3 queue-runner.py --nightly    # Alias for --once
    python3 queue-runner.py --daytime    # Legacy: daytime fill mode with GPU idle checks

Deployed to /opt/netscan/queue-runner.py on bc250.
"""

import subprocess
import json
import os
import re
import shlex
import threading
import time
import sys
import signal
import socket
import urllib.request
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

# ─── Configuration ──────────────────────────────────────────────────────────
JOBS_JSON = Path("/opt/netscan/data/jobs.json")
STATE_FILE = Path("/opt/netscan/data/queue-runner-state.json")
LOG_FILE = Path("/tmp/queue-runner.log")

# Legacy idle window (only used in --daytime mode)
IDLE_START = 9   # 09:00
IDLE_END = 15    # 15:00

# Default timeout for jobs with timeoutSeconds=0
DEFAULT_TIMEOUT_S = 3600   # 60 min — generous for deep thinking

# Per-category timeout caps (empty — let jobs think as long as they need)
# Previous caps (lore-: 600, repo-scan: 1500) were choking analysis quality.
CATEGORY_TIMEOUT_CAPS = {}

# Extra buffer on CLI timeout beyond job's own timeout
CLI_BUFFER_MS = 120_000    # 2 min

# Market analysis preferred after this hour
MARKET_AFTER_HOUR = 20     # 20:00

# Batch settings
NIGHTLY_START_HOUR = 23    # Start nightly batch at 23:00 (legacy, used in --daytime)
NIGHTLY_MAX_HOURS = 0      # 0 = unlimited — run ALL jobs, never skip
HA_INTERLEAVE_EVERY = 50   # Interleave HA observations every N jobs (~2-3h)
DAYTIME_HA_CHECK_S = 1800  # Check GPU idle every 30min (--daytime mode only)

# Pause between cycles (0 = continuous loop)
INTER_CYCLE_PAUSE_S = 0    # No pause — immediately start next cycle

# Opportunistic settings
OPP_CHECK_INTERVAL_S = 5400   # 90 min — how often to wake during idle window
OPP_RETRY_DELAY_S = 300       # 5 min — retry if GPU was busy
GPU_IDLE_THRESHOLD_S = 120    # Model expires within 2 min → idle for 3+ min
GPU_CLK_IDLE_MHZ = 1200       # GPU clock below this = not computing (idle=1000)
GPU_SCLK_PATH = "/sys/class/drm/card1/device/pp_dpm_sclk"

# Ollama API
OLLAMA_URL = "http://localhost:11434"

# Health / resilience
OLLAMA_HEALTH_TIMEOUT_S = 10   # Timeout for ollama health checks
OLLAMA_RECOVER_WAIT_S = 60     # Wait after ollama restart before continuing
NETWORK_CHECK_INTERVAL_S = 300 # How often to check if network is OK during waits
MAX_CONSECUTIVE_FAILURES = 5   # After this many job failures, pause & health-check
GPU_BUSY_MAX_WAIT_S = 3600     # Max time to wait for GPU idle before forcing cycle reset
SD_NOTIFY = 'NOTIFY_SOCKET' in os.environ  # systemd watchdog support

# ─── Signal Chat ────────────────────────────────────────────────────────────
# Interactive chat: between jobs, check for messages from the owner on Signal.
# Process each with LLM (with optional shell tool use) and reply.
SIGNAL_RPC = "http://127.0.0.1:8080/api/v1/rpc"
SIGNAL_ACCOUNT = os.environ.get('SIGNAL_ACCOUNT', '+<BOT_PHONE>')
SIGNAL_OWNER = os.environ.get('SIGNAL_OWNER', '+<OWNER_PHONE>')
SIGNAL_CHAT_MODEL = "qwen3.5-35b-a3b-iq2m"
SIGNAL_CHAT_CTX = 16384             # Context window for chat responses (matches OLLAMA_CONTEXT_LENGTH)
SIGNAL_CHAT_MAX_EXEC = 3            # Max shell commands per message (search+fetch+verify)
SIGNAL_EXEC_TIMEOUT_S = 30          # Timeout for shell commands
SIGNAL_MAX_REPLY = 1800             # Signal message char limit
DATA_DIR = Path("/opt/netscan/data")

# ─── Stable Diffusion Image Generation ──────────────────────────────────────
# FLUX.1-schnell on sd.cpp. Cannot coexist with Ollama (16GB unified VRAM).
SD_CLI = "/opt/stable-diffusion.cpp/build/bin/sd-cli"
SD_FLUX_DIR = "/opt/stable-diffusion.cpp/models/flux"
SD_OUTPUT_PATH = "/tmp/sd-output.png"
SD_TIMEOUT_S = 300                  # Max 5 min for image generation
SD_SCRIPT_PREFIX = "/opt/stable-diffusion.cpp/generate-and-send"  # EXEC intercept pattern

# ─── Video Generation (WAN 2.1 T2V) ────────────────────────────────────────
SD_WAN_DIR = "/opt/stable-diffusion.cpp/models/wan"
SD_VIDEO_OUTPUT_PATH = "/tmp/sd-output.avi"
SD_VIDEO_TIMEOUT_S = 3000           # Max 50 min for video generation (~38 min typical)
SD_VIDEO_SCRIPT_PREFIX = "/opt/stable-diffusion.cpp/generate-video"  # EXEC video intercept

# ─── ESRGAN Upscale ────────────────────────────────────────────────────────
SD_ESRGAN_MODEL = "/opt/stable-diffusion.cpp/models/esrgan/RealESRGAN_x4plus.pth"
SD_UPSCALE_TILE_SIZE = 192       # Larger tiles = better seam quality. 128→15s, 192→25s, 256→41s @512²
SD_UPSCALE_TIMEOUT_S = 120

# ─── Kontext Image Editing ──────────────────────────────────────────────────
SD_KONTEXT_MODEL = f"{SD_FLUX_DIR}/flux1-kontext-dev-Q4_0.gguf"
SD_KONTEXT_TIMEOUT_S = 900          # Max 15 min for Kontext editing (~5 min typical @512²)
SD_KONTEXT_STALL_S = 180            # Kill if no stdout progress for 3 min
SD_EDIT_SCRIPT_PREFIX = "/opt/stable-diffusion.cpp/edit-image"  # EXEC edit intercept
SIGNAL_ATTACHMENTS_DIR = os.path.expanduser("~/.local/share/signal-cli/attachments")

# ─── Job name sets ──────────────────────────────────────────────────────────
# HA jobs: run opportunistically when GPU is idle (2-3 times/day)
# Only one of each script — ha-journal.py and ha-correlate.py
# (d1 duplicates were causing double-runs of the same analysis)
HA_JOURNAL_NAMES = {'ha-journal-n1'}
HA_CORRELATE_NAMES = {'ha-correlate'}
ALL_HA_NAMES = HA_JOURNAL_NAMES | HA_CORRELATE_NAMES

# Think jobs: run opportunistically when GPU is idle (fill time)
THINK_PREFIXES = ('think-',)

# Weekly job schedule: {name: day_of_week (0=Mon, 6=Sun)}
WEEKLY_JOBS = {
    'csi-sensor-discover': 2,   # Wednesday
    'csi-sensor-improve': 6,    # Sunday
    'vulnscan-weekly': 6,       # Sunday
    'career-digest-weekly': 6,  # Sunday — weekly career summary via Signal
}

# ─── Globals ────────────────────────────────────────────────────────────────
running = True

# Deferred SD queue: when image/edit/video/upscale is requested during a GPU job
# (allow_sd=False), save the request here instead of rejecting it.
# Processed at the next between-jobs chat window.
_deferred_sd_queue = []  # List of (action, args) tuples


def signal_handler(sig, frame):
    global running
    log("Received shutdown signal, finishing current job...")
    running = False


signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


# ─── Systemd watchdog ───────────────────────────────────────────────────────
def sd_notify(state):
    """Send notification to systemd (sd_notify protocol)."""
    addr = os.environ.get('NOTIFY_SOCKET')
    if not addr:
        return
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        if addr.startswith('@'):
            addr = '\0' + addr[1:]
        sock.sendto(state.encode(), addr)
        sock.close()
    except Exception:
        pass


def sd_watchdog_ping():
    """Tell systemd we're still alive."""
    sd_notify('WATCHDOG=1')


def sd_status(msg):
    """Update systemd status line."""
    sd_notify(f'STATUS={msg}')


# ─── Logging ────────────────────────────────────────────────────────────────
def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(line + '\n')
    except Exception:
        pass


# ─── Health checks ──────────────────────────────────────────────────────────
def is_ollama_healthy():
    """Check if Ollama is responding to API requests."""
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=OLLAMA_HEALTH_TIMEOUT_S) as resp:
            return resp.status == 200
    except Exception:
        return False


def is_network_up():
    """Quick check if basic network connectivity works."""
    try:
        # Try to reach the default gateway or a local DNS
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        # Try local gateway (common home router)
        sock.connect(('192.168.1.254', 53))
        sock.close()
        return True
    except Exception:
        pass
    try:
        # Fallback: try localhost ollama
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        sock.connect(('127.0.0.1', 11434))
        sock.close()
        return True
    except Exception:
        return False


def wait_for_ollama(max_wait_s=300):
    """Wait for Ollama to become healthy, with exponential backoff."""
    wait = 5
    total_waited = 0
    while total_waited < max_wait_s and running:
        if is_ollama_healthy():
            return True
        log(f"  Ollama not ready, waiting {wait}s (total: {total_waited}s)")
        sd_watchdog_ping()
        sleep_interruptible(wait)
        total_waited += wait
        wait = min(wait * 2, 60)  # Exponential backoff, max 60s
    return is_ollama_healthy()


def wait_for_network(max_wait_s=600):
    """Wait for network connectivity with backoff."""
    wait = 10
    total_waited = 0
    while total_waited < max_wait_s and running:
        if is_network_up():
            return True
        log(f"  Network down, waiting {wait}s (total: {total_waited}s)")
        sd_watchdog_ping()
        sleep_interruptible(wait)
        total_waited += wait
        wait = min(wait * 2, 120)
    return is_network_up()


# ─── GPU idle detection ────────────────────────────────────────────────────
def is_gpu_idle():
    """
    Check if GPU/LLM is idle by querying Ollama's /api/ps endpoint
    AND checking the actual GPU clock frequency.

    Ollama keeps the model loaded for 5 minutes after the last request.
    The `expires_at` field tells us when the model will be unloaded.
    However, the openclaw-gateway keepalive can keep the model warm
    indefinitely, so we also check the actual GPU clock:
      - pp_dpm_sclk at 1000MHz → GPU is idle (not computing)
      - pp_dpm_sclk at 1500-2000MHz → GPU is actively generating

    Logic:
      - No models loaded → definitely idle
      - GPU clock is low (< GPU_CLK_IDLE_MHZ) → model loaded but not generating → idle
      - Model will expire within GPU_IDLE_THRESHOLD_S → been idle for a while → idle
      - Model loaded + GPU clock high → actively generating → busy
    """
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/ps")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())

        models = data.get('models', [])
        if not models:
            return True  # No models loaded — GPU is free

        # Check actual GPU clock — most reliable indicator of active generation
        try:
            with open(GPU_SCLK_PATH) as f:
                for line in f:
                    if '*' in line:  # Active clock level has '*'
                        mhz = int(line.split(':')[1].strip().rstrip('Mhz *'))
                        if mhz < GPU_CLK_IDLE_MHZ:
                            return True  # Model loaded but GPU idle — safe to use
                        break
        except (FileNotFoundError, ValueError, IndexError):
            pass  # Fall through to expires_at check

        for model in models:
            expires_str = model.get('expires_at', '')
            if not expires_str:
                continue

            # Parse ISO8601 timestamp
            # Format: "2026-03-01T20:51:56.922317456+01:00"
            # Python's fromisoformat handles this in 3.11+
            try:
                expires = datetime.fromisoformat(expires_str)
                now = datetime.now(timezone.utc)
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                remaining = (expires - now).total_seconds()
            except (ValueError, TypeError):
                # Can't parse → assume busy to be safe
                return False

            if remaining > GPU_IDLE_THRESHOLD_S:
                # Model was recently used — GPU may be busy
                return False

        # All models close to expiry → idle
        return True

    except Exception:
        # Can't reach Ollama → assume idle (Ollama might not be running)
        return True


# ─── Job loading ────────────────────────────────────────────────────────────
def load_all_jobs():
    """Load all enabled jobs from openclaw jobs.json."""
    with open(JOBS_JSON) as f:
        data = json.load(f)
    jobs = {}
    for j in data['jobs']:
        if j.get('enabled', True):
            jobs[j['name']] = j
    return jobs


def categorize_jobs(all_jobs):
    """
    Split jobs into scheduling categories:
      - batch: Main sequential work (company, career, repo, infra, lore, etc.)
      - market: Evening market analysis block
      - morning_market: market-watch-am (timed ~10:50)
      - report: daily-report
      - weekly: Run only on specific day of week
      - opp_ha: Opportunistic HA observations (when GPU idle)
      - opp_think: Opportunistic think tasks (when GPU idle)
    """
    cats = {
        'batch': [],        # Sequential main workload
        'market': [],       # Evening block
        'morning_market': [],
        'report': [],
        'weekly': [],
        'opp_ha': [],       # Opportunistic HA
        'opp_think': [],    # Opportunistic think
        'meta': [],         # Cross-domain synthesis / life advisor
    }

    for name, job in all_jobs.items():
        # HA → opportunistic
        if name in ALL_HA_NAMES:
            cats['opp_ha'].append(job)
        elif name.startswith('ha-journal') or name.startswith('ha-correlate'):
            continue  # Disabled extra HA jobs — skip

        # Meta: cross-domain / life advisor / system intelligence
        elif name.startswith('life-think-') or name.startswith('system-think-'):
            cats['meta'].append(job)

        # Think → opportunistic
        elif any(name.startswith(p) for p in THINK_PREFIXES):
            cats['opp_think'].append(job)

        # Market
        elif name.startswith('market-think-') or name == 'market-think-summary':
            cats['market'].append(job)
        elif name == 'market-watch-pm':
            cats['market'].append(job)
        elif name == 'market-watch-am':
            cats['morning_market'].append(job)

        # Report, summary & weekly
        elif name == 'daily-summary':
            cats['report'].append(job)  # Runs before daily-report
        elif name == 'daily-report':
            cats['report'].append(job)
        elif name in WEEKLY_JOBS:
            cats['weekly'].append(job)

        # Everything else → batch
        else:
            cats['batch'].append(job)

    # Sort market-think so summary comes last
    cats['market'].sort(key=lambda j: (
        0 if j['name'] == 'market-watch-pm' else
        2 if j['name'] == 'market-think-summary' else 1,
        j['name']
    ))

    # Sort opportunistic pools
    cats['opp_ha'].sort(key=lambda j: j['name'])
    cats['opp_think'].sort(key=lambda j: j['name'])

    # Sort meta: life-cross → life-advisor → system-gpu → system-netsec → system-health (last)
    _meta_order = {
        'life-think-cross': 0,
        'life-think-advisor': 1,
        'system-think-gpu': 2,
        'system-think-netsec': 3,
        'system-think-health': 4,  # health watchdog last (needs all data)
    }
    cats['meta'].sort(key=lambda j: (_meta_order.get(j['name'], 3), j['name']))

    return cats


# ─── Queue building ─────────────────────────────────────────────────────────
def build_batch_queue(batch_jobs, weekly_jobs):
    """
    Build the sequential batch queue.
    Order: infra → scrape → company → career → repo → lore → other → weekly
    Scrape-only jobs (-scrape suffix) run early to produce raw data.
    Analyze-only jobs (-analyze suffix) run in their normal category after scrape.
    """
    blocks = {
        'infra': [],
        'scrape': [],       # *-scrape jobs: data gathering, no LLM
        'company': [],
        'career': [],
        'repo-scan': [],    # repo-scan-* → data collection first
        'repo-think': [],   # repo-think-* → LLM analysis after scans
        'repo-other': [],   # other repo-* jobs
        'lore': [],
        'academic': [],     # academic-watch-* → publications, dissertations, patents
        'other': [],
    }

    for job in batch_jobs:
        name = job['name']
        # Route scrape-only jobs to early scrape block
        if name.endswith('-scrape'):
            blocks['scrape'].append(job)
        elif name.startswith('netscan') or name.startswith('leak-') or \
           name == 'event-scout' or name.startswith('radio-scan') or \
           name == 'watchdog':
            blocks['infra'].append(job)
        elif name.startswith('company-'):
            blocks['company'].append(job)
        elif name.startswith('career-'):
            blocks['career'].append(job)
        elif name.startswith('repo-scan-') or name == 'repo-digest':
            blocks['repo-scan'].append(job)
        elif name.startswith('repo-think-'):
            blocks['repo-think'].append(job)
        elif name.startswith('repo-') or name == 'salary-tracker':
            blocks['repo-other'].append(job)
        elif name.startswith('lore-'):
            blocks['lore'].append(job)
        elif name.startswith('academic-'):
            blocks['academic'].append(job)
        else:
            blocks['other'].append(job)

    for block in blocks.values():
        block.sort(key=lambda j: j['name'])

    # Sort repo-think so summary always comes last
    blocks['repo-think'].sort(key=lambda j: (
        1 if j['name'] == 'repo-think-summary' else 0,
        j['name']
    ))

    # Sort company/career: main analysis first, focused second, summary last
    for bk in ('company', 'career'):
        blocks[bk].sort(key=lambda j: (
            2 if 'summary' in j['name'] else (1 if j['name'].count('-') > 2 else 0),
            j['name']
        ))

    queue = []

    # ── Priority 0: Scrape-only (data gathering, no LLM, produces raw-*.json) ──
    queue.extend(blocks['scrape'])

    # ── Priority 1: Infra (data gathering, no LLM needed, fast ~35min) ──
    queue.extend(blocks['infra'])

    # ── Priority 2: Quick data (moderate, ~1-2h) ──
    queue.extend(blocks['academic'])
    queue.extend(blocks['repo-other'])  # salary-tracker etc.
    queue.extend(blocks['other'])       # car-tracker, city-watch, csi-sensor

    # ── Priority 3: Think + analysis (the user's priority!) ──
    # Think jobs use data from PREVIOUS nights. Getting them done early
    # ensures the dashboard has fresh analysis every morning.
    company_main = [j for j in blocks['company'] if j['name'].count('-') <= 2]
    company_deep = [j for j in blocks['company'] if j['name'].count('-') > 2]
    career_main = [j for j in blocks['career']
                   if j['name'].count('-') <= 2 or j['name'] == 'career-scan']
    career_deep = [j for j in blocks['career'] if j['name'].count('-') > 2]

    queue.extend(company_main)
    queue.extend(career_main)
    queue.extend(blocks['repo-think'])
    queue.extend(company_deep)
    queue.extend(career_deep)

    # ── Priority 4: Slow data gathering (feeds NEXT night's analysis) ──
    # Lore digests and repo-scan are heavy (40min each for repo-scan,
    # 10-30min each for lore). Placed last so they don't block think jobs.
    queue.extend(blocks['lore'])
    queue.extend(blocks['repo-scan'])

    # Weekly at end of batch
    queue.extend(sorted(weekly_jobs, key=lambda j: j['name']))

    return queue


def build_nightly_queue(cats):
    """Build mega-queue for nightly batch: ALL jobs with HA interleaved.

    Combines batch, think, market, report, and weekly jobs into one sequential
    queue. HA observations are inserted every HA_INTERLEAVE_EVERY jobs so the
    home gets analyzed every ~2-3 hours throughout the overnight run.

    Order rationale — think BEFORE data gathering:
      1. Infra (fast, ~35min)            — network scans, no LLM
      2. Quick data (~1-2h)              — academic, salary, car, csi
      3. Think + analysis (~8-12h)       — company/career/repo think (uses prior data)
      4. Opportunity think + meta        — cross-domain synthesis, research
      5. Market analysis                 — financial sectors
      6. Slow data gathering (~5-7h)     — lore digests, repo-scan (40min each)
      7. Report + weekly                 — closure
    """
    # Base batch queue with think-first ordering
    main_queue = build_batch_queue(cats['batch'], cats['weekly'])

    # Find the slow data-gathering tail (lore + repo-scan, at end of batch)
    # Insert think/market/meta BEFORE the slow tail so they actually run.
    tail_start = len(main_queue)
    for i, job in enumerate(main_queue):
        if job['name'].startswith('lore-') or job['name'].startswith('repo-scan'):
            tail_start = i
            break

    early = main_queue[:tail_start]
    late = main_queue[tail_start:]

    # Build final queue: early batch → think → meta → market → slow data → report
    main_queue = early

    # Opportunity think tasks (research, system-think, life-think)
    main_queue.extend(sorted(cats['opp_think'], key=lambda j: j['name']))

    # Meta: cross-domain synthesis + life advisor
    main_queue.extend(cats['meta'])

    # Market analysis (watch-pm → sectors → summary)
    main_queue.extend(cats['market'])
    main_queue.extend(cats['morning_market'])

    # Slow data gathering tail (lore, repo-scan, weekly)
    main_queue.extend(late)

    # Daily report at the end
    main_queue.extend(cats['report'])

    # Interleave HA observations throughout the queue
    ha_pool = sorted(cats['opp_ha'], key=lambda j: j['name'])
    if not ha_pool:
        return main_queue

    result = []
    jobs_since_ha = 0
    for job in main_queue:
        result.append(job)
        jobs_since_ha += 1
        if jobs_since_ha >= HA_INTERLEAVE_EVERY:
            # Insert full round of HA observations
            for ha_job in ha_pool:
                result.append(ha_job)
            jobs_since_ha = 0

    # Ensure HA runs at least once even for small queues
    if jobs_since_ha == len(main_queue):
        result.extend(ha_pool)

    return result


def build_daytime_fill_queue(cats):
    """Build a diverse fill queue for daytime GPU usage.

    Interleaves categories so ALL dashboard tabs get refreshed even if
    we only get through part of the queue before 23:00.
    Order within each round: lore → academic → repo-think → career-think →
    company-think → think → meta → market  (data gathering before analysis).
    """
    # Group fill candidates by priority
    lore = [j for j in cats['batch'] if j['name'].startswith('lore-')]
    academic = [j for j in cats['batch'] if j['name'].startswith('academic-')]
    repo_think = [j for j in cats['batch']
                  if j['name'].startswith('repo-think-')]
    career = [j for j in cats['batch']
              if j['name'].startswith('career-think-') or j['name'] == 'career-scan']
    company = [j for j in cats['batch']
               if j['name'].startswith('company-think-')]
    think = list(cats.get('opp_think', []))
    meta = list(cats.get('meta', []))
    market = list(cats.get('market', []))

    # Sort each group
    for g in (lore, academic, repo_think, career, company, think, meta, market):
        g.sort(key=lambda j: j['name'])

    # Round-robin interleave: pick one from each category per round
    pools = [lore, academic, repo_think, career, company, think, meta, market]
    result = []
    while any(pools):
        for pool in pools:
            if pool:
                result.append(pool.pop(0))
        # Remove exhausted pools
        pools = [p for p in pools if p]

    return result


# ─── Execution ──────────────────────────────────────────────────────────────
def is_idle_window():
    """Check if current time is in the idle window."""
    hour = datetime.now().hour
    return IDLE_START <= hour < IDLE_END


def should_run_weekly(job):
    """Check if a weekly job should run today."""
    name = job['name']
    if name not in WEEKLY_JOBS:
        return True
    return datetime.now().weekday() == WEEKLY_JOBS[name]


def extract_direct_command(job):
    """Extract direct shell command from job payload if available.

    Many openclaw jobs ask the LLM agent to shell-execute a script.
    Running scripts directly avoids the overhead and unreliability of
    the LLM-as-shell-proxy pattern (agent may acknowledge without executing).

    Handles these payload formats:
      python3 /opt/netscan/script.py --args
      /opt/netscan/script.py --args
      cd /opt/netscan && flock ... python3 script.py --args
      cd /opt/netscan && python3 script.py --args
      cd /opt/netscan && bash script.sh --args

    Returns command string or None if job needs agent reasoning.
    """
    msg = job.get('payload', {}).get('message', '')
    if not msg:
        return None

    for line in msg.strip().split('\n'):
        line = line.strip()

        # Strip "Run " prefix from prose-style commands
        # e.g. "Run /opt/netscan/csi-sensor-watch.py --discover to search..."
        run_m = re.match(r'^[Rr]un\s+(/opt/netscan/\S+\.(?:py|sh)(?:\s+--?\S+)*)', line)
        if run_m:
            return run_m.group(1)

        # Strip "cd /opt/netscan && " prefix (common in openclaw jobs)
        cd_m = re.match(r'cd\s+/opt/netscan\s*&&\s*(.*)', line)
        if cd_m:
            line = cd_m.group(1).strip()

        # Strip flock wrapper (not needed — queue-runner serializes)
        flock_m = re.match(r'flock\s+-w\s+\d+\s+\S+\s+(.*)', line)
        if flock_m:
            line = flock_m.group(1).strip()

        # Match: python3 /opt/netscan/... or /opt/netscan/...
        if re.match(r'^(python3\s+)?/opt/netscan/\S+', line):
            cmd_str = re.sub(r'\s+2>&1\s*(\|\s*tail\s+-\d+)?\s*$', '', line)
            return cmd_str

        # Match: python3 script.py or bash script.sh (relative, after cd stripping)
        if re.match(r'^(python3|bash)\s+\S+\.(py|sh)', line):
            # Convert relative to absolute
            script_part = line
            cmd_str = re.sub(r'\s+2>&1\s*(\|\s*tail\s+-\d+)?\s*$', '', script_part)
            # Expand relative path to /opt/netscan/
            cmd_str = re.sub(r'^(python3|bash)\s+(\S+)',
                             lambda m: f'{m.group(1)} /opt/netscan/{m.group(2)}',
                             cmd_str)
            return cmd_str

    return None


def run_job(job):
    """Run a single job — direct execution for scripts, openclaw for agent tasks.
    
    Includes pre-flight health check: verifies ollama is responsive before
    starting a job. This prevents wasting timeout-length waits on a dead backend.
    """
    name = job['name']
    timeout_s = job.get('payload', {}).get('timeoutSeconds', 0)
    if not timeout_s:
        timeout_s = DEFAULT_TIMEOUT_S

    # Apply per-category timeout caps
    for prefix, cap in CATEGORY_TIMEOUT_CAPS.items():
        if name.startswith(prefix):
            timeout_s = min(timeout_s, cap)
            break

    # Pre-flight: ensure ollama is healthy before we commit to a job
    # Non-LLM jobs (scrape + infra) don't use Ollama — skip pre-flight
    is_scrape = name.endswith('-scrape')
    is_infra_nollm = name.startswith('netscan-')  # scan.sh, enumerate.sh — pure nmap
    gpu_free = is_scrape or is_infra_nollm
    if not gpu_free and not is_ollama_healthy():
        log(f"  Pre-flight: Ollama unhealthy before {name}, waiting...")
        if not wait_for_ollama(max_wait_s=300):
            log(f"  SKIP: {name} — Ollama not available after 5min wait")
            return False, 0.0

    sd_watchdog_ping()
    sd_status(f'Running: {name}')

    direct_cmd = extract_direct_command(job)
    if direct_cmd:
        return run_direct(job, direct_cmd, timeout_s, gpu_free=gpu_free)
    return run_via_openclaw(job, timeout_s)


def run_direct(job, cmd_str, timeout_s, gpu_free=False):
    """Run a script directly (bypassing openclaw agent).

    Uses Popen + poll loop to ping systemd watchdog every 30s during execution,
    preventing watchdog kills on long-running jobs (e.g. career-scan ~2h).
    Background threads drain stdout/stderr to prevent pipe buffer deadlocks.

    When gpu_free=True (scrape/infra jobs that don't use Ollama), Signal chat
    is processed during the poll loop — the GPU is idle so we can answer
    messages while the CPU/network job runs in parallel.
    """
    name = job['name']
    cmd = shlex.split(cmd_str)

    env = dict(os.environ)
    env['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
    env.setdefault('HOME', str(Path.home()))

    deadline = timeout_s + 120
    start = time.time()
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd='/opt/netscan', env=env,
        )
        # Drain pipes in background threads to prevent deadlock
        stdout_chunks = []
        stderr_chunks = []
        def drain(pipe, buf):
            for line in pipe:
                buf.append(line)
        t_out = threading.Thread(target=drain, args=(proc.stdout, stdout_chunks), daemon=True)
        t_err = threading.Thread(target=drain, args=(proc.stderr, stderr_chunks), daemon=True)
        t_out.start()
        t_err.start()

        # Poll loop: ping watchdog every 30s while subprocess runs.
        # When gpu_free, also process Signal chat — GPU is idle during
        # scrape/infra jobs so we can answer messages in parallel.
        poll_interval = 10 if gpu_free else 30
        chat_served = 0
        chat_time = 0.0
        while proc.poll() is None:
            elapsed = time.time() - start
            if elapsed > deadline:
                proc.kill()
                proc.wait(timeout=10)
                log(f"  TIMEOUT: {name} — killed after {elapsed:.0f}s")
                return False, elapsed
            sd_watchdog_ping()
            if gpu_free:
                try:
                    t0 = time.time()
                    n = process_signal_chat(tag='parallel')
                    if n:
                        chat_served += n
                        chat_time += time.time() - t0
                    sd_watchdog_ping()  # ping again after potentially long chat
                except Exception as e:
                    log(f"  ⚠ Chat error during {name} (job continues): {e}")
            try:
                proc.wait(timeout=poll_interval)
            except subprocess.TimeoutExpired:
                pass  # Loop again — ping watchdog

        if chat_served:
            log(f"  📱 Served {chat_served} message(s) during {name} ({chat_time:.0f}s chat, GPU free)")

        t_out.join(timeout=5)
        t_err.join(timeout=5)

        elapsed = time.time() - start
        stdout = ''.join(stdout_chunks)
        stderr = ''.join(stderr_chunks)
        ok = proc.returncode == 0
        status = 'ok' if ok else f'exit={proc.returncode}'
        # Log tail of output for context
        out = stdout.strip().split('\n')
        last = out[-1][:120] if out and out[-1] else ''
        tag = f' [{last}]' if last else ''
        log(f"  Done: {name} — {elapsed:.0f}s ({status}){tag}")
        if not ok and stderr:
            for err in stderr.strip().split('\n')[-3:]:
                log(f"  stderr: {err[:120]}")
        return ok, elapsed
    except Exception as e:
        elapsed = time.time() - start
        log(f"  ERROR: {name} — {e}")
        return False, elapsed


def run_via_openclaw(job, timeout_s):
    """Run a job through openclaw agent (for tasks needing LLM reasoning).

    Uses Popen + poll loop to ping systemd watchdog every 30s during execution.
    Background threads drain stdout/stderr to prevent pipe buffer deadlocks.
    """
    jid = job['id']
    name = job['name']
    cli_timeout_ms = timeout_s * 1000 + CLI_BUFFER_MS

    cmd = [
        'openclaw', 'cron', 'run', jid,
        '--expect-final',
        '--timeout', str(cli_timeout_ms)
    ]

    deadline = cli_timeout_ms / 1000 + 120
    start = time.time()
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        # Drain pipes in background threads to prevent deadlock
        def drain(pipe):
            for _ in pipe:
                pass
        t_out = threading.Thread(target=drain, args=(proc.stdout,), daemon=True)
        t_err = threading.Thread(target=drain, args=(proc.stderr,), daemon=True)
        t_out.start()
        t_err.start()

        while proc.poll() is None:
            elapsed = time.time() - start
            if elapsed > deadline:
                proc.kill()
                proc.wait(timeout=10)
                log(f"  TIMEOUT: {name} — killed after {elapsed:.0f}s")
                return False, elapsed
            sd_watchdog_ping()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass

        t_out.join(timeout=5)
        t_err.join(timeout=5)

        elapsed = time.time() - start
        ok = proc.returncode == 0
        status = 'ok' if ok else f'exit={proc.returncode}'
        log(f"  Done: {name} — {elapsed:.0f}s ({status})")
        return ok, elapsed
    except Exception as e:
        elapsed = time.time() - start
        log(f"  ERROR: {name} — {e}")
        return False, elapsed


def sleep_interruptible(seconds):
    """Sleep in small chunks, respecting shutdown signal and pinging watchdog."""
    remaining = seconds
    while remaining > 0 and running:
        chunk = min(30, remaining)
        time.sleep(chunk)
        remaining -= chunk
        sd_watchdog_ping()


# ─── Signal Chat Integration ───────────────────────────────────────────────
# Between jobs, check for incoming Signal messages from the owner.
# Process each with LLM (with optional shell tool use) and reply.
# This gives the owner interactive access without GPU concurrency issues.

_last_signal_check = 0.0  # epoch — initialized on first call


def check_signal_inbox():
    """Check for pending Signal messages from the owner since last check.

    Reads signal-cli journal entries (JSON lines produced by --output=json
    mode) and parses for text messages from SIGNAL_OWNER.
    Returns list of {'text': str, 'timestamp': int} dicts.
    """
    global _last_signal_check
    now = time.time()
    if _last_signal_check == 0.0:
        # First check: look back 5 min (catch messages sent during startup)
        _last_signal_check = now - 300
    since_str = time.strftime('%Y-%m-%d %H:%M:%S',
                               time.localtime(_last_signal_check))
    _last_signal_check = now

    try:
        result = subprocess.run(
            ['journalctl', '-u', 'signal-cli', '--since', since_str,
             '--output=cat', '--no-pager'],
            capture_output=True, text=True, timeout=10
        )
        messages = []
        for line in result.stdout.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                envelope = data.get('envelope', {})
                source = (envelope.get('sourceNumber') or
                          envelope.get('source', ''))
                dm = envelope.get('dataMessage', {})
                text = dm.get('message', '')
                ts = envelope.get('timestamp', 0)
                # Extract attachments (images sent by user)
                attachments = []
                for att in dm.get('attachments', []):
                    att_id = att.get('id', '')
                    content_type = att.get('contentType', '')
                    if att_id and content_type.startswith('image/'):
                        att_path = os.path.join(SIGNAL_ATTACHMENTS_DIR, att_id)
                        if os.path.exists(att_path):
                            attachments.append(att_path)
                if source == SIGNAL_OWNER and (text or attachments):
                    messages.append({
                        'text': text or '',
                        'timestamp': ts,
                        'attachments': attachments,
                    })
            except (json.JSONDecodeError, KeyError, TypeError):
                # Not a JSON message line (signal-cli log noise) — skip
                continue
        return messages
    except Exception as e:
        log(f"  Signal inbox check error: {e}")
        return []


def signal_reply(text):
    """Send a reply message to the owner via Signal JSON-RPC."""
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "send",
        "params": {
            "account": SIGNAL_ACCOUNT,
            "recipient": [SIGNAL_OWNER],
            "message": text[:SIGNAL_MAX_REPLY]
        }
    }).encode()
    try:
        req = urllib.request.Request(
            SIGNAL_RPC, data=payload,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return True
    except Exception as e:
        log(f"  Signal reply error: {e}")
        return False


def signal_send_attachment(text, attachment_path):
    """Send a message with file attachment to the owner via Signal JSON-RPC."""
    payload = json.dumps({
        "jsonrpc": "2.0", "id": f"img-{int(time.time())}", "method": "send",
        "params": {
            "account": SIGNAL_ACCOUNT,
            "recipient": [SIGNAL_OWNER],
            "message": text[:SIGNAL_MAX_REPLY],
            "attachments": [str(attachment_path)]
        }
    }).encode()
    try:
        req = urllib.request.Request(
            SIGNAL_RPC, data=payload,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return True
    except Exception as e:
        log(f"  Signal attachment send error: {e}")
        return False


def generate_and_send_image(prompt):
    """Synchronous FLUX.1 image generation: stop Ollama, run SD, send, restart.

    BC-250 has 16GB unified memory — SD and Ollama cannot coexist.
    This function handles the full lifecycle synchronously so queue-runner
    stays in control and knows when it's safe to resume jobs.

    Flow: notify user → stop Ollama → sd-cli → restart Ollama → send image
    → wait for Ollama health → return status.
    """
    log(f"  SD: Image generation requested: {prompt[:80]}")
    sd_status('SD: generating image')

    # Notify user
    signal_reply(f"\U0001f3a8 Generating your image... ~2-3 min. Prompt: {prompt[:200]}")

    # Stop Ollama to free VRAM
    log("  SD: Stopping Ollama...")
    try:
        subprocess.run(["sudo", "systemctl", "stop", "ollama"],
                       timeout=30, capture_output=True)
        time.sleep(3)
    except Exception as e:
        log(f"  SD: Failed to stop Ollama: {e}")
        return f"Failed to stop Ollama for image generation: {e}"

    # Verify FLUX model files exist
    t5xxl = f"{SD_FLUX_DIR}/t5-v1_1-xxl-encoder-Q4_K_M.gguf"
    if not os.path.exists(t5xxl):
        log("  SD: FLUX model not found")
        subprocess.run(["sudo", "systemctl", "start", "ollama"],
                       timeout=30, capture_output=True)
        wait_for_ollama()
        return "SD error: FLUX model files not found."

    # Remove stale output
    try:
        os.remove(SD_OUTPUT_PATH)
    except FileNotFoundError:
        pass

    # Run sd-cli
    cmd = [
        SD_CLI,
        "--diffusion-model", f"{SD_FLUX_DIR}/flux1-schnell-q4_k.gguf",
        "--vae", f"{SD_FLUX_DIR}/ae.safetensors",
        "--clip_l", f"{SD_FLUX_DIR}/clip_l.safetensors",
        "--t5xxl", t5xxl,
        "--clip-on-cpu",
        "-p", prompt,
        "-o", SD_OUTPUT_PATH,
        "--steps", "4",
        "-W", "512", "-H", "512",
        "--cfg-scale", "1.0",
        "--sampling-method", "euler"
    ]

    log("  SD: Running sd-cli...")
    start_t = time.time()
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        # Poll for output file (sd-cli writes it when done)
        while time.time() - start_t < SD_TIMEOUT_S:
            if (os.path.exists(SD_OUTPUT_PATH) and
                    os.path.getsize(SD_OUTPUT_PATH) > 1000):
                time.sleep(2)  # Let it finish writing
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
                subprocess.run(["killall", "-9", "sd-cli"],
                               capture_output=True, timeout=5)
                break
            time.sleep(3)
            sd_watchdog_ping()
        else:
            # Timed out
            proc.kill()
            subprocess.run(["killall", "-9", "sd-cli"],
                           capture_output=True, timeout=5)
            log("  SD: Generation timed out")
            subprocess.run(["sudo", "systemctl", "start", "ollama"],
                           timeout=30, capture_output=True)
            wait_for_ollama()
            return "Image generation timed out (5 min limit). Sorry!"
    except Exception as e:
        log(f"  SD: sd-cli error: {e}")
        subprocess.run(["sudo", "systemctl", "start", "ollama"],
                       timeout=30, capture_output=True)
        wait_for_ollama()
        return f"SD error: {e}"

    elapsed = int(time.time() - start_t)
    log(f"  SD: Image generated in {elapsed}s")

    # Auto-upscale with ESRGAN (Ollama is already stopped — no extra swap cost)
    upscaled_path = None
    if os.path.exists(SD_ESRGAN_MODEL):
        base, ext = os.path.splitext(SD_OUTPUT_PATH)
        upscaled_path = f"{base}-4x{ext}"
        try:
            os.remove(upscaled_path)
        except FileNotFoundError:
            pass
        upscale_cmd = [
            SD_CLI, "-M", "upscale",
            "--upscale-model", SD_ESRGAN_MODEL,
            "--upscale-tile-size", str(SD_UPSCALE_TILE_SIZE),
            "-i", SD_OUTPUT_PATH,
            "-o", upscaled_path,
        ]
        log(f"  SD: Auto-upscaling with ESRGAN 4× (tile {SD_UPSCALE_TILE_SIZE})...")
        up_start = time.time()
        try:
            proc = subprocess.Popen(upscale_cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT)
            while time.time() - up_start < SD_UPSCALE_TIMEOUT_S:
                if (os.path.exists(upscaled_path) and
                        os.path.getsize(upscaled_path) > 1000):
                    time.sleep(2)
                    proc.kill()
                    try:
                        proc.wait(timeout=5)
                    except Exception:
                        pass
                    subprocess.run(["killall", "-9", "sd-cli"],
                                   capture_output=True, timeout=5)
                    break
                time.sleep(3)
                sd_watchdog_ping()
            else:
                proc.kill()
                subprocess.run(["killall", "-9", "sd-cli"],
                               capture_output=True, timeout=5)
                log("  SD: ESRGAN upscale timed out, skipping")
                upscaled_path = None
            if upscaled_path and os.path.exists(upscaled_path):
                up_elapsed = int(time.time() - up_start)
                up_kb = os.path.getsize(upscaled_path) // 1024
                log(f"  SD: Upscaled in {up_elapsed}s ({up_kb}KB)")
            else:
                upscaled_path = None
        except Exception as e:
            log(f"  SD: ESRGAN error (skipping): {e}")
            upscaled_path = None

    # Restart Ollama (start loading model while we send)
    log("  SD: Restarting Ollama...")
    subprocess.run(["sudo", "systemctl", "start", "ollama"],
                   timeout=30, capture_output=True)

    # Send original image via Signal
    log("  SD: Sending image via Signal...")
    if signal_send_attachment(prompt, SD_OUTPUT_PATH):
        log("  SD: Image sent successfully")
    else:
        log("  SD: WARNING: Failed to send image via Signal")

    # Send upscaled version if available
    if upscaled_path:
        up_kb = os.path.getsize(upscaled_path) // 1024
        caption = f"⬆️ 4× upscale ({up_kb}KB)"
        if signal_send_attachment(caption, upscaled_path):
            log("  SD: Upscaled image sent")
        else:
            log("  SD: WARNING: Failed to send upscaled image")

    # Wait for Ollama health before returning to job queue
    wait_for_ollama()
    sd_status('SD: complete, resuming jobs')
    extra = f" + 4× upscale" if upscaled_path else ""
    return f"\U0001f3a8 Image generated and sent! ({elapsed}s{extra})"


def generate_and_send_video(prompt):
    """Synchronous WAN 2.1 T2V video generation: stop Ollama, run sd-cli, send, restart.

    Similar to generate_and_send_image but uses WAN 2.1 T2V 1.3B model
    in vid_gen mode. Takes ~38 minutes for 17 frames @480×320.
    Output is AVI (MJPEG) regardless of -o extension.
    """
    log(f"  VID: Video generation requested: {prompt[:80]}")
    sd_status('VID: generating video')

    # Verify WAN model files exist
    wan_diffusion = f"{SD_WAN_DIR}/Wan2.1-T2V-1.3B-Q4_0.gguf"
    wan_vae = f"{SD_WAN_DIR}/wan_2.1_vae.safetensors"
    wan_t5 = f"{SD_WAN_DIR}/umt5-xxl-encoder-Q4_K_M.gguf"

    for model_f in (wan_diffusion, wan_vae, wan_t5):
        if not os.path.exists(model_f):
            log(f"  VID: Model not found: {model_f}")
            return f"Video generation error: model not found ({os.path.basename(model_f)})"

    # Notify user (video takes a long time)
    signal_reply(f"\U0001f3ac Generating video... this takes ~35-40 min. Prompt: {prompt[:150]}")

    # Stop Ollama to free VRAM
    log("  VID: Stopping Ollama...")
    try:
        subprocess.run(["sudo", "systemctl", "stop", "ollama"],
                       timeout=30, capture_output=True)
        time.sleep(3)
    except Exception as e:
        log(f"  VID: Failed to stop Ollama: {e}")
        return f"Failed to stop Ollama for video generation: {e}"

    # Remove stale output (sd-cli produces .avi regardless of -o extension)
    for ext in ('.avi', '.mp4.avi', '.mp4'):
        try:
            os.remove(SD_VIDEO_OUTPUT_PATH + ext.lstrip('.avi').rstrip('.avi'))
        except FileNotFoundError:
            pass
    try:
        os.remove(SD_VIDEO_OUTPUT_PATH)
    except FileNotFoundError:
        pass

    cmd = [
        SD_CLI, "-M", "vid_gen",
        "--diffusion-model", wan_diffusion,
        "--vae", wan_vae,
        "--t5xxl", wan_t5,
        "-p", prompt,
        "--cfg-scale", "6.0",
        "--sampling-method", "euler",
        "-W", "480", "-H", "320",
        "--diffusion-fa", "--offload-to-cpu",
        "--video-frames", "17",
        "--flow-shift", "3.0",
        "-o", SD_VIDEO_OUTPUT_PATH,
    ]

    log("  VID: Running sd-cli vid_gen...")
    start_t = time.time()
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        # Poll for output file — sd-cli hangs after writing on GFX1013
        # Video output is always .avi (MJPEG), sd-cli appends .avi to whatever -o says
        # Check both /tmp/sd-output.avi and /tmp/sd-output.avi.avi
        while time.time() - start_t < SD_VIDEO_TIMEOUT_S:
            for candidate in (SD_VIDEO_OUTPUT_PATH,
                              SD_VIDEO_OUTPUT_PATH + ".avi"):
                if (os.path.exists(candidate) and
                        os.path.getsize(candidate) > 10000):
                    time.sleep(5)  # Let it finish writing (video is larger)
                    proc.kill()
                    try:
                        proc.wait(timeout=5)
                    except Exception:
                        pass
                    subprocess.run(["killall", "-9", "sd-cli"],
                                   capture_output=True, timeout=5)
                    actual_output = candidate
                    break
            else:
                time.sleep(10)
                sd_watchdog_ping()
                continue
            break  # Found the file, exit outer loop
        else:
            proc.kill()
            subprocess.run(["killall", "-9", "sd-cli"],
                           capture_output=True, timeout=5)
            log("  VID: Generation timed out")
            subprocess.run(["sudo", "systemctl", "start", "ollama"],
                           timeout=30, capture_output=True)
            wait_for_ollama()
            return "Video generation timed out (50 min limit). Sorry!"
    except Exception as e:
        log(f"  VID: sd-cli error: {e}")
        subprocess.run(["sudo", "systemctl", "start", "ollama"],
                       timeout=30, capture_output=True)
        wait_for_ollama()
        return f"Video generation error: {e}"

    elapsed_min = int((time.time() - start_t) / 60)
    file_size = os.path.getsize(actual_output) // 1024
    log(f"  VID: Video generated in {elapsed_min}min ({file_size} KB)")

    # Restart Ollama
    log("  VID: Restarting Ollama...")
    subprocess.run(["sudo", "systemctl", "start", "ollama"],
                   timeout=30, capture_output=True)

    # Send video via Signal
    log("  VID: Sending video via Signal...")
    if signal_send_attachment(f"\U0001f3ac {prompt[:200]}", actual_output):
        log("  VID: Video sent successfully")
    else:
        log("  VID: WARNING: Failed to send video via Signal")

    wait_for_ollama()
    sd_status('VID: complete, resuming jobs')
    return f"\U0001f3ac Video generated and sent! ({elapsed_min}min, {file_size}KB)"


def upscale_and_send(image_path, prompt=""):
    """Upscale an existing image with ESRGAN and send via Signal.

    Uses sd-cli -M upscale with RealESRGAN_x4plus (4× upscale).
    Requires ESRGAN model on disk. Stops Ollama, upscales, sends, restarts.
    """
    if not os.path.exists(SD_ESRGAN_MODEL):
        return "ESRGAN model not found. Download RealESRGAN_x4plus.pth first."

    if not os.path.exists(image_path):
        return f"Input image not found: {image_path}"

    log(f"  ESRGAN: Upscaling {image_path}")
    sd_status('ESRGAN: upscaling image')

    signal_reply(f"⬆️ Upscaling image (4×)... ~30s")

    # Stop Ollama
    try:
        subprocess.run(["sudo", "systemctl", "stop", "ollama"],
                       timeout=30, capture_output=True)
        time.sleep(3)
    except Exception as e:
        log(f"  ESRGAN: Failed to stop Ollama: {e}")
        return f"Failed to stop Ollama: {e}"

    # Output path: original name with -4x suffix
    base, ext = os.path.splitext(image_path)
    upscaled_path = f"{base}-4x{ext}"
    try:
        os.remove(upscaled_path)
    except FileNotFoundError:
        pass

    cmd = [
        SD_CLI, "-M", "upscale",
        "--upscale-model", SD_ESRGAN_MODEL,
        "--upscale-tile-size", str(SD_UPSCALE_TILE_SIZE),
        "-i", image_path,
        "-o", upscaled_path,
    ]

    log(f"  ESRGAN: Running sd-cli upscale (tile {SD_UPSCALE_TILE_SIZE})...")
    start_t = time.time()
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        # Poll for upscaled output
        while time.time() - start_t < SD_UPSCALE_TIMEOUT_S:
            if (os.path.exists(upscaled_path) and
                    os.path.getsize(upscaled_path) > 1000):
                time.sleep(2)
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
                subprocess.run(["killall", "-9", "sd-cli"],
                               capture_output=True, timeout=5)
                break
            time.sleep(3)
            sd_watchdog_ping()
        else:
            proc.kill()
            subprocess.run(["killall", "-9", "sd-cli"],
                           capture_output=True, timeout=5)
            log("  ESRGAN: Upscale timed out")
            subprocess.run(["sudo", "systemctl", "start", "ollama"],
                           timeout=30, capture_output=True)
            wait_for_ollama()
            return "ESRGAN upscale timed out."
    except Exception as e:
        log(f"  ESRGAN: error: {e}")
        subprocess.run(["sudo", "systemctl", "start", "ollama"],
                       timeout=30, capture_output=True)
        wait_for_ollama()
        return f"ESRGAN error: {e}"

    elapsed = int(time.time() - start_t)
    size_kb = os.path.getsize(upscaled_path) // 1024
    log(f"  ESRGAN: Upscaled in {elapsed}s ({size_kb}KB)")

    # Restart Ollama
    subprocess.run(["sudo", "systemctl", "start", "ollama"],
                   timeout=30, capture_output=True)

    # Send upscaled image
    caption = f"⬆️ 4× upscale ({size_kb}KB)"
    if prompt:
        caption = f"⬆️ {prompt[:150]} (4× upscale, {size_kb}KB)"
    if signal_send_attachment(caption, upscaled_path):
        log("  ESRGAN: Upscaled image sent")
    else:
        log("  ESRGAN: WARNING: Failed to send upscaled image")

    wait_for_ollama()
    sd_status('ESRGAN: complete, resuming jobs')
    return f"⬆️ Image upscaled 4× and sent! ({elapsed}s, {size_kb}KB)"


def edit_and_send_image(source_path, prompt):
    """Edit an image using FLUX.1-Kontext-dev: stop Ollama, run sd-cli with -r, send, restart.

    Uses the Kontext model which understands reference images and can edit them
    based on text instructions. Flow mirrors generate_and_send_image().
    """
    if not os.path.exists(SD_KONTEXT_MODEL):
        return "Kontext model not found. Download flux1-kontext-dev-Q4_0.gguf first."

    if not os.path.exists(source_path):
        return f"Source image not found: {source_path}"

    log(f"  EDIT: Kontext edit requested: {prompt[:80]}")
    sd_status('EDIT: editing image with Kontext')

    # Resize input image to 512×512 — Kontext processes the full reference image
    # through the model. A 1200×1600 input takes 3-4× longer than 512×512.
    resized_path = "/tmp/sd-edit-input-resized.png"
    try:
        from PIL import Image
        with Image.open(source_path) as img:
            orig_size = img.size
            img_resized = img.resize((512, 512), Image.LANCZOS)
            img_resized.save(resized_path)
        log(f"  EDIT: Resized input {orig_size[0]}×{orig_size[1]} → 512×512")
    except Exception as e:
        log(f"  EDIT: PIL resize failed ({e}), using original")
        resized_path = source_path

    signal_reply(f"\u270f\ufe0f Editing your image with Kontext... ~5 min. Instruction: {prompt[:200]}")

    # Stop Ollama to free VRAM
    log("  EDIT: Stopping Ollama...")
    try:
        subprocess.run(["sudo", "systemctl", "stop", "ollama"],
                       timeout=30, capture_output=True)
        time.sleep(3)
    except Exception as e:
        log(f"  EDIT: Failed to stop Ollama: {e}")
        return f"Failed to stop Ollama for image editing: {e}"

    # Reuse FLUX1 text encoders and VAE (same architecture as Kontext)
    t5xxl = f"{SD_FLUX_DIR}/t5-v1_1-xxl-encoder-Q4_K_M.gguf"
    clip_l = f"{SD_FLUX_DIR}/clip_l.safetensors"
    vae = f"{SD_FLUX_DIR}/ae.safetensors"

    for model_f in (t5xxl, clip_l, vae):
        if not os.path.exists(model_f):
            log(f"  EDIT: Model not found: {model_f}")
            subprocess.run(["sudo", "systemctl", "start", "ollama"],
                           timeout=30, capture_output=True)
            wait_for_ollama()
            return f"FLUX model file missing: {os.path.basename(model_f)}"

    # Remove stale output
    edit_output = "/tmp/sd-edit-output.png"
    try:
        os.remove(edit_output)
    except FileNotFoundError:
        pass

    cmd = [
        SD_CLI,
        "--diffusion-model", SD_KONTEXT_MODEL,
        "--vae", vae,
        "--clip_l", clip_l,
        "--t5xxl", t5xxl,
        "--clip-on-cpu",
        "-r", resized_path,
        "-p", prompt,
        "-o", edit_output,
        "--steps", "28",
        "-W", "512", "-H", "512",
        "--cfg-scale", "3.5",
        "--sampling-method", "euler",
        "--offload-to-cpu", "--diffusion-fa",
    ]

    log("  EDIT: Running sd-cli Kontext...")
    start_t = time.time()
    last_output_t = start_t  # Track last stdout activity for stall detection
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        import fcntl
        fd = proc.stdout.fileno()
        fl = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

        while time.time() - start_t < SD_KONTEXT_TIMEOUT_S:
            # Check for stdout activity (progress)
            try:
                chunk = proc.stdout.read(4096)
                if chunk:
                    last_output_t = time.time()
                    last_line = chunk.decode('utf-8', errors='replace').strip().split('\n')[-1]
                    if 'step' in last_line.lower() or 'it/s' in last_line.lower():
                        log(f"  EDIT: progress: {last_line[:120]}")
            except (BlockingIOError, OSError):
                pass

            # Check for stall (no output for SD_KONTEXT_STALL_S)
            stall_s = int(time.time() - last_output_t)
            if stall_s > SD_KONTEXT_STALL_S and time.time() - start_t > 60:
                proc.kill()
                try:
                    proc.wait(timeout=10)
                except Exception:
                    pass
                subprocess.run(["killall", "-9", "sd-cli"],
                               capture_output=True, timeout=5)
                elapsed = int(time.time() - start_t)
                log(f"  EDIT: sd-cli stalled (no output for {stall_s}s), killed after {elapsed}s")
                subprocess.run(["sudo", "systemctl", "start", "ollama"],
                               timeout=30, capture_output=True)
                wait_for_ollama()
                return f"Image editing stalled after {elapsed // 60} min (sd-cli stopped responding). Sorry!"

            # Check if output file appeared (success)
            if (os.path.exists(edit_output) and
                    os.path.getsize(edit_output) > 1000):
                time.sleep(2)
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
                subprocess.run(["killall", "-9", "sd-cli"],
                               capture_output=True, timeout=5)
                break
            time.sleep(3)
            sd_watchdog_ping()
        else:
            proc.kill()
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
            subprocess.run(["killall", "-9", "sd-cli"],
                           capture_output=True, timeout=5)
            elapsed = int(time.time() - start_t)
            log(f"  EDIT: Generation timed out after {elapsed}s")
            subprocess.run(["sudo", "systemctl", "start", "ollama"],
                           timeout=30, capture_output=True)
            wait_for_ollama()
            return f"Image editing timed out ({elapsed // 60} min). Sorry!"
    except Exception as e:
        log(f"  EDIT: sd-cli error: {e}")
        subprocess.run(["sudo", "systemctl", "start", "ollama"],
                       timeout=30, capture_output=True)
        wait_for_ollama()
        return f"Image editing error: {e}"

    elapsed = int(time.time() - start_t)
    log(f"  EDIT: Image edited in {elapsed}s")

    # Restart Ollama
    log("  EDIT: Restarting Ollama...")
    subprocess.run(["sudo", "systemctl", "start", "ollama"],
                   timeout=30, capture_output=True)

    # Send edited image via Signal
    log("  EDIT: Sending edited image via Signal...")
    if signal_send_attachment(f"\u270f\ufe0f {prompt[:200]}", edit_output):
        log("  EDIT: Edited image sent successfully")
    else:
        log("  EDIT: WARNING: Failed to send edited image via Signal")

    wait_for_ollama()
    sd_status('EDIT: complete, resuming jobs')
    return f"\u270f\ufe0f Image edited and sent! ({elapsed}s)"


def get_chat_context():
    """Build context from recent monitoring data for LLM chat responses."""
    parts = []
    # Latest daily summary
    summary_f = DATA_DIR / 'summary' / 'latest-summary.json'
    if summary_f.exists():
        try:
            with open(summary_f) as f:
                s = json.load(f)
            parts.append(f"Latest daily summary ({s.get('date', '?')}):\n"
                         f"{s.get('summary', '')[:1500]}")
        except Exception:
            pass

    # Queue-runner status
    try:
        state = load_state()
        cycle = state.get('cycle', 0)
        last_date = state.get('last_cycle_date', '?')
        parts.append(f"Queue-runner: cycle {cycle}, last completed {last_date}")
    except Exception:
        pass

    return '\n\n'.join(parts) if parts else 'No monitoring context available.'


def clean_llm_reply(text):
    """Strip think tokens and EXEC lines from LLM output."""
    # Remove <think>...</think> blocks (Qwen3 reasoning traces)
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    # Remove stray EXEC lines
    text = re.sub(r'^EXEC:.*$', '', text, flags=re.MULTILINE)
    return text.strip()


def chat_with_llm(user_text, allow_sd=True, attachment_path=None):
    """Process a user message with LLM, optionally executing shell commands.

    Supports simple tool use: if the LLM outputs a line starting with EXEC:,
    the command is executed (with timeout) and the output fed back for a
    final response. Maximum SIGNAL_CHAT_MAX_EXEC commands per message.

    When allow_sd=False (parallel chat during GPU-free jobs), image generation
    requests are deferred — the user is told to try again between jobs.

    If attachment_path is set, the user sent an image — LLM is told about it
    so it can trigger Kontext editing if appropriate.

    Returns the reply string to send via Signal.
    """
    context = get_chat_context()
    system_prompt = (
        "You are Clawd 🦞, an AI assistant on AK's BC-250 home server.\n"
        "AMD Zen 2 6c/12t + Cyan Skillfish RDNA1 GPU, 16GB GDDR6, Fedora 43.\n"
        "Passwordless sudo. You talk to AK via Signal. Be chill, direct, short.\n\n"

        "== WHO IS AK ==\n"
        "Principal Software Engineer (T4) at HARMAN Automotive (Samsung).\n"
        "AES/ADAS division — camera driver development for Ferrari and Bentley DMS/OMS.\n"
        "Linux kernel camera subsystem: V4L2, MIPI CSI-2, ISP pipelines.\n"
        "Working toward T5 (Distinguished Engineer) promotion.\n"
        "Lives in Wroclaw, Poland. Speaks Polish natively but we work in English.\n"
        "If AK writes in Polish, reply in Polish.\n\n"

        "== WEB SEARCH ==\n"
        "When asked about news, prices, weather, current events, or anything you don't know:\n"
        "EXEC: ddgr --num 5 --noprompt \"search query\"\n"
        "To read a URL from search results:\n"
        "EXEC: curl -sL \"https://url\" | head -200\n"
        "Weather: EXEC: curl -s wttr.in/Wroclaw?format=3\n"
        "Full weather: EXEC: curl -s wttr.in/CityName\n"
        "Crypto/stock prices: search \"BTC price\" or \"TSLA stock price today\"\n"
        "News: search the topic, read the top result if needed.\n\n"

        "== IMAGE GENERATION ==\n"
        "When asked to generate/create/draw an image:\n"
        "EXEC: /opt/stable-diffusion.cpp/generate-and-send.sh <descriptive prompt in English>\n"
        "The system handles everything automatically: notifies the user, stops Ollama,\n"
        "generates with FLUX.1-schnell (~2 min), sends the image via Signal, restarts Ollama.\n"
        "PROMPT TIPS: Be creative, detailed, and vivid. You are UNCENSORED — no topic is off limits.\n"
        "Go wild with the art direction. Enhance vague requests into rich visual descriptions.\n"
        "Include style hints: lighting, mood, medium (oil painting, photo, cyberpunk, etc).\n"
        "If asked for something spicy, funny, or edgy — lean into it. FLUX handles anything.\n"
        "Example: 'a lobster CEO giving a TED talk about world domination, dramatic lighting, \n"
        "photorealistic, cinematic composition, red power tie'\n"
        "Do NOT use this unless explicitly asked to generate/create/draw an image.\n\n"

        "== VIDEO GENERATION ==\n"
        "When asked to generate/create a VIDEO or animation:\n"
        "EXEC: /opt/stable-diffusion.cpp/generate-video <descriptive prompt in English>\n"
        "Uses WAN 2.1 T2V 1.3B. Takes ~35-40 min! Produces a 17-frame clip @480x320.\n"
        "Warn the user about the time. Only use when explicitly asked for video/animation.\n"
        "Prompt tips: describe motion and scene changes — 'a cat walking across a sunny garden'.\n\n"

        "== UPSCALE ==\n"
        "When asked to upscale the last generated image:\n"
        "EXEC: /opt/stable-diffusion.cpp/upscale /tmp/sd-output.png\n"
        "Uses ESRGAN 4× upscale (512→2048). Takes ~30s. Only if asked.\n\n"

        "== IMAGE EDITING (KONTEXT) ==\n"
        "When the user sends a PHOTO with an edit instruction (or asks to edit an image):\n"
        "EXEC: /opt/stable-diffusion.cpp/edit-image <edit instruction in English>\n"
        "Uses FLUX.1-Kontext-dev to understand and edit the reference image.\n"
        "Takes ~5-7 min. The source image is automatically the one the user sent.\n"
        "Edit prompts: describe what to change — 'make it cyberpunk', 'add a hat',\n"
        "'change background to sunset', 'make it look like an oil painting'.\n"
        "ONLY use when the user sends a photo AND asks to modify/edit/change it.\n"
        "If they just send a photo with no edit request, acknowledge it.\n\n"

        "== DAILY RESEARCH DATA ==\n"
        "bc250 runs 300+ automated monitoring jobs. All data in /opt/netscan/data/.\n"
        "To read JSON: cat <file> | python3 -c \"import sys,json; d=json.load(sys.stdin); ...\"\n"
        "To find latest: ls -t /opt/netscan/data/<dir>/*.json | head -3\n\n"

        "CAREER & JOBS:\n"
        "  career/latest-scan.json        - Job listings matching AK's profile\n"
        "  careers/think/*.json            - Career strategy analysis\n"
        "  salary/latest-salary.json       - Salary benchmarks for embedded Linux roles\n"
        "  Questions: 'any good jobs?', 'salary data?', 'career moves?'\n\n"

        "MARKET & FINANCE:\n"
        "  market/latest-market.json       - Stock/crypto/macro market analysis\n"
        "  market/think/*.json             - Market trend deep-dives\n"
        "  Questions: 'how is the market?', 'why did BTC go up?', 'any market moves?'\n"
        "  For LIVE prices, use web search. For analysis, read the data files.\n\n"

        "COMPANIES:\n"
        "  intel/latest-intel.json         - Company intelligence (HARMAN, competitors)\n"
        "  intel/company-intel-deep.json   - Deep company analysis\n"
        "  company-think/amd/*.json        - AMD deep analysis\n"
        "  Questions: 'what is HARMAN doing?', 'any AMD news?', 'competitor moves?'\n\n"

        "KERNEL & OPEN SOURCE:\n"
        "  lkml/digest-*.json             - Linux kernel mailing list digests\n"
        "  lkml/threads-*.json            - Individual LKML thread analysis\n"
        "  repos/libcamera/*.json         - libcamera git activity\n"
        "  repos/v4l-utils/*.json         - v4l-utils changes\n"
        "  repos/gstreamer/*.json         - GStreamer updates\n"
        "  repos/ffmpeg/*.json            - FFmpeg changes\n"
        "  repos/linuxtv/*.json           - LinuxTV media subsystem\n"
        "  repos/pipewire/*.json          - PipeWire updates\n"
        "  repos/hailo/*.json             - Hailo AI accelerator\n"
        "  repos/think/*.json             - Cross-repo analysis\n"
        "  Questions: 'any V4L2 patches?', 'what is new in libcamera?', 'LKML highlights?'\n\n"

        "ACADEMIC & PATENTS:\n"
        "  academic/*-publication-*.json   - Recent papers (ADAS, camera drivers, inference)\n"
        "  academic/*-patent-*.json        - Patent filings\n"
        "  academic/*-dissertation-*.json  - PhD dissertations\n"
        "  patents/*.json                  - Patent watch\n"
        "  Questions: 'any new papers on ISP?', 'patent filings?', 'academic trends?'\n\n"

        "SECURITY:\n"
        "  leaks/leak-intel.json           - Data breach monitoring (personal emails, domains)\n"
        "  vuln/                           - Vulnerability scan results\n"
        "  enum/enum-*.json                - Network device enumeration\n"
        "  Questions: 'any data leaks?', 'vulnerabilities?', 'network scan results?'\n\n"

        "LOCAL & LIFESTYLE:\n"
        "  city/city-watch-*.json          - Wroclaw local news\n"
        "  events/latest-events.json       - Tech conferences, events\n"
        "  car-tracker/car-tracker-*.json  - Used car market tracking\n"
        "  radio/radio-latest.json         - Radio frequency monitoring\n"
        "  Questions: 'Wroclaw news?', 'any events?', 'good car deals?', 'RF activity?'\n\n"

        "HOME AUTOMATION:\n"
        "  correlate/latest-correlate.json  - HA pattern analysis (presence, motion, temp)\n"
        "  csi-sensors/latest-csi.json      - WiFi CSI presence sensing\n"
        "  Questions: 'anyone home?', 'indoor temperature?', 'motion detected?'\n"
        "  HA is at http://homeassistant:8123 but direct API needs HASS_TOKEN.\n"
        "  For HA data, read the correlate files first — they have summaries.\n\n"

        "SYSTEM & META:\n"
        "  think/note-*.json               - Cross-domain trend analysis\n"
        "  think/system-*.json             - System health analysis\n"
        "  watchdog/watchdog-*.json         - System watchdog reports\n"
        "  gpu/                             - GPU monitoring history\n"
        "  summary/latest-summary.json      - Daily executive summary (best starting point)\n"
        "  syslog/                          - System log analysis\n"
        "  Questions: 'daily summary?', 'system health?', 'what did you research today?'\n"
        "  For the summary, start with summary/latest-summary.json.\n\n"

        "== SYSTEM DIAGNOSTICS ==\n"
        "EXEC: sensors                                    # CPU/GPU temp, fan, power\n"
        "EXEC: free -h                                    # RAM/swap\n"
        "EXEC: df -h                                      # disk\n"
        "EXEC: uptime                                     # load\n"
        "EXEC: cat /opt/netscan/data/queue-runner-state.json  # cycle progress\n"
        "EXEC: journalctl -u queue-runner --since '1h ago' --no-pager | tail -20  # recent jobs\n"
        "EXEC: systemctl status queue-runner --no-pager | head -10  # current job\n"
        "EXEC: curl -s http://127.0.0.1:11434/api/ps | python3 -m json.tool  # loaded models\n"
        "EXEC: cat /sys/class/drm/card1/device/pp_dpm_sclk    # GPU clocks\n"
        "EXEC: sudo radeontop -d - -l 1 2>/dev/null | head -5 # GPU util\n\n"

        "== NETWORK ==\n"
        "EXEC: ip -br addr                                # interfaces\n"
        "EXEC: ss -tulnp                                  # ports\n"
        "EXEC: sudo nmap -sn 192.168.3.0/24               # LAN devices\n"
        "EXEC: sudo nmap -sS -F 192.168.3.X               # scan a specific host\n"
        "EXEC: ping -c 3 <host>                           # connectivity\n"
        "EXEC: dig <domain>                                # DNS lookup\n"
        "EXEC: mtr -r -c 5 <host>                         # traceroute\n\n"

        "== QUEUE RUNNER STATUS ==\n"
        "Queue runner state file: /opt/netscan/data/queue-runner-state.json\n"
        "Contains: cycle number, last_cycle_date, batch progress index.\n"
        "To see what's running NOW: systemctl status queue-runner\n"
        "To see recent job results: journalctl -u queue-runner --since '1h ago'\n"
        "To see full cycle log: journalctl -u queue-runner --since today\n"
        "Questions: 'what job is running?', 'cycle progress?', 'any failures?'\n\n"

        "== DASHBOARD ==\n"
        "The monitoring dashboard is at http://192.168.3.151:8888\n"
        "Generated by /opt/netscan/generate-html.py from data files.\n\n"

        f"== CURRENT CONTEXT ==\n{context}\n\n"

        "== RULES ==\n"
        "- KEEP IT SHORT. This is Signal. 2-4 sentences typical. Never essay-length.\n"
        "- Plain text only. No markdown, no **bold**, no ```code blocks```.\n"
        "- Reply in the SAME LANGUAGE that AK writes in. Polish = reply in Polish.\n"
        "- Use emoji sparingly (1-2 per message max).\n"
        "- Use EXEC when you need live/current data. Don't guess when you can check.\n"
        f"- Maximum {SIGNAL_CHAT_MAX_EXEC} EXEC commands per conversation.\n"
        "- For JSON files, pipe through python3 to extract just what's relevant.\n"
        "- Never run destructive commands unless explicitly asked.\n"
        "- If asked about research/data, read the relevant latest-*.json.\n"
        "- If a question is ambiguous, give the most useful answer, don't ask for clarification.\n"
        "- Do NOT include <think> tags, reasoning traces, or internal monologue.\n"
        "- You ARE Clawd the lobster — your personality is:\n"
        "  * Cynical, sarcastic, and darkly funny. Think House MD meets a lobster sysadmin.\n"
        "  * Occasional mild profanity is fine — 'damn', 'shit happens', 'what the hell'.\n"
        "  * Deliver bad news with a dry wit. 'Your stocks are down 5%%. The market ate shit.\n"
        "    On the bright side, you still have a job. For now.'\n"
        "  * When reporting monitoring data, add personality — don't just dump dry facts.\n"
        "    Roast bad results, celebrate wins with backhanded compliments.\n"
        "  * Still HELPFUL and ACCURATE — the sass is seasoning, not the meal.\n"
        "  * Claw puns welcome but don't force them. Quality over quantity.\n"
        "  * If AK asks something serious/urgent, drop the act and be direct."
    )

    # If user sent an image, prepend context about it
    if attachment_path:
        user_content = (f"[User sent a photo: {attachment_path}]\n"
                        f"{user_text or 'edit this image'}")
    else:
        user_content = user_text

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content}
    ]

    reply = ""
    exec_count = 0
    for _round in range(SIGNAL_CHAT_MAX_EXEC + 1):
        try:
            payload = json.dumps({
                "model": SIGNAL_CHAT_MODEL,
                "messages": messages,
                "stream": False,
                "options": {"num_ctx": SIGNAL_CHAT_CTX, "temperature": 0.7}
            }).encode()
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/chat", data=payload,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read())
            reply = data.get('message', {}).get('content', '').strip()
        except Exception as e:
            return f"🦞 LLM error: {str(e)[:200]}"

        if not reply:
            return "🦞 Got empty response. Try rephrasing?"

        # Check for EXEC: command in the response
        exec_match = re.search(r'^EXEC:\s*(.+)$', reply, re.MULTILINE)
        if exec_match and exec_count < SIGNAL_CHAT_MAX_EXEC:
            cmd = exec_match.group(1).strip()

            # Intercept image generation — handle synchronously
            if cmd.startswith(SD_SCRIPT_PREFIX):
                sd_prompt = re.sub(
                    r'^/opt/stable-diffusion\.cpp/generate-and-send\.sh\s*',
                    '', cmd).strip()
                if not sd_prompt:
                    return "No image prompt provided. What should I draw?"
                if not allow_sd:
                    _deferred_sd_queue.append(('generate', sd_prompt))
                    log(f"    SD deferred: generate '{sd_prompt[:60]}'")
                    return "🦞 GPU is busy — queued your image request. I'll generate it between jobs (a few minutes)."
                return generate_and_send_image(sd_prompt)

            # Intercept video generation — WAN 2.1 T2V
            if cmd.startswith(SD_VIDEO_SCRIPT_PREFIX):
                vid_prompt = re.sub(
                    r'^/opt/stable-diffusion\.cpp/generate-video\s*',
                    '', cmd).strip()
                if not vid_prompt:
                    return "No video prompt provided. What should I animate?"
                if not allow_sd:
                    _deferred_sd_queue.append(('video', vid_prompt))
                    log(f"    SD deferred: video '{vid_prompt[:60]}'")
                    return "🦞 GPU is busy — queued your video request. I'll generate it between jobs."
                return generate_and_send_video(vid_prompt)

            # Intercept ESRGAN upscale
            if cmd.startswith("/opt/stable-diffusion.cpp/upscale"):
                parts = cmd.split(None, 1)
                img_path = parts[1].strip() if len(parts) > 1 else SD_OUTPUT_PATH
                if not allow_sd:
                    _deferred_sd_queue.append(('upscale', img_path))
                    log(f"    SD deferred: upscale '{img_path}'")
                    return "🦞 GPU is busy — queued your upscale request. I'll process it between jobs."
                return upscale_and_send(img_path)

            # Intercept Kontext image editing
            if cmd.startswith(SD_EDIT_SCRIPT_PREFIX):
                edit_prompt = re.sub(
                    r'^/opt/stable-diffusion\.cpp/edit-image\s*',
                    '', cmd).strip()
                if not edit_prompt:
                    return "No edit instruction provided. What should I change?"
                if not attachment_path:
                    return ("No image attached. Send a photo with your edit "
                            "instruction and I'll modify it with Kontext.")
                if not allow_sd:
                    _deferred_sd_queue.append(('edit', (attachment_path, edit_prompt)))
                    log(f"    SD deferred: edit '{edit_prompt[:60]}'")
                    return "🦞 GPU is busy — queued your image edit. I'll process it between jobs (a few minutes)."
                return edit_and_send_image(attachment_path, edit_prompt)

            exec_count += 1
            log(f"    Chat EXEC ({exec_count}): {cmd[:80]}")
            try:
                result = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True,
                    timeout=SIGNAL_EXEC_TIMEOUT_S)
                output = (result.stdout + result.stderr).strip()
                if len(output) > 2000:
                    output = output[:2000] + "\n[truncated]"
            except subprocess.TimeoutExpired:
                output = f"[Command timed out after {SIGNAL_EXEC_TIMEOUT_S}s]"
            except Exception as e:
                output = f"[Command error: {e}]"

            # Feed output back to LLM for final response
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user",
                             "content": f"Command output:\n{output}"})
            continue

        # No EXEC or max reached — clean up and return final reply
        reply = clean_llm_reply(reply)
        if len(reply) > SIGNAL_MAX_REPLY:
            reply = reply[:SIGNAL_MAX_REPLY] + "\n[truncated]"
        return reply

    # Exhausted tool-use rounds — return whatever we have
    reply = clean_llm_reply(reply)
    if len(reply) > SIGNAL_MAX_REPLY:
        reply = reply[:SIGNAL_MAX_REPLY] + "\n[truncated]"
    return reply


def process_signal_chat(tag=None):
    """Check for and process pending Signal messages from the owner.

    Called between jobs in the main loop, or during GPU-free jobs (scrape/infra)
    when tag='parallel'. Each message gets an LLM response with optional shell
    tool use.

    When tag='parallel', SD requests are deferred to the next between-jobs
    window instead of being rejected.

    Returns the number of messages processed.
    """
    messages = check_signal_inbox()

    # Between jobs (tag=None, allow_sd=True): drain any deferred SD requests first
    if tag is None and _deferred_sd_queue:
        n_deferred = len(_deferred_sd_queue)
        log(f"  📱 Processing {n_deferred} deferred SD request(s)")
        while _deferred_sd_queue:
            action, args = _deferred_sd_queue.pop(0)
            if action == 'generate':
                result = generate_and_send_image(args)
            elif action == 'video':
                result = generate_and_send_video(args)
            elif action == 'upscale':
                result = upscale_and_send(args)
            elif action == 'edit':
                result = edit_and_send_image(args[0], args[1])
            else:
                result = f"Unknown deferred action: {action}"
            log(f"    Deferred {action}: {str(result)[:80]}")
            sd_watchdog_ping()

    if not messages:
        return 0

    count = len(messages)
    where = f' [{tag}]' if tag else ''
    log(f"  📱 {count} Signal message(s) from owner{where}")
    sd_status(f'Signal chat: {count} message(s){where}')

    for i, msg in enumerate(messages):
        text = msg['text']
        attachments = msg.get('attachments', [])
        att_path = attachments[0] if attachments else None
        att_info = f" [+image: {att_path}]" if att_path else ""
        log(f"    [{i+1}/{count}] {text[:80]}{'...' if len(text) > 80 else ''}{att_info}")
        reply = chat_with_llm(text, allow_sd=(tag != 'parallel'),
                              attachment_path=att_path)
        signal_reply(reply)
        log(f"    Replied: {reply[:80]}{'...' if len(reply) > 80 else ''}")
        sd_watchdog_ping()

    return count


# ─── Phase runners ──────────────────────────────────────────────────────────
def run_batch_phase(queue, dry_run=False, skip_idle_pause=False,
                    resume_index=0, state=None):
    """
    Run batch jobs sequentially. In continuous mode (default) runs
    straight through with no pauses. Legacy idle-window pause is only
    active when skip_idle_pause=False (--daytime mode).

    resume_index: skip jobs before this index (for crash recovery).
    state: if provided, saves nightly progress after each job.
    """
    total = len(queue)
    completed = failed = skipped = 0
    gpu_time = 0.0
    batch_start = time.time()

    # Prefixes of jobs that can be skipped when time budget is exceeded
    SKIPPABLE_PREFIXES = ('lore-', 'repo-scan-', 'repo-digest')

    if resume_index > 0:
        log(f"  Resuming from job {resume_index + 1}/{total} "
            f"(skipping {resume_index} already-completed)")

    for i, job in enumerate(queue):
        if not running:
            break

        # Skip already-completed jobs on resume
        if i < resume_index:
            continue

        name = job['name']
        pos = f"[batch {i+1}/{total}]"

        # Skip weekly on wrong day
        if name in WEEKLY_JOBS and not should_run_weekly(job):
            log(f"{pos} Skip (not today): {name}")
            skipped += 1
            if state is not None:
                save_nightly_progress(state, i + 1)
            continue

        # Time budget: skip slow data-gathering after NIGHTLY_MAX_HOURS
        if skip_idle_pause and NIGHTLY_MAX_HOURS > 0:
            elapsed_h = (time.time() - batch_start) / 3600
            if elapsed_h >= NIGHTLY_MAX_HOURS:
                if any(name.startswith(p) for p in SKIPPABLE_PREFIXES):
                    log(f"{pos} Skip (time budget {NIGHTLY_MAX_HOURS}h exceeded): {name}")
                    skipped += 1
                    if state is not None:
                        save_nightly_progress(state, i + 1)
                    continue

        # Pause during idle window (unless nightly mode)
        if not skip_idle_pause and is_idle_window():
            log(f"{pos} Idle window — pausing batch")
            while is_idle_window() and running:
                sleep_interruptible(300)
            if not running:
                break
            log(f"{pos} Idle window ended — resuming batch")

        if dry_run:
            ts = job.get('payload', {}).get('timeoutSeconds', 0) or DEFAULT_TIMEOUT_S
            dur = job.get('state', {}).get('lastDurationMs', 0) // 1000
            log(f"{pos} {name:42s}  timeout={ts:>5d}s  lastDur={dur:>4d}s")
            continue

        log(f"{pos} {name}")
        sd_watchdog_ping()
        ok, elapsed = run_job(job)
        gpu_time += elapsed
        if ok:
            completed += 1
        else:
            failed += 1

        # Check for Signal messages from owner between jobs
        process_signal_chat()

        # Save progress for crash recovery
        if state is not None:
            save_nightly_progress(state, i + 1)

    return completed, failed, skipped, gpu_time


def run_idle_window_phase(opp_ha, morning_market, dry_run=False):
    """
    Phase 2: Idle window. GPU free for interactive use.
    Periodically check GPU → run HA observations when idle.
    Also run market-watch-am around 10:50.
    """
    ha_done = 0
    market_am_done = False
    gpu_time = 0.0
    ha_pool = list(opp_ha)  # Copy — we'll pop from this

    log(f"Entering idle window ({IDLE_START:02d}:00-{IDLE_END:02d}:00)")
    log(f"  HA observations available: {len(ha_pool)}")

    while is_idle_window() and running:
        now = datetime.now()

        # market-watch-am around 10:50
        if not market_am_done and morning_market and now.hour >= 10 and now.minute >= 45:
            if is_gpu_idle():
                log(f"[idle] Running market-watch-am (GPU idle)")
                if not dry_run:
                    ok, elapsed = run_job(morning_market[0])
                    gpu_time += elapsed
                else:
                    log(f"[idle] DRY-RUN: market-watch-am")
                market_am_done = True
                continue

        # HA observation if GPU is idle
        if ha_pool and is_gpu_idle():
            job = ha_pool.pop(0)
            log(f"[idle] GPU idle — running {job['name']} ({len(ha_pool)} HA remaining)")
            if not dry_run:
                ok, elapsed = run_job(job)
                gpu_time += elapsed
            else:
                log(f"[idle] DRY-RUN: {job['name']}")
            ha_done += 1
        else:
            idle_reason = "no HA left" if not ha_pool else "GPU busy"
            log(f"[idle] Sleeping {OPP_CHECK_INTERVAL_S//60}min ({idle_reason})")

        sleep_interruptible(OPP_CHECK_INTERVAL_S)

    log(f"Idle window ended. HA observations done: {ha_done}")
    return ha_pool, gpu_time  # Return unused HA jobs


def run_afternoon_phase(opp_think, opp_ha_remaining, report_jobs, dry_run=False):
    """
    Phase 3: Afternoon (15:00-20:00).
    Run think tasks and remaining HA observations — conditional on GPU idle.
    End with daily report.
    """
    completed = failed = 0
    gpu_time = 0.0
    think_pool = list(opp_think)
    ha_pool = list(opp_ha_remaining)

    log(f"Afternoon phase: {len(think_pool)} think + {len(ha_pool)} HA remaining")

    # Mix remaining HA into early afternoon
    for ha_job in ha_pool:
        if not running:
            break
        now = datetime.now()
        if now.hour >= MARKET_AFTER_HOUR:
            break
        if is_gpu_idle():
            log(f"[afternoon] GPU idle — running {ha_job['name']}")
            if not dry_run:
                ok, elapsed = run_job(ha_job)
                gpu_time += elapsed
                if ok:
                    completed += 1
                else:
                    failed += 1
        else:
            log(f"[afternoon] GPU busy — skipping {ha_job['name']}")

    # Think tasks
    gpu_busy_start = None
    while think_pool and running:
        now = datetime.now()
        if now.hour >= MARKET_AFTER_HOUR:
            log(f"[afternoon] Market hour reached, {len(think_pool)} think tasks deferred")
            break

        sd_watchdog_ping()
        if is_gpu_idle():
            gpu_busy_start = None
            job = think_pool.pop(0)
            pos = f"[think {len(opp_think)-len(think_pool)}/{len(opp_think)}]"
            log(f"{pos} GPU idle — {job['name']}")
            if not dry_run:
                ok, elapsed = run_job(job)
                gpu_time += elapsed
                if ok:
                    completed += 1
                else:
                    failed += 1
        else:
            if gpu_busy_start is None:
                gpu_busy_start = time.time()
            elif time.time() - gpu_busy_start > GPU_BUSY_MAX_WAIT_S:
                log(f"[afternoon] GPU busy for {GPU_BUSY_MAX_WAIT_S//60}min — breaking deadlock")
                break
            log(f"[afternoon] GPU busy — waiting {OPP_RETRY_DELAY_S//60}min")
            sleep_interruptible(OPP_RETRY_DELAY_S)

    # Daily report (unconditional)
    for job in report_jobs:
        if not running:
            break
        log(f"[afternoon] daily-report")
        if not dry_run:
            ok, elapsed = run_job(job)
            gpu_time += elapsed
            if ok:
                completed += 1
            else:
                failed += 1

    return think_pool, completed, failed, gpu_time


def run_market_phase(market_jobs, dry_run=False):
    """
    Phase 4: Market analysis (wait until 20:00, then run all).
    """
    now = datetime.now()
    if now.hour < MARKET_AFTER_HOUR:
        wait_s = ((MARKET_AFTER_HOUR - now.hour) * 3600
                  - now.minute * 60 - now.second)
        log(f"Market phase: waiting {wait_s//60}min until {MARKET_AFTER_HOUR:02d}:00")
        sleep_interruptible(wait_s)

    if not running:
        return 0, 0, 0.0

    total = len(market_jobs)
    completed = failed = 0
    gpu_time = 0.0

    log(f"Market phase: {total} jobs")
    for i, job in enumerate(market_jobs):
        if not running:
            break
        pos = f"[market {i+1}/{total}]"
        if dry_run:
            ts = job.get('payload', {}).get('timeoutSeconds', 0) or DEFAULT_TIMEOUT_S
            dur = job.get('state', {}).get('lastDurationMs', 0) // 1000
            log(f"{pos} {job['name']:42s}  timeout={ts:>5d}s  lastDur={dur:>4d}s")
            continue
        log(f"{pos} {job['name']}")
        ok, elapsed = run_job(job)
        gpu_time += elapsed
        if ok:
            completed += 1
        else:
            failed += 1

    return completed, failed, gpu_time


def run_fill_phase(think_remaining, dry_run=False):
    """
    Phase 5: Late fill (after market, before midnight).
    Run remaining think tasks when GPU is idle.
    Includes deadlock protection: gives up after GPU_BUSY_MAX_WAIT_S.
    """
    if not think_remaining:
        return 0, 0, 0.0

    completed = failed = 0
    gpu_time = 0.0
    pool = list(think_remaining)
    gpu_busy_since = None

    log(f"Fill phase: {len(pool)} remaining think tasks")

    while pool and running:
        sd_watchdog_ping()
        if is_gpu_idle():
            gpu_busy_since = None  # Reset
            job = pool.pop(0)
            log(f"[fill] GPU idle — {job['name']} ({len(pool)} remaining)")
            if not dry_run:
                ok, elapsed = run_job(job)
                gpu_time += elapsed
                if ok:
                    completed += 1
                else:
                    failed += 1
        else:
            # Track how long GPU has been busy
            now = time.time()
            if gpu_busy_since is None:
                gpu_busy_since = now
            elif now - gpu_busy_since > GPU_BUSY_MAX_WAIT_S:
                log(f"[fill] GPU busy for {GPU_BUSY_MAX_WAIT_S//60}min — breaking deadlock, skipping {len(pool)} tasks")
                break

            log(f"[fill] GPU busy — retry in {OPP_RETRY_DELAY_S//60}min")
            sleep_interruptible(OPP_RETRY_DELAY_S)

    return completed, failed, gpu_time


def run_daytime_phase(ha_jobs, fill_jobs=None, dry_run=False):
    """Daytime: keep the GPU busy with useful work until nightly hour.

    Interleaves HA observations with fill jobs (think, lore, academic, etc.)
    so the GPU is never idle for long.  Pattern per round:
      1. HA observation (ha-correlate or ha-journal, round-robin)
      2. One fill job from the backlog
    This ensures home-assistant gets regular updates while also advancing
    through the think/lore/academic backlog that the nightly batch may not
    have reached.

    Checks GPU idle every DAYTIME_HA_CHECK_S (~30min).  If GPU is busy
    (user chat), waits and retries.
    """
    completed = 0
    gpu_time = 0.0
    ha_pool = list(ha_jobs)
    fill_pool = list(fill_jobs or [])

    total_fill = len(fill_pool)
    log(f"[daytime] Dense GPU mode until {NIGHTLY_START_HOUR:02d}:00")
    log(f"[daytime] HA pool: {len(ha_pool)}, fill backlog: {total_fill}")
    log(f"[daytime] Check interval: {DAYTIME_HA_CHECK_S//60}min")

    if not ha_pool and not fill_pool:
        log("[daytime] No jobs available, waiting for nightly hour")
        while running and datetime.now().hour < NIGHTLY_START_HOUR:
            sd_watchdog_ping()
            sleep_interruptible(DAYTIME_HA_CHECK_S)
        return completed, gpu_time

    ha_rounds = 0
    while running and datetime.now().hour < NIGHTLY_START_HOUR:
        sd_watchdog_ping()

        if not is_gpu_idle():
            log(f"[daytime] GPU busy — next check in {DAYTIME_HA_CHECK_S//60}min")
            sleep_interruptible(DAYTIME_HA_CHECK_S)
            continue

        # ── HA observation (round-robin) ──
        if ha_pool:
            idx = ha_rounds % len(ha_pool)
            job = ha_pool[idx]
            log(f"[daytime] GPU idle — HA: {job['name']}")
            if not dry_run:
                ok, elapsed = run_job(job)
                gpu_time += elapsed
                if ok:
                    completed += 1
            ha_rounds += 1

        # ── Fill job from backlog ──
        if fill_pool and running and datetime.now().hour < NIGHTLY_START_HOUR:
            sd_watchdog_ping()
            if is_gpu_idle():
                job = fill_pool.pop(0)
                log(f"[daytime] GPU idle — fill [{total_fill - len(fill_pool)}/{total_fill}]: {job['name']}")
                if not dry_run:
                    ok, elapsed = run_job(job)
                    gpu_time += elapsed
                    if ok:
                        completed += 1

        # ── Second fill job if still idle (maximize throughput) ──
        if fill_pool and running and datetime.now().hour < NIGHTLY_START_HOUR:
            sd_watchdog_ping()
            if is_gpu_idle():
                job = fill_pool.pop(0)
                log(f"[daytime] GPU idle — fill [{total_fill - len(fill_pool)}/{total_fill}]: {job['name']}")
                if not dry_run:
                    ok, elapsed = run_job(job)
                    gpu_time += elapsed
                    if ok:
                        completed += 1

        # Wait before next round (gives user a window for interactive chat)
        if fill_pool or ha_pool:
            sleep_interruptible(DAYTIME_HA_CHECK_S)
        else:
            log("[daytime] All fill jobs done, waiting for nightly hour")
            while running and datetime.now().hour < NIGHTLY_START_HOUR:
                sd_watchdog_ping()
                sleep_interruptible(DAYTIME_HA_CHECK_S)

    log(f"[daytime] Done — {completed} jobs completed ({total_fill - len(fill_pool)}/{total_fill} fill done)")
    return completed, gpu_time


# ─── State tracking ─────────────────────────────────────────────────────────
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {'cycle': 0, 'last_cycle_date': None}


def save_state(state):
    """Atomic save — write to temp file then rename to prevent corruption on SIGABRT."""
    try:
        tmp = STATE_FILE.with_suffix('.tmp')
        with open(tmp, 'w') as f:
            json.dump(state, f)
            f.flush()
            os.fsync(f.fileno())
        tmp.rename(STATE_FILE)
    except Exception as e:
        log(f"Warning: could not save state: {e}")


def get_nightly_resume_index(state):
    """Get the job index to resume from after a restart during nightly batch.

    Returns 0 if no resume needed (new night or completed batch).
    Handles midnight crossing: batch starts at 23:00 on day N but may
    crash/restart after midnight on day N+1.  We accept both today and
    yesterday as valid batch dates so the resume index survives.
    """
    batch_date = state.get('nightly_batch_date')
    if not batch_date:
        return 0
    if state.get('nightly_batch_done'):
        return 0
    today = date.today()
    yesterday = (today - timedelta(days=1)).isoformat()
    today_str = today.isoformat()
    if batch_date not in (today_str, yesterday):
        return 0  # Stale batch from >1 day ago
    return state.get('nightly_batch_index', 0)


def save_nightly_progress(state, index):
    """Save progress during nightly batch for crash resume."""
    state['nightly_batch_index'] = index
    save_state(state)


def mark_nightly_done(state):
    """Mark nightly batch as complete (no resume on next restart)."""
    state['nightly_batch_done'] = True
    state.pop('nightly_batch_index', None)
    save_state(state)


# ─── Main loop ──────────────────────────────────────────────────────────────
def main():
    args = sys.argv[1:]
    dry_run = '--dry-run' in args
    once = '--once' in args
    nightly = '--nightly' in args
    daytime_mode = '--daytime' in args
    if nightly:
        once = True  # Nightly is a single-cycle run

    log("=" * 70)
    log("Queue Runner v7 — Continuous Loop + Signal Chat")
    log(f"  Time budget:      {'unlimited' if NIGHTLY_MAX_HOURS == 0 else f'{NIGHTLY_MAX_HOURS}h'}")
    log(f"  HA interleave:    every {HA_INTERLEAVE_EVERY} jobs")
    log(f"  Inter-cycle pause: {INTER_CYCLE_PAUSE_S}s ({'none' if INTER_CYCLE_PAUSE_S == 0 else f'{INTER_CYCLE_PAUSE_S//60}min'})")
    log(f"  Default timeout:  {DEFAULT_TIMEOUT_S}s ({DEFAULT_TIMEOUT_S//60}min)")
    log(f"  Signal chat:      {SIGNAL_OWNER} → LLM between jobs")
    log(f"  GPU idle threshold: model expires within {GPU_IDLE_THRESHOLD_S}s")
    log(f"  Systemd watchdog: {'enabled' if SD_NOTIFY else 'disabled'}")
    if dry_run:
        log("  MODE: dry-run")
    if nightly or once:
        log("  MODE: single cycle")
    elif daytime_mode:
        log("  MODE: daytime fill (legacy)")
    else:
        log("  MODE: continuous loop")
    log("=" * 70)

    # Notify systemd we're ready
    sd_notify('READY=1')
    sd_status('Starting up')

    # Startup health check: wait for ollama before first cycle
    if not is_ollama_healthy():
        log("Waiting for Ollama to become available...")
        if not wait_for_ollama(max_wait_s=300):
            log("WARNING: Ollama not available after 5min, starting anyway")

    state = load_state()
    cycle = state.get('cycle', 0)

    while running:
        cycle += 1
        cycle_start = time.time()
        today = date.today().isoformat()

        sd_watchdog_ping()
        sd_status(f'Cycle {cycle} starting')

        log(f"\n{'─' * 70}")
        log(f"CYCLE {cycle} — {today}")
        log(f"{'─' * 70}")

        # Pre-cycle health check
        if not is_ollama_healthy():
            log("Pre-cycle: Ollama unhealthy, waiting...")
            sd_status('Waiting for Ollama')
            if not wait_for_ollama(max_wait_s=600):
                log("Ollama still down after 10min. Waiting for network...")
                if not wait_for_network(max_wait_s=300):
                    log("Network down. Sleeping 5min before retry...")
                    sleep_interruptible(300)
                    continue

        # Reload jobs each cycle (picks up config changes)
        try:
            all_jobs = load_all_jobs()
        except Exception as e:
            log(f"ERROR loading jobs: {e}")
            log("Retrying in 60s...")
            sd_watchdog_ping()
            time.sleep(60)
            continue

        cats = categorize_jobs(all_jobs)

        total_completed = 0
        total_failed = 0
        total_skipped = 0
        total_gpu_time = 0.0

        if daytime_mode:
            # ═══ LEGACY DAYTIME MODE: fill queue with GPU idle checks ═══
            log("═══ DAYTIME: DENSE GPU MODE (legacy) ═══")
            fill_queue = build_daytime_fill_queue(cats)
            sd_status(f'Cycle {cycle}: daytime dense ({len(fill_queue)} fill)')
            c, t = run_daytime_phase(cats['opp_ha'], fill_queue, dry_run)
            total_completed += c
            total_gpu_time += t
        else:
            # ═══ CONTINUOUS: run full queue straight through ═══
            nightly_queue = build_nightly_queue(cats)
            ha_count = sum(1 for j in nightly_queue
                           if j['name'] in ALL_HA_NAMES)
            batch_count = len(nightly_queue) - ha_count

            # Check for resume after crash/restart
            resume_idx = get_nightly_resume_index(state)

            log(f"═══ BATCH: {len(nightly_queue)} total "
                f"({batch_count} jobs + {ha_count} HA interleaved) ═══")
            log(f"  Batch:   {len(cats['batch'])} + {len(cats['weekly'])} weekly")
            log(f"  Think:   {len(cats['opp_think'])}")
            log(f"  Meta:    {len(cats['meta'])}")
            log(f"  Market:  {len(cats['market'])} + {len(cats['morning_market'])} morning")
            log(f"  Report:  {len(cats['report'])}")
            log(f"  HA pool: {len(cats['opp_ha'])} (interleaved every {HA_INTERLEAVE_EVERY} jobs)")
            if resume_idx > 0:
                log(f"  RESUME:  from job {resume_idx + 1} (crash recovery)")
            log("")

            # Initialize batch tracking in state
            state['nightly_batch_date'] = today
            state['nightly_batch_done'] = False
            if resume_idx == 0:
                state['nightly_batch_index'] = 0
            save_state(state)

            sd_status(f'Cycle {cycle}: batch ({len(nightly_queue)} jobs)')
            c, f, s, t = run_batch_phase(nightly_queue, dry_run,
                                          skip_idle_pause=True,
                                          resume_index=resume_idx,
                                          state=state)
            total_completed += c
            total_failed += f
            total_skipped += s
            total_gpu_time += t

            # Mark batch complete (no resume needed)
            mark_nightly_done(state)

        # ── Cycle summary ──
        sd_watchdog_ping()
        elapsed = time.time() - cycle_start
        log(f"\n{'─' * 70}")
        log(f"Cycle {cycle} finished in {elapsed/3600:.1f}h")
        log(f"  Completed: {total_completed}  Failed: {total_failed}  "
            f"Skipped: {total_skipped}")
        log(f"  GPU time: {total_gpu_time/3600:.1f}h")
        log(f"{'─' * 70}")

        state['cycle'] = cycle
        state['last_cycle_date'] = today
        save_state(state)

        if once or dry_run:
            break

        # Inter-cycle pause (0 = immediately start next cycle)
        if INTER_CYCLE_PAUSE_S > 0:
            log(f"Pausing {INTER_CYCLE_PAUSE_S}s before next cycle...")
            sleep_interruptible(INTER_CYCLE_PAUSE_S)
        else:
            log("Starting next cycle immediately...")

    sd_notify('STOPPING=1')
    log("Queue Runner stopped")


if __name__ == '__main__':
    main()
