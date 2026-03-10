#!/usr/bin/env python3
"""
leak-monitor.py — Cyber Threat Intelligence & Leak Monitor
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
White-hat OSINT tool for monitoring data leaks, exposed credentials,
ransomware victims, and threat intelligence across multiple sources.

Sources:
  1. Ransomware.live API v2 — ransomware victim tracking & group intel
  2. Ransomlook.io API      — open-source leak site intelligence
  3. GitHub Search           — exposed secrets & leaked code (dork queries)
  4. CISA KEV               — known exploited vulnerabilities (govt feed)
  5. Telegram OSINT          — public CTI channel monitoring (no API key)
  6. Feodo C2 Tracker        — botnet C2 IP & malware tracking (abuse.ch)
  7. HIBP Breach Catalog     — Polish & major breach detection (credentials/PII)
  8. Hudson Rock             — infostealer exposure for Polish domains
  9. LeakForum.io            — underground forum (Atom feed, PL DBs & code leaks)
 10. Cracked.sh              — underground forum (auth scrape, PL DBs & code leaks)
 11. Ahmia.fi                — dark web search engine (clearnet → Tor hidden service index)

Security:
  - Tor SOCKS5 on 127.0.0.1:9050 (client-only, no relay/exit)
  - Downloaded content sandboxed — no raw secrets written to disk
  - Content analysis separated from acquisition (anti-prompt-injection)
  - Findings stored as hashed indicators + summaries only

Output:
  DB:    /opt/netscan/data/leaks/leak-intel.json
  Log:   /opt/netscan/data/leaks/leak-monitor.log

Cron:
  0 4 * * * flock -w 1200 /tmp/ollama-gpu.lock python3 /opt/netscan/leak-monitor.py
"""

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from llm_sanitize import sanitize_llm_output

# ── Config ─────────────────────────────────────────────────────────────────

OLLAMA_URL = "http://localhost:11434"
OLLAMA_CHAT = f"{OLLAMA_URL}/api/chat"
OLLAMA_MODEL = "qwen3:14b"

SIGNAL_RPC = "http://127.0.0.1:8080/api/v1/rpc"
SIGNAL_FROM = "+<BOT_PHONE>"
SIGNAL_TO = "+<OWNER_PHONE>"

LEAKS_DIR = Path("/opt/netscan/data/leaks")
LEAKS_DB = LEAKS_DIR / "leak-intel.json"
RAW_LEAK_FILE = LEAKS_DIR / "raw-leak.json"
LOG_FILE = LEAKS_DIR / "leak-monitor.log"

TOR_SOCKS = "socks5h://127.0.0.1:9050"
TOR_PORT = 9050

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

# ── Watch Targets ──────────────────────────────────────────────────────────
# Companies to monitor for ransomware/leaks/credential exposure
WATCH_DOMAINS = [
    "harman.com", "samsung.com", "jbl.com", "akg.com",
    "harmanpro.com", "harmaninternational.com",
]
WATCH_KEYWORDS = [
    "harman", "samsung", "HARMAN", "SAMSUNG",
    "JBL", "AKG", "Mark Levinson", "Harman Kardon",
    "Crown Audio", "Martin Lighting", "DigiTech",
]

# Extended watchlist: supply chain, automotive partners, tech companies
SUPPLY_CHAIN = [
    "qualcomm", "nvidia", "nxp", "renesas", "bosch",
    "continental", "tesla", "bmw", "mercedes", "stellantis",
    "hexagon", "aptiv", "mobileye", "intel",
]

# Keywords for general interesting leaks (firmware, low-level code)
INTEREST_KEYWORDS = [
    "firmware leak", "source code leak", "bootloader", "UEFI",
    "kernel source", "embedded", "automotive", "ADAS",
    "infostealer", "stealer logs", "combolist",
    "zero-day", "0day", "CVE-2026", "CVE-2025",
    "camera firmware", "ISP firmware", "V4L2",
]

# ── GitHub Dork Queries for exposed secrets ────────────────────────────────
GITHUB_DORKS = [
    # HARMAN-specific
    'org:harman "password" OR "secret" OR "token"',
    '"harman.com" filename:.env',
    '"harman.com" filename:credentials',
    # General interesting leaks
    'filename:.env "DB_PASSWORD" "samsung"',
    'filename:id_rsa "harman" OR "samsung"',
    '"HARMAN" filename:config extension:yaml password',
    # Automotive/embedded
    '"automotive" filename:.env DB_PASSWORD',
    'extension:bin "firmware" "harman" OR "samsung"',
]

# ── Ransomware Groups to Track ─────────────────────────────────────────────
TRACKED_GROUPS = [
    "lockbit3", "cl0p", "alphv", "akira", "qilin",
    "play", "ransomhub", "bianlian", "medusa", "rhysida",
    "hunters", "cactus", "blackbasta", "8base", "inc",
]

# ── CISA Known Exploited Vulnerabilities feed ──────────────────────────────
CISA_KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"

# Vendors we care about in CISA KEV
KEV_WATCH_VENDORS = [
    "samsung", "harman", "qualcomm", "google", "linux",
    "android", "microsoft", "apple", "cisco", "nvidia",
    "intel", "arm", "broadcom", "mediatek",
]

# ── Underground Forum Credentials ──────────────────────────────────────────
FORUM_CREDS_PATH = "/opt/netscan/forum-creds.json"

# Forum sections to monitor on each site
LEAKFORUM_SECTIONS = [
    "Forum-Leaks", "Forum-Other-Leaks", "Forum-Premium-Leaks",
    "Forum-Combolist", "Forum-Cloud-Logs", "Forum-Source-codes",
    "Forum-Cracked-Programs", "Forum-IT-Programing--93",
    "Forum-Security-Pentesting--94",
]
CRACKED_SECTIONS = [
    "Forum-Premium-Leaks", "Forum-Other-Leaks", "Forum-Combolists--297",
    "Forum-Source-codes", "Forum-Cracked-Programs",
    "Forum-General-Hacking", "Forum-Hacking-Tools-and-Programs",
]

# Keywords for matching interesting forum threads
FORUM_PL_KEYWORDS = [
    "poland", "polish", "polska", "polsk",
    ".pl ", ".pl/", ".pl\"", "@wp.pl", "@onet.pl", "@interia.pl",
    "allegro", "morele", "x-kom", "olx", "pracuj",
    "mbank", "pkobp", "ing.pl", "santander",
    "cdprojekt", "gog.com", "empik",
    "pesel", "nip", "dowod", "regon",
    "baza danych", "wyciek", "dane osobowe",
    "gov.pl", "zus", "epuap",
]
FORUM_CODE_KEYWORDS = [
    "source code", "sourcecode", "src leak", "firmware",
    "kernel", "bootloader", "uefi", "bios",
    "driver leak", "sdk leak", "api key",
    "git dump", "git repo", "gitlab", "decompil",
    "reverse engineer", "disassembl", "ida pro",
    "0day", "zero-day", "exploit", "poc ",
    "embedded", "iot ", "scada", "plc ",
    "samsung", "qualcomm", "nvidia", "intel",
    "automotive", "adas", "v4l2", "camera",
    "low level", "low-level", "baremetal", "bare metal",
    "arm ", "aarch64", "risc-v", "mips",
]

# ── Telegram CTI channels (verified working with t.me/s/ preview) ──────────
TELEGRAM_CHANNELS = [
    "infostealers",         # infostealer & credential leak tracking
    "CyberSecurityPulse",   # cybersecurity news feed
    "cveNotify",            # real-time CVE notifications
    "BreachBase",           # breach data aggregation
    "RansomFeed",           # ransomware tracking feed
    "CERT_UA",              # Ukraine CERT (active threat intel)
]

# ── Feodo C2 Tracker (abuse.ch) ────────────────────────────────────────────
FEODO_RECENT_URL = "https://feodotracker.abuse.ch/downloads/ipblocklist.txt"
FEODO_C2_URL = "https://feodotracker.abuse.ch/browse/"

# ── HIBP Breach Catalog ────────────────────────────────────────────────────
HIBP_BREACHES_URL = "https://haveibeenpwned.com/api/v3/breaches"

# ── Hudson Rock — infostealer exposure for Polish domains ──────────────────
HUDSON_ROCK_URL = "https://cavalier.hudsonrock.com/api/json/v2/osint-tools/search-by-domain"

