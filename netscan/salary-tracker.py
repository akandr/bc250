#!/usr/bin/env python3
"""
salary-tracker.py — Nightly salary & rate intelligence tracker
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Collects salary data from multiple sources and tracks trends for
embedded Linux / camera driver roles in Poland.

Sources:
  - Existing career-scan JSON (extract salary fields already parsed)
  - NoFluffJobs salary API (live, structured)
  - JustJoinIT salary data (live, structured)
  - levels.fyi Poland data (live)
  - Bulldogjob (live, structured)

Output: /opt/netscan/data/salary/
  - salary-YYYYMMDD.json      (daily snapshot)
  - salary-history.json       (rolling 180-day trend DB)
  - latest-salary.json        (symlink to latest)

Cron: 0 2 * * * flock -w 1200 /tmp/ollama-gpu.lock python3 /opt/netscan/salary-tracker.py
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────
OLLAMA_URL = "http://localhost:11434"
OLLAMA_CHAT = f"{OLLAMA_URL}/api/chat"
OLLAMA_MODEL = "huihui_ai/qwen3-abliterated:14b"

SALARY_DIR = Path("/opt/netscan/data/salary")
CAREER_DIR = Path("/opt/netscan/data/career")
HISTORY_FILE = SALARY_DIR / "salary-history.json"
PROFILE_FILE = Path("/opt/netscan/profile-private.json")

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
HISTORY_DAYS = 180

# Keywords for filtering relevant listings
ROLE_KEYWORDS = [
    "embedded", "kernel", "driver", "linux", "firmware", "bsp",
    "camera", "v4l2", "mipi", "csi", "isp", "libcamera",
    "soc", "automotive", "adas", "sensor", "imaging",
    "gstreamer", "device tree", "dma", "pcie", "i2c", "spi",
    "fpga", "rtos", "c/c++", "low-level", "bare-metal",
    "gpu", "vulkan", "drm",
]

# ── Helpers ────────────────────────────────────────────────────────────────

def log(msg):
    print(f"  {msg}", flush=True)

def fetch_url(url, timeout=25):
    """Fetch URL, return text or None."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/json,*/*",
            "Accept-Language": "en-US,en;q=0.9,pl;q=0.8",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return data.decode(charset, errors="replace")
    except Exception as e:
        log(f"  fetch error {url}: {e}")
        return None

def fetch_json(url, timeout=25):
    """Fetch URL expecting JSON, return dict or None."""
    text = fetch_url(url, timeout)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        log(f"  JSON parse error {url}: {e}")
        return None

def matches_role(text):
    """Check if text matches our role keywords."""
    if not text:
        return False
    lower = text.lower()
    return any(kw in lower for kw in ROLE_KEYWORDS)

def call_ollama(system_prompt, user_prompt, temperature=0.3, max_tokens=3000):
    """Call Ollama for LLM analysis. Returns content string or None."""
    try:
        # Health check
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=10) as r:
            tags = json.loads(r.read())
            models = [m["name"] for m in tags.get("models", [])]
            if not any(OLLAMA_MODEL in m for m in models):
                log(f"  Model {OLLAMA_MODEL} not found in Ollama")
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
        "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": 12288},
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
            return content
    except Exception as e:
        log(f"  Ollama call failed: {e}")
        return None


# ── Source: Existing career-scan JSONs ─────────────────────────────────────

def collect_from_career_scans():
    """Extract salary data from recent career-scan output files."""
    entries = []
    if not CAREER_DIR.exists():
        return entries

    # Look at scans from last 7 days
    cutoff = datetime.now() - timedelta(days=7)
    for f in sorted(CAREER_DIR.glob("scan-*.json")):
        try:
            # Parse date from filename: scan-YYYYMMDD-HHMM.json
            parts = f.stem.split("-")
            fdate = datetime.strptime(f"{parts[1]}-{parts[2]}", "%Y%m%d-%H%M")
            if fdate < cutoff:
                continue

            data = json.load(open(f))
            for job in data.get("jobs", []):
                sal_b2b = job.get("salary_b2b_net_pln")
                sal_uop = job.get("salary_uop_gross_pln")
                if not sal_b2b and not sal_uop:
                    continue
                entries.append({
                    "source": "career-scan",
                    "scan_date": fdate.strftime("%Y-%m-%d"),
                    "title": job.get("title", ""),
                    "company": job.get("company", ""),
                    "location": job.get("location", ""),
                    "salary_b2b_net_pln": sal_b2b,
                    "salary_uop_gross_pln": sal_uop,
                    "salary_source": job.get("salary_source", "unknown"),
                    "salary_note": job.get("salary_note", ""),
                    "match_score": job.get("match_score", 0),
                    "remote_compatible": job.get("remote_compatible", False),
                    "url": job.get("job_url", ""),
                })
        except Exception as e:
            log(f"  Error reading {f.name}: {e}")

    log(f"Career scans: {len(entries)} salary records from last 7 days")
    return entries


