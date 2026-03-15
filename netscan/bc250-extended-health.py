#!/usr/bin/env python3
"""
bc250-extended-health.py — Extended health check & assessment for BC-250
Phase 18: Validates all scripts executed, data freshness, LLM output quality,
Chinese text contamination, stale dashboards, and generates an overall assessment.

Usage:
    python3 bc250-extended-health.py                 # Full check + LLM assessment
    python3 bc250-extended-health.py --quick          # Data freshness only (no LLM)
    python3 bc250-extended-health.py --fix-chinese    # Clean Chinese from think notes
"""

import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from llm_sanitize import sanitize_llm_output

# ── Config ─────────────────────────────────────────────────────────────────
DATA_DIR = Path(os.environ.get("NETSCAN_DATA", "/opt/netscan/data"))
WEB_DIR = Path(os.environ.get("NETSCAN_WEB", "/opt/netscan/web"))
THINK_DIR = DATA_DIR / "think"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_CHAT = f"{OLLAMA_URL}/v1/chat/completions"
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:14b")

# CJK detection regex
CJK_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf\u3000-\u303f\uff00-\uffef]')

# Expected data sources and their max staleness (hours)
# Data lives in subdirectories under DATA_DIR, e.g. data/career/, data/events/
EXPECTED_DATA = {
    # Scrape outputs (in subdirectories)
    "career":        {"max_hours": 48, "pattern": "career/scan-*.json"},
    "company":       {"max_hours": 72, "pattern": "careers/think/**/*.json"},
    "academic":      {"max_hours": 72, "pattern": "academic/latest-*.json"},
    "events":        {"max_hours": 72, "pattern": "events/events-*.json"},
    "radio":         {"max_hours": 48, "pattern": "radio/radio-*.json"},
    "patents":       {"max_hours": 72, "pattern": "patents/patents-*.json"},
    "leaks":         {"max_hours": 48, "pattern": "leaks/leak-*.json"},
    "sensors":       {"max_hours": 72, "pattern": "csi-sensors/latest-csi.json"},
    "salary":        {"max_hours": 72, "pattern": "salary/salary-*.json"},
    "car":           {"max_hours": 48, "pattern": "car-tracker/car-tracker-*.json"},
    "home":          {"max_hours": 24, "pattern": "correlate/correlate-*.json"},
    "weather":       {"max_hours": 24, "pattern": "think/note-weather-*.json"},
    "news":          {"max_hours": 48, "pattern": "news/raw-news.json"},
    "books":         {"max_hours": 168, "pattern": "think/note-publication-*.json"},
    "city":          {"max_hours": 72, "pattern": "city/city-watch-*.json"},
    "ha-journal":    {"max_hours": 48, "pattern": "ha-journal/raw-ha-data.json"},
    "gpu":           {"max_hours": 48, "pattern": "gpu/gpu-*.csv"},
    "market":        {"max_hours": 72, "pattern": "market/market-*.json"},
    # Think notes (should appear from queue-runner)
    "think/career":  {"max_hours": 72, "pattern": "think/note-career-*.json"},
    "think/system":  {"max_hours": 72, "pattern": "think/note-system-*.json"},
    "think/life":    {"max_hours": 72, "pattern": "think/note-life-*.json"},
    "think/research":{"max_hours": 72, "pattern": "think/note-research-*.json"},
    "think/home":    {"max_hours": 72, "pattern": "think/note-home-*.json"},
    "think/trends":  {"max_hours": 72, "pattern": "think/note-trends-*.json"},
}

