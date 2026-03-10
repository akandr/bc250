#!/usr/bin/env python3
"""life-think.py — Cross-domain intelligence synthesis and life advisor.

Group-think layers on top of all nightly batch data:

  --cross        Career + Company cross-domain strategic synthesis
  --advisor      Full life advisor: reads ALL data, generates actionable advice

Designed for heavy GPU usage with chain-of-thought reasoning and large context.
Runs after individual think summaries complete in the nightly batch.

Output:
  /opt/netscan/data/think/life-cross-YYYYMMDD.json
  /opt/netscan/data/think/life-advisor-YYYYMMDD.json
"""

import argparse
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
from glob import glob
from llm_sanitize import sanitize_llm_output

# ── Config ─────────────────────────────────────────────────────────────────
OLLAMA_URL = "http://localhost:11434"
OLLAMA_CHAT = f"{OLLAMA_URL}/api/chat"
OLLAMA_MODEL = "qwen3:14b"

DATA_DIR = Path("/opt/netscan/data")
THINK_DIR = DATA_DIR / "think"
PROFILE_FILE = Path("/opt/netscan/profile.json")

# Data source paths
CAREER_THINK_DIR = DATA_DIR / "careers" / "think"
COMPANY_THINK_DIR = DATA_DIR / "intel" / "think"
CAREER_SCAN_DIR = DATA_DIR / "career"
MARKET_DIR = DATA_DIR / "market"
SALARY_DIR = DATA_DIR / "salary"
ACADEMIC_DIR = DATA_DIR / "academic"
CAR_TRACKER_DIR = DATA_DIR / "car-tracker"
CORRELATE_DIR = DATA_DIR / "correlate"
EVENTS_DIR = DATA_DIR / "events"
RADIO_DIR = DATA_DIR / "radio"
CITY_DIR = DATA_DIR / "city"
LEAKS_DIR = DATA_DIR / "leaks"
REPOS_DIR = DATA_DIR / "repos"
PATENTS_DIR = DATA_DIR / "patents"

TODAY = datetime.now().strftime("%Y%m%d")
TODAY_ISO = datetime.now().strftime("%Y-%m-%d")

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"


# ── Helpers ────────────────────────────────────────────────────────────────

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_json(path):
    """Load JSON file, return dict or None."""
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


def load_profile():
    return load_json(PROFILE_FILE) or {}


def truncate(text, max_chars=1500):
    """Truncate text preserving complete lines."""
    if not text or len(text) <= max_chars:
        return text or ""
    cut = text[:max_chars].rsplit("\n", 1)[0]
    return cut + "\n[... truncated]"


def call_ollama(system_prompt, user_prompt, temperature=0.3, max_tokens=4000, think=True):
    """Call Ollama with chain-of-thought support for deep analysis."""
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

    # Use /nothink or /think prefix based on reasoning mode
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
            "num_ctx": 24576,  # Max context for deep analysis
        },
    }).encode()

    req = urllib.request.Request(OLLAMA_CHAT, data=payload, headers={
        "Content-Type": "application/json",
    })

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:  # 15 min timeout
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