# Polish domains to check for stolen credentials (infostealers)
PL_WATCH_DOMAINS = [
    # Major Polish services
    "allegro.pl", "olx.pl", "wp.pl", "onet.pl", "interia.pl",
    "o2.pl", "morele.net", "x-kom.pl", "ceneo.pl", "pracuj.pl",
    # Government & critical infrastructure
    "gov.pl", "zus.pl", "epuap.gov.pl", "mbank.pl", "ing.pl",
    "pkobp.pl", "santander.pl", "bnpparibas.pl",
    # Tech / gaming / e-commerce
    "cdprojekt.com", "cdprojektred.com", "gog.com",
    "empik.com", "mediamarkt.pl", "reserved.com",
    # HARMAN & Samsung (always tracked)
    "harman.com", "samsung.com",
]

# Poland-specific keywords for database/leak detection in Telegram & forums
PL_LEAK_KEYWORDS = [
    # Polish language leak terms
    "polska", "poland", "polish", "baza danych", "wyciek",
    # Polish domains & TLDs
    ".pl ", ".pl/", ".pl\"", "@wp.pl", "@onet.pl", "@interia.pl", "@o2.pl",
    # PESEL / NIP / ID numbers
    "pesel", "nip", "dowod osobisty", "regon",
    # Polish companies & services
    "allegro", "morele", "cdprojekt", "empik", "mbank", "pkobp",
    "olx.pl", "pracuj", "x-kom", "reserved",
    # Database/dump indicators
    "database download", "db dump", "sql dump", "combolist",
    "credential dump", "user database", "full db",
    "stealer logs", "infostealer", "redline logs", "raccoon logs",
    "lumma logs", "stealc logs", "vidar logs",
]

# Source code leak keywords
SOURCE_CODE_KEYWORDS = [
    "source code", "sourcecode", "src leak", "git dump", "git repo",
    "full source", "leaked source", "decompiled", "firmware dump",
    "kernel source", "bootloader source", "sdk leak",
    "internal repo", "private repo", "proprietary code",
    "apk source", "ipa source", "mobile app source",
]

# ── Logging ────────────────────────────────────────────────────────────────

_log_lines = []

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    _log_lines.append(line)

def save_log():
    LEAKS_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(f"\n{'='*60}\n")
        f.write(f"Run: {datetime.now().isoformat()}\n")
        for line in _log_lines:
            f.write(line + "\n")


# ── HTTP Helpers ───────────────────────────────────────────────────────────

def fetch_url(url, timeout=30, headers=None, use_tor=False):
    """Fetch URL, return text or None. Supports Tor SOCKS5 proxy."""
    h = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/json,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "identity",
        "DNT": "1",
    }
    if headers:
        h.update(headers)

    if use_tor:
        # Use Tor SOCKS5 proxy via urllib with socks
        # Fallback: use torsocks wrapper for .onion
        return _fetch_via_tor(url, timeout, h)

    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return data.decode(charset, errors="replace")
    except Exception as e:
        log(f"  fetch error {url[:80]}: {e}")
        return None


def _fetch_via_tor(url, timeout, headers):
    """Fetch URL through Tor SOCKS5 proxy using torsocks + curl."""
    import subprocess
    try:
        cmd = [
            "curl", "-s", "--max-time", str(timeout),
            "--socks5-hostname", "127.0.0.1:9050",
            "-H", f"User-Agent: {headers.get('User-Agent', UA)}",
            "-H", "Accept: text/html,application/json,*/*",
            "-H", "DNT: 1",
            url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
        if result.returncode == 0 and result.stdout:
            return result.stdout
        return None
    except Exception as e:
        log(f"  tor fetch error {url[:60]}: {e}")
        return None


def fetch_json(url, timeout=30, use_tor=False):
    """Fetch URL and parse JSON."""
    text = fetch_url(url, timeout, use_tor=use_tor)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        log(f"  JSON parse error: {e}")
        return None


def strip_html(text):
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&#\d+;", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def hash_indicator(text):
    """SHA256 hash of a finding indicator for dedup without storing raw data."""
    return hashlib.sha256(text.lower().strip().encode()).hexdigest()[:16]


# ── Signal Notification ───────────────────────────────────────────────────

def signal_send(msg):
    """Send Signal notification."""
    try:
        payload = json.dumps({
            "jsonrpc": "2.0", "method": "send",
            "params": {
                "account": SIGNAL_FROM,
                "recipient": [SIGNAL_TO],
                "message": msg,
            },
            "id": "leak-monitor",
        })
        req = urllib.request.Request(
            SIGNAL_RPC, data=payload.encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception as e:
        log(f"  Signal send failed: {e}")
        return False


# ── LLM Analysis ──────────────────────────────────────────────────────────

def call_ollama(system_prompt, user_prompt, temperature=0.3, max_tokens=2000):
    """Call Ollama for LLM analysis. Returns text or None."""
    # Health check
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=10) as r:
            tags = json.loads(r.read())
            models = [m["name"] for m in tags.get("models", [])]
            if not any(OLLAMA_MODEL in m for m in models):
                log(f"  Model {OLLAMA_MODEL} not available")
                return None
    except Exception as e:
        log(f"  Ollama health check failed: {e}")
        return None

    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "/nothink\n" + user_prompt},
        ],
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": 24576},
    }).encode()

    req = urllib.request.Request(OLLAMA_CHAT, data=payload, headers={
        "Content-Type": "application/json",
    })
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            result = json.loads(resp.read())
            content = result.get("message", {}).get("content", "")
            elapsed = time.time() - t0
            tokens = result.get("eval_count", len(content.split()))
            tps = tokens / elapsed if elapsed > 0 else 0
            log(f"  LLM: {elapsed:.0f}s, {tokens} tok ({tps:.1f} t/s)")
            # Strip thinking/reasoning preamble (qwen3 sometimes leaks CoT)
            content = _strip_thinking(content)
            return content
    except Exception as e:
        log(f"  Ollama call failed: {e}")
        return None


def _strip_thinking(text):
    """Remove chain-of-thought reasoning and Chinese text from LLM output."""
    return sanitize_llm_output(text) if text else text


# ── Database ───────────────────────────────────────────────────────────────

def load_db():
    """Load the leak intelligence database."""
    if LEAKS_DB.exists():
        try:
            return json.load(open(LEAKS_DB))
        except Exception:
            pass
    return {
        "version": 1,
        "findings": [],       # list of finding dicts
        "seen_hashes": [],    # dedup hashes (last 10000)
        "runs": [],           # run metadata
        "stats": {},
    }


def save_db(db):
    """Save DB with retention (keep last 90 days of findings)."""
    cutoff = (datetime.now() - timedelta(days=90)).isoformat()
    db["findings"] = [f for f in db["findings"] if f.get("first_seen", "") > cutoff]
    db["seen_hashes"] = db["seen_hashes"][-10000:]
    db["runs"] = db["runs"][-90:]
    LEAKS_DIR.mkdir(parents=True, exist_ok=True)
    with open(LEAKS_DB, "w") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def is_seen(db, indicator_hash):
    """Check if a finding has already been seen."""
    return indicator_hash in db["seen_hashes"]


def add_finding(db, source, category, title, summary, url="",
                severity="info", relevance=0, raw_snippet=""):
    """Add a new finding to the database."""
    h = hash_indicator(f"{source}:{title}:{url}")
    if is_seen(db, h):
        return False  # already seen

    db["seen_hashes"].append(h)
    finding = {
        "hash": h,
        "source": source,
        "category": category,
        "title": title[:200],
        "summary": summary[:1000],
        "url": url[:500],
        "severity": severity,
        "relevance": relevance,
        "first_seen": datetime.now().isoformat(),
        # No raw secrets/passwords stored — only sanitized summaries
    }
    db["findings"].append(finding)
    return True


# ══════════════════════════════════════════════════════════════════════════
#  Source 1: Ransomware.live API v2
# ══════════════════════════════════════════════════════════════════════════

