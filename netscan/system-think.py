#!/usr/bin/env python3
"""system-think.py — System intelligence: GPU analysis, network security, health watchdog.

Three think modes that run after the life-advisor in nightly batch:

  --gpu        Analyze GPU telemetry: thermals, throttle patterns, power, cost trends
  --netsec     Analyze network scan + vulnerabilities: posture, anomalies, recommendations
  --health     System health watchdog: check staleness, job failures, anomalies → Signal alert

Signal alerts are sent for anomalies detected in --health mode:
  - Device count anomaly (too many or too few)
  - Stale dashboard / data files not updated
  - Job execution failures
  - Critical service outages
  - Any finding the LLM flags as requiring immediate attention

Output:
  /opt/netscan/data/think/system-gpu-YYYYMMDD.json
  /opt/netscan/data/think/system-netsec-YYYYMMDD.json
  /opt/netscan/data/think/system-health-YYYYMMDD.json
"""

import argparse
import csv
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from llm_sanitize import sanitize_llm_output

# ── Config ─────────────────────────────────────────────────────────────────
OLLAMA_URL = "http://localhost:11434"
OLLAMA_CHAT = f"{OLLAMA_URL}/api/chat"
OLLAMA_MODEL = "qwen3:14b"

DATA_DIR = Path("/opt/netscan/data")
THINK_DIR = DATA_DIR / "think"
WEB_DIR = Path("/opt/netscan/web")

# Signal JSON-RPC
SIGNAL_RPC = "http://127.0.0.1:8080/api/v1/rpc"
BOT_NUMBER = "+<BOT_PHONE>"
RECIPIENT = "+<OWNER_PHONE>"

DASHBOARD_URL = "http://192.168.3.151:8888/"

TODAY = datetime.now().strftime("%Y%m%d")
TODAY_ISO = datetime.now().strftime("%Y-%m-%d")
NOW = datetime.now()

# Thresholds
EXPECTED_HOST_COUNT_MIN = 30  # below = suspicious
EXPECTED_HOST_COUNT_MAX = 80  # above = suspicious
STALE_HOURS_SCAN = 36         # scan data older than this = stale
STALE_HOURS_DASHBOARD = 36    # dashboard files not updated
GPU_TEMP_WARN = 95            # °C — sustained high temp
GPU_TEMP_CRIT = 100           # °C — critical
GPU_THROTTLE_WARN_PCT = 20    # >20% time throttled = concerning

# GPU throttle bit names
THROTTLE_BITS = {
    0: "SPL", 1: "FPPT", 2: "SPPT", 3: "SPPT_APU",
    4: "THM_CORE", 5: "THM_GFX", 6: "THM_SOC",
    7: "TDC_VDD", 8: "TDC_SOC", 9: "TDC_GFX",
    10: "EDC_CPU", 11: "EDC_GFX", 12: "PROCHOT",
}


# ── Helpers ────────────────────────────────────────────────────────────────

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.rename(path)


def truncate(text, max_chars=1500):
    if not text or len(text) <= max_chars:
        return text or ""
    cut = text[:max_chars].rsplit("\n", 1)[0]
    return cut + "\n[... truncated]"


def shell_exec(cmd, timeout=30):
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"


def send_signal(msg):
    """Send message via signal-cli JSON-RPC daemon."""
    try:
        payload = json.dumps({
            "jsonrpc": "2.0",
            "method": "send",
            "params": {
                "account": BOT_NUMBER,
                "recipient": [RECIPIENT],
                "message": msg,
            },
            "id": f"system-think-{int(NOW.timestamp())}",
        })
        req = urllib.request.Request(
            SIGNAL_RPC,
            data=payload.encode(),
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=15)
        body = json.loads(resp.read())
        if "error" in body:
            log(f"  Signal RPC error: {body['error']}")
            return False
        log("  Signal message sent ✓")
        return True
    except Exception as ex:
        log(f"  Signal send FAILED: {ex}")
        return False


