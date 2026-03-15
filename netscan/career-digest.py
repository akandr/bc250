#!/usr/bin/env python3
"""
Weekly Career Digest — Signal summary of the week's career intelligence.
========================================================================
Aggregates the last 7 days of career scan data + career-think summary
into a concise Signal-friendly digest. Sent every Sunday morning.

Data sources:
  - /opt/netscan/data/career/scan-YYYYMMDD-*.json  (daily scans)
  - /opt/netscan/data/careers/think/latest-summary.json (career-think)
  - /opt/netscan/data/salary/latest-salary.json (salary benchmarks)

Output:
  - /opt/netscan/data/career/weekly-digest-YYYYMMDD.json
  - Signal message to owner

Usage:
    python3 career-digest.py              # Generate + send
    python3 career-digest.py --dry-run    # Generate only, don't send via Signal
    python3 career-digest.py --days 14    # Look back 14 days instead of 7
"""

import json
import glob
import os
import sys
import urllib.request
from datetime import datetime, date, timedelta
from pathlib import Path
from collections import Counter

# ─── Configuration ──────────────────────────────────────────────────────────
DATA_DIR = Path("/opt/netscan/data")
CAREER_DIR = DATA_DIR / "career"
THINK_DIR = DATA_DIR / "careers/think"
SALARY_DIR = DATA_DIR / "salary"
OUTPUT_DIR = CAREER_DIR

SIGNAL_RPC = "http://127.0.0.1:8080/api/v1/rpc"
SIGNAL_ACCOUNT = os.environ.get('SIGNAL_ACCOUNT', '+<BOT_PHONE>')
SIGNAL_OWNER = os.environ.get('SIGNAL_OWNER', '+<OWNER_PHONE>')
SIGNAL_MAX_LEN = 1800

LOOKBACK_DAYS = 7
TODAY = date.today()
TODAY_STR = TODAY.strftime("%Y%m%d")


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def collect_weekly_scans(days):
    """Collect all career scan results from the last N days."""
    cutoff = TODAY - timedelta(days=days)
    pattern = str(CAREER_DIR / "scan-*.json")
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)

    all_jobs = []
    scan_count = 0
    companies_seen = set()

    for f in files:
        # Parse date from filename: scan-YYYYMMDD-HHMM.json
        basename = os.path.basename(f)
        try:
            date_part = basename.split("-")[1]  # YYYYMMDD
            file_date = datetime.strptime(date_part, "%Y%m%d").date()
        except (IndexError, ValueError):
            continue

        if file_date < cutoff:
            continue

        data = read_json(f)
        if not data:
            continue

        scan_count += 1
        meta = data.get("meta", {})
        companies_seen.add(meta.get("companies_scanned", 0))

        for job in data.get("jobs", []):
            # Dedup by (company, title)
            key = (job.get("company", ""), job.get("title", ""))
            job["_dedup_key"] = f"{key[0]}:{key[1]}"
            job["_scan_date"] = file_date.isoformat()
            all_jobs.append(job)

    # Deduplicate — keep highest score version
    seen = {}
    for job in all_jobs:
        key = job["_dedup_key"]
        if key not in seen or job.get("match_score", 0) > seen[key].get("match_score", 0):
            seen[key] = job

    unique_jobs = sorted(seen.values(),
                         key=lambda j: j.get("match_score", 0), reverse=True)

    return {
        "scan_count": scan_count,
        "unique_jobs": unique_jobs,
        "total_raw": len(all_jobs),
    }