def web_fetch(url, timeout=30):
    """Fetch a web page. Returns text or None."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            try:
                return data.decode("utf-8")
            except:
                return data.decode("latin-1")
    except Exception as e:
        log(f"  Web fetch failed ({url[:60]}): {e}")
        return None


def web_search_ddg(query, max_results=5):
    """Quick DuckDuckGo HTML search, return list of {title, url, snippet}."""
    import urllib.parse
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
    html = web_fetch(url, timeout=20)
    if not html:
        return []

    results = []
    # Parse result blocks
    for m in re.finditer(r'<a\s+rel="nofollow"\s+class="result__a"\s+href="([^"]+)"[^>]*>(.+?)</a>', html):
        link = m.group(1)
        title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        # Get snippet
        pos = m.end()
        snip_m = re.search(r'class="result__snippet"[^>]*>(.+?)</(?:a|td|div)', html[pos:pos+1000])
        snippet = re.sub(r'<[^>]+>', '', snip_m.group(1)).strip() if snip_m else ""
        # Clean DuckDuckGo redirect URL
        if "uddg=" in link:
            real = re.search(r'uddg=([^&]+)', link)
            if real:
                link = urllib.parse.unquote(real.group(1))
        results.append({"title": title, "url": link, "snippet": snippet})
        if len(results) >= max_results:
            break

    return results


def shell_exec(cmd, timeout=60):
    """Execute a shell command, return stdout."""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"


# ── Data collectors ────────────────────────────────────────────────────────

def collect_career_data():
    """Collect latest career intelligence summaries."""
    data = {}

    # Career think summary
    summary = load_json(CAREER_THINK_DIR / "latest-summary.json")
    if summary:
        data["career_summary"] = truncate(summary.get("summary", ""), 2000)
        data["companies_analyzed"] = summary.get("companies", [])

    # Latest career scan results
    scan = load_json(CAREER_SCAN_DIR / "latest-scan.json")
    if scan:
        jobs = scan.get("jobs", [])
        hot = [j for j in jobs if j.get("match_score", 0) >= 70]
        good = [j for j in jobs if 40 <= j.get("match_score", 0) < 70]
        data["scan_summary"] = scan.get("summary", "")[:800]
        data["total_jobs"] = len(jobs)
        data["hot_jobs"] = len(hot)
        data["good_jobs"] = len(good)
        data["top_positions"] = [
            f"{j['title']} @ {j.get('company','?')} (score: {j.get('match_score',0)}%, "
            f"{'remote' if j.get('remote_compatible') else 'onsite'})"
            for j in sorted(hot, key=lambda x: -x.get("match_score", 0))[:10]
        ]

    # Recent per-company analyses (today's)
    company_analyses = []
    for fp in sorted(glob(str(CAREER_THINK_DIR / f"*-{TODAY}.json")))[-20:]:
        a = load_json(fp)
        if a and a.get("meta", {}).get("type") != "summary":
            name = a.get("meta", {}).get("name", "?")
            analysis = a.get("analysis", "")[:300]
            company_analyses.append(f"  {name}: {analysis}")
    data["per_company_excerpts"] = company_analyses[:15]

    # Salary data
    salary = load_json(SALARY_DIR / "latest-salary.json")
    if salary:
        data["salary_summary"] = truncate(salary.get("summary", salary.get("analysis", "")), 500)

    return data


def collect_company_data():
    """Collect latest company intelligence summaries."""
    data = {}

    # Company think summary
    summary = load_json(COMPANY_THINK_DIR / "latest-summary.json")
    if summary:
        data["intel_summary"] = truncate(summary.get("summary", ""), 2000)
        data["companies_analyzed"] = summary.get("companies", [])

    # Recent per-company analyses
    company_analyses = []
    for fp in sorted(glob(str(COMPANY_THINK_DIR / f"*-{TODAY}.json")))[-20:]:
        a = load_json(fp)
        if a and a.get("meta", {}).get("type") != "summary":
            name = a.get("meta", {}).get("name", "?")
            analysis = a.get("analysis", "")[:300]
            company_analyses.append(f"  {name}: {analysis}")
    data["per_company_excerpts"] = company_analyses[:15]

    return data


def collect_market_data():
    """Collect latest market/investment data."""
    data = {}
    market = load_json(MARKET_DIR / "latest-market.json")
    if market:
        data["market_summary"] = truncate(market.get("analysis", market.get("summary", "")), 800)
        positions = market.get("positions", [])
        if positions:
            data["portfolio_brief"] = [
                f"  {p.get('symbol','?')}: {p.get('change_pct', 0):+.1f}% ({p.get('sentiment','')})"
                for p in positions[:10]
            ]
    return data


def collect_home_data():
    """Collect HA home automation data."""
    data = {}
    correlate = load_json(CORRELATE_DIR / "latest-correlate.json")
    if correlate:
        data["home_analysis"] = truncate(correlate.get("llm_analysis", ""), 600)
        room_usage = correlate.get("room_usage", {})
        if room_usage:
            data["room_usage"] = {
                room: f"lit {u.get('lit_hours',0):.1f}h, motion {u.get('motion_events',0)} events"
                for room, u in list(room_usage.items())[:8]
            }
    return data


def collect_car_data():
    """Collect car tracker data."""
    data = {}
    car = load_json(CAR_TRACKER_DIR / "latest-car-tracker.json")
    if car:
        status = car.get("current_status", {})
        mileage = car.get("mileage", {})
        trips = car.get("trips", [])
        data["car_status"] = f"{'Moving' if status.get('is_moving') else 'Parked'} at {status.get('location','?')}"
        if status.get("parked_duration_h"):
            data["car_status"] += f", parked {status['parked_duration_h']:.1f}h"
        data["car_mileage_avg"] = f"{mileage.get('avg_km', 0):.1f} km/day" if mileage else "?"
        data["car_trips_recent"] = len(trips)
        data["car_analysis"] = truncate(car.get("llm_analysis", ""), 500)
    return data


def collect_tech_data():
    """Collect repo/tech trend data."""
    data = {}
    # Repo think notes
    repo_think = load_json(REPOS_DIR / "think" / "latest-summary.json") if (REPOS_DIR / "think").exists() else None
    if repo_think:
        data["repo_summary"] = truncate(repo_think.get("summary", ""), 600)

    # Radio/kernel lists
    for name in ["radio-latest.json"]:
        radio = load_json(RADIO_DIR / name)
        if radio:
            data["radio_summary"] = truncate(radio.get("analysis", radio.get("summary", "")), 400)

    # Academic
    for fp in sorted(glob(str(ACADEMIC_DIR / "latest-*.json")))[:5]:
        academic = load_json(fp)
        if academic:
            data.setdefault("academic_highlights", [])
            for item in academic.get("papers", academic.get("items", []))[:3]:
                title = item.get("title", "?")
                data["academic_highlights"].append(title[:100])

    # Patents
    patents = load_json(PATENTS_DIR / "latest-patents.json") if PATENTS_DIR.exists() else None
    if patents:
        data["patent_highlights"] = [
            p.get("title", "?")[:80] for p in patents.get("patents", [])[:5]
        ]

    return data


def collect_events_data():
    """Collect events and city data."""
    data = {}
    events = load_json(EVENTS_DIR / "latest-events.json")
    if events:
        upcoming = events.get("events", [])[:5]
        data["upcoming_events"] = [
            f"  {ev.get('date','?')}: {ev.get('title','?')[:60]}"
            for ev in upcoming
        ]

    city = load_json(CITY_DIR / "latest-city.json")
    if city:
        data["city_summary"] = truncate(city.get("llm_analysis", city.get("analysis", "")), 400)

    return data


def collect_think_notes():
    """Collect recent think notes from data/think/."""
    notes = []
    for fp in sorted(glob(str(THINK_DIR / "note-*.json")))[-10:]:
        n = load_json(fp)
        if n:
            notes.append({
                "type": n.get("type", "?"),
                "title": n.get("title", os.path.basename(fp)),
                "summary": truncate(n.get("content", n.get("summary", "")), 300),
            })
    return notes


def collect_leaks_data():
    """Collect security leak intel."""
    data = {}
    for fp in sorted(glob(str(LEAKS_DIR / "leak-*.json")))[:3]:
        leak = load_json(fp)
        if leak:
            data.setdefault("leaks", [])
            for entry in leak.get("findings", leak.get("results", []))[:3]:
                data["leaks"].append(truncate(str(entry), 200))
    return data


# ══════════════════════════════════════════════════════════════════════════
# MODE: --cross — Career + Company cross-domain synthesis
# ══════════════════════════════════════════════════════════════════════════

def run_cross():
    """Cross-domain synthesis: combine career intelligence with company intel."""
    t_start = time.time()

    log(f"{'='*60}")
    log(f"CROSS-DOMAIN SYNTHESIS — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    log(f"{'='*60}")

    THINK_DIR.mkdir(parents=True, exist_ok=True)
    profile = load_profile()

    # Collect data
    log("Collecting career intelligence...")
    career = collect_career_data()
    log(f"  Career: {career.get('total_jobs', 0)} jobs, {career.get('hot_jobs', 0)} hot, "
        f"{len(career.get('per_company_excerpts', []))} company analyses")

    log("Collecting company intelligence...")
    company = collect_company_data()
    log(f"  Company: {len(company.get('per_company_excerpts', []))} company analyses")

    log("Collecting salary data...")
    salary = collect_career_data().get("salary_summary", "")

    if not career.get("career_summary") and not company.get("intel_summary"):
        log("No career or company summaries available. Need at least one. Aborting.")
        return

    # Build comprehensive prompt
    system = """You are the Chief Strategic Intelligence Officer for a senior embedded Linux engineer.