# ── Source: NoFluffJobs API ────────────────────────────────────────────────

def collect_from_nofluffjobs():
    """Fetch salary data from NoFluffJobs API."""
    entries = []
    keywords = ["embedded", "linux", "kernel", "camera", "driver", "firmware",
                "bsp", "automotive", "adas", "c++"]
    seen_ids = set()

    for kw in keywords:
        url = f"https://nofluffjobs.com/api/posting?salaryCurrency=PLN&requirement={urllib.parse.quote(kw)}&country=PL"
        data = fetch_json(url, timeout=30)
        if not data:
            continue

        postings = data if isinstance(data, list) else data.get("postings", [])
        for p in postings:
            pid = p.get("id", p.get("url", ""))
            if pid in seen_ids:
                continue
            seen_ids.add(pid)

            title = p.get("title", p.get("name", ""))
            if not matches_role(title + " " + " ".join(p.get("mulesoftTags", [])) +
                               " " + " ".join(str(r) for r in p.get("requirements", []))):
                continue

            salary = p.get("salary", {})
            if isinstance(salary, dict):
                sal_from = salary.get("from", 0)
                sal_to = salary.get("to", 0)
                sal_type = salary.get("type", "b2b")
                sal_currency = salary.get("currency", "PLN")
            else:
                continue

            if sal_currency != "PLN" or sal_from == 0:
                continue

            entry = {
                "source": "nofluffjobs",
                "scan_date": datetime.now().strftime("%Y-%m-%d"),
                "title": title,
                "company": p.get("company", {}).get("name", p.get("companyName", "")),
                "location": p.get("location", {}).get("places", [{}])[0].get("city", "remote")
                            if isinstance(p.get("location"), dict) else "unknown",
                "remote_compatible": p.get("fullyRemote", False) or p.get("location", {}).get("fullyRemote", False),
                "salary_type": sal_type,
                "salary_from": sal_from,
                "salary_to": sal_to,
                "salary_currency": sal_currency,
                "url": f"https://nofluffjobs.com/pl/job/{p.get('url', pid)}",
            }

            # Normalize to B2B net PLN monthly
            if sal_type == "b2b":
                entry["salary_b2b_net_pln"] = f"{sal_from}-{sal_to}"
            elif sal_type in ("uop", "permanent"):
                entry["salary_uop_gross_pln"] = f"{sal_from}-{sal_to}"
                entry["salary_b2b_net_pln"] = f"{int(sal_from/0.82)}-{int(sal_to/0.82)}"

            entries.append(entry)

        time.sleep(1)  # rate limit

    log(f"NoFluffJobs: {len(entries)} relevant salary listings")
    return entries


# ── Source: JustJoinIT ─────────────────────────────────────────────────────

def collect_from_justjoinit():
    """Fetch salary data from JustJoin.it API."""
    entries = []
    url = "https://api.justjoin.it/v2/user-panel/offers?categories[]=embedded&categories[]=devops&categories[]=other&page=1&sortBy=newest&orderBy=DESC&perPage=100&salaryCurrencies[]=PLN"
    data = fetch_json(url, timeout=30)
    if not data:
        # Fallback: try older API
        url = "https://justjoin.it/api/offers"
        data = fetch_json(url, timeout=30)

    if not data:
        log("JustJoinIT: API unavailable")
        return entries

    offers = data if isinstance(data, list) else data.get("data", data.get("offers", []))
    for o in offers:
        title = o.get("title", "")
        skills = " ".join(s.get("name", "") for s in o.get("skills", o.get("requiredSkills", [])))
        marker = o.get("marker_icon", o.get("category", ""))

        if not matches_role(f"{title} {skills} {marker}"):
            continue

        # Extract salary
        emp_types = o.get("employment_types", o.get("employmentTypes", []))
        for et in emp_types:
            sal = et.get("salary", et.get("from_pln", None))
            if isinstance(sal, dict):
                sal_from = sal.get("from", 0)
                sal_to = sal.get("to", 0)
                sal_currency = sal.get("currency", "PLN")
            elif isinstance(et.get("from"), (int, float)):
                sal_from = et.get("from", 0)
                sal_to = et.get("to", 0)
                sal_currency = et.get("currency", "PLN")
            else:
                continue

            if sal_currency != "PLN" or sal_from == 0:
                continue

            sal_type = et.get("type", "b2b")
            entry = {
                "source": "justjoinit",
                "scan_date": datetime.now().strftime("%Y-%m-%d"),
                "title": title,
                "company": o.get("company_name", o.get("companyName", "")),
                "location": o.get("city", o.get("workplace", {}).get("city", "remote")),
                "remote_compatible": o.get("remote", o.get("workplace_type", "")) in (True, "remote", "partly_remote"),
                "salary_type": sal_type,
                "salary_from": sal_from,
                "salary_to": sal_to,
                "url": f"https://justjoin.it/offers/{o.get('slug', o.get('id', ''))}",
            }

            if sal_type == "b2b":
                entry["salary_b2b_net_pln"] = f"{sal_from}-{sal_to}"
            elif sal_type in ("uop", "permanent"):
                entry["salary_uop_gross_pln"] = f"{sal_from}-{sal_to}"
                entry["salary_b2b_net_pln"] = f"{int(sal_from/0.82)}-{int(sal_to/0.82)}"

            entries.append(entry)
            break  # one salary per offer

    log(f"JustJoinIT: {len(entries)} relevant salary listings")
    return entries