def call_ollama(system_prompt, user_prompt, temperature=0.3, max_tokens=4000, think=True):
    """Call Ollama with chain-of-thought for deep analysis."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=10) as r:
            tags = json.loads(r.read())
            models = [m["name"] for m in tags.get("models", [])]
            if not any(OLLAMA_MODEL in m for m in models):
                log(f"  Model {OLLAMA_MODEL} not found")
                return None
    except Exception as e:
        log(f"  Ollama health check failed: {e}")
        return None

    prefix = "" if think else "/nothink\n"

    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prefix + user_prompt},
        ],
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
            "num_ctx": 24576,
        },
    }).encode()

    req = urllib.request.Request(OLLAMA_CHAT, data=payload, headers={
        "Content-Type": "application/json",
    })

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            result = json.loads(resp.read())
            content = result.get("message", {}).get("content", "")
            elapsed = time.time() - t0
            tokens = result.get("eval_count", len(content.split()))
            tps = tokens / elapsed if elapsed > 0 else 0
            log(f"  LLM: {elapsed:.0f}s, {tokens} tok ({tps:.1f} t/s)")
            return sanitize_llm_output(content)
    except Exception as e:
        log(f"  Ollama call failed: {e}")
        return None


# ── Data loaders ───────────────────────────────────────────────────────────

def load_gpu_csv(date_str=None):
    """Load GPU hardware CSV for a given date. Returns list of dicts."""
    if date_str is None:
        date_str = TODAY
    path = DATA_DIR / "gpu" / f"gpu-{date_str}.csv"
    if not path.exists():
        return []
    rows = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                rows.append({
                    "time": row.get("timestamp", ""),
                    "power_w": float(row.get("power_w", 0)),
                    "temp_c": float(row.get("temp_c", 0)),
                    "freq_mhz": float(row.get("freq_mhz", 0)),
                    "vram_mb": float(row.get("vram_mb", 0)),
                    "gtt_mb": float(row.get("gtt_mb", 0)),
                    "throttle": row.get("throttle_status", "0x0"),
                })
            except (ValueError, TypeError):
                continue
    return rows


def load_gpu_load_tsv(days=3):
    """Load GPU load TSV (gpu-monitor.sh output). Returns list of dicts."""
    path = DATA_DIR / "gpu-load.tsv"
    if not path.exists():
        return []
    cutoff = NOW - timedelta(days=days)
    rows = []
    with open(path) as f:
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) < 8:
                continue
            try:
                ts = datetime.strptime(parts[0], "%Y-%m-%d %H:%M")
                if ts < cutoff:
                    continue
                rows.append({
                    "timestamp": parts[0],
                    "status": parts[1],
                    "model": parts[2],
                    "script": parts[3],
                    "vram_mb": int(parts[4]),
                    "gpu_mhz": int(parts[5]),
                    "temp_c": int(parts[6]),
                    "throttle": int(parts[7]) if parts[7].isdigit() else 0,
                })
            except (ValueError, IndexError):
                continue
    return rows


def load_latest_scan():
    """Load latest network scan JSON."""
    files = sorted(glob.glob(str(DATA_DIR / "scan-*.json")))
    return load_json(files[-1]) if files else None


def load_latest_vuln():
    """Load latest vulnerability scan JSON."""
    files = sorted(glob.glob(str(DATA_DIR / "vuln" / "vuln-*.json")))
    return load_json(files[-1]) if files else None


def load_latest_health():
    """Load latest health snapshot JSON."""
    files = sorted(glob.glob(str(DATA_DIR / "health-*.json")))
    return load_json(files[-1]) if files else None


def load_latest_watchdog():
    """Load latest watchdog report JSON."""
    files = sorted(glob.glob(str(DATA_DIR / "watchdog" / "watchdog-*.json")))
    return load_json(files[-1]) if files else None


def load_job_status():
    """Load all job states from openclaw jobs.json."""
    jobs_path = Path("/home/akandr/.openclaw/cron/jobs.json")
    data = load_json(jobs_path)
    if not data:
        return {}
    results = {}
    for j in data.get("jobs", []):
        name = j.get("name", "?")
        state = j.get("state", {})
        results[name] = {
            "enabled": j.get("enabled", True),
            "last_status": state.get("lastStatus"),
            "last_run": state.get("lastRunAtMs"),
            "last_duration_ms": state.get("lastDurationMs"),
            "consecutive_errors": state.get("consecutiveErrors", 0),
        }
    return results


# ══════════════════════════════════════════════════════════════════════════
# MODE: --gpu — GPU telemetry analysis
# ══════════════════════════════════════════════════════════════════════════

def run_gpu():
    """Analyze GPU telemetry: thermals, throttle patterns, power efficiency, cost trends."""
    t_start = time.time()

    log(f"{'='*60}")
    log(f"GPU INTELLIGENCE — {NOW.strftime('%Y-%m-%d %H:%M')}")
    log(f"{'='*60}")

    THINK_DIR.mkdir(parents=True, exist_ok=True)

    # Load multi-day GPU hardware data
    gpu_data = {}
    for days_ago in range(7):
        d = (NOW - timedelta(days=days_ago)).strftime("%Y%m%d")
        rows = load_gpu_csv(d)
        if rows:
            gpu_data[d] = rows
            log(f"  GPU CSV {d}: {len(rows)} samples")

    # Load GPU load TSV
    load_data = load_gpu_load_tsv(days=7)
    log(f"  GPU load TSV: {len(load_data)} entries (7 days)")

    # Load latest health
    health = load_latest_health()

    if not gpu_data and not load_data:
        log("No GPU data available. Aborting.")
        return

    # Compute per-day summaries
    day_summaries = []
    for date_str, rows in sorted(gpu_data.items()):
        powers = [r["power_w"] for r in rows if r["power_w"] > 0]
        temps = [r["temp_c"] for r in rows if r["temp_c"] > 0]
        freqs = [r["freq_mhz"] for r in rows if r["freq_mhz"] > 0]
        vrams = [r["vram_mb"] for r in rows if r["vram_mb"] > 0]

        # Throttle analysis
        throttle_count = 0
        thermal_throttle = 0
        power_throttle = 0
        for r in rows:
            thr = r["throttle"]
            if isinstance(thr, str):
                try:
                    thr = int(thr, 16)
                except ValueError:
                    thr = 0
            if thr > 0:
                throttle_count += 1
                if thr & 0x1070:  # THM_CORE | THM_GFX | THM_SOC | PROCHOT
                    thermal_throttle += 1
                if thr & 0x000F:  # SPL | FPPT | SPPT | SPPT_APU
                    power_throttle += 1

        total = len(rows)
        throttle_pct = (throttle_count / total * 100) if total else 0
        hours = total / 60.0

        # Energy cost estimate (1.30 PLN/kWh, 0.87 PSU eff, +9W overhead)
        avg_ppt = sum(powers) / len(powers) if powers else 0
        wall_w = (avg_ppt + 9) / 0.87
        kwh = wall_w * hours / 1000
        cost_pln = kwh * 1.30

        day_summaries.append({
            "date": date_str,
            "samples": total,
            "hours": round(hours, 1),
            "ppt_avg": round(avg_ppt, 1),
            "ppt_max": round(max(powers), 1) if powers else 0,
            "temp_avg": round(sum(temps) / len(temps), 1) if temps else 0,
            "temp_max": round(max(temps), 1) if temps else 0,
            "freq_avg": round(sum(freqs) / len(freqs), 1) if freqs else 0,
            "freq_max": round(max(freqs), 1) if freqs else 0,
            "vram_avg": round(sum(vrams) / len(vrams), 1) if vrams else 0,
            "vram_max": round(max(vrams), 1) if vrams else 0,
            "throttle_pct": round(throttle_pct, 1),
            "thermal_throttle_pct": round(thermal_throttle / total * 100, 1) if total else 0,
            "power_throttle_pct": round(power_throttle / total * 100, 1) if total else 0,
            "kwh": round(kwh, 2),
            "cost_pln": round(cost_pln, 2),
        })

    # GPU utilization analysis from load TSV
    if load_data:
        generating = sum(1 for r in load_data if r["status"] == "generating")
        loaded = sum(1 for r in load_data if r["status"] == "loaded")
        idle = sum(1 for r in load_data if r["status"] == "idle")
        total_load = len(load_data)

        # Script distribution
        script_counter = {}
        for r in load_data:
            s = r.get("script", "unknown")
            script_counter[s] = script_counter.get(s, 0) + 1
        top_scripts = sorted(script_counter.items(), key=lambda x: -x[1])[:10]

        utilization = {
            "total_samples": total_load,
            "generating_pct": round(generating / total_load * 100, 1) if total_load else 0,
            "loaded_pct": round(loaded / total_load * 100, 1) if total_load else 0,
            "idle_pct": round(idle / total_load * 100, 1) if total_load else 0,
            "top_scripts": top_scripts,
        }
    else:
        utilization = {}

    # Build prompt
    system = """You are ClawdBot, a GPU infrastructure analyst for a dedicated AI inference server.