def scan_ransomware_live(db):
    """Check ransomware.live for recent victims matching watch targets."""
    log("🔒 Scanning ransomware.live API...")
    base = "https://api.ransomware.live/v2"
    new_findings = 0

    # 1. Recent victims (last 7 days)
    data = fetch_json(f"{base}/recentvictims", timeout=30)
    if data and isinstance(data, list):
        log(f"  {len(data)} recent victims fetched")
        for victim in data:
            vname = (victim.get("victim") or "").lower()
            vgroup = victim.get("group", "unknown")
            vdate = victim.get("attackdate", "")
            vcountry = victim.get("country", "")
            vurl = victim.get("website", "")

            # Check against watch targets
            is_direct = any(kw.lower() in vname for kw in WATCH_KEYWORDS)
            is_supply = any(kw.lower() in vname for kw in SUPPLY_CHAIN)
            is_poland = vcountry == "PL"

            if is_direct or is_supply or is_poland:
                severity = "critical" if is_direct else "high" if is_supply else "medium"
                relevance = 10 if is_direct else 7 if is_supply else 4
                title = f"Ransomware victim: {victim.get('victim', vname)} [{vgroup}]"
                summary = (f"Group: {vgroup}, Date: {vdate}, "
                           f"Country: {vcountry}, URL: {vurl}")
                if add_finding(db, "ransomware.live", "ransomware_victim",
                              title, summary, severity=severity,
                              relevance=relevance):
                    new_findings += 1
                    log(f"  ⚠ NEW: {title}")
    else:
        log("  Failed to fetch recent victims")

    # 2. Specific searches for watch domains
    for domain in WATCH_DOMAINS[:4]:  # rate limit friendly
        keyword = domain.split(".")[0]
        results = fetch_json(f"{base}/searchvictims/{keyword}", timeout=20)
        if results and isinstance(results, list):
            for v in results:
                title = f"Victim match [{keyword}]: {v.get('victim', '?')} [{v.get('group', '?')}]"
                summary = f"Date: {v.get('attackdate', '?')}, Country: {v.get('country', '?')}"
                add_finding(db, "ransomware.live", "ransomware_victim",
                           title, summary, severity="critical", relevance=9)
        time.sleep(2)  # respect rate limits

    # 3. Check recent Poland-specific victims
    pl_victims = fetch_json(f"{base}/countryvictims/PL", timeout=20)
    if pl_victims and isinstance(pl_victims, list):
        recent_pl = [v for v in pl_victims
                     if v.get("attackdate", "") >= (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")]
        for v in recent_pl:
            title = f"Poland victim: {v.get('victim', '?')} [{v.get('group', '?')}]"
            summary = f"Date: {v.get('attackdate', '?')}, Sector: {v.get('sector', '?')}"
            if add_finding(db, "ransomware.live", "ransomware_victim",
                          title, summary, severity="medium", relevance=5):
                new_findings += 1

    log(f"  ransomware.live: {new_findings} new findings")
    return new_findings


# ══════════════════════════════════════════════════════════════════════════
#  Source 2: Ransomlook.io API
# ══════════════════════════════════════════════════════════════════════════

def scan_ransomlook(db):
    """Check ransomlook.io for recent leak site posts."""
    log("🔍 Scanning ransomlook.io API...")
    new_findings = 0

    # Recent posts from all groups
    data = fetch_json("https://www.ransomlook.io/api/recent", timeout=30)
    if not data:
        # Try alternate endpoint
        data = fetch_json("https://www.ransomlook.io/api/recentvictims", timeout=30)
    if data and isinstance(data, list):
        log(f"  {len(data)} recent posts fetched")
        for post in data[:200]:  # cap processing
            pname = ""
            pgroup = ""
            if isinstance(post, dict):
                pname = (post.get("post_title") or post.get("victim") or "").lower()
                pgroup = post.get("group_name") or post.get("group") or ""
            elif isinstance(post, str):
                pname = post.lower()

            is_direct = any(kw.lower() in pname for kw in WATCH_KEYWORDS)
            is_supply = any(kw.lower() in pname for kw in SUPPLY_CHAIN)

            if is_direct or is_supply:
                severity = "critical" if is_direct else "high"
                title = f"Ransomlook: {pname[:100]} [{pgroup}]"
                summary = f"Detected via ransomlook.io recent posts"
                if add_finding(db, "ransomlook.io", "ransomware_victim",
                              title, summary, severity=severity,
                              relevance=9 if is_direct else 6):
                    new_findings += 1
                    log(f"  ⚠ NEW: {title}")
    else:
        log("  ransomlook.io: no data or API changed")

    log(f"  ransomlook.io: {new_findings} new findings")
    return new_findings


# ══════════════════════════════════════════════════════════════════════════
#  Source 3: GitHub Search (exposed secrets, leaked code)
# ══════════════════════════════════════════════════════════════════════════

def scan_github(db):
    """Search GitHub for exposed secrets and leaked code via search API."""
    log("🐙 Scanning GitHub for exposed secrets...")
    new_findings = 0

    # GitHub code search requires authentication
    gh_token = os.environ.get("GITHUB_TOKEN", "")
    if not gh_token:
        # Try loading from .env
        env_file = Path(os.path.expanduser("~/.openclaw/.env"))
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("GITHUB_TOKEN="):
                    gh_token = line.split("=", 1)[1].strip().strip('"')
    if not gh_token:
        # Try gh CLI authentication
        try:
            result = subprocess.run(['gh', 'auth', 'token'],
                                   capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and result.stdout.strip():
                gh_token = result.stdout.strip()
                log("  Using GitHub token from gh CLI")
        except Exception:
            pass
    if not gh_token:
        log("  No GITHUB_TOKEN — skipping code search")
        log("  Fix: set GITHUB_TOKEN in ~/.openclaw/.env or install gh CLI")
        return 0

    for dork in GITHUB_DORKS:
        encoded = urllib.parse.quote(dork)
        url = f"https://api.github.com/search/code?q={encoded}&sort=indexed&order=desc&per_page=10"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "ClawdBot-CTI/1.0 (white-hat-research)",
            "Authorization": f"token {gh_token}",
        }

        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())
                items = data.get("items", [])
                log(f"  dork [{dork[:40]}...]: {len(items)} results")

                for item in items[:5]:
                    repo = item.get("repository", {}).get("full_name", "?")
                    path = item.get("path", "?")
                    html_url = item.get("html_url", "")

                    title = f"GitHub exposed: {repo}/{path}"
                    summary = f"Dork: {dork}, Repo: {repo}, File: {path}"

                    # Determine severity based on what was found
                    severity = "high"
                    if any(kw in dork.lower() for kw in ["password", "secret", "key", "credential"]):
                        severity = "critical"

                    is_direct = any(kw.lower() in (repo + path).lower() for kw in WATCH_KEYWORDS[:4])
                    relevance = 9 if is_direct else 5

                    if add_finding(db, "github", "exposed_secret",
                                  title, summary, url=html_url,
                                  severity=severity, relevance=relevance):
                        new_findings += 1
                        log(f"  ⚠ NEW: {title}")

        except urllib.error.HTTPError as e:
            if e.code == 403:
                log(f"  GitHub rate limited (403), skipping remaining dorks")
                break
            elif e.code == 422:
                log(f"  GitHub dork invalid (422): {dork[:50]}")
            else:
                log(f"  GitHub error {e.code}: {e}")
        except Exception as e:
            log(f"  GitHub search error: {e}")

        time.sleep(6)  # GitHub rate limit: 10 req/min unauthenticated

    log(f"  github: {new_findings} new findings")
    return new_findings


# ══════════════════════════════════════════════════════════════════════════
#  Source 4: CISA Known Exploited Vulnerabilities
# ══════════════════════════════════════════════════════════════════════════

def scan_cisa_kev(db):
    """Check CISA KEV catalog for newly-added exploited vulnerabilities."""
    log("🛡 Scanning CISA Known Exploited Vulnerabilities...")
    new_findings = 0

    data = fetch_json(CISA_KEV_URL, timeout=30)
    if not data or "vulnerabilities" not in data:
        log("  CISA KEV: failed to fetch catalog")
        return 0

    vulns = data["vulnerabilities"]
    log(f"  {len(vulns)} total CVEs in catalog")

    # Only look at recently added CVEs (last 30 days)
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    for v in vulns:
        date_added = v.get("dateAdded", "")
        if date_added < cutoff:
            continue

        cve_id = v.get("cveID", "?")
        vendor = v.get("vendorProject", "").lower()
        product = v.get("product", "")
        desc = v.get("shortDescription", "")
        due_date = v.get("dueDate", "")
        action = v.get("requiredAction", "")

        # Check if it matches our watch vendors
        is_watched = any(wv in vendor for wv in KEV_WATCH_VENDORS)
        is_interesting = any(kw.lower() in desc.lower()
                           for kw in ["embedded", "firmware", "automotive",
                                      "remote code", "authentication bypass",
                                      "privilege escalation", "zero-day"])

        if is_watched or is_interesting:
            severity = "high" if is_watched else "medium"
            relevance = 7 if is_watched else 4

            title = f"CISA KEV: {cve_id} — {v.get('vendorProject', '?')} {product}"
            summary = (f"Added: {date_added}, Due: {due_date}\n"
                      f"Desc: {desc[:300]}\n"
                      f"Action: {action[:200]}")

            if add_finding(db, "cisa_kev", "exploited_vuln",
                          title, summary,
                          url=f"https://nvd.nist.gov/vuln/detail/{cve_id}",
                          severity=severity, relevance=relevance):
                new_findings += 1
                log(f"  ⚠ NEW: {title[:80]}")

    log(f"  cisa_kev: {new_findings} new findings")
    return new_findings


# ══════════════════════════════════════════════════════════════════════════
#  Source 5: Telegram CTI Channels (public web preview scraping)
# ══════════════════════════════════════════════════════════════════════════

def scan_telegram_public(db):
    """Scrape public Telegram channels via t.me web preview (no API needed)."""
    log("📱 Scanning public Telegram channels...")
    new_findings = 0

    for channel in TELEGRAM_CHANNELS:
        # Telegram's public preview endpoint (shows ~20 recent messages)
        url = f"https://t.me/s/{channel}"
        html = fetch_url(url, timeout=25)
        if not html:
            log(f"  {channel}: failed to fetch")
            continue

        # Parse message text blocks
        messages = re.findall(
            r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
            html, re.DOTALL
        )

        relevant = 0
        for msg_html in messages:
            text = strip_html(msg_html)
            if len(text) < 20:
                continue

            combined = text.lower()
            is_direct = any(kw.lower() in combined for kw in WATCH_KEYWORDS)
            is_supply = any(kw.lower() in combined for kw in SUPPLY_CHAIN)
            is_interesting = any(kw.lower() in combined
                                for kw in ["leak", "breach", "dump",
                                           "stealer", "credential",
                                           "database", "source code",
                                           "firmware", "combolist"])
            is_poland = any(kw.lower() in combined for kw in PL_LEAK_KEYWORDS)
            is_source = any(kw.lower() in combined for kw in SOURCE_CODE_KEYWORDS)

            if is_direct or is_supply or is_interesting or is_poland or is_source:
                if is_direct:
                    severity, relevance = "critical", 9
                elif is_poland:
                    severity, relevance = "high", 7
                    category = "pl_database" if any(x in combined for x in
                        ["database", "dump", "db ", "combo", "stealer", "credential",
                         "baza danych", "wyciek", "pesel"]) else "channel_mention"
                elif is_source:
                    severity, relevance = "high", 7
                elif is_supply:
                    severity, relevance = "high", 5
                else:
                    severity, relevance = "low", 2

                # Detect download indicators
                has_download = any(x in combined for x in
                    ["download", "mega.nz", "anonfiles", "gofile", "mediafire",
                     "pixeldrain", "send.exploit", "catbox", "transfer.sh",
                     "magnet:", "torrent", ".rar", ".zip", ".7z", ".sql",
                     "telegra.ph", "paste", "link in", "dm for"])

                cat = "pl_database" if is_poland and any(x in combined for x in
                    ["database", "dump", "credential", "stealer", "combo",
                     "pesel", "baza"]) else \
                    "source_code_leak" if is_source else "channel_mention"

                dl_tag = " [DOWNLOAD]" if has_download else ""

                finding_title = f"TG/{channel}{dl_tag}: {text[:100]}"
                summary = text[:500]

                if add_finding(db, "telegram", cat,
                              finding_title, summary,
                              url=f"https://t.me/s/{channel}",
                              severity=severity, relevance=relevance):
                    new_findings += 1
                    relevant += 1

        log(f"  TG/{channel}: {len(messages)} messages, {relevant} new relevant")
        time.sleep(3)

    log(f"  telegram: {new_findings} new findings")
    return new_findings


# ══════════════════════════════════════════════════════════════════════════
#  Source 6: Feodo C2 Tracker (abuse.ch) — botnet & malware tracking
# ══════════════════════════════════════════════════════════════════════════

def scan_feodo_c2(db):
    """Check Feodo C2 tracker for recent botnet infrastructure."""
    log("🦠 Scanning Feodo C2 Tracker (abuse.ch)...")
    new_findings = 0

    # Fetch the C2 blocklist (IP-based, text format)
    text = fetch_url(FEODO_RECENT_URL, timeout=20)
    if not text:
        log("  Feodo: failed to fetch blocklist")
        return 0

    # Parse: lines starting with IP (not comments)
    ips = [line.strip() for line in text.splitlines()
           if line.strip() and not line.startswith("#")]

    log(f"  {len(ips)} active C2 IPs in blocklist")

    # Also try to scrape the Feodo browse page for recent C2 entries
    html = fetch_url("https://feodotracker.abuse.ch/browse/", timeout=20)
    if html:
        # Parse table rows for recent C2 servers
        rows = re.findall(
            r'<tr>\s*<td[^>]*>(\d{4}-\d{2}-\d{2}[^<]*)</td>\s*'
            r'<td[^>]*>([^<]*)</td>\s*'   # IP
            r'<td[^>]*>([^<]*)</td>\s*'   # Port
            r'<td[^>]*>([^<]*)</td>\s*'   # Status
            r'<td[^>]*>([^<]*)</td>',     # Malware
            html, re.DOTALL
        )
        log(f"  {len(rows)} recent C2 entries from browse page")

        # Only flag new/interesting C2 infrastructure (last 7 days)
        cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        for date_str, ip, port, status, malware in rows[:50]:
            if date_str.strip()[:10] < cutoff:
                continue

            malware = malware.strip()
            # Flag notable malware families
            notable = ["emotet", "trickbot", "dridex", "qakbot", "icedid",
                       "pikabot", "bumblebee", "latrodectus", "smokeloader"]
            is_notable = any(n in malware.lower() for n in notable)

            if is_notable:
                title = f"Feodo C2: {malware} @ {ip.strip()}:{port.strip()}"
                summary = f"Date: {date_str.strip()}, Status: {status.strip()}, Malware: {malware}"
                if add_finding(db, "feodo", "c2_infrastructure",
                              title, summary,
                              url=f"https://feodotracker.abuse.ch/browse/host/{ip.strip()}/",
                              severity="medium", relevance=3):
                    new_findings += 1

    # Record C2 count as a general intel finding (once per day)
    today = datetime.now().strftime("%Y-%m-%d")
    summary_title = f"Feodo C2 daily: {len(ips)} active C2 IPs ({today})"
    if not is_seen(db, hash_indicator(summary_title)):
        add_finding(db, "feodo", "c2_stats",
                   summary_title,
                   f"Active C2 IP count: {len(ips)} as of {today}",
                   severity="info", relevance=1)

    log(f"  feodo: {new_findings} new findings")
    return new_findings


# ══════════════════════════════════════════════════════════════════════════
#  Source 7: HIBP Breach Catalog — Poland-focused breach detection
# ══════════════════════════════════════════════════════════════════════════

def scan_hibp_breaches(db):
    """Check Have I Been Pwned for new breaches, especially Polish ones."""
    log("🔐 Scanning HIBP breach catalog...")
    new_findings = 0

    data = fetch_json(HIBP_BREACHES_URL, timeout=30)
    if not data or not isinstance(data, list):
        log("  HIBP: failed to fetch breach catalog")
        return 0

    log(f"  {len(data)} total breaches in HIBP catalog")

    # Track recently added breaches (last 60 days)
    cutoff = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")

    for breach in data:
        name = breach.get("Name", "")
        domain = breach.get("Domain", "").lower()
        added = breach.get("AddedDate", "")[:10]
        breach_date = breach.get("BreachDate", "")
        pwn_count = breach.get("PwnCount", 0)
        data_classes = breach.get("DataClasses", [])
        description = breach.get("Description", "")
        desc_lower = (name + " " + domain + " " + description).lower()

        # ── Check if Poland-related ──
        is_polish = (
            domain.endswith(".pl") or
            any(kw in desc_lower for kw in
                ["poland", "polska", "polish", "allegro", "morele",
                 "cdprojekt", "wp.pl", "onet.pl", "interia", "olx.pl",
                 "mbank", "pkobp", "łódź", "lodz", "warsaw", "warszawa",
                 "krakow", "kraków", "wroclaw", "wrocław", "gdansk", "gdańsk"])
        )

        # ── Check if it's a direct target ──
        is_direct = any(kw.lower() in desc_lower for kw in WATCH_KEYWORDS)

        # ── Check if recently added (regardless of breach date) ──
        is_recent = added >= cutoff

        # ── Check for interesting data classes ──
        has_creds = any(c in data_classes for c in
            ["Passwords", "Password hints", "Security questions and answers"])
        has_pii = any(c in data_classes for c in
            ["Physical addresses", "Phone numbers", "Government issued IDs",
             "Dates of birth", "Social security numbers"])
        has_financial = any(c in data_classes for c in
            ["Credit cards", "Bank account numbers", "Financial transactions"])

        # Determine what to report
        if is_direct:
            severity = "critical"
            relevance = 10
            category = "target_breach"
        elif is_polish:
            severity = "high"
            relevance = 8
            category = "pl_database"
        elif is_recent and pwn_count >= 500000 and has_creds:
            # Large new breach with credentials — noteworthy
            severity = "medium"
            relevance = 5
            category = "major_breach"
        else:
            continue  # Skip — not interesting enough

        # Format data classes compactly
        classes_str = ", ".join(data_classes[:6])
        title = f"HIBP: {name} ({domain or 'no domain'})"
        summary = (f"Breach date: {breach_date}, Added: {added}, "
                  f"Records: {pwn_count:,}\n"
                  f"Data: {classes_str}\n"
                  f"{'🇵🇱 POLISH BREACH' if is_polish else ''}"
                  f"{'💰 Has financial data' if has_financial else ''}"
                  f"{'🔑 Has credentials' if has_creds else ''}"
                  f"{'🪪 Has PII' if has_pii else ''}")

        if add_finding(db, "hibp", category,
                      title, summary,
                      url=f"https://haveibeenpwned.com/PwnedWebsites#{name}",
                      severity=severity, relevance=relevance):
            new_findings += 1
            emoji = "🇵🇱" if is_polish else "⚠"
            log(f"  {emoji} NEW: {title} — {pwn_count:,} records")

    log(f"  hibp: {new_findings} new findings")
    return new_findings


# ══════════════════════════════════════════════════════════════════════════
#  Source 8: Hudson Rock — Infostealer exposure for Polish domains
# ══════════════════════════════════════════════════════════════════════════

def scan_hudson_rock(db):
    """Check Hudson Rock for infostealer-stolen credentials on Polish domains."""
    log("🕵 Scanning Hudson Rock infostealer data...")
    new_findings = 0

    for domain in PL_WATCH_DOMAINS:
        url = f"{HUDSON_ROCK_URL}?domain={domain}"
        data = fetch_json(url, timeout=60)
        if data is None:
            time.sleep(10)
            data = fetch_json(url, timeout=60)  # retry once
        if not data or not isinstance(data, dict):
            time.sleep(3)
            continue

        total = data.get("total", 0)
        employees = data.get("employees", 0)
        users = data.get("users", 0)

        if total == 0:
            time.sleep(2)
            continue

        # Get stealer family breakdown
        stealers = data.get("stealerFamilies", {})
        top_stealers = sorted(
            [(k, v) for k, v in stealers.items() if k != "total" and isinstance(v, int)],
            key=lambda x: -x[1]
        )[:5]
        stealer_str = ", ".join(f"{k}:{v:,}" for k, v in top_stealers)

        # Last compromise dates
        last_emp = data.get("last_employee_compromised", "")[:10]
        last_usr = data.get("last_user_compromised", "")[:10]

        # Determine severity based on exposure level and recency
        is_target = domain in [d for d in WATCH_DOMAINS]
        recent_30d = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        if is_target:
            severity = "critical"
            relevance = 9
        elif employees > 5 or (last_emp and last_emp >= recent_30d):
            severity = "high"
            relevance = 7
        elif total > 10000:
            severity = "high"
            relevance = 6
        elif total > 1000:
            severity = "medium"
            relevance = 4
        else:
            severity = "low"
            relevance = 2

        title = f"Hudson Rock: {domain} — {total:,} stolen creds"
        summary = (f"Total: {total:,} (employees: {employees}, users: {users:,})\n"
                  f"Last employee compromised: {last_emp or 'N/A'}\n"
                  f"Last user compromised: {last_usr or 'N/A'}\n"
                  f"Top stealers: {stealer_str}")

        if add_finding(db, "hudson_rock", "infostealer_exposure",
                      title, summary,
                      url=f"https://cavalier.hudsonrock.com/api/json/v2/osint-tools/search-by-domain?domain={domain}",
                      severity=severity, relevance=relevance):
            new_findings += 1
            log(f"  ⚠ NEW: {domain}: {total:,} stolen creds "
                f"({employees} emp, {users:,} users)")

        time.sleep(4)  # respect rate limits — Hudson Rock is generous but limited

    log(f"  hudson_rock: {new_findings} new findings")
    return new_findings


# ══════════════════════════════════════════════════════════════════════════
#  Source 9: LeakForum.io — Underground forum (Atom feed, no login needed)
# ══════════════════════════════════════════════════════════════════════════

def _load_forum_creds():
    """Load forum credentials from JSON file."""
    try:
        with open(FORUM_CREDS_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _mybb_login(base_url, username, password):
    """Login to a MyBB forum, return opener with auth cookies."""
    import http.cookiejar
    import ssl as _ssl
    ctx = _ssl.create_default_context()
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj),
        urllib.request.HTTPSHandler(context=ctx),
    )
    opener.addheaders = [
        ("User-Agent", UA),
        ("Accept", "text/html,application/xhtml+xml,*/*"),
        ("Accept-Language", "en-US,en;q=0.9"),
    ]
    # Step 1: GET login page for CSRF token
    try:
        login_url = f"{base_url}/member.php?action=login"
        resp = opener.open(login_url, timeout=20)
        page = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        log(f"  MyBB login GET failed for {base_url}: {e}")
        return None

    m = re.search(r'name="my_post_key"[^>]*value="([^"]+)"', page)
    if not m:
        # Also try JavaScript variable assignment
        m = re.search(r'my_post_key\s*=\s*"([^"]+)"', page)
    if not m:
        log(f"  MyBB CSRF token not found on {base_url}")
        return None
    post_key = m.group(1)

    # Step 2: POST login — try quick_login first (no CAPTCHA), fall back to regular
    # Quick login uses different field names on some forums
    login_data = urllib.parse.urlencode({
        "action": "do_login",
        "url": f"{base_url}/index.php",
        "my_post_key": post_key,
        "quick_login": "1",
        "quick_username": username,
        "quick_password": password,
        "quick_remember": "yes",
        "submit": "Login",
    }).encode("utf-8")

    try:
        resp = opener.open(f"{base_url}/member.php", login_data, timeout=20)
        result = resp.read().decode("utf-8", errors="replace")
        if username.lower() in result.lower():
            log(f"  Logged in to {base_url} as {username}")
            return opener
        # Quick login failed — try standard (may fail on CAPTCHA sites)
        login_data2 = urllib.parse.urlencode({
            "action": "do_login",
            "url": f"{base_url}/index.php",
            "my_post_key": post_key,
            "username": username,
            "password": password,
            "remember": "yes",
            "submit": "Login",
        }).encode("utf-8")
        resp2 = opener.open(f"{base_url}/member.php", login_data2, timeout=20)
        result2 = resp2.read().decode("utf-8", errors="replace")
        if username.lower() in result2.lower():
            log(f"  Logged in to {base_url} as {username} (standard form)")
            return opener
        log(f"  Login to {base_url} may have failed (username not in response)")
        return opener  # cookies may still work
    except Exception as e:
        log(f"  MyBB login POST failed for {base_url}: {e}")
        return None