Your role: synthesize career opportunities with corporate intelligence to produce
actionable strategic career advice. Think deeply about connections between company
health, hiring patterns, and career opportunities.

IMPORTANT: Write ONLY in English. Be analytical, specific, and actionable.
The reader is a senior engineer (15+ years, kernel/camera/BSP/multimedia)
based in Łódź, Poland, currently exploring new opportunities."""

    prompt_parts = [f"=== CROSS-DOMAIN INTELLIGENCE SYNTHESIS — {TODAY_ISO} ===\n"]

    if career.get("career_summary"):
        prompt_parts.append(f"## CAREER INTELLIGENCE SUMMARY\n{career['career_summary']}\n")
    if career.get("top_positions"):
        prompt_parts.append("## TOP JOB MATCHES\n" + "\n".join(career["top_positions"]) + "\n")
    if career.get("per_company_excerpts"):
        prompt_parts.append("## PER-COMPANY CAREER ANALYSIS EXCERPTS\n" + "\n".join(career["per_company_excerpts"][:10]) + "\n")

    if company.get("intel_summary"):
        prompt_parts.append(f"\n## COMPANY INTELLIGENCE SUMMARY\n{company['intel_summary']}\n")
    if company.get("per_company_excerpts"):
        prompt_parts.append("## PER-COMPANY INTEL EXCERPTS\n" + "\n".join(company["per_company_excerpts"][:10]) + "\n")

    if salary:
        prompt_parts.append(f"\n## SALARY INTELLIGENCE\n{salary}\n")

    prompt_parts.append(f"""