# Expected HTML dashboard pages
EXPECTED_PAGES = [
    "index.html", "hosts.html", "presence.html", "security.html",
    "history.html", "log.html", "home.html", "notes.html",
    "academic.html", "radio.html", "events.html", "career.html",
    "car.html", "advisor.html", "load.html", "leaks.html",
    "weather.html", "news.html", "health.html",
]


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def check_data_freshness():
    """Check all expected data directories for freshness."""
    log("═══ Data Freshness Check ═══")
    results = {}
    now = datetime.now()

    for name, spec in EXPECTED_DATA.items():
        max_hours = spec["max_hours"]
        pattern = spec["pattern"]

        # Find matching files
        files = sorted(DATA_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)

        if not files:
            status = "MISSING"
            age_hours = -1
            newest = None
        else:
            newest = files[0]
            mtime = datetime.fromtimestamp(newest.stat().st_mtime)
            age_hours = (now - mtime).total_seconds() / 3600

            if age_hours > max_hours:
                status = "STALE"
            else:
                status = "OK"

        results[name] = {
            "status": status,
            "age_hours": round(age_hours, 1),
            "max_hours": max_hours,
            "newest_file": str(newest.name) if newest else None,
            "file_count": len(files),
        }

        icon = {"OK": "✓", "STALE": "⚠", "MISSING": "✗"}[status]
        if status == "OK":
            log(f"  {icon} {name}: {age_hours:.0f}h old ({len(files)} files)")
        elif status == "STALE":
            log(f"  {icon} {name}: STALE — {age_hours:.0f}h old (max {max_hours}h), {len(files)} files")
        else:
            log(f"  {icon} {name}: MISSING — no files matching {pattern}")

    return results


def check_dashboard_freshness():
    """Check dashboard HTML pages for staleness."""
    log("\n═══ Dashboard Freshness Check ═══")
    results = {}
    now = datetime.now()

    for page in EXPECTED_PAGES:
        path = WEB_DIR / page
        if not path.exists():
            results[page] = {"status": "MISSING", "age_hours": -1}
            log(f"  ✗ {page}: MISSING")
            continue

        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        age_hours = (now - mtime).total_seconds() / 3600

        if age_hours > 24:
            status = "STALE"
            log(f"  ⚠ {page}: STALE — {age_hours:.0f}h old")
        else:
            status = "OK"
            log(f"  ✓ {page}: {age_hours:.1f}h old")

        results[page] = {"status": status, "age_hours": round(age_hours, 1)}

    return results


def check_chinese_contamination():
    """Scan think notes for Chinese text contamination."""
    log("\n═══ Chinese Text Contamination Check ═══")
    results = {"clean": 0, "contaminated": 0, "files": []}

    if not THINK_DIR.exists():
        log("  THINK_DIR does not exist")
        return results

    for f in sorted(THINK_DIR.glob("note-*.json")):
        try:
            data = json.loads(f.read_text())
            content = data.get("content", "") + data.get("title", "")
            chinese_chars = CJK_RE.findall(content)

            if chinese_chars:
                results["contaminated"] += 1
                results["files"].append({
                    "name": f.name,
                    "chinese_count": len(chinese_chars),
                    "age_hours": round((datetime.now() - datetime.fromtimestamp(f.stat().st_mtime)).total_seconds() / 3600, 1),
                })
            else:
                results["clean"] += 1
        except Exception:
            pass

    total = results["clean"] + results["contaminated"]
    log(f"  Scanned {total} think notes")
    log(f"  ✓ Clean: {results['clean']}")
    if results["contaminated"]:
        log(f"  ✗ Contaminated: {results['contaminated']}")
        for cf in results["files"][:10]:
            log(f"    - {cf['name']}: {cf['chinese_count']} Chinese chars")
    else:
        log(f"  ✓ No Chinese contamination found")

    return results


