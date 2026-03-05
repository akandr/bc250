#!/usr/bin/env python3
"""
Daily Executive Summary — LLM-synthesized briefing for the dashboard.
=====================================================================
Reads the latest outputs from ALL analysis categories and asks the LLM
to produce a short executive briefing (~500 words) covering:
  - Key career opportunities detected
  - Company intelligence highlights
  - Kernel/repo notable changes
  - Market movements worth noting
  - Security/leak alerts
  - Home automation insights
  - Academic/patent discoveries

Runs near the end of each cycle so most analyses are fresh.

Output: /opt/netscan/data/summary/daily-YYYYMMDD.json
        /opt/netscan/data/summary/latest-summary.json

Usage:
    python3 daily-summary.py              # Generate summary
    python3 daily-summary.py --dry-run    # Show what would be read, don't call LLM
"""

import json
import os
import sys
import glob
import urllib.request
from datetime import datetime, date
from pathlib import Path

# ─── Configuration ──────────────────────────────────────────────────────────
DATA_DIR = Path("/opt/netscan/data")
SUMMARY_DIR = DATA_DIR / "summary"
OLLAMA_URL = "http://localhost:11434"
OLLAMA_CHAT = f"{OLLAMA_URL}/api/chat"
OLLAMA_MODEL = "huihui_ai/qwen3-abliterated:14b"

TODAY = date.today().isoformat().replace("-", "")
TODAY_LABEL = date.today().strftime("%B %d, %Y")


# ─── Data collection helpers ───────────────────────────────────────────────
def read_json(path):
    """Read a JSON file, return dict or None on error."""
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def find_latest(pattern, fallback=None):
    """Find the most recently modified file matching a glob pattern."""
    files = sorted(glob.glob(str(pattern)), key=os.path.getmtime, reverse=True)
    if files:
        return read_json(files[0])
    if fallback:
        return read_json(fallback)
    return None


def extract_career_highlights():
    """Extract latest career intelligence summary."""
    summary = read_json(DATA_DIR / "careers/think/latest-summary.json")
    if summary:
        bulletin = summary.get("bulletin", "")
        top = summary.get("top_opportunities", [])
        return {
            "source": "career-intelligence",
            "bulletin": bulletin[:2000] if bulletin else "",
            "top_opportunities": [
                {"company": o.get("company", ""), "role": o.get("title", o.get("role", "")),
                 "score": o.get("score", "")}
                for o in top[:5]
            ],
        }
    # Fallback: read individual company analyses
    files = sorted(glob.glob(str(DATA_DIR / f"careers/think/*-{TODAY}.json")),
                   key=os.path.getmtime, reverse=True)[:5]
    highlights = []
    for f in files:
        d = read_json(f)
        if d and d.get("bulletin"):
            highlights.append(d["bulletin"][:300])
    return {"source": "career-intelligence", "highlights": highlights} if highlights else None


def extract_company_highlights():
    """Extract latest company intelligence."""
    files = sorted(glob.glob(str(DATA_DIR / f"intel/think/*-{TODAY}.json")),
                   key=os.path.getmtime, reverse=True)[:8]
    highlights = []
    for f in files:
        d = read_json(f)
        if not d:
            continue
        company = d.get("company", os.path.basename(f).split("-")[0])
        bullet = d.get("bulletin", d.get("summary", ""))
        if bullet:
            highlights.append(f"**{company}**: {bullet[:300]}")
    return {"source": "company-intelligence", "highlights": highlights} if highlights else None


def extract_repo_highlights():
    """Extract latest repo/kernel analysis summary."""
    summary = read_json(DATA_DIR / "repos/think/latest-summary.json")
    if summary:
        return {
            "source": "repo-analysis",
            "bulletin": summary.get("bulletin", "")[:2000],
        }
    files = sorted(glob.glob(str(DATA_DIR / "repos/think/*-summary*.json")),
                   key=os.path.getmtime, reverse=True)[:1]
    if files:
        d = read_json(files[0])
        if d:
            return {"source": "repo-analysis", "bulletin": d.get("bulletin", "")[:2000]}
    return None


def extract_market_highlights():
    """Extract latest market analysis."""
    files = sorted(glob.glob(str(DATA_DIR / f"market/think/*-{TODAY}.json")),
                   key=os.path.getmtime, reverse=True)[:10]
    highlights = []
    for f in files:
        d = read_json(f)
        if not d:
            continue
        ticker = d.get("ticker", os.path.basename(f).split("-")[0])
        bullet = d.get("bulletin", d.get("summary", ""))
        if bullet:
            highlights.append(f"**{ticker}**: {bullet[:200]}")
    return {"source": "market-analysis", "highlights": highlights} if highlights else None