def _mybb_scrape_threads(opener, base_url, section_slug, limit=25):
    """Scrape thread titles from a MyBB forum section page."""
    url = f"{base_url}/{section_slug}"
    try:
        resp = opener.open(url, timeout=25)
        html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        log(f"  scrape error {section_slug}: {e}")
        return []

    threads = []
    # MyBB SEO URLs: Thread-SLUG or Thread-SLUG--NNN
    # Also match showthread.php?tid=NNN
    for m in re.finditer(
        r'href="((?:Thread-[^"]+|showthread\.php\?tid=\d+)[^"]*)"[^>]*>\s*([^<]+)',
        html
    ):
        href, title = m.group(1), m.group(2).strip()
        if not title or len(title) < 5:
            continue
        if 'action=lastpost' in href:
            continue  # skip "last post" links
        # Clean up the URL
        if href.startswith('Thread-') or href.startswith('showthread'):
            full_url = f"{base_url}/{href}"
        elif href.startswith('http'):
            full_url = href
        else:
            full_url = f"{base_url}/{href}"
        threads.append({"title": title, "url": full_url, "section": section_slug})
        if len(threads) >= limit:
            break

    # Deduplicate by URL
    seen = set()
    unique = []
    for t in threads:
        if t["url"] not in seen:
            seen.add(t["url"])
            unique.append(t)
    return unique