## PROFILE CONTEXT
Role: {profile.get('role', 'Embedded Linux engineer')}
Location: Łódź, Poland
Key interests: {', '.join(profile.get('interests', [])[:5])}

─────────────────────────────────────────────────────
SYNTHESIZE a cross-domain strategic career brief:

1. OPPORTUNITY-RISK MATRIX
   For each top company hiring: how are they doing financially/strategically?
   Which companies are GROWING and hiring vs STRUGGLING and hiring (desperation)?
   Red flags: companies hiring because of high turnover vs genuine expansion.

2. STRATEGIC TIMING ANALYSIS
   Which opportunities are time-sensitive (new teams, new products launching)?
   Which are evergreen (backfill, ongoing programs)?
   Market cycle position: is this a good time to move, or better to wait?

3. COMPENSATION INTELLIGENCE
   Cross-reference salary data with company financial health.
   Which companies can afford to pay top-of-market?
   Stock/equity considerations for companies with strong/weak trajectories.

4. HIDDEN OPPORTUNITIES
   Companies with strong financials but few visible openings — probe deeper?
   Companies where internal team signals suggest upcoming hiring waves.
   Startups vs established companies: risk-adjusted opportunity ranking.

5. NETWORK & APPROACH STRATEGY
   For top 3 opportunities: how to approach? Direct application vs network referral?
   Which open-source communities overlap with target companies?
   Conference/meetup targeting for networking.

6. IMMEDIATE ACTIONS (top 3-5 specific, dated recommendations)
   What to do this week. Be specific: apply to X, reach out to Y, learn Z.