def extract_security_highlights():
    """Extract security/leak alerts."""
    parts = []
    # Leak monitor
    leak = read_json(DATA_DIR / "leaks/leak-intel.json")
    if leak:
        alerts = leak.get("alerts", leak.get("findings", []))
        if alerts:
            parts.append(f"Leak monitor: {len(alerts)} alert(s)")
            for a in alerts[:3]:
                if isinstance(a, dict):
                    parts.append(f"  - {a.get('summary', a.get('title', str(a)[:150]))}")
                else:
                    parts.append(f"  - {str(a)[:150]}")

    # System security think
    netsec = find_latest(DATA_DIR / f"think/system-netsec-{TODAY}.json")
    if netsec and netsec.get("bulletin"):
        parts.append(f"Network security: {netsec['bulletin'][:300]}")

    return {"source": "security", "highlights": parts} if parts else None


def extract_home_highlights():
    """Extract home automation insights."""
    ha = read_json(DATA_DIR / "correlate/latest-correlate.json")
    if ha and ha.get("bulletin"):
        return {"source": "home-automation", "bulletin": ha["bulletin"][:1000]}
    return None


def extract_lore_highlights():
    """Extract kernel mailing list digest highlights."""
    # Read from digest-feeds.json to find all feed dirs
    feeds_file = Path("/opt/netscan/digest-feeds.json")
    if not feeds_file.exists():
        feeds_file = DATA_DIR.parent / "digest-feeds.json"
    highlights = []
    if feeds_file.exists():
        feeds = read_json(feeds_file)
        if feeds:
            for feed in feeds.get("feeds", []):
                data_dir = feed.get("data_dir", "")
                if not data_dir:
                    continue
                digest = find_latest(DATA_DIR / data_dir / f"digest-{TODAY}.json")
                if digest and digest.get("bulletin"):
                    name = feed.get("name", data_dir)
                    highlights.append(f"**{name}**: {digest['bulletin'][:400]}")
    return {"source": "kernel-lists", "highlights": highlights} if highlights else None


def extract_academic_highlights():
    """Extract academic/patent discoveries."""
    files = sorted(glob.glob(str(DATA_DIR / f"academic/latest-*.json")),
                   key=os.path.getmtime, reverse=True)[:5]
    highlights = []
    for f in files:
        d = read_json(f)
        if not d:
            continue
        topic = d.get("topic", os.path.basename(f))
        new_count = len(d.get("new_items", d.get("results", [])))
        if new_count > 0:
            highlights.append(f"**{topic}**: {new_count} new item(s)")
    return {"source": "academic-research", "highlights": highlights} if highlights else None


def extract_events_highlights():
    """Extract upcoming events."""
    events = read_json(DATA_DIR / "events/latest-events.json")
    if events:
        upcoming = events.get("upcoming", events.get("events", []))[:5]
        if upcoming:
            items = []
            for e in upcoming:
                if isinstance(e, dict):
                    name = e.get("name", e.get("title", ""))
                    dt = e.get("date", e.get("start_date", ""))
                    items.append(f"{name} ({dt})" if dt else name)
            return {"source": "events", "upcoming": items}
    return None


def extract_radio_highlights():
    """Extract radio scan highlights."""
    radio = read_json(DATA_DIR / "radio/radio-latest.json")
    if radio and radio.get("bulletin"):
        return {"source": "radio", "bulletin": radio["bulletin"][:500]}
    return None


def extract_life_advisor():
    """Extract life advisor / cross-domain insights."""
    advisor = find_latest(DATA_DIR / f"think/life-advisor-{TODAY}.json")
    if advisor and advisor.get("bulletin"):
        return {"source": "life-advisor", "bulletin": advisor["bulletin"][:1000]}
    cross = find_latest(DATA_DIR / f"think/life-cross-{TODAY}.json")
    if cross and cross.get("bulletin"):
        return {"source": "life-cross-domain", "bulletin": cross["bulletin"][:1000]}
    return None