The hardware is: AMD Cyan Skillfish (RDNA1, 16GB UMA) in a BC-250 mini PC (Zen 2 APU).
Running Ollama with qwen3:14b (24K context). Fedora 43 Linux.

Analyze the GPU telemetry data and provide actionable insights about:
- Thermal management and throttling patterns
- Power efficiency and cost optimization
- Utilization patterns and scheduling efficiency
- Hardware health trends (degradation signals)
- Specific recommendations for improvement

Write ONLY in English. Be technical and specific."""

    prompt_parts = [f"=== GPU TELEMETRY ANALYSIS — {TODAY_ISO} ===\n"]

    prompt_parts.append("## DAILY SUMMARIES (last 7 days)\n")
    for ds in day_summaries:
        prompt_parts.append(
            f"  {ds['date']}: {ds['hours']}h active, PPT avg {ds['ppt_avg']}W / max {ds['ppt_max']}W, "
            f"temp avg {ds['temp_avg']}°C / max {ds['temp_max']}°C, "
            f"freq avg {ds['freq_avg']} / max {ds['freq_max']} MHz, "
            f"VRAM avg {ds['vram_avg']} / max {ds['vram_max']} MB, "
            f"throttle {ds['throttle_pct']}% (thermal {ds['thermal_throttle_pct']}%, power {ds['power_throttle_pct']}%), "
            f"energy {ds['kwh']} kWh = {ds['cost_pln']} PLN"
        )

    if utilization:
        prompt_parts.append(f"\n## GPU UTILIZATION (7-day rolling, {utilization['total_samples']} samples)")
        prompt_parts.append(
            f"  Generating: {utilization['generating_pct']}% | Loaded/idle-warm: {utilization['loaded_pct']}% | "
            f"Fully idle: {utilization['idle_pct']}%"
        )
        prompt_parts.append("  Script distribution:")
        for script, count in utilization.get("top_scripts", [])[:8]:
            pct = count / utilization["total_samples"] * 100
            prompt_parts.append(f"    {script}: {count} min ({pct:.1f}%)")

    if health:
        prompt_parts.append(f"\n## SYSTEM HEALTH SNAPSHOT")
        prompt_parts.append(f"  Uptime: {health.get('uptime', '?')}")
        prompt_parts.append(f"  Load avg: {health.get('load_avg', '?')}")
        prompt_parts.append(f"  RAM: {health.get('mem_available_mb', 0)} MB free / {health.get('mem_total_mb', 0)} MB total")
        prompt_parts.append(f"  Swap used: {health.get('swap_used_mb', 0)} MB")
        prompt_parts.append(f"  Disk: {health.get('disk_used', '?')} / {health.get('disk_total', '?')} ({health.get('disk_pct', '?')})")
        prompt_parts.append(f"  GPU VRAM: {health.get('gpu_vram_used_mb', 0)} MB, GTT: {health.get('gpu_gtt_used_mb', 0)} MB")
        prompt_parts.append(f"  CPU temp: {health.get('cpu_temp', '?')}°C, GPU temp: {health.get('gpu_temp', '?')}°C")
        prompt_parts.append(f"  NVMe temp: {health.get('nvme_temp', '?')}°C, wear: {health.get('nvme_wear_pct', '?')}")
        prompt_parts.append(f"  OOM kills 24h: {health.get('oom_kills_24h', 0)}, failed units: {health.get('failed_units', '?')}")

    prompt_parts.append(f"""
─────────────────────────────────────────────────────
ANALYZE and provide:

1. THERMAL HEALTH ASSESSMENT
   Trend analysis: are temps increasing over last 7 days? Throttling getting worse?
   Is the cooling solution adequate for 24/7 LLM inference?
   Risk of thermal degradation to the GPU/APU silicon?

2. POWER & COST EFFICIENCY
   Weekly energy cost. Monthly extrapolation.
   Power-performance ratio: are we getting good tokens/watt?
   Suggestions: scheduling batches to allow cooldown periods?

3. THROTTLE DEEP-DIVE
   Which throttle reasons are dominant? Thermal vs power limit?
   Correlation between throttle events and specific scripts?
   Impact on inference speed (token/s degradation during throttling)?

4. UTILIZATION OPTIMIZATION
   Is GPU being used efficiently? Too much idle-warm time?
   Which scripts are heaviest users? Are there scheduling conflicts?
   Could the pipeline be reordered for better thermal management?

5. HARDWARE LONGEVITY RISKS
   Signs of wear (NVMe, thermal cycling stress)?
   Is 24/7 operation sustainable at these thermals?
   When should maintenance be planned?

6. SPECIFIC RECOMMENDATIONS (top 5, actionable)
   What to do this week for GPU health and efficiency.