Target: 800-1000 words. Chain-of-thought reasoning. English only.""")

    user = "\n".join(prompt_parts)

    log(f"\n── Running cross-domain LLM analysis (chain-of-thought) ──")
    log(f"  Prompt: {len(user)} chars")
    analysis = call_ollama(system, user, temperature=0.4, max_tokens=5000, think=True)

    if not analysis:
        log("Cross-domain LLM analysis failed")
        return

    elapsed = time.time() - t_start

    output = {
        "type": "life-think-cross",
        "generated": datetime.now().isoformat(),
        "date": TODAY,
        "analysis": analysis,
        "sources": {
            "career_summary": bool(career.get("career_summary")),
            "company_summary": bool(company.get("intel_summary")),
            "salary_data": bool(salary),
            "career_companies": career.get("companies_analyzed", []),
            "company_companies": company.get("companies_analyzed", []),
            "hot_jobs": career.get("hot_jobs", 0),
        },
        "meta": {
            "duration_s": round(elapsed, 1),
            "prompt_chars": len(user),
            "analysis_chars": len(analysis),
        },
    }

    out_file = THINK_DIR / f"life-cross-{TODAY}.json"
    save_json(out_file, output)
    log(f"\nSaved: {out_file.name} ({out_file.stat().st_size:,} bytes)")

    latest = THINK_DIR / "latest-life-cross.json"
    latest.unlink(missing_ok=True)
    latest.symlink_to(out_file.name)
    log(f"Symlink: {latest.name}")

    # Also save as think note for dashboard
    note = {
        "type": "life-cross",
        "title": "Cross-Domain Career + Company Intelligence",
        "generated": output["generated"],
        "content": analysis,
        "summary": f"Cross-domain synthesis: {career.get('hot_jobs', 0)} hot jobs, "
                   f"{len(career.get('companies_analyzed', []))} career + "
                   f"{len(company.get('companies_analyzed', []))} company analyses combined",
    }
    note_path = THINK_DIR / f"note-life-cross-{TODAY}.json"
    save_json(note_path, note)

    log(f"Done in {elapsed:.0f}s")
    return output


# ══════════════════════════════════════════════════════════════════════════
# MODE: --advisor — Full life advisor mega-think
# ══════════════════════════════════════════════════════════════════════════

def run_advisor():
    """Full life advisor: reads ALL data, generates comprehensive life advice."""
    t_start = time.time()

    log(f"{'='*60}")
    log(f"LIFE ADVISOR — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    log(f"{'='*60}")

    THINK_DIR.mkdir(parents=True, exist_ok=True)
    profile = load_profile()

    # ── Phase 1: Collect ALL data sources ──
    log("Phase 1: Collecting all data sources...")

    log("  Career intelligence...")
    career = collect_career_data()

    log("  Company intelligence...")
    company = collect_company_data()

    log("  Market & investments...")
    market = collect_market_data()

    log("  Home automation...")
    home = collect_home_data()

    log("  Car tracker...")
    car = collect_car_data()

    log("  Tech trends (repos, radio, academic)...")
    tech = collect_tech_data()

    log("  Events & city...")
    events = collect_events_data()

    log("  Think notes...")
    think_notes = collect_think_notes()

    log("  Leaks & security...")
    leaks = collect_leaks_data()

    # Load cross-domain synthesis if available
    cross = load_json(THINK_DIR / f"life-cross-{TODAY}.json")
    cross_analysis = cross.get("analysis", "") if cross else ""

    sources_count = sum([
        bool(career.get("career_summary")),
        bool(company.get("intel_summary")),
        bool(market.get("market_summary")),
        bool(home.get("home_analysis")),
        bool(car.get("car_status")),
        bool(tech),
        bool(events),
        bool(think_notes),
        bool(cross_analysis),
    ])
    log(f"  Collected {sources_count} data sources")

    # ── Phase 2: Proactive web research ──
    log("\nPhase 2: Proactive web research...")

    web_results = {}

    # Search for recent tech news relevant to interests
    search_queries = [
        "embedded Linux camera driver jobs Europe 2026",
        "V4L2 libcamera MIPI CSI latest news",
        f"Łódź Poland tech industry news {TODAY_ISO[:7]}",
    ]

    # Add company-specific queries for hot job targets
    if career.get("top_positions"):
        # Extract company names from top positions
        for pos in career["top_positions"][:3]:
            company_name = pos.split("@")[1].split("(")[0].strip() if "@" in pos else ""
            if company_name:
                search_queries.append(f"{company_name} embedded Linux engineer hiring 2026")

    for query in search_queries[:5]:
        log(f"  Searching: {query[:60]}...")
        results = web_search_ddg(query, max_results=3)
        if results:
            web_results[query] = results
            log(f"    → {len(results)} results")
        time.sleep(1)  # Rate limit

    # ── Phase 3: System health check ──
    log("\nPhase 3: System health...")
    system_info = {}
    system_info["disk"] = shell_exec("df -h /opt/netscan | tail -1")
    system_info["uptime"] = shell_exec("uptime")
    system_info["gpu_mem"] = shell_exec("cat /sys/class/drm/card1/device/mem_info_vram_used 2>/dev/null || echo 'N/A'")
    system_info["cron_jobs"] = shell_exec("python3 -c \"import json; d=json.load(open('/home/akandr/.openclaw/cron/jobs.json')); print(len([j for j in d['jobs'] if j.get('enabled',True)]))\" 2>/dev/null")

    # ── Phase 4: Build mega-prompt and run LLM ──
    log("\nPhase 4: Building life advisor prompt...")

    system_prompt = """You are ClawdBot, a personal AI life advisor and strategic intelligence system.