def _match_forum_thread(title_lower, content_lower=""):
    """Check if a forum thread matches our interest keywords.
    Returns (is_match, category, severity, relevance, matched_keywords)."""
    combined = f"{title_lower} {content_lower}"
    matched = []

    is_poland = False
    for kw in FORUM_PL_KEYWORDS:
        if kw in combined:
            is_poland = True
            matched.append(kw.strip())

    is_code = False
    for kw in FORUM_CODE_KEYWORDS:
        if kw in combined:
            is_code = True
            matched.append(kw.strip())

    # Also check general leak indicators
    is_leak = any(x in combined for x in
        ["database", "db dump", "sql dump", "combo", "credential",
         "stealer log", "redline", "raccoon", "lumma", "vidar",
         "infostealer", "full db", "user data", "leak"])

    has_download = any(x in combined for x in
        ["download", "mega.nz", "anonfiles", "gofile", "mediafire",
         "pixeldrain", "catbox", "transfer.sh", "torrent",
         ".rar", ".zip", ".7z", ".sql", ".csv",
         "magnet:", "link in bio", "dm for"])

    if is_poland and (is_leak or has_download):
        return True, "pl_database", "high", 8, matched
    elif is_poland:
        return True, "pl_mention", "medium", 5, matched
    elif is_code:
        cat = "source_code_leak" if any(x in combined for x in
            ["source", "firmware", "kernel", "driver", "decompil",
             "reverse", "git ", "sdk"]) else "exploit_tool"
        sev = "high" if has_download else "medium"
        rel = 7 if has_download else 5
        return True, cat, sev, rel, matched
    elif is_leak and has_download:
        # Generic leak with download — still interesting
        return True, "generic_leak", "low", 3, matched

    return False, "", "", 0, []