# ── Source: Bulldogjob ─────────────────────────────────────────────────────

def collect_from_bulldogjob():
    """Fetch salary data from Bulldogjob."""
    entries = []
    for kw in ["embedded", "linux", "kernel", "camera", "driver", "firmware"]:
        url = f"https://bulldogjob.pl/api/companies/jobs?q={urllib.parse.quote(kw)}"
        data = fetch_json(url, timeout=30)
        if not data:
            continue

        jobs = data if isinstance(data, list) else data.get("jobs", data.get("data", []))
        for j in jobs:
            title = j.get("title", j.get("position", ""))
            if not matches_role(title):
                continue

            sal = j.get("salary", {})
            if isinstance(sal, dict) and sal.get("from"):
                entries.append({
                    "source": "bulldogjob",
                    "scan_date": datetime.now().strftime("%Y-%m-%d"),
                    "title": title,
                    "company": j.get("company", {}).get("name", ""),
                    "location": j.get("city", j.get("location", "")),
                    "salary_from": sal.get("from", 0),
                    "salary_to": sal.get("to", 0),
                    "salary_type": sal.get("employment_type", "b2b"),
                    "salary_currency": sal.get("currency", "PLN"),
                    "url": j.get("url", j.get("apply_url", "")),
                })
        time.sleep(1)

    log(f"Bulldogjob: {len(entries)} relevant salary listings")
    return entries


# ── Analysis ───────────────────────────────────────────────────────────────

def parse_salary_range(s):
    """Parse '20000-30000' or '20000' into (low, high) ints."""
    if not s:
        return None, None
    s = str(s).replace(" ", "").replace(",", "")
    m = re.match(r"(\d+)\s*[-–]\s*(\d+)", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"(\d+)", s)
    if m:
        v = int(m.group(1))
        return v, v
    return None, None

def compute_statistics(entries):
    """Compute salary statistics from collected entries."""
    b2b_ranges = []
    for e in entries:
        sal = e.get("salary_b2b_net_pln") or e.get("salary_from")
        if e.get("salary_b2b_net_pln"):
            low, high = parse_salary_range(e["salary_b2b_net_pln"])
        elif e.get("salary_type") == "b2b" and e.get("salary_from"):
            low, high = e["salary_from"], e.get("salary_to", e["salary_from"])
        else:
            continue

        if low and high and low > 0:
            b2b_ranges.append({
                "low": low, "high": high, "mid": (low + high) // 2,
                "title": e.get("title", ""),
                "company": e.get("company", ""),
                "source": e.get("source", ""),
            })

    if not b2b_ranges:
        return {"sample_size": 0}

    mids = sorted(r["mid"] for r in b2b_ranges)
    lows = sorted(r["low"] for r in b2b_ranges)
    highs = sorted(r["high"] for r in b2b_ranges)

    def percentile(data, p):
        k = (len(data) - 1) * p / 100
        f = int(k)
        c = f + 1 if f + 1 < len(data) else f
        return data[f] + (k - f) * (data[c] - data[f])

    stats = {
        "sample_size": len(b2b_ranges),
        "b2b_net_pln_monthly": {
            "min": min(lows),
            "max": max(highs),
            "median_mid": int(percentile(mids, 50)),
            "p25_mid": int(percentile(mids, 25)),
            "p75_mid": int(percentile(mids, 75)),
            "avg_mid": int(sum(mids) / len(mids)),
        },
        "by_source": {},
        "top_paying": sorted(b2b_ranges, key=lambda r: r["high"], reverse=True)[:5],
    }

    # Breakdown by source
    sources = set(r["source"] for r in b2b_ranges)
    for src in sources:
        src_mids = [r["mid"] for r in b2b_ranges if r["source"] == src]
        if src_mids:
            stats["by_source"][src] = {
                "count": len(src_mids),
                "avg_mid": int(sum(src_mids) / len(src_mids)),
                "min": min(src_mids),
                "max": max(src_mids),
            }

    return stats