def fix_chinese_notes():
    """Clean Chinese text from existing think notes."""
    log("\n═══ Cleaning Chinese Text from Think Notes ═══")
    fixed = 0
    skipped = 0

    if not THINK_DIR.exists():
        log("  THINK_DIR does not exist")
        return 0

    for f in sorted(THINK_DIR.glob("note-*.json")):
        try:
            data = json.loads(f.read_text())
            content = data.get("content", "")
            title = data.get("title", "")

            # Check if contaminated
            if not CJK_RE.search(content) and not CJK_RE.search(title):
                skipped += 1
                continue

            # Sanitize
            new_content = sanitize_llm_output(content)
            new_title = sanitize_llm_output(title)

            # Skip if sanitization wiped everything
            if not new_content.strip():
                log(f"  ⚠ {f.name}: content became empty after sanitization, skipping")
                continue

            data["content"] = new_content
            data["title"] = new_title
            data["_sanitized"] = datetime.now().isoformat(timespec="seconds")

            with open(f, "w") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
            fixed += 1
            log(f"  ✓ Fixed: {f.name}")
        except Exception as e:
            log(f"  ✗ Error processing {f.name}: {e}")

    log(f"\n  Fixed: {fixed}, Already clean: {skipped}")
    return fixed


def check_queue_runner_jobs():
    """Check job execution stats from queue-runner."""
    log("\n═══ Queue Runner Job Assessment ═══")

    # jobs.json is at /opt/netscan/data/jobs.json with {"version": N, "jobs": [...]}
    jobs_file = Path("/opt/netscan/data/jobs.json")
    state_file = DATA_DIR / "queue-runner-state.json"

    # Queue runner state
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
            log(f"  Cycle: {state.get('cycle', '?')}, last: {state.get('last_cycle_date', '?')}")
            if state.get("nightly_batch_date"):
                log(f"  Nightly batch: {state['nightly_batch_date']} (done={state.get('nightly_batch_done', '?')}, idx={state.get('nightly_batch_index', '?')})")
        except Exception:
            pass

    if not jobs_file.exists():
        log(f"  jobs.json not found at {jobs_file}")
        return {}

    try:
        raw = json.loads(jobs_file.read_text())
        jobs = raw.get("jobs", raw) if isinstance(raw, dict) else raw
    except Exception as e:
        log(f"  Failed to parse jobs.json: {e}")
        return {}

    total = len(jobs)
    by_name = {}

    for job in jobs:
        if not isinstance(job, dict):
            continue
        name = job.get("name", "unknown")
        if name not in by_name:
            by_name[name] = 0
        by_name[name] += 1

    log(f"  Total jobs: {total}")
    log(f"  Unique job names: {len(by_name)}")
    for name, count in sorted(by_name.items(), key=lambda x: -x[1])[:15]:
        log(f"    {name}: {count}")

    return {
        "total": total,
        "unique_names": len(by_name),
        "by_name": by_name,
    }


def check_services():
    """Check core service status."""
    log("\n═══ Service Status ═══")
    results = {}

    # Ollama
    try:
        req = urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=5)
        data = json.loads(req.read())
        models = [m["name"] for m in data.get("models", [])]
        has_model = any(OLLAMA_MODEL in m for m in models)
        results["ollama"] = {"status": "OK" if has_model else "NO_MODEL", "models": models}
        log(f"  ✓ Ollama: OK ({len(models)} models)")
    except Exception as e:
        results["ollama"] = {"status": "DOWN", "error": str(e)}
        log(f"  ✗ Ollama: DOWN — {e}")

    # Ollama loaded models
    try:
        req = urllib.request.urlopen(f"{OLLAMA_URL}/api/ps", timeout=5)
        ps = json.loads(req.read())
        loaded = [m["name"] for m in ps.get("models", [])]
        results["ollama_loaded"] = loaded
        if loaded:
            log(f"  ✓ Loaded models: {', '.join(loaded)}")
        else:
            log(f"  · No models currently loaded (GPU free)")
    except Exception:
        results["ollama_loaded"] = []

    # Queue-runner via systemctl
    qr_active = os.system("systemctl is-active queue-runner.service >/dev/null 2>&1") == 0
    results["queue_runner"] = "active" if qr_active else "inactive"
    icon = "✓" if qr_active else "✗"
    log(f"  {icon} Queue-runner: {'active' if qr_active else 'INACTIVE'}")

    return results