# ─── LLM call ──────────────────────────────────────────────────────────────
def call_llm(system_prompt, user_prompt, temperature=0.7, max_tokens=4096):
    """Call Ollama chat API."""
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
            "num_ctx": 16384,
        },
    }).encode()

    req = urllib.request.Request(OLLAMA_CHAT, data=payload,
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        result = json.loads(resp.read())
    return result.get("message", {}).get("content", "")


# ─── Main ───────────────────────────────────────────────────────────────────
def main():
    dry_run = "--dry-run" in sys.argv
    start_time = datetime.now()

    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)

    print(f"═══ Daily Executive Summary — {TODAY_LABEL} ═══")
    print(f"Collecting intelligence from all sources...")

    # Gather all sections
    collectors = [
        ("Career Intelligence", extract_career_highlights),
        ("Company Intelligence", extract_company_highlights),
        ("Repo & Kernel Analysis", extract_repo_highlights),
        ("Market Analysis", extract_market_highlights),
        ("Security & Leaks", extract_security_highlights),
        ("Home Automation", extract_home_highlights),
        ("Kernel Mailing Lists", extract_lore_highlights),
        ("Academic Research", extract_academic_highlights),
        ("Events", extract_events_highlights),
        ("Radio Scan", extract_radio_highlights),
        ("Life Advisor", extract_life_advisor),
    ]

    sections = {}
    for name, fn in collectors:
        try:
            data = fn()
            if data:
                sections[name] = data
                print(f"  ✓ {name}: collected")
            else:
                print(f"  · {name}: no data")
        except Exception as e:
            print(f"  ✗ {name}: error — {e}")

    if not sections:
        print("No data collected — nothing to summarize.")
        return

    # Build the context for LLM
    context_parts = []
    for name, data in sections.items():
        context_parts.append(f"\n## {name}")
        if "bulletin" in data:
            context_parts.append(data["bulletin"])
        if "highlights" in data:
            for h in data["highlights"]:
                context_parts.append(f"- {h}")
        if "top_opportunities" in data:
            for o in data["top_opportunities"]:
                context_parts.append(
                    f"- {o.get('company', '?')}: {o.get('role', '?')} "
                    f"(score: {o.get('score', '?')})")
        if "upcoming" in data:
            for e in data["upcoming"]:
                context_parts.append(f"- {e}")

    context_text = "\n".join(context_parts)
    print(f"\nContext: {len(context_text)} chars from {len(sections)} sources")

    if dry_run:
        print("\n--- DRY RUN: Context that would be sent to LLM ---")
        print(context_text[:3000])
        print(f"\n... ({len(context_text)} chars total)")
        return

    # LLM synthesis
    system_prompt = """You are a concise executive briefing analyst for a personal intelligence dashboard.
The user is a senior Linux kernel/camera/embedded engineer tracking career opportunities,
company intelligence, open-source repos, financial markets, academic papers, and home automation.

Write a SHORT executive briefing (300-500 words). Use these sections:
🔑 KEY HIGHLIGHTS (3-5 bullet points — the most actionable items across all domains)
📊 MARKET & CAREER (brief market moves + top career opportunities)
🔬 TECH & RESEARCH (kernel/repo changes + academic discoveries worth noting)
🏠 HOME & SECURITY (HA insights + any security alerts)

Rules:
- Be specific: mention company names, ticker symbols, kernel subsystems
- Prioritize actionable items over background info
- If a section has no notable items, skip it entirely
- Use plain text with emoji section headers, no markdown
- End with a one-line "Bottom line:" takeaway"""

    user_prompt = f"""Here is today's intelligence data ({TODAY_LABEL}).
Synthesize a brief executive dashboard summary:

{context_text}"""

    print("Calling LLM for synthesis...")
    try:
        summary_text = call_llm(system_prompt, user_prompt)
    except Exception as e:
        print(f"ERROR: LLM call failed — {e}")
        summary_text = None

    elapsed = (datetime.now() - start_time).total_seconds()

    if not summary_text:
        print("No summary generated.")
        return

    print(f"Summary generated: {len(summary_text)} chars in {elapsed:.0f}s")

    # Save
    result = {
        "date": TODAY_LABEL,
        "date_file": TODAY,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "elapsed_s": round(elapsed, 1),
        "model": OLLAMA_MODEL,
        "sources": list(sections.keys()),
        "source_count": len(sections),
        "context_chars": len(context_text),
        "summary": summary_text,
    }

    dated_path = SUMMARY_DIR / f"daily-{TODAY}.json"
    latest_path = SUMMARY_DIR / "latest-summary.json"

    for path in (dated_path, latest_path):
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(result, f, indent=2, default=str)
        os.replace(tmp, path)

    print(f"Saved: {dated_path}")
    print(f"Saved: {latest_path}")
    print(f"\n--- Summary Preview ---")
    print(summary_text[:1000])


if __name__ == "__main__":
    main()