You have deep access to your human's career data, company intelligence, market investments,
home automation, vehicle tracking, tech research, and neighborhood information.

Your mission: synthesize ALL available data into actionable life improvement advice.
Think holistically — career, health, finances, learning, networking, home, transportation.

IMPORTANT RULES:
- Write ONLY in English
- Be specific and actionable — no vague advice
- Provide dated, concrete action items
- Use data to back up recommendations
- Think about connections between different life domains
- Suggest web searches or investigations for information gaps
- Be direct and honest — flag risks and opportunities clearly

Your human: Senior embedded Linux engineer (15+ years), based in Łódź, Poland.
Interests: V4L2, libcamera, camera pipelines, ARM SoC, GStreamer, AMD GPU, RISC-V.
Active job seeker monitoring 28+ companies in embedded/semiconductor space."""

    prompt_sections = [f"=== CLAWDBOT LIFE ADVISOR — {TODAY_ISO} ===\n"]
    prompt_sections.append(f"Today is {datetime.now().strftime('%A, %B %d, %Y')}.\n")

    # Cross-domain synthesis (if available)
    if cross_analysis:
        prompt_sections.append(f"## CROSS-DOMAIN CAREER+COMPANY SYNTHESIS (already computed)\n{truncate(cross_analysis, 1500)}\n")

    # Career
    if career.get("career_summary"):
        prompt_sections.append(f"## CAREER MARKET INTELLIGENCE\n{truncate(career['career_summary'], 1000)}")
    if career.get("top_positions"):
        prompt_sections.append("Top matches:\n" + "\n".join(career["top_positions"][:5]))

    # Company
    if company.get("intel_summary"):
        prompt_sections.append(f"\n## CORPORATE INTELLIGENCE\n{truncate(company['intel_summary'], 1000)}")

    # Market
    if market.get("market_summary"):
        prompt_sections.append(f"\n## MARKET & INVESTMENTS\n{truncate(market['market_summary'], 600)}")
    if market.get("portfolio_brief"):
        prompt_sections.append("Portfolio:\n" + "\n".join(market["portfolio_brief"]))

    # Home
    if home.get("home_analysis"):
        prompt_sections.append(f"\n## HOME AUTOMATION INSIGHTS\n{truncate(home['home_analysis'], 400)}")

    # Car
    if car.get("car_status"):
        prompt_sections.append(f"\n## VEHICLE TRACKING\nStatus: {car['car_status']}")
        prompt_sections.append(f"Avg mileage: {car.get('car_mileage_avg', '?')}")
        prompt_sections.append(f"Recent trips: {car.get('car_trips_recent', 0)}")
        if car.get("car_analysis"):
            prompt_sections.append(truncate(car["car_analysis"], 300))

    # Tech trends
    if tech.get("repo_summary"):
        prompt_sections.append(f"\n## TECH TRENDS (repos)\n{truncate(tech['repo_summary'], 400)}")
    if tech.get("academic_highlights"):
        prompt_sections.append("Recent papers:\n  " + "\n  ".join(tech["academic_highlights"][:5]))
    if tech.get("patent_highlights"):
        prompt_sections.append("Recent patents:\n  " + "\n  ".join(tech["patent_highlights"][:3]))

    # Events & city
    if events.get("upcoming_events"):
        prompt_sections.append(f"\n## UPCOMING EVENTS\n" + "\n".join(events["upcoming_events"]))
    if events.get("city_summary"):
        prompt_sections.append(f"\n## CITY/NEIGHBORHOOD\n{truncate(events['city_summary'], 300)}")

    # Think notes
    if think_notes:
        prompt_sections.append(f"\n## RECENT THINK NOTES ({len(think_notes)} notes)")
        for n in think_notes[-5:]:
            prompt_sections.append(f"  [{n['type']}] {n.get('title','')[:50]}: {n['summary'][:150]}")

    # Web research
    if web_results:
        prompt_sections.append(f"\n## FRESH WEB RESEARCH ({len(web_results)} queries)")
        for query, results in web_results.items():
            prompt_sections.append(f"  Query: {query}")
            for r in results[:2]:
                prompt_sections.append(f"    → {r['title'][:60]} | {r['snippet'][:100]}")

    # System health
    prompt_sections.append(f"\n## SYSTEM HEALTH")
    prompt_sections.append(f"  Disk: {system_info.get('disk', '?')}")
    prompt_sections.append(f"  Uptime: {system_info.get('uptime', '?')}")
    prompt_sections.append(f"  Active cron jobs: {system_info.get('cron_jobs', '?')}")

    # Security
    if leaks.get("leaks"):
        prompt_sections.append(f"\n## SECURITY ALERTS\n" + "\n".join(leaks["leaks"][:3]))

    prompt_sections.append(f"""