Target: 600-800 words. Chain-of-thought reasoning. English only.""")

    user = "\n".join(prompt_parts)
    log(f"\n── Running GPU analysis (chain-of-thought) ──")
    log(f"  Prompt: {len(user)} chars")
    analysis = call_ollama(system, user, temperature=0.3, max_tokens=4000, think=True)

    if not analysis:
        log("GPU LLM analysis failed")
        return

    elapsed = time.time() - t_start

    output = {
        "type": "system-think-gpu",
        "generated": NOW.isoformat(),
        "date": TODAY,
        "analysis": analysis,
        "day_summaries": day_summaries,
        "utilization": utilization,
        "meta": {
            "duration_s": round(elapsed, 1),
            "prompt_chars": len(user),
            "analysis_chars": len(analysis),
            "days_analyzed": len(day_summaries),
        },
    }

    out_file = THINK_DIR / f"system-gpu-{TODAY}.json"
    save_json(out_file, output)
    log(f"\nSaved: {out_file.name} ({out_file.stat().st_size:,} bytes)")

    latest = THINK_DIR / "latest-system-gpu.json"
    latest.unlink(missing_ok=True)
    latest.symlink_to(out_file.name)

    # Save think note
    note = {
        "type": "gpu-analysis",
        "title": f"GPU Telemetry Analysis — {len(day_summaries)} days",
        "generated": output["generated"],
        "content": analysis,
        "summary": f"GPU: {day_summaries[-1]['temp_avg'] if day_summaries else 0}°C avg, "
                   f"{day_summaries[-1]['throttle_pct'] if day_summaries else 0}% throttle, "
                   f"${sum(d['cost_pln'] for d in day_summaries):.1f} PLN/week"
                   if day_summaries else "No GPU data",
    }
    save_json(THINK_DIR / f"note-system-gpu-{TODAY}.json", note)

    log(f"Done in {elapsed:.0f}s")
    return output


# ══════════════════════════════════════════════════════════════════════════
# MODE: --netsec — Network security analysis
# ══════════════════════════════════════════════════════════════════════════

def run_netsec():
    """Analyze network scan + vulnerabilities: posture, anomalies, risk assessment."""
    t_start = time.time()

    log(f"{'='*60}")
    log(f"NETWORK SECURITY INTELLIGENCE — {NOW.strftime('%Y-%m-%d %H:%M')}")
    log(f"{'='*60}")

    THINK_DIR.mkdir(parents=True, exist_ok=True)

    # Load scan data (current + previous for delta)
    scan_files = sorted(glob.glob(str(DATA_DIR / "scan-*.json")))
    current_scan = load_json(scan_files[-1]) if scan_files else None
    prev_scan = load_json(scan_files[-2]) if len(scan_files) >= 2 else None

    # Load vuln data
    vuln = load_latest_vuln()

    # Load watchdog data (recent reports)
    watchdog_files = sorted(glob.glob(str(DATA_DIR / "watchdog" / "watchdog-*.json")))
    watchdog_recent = []
    for fp in watchdog_files[-5:]:
        w = load_json(fp)
        if w:
            watchdog_recent.append(w)

    # Load enum data for service details
    enum_files = sorted(glob.glob(str(DATA_DIR / "enum" / "enum-*.json")))
    enum_data = load_json(enum_files[-1]) if enum_files else None

    if not current_scan:
        log("No scan data available. Aborting.")
        return

    hosts = current_scan.get("hosts", {})
    host_count = len(hosts)
    log(f"  Scan: {host_count} hosts, date: {current_scan.get('date', '?')}")

    if vuln:
        log(f"  Vuln: {vuln.get('stats', {}).get('total_findings', 0)} findings, "
            f"crit={vuln.get('stats', {}).get('critical', 0)}, "
            f"high={vuln.get('stats', {}).get('high', 0)}")

    # Compute scan deltas
    delta = {}
    if prev_scan:
        prev_hosts = set(prev_scan.get("hosts", {}).keys())
        curr_hosts = set(hosts.keys())
        delta = {
            "new_hosts": list(curr_hosts - prev_hosts),
            "gone_hosts": list(prev_hosts - curr_hosts),
            "prev_count": len(prev_hosts),
            "curr_count": len(curr_hosts),
        }
        log(f"  Delta: +{len(delta['new_hosts'])} new, -{len(delta['gone_hosts'])} gone")

    # Device type breakdown
    device_types = {}
    for ip, h in hosts.items():
        dt = h.get("device_type", "unknown")
        device_types[dt] = device_types.get(dt, 0) + 1

    # Security score distribution
    scores = [h.get("security_score", 100) for h in hosts.values()]
    score_avg = sum(scores) / len(scores) if scores else 0
    low_score_hosts = [
        (ip, h.get("hostname", "?"), h.get("security_score", 100))
        for ip, h in hosts.items()
        if h.get("security_score", 100) < 70
    ]

    # Collect port landscape
    port_map = {}
    for ip, h in hosts.items():
        for p in h.get("ports", []):
            port_num = p.get("port", 0)
            port_map.setdefault(port_num, []).append(ip)

    # Collect watchdog alerts
    recent_alerts = []
    for w in watchdog_recent:
        for a in w.get("alerts", []):
            recent_alerts.append({
                "date": w.get("date", "?"),
                "tier": a.get("tier", "?"),
                "category": a.get("category", "?"),
                "title": a.get("title", "?"),
            })

    # Build prompt
    system = """You are ClawdBot, a network security analyst for a home/lab network.
The network is a /22 subnet (192.168.0.0-192.168.3.255) with ~60 devices:
PCs, servers, IoT devices, cameras, phones, network equipment.
Running nightly nmap scans, vulnerability assessments, and security watchdog checks.