def llm_analyze_trends(today_stats, history):
    """Use LLM to analyze salary trends."""
    if today_stats.get("sample_size", 0) < 3:
        return "Insufficient data for trend analysis."

    # Build history context
    hist_lines = []
    recent = sorted(history.get("daily_snapshots", []), key=lambda s: s.get("date", ""))[-30:]
    for snap in recent:
        s = snap.get("stats", {}).get("b2b_net_pln_monthly", {})
        if s:
            hist_lines.append(f"{snap['date']}: median={s.get('median_mid',0)} PLN, "
                            f"n={snap.get('stats',{}).get('sample_size',0)}, "
                            f"range={s.get('min',0)}-{s.get('max',0)}")

    system = """You are a salary market analyst specializing in embedded Linux and camera driver
engineering roles in Poland. You analyze B2B net monthly rates in PLN.
Respond in concise bullet points. No markdown headers. /no_think"""

    prompt = f"""Today's salary snapshot ({today_stats['sample_size']} data points):
- Median midpoint: {today_stats['b2b_net_pln_monthly']['median_mid']} PLN/month B2B net
- Range: {today_stats['b2b_net_pln_monthly']['min']} – {today_stats['b2b_net_pln_monthly']['max']} PLN
- P25-P75: {today_stats['b2b_net_pln_monthly']['p25_mid']} – {today_stats['b2b_net_pln_monthly']['p75_mid']} PLN
- Top paying: {json.dumps(today_stats['top_paying'][:3], ensure_ascii=False)}

Recent 30-day history:
{chr(10).join(hist_lines) if hist_lines else "No prior history yet (first run)."}

Analyze:
1. Current market rate for this niche (embedded Linux camera drivers, Poland)
2. Any visible trends (rising/falling/stable) if history available
3. How this compares to FAANG vs silicon vs automotive tiers
4. Actionable insights for someone at T4 level (15yr exp) considering T5 promotion or external move"""

    return call_ollama(system, prompt, temperature=0.3, max_tokens=2000)


# ── History Management ─────────────────────────────────────────────────────

def load_history():
    """Load rolling salary history."""
    if HISTORY_FILE.exists():
        try:
            return json.load(open(HISTORY_FILE))
        except Exception:
            pass
    return {"daily_snapshots": [], "version": 1}

def save_history(history):
    """Save history, pruning entries older than HISTORY_DAYS."""
    cutoff = (datetime.now() - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")
    history["daily_snapshots"] = [
        s for s in history["daily_snapshots"]
        if s.get("date", "") >= cutoff
    ]
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

    dt = datetime.now()
    today = dt.strftime("%Y-%m-%d")
    print(f"[{dt.strftime('%Y-%m-%d %H:%M:%S')}] salary-tracker starting", flush=True)

    SALARY_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    # ── Collect from all sources ──
    all_entries = []

    log("Phase 1: Collecting salary data...")
    all_entries.extend(collect_from_career_scans())
    all_entries.extend(collect_from_nofluffjobs())
    time.sleep(2)
    all_entries.extend(collect_from_justjoinit())
    time.sleep(2)
    all_entries.extend(collect_from_bulldogjob())

    log(f"Total: {len(all_entries)} salary records collected")

    # ── Compute statistics ──
    log("Phase 2: Computing statistics...")
    stats = compute_statistics(all_entries)
    log(f"Stats: {stats.get('sample_size', 0)} valid B2B salary ranges")

    # ── LLM trend analysis ──
    log("Phase 3: LLM trend analysis...")
    history = load_history()
    analysis = llm_analyze_trends(stats, history) or "LLM analysis unavailable."

    # ── Build output ──
    duration = int(time.time() - t0)
    snapshot = {
        "meta": {
            "timestamp": dt.isoformat(timespec="seconds"),
            "duration_seconds": duration,
            "total_records": len(all_entries),
            "sources": list(set(e.get("source", "unknown") for e in all_entries)),
        },
        "stats": stats,
        "analysis": analysis,
        "entries": all_entries,
    }

    # Save daily snapshot
    fname = f"salary-{dt.strftime('%Y%m%d')}.json"
    out_path = SALARY_DIR / fname
    with open(out_path, "w") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)

    # Update latest symlink
    latest = SALARY_DIR / "latest-salary.json"
    latest.unlink(missing_ok=True)
    latest.symlink_to(fname)

    # Update history
    history["daily_snapshots"].append({
        "date": today,
        "stats": stats,
        "record_count": len(all_entries),
    })
    save_history(history)

    # Cleanup: keep last 60 daily snapshots
    snapshots = sorted(SALARY_DIR.glob("salary-2*.json"))
    for old in snapshots[:-60]:
        old.unlink(missing_ok=True)

    log(f"Saved: {out_path}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] salary-tracker done ({duration}s)", flush=True)


if __name__ == "__main__":
    main()