─────────────────────────────────────────────────────
GENERATE A COMPREHENSIVE LIFE ADVISOR BRIEFING:

1. 🎯 CAREER & PROFESSIONAL
   Top 3 immediate career actions with deadlines.
   Skills to invest in this week/month.
   Networking targets and approaches.
   Application strategy: where to apply, how to customize.

2. 💰 FINANCIAL & INVESTMENTS
   Portfolio observations. Any positions to review?
   Salary negotiation insights from career data.
   Financial moves to consider.

3. 🏠 HOME & LIFESTYLE
   Home automation insights — any patterns worth addressing?
   Energy efficiency or comfort improvements.
   Vehicle usage patterns — any optimization opportunities?

4. 📚 LEARNING & GROWTH
   Technical skills trending up (from repo/academic/patent data).
   Online courses, conferences, or certifications to consider.
   Open-source contribution opportunities aligned with career goals.

5. 🌐 NETWORK & COMMUNITY
   Events to attend (from events data).
   Open-source communities to engage with.
   Professional connections to cultivate.

6. ⚠️ RISKS & WATCHOUTS
   Security alerts requiring attention.
   Market risks to investments.
   Career risks (company stability, industry shifts).

7. 📋 THIS WEEK'S ACTION PLAN
   Day-by-day specific actions for the coming week.
   Priority-ordered, with estimated time investment each.