def build_digest(scan_data, days):
    """Build the weekly digest message."""
    jobs = scan_data["unique_jobs"]
    hot = [j for j in jobs if j.get("match_score", 0) >= 70]
    warm = [j for j in jobs if 55 <= j.get("match_score", 0) < 70]
    remote = [j for j in jobs if j.get("remote_compatible")]

    # Company frequency
    companies = Counter(j.get("company", "?") for j in jobs)
    top_companies = companies.most_common(5)

    # Career-think summary
    think_summary = read_json(THINK_DIR / "latest-summary.json")
    think_text = ""
    if think_summary and think_summary.get("summary"):
        # Extract first paragraph (market overview)
        lines = think_summary["summary"].split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("*For"):
                think_text = stripped[:300]
                break

    # Salary data
    salary = read_json(SALARY_DIR / "latest-salary.json")
    salary_text = ""
    if salary:
        benchmarks = salary.get("benchmarks", salary.get("summary", ""))
        if isinstance(benchmarks, str) and benchmarks:
            salary_text = benchmarks[:200]

    # Build message
    parts = []
    period = f"{(TODAY - timedelta(days=days)).strftime('%d.%m')}–{TODAY.strftime('%d.%m')}"
    parts.append(f"📋 WEEKLY CAREER DIGEST ({period})")
    parts.append(f"{scan_data['scan_count']} scans, {len(jobs)} unique jobs found")
    parts.append("")

    if hot:
        parts.append(f"🔥 HOT MATCHES ({len(hot)}):")
        for j in hot[:5]:
            score = j.get("match_score", 0)
            title = j.get("title", "?")[:60]
            company = j.get("company", "?")
            remote_flag = " 🌍" if j.get("remote_compatible") else ""
            parts.append(f"  [{score}%] {title} @ {company}{remote_flag}")
        if len(hot) > 5:
            parts.append(f"  ...and {len(hot)-5} more")
        parts.append("")

    if warm:
        parts.append(f"📊 WORTH CHECKING ({len(warm)}):")
        for j in warm[:5]:
            score = j.get("match_score", 0)
            title = j.get("title", "?")[:50]
            company = j.get("company", "?")
            remote_flag = " 🌍" if j.get("remote_compatible") else ""
            parts.append(f"  [{score}%] {title} @ {company}{remote_flag}")
        if len(warm) > 5:
            parts.append(f"  ...and {len(warm)-5} more")
        parts.append("")

    if not hot and not warm:
        parts.append("No strong matches this week. Market is quiet.")
        parts.append("")

    # Stats line
    parts.append(f"📈 Stats: {len(remote)} remote-friendly, top hiring: " +
                 ", ".join(f"{c}({n})" for c, n in top_companies[:3]))

    if think_text:
        parts.append("")
        parts.append(f"🧠 Intel: {think_text}")

    if salary_text:
        parts.append("")
        parts.append(f"💰 Salary: {salary_text}")

    msg = "\n".join(parts)
    # Truncate to Signal limit
    if len(msg) > SIGNAL_MAX_LEN:
        msg = msg[:SIGNAL_MAX_LEN - 20] + "\n[truncated]"

    return msg


def send_signal(text):
    """Send message via Signal JSON-RPC."""
    payload = json.dumps({
        "jsonrpc": "2.0", "id": f"digest-{int(datetime.now().timestamp())}",
        "method": "send",
        "params": {
            "account": SIGNAL_ACCOUNT,
            "recipient": [SIGNAL_OWNER],
            "message": text
        }
    }).encode()
    try:
        req = urllib.request.Request(
            SIGNAL_RPC, data=payload,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return True
    except Exception as e:
        log(f"Signal send error: {e}")
        return False


def main():
    dry_run = "--dry-run" in sys.argv
    days = LOOKBACK_DAYS

    # Parse --days N
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--days" and i < len(sys.argv) - 1:
            try:
                days = int(sys.argv[i + 1])
            except ValueError:
                pass

    log(f"Career weekly digest — looking back {days} days")

    scan_data = collect_weekly_scans(days)
    log(f"Found {scan_data['scan_count']} scans, {len(scan_data['unique_jobs'])} unique jobs "
        f"(from {scan_data['total_raw']} raw)")

    digest_msg = build_digest(scan_data, days)

    # Save to file
    output_path = OUTPUT_DIR / f"weekly-digest-{TODAY_STR}.json"
    output_data = {
        "meta": {
            "type": "weekly-career-digest",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "lookback_days": days,
            "scans_analyzed": scan_data["scan_count"],
            "unique_jobs": len(scan_data["unique_jobs"]),
        },
        "message": digest_msg,
        "jobs": scan_data["unique_jobs"],
    }
    # Clean internal keys
    for job in output_data["jobs"]:
        job.pop("_dedup_key", None)
        job.pop("_scan_date", None)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
    log(f"Saved: {output_path}")

    print(digest_msg)
    print()

    if dry_run:
        log("Dry run — not sending via Signal")
    else:
        log("Sending via Signal...")
        if send_signal(digest_msg):
            log("Sent successfully")
        else:
            log("WARNING: Failed to send")

    log("Done")


if __name__ == "__main__":
    main()