def scan_leakforum(db):
    """Scan LeakForum.io via Atom syndication feed (no login required)."""
    log("🔓 Scanning LeakForum.io (Atom feed)...")
    new_findings = 0

    # Fetch global Atom feed (latest 50 threads)
    atom_url = "https://leakforum.io/syndication.php?type=atom1.0&limit=50"
    xml_text = fetch_url(atom_url, timeout=30)
    if not xml_text:
        log("  LeakForum: failed to fetch Atom feed")
        return 0

    # Parse Atom entries
    entries = re.findall(
        r'<entry[^>]*>(.*?)</entry>',
        xml_text, re.DOTALL
    )
    log(f"  LeakForum: {len(entries)} entries in feed")

    for entry_xml in entries:
        # Extract title
        title_m = re.search(r'<title[^>]*>\s*(?:<!\[CDATA\[)?(.+?)(?:\]\]>)?\s*</title>', entry_xml, re.DOTALL)
        title = title_m.group(1).strip() if title_m else ""
        title = re.sub(r'<[^>]+>', '', title)  # strip any HTML in title

        # Extract URL
        url_m = re.search(r'<link[^>]*href="([^"]+)"[^>]*/>', entry_xml)
        url = url_m.group(1) if url_m else ""

        # Extract content
        content_m = re.search(r'<content[^>]*>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</content>', entry_xml, re.DOTALL)
        content_raw = content_m.group(1) if content_m else ""
        content_text = strip_html(content_raw).lower()

        # Extract author
        author_m = re.search(r'<name[^>]*>\s*(?:<!\[CDATA\[)?(.+?)(?:\]\]>)?\s*</name>', entry_xml, re.DOTALL)
        author = strip_html(author_m.group(1)) if author_m else "?"

        if not title:
            continue

        title_lower = title.lower()
        is_match, cat, severity, relevance, keywords = _match_forum_thread(
            title_lower, content_text
        )

        if is_match:
            dl_tag = " [DL]" if any(x in f"{title_lower} {content_text}" for x in
                ["download", "mega.nz", "gofile", "mediafire", "pixeldrain",
                 ".rar", ".zip", ".sql", "magnet:"]) else ""
            finding_title = f"LF{dl_tag}: {title[:150]}"
            kw_str = ", ".join(keywords[:5])
            summary = f"Author: {author}\nKeywords: {kw_str}\n\n{content_text[:400]}"

            if add_finding(db, "leakforum", cat,
                          finding_title, summary,
                          url=url, severity=severity,
                          relevance=relevance):
                new_findings += 1
                log(f"  ⚠ NEW [{severity}] {cat}: {title[:80]}")

    log(f"  leakforum: {new_findings} new findings")
    return new_findings


# ══════════════════════════════════════════════════════════════════════════
#  Source 10: Cracked.sh — Underground forum (authenticated HTML scrape)
# ══════════════════════════════════════════════════════════════════════════

def scan_cracked(db):
    """Scan Cracked.sh by scraping homepage thread listings (guest view).
    Note: cracked.sh requires CAPTCHA for login, so we use the homepage
    which shows recent threads across all sections without authentication."""
    log("🔓 Scanning Cracked.sh (homepage scrape)...")
    new_findings = 0

    base = "https://cracked.sh"
    html = fetch_url(f"{base}/", timeout=30)
    if not html:
        log("  Cracked.sh: failed to fetch homepage")
        return 0

    # Extract all Thread- links from homepage
    # Matches both relative and absolute URLs, with or without quotes
    thread_refs = re.findall(r'href="((?:https?://cracked\.sh/)?Thread-[^"]+)"', html)
    # Also try unquoted href (some MyBB themes)
    thread_refs += re.findall(r'href=((?:https?://cracked\.sh/)?Thread-[^\s>"]+)', html)

    # Deduplicate and clean
    seen_urls = set()
    threads = []
    for href in thread_refs:
        # Strip action=lastpost query param
        clean_href = re.sub(r'\?action=lastpost$', '', href)
        # Normalize URL
        if clean_href.startswith('Thread-'):
            url = f"{base}/{clean_href}"
        elif clean_href.startswith('http'):
            url = clean_href
        else:
            url = f"{base}/{clean_href}"
        if url in seen_urls:
            continue
        seen_urls.add(url)
        # Decode URL-encoded title from slug
        slug = url.split('/')[-1] if '/' in url else url
        slug = slug.replace('Thread-', '', 1)
        # Remove trailing --NNNNN (MyBB thread ID suffix)
        slug = re.sub(r'--\d+$', '', slug)
        title = urllib.parse.unquote(slug).replace('-', ' ')
        if len(title) >= 5:
            threads.append({"title": title, "url": url})

    # Also extract titles from link text and title attributes for better matching
    title_map = {}
    for m in re.finditer(
        r'href="[^"]*Thread-[^"]*"[^>]*title="([^"]*)"[^>]*>([^<]*)',
        html
    ):
        attr_title, link_text = m.group(1).strip(), m.group(2).strip()
        for t in threads:
            slug_part = t["url"].split("/Thread-")[-1].split("?")[0][:40]
            if slug_part and slug_part in m.group(0):
                better_title = attr_title or link_text
                if better_title and len(better_title) > len(t["title"]):
                    t["title"] = better_title
                break

    log(f"  Cracked.sh homepage: {len(threads)} unique threads")

    for t in threads:
        title_lower = t["title"].lower()
        is_match, cat, severity, relevance, keywords = _match_forum_thread(
            title_lower
        )

        if is_match:
            dl_tag = " [DL]" if any(x in title_lower for x in
                ["download", "mega", "gofile", "mediafire",
                 ".rar", ".zip", ".sql"]) else ""
            finding_title = f"CR{dl_tag}: {t['title'][:150]}"
            kw_str = ", ".join(keywords[:5])
            summary = f"Source: cracked.sh homepage\nKeywords: {kw_str}"

            if add_finding(db, "cracked", cat,
                          finding_title, summary,
                          url=t["url"], severity=severity,
                          relevance=relevance):
                new_findings += 1
                log(f"  ⚠ NEW [{severity}] {cat}: {t['title'][:80]}")

    # Also try scraping individual sections that may be guest-visible
    creds = _load_forum_creds()
    cracked_creds = creds.get("cracked", {})
    if cracked_creds:
        opener = _mybb_login(base, cracked_creds["username"], cracked_creds["password"])
        if opener:
            for section in CRACKED_SECTIONS[:3]:  # Only try first few
                section_threads = _mybb_scrape_threads(opener, base, section)
                if section_threads:
                    log(f"  {section}: {len(section_threads)} threads (authenticated)")
                    for t in section_threads:
                        title_lower = t["title"].lower()
                        is_match, cat, severity, relevance, keywords = _match_forum_thread(
                            title_lower
                        )
                        if is_match:
                            finding_title = f"CR: {t['title'][:150]}"
                            kw_str = ", ".join(keywords[:5])
                            summary = f"Section: {section}\nKeywords: {kw_str}"
                            if add_finding(db, "cracked", cat,
                                          finding_title, summary,
                                          url=t["url"], severity=severity,
                                          relevance=relevance):
                                new_findings += 1
                                log(f"  ⚠ NEW [{severity}] {cat}: {t['title'][:80]}")
                    time.sleep(2)

    log(f"  cracked: {new_findings} new findings")
    return new_findings