Be specific, data-driven, and actionable. 1000-1500 words. English only.""")

    user_prompt = "\n".join(prompt_sections)
    log(f"  Prompt size: {len(user_prompt)} chars")

    log(f"\n── Running life advisor LLM analysis (deep chain-of-thought) ──")
    analysis = call_ollama(system_prompt, user_prompt, temperature=0.5, max_tokens=6000, think=True)

    if not analysis:
        log("Life advisor LLM analysis failed")
        return

    # ── Phase 5: Follow-up research based on LLM suggestions ──
    log("\nPhase 5: Following up on LLM suggestions...")
    followup_data = []

    # Extract any URLs or search queries the LLM suggested
    url_matches = re.findall(r'https?://[^\s<>"\']+', analysis)
    search_suggestions = re.findall(r'(?:search for|look up|google|investigate)[:\s]+"?([^".\n]+)"?', analysis, re.IGNORECASE)

    for url in url_matches[:3]:
        log(f"  Fetching suggested URL: {url[:60]}...")
        content = web_fetch(url)
        if content:
            # Extract text summary
            text = re.sub(r'<[^>]+>', ' ', content)
            text = re.sub(r'\s+', ' ', text).strip()[:500]
            followup_data.append({"type": "url_fetch", "url": url, "excerpt": text})

    for query in search_suggestions[:2]:
        log(f"  Following up search: {query[:50]}...")
        results = web_search_ddg(query.strip(), max_results=3)
        if results:
            followup_data.append({"type": "search", "query": query, "results": results})

    elapsed = time.time() - t_start

    output = {
        "type": "life-think-advisor",
        "generated": datetime.now().isoformat(),
        "date": TODAY,
        "analysis": analysis,
        "web_research": web_results,
        "followup_research": followup_data,
        "system_health": system_info,
        "sources_used": {
            "career": bool(career.get("career_summary")),
            "company": bool(company.get("intel_summary")),
            "market": bool(market.get("market_summary")),
            "home": bool(home.get("home_analysis")),
            "car": bool(car.get("car_status")),
            "tech": bool(tech),
            "events": bool(events.get("upcoming_events")),
            "think_notes": len(think_notes),
            "web_searches": len(web_results),
            "cross_synthesis": bool(cross_analysis),
        },
        "meta": {
            "duration_s": round(elapsed, 1),
            "prompt_chars": len(user_prompt),
            "analysis_chars": len(analysis),
            "followup_count": len(followup_data),
        },
    }

    out_file = THINK_DIR / f"life-advisor-{TODAY}.json"
    save_json(out_file, output)
    log(f"\nSaved: {out_file.name} ({out_file.stat().st_size:,} bytes)")

    latest = THINK_DIR / "latest-life-advisor.json"
    latest.unlink(missing_ok=True)
    latest.symlink_to(out_file.name)
    log(f"Symlink: {latest.name}")

    # Save as think note
    note = {
        "type": "life-advisor",
        "title": "ClawdBot Life Advisor Daily Briefing",
        "generated": output["generated"],
        "content": analysis,
        "summary": f"Life advisor: {sources_count} data sources, "
                   f"{len(web_results)} web searches, "
                   f"{len(followup_data)} follow-ups",
    }
    note_path = THINK_DIR / f"note-life-advisor-{TODAY}.json"
    save_json(note_path, note)

    # Regenerate dashboard
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
        description="Cross-domain intelligence synthesis and life advisor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  life-think.py --cross      # Career + Company strategic synthesis
  life-think.py --advisor    # Full life advisor mega-think (reads ALL data)
  life-think.py --all        # Run both: cross first, then advisor
        """
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--cross', '-c', action='store_true',
                       help="Cross-domain career + company synthesis")
    group.add_argument('--advisor', '-a', action='store_true',
                       help="Full life advisor (reads ALL data sources)")
    group.add_argument('--all', action='store_true',
                       help="Run cross synthesis first, then life advisor")

    args = parser.parse_args()

    if args.cross:
        run_cross()
    elif args.advisor:
        run_advisor()
    elif args.all:
        run_cross()
        log("\n" + "─" * 60 + "\n")
        run_advisor()


if __name__ == "__main__":
    main()