Analyze the network posture and provide defensive recommendations.
Think like a penetration tester reviewing the target before an engagement.
Write ONLY in English. Be technical, specific, and actionable."""

    prompt_parts = [f"=== NETWORK SECURITY ANALYSIS — {TODAY_ISO} ===\n"]

    # Scan overview
    prompt_parts.append(f"## NETWORK INVENTORY: {host_count} hosts")
    prompt_parts.append("Device types:")
    for dt, count in sorted(device_types.items(), key=lambda x: -x[1]):
        prompt_parts.append(f"  {dt}: {count}")

    if delta:
        prompt_parts.append(f"\n## HOST CHANGES (vs previous scan)")
        prompt_parts.append(f"  Previous: {delta['prev_count']} → Current: {delta['curr_count']}")
        if delta["new_hosts"]:
            for ip in delta["new_hosts"][:10]:
                h = hosts.get(ip, {})
                prompt_parts.append(f"  NEW: {ip} ({h.get('hostname','?')}, {h.get('device_type','?')})")
        if delta["gone_hosts"]:
            for ip in delta["gone_hosts"][:10]:
                prompt_parts.append(f"  GONE: {ip}")

    # Security scores
    prompt_parts.append(f"\n## SECURITY SCORES: avg={score_avg:.0f}/100")
    if low_score_hosts:
        prompt_parts.append(f"  LOW SCORE HOSTS ({len(low_score_hosts)}):")
        for ip, name, score in sorted(low_score_hosts, key=lambda x: x[2])[:10]:
            prompt_parts.append(f"    {ip} ({name}): {score}/100")

    # Port landscape
    common_ports = sorted(port_map.items(), key=lambda x: -len(x[1]))[:20]
    prompt_parts.append(f"\n## PORT LANDSCAPE ({sum(len(v) for v in port_map.values())} total open ports)")
    for port, ips in common_ports:
        prompt_parts.append(f"  Port {port}: {len(ips)} hosts")

    # Vulnerabilities
    if vuln:
        stats = vuln.get("stats", {})
        prompt_parts.append(f"\n## VULNERABILITY ASSESSMENT")
        prompt_parts.append(
            f"  Total: {stats.get('total_findings', 0)} findings across {stats.get('hosts_with_findings', 0)} hosts")
        prompt_parts.append(
            f"  Severity: crit={stats.get('critical', 0)}, high={stats.get('high', 0)}, "
            f"med={stats.get('medium', 0)}, low={stats.get('low', 0)}")
        prompt_parts.append(f"  Avg risk score: {stats.get('avg_risk_score', 0)}/100")
        prompt_parts.append(f"  New: {stats.get('new_findings', 0)}, Resolved: {stats.get('resolved_findings', 0)}")

        # Top vulnerable hosts
        vuln_hosts = vuln.get("hosts", {})
        top_vuln = sorted(vuln_hosts.items(), key=lambda x: x[1].get("risk_score", 100))[:8]
        if top_vuln:
            prompt_parts.append("\n  TOP VULNERABLE HOSTS:")
            for ip, vh in top_vuln:
                prompt_parts.append(
                    f"    {ip} ({vh.get('name','?')}): score {vh.get('risk_score',100)}, "
                    f"{vh.get('finding_count',0)} findings "
                    f"(crit={vh.get('severity_counts',{}).get('critical',0)}, "
                    f"high={vh.get('severity_counts',{}).get('high',0)})")

                # Show top findings
                for finding in vh.get("findings", [])[:3]:
                    if finding.get("severity") in ("critical", "high"):
                        prompt_parts.append(
                            f"      [{finding['severity'].upper()}] {finding.get('type','?')}: "
                            f"{finding.get('detail','')[:100]}")

    # Watchdog alerts
    if recent_alerts:
        prompt_parts.append(f"\n## RECENT WATCHDOG ALERTS ({len(recent_alerts)} in last {len(watchdog_recent)} reports)")
        for a in recent_alerts[:15]:
            prompt_parts.append(f"  [{a['tier'].upper()}] {a['date']}: {a['category']} — {a['title']}")

    prompt_parts.append(f"""
─────────────────────────────────────────────────────
ANALYZE and provide:

1. THREAT POSTURE ASSESSMENT
   Overall network security grade (A-F).
   Most critical vulnerabilities requiring immediate attention.
   Attack surface analysis: what would a real attacker target first?

2. DEVICE ANOMALY ANALYSIS
   Any unexpected devices? Suspicious new hosts?
   Devices that shouldn't be exposing certain ports?
   IoT devices that need isolation/segmentation?

3. VULNERABILITY PRIORITIZATION
   Top 5 vulnerabilities to remediate NOW (with specific fix instructions).
   Which findings are false positives vs real risks?
   CVEs that have known active exploits in the wild?

4. NETWORK HYGIENE
   Services that should be disabled or restricted.
   TLS/SSH configuration improvements.
   Firewall rules to implement.

5. MONITORING GAPS
   What are we NOT scanning that we should be?
   Blind spots in the current monitoring setup.
   Additional checks to implement.

6. ACTION PLAN (prioritized, top 5-7 items)
   Specific remediation steps with commands/configs where applicable.
   Estimate effort for each (quick-fix, weekend project, major overhaul).