def generate_llm_assessment(data_results, dashboard_results, chinese_results, job_results, service_results):
    """Use LLM to generate an overall health assessment."""
    log("\n═══ Generating LLM Health Assessment ═══")

    # Build summary for LLM
    summary_parts = ["## BC-250 System Health Data\n"]

    # Services
    summary_parts.append("### Services")
    for svc, info in service_results.items():
        if isinstance(info, dict):
            summary_parts.append(f"- {svc}: {info.get('status', info)}")
        else:
            summary_parts.append(f"- {svc}: {info}")

    # Data freshness
    summary_parts.append("\n### Data Freshness")
    ok_count = sum(1 for v in data_results.values() if v["status"] == "OK")
    stale_count = sum(1 for v in data_results.values() if v["status"] == "STALE")
    missing_count = sum(1 for v in data_results.values() if v["status"] == "MISSING")
    summary_parts.append(f"OK: {ok_count}, Stale: {stale_count}, Missing: {missing_count}")
    for name, info in data_results.items():
        if info["status"] != "OK":
            summary_parts.append(f"  - {name}: {info['status']} ({info['age_hours']}h old, max {info['max_hours']}h)")

    # Dashboard
    summary_parts.append("\n### Dashboard Pages")
    d_ok = sum(1 for v in dashboard_results.values() if v["status"] == "OK")
    d_stale = sum(1 for v in dashboard_results.values() if v["status"] == "STALE")
    d_miss = sum(1 for v in dashboard_results.values() if v["status"] == "MISSING")
    summary_parts.append(f"OK: {d_ok}, Stale: {d_stale}, Missing: {d_miss}")
    for name, info in dashboard_results.items():
        if info["status"] != "OK":
            summary_parts.append(f"  - {name}: {info['status']}")

    # Chinese
    summary_parts.append("\n### LLM Output Quality")
    summary_parts.append(f"Clean notes: {chinese_results.get('clean', 0)}")
    summary_parts.append(f"Contaminated notes: {chinese_results.get('contaminated', 0)}")
    if chinese_results.get("files"):
        for cf in chinese_results["files"][:5]:
            summary_parts.append(f"  - {cf['name']}: {cf['chinese_count']} Chinese chars")

    # Jobs
    summary_parts.append("\n### Queue Runner Jobs")
    summary_parts.append(f"Total: {job_results.get('total', 0)}")
    summary_parts.append(f"Unique job names: {job_results.get('unique_names', 0)}")
    by_name = job_results.get('by_name', {})
    for name, cnt in sorted(by_name.items(), key=lambda x: -x[1])[:10]:
        summary_parts.append(f"  - {name}: {cnt} jobs")

    data_text = "\n".join(summary_parts)

    system_prompt = """You are the BC-250 system health advisor. Analyze the health data and provide a concise assessment.
Write ONLY in English. No Chinese characters.

Format your response as:
## Overall Status: [HEALTHY / DEGRADED / CRITICAL]
Brief one-line summary.

### Issues Found
- List each issue with severity and recommended action

### Recommendations
- Prioritized list of actions to improve system health

Keep it under 500 words. Be specific and actionable."""

    try:
        # Wait for GPU to be free (queue-runner may be using it)
        for attempt in range(6):  # Wait up to 60s
            try:
                ps_req = urllib.request.urlopen(f"{OLLAMA_URL}/api/ps", timeout=5)
                ps_data = json.loads(ps_req.read())
                loaded = ps_data.get("models", [])
                if not loaded:
                    break  # GPU is free
                # Model is loaded but may be idle - check if a generate is running
                # by trying a tiny prompt with short timeout
                log(f"  GPU occupied, waiting... (attempt {attempt+1}/6)")
                time.sleep(10)
            except Exception:
                break  # Can't check, just proceed

        payload = json.dumps({
            "model": OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "/nothink\n" + data_text},
            ],
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 1500, "num_ctx": 24576},
        }).encode()

        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )

        t0 = time.time()
        with urllib.request.urlopen(req, timeout=600) as resp:
            result = json.loads(resp.read())
            raw = result.get("message", {}).get("content", "")
            elapsed = time.time() - t0
            assessment = sanitize_llm_output(raw)
            log(f"  LLM assessment generated in {elapsed:.0f}s")
            return assessment
    except Exception as e:
        log(f"  LLM assessment failed: {e}")
        return None