# ══════════════════════════════════════════════════════════════════════════
#  Source 11: Ahmia.fi — Dark Web Search Engine (clearnet Tor index)
# ══════════════════════════════════════════════════════════════════════════

def scan_ahmia_darkweb(db):
    """Search Ahmia.fi for dark web mentions of watched targets.

    Ahmia.fi is a clearnet search engine that indexes Tor hidden services.
    Searching here surfaces .onion site content without direct Tor access.
    """
    log("🧅 Scanning Ahmia.fi dark web index...")
    new_findings = 0

    queries = [
        "harman international leak",
        "harman firmware dump",
        "samsung source code leak",
        "samsung firmware dump",
        "poland database leak",
        "polska wyciek danych",
        "polish credentials dump",
        "allegro.pl database",
        "automotive firmware leak",
        "embedded source code leak",
    ]

    for query in queries:
        encoded = urllib.parse.quote(query)
        url = f"https://ahmia.fi/search/?q={encoded}"
        html = fetch_url(url, timeout=30)
        if not html:
            log(f"  ahmia [{query[:30]}]: fetch failed")
            time.sleep(5)
            continue

        # Parse search results
        results = re.findall(
            r'<li[^>]*class="[^"]*result[^"]*"[^>]*>(.*?)</li>',
            html, re.DOTALL
        )
        if not results:
            results = re.findall(
                r'<div[^>]*class="[^"]*result[^"]*"[^>]*>(.*?)</div>\s*(?:</div>)?',
                html, re.DOTALL
            )

        log(f"  ahmia [{query[:30]}]: {len(results)} results")

        for result_html in results[:8]:
            link_m = re.search(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                               result_html, re.DOTALL)
            if not link_m:
                continue
            result_url = link_m.group(1)
            result_title = strip_html(link_m.group(2))

            desc_m = re.search(r'<p[^>]*>(.*?)</p>', result_html, re.DOTALL)
            desc = strip_html(desc_m.group(1)) if desc_m else ""
            if not result_title and not desc:
                continue

            combined = f"{result_title} {desc}".lower()
            is_direct = any(kw.lower() in combined for kw in WATCH_KEYWORDS)
            is_supply = any(kw.lower() in combined for kw in SUPPLY_CHAIN)
            is_poland = any(kw in combined for kw in
                           ["poland", "polish", "polska", ".pl ",
                            "allegro", "morele", "pesel", "wyciek"])
            is_leak = any(x in combined for x in
                         ["leak", "breach", "dump", "database", "credential",
                          "source code", "firmware", "password", "combolist"])

            if not (is_direct or (is_supply and is_leak) or
                    (is_poland and is_leak)):
                continue

            if is_direct:
                severity, relevance = "critical", 9
            elif is_poland and is_leak:
                severity, relevance = "high", 7
            elif is_supply:
                severity, relevance = "high", 6
            else:
                severity, relevance = "medium", 4

            is_onion = ".onion" in result_url
            onion_tag = " [.onion]" if is_onion else ""

            finding_title = f"Ahmia{onion_tag}: {result_title[:120]}"
            summary = (f"Query: {query}\nURL: {result_url[:200]}\n"
                      f"{desc[:300]}")

            if add_finding(db, "ahmia_darkweb", "darkweb_mention",
                          finding_title, summary,
                          url=result_url[:500],
                          severity=severity, relevance=relevance):
                new_findings += 1
                log(f"  ⚠ NEW: {finding_title[:80]}")

        time.sleep(5)  # rate limit

    log(f"  ahmia_darkweb: {new_findings} new findings")
    return new_findings


# ══════════════════════════════════════════════════════════════════════════
#  LLM Analysis — Triage & Summarize findings
# ══════════════════════════════════════════════════════════════════════════

def llm_analyze_findings(db, new_count):
    """Use LLM to analyze and triage new findings."""
    if new_count == 0:
        log("  No new findings to analyze")
        return ""

    # Get recent findings (last 24h)
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
    recent = [f for f in db["findings"] if f.get("first_seen", "") > cutoff]

    if not recent:
        return ""

    # Sort by relevance
    recent.sort(key=lambda x: x.get("relevance", 0), reverse=True)

    # Prepare findings summary for LLM (sanitized — no raw credentials)
    findings_text = ""
    for i, f in enumerate(recent[:30]):
        findings_text += (
            f"{i+1}. [{f['severity'].upper()}] {f['source']} — {f['title']}\n"
            f"   Category: {f['category']}, Relevance: {f['relevance']}/10\n"
            f"   {f['summary'][:200]}\n\n"
        )

    system_prompt = """You are a cyber threat intelligence analyst performing white-hat security monitoring.
Analyze the following findings from automated OSINT sources and provide:
1. A brief executive summary (2-3 sentences)
2. CRITICAL items that need immediate attention (if any)
3. Polish database leaks — any credential dumps, PII databases, or stealer logs involving .pl domains
4. Underground forum activity — pay special attention to findings from LeakForum.io and Cracked.sh,
   these are underground hacking forums where early indicators of data breaches, credential dumps,
   Polish database leaks, and source code/firmware leaks surface before they appear elsewhere
5. Source code leaks — any leaked proprietary source code, firmware dumps, or internal repos
6. Notable trends or patterns
7. Recommended actions

Focus on findings related to HARMAN International, Samsung, their supply chain,
and Polish entities (allegro, morele, government, banks). Flag any downloadable
databases, credential dumps, or source code leaks with high priority.
For forum-sourced findings, assess whether the thread indicates a NEW leak vs rehashed old data.
Be concise. Output plain text, no markdown."""

    user_prompt = f"""Today's OSINT scan produced {new_count} new findings ({len(recent)} from last 24h).

FINDINGS:
{findings_text}

CONTEXT: Monitoring for HARMAN International (Samsung subsidiary), automotive embedded systems,
and general cybersecurity threats relevant to embedded/automotive industry in Poland/EU.
Also tracking Polish credential databases, infostealer exposure on .pl domains,
and source code leaks from any interesting targets.
Sources include LeakForum.io and Cracked.sh underground forums — findings from these
are particularly valuable as early warning indicators for fresh database drops and code leaks.
Assess each forum finding for freshness and credibility."""

    log("🤖 Running LLM analysis...")
    analysis = call_ollama(system_prompt, user_prompt, temperature=0.3, max_tokens=1500)
    return analysis or ""


# ══════════════════════════════════════════════════════════════════════════
#  Main Orchestration
# ══════════════════════════════════════════════════════════════════════════