Target: 600-800 words. Chain-of-thought reasoning. English only.""")

    user = "\n".join(prompt_parts)
    log(f"\n── Running network security LLM analysis (chain-of-thought) ──")
    log(f"  Prompt: {len(user)} chars")
    analysis = call_ollama(system, user, temperature=0.3, max_tokens=4500, think=True)

    if not analysis:
        log("Netsec LLM analysis failed")
        return

    elapsed = time.time() - t_start

    output = {
        "type": "system-think-netsec",
        "generated": NOW.isoformat(),
        "date": TODAY,
        "analysis": analysis,
        "scan_summary": {
            "host_count": host_count,
            "device_types": device_types,
            "avg_security_score": round(score_avg, 1),
            "low_score_hosts": len(low_score_hosts),
            "total_open_ports": sum(len(v) for v in port_map.values()),
        },
        "vuln_summary": vuln.get("stats", {}) if vuln else None,
        "delta": delta,
        "recent_alerts": len(recent_alerts),
        "meta": {
            "duration_s": round(elapsed, 1),
            "prompt_chars": len(user),
            "analysis_chars": len(analysis),
        },
    }

    out_file = THINK_DIR / f"system-netsec-{TODAY}.json"
    save_json(out_file, output)
    log(f"\nSaved: {out_file.name} ({out_file.stat().st_size:,} bytes)")

    latest = THINK_DIR / "latest-system-netsec.json"
    latest.unlink(missing_ok=True)
    latest.symlink_to(out_file.name)

    note = {
        "type": "netsec-analysis",
        "title": f"Network Security Analysis — {host_count} hosts, {vuln.get('stats', {}).get('total_findings', 0) if vuln else 0} vulns",
        "generated": output["generated"],
        "content": analysis,
        "summary": f"Netsec: {host_count} hosts, score {score_avg:.0f}/100, "
                   f"{len(low_score_hosts)} low-score, {len(recent_alerts)} watchdog alerts",
    }
    save_json(THINK_DIR / f"note-system-netsec-{TODAY}.json", note)

    log(f"Done in {elapsed:.0f}s")
    return output


# ══════════════════════════════════════════════════════════════════════════
# MODE: --health — System health watchdog with Signal alerts
# ══════════════════════════════════════════════════════════════════════════

def run_health():
    """System health watchdog: detect anomalies, failed jobs, stale data → LLM + Signal."""
    t_start = time.time()

    log(f"{'='*60}")
    log(f"HEALTH WATCHDOG — {NOW.strftime('%Y-%m-%d %H:%M')}")
    log(f"{'='*60}")

    THINK_DIR.mkdir(parents=True, exist_ok=True)

    alerts = []   # list of {severity, category, detail}
    checks = {}   # check_name → {status, detail}

    # ── Check 1: Device count anomaly ──
    log("Check 1: Device count...")
    scan = load_latest_scan()
    if scan:
        host_count = scan.get("host_count", len(scan.get("hosts", {})))
        scan_date = scan.get("date", "?")
        checks["device_count"] = {"status": "ok", "count": host_count, "scan_date": scan_date}

        if host_count < EXPECTED_HOST_COUNT_MIN:
            a = {
                "severity": "HIGH",
                "category": "device_count_low",
                "detail": f"Only {host_count} devices found (expected ≥{EXPECTED_HOST_COUNT_MIN}). "
                          f"Scan date: {scan_date}. Possible: network outage, scanner failure, or DHCP issue.",
            }
            alerts.append(a)
            checks["device_count"]["status"] = "alert"
        elif host_count > EXPECTED_HOST_COUNT_MAX:
            a = {
                "severity": "HIGH",
                "category": "device_count_high",
                "detail": f"{host_count} devices found (expected ≤{EXPECTED_HOST_COUNT_MAX}). "
                          f"Possible: rogue devices, DHCP leak, or ARP spoof.",
            }
            alerts.append(a)
            checks["device_count"]["status"] = "alert"
        else:
            log(f"  OK: {host_count} devices")
    else:
        alerts.append({
            "severity": "CRITICAL",
            "category": "no_scan_data",
            "detail": "No scan data found at all. Nightly scan may have never run.",
        })
        checks["device_count"] = {"status": "error", "detail": "No scan data"}

    # ── Check 2: Scan data staleness ──
    log("Check 2: Data staleness...")
    stale_files = []
    freshness_checks = [
        ("scan", DATA_DIR / "scan-*.json", STALE_HOURS_SCAN),
        ("vuln", DATA_DIR / "vuln" / "vuln-*.json", 192),   # weekly scan (Sunday) — 8 day window
        ("health", DATA_DIR / "health-*.json", STALE_HOURS_SCAN),
        ("gpu_csv", DATA_DIR / "gpu" / f"gpu-{TODAY}.csv", 24),
        ("watchdog", DATA_DIR / "watchdog" / "watchdog-*.json", 48),
        ("correlate", DATA_DIR / "correlate" / "*.json", 48),
        ("career_scan", DATA_DIR / "career" / "*.json", 96),  # career-scan is heavy (~1h), may miss nightly cycles
    ]

    for name, pattern, max_age_h in freshness_checks:
        files = sorted(glob.glob(str(pattern)))
        if not files:
            stale_files.append(f"{name}: NO DATA FILES")
            continue
        mtime = datetime.fromtimestamp(os.path.getmtime(files[-1]))
        age_h = (NOW - mtime).total_seconds() / 3600
        if age_h > max_age_h:
            stale_files.append(f"{name}: last updated {age_h:.0f}h ago (max {max_age_h}h) — {os.path.basename(files[-1])}")

    if stale_files:
        checks["staleness"] = {"status": "alert", "stale": stale_files}
        alerts.append({
            "severity": "MEDIUM",
            "category": "stale_data",
            "detail": f"{len(stale_files)} data source(s) stale:\n" + "\n".join(f"  • {s}" for s in stale_files),
        })
    else:
        checks["staleness"] = {"status": "ok"}
        log("  OK: all data sources fresh")

    # ── Check 3: Dashboard freshness ──
    log("Check 3: Dashboard freshness...")
    dashboard_pages = ["index.html", "home.html", "career.html", "advisor.html", "car.html"]
    stale_pages = []
    for page in dashboard_pages:
        p = WEB_DIR / page
        if not p.exists():
            if page not in ("advisor.html",):  # advisor is new, ok to be missing
                stale_pages.append(f"{page}: MISSING")
            continue
        mtime = datetime.fromtimestamp(os.path.getmtime(p))
        age_h = (NOW - mtime).total_seconds() / 3600
        if age_h > STALE_HOURS_DASHBOARD:
            stale_pages.append(f"{page}: {age_h:.0f}h old")

    if stale_pages:
        checks["dashboard"] = {"status": "alert", "stale_pages": stale_pages}
        alerts.append({
            "severity": "MEDIUM",
            "category": "stale_dashboard",
            "detail": f"Dashboard pages not updated:\n" + "\n".join(f"  • {s}" for s in stale_pages),
        })
    else:
        checks["dashboard"] = {"status": "ok"}
        log("  OK: dashboard pages fresh")

    # ── Check 4: Job execution status ──
    log("Check 4: Job execution status...")
    job_status = load_job_status()
    failed_jobs = []
    error_jobs = []
    never_run = []

    for name, st in job_status.items():
        if not st["enabled"]:
            continue
        if st["last_status"] and st["last_status"] != "ok":
            failed_jobs.append(f"{name}: {st['last_status']}")
        if st["consecutive_errors"] and st["consecutive_errors"] > 2:
            error_jobs.append(f"{name}: {st['consecutive_errors']} consecutive errors")
        if st["last_run"] is None:
            never_run.append(name)

    if failed_jobs:
        checks["jobs_failed"] = {"status": "alert", "failed": failed_jobs}
        alerts.append({
            "severity": "MEDIUM" if len(failed_jobs) < 5 else "HIGH",
            "category": "job_failures",
            "detail": f"{len(failed_jobs)} job(s) with non-ok status:\n" + "\n".join(f"  • {j}" for j in failed_jobs[:15]),
        })
    else:
        checks["jobs_failed"] = {"status": "ok"}

    if error_jobs:
        checks["jobs_errors"] = {"status": "alert", "errors": error_jobs}
        alerts.append({
            "severity": "HIGH",
            "category": "consecutive_errors",
            "detail": f"Jobs with consecutive errors:\n" + "\n".join(f"  • {j}" for j in error_jobs[:10]),
        })
    else:
        checks["jobs_errors"] = {"status": "ok"}

    total_jobs = len([n for n, s in job_status.items() if s["enabled"]])
    log(f"  {total_jobs} enabled jobs, {len(failed_jobs)} failed, {len(error_jobs)} with repeated errors")

    # ── Check 5: Critical services ──
    log("Check 5: Critical services...")
    health = load_latest_health()
    service_issues = []
    if health:
        for svc in ["svc_ollama", "svc_nginx", "svc_signal-cli"]:
            status = health.get(svc, "")
            if status != "active":
                service_issues.append(f"{svc.replace('svc_', '')}: {status or 'NOT RUNNING'}")

        oom = health.get("oom_kills_24h", 0)
        if oom > 0:
            service_issues.append(f"OOM kills in 24h: {oom}")

        failed_units = health.get("failed_units", "0")
        if failed_units and failed_units != "0":
            service_issues.append(f"Failed systemd units: {failed_units}")

    if service_issues:
        checks["services"] = {"status": "alert", "issues": service_issues}
        alerts.append({
            "severity": "CRITICAL" if any("ollama" in s for s in service_issues) else "HIGH",
            "category": "service_down",
            "detail": "Service issues:\n" + "\n".join(f"  • {s}" for s in service_issues),
        })
    else:
        checks["services"] = {"status": "ok"}
        log("  OK: critical services running")

    # ── Check 6: Disk space ──
    log("Check 6: Disk space...")
    disk_pct = health.get("disk_pct", "0%").rstrip("%") if health else "0"
    try:
        disk_val = int(disk_pct)
    except ValueError:
        disk_val = 0
    if disk_val > 85:
        alerts.append({
            "severity": "HIGH" if disk_val > 95 else "MEDIUM",
            "category": "disk_space",
            "detail": f"Disk usage at {disk_val}% ({health.get('disk_used', '?')} / {health.get('disk_total', '?')})",
        })
        checks["disk"] = {"status": "alert", "pct": disk_val}
    else:
        checks["disk"] = {"status": "ok", "pct": disk_val}
        log(f"  OK: disk at {disk_val}%")

    # ── Check 7: GPU thermal stress ──
    log("Check 7: GPU thermal stress...")
    gpu_rows = load_gpu_csv()
    if gpu_rows:
        recent = gpu_rows[-60:]  # last hour
        temps = [r["temp_c"] for r in recent if r["temp_c"] > 0]
        if temps:
            avg_temp = sum(temps) / len(temps)
            max_temp = max(temps)
            if max_temp >= GPU_TEMP_CRIT:
                alerts.append({
                    "severity": "CRITICAL",
                    "category": "gpu_temp_critical",
                    "detail": f"GPU hit {max_temp:.0f}°C (critical threshold: {GPU_TEMP_CRIT}°C). "
                              f"Average last hour: {avg_temp:.0f}°C. Risk of hardware damage.",
                })
                checks["gpu_temp"] = {"status": "critical", "max": max_temp, "avg": avg_temp}
            elif avg_temp >= GPU_TEMP_WARN:
                alerts.append({
                    "severity": "MEDIUM",
                    "category": "gpu_temp_high",
                    "detail": f"GPU sustained {avg_temp:.0f}°C avg (max {max_temp:.0f}°C) last hour. "
                              f"Warn threshold: {GPU_TEMP_WARN}°C.",
                })
                checks["gpu_temp"] = {"status": "warn", "max": max_temp, "avg": avg_temp}
            else:
                checks["gpu_temp"] = {"status": "ok", "max": max_temp, "avg": avg_temp}
                log(f"  OK: GPU avg {avg_temp:.0f}°C, max {max_temp:.0f}°C last hour")
    else:
        checks["gpu_temp"] = {"status": "no_data"}

    # ── Summarize checks ──
    crit_count = sum(1 for a in alerts if a["severity"] == "CRITICAL")
    high_count = sum(1 for a in alerts if a["severity"] == "HIGH")
    med_count = sum(1 for a in alerts if a["severity"] == "MEDIUM")
    total_alerts = len(alerts)

    log(f"\n  Checks complete: {total_alerts} alerts (CRIT={crit_count}, HIGH={high_count}, MED={med_count})")

    # ── LLM analysis of health state ──
    log("\n── Running health LLM analysis ──")

    system_prompt = """You are ClawdBot, an autonomous system health monitor for a home lab server (BC-250).