def save_report(data_results, dashboard_results, chinese_results, job_results, service_results, assessment):
    """Save comprehensive health report."""
    report = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "services": service_results,
        "data_freshness": data_results,
        "dashboard_freshness": dashboard_results,
        "chinese_contamination": {
            "clean": chinese_results.get("clean", 0),
            "contaminated": chinese_results.get("contaminated", 0),
            "worst_files": chinese_results.get("files", [])[:10],
        },
        "queue_runner": job_results,
        "llm_assessment": assessment,
    }

    # Save to data dir
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    dt_str = datetime.now().strftime("%Y%m%d-%H%M")
    output_path = DATA_DIR / f"health-{dt_str}.json"
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    log(f"\n  Report saved: {output_path}")

    # Update symlink
    latest = DATA_DIR / "latest-health.json"
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    latest.symlink_to(output_path.name)

    # Also save as a think note for the dashboard
    if assessment:
        note = {
            "type": "system-health",
            "title": f"System Health Assessment — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "content": assessment,
            "generated": datetime.now().isoformat(timespec="seconds"),
            "model": OLLAMA_MODEL,
            "context": {
                "services_ok": all(
                    (v.get("status") if isinstance(v, dict) else v) in ("OK", "active")
                    for v in service_results.values()
                    if v != service_results.get("ollama_loaded")
                ),
                "data_ok_pct": round(
                    sum(1 for v in data_results.values() if v["status"] == "OK") / max(len(data_results), 1) * 100
                ),
                "chinese_contaminated": chinese_results.get("contaminated", 0),
            },
        }
        THINK_DIR.mkdir(parents=True, exist_ok=True)
        note_path = THINK_DIR / f"note-system-health-{datetime.now().strftime('%Y%m%d-%H%M')}.json"
        with open(note_path, "w") as f:
            json.dump(note, f, indent=2, ensure_ascii=False)
        log(f"  Think note saved: {note_path}")

    return report


def main():
    quick = "--quick" in sys.argv
    fix = "--fix-chinese" in sys.argv

    log("═══════════════════════════════════════════════════════")
    log("  BC-250 Extended Health Check — Phase 18")
    log("═══════════════════════════════════════════════════════\n")

    if fix:
        fixed = fix_chinese_notes()
        log(f"\nDone. Fixed {fixed} files.")
        return

    # Run all checks
    service_results = check_services()
    data_results = check_data_freshness()
    dashboard_results = check_dashboard_freshness()
    chinese_results = check_chinese_contamination()
    job_results = check_queue_runner_jobs()

    # Generate LLM assessment (unless quick mode)
    assessment = None
    if not quick:
        assessment = generate_llm_assessment(
            data_results, dashboard_results, chinese_results, job_results, service_results
        )

    # Save report
    report = save_report(
        data_results, dashboard_results, chinese_results, job_results, service_results, assessment
    )

    # Print summary
    log("\n═══ Summary ═══")
    ok = sum(1 for v in data_results.values() if v["status"] == "OK")
    total = len(data_results)
    log(f"  Data: {ok}/{total} OK")

    d_ok = sum(1 for v in dashboard_results.values() if v["status"] == "OK")
    d_total = len(dashboard_results)
    log(f"  Dashboard: {d_ok}/{d_total} OK")

    log(f"  Chinese contamination: {chinese_results.get('contaminated', 0)} files")
    log(f"  Jobs: {job_results.get('total', 0)} total, {job_results.get('unique_names', 0)} unique")

    if assessment:
        log("\n═══ LLM Assessment ═══")
        print(assessment)


if __name__ == "__main__":
    main()