def run_scrape_only():
    """Run all intelligence scanners. Save raw findings (no LLM). Update DB with findings."""
    log("=" * 60)
    log("LEAK MONITOR — Scrape-only (no LLM)")
    log(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    log("=" * 60)

    LEAKS_DIR.mkdir(parents=True, exist_ok=True)
    db = load_db()
    total_new = 0
    t0 = time.time()
    dt = datetime.now()

    scanners = [
        ("ransomware.live", scan_ransomware_live),
        ("ransomlook.io", scan_ransomlook),
        ("github", scan_github),
        ("cisa_kev", scan_cisa_kev),
        ("telegram", scan_telegram_public),
        ("feodo_c2", scan_feodo_c2),
        ("hibp", scan_hibp_breaches),
        ("hudson_rock", scan_hudson_rock),
        ("leakforum", scan_leakforum),
        ("cracked", scan_cracked),
        ("ahmia_darkweb", scan_ahmia_darkweb),
    ]

    scanner_errors = []
    for name, scanner in scanners:
        try:
            n = scanner(db)
            total_new += n
        except Exception as e:
            log(f"  ✗ {name} scanner error: {e}")
            scanner_errors.append(f"{name}: {e}")

    # Record run in DB
    db["runs"].append({
        "timestamp": datetime.now().isoformat(),
        "new_findings": total_new,
        "total_findings": len(db["findings"]),
        "sources_ok": len(scanners) - len(scanner_errors),
    })
    db["stats"]["last_run"] = datetime.now().isoformat()
    db["stats"]["total_findings"] = len(db["findings"])
    save_db(db)

    # Save raw intermediate data
    scrape_duration = int(time.time() - t0)
    raw_data = {
        "scrape_timestamp": dt.isoformat(timespec="seconds"),
        "scrape_duration_seconds": scrape_duration,
        "scrape_version": 1,
        "data": {
            "total_new": total_new,
            "total_findings": len(db["findings"]),
            "sources_run": len(scanners),
            "sources_ok": len(scanners) - len(scanner_errors),
        },
        "scrape_errors": scanner_errors,
    }
    tmp = RAW_LEAK_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(raw_data, f, indent=2, ensure_ascii=False)
    tmp.rename(RAW_LEAK_FILE)

    log(f"Scrape done: {total_new} new findings ({scrape_duration}s)")
    save_log()
    return total_new


def run_analyze_only():
    """Load raw scan metadata, run LLM analysis on DB findings, send Signal."""
    log("=" * 60)
    log("LEAK MONITOR — Analyze-only (LLM + Signal)")
    log(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    log("=" * 60)

    dt = datetime.now()
    LEAKS_DIR.mkdir(parents=True, exist_ok=True)

    if not RAW_LEAK_FILE.exists():
        log(f"ERROR: Raw data file not found: {RAW_LEAK_FILE}")
        log("Run with --scrape-only first.")
        sys.exit(1)

    try:
        with open(RAW_LEAK_FILE) as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log(f"ERROR: Failed to read raw data: {e}")
        sys.exit(1)

    scrape_ts = raw.get("scrape_timestamp", "")
    total_new = raw.get("data", {}).get("total_new", 0)

    # Check staleness
    if scrape_ts:
        try:
            scrape_dt = datetime.fromisoformat(scrape_ts)
            age_hours = (dt - scrape_dt).total_seconds() / 3600
            if age_hours > 48:
                log(f"WARNING: Raw data is {age_hours:.0f}h old (scraped {scrape_ts})")
        except ValueError:
            pass

    log(f"Loaded raw data: {total_new} new findings (scraped {scrape_ts})")

    db = load_db()

    # LLM Analysis
    analysis = ""
    if total_new > 0:
        analysis = llm_analyze_findings(db, total_new)
        if analysis:
            db["stats"]["last_analysis"] = analysis
            db["stats"]["last_analysis_date"] = datetime.now().isoformat()
            save_db(db)

    # Signal alert for critical/high findings from the scrape
    critical = []
    if total_new > 0:
        # Get findings from the scrape window (check scrape_timestamp)
        run_cutoff = scrape_ts if scrape_ts else (datetime.now() - timedelta(minutes=30)).isoformat()
        critical = [f for f in db["findings"]
                    if f.get("first_seen", "") >= run_cutoff
                    and f.get("severity") in ("critical", "high")]

        if critical:
            alert_lines = [f"🚨 LEAK MONITOR — {len(critical)} critical/high findings:\n"]
            for f in critical[:5]:
                alert_lines.append(f"[{f['severity'].upper()}] {f['title'][:100]}")
            if analysis:
                alert_lines.append(f"\n📊 Analysis:\n{analysis[:500]}")
            signal_send("\n".join(alert_lines))

    # Summary
    log(f"\n{'='*60}")
    log(f"ANALYZE COMPLETE: {total_new} new findings, {len(db['findings'])} total")
    if critical:
        log(f"⚠ {len(critical)} CRITICAL/HIGH findings — Signal alert sent")
    if analysis:
        log(f"\nLLM Analysis:\n{analysis[:800]}")
    log("=" * 60)

    save_log()


def run_full_scan():
    """Run all intelligence sources and produce analysis."""
    log("=" * 60)
    log("LEAK MONITOR — Cyber Threat Intelligence Scan")
    log(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    log("=" * 60)

    LEAKS_DIR.mkdir(parents=True, exist_ok=True)
    db = load_db()
    total_new = 0

    # ── Run all sources ──
    scanners = [
        ("ransomware.live", scan_ransomware_live),
        ("ransomlook.io", scan_ransomlook),
        ("github", scan_github),
        ("cisa_kev", scan_cisa_kev),
        ("telegram", scan_telegram_public),
        ("feodo_c2", scan_feodo_c2),
        ("hibp", scan_hibp_breaches),
        ("hudson_rock", scan_hudson_rock),
        ("leakforum", scan_leakforum),
        ("cracked", scan_cracked),
        ("ahmia_darkweb", scan_ahmia_darkweb),
    ]

    for name, scanner in scanners:
        try:
            n = scanner(db)
            total_new += n
        except Exception as e:
            log(f"  ✗ {name} scanner error: {e}")

    # ── LLM Analysis ──
    analysis = ""
    if total_new > 0:
        analysis = llm_analyze_findings(db, total_new)
        if analysis:
            db["stats"]["last_analysis"] = analysis
            db["stats"]["last_analysis_date"] = datetime.now().isoformat()

    # ── Record run ──
    db["runs"].append({
        "timestamp": datetime.now().isoformat(),
        "new_findings": total_new,
        "total_findings": len(db["findings"]),
        "sources_ok": len(scanners),
    })
    db["stats"]["last_run"] = datetime.now().isoformat()
    db["stats"]["total_findings"] = len(db["findings"])

    save_db(db)

    # ── Signal alert for critical/high findings from THIS run ──
    critical = []
    if total_new > 0:
        # Get findings added during this scan (check last 10 minutes)
        run_cutoff = (datetime.now() - timedelta(minutes=10)).isoformat()
        critical = [f for f in db["findings"]
                    if f.get("first_seen", "") > run_cutoff
                    and f.get("severity") in ("critical", "high")]

        if critical:
            alert_lines = [f"🚨 LEAK MONITOR — {len(critical)} critical/high findings:\n"]
            for f in critical[:5]:
                alert_lines.append(f"[{f['severity'].upper()}] {f['title'][:100]}")
            if analysis:
                alert_lines.append(f"\n📊 Analysis:\n{analysis[:500]}")
            signal_send("\n".join(alert_lines))

    # ── Summary ──
    log(f"\n{'='*60}")
    log(f"SCAN COMPLETE: {total_new} new findings, {len(db['findings'])} total")
    if critical:
        log(f"⚠ {len(critical)} CRITICAL/HIGH findings — Signal alert sent")
    if analysis:
        log(f"\nLLM Analysis:\n{analysis[:800]}")
    log("=" * 60)

    save_log()
    return total_new


def print_status():
    """Print current database status."""
    db = load_db()
    findings = db.get("findings", [])
    runs = db.get("runs", [])
    stats = db.get("stats", {})

    print(f"\n  LEAK MONITOR — Status")
    print(f"  {'─'*44}")
    print(f"  Total findings:  {len(findings)}")
    print(f"  Last run:        {stats.get('last_run', 'never')}")
    print(f"  Runs recorded:   {len(runs)}")

    # Severity breakdown
    sev_counts = {}
    for f in findings:
        s = f.get("severity", "info")
        sev_counts[s] = sev_counts.get(s, 0) + 1
    if sev_counts:
        print(f"  Severity:        ", end="")
        parts = []
        for s in ["critical", "high", "medium", "low", "info"]:
            if s in sev_counts:
                parts.append(f"{s}:{sev_counts[s]}")
        print(", ".join(parts))

    # Source breakdown
    src_counts = {}
    for f in findings:
        s = f.get("source", "?")
        src_counts[s] = src_counts.get(s, 0) + 1
    if src_counts:
        print(f"  Sources:         ", end="")
        print(", ".join(f"{s}:{c}" for s, c in sorted(src_counts.items())))

    # Recent findings
    recent = sorted(findings, key=lambda x: x.get("first_seen", ""), reverse=True)[:10]
    if recent:
        print(f"\n  Recent findings:")
        for f in recent:
            ts = f.get("first_seen", "")[:16]
            print(f"  [{f['severity'][:4].upper():4s}] {ts} {f['source']:15s} {f['title'][:60]}")

    # Last analysis
    if stats.get("last_analysis"):
        print(f"\n  Last analysis ({stats.get('last_analysis_date', '')[:16]}):")
        for line in stats["last_analysis"].split("\n")[:8]:
            print(f"  {line}")
    print()


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Cyber threat intelligence & leak monitor")
    parser.add_argument('command', nargs='?', choices=['scan', 'status'],
                        help='scan = full CTI scan, status = show DB status')
    parser.add_argument('--scrape-only', action='store_true',
                        help='Only run scanners, save raw findings (no LLM)')
    parser.add_argument('--analyze-only', action='store_true',
                        help='Only run LLM analysis on previously scanned data')
    args = parser.parse_args()

    if args.scrape_only:
        run_scrape_only()
    elif args.analyze_only:
        run_analyze_only()
    elif args.command == 'scan':
        run_full_scan()
    elif args.command == 'status':
        print_status()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