You monitor the infrastructure, nightly batch jobs, dashboards, and network scans.
When things break, you diagnose the root cause and suggest specific fix commands.

Your human will read this analysis and may ask Claude to execute the fixes.
So be VERY specific: exact commands, config changes, file paths.

Write ONLY in English. Be concise and actionable."""

    health_prompt_parts = [f"=== SYSTEM HEALTH WATCHDOG — {TODAY_ISO} ===\n"]
    health_prompt_parts.append(f"Total alerts: {total_alerts} (CRITICAL={crit_count}, HIGH={high_count}, MEDIUM={med_count})\n")

    if alerts:
        health_prompt_parts.append("## ALERTS DETECTED")
        for i, a in enumerate(alerts, 1):
            health_prompt_parts.append(f"\n### Alert {i}: [{a['severity']}] {a['category']}")
            health_prompt_parts.append(a["detail"])
    else:
        health_prompt_parts.append("## ALL SYSTEMS NOMINAL — no alerts triggered\n")

    health_prompt_parts.append(f"\n## CHECK SUMMARY")
    for name, c in checks.items():
        status = c.get("status", "?")
        health_prompt_parts.append(f"  {name}: {status}")

    if health:
        health_prompt_parts.append(f"\n## SYSTEM STATE")
        health_prompt_parts.append(f"  Uptime: {health.get('uptime', '?')}")
        health_prompt_parts.append(f"  Load: {health.get('load_avg', '?')}")
        health_prompt_parts.append(f"  RAM free: {health.get('mem_available_mb', 0)} MB / {health.get('mem_total_mb', 0)} MB")
        health_prompt_parts.append(f"  Swap: {health.get('swap_used_mb', 0)} MB")

    health_prompt_parts.append(f"""
─────────────────────────────────────────────────────
PROVIDE:

1. HEALTH VERDICT: Is the system healthy? Grade it GREEN/YELLOW/RED.

2. For EACH alert — ROOT CAUSE ANALYSIS:
   What likely caused this? Is it a one-off or systemic?

3. For EACH alert — SPECIFIC FIX:
   Exact shell commands or config changes. File paths.
   The human will ask Claude Code to execute these, so be precise.

4. PREVENTIVE MEASURES:
   What can be done to prevent recurrence?

5. PRIORITY ORDER: Which fix is most urgent?

If no alerts: confirm health, mention any metrics worth watching.
Keep it brief — 200-500 words. English only.""")

    health_prompt = "\n".join(health_prompt_parts)
    log(f"  Prompt: {len(health_prompt)} chars")

    # Use less heavy inference for health check (faster response for alerts)
    analysis = call_ollama(system_prompt, health_prompt, temperature=0.2, max_tokens=3000, think=True)

    if not analysis:
        log("Health LLM analysis failed — sending raw alert via Signal")
        # Still send Signal if we have critical/high alerts
        if crit_count + high_count > 0:
            raw_msg = f"⚠️ CLAWDBOT HEALTH ALERT ⚠️\n\n"
            raw_msg += f"Alerts: {crit_count} CRITICAL, {high_count} HIGH, {med_count} MEDIUM\n\n"
            for a in alerts:
                if a["severity"] in ("CRITICAL", "HIGH"):
                    raw_msg += f"[{a['severity']}] {a['category']}\n{a['detail'][:200]}\n\n"
            raw_msg += f"\nDashboard: {DASHBOARD_URL}"
            send_signal(raw_msg)
        return

    # ── Signal alert decision ──
    signal_sent = False
    if crit_count + high_count > 0:
        log("\n── Sending Signal alert (CRITICAL/HIGH alerts detected) ──")

        # Build Signal message: emoji-rich, compact
        sig_parts = [f"🚨 CLAWDBOT SYSTEM ALERT 🚨\n"]
        sig_parts.append(f"🔴 {crit_count} critical · 🟠 {high_count} high · 🟡 {med_count} medium\n")

        for a in alerts:
            if a["severity"] in ("CRITICAL", "HIGH"):
                icon = "🔴" if a["severity"] == "CRITICAL" else "🟠"
                sig_parts.append(f"{icon} {a['category'].upper()}")
                # Compact detail
                detail = a["detail"].split("\n")[0][:120]
                sig_parts.append(f"   {detail}\n")

        # Extract top fix from LLM analysis (first command-like line)
        fix_lines = [l.strip() for l in analysis.split("\n") if l.strip().startswith(("sudo ", "systemctl ", "journalctl ", "python3 ", "bash "))]
        if fix_lines:
            sig_parts.append(f"💡 Suggested fix:\n   {fix_lines[0][:120]}\n")

        sig_parts.append(f"📊 {DASHBOARD_URL}advisor.html")

        signal_msg = "\n".join(sig_parts)
        signal_sent = send_signal(signal_msg)
    elif total_alerts > 0:
        log("  Medium-only alerts — no Signal notification")
    else:
        log("  No alerts — system healthy ✓")

    elapsed = time.time() - t_start

    output = {
        "type": "system-think-health",
        "generated": NOW.isoformat(),
        "date": TODAY,
        "analysis": analysis,
        "alerts": alerts,
        "alert_counts": {"critical": crit_count, "high": high_count, "medium": med_count},
        "checks": checks,
        "signal_sent": signal_sent,
        "meta": {
            "duration_s": round(elapsed, 1),
            "prompt_chars": len(health_prompt),
            "analysis_chars": len(analysis),
            "total_jobs": total_jobs if 'total_jobs' in dir() else 0,
        },
    }

    out_file = THINK_DIR / f"system-health-{TODAY}.json"
    save_json(out_file, output)
    log(f"\nSaved: {out_file.name} ({out_file.stat().st_size:,} bytes)")

    latest = THINK_DIR / "latest-system-health.json"
    latest.unlink(missing_ok=True)
    latest.symlink_to(out_file.name)

    note = {
        "type": "health-watchdog",
        "title": f"System Health: {'🔴 ALERT' if crit_count else '🟠 WARNING' if high_count else '🟡 ATTENTION' if med_count else '✅ HEALTHY'}",
        "generated": output["generated"],
        "content": analysis,
        "summary": f"Health: {total_alerts} alerts (C={crit_count}/H={high_count}/M={med_count}), "
                   f"signal={'sent' if signal_sent else 'not needed'}",
    }
    save_json(THINK_DIR / f"note-system-health-{TODAY}.json", note)

    # Regen dashboard
    try:
        subprocess.run(["python3", "/opt/netscan/generate-html.py"],
                       timeout=120, capture_output=True)
        log("Dashboard regenerated")
    except Exception as e:
        log(f"Dashboard regen failed: {e}")

    log(f"\nDone in {elapsed:.0f}s")
    return output


# ══════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="System intelligence: GPU analysis, network security, health watchdog",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  system-think.py --gpu       # GPU telemetry deep-dive
  system-think.py --netsec    # Network + vulnerability analysis
  system-think.py --health    # Health watchdog + Signal alerts
  system-think.py --all       # Run all three sequentially
        """
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--gpu', '-g', action='store_true',
                       help="GPU telemetry analysis (thermals, throttle, cost)")
    group.add_argument('--netsec', '-n', action='store_true',
                       help="Network scan + vulnerability posture analysis")
    group.add_argument('--health', '-H', action='store_true',
                       help="System health watchdog with Signal alerts")
    group.add_argument('--all', action='store_true',
                       help="Run all three: gpu → netsec → health")

    args = parser.parse_args()

    if args.gpu:
        run_gpu()
    elif args.netsec:
        run_netsec()
    elif args.health:
        run_health()
    elif args.all:
        run_gpu()
        log("\n" + "─" * 60 + "\n")
        run_netsec()
        log("\n" + "─" * 60 + "\n")
        run_health()


if __name__ == "__main__":
    main()
