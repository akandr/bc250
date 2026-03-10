#!/usr/bin/env python3
"""career-think.py — Deep per-company career intelligence.

Runs extended LLM chain-of-thought analysis on one company's career
signals. Reads latest career-scan data + fetches fresh career page,
then produces deep strategic hiring analysis.

Usage:
  career-think.py --company nvidia   # Deep career analysis for NVIDIA
  career-think.py --summary          # Aggregate all daily analyses
  career-think.py --list             # Show available companies

Output: /opt/netscan/data/careers/think/<company>-YYYYMMDD.json
"""

import argparse, html, json, os, re, sys, time, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path
from llm_sanitize import sanitize_llm_output

# ── Config ─────────────────────────────────────────────────────────────────

OLLAMA_URL   = "http://localhost:11434"
OLLAMA_CHAT  = f"{OLLAMA_URL}/api/chat"
OLLAMA_MODEL = "qwen3:14b"

THINK_DIR    = Path("/opt/netscan/data/careers/think")
CAREERS_DIR  = Path("/opt/netscan/data/careers")
PROFILE_FILE = Path("/opt/netscan/profile.json")

SIGNAL_RPC   = "http://127.0.0.1:8080/api/v1/rpc"
SIGNAL_FROM  = "+<BOT_PHONE>"
SIGNAL_TO    = "+<OWNER_PHONE>"

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

# ── Focus areas for per-company career sub-topic analysis ──────────────────
CAREER_FOCUS_AREAS = {
    "skills": {
        "label": "Technology Stack & Skills Analysis",
        "sections": """
1. TECHNOLOGY STACK DEEP-DIVE
   What specific technologies appear in job listings? Kernel versions, GPU APIs,
   camera frameworks, build systems, CI/CD tools, programming languages.
   Compare to industry standard stacks — is {name} ahead or behind?
   Open source vs proprietary tool preferences.

2. SKILLS DEMAND ANALYSIS
   Top 10 most-requested technical skills across all listings.
   Emerging skills: what's NEW in listings vs 6 months ago?
   Skills gap analysis: what does the developer need to learn?
   Certification or education requirements — trends changing?

3. TECHNOLOGY ARCHITECTURE SIGNALS
   What do job descriptions reveal about {name}'s internal architecture?
   Platform transitions visible through hiring (e.g., moving to new SOC)?
   Internal tool development vs external framework adoption.
   DevOps/infrastructure evolution signals.

4. CAREER DEVELOPMENT IMPLICATIONS
   Which skills to invest in for maximum career leverage at {name}?
   Cross-functional skills valued (e.g., HW/SW boundary, system design)?
   Leadership vs IC balance in technical roles.
   Remote/distributed work implications for skill requirements.
""",
    },
    "opportunity": {
        "label": "Opportunity & Fit Assessment",
        "sections": """
1. BEST-FIT POSITIONS (detailed)
   For an experienced embedded Linux / camera / kernel engineer in Łódź, Poland:
   Rank ALL relevant positions by fit score. For each:
   - Title, location, team (if identifiable)
   - Match percentage based on skills overlap
   - What makes this position attractive or concerning
   - Key requirements the developer meets vs gaps

2. TEAM & ORGANIZATION SIGNALS
   Are they building new teams or backfilling existing ones?
   Team maturity indicators: greenfield vs established product.
   Manager vs IC roles ratio — which level is hiring most?
   Cross-team collaboration patterns visible from job descriptions.

3. COMPENSATION & NEGOTIATION INTELLIGENCE
   Salary range hints from job descriptions or Glassdoor data.
   Benefits and perks mentioned (equity, remote, relocation).
   Negotiation leverage: how hard is the role to fill?
   Counter-offer risk factors.

4. TIMING & APPLICATION STRATEGY
   Is NOW the right time to apply? Why or why not?
   Application tips: what to emphasize, what to demonstrate.
   Referral paths: any known connections or communities?
   Interview preparation: likely technical focus areas.
   Red flags to watch for during interview process.
""",
    },
    "culture": {
        "label": "Engineering Culture & Work Environment",
        "sections": """
1. ENGINEERING CULTURE DEEP-DIVE
   Development methodology: agile, waterfall, hybrid? Sprint cadence?
   Code review culture: strict, collaborative, rubber-stamp?
   Open source contributions and upstream engagement from employees.
   Technical debt management: do engineers get time for refactoring?
   Innovation culture: hackathons, 20% time, patent incentives?

2. WORK-LIFE BALANCE & REMOTE POLICY
   Remote/hybrid/office policy — what's the reality vs stated policy?
   Working hours culture: overtime expectations, on-call burden.
   Vacation and PTO usage patterns from employee reports.
   Burnout signals from reviews — frequency of mentions.
   Flexibility for parents, timezone spread for global teams.

3. MANAGEMENT & LEADERSHIP QUALITY
   Engineering management reputation from GoWork/Glassdoor reviews.
   Technical vs non-technical management ratio.
   Career growth paths: IC track vs management track clarity.
   Performance review process: fair, political, stack-ranking?
   Communication quality: transparency, all-hands, 1:1s.

4. TEAM DYNAMICS & EMPLOYEE SENTIMENT
   GoWork/Glassdoor overall rating trend (improving/declining).
   Most common praise themes from employee reviews.
   Most common complaint themes from employee reviews.
   DEI initiatives and actual representation.
   Employee referral rate — do people recommend working there?
   Attrition signals: average tenure, departure patterns.
""",
    },
    "compensation": {
        "label": "Compensation & Market Positioning",
        "sections": """
1. SALARY RANGE ANALYSIS
   Salary ranges for embedded Linux / kernel / camera roles at {name}.
   Compare Poland market vs global pay for equivalent roles.
   Senior engineer vs staff/principal pay progression.
   B2B contract (umowa zlecenie/B2B) vs UoP contract differences.
   How does {name} Poland pay compare to local competitors?

2. TOTAL COMPENSATION BREAKDOWN
   Base salary ranges (gross, PLN and EUR).
   Bonus structure: performance, signing, retention — typical amounts.
   Equity/stock: RSU/ESPP/options — vesting schedule, typical grants.
   Benefits: healthcare, gym, equipment budget, conference budget.
   Relocation packages if applicable.

3. NEGOTIATION INTELLIGENCE
   How flexible is {name} on compensation? Known negotiation patterns.
   Counter-offer likelihood if currently employed elsewhere.
   Timing: end-of-year budgets, refresh cycles, promotion windows.
   Leverage points: competing offers, scarce skills, urgency signals.
   What roles are hardest to fill (= most negotiating power)?

4. MARKET BENCHMARKING
   Where does {name}'s compensation rank vs Top 10 employers in Poland?
   Industry benchmarks for embedded/kernel engineers in Łódź/Warsaw/Kraków.
   Compensation trend: increasing, flat, or declining for this role?
   Cost-of-living adjusted comparison with other EU locations.
   Long-term financial value: equity appreciation potential vs cash-heavy offers.
""",
    },
}

# ── Companies (mirrors career-scan.py) ─────────────────────────────────────

COMPANIES = {
    "nvidia": {
        "name": "NVIDIA",
        "industry": "silicon",
        "workday_api": "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite",
        "workday_searches": ["linux kernel driver Poland", "embedded software Poland remote", "camera driver"],
        "career_urls": [],
        "keywords": ["kernel", "driver", "linux", "camera", "tegra", "embedded", "V4L2", "BSP"],
    },
    "google": {
        "name": "Google",
        "industry": "silicon",
        "career_urls": [
            "https://www.google.com/about/careers/applications/jobs/results/?location=Poland&location=Remote&q=linux%20kernel%20driver",
        ],
        "keywords": ["kernel", "driver", "linux", "chromeos", "camera", "pixel", "embedded", "firmware"],
    },
    "amd": {
        "name": "AMD",
        "industry": "silicon",
        "career_urls": [
            "https://careers.amd.com/careers/SearchJobs?3_56_3=19606&3_56_3=19610&15=8662&listFilterMode=1",
        ],
        "keywords": ["kernel", "driver", "linux", "gpu", "rdna", "rocm", "embedded", "firmware"],
    },
    "samsung": {
        "name": "Samsung Electronics",
        "industry": "silicon",
        "workday_api": "https://sec.wd3.myworkdayjobs.com/wday/cxs/sec/Samsung_Careers",
        "workday_searches": ["linux kernel driver", "embedded software Poland", "camera firmware"],
        "career_urls": [],
        "keywords": ["kernel", "driver", "linux", "camera", "embedded", "exynos", "firmware"],
    },
    "amazon": {
        "name": "Amazon",
        "industry": "tech",
        "career_urls": [
            "https://www.amazon.jobs/en/search?base_query=linux+kernel+driver&loc_query=Poland&country=POL",
        ],
        "keywords": ["kernel", "driver", "linux", "embedded", "camera", "ring", "alexa", "firmware"],
    },
    "tcl": {
        "name": "TCL Research Europe",
        "industry": "consumer_electronics",
        "career_urls": ["https://tcl-research.pl/career/"],
        "keywords": ["linux", "driver", "camera", "video", "AI", "embedded", "computer vision"],
    },
    "harman": {
        "name": "HARMAN International",
        "industry": "automotive",
        "career_urls": [
            "https://jobs.harman.com/en_US/careers/SearchJobs/?523=%5B8662%5D&523_format=1482&524=%5B3944%5D&524_format=1483&listFilterMode=1&jobRecordsPerPage=25&jobSort=relevancy",
        ],
        "keywords": ["linux", "driver", "camera", "embedded", "ADAS", "automotive", "kernel"],
    },
    "qualcomm": {
        "name": "Qualcomm",
        "industry": "silicon",
        "career_urls": [
            "https://careers.qualcomm.com/careers?query=linux%20kernel%20driver&pid=446700572793&domain=qualcomm.com&location=Poland&triggerGoButton=false",
        ],
        "keywords": ["kernel", "driver", "linux", "camera", "snapdragon", "embedded", "BSP", "MIPI"],
    },
    "arm": {
        "name": "Arm",
        "industry": "silicon",
        "career_urls": [
            "https://careers.arm.com/search-jobs?k=linux+kernel&l=Poland&orgIds=3529",
        ],
        "keywords": ["kernel", "driver", "linux", "embedded", "GPU", "mali", "firmware"],
    },
    # ── Group A: open-source / embedded / automotive ──
    "intel": {
        "name": "Intel",
        "industry": "silicon",
        "career_urls": [],
        "keywords": ["kernel", "driver", "linux", "embedded", "firmware", "foundry", "GPU"],
    },
    "ericsson": {
        "name": "Ericsson",
        "industry": "telecom",
        "career_urls": [],
        "keywords": ["linux", "embedded", "5G", "firmware", "driver", "telecom"],
    },
    "fujitsu": {
        "name": "Fujitsu",
        "industry": "telecom",
        "career_urls": [],
        "keywords": ["linux", "embedded", "driver", "automotive", "firmware"],
    },
    "thales": {
        "name": "Thales",
        "industry": "defence",
        "career_urls": [],
        "keywords": ["linux", "embedded", "defence", "firmware", "driver", "security"],
    },
    "hexagon": {
        "name": "Hexagon / Leica Geosystems",
        "industry": "metrology",
        "career_urls": [],
        "keywords": ["linux", "embedded", "ADAS", "lidar", "firmware", "driver"],
    },
    "cerence": {
        "name": "Cerence AI",
        "industry": "automotive",
        "career_urls": [],
        "keywords": ["linux", "embedded", "automotive", "AI", "driver", "camera"],
    },
    "apple": {
        "name": "Apple",
        "industry": "faang",
        "career_urls": [],
        "keywords": ["kernel", "driver", "linux", "camera", "embedded", "silicon", "firmware"],
    },
    "aptiv": {
        "name": "Aptiv",
        "industry": "automotive",
        "career_urls": [],
        "keywords": ["linux", "embedded", "ADAS", "automotive", "driver", "camera"],
    },
    "continental": {
        "name": "Continental",
        "industry": "automotive",
        "career_urls": [],
        "keywords": ["linux", "embedded", "ADAS", "camera", "automotive", "driver"],
    },
    "dell": {
        "name": "Dell Technologies",
        "industry": "hardware",
        "career_urls": [],
        "keywords": ["linux", "firmware", "embedded", "driver", "kernel"],
    },
    "tesla": {
        "name": "Tesla",
        "industry": "automotive",
        "career_urls": [],
        "keywords": ["linux", "embedded", "autopilot", "camera", "firmware", "driver"],
    },
    "waymo": {
        "name": "Waymo",
        "industry": "automotive",
        "career_urls": [],
        "keywords": ["linux", "autonomous", "camera", "embedded", "driver", "perception"],
    },
    "hailo": {
        "name": "Hailo",
        "industry": "silicon",
        "career_urls": [],
        "keywords": ["linux", "NPU", "edge AI", "embedded", "driver", "firmware"],
    },
    "bootlin": {
        "name": "Bootlin",
        "industry": "open_source",
        "career_urls": ["https://bootlin.com/company/job-opportunities/"],
        "keywords": ["linux", "kernel", "driver", "embedded", "BSP", "bootloader"],
    },
    "collabora": {
        "name": "Collabora",
        "industry": "open_source",
        "career_urls": ["https://www.collabora.com/careers.html"],
        "keywords": ["linux", "kernel", "multimedia", "camera", "driver", "mesa"],
    },
    "pengutronix": {
        "name": "Pengutronix",
        "industry": "open_source",
        "career_urls": [],
        "keywords": ["linux", "kernel", "embedded", "driver", "Barebox", "BSP"],
    },
    "igalia": {
        "name": "Igalia",
        "industry": "open_source",
        "career_urls": ["https://www.igalia.com/jobs/"],
        "keywords": ["linux", "kernel", "open source", "driver", "mesa", "web engine"],
    },
    "toradex": {
        "name": "Toradex",
        "industry": "silicon",
        "career_urls": [],
        "keywords": ["linux", "embedded", "Arm", "SoM", "BSP", "driver", "Torizon"],
    },
    "linaro": {
        "name": "Linaro",
        "industry": "open_source",
        "career_urls": ["https://www.linaro.org/careers/"],
        "keywords": ["linux", "kernel", "Arm", "embedded", "multimedia", "driver"],
    },
    "canonical": {
        "name": "Canonical",
        "industry": "open_source",
        "career_urls": ["https://canonical.com/careers"],
        "keywords": ["linux", "kernel", "Ubuntu", "embedded", "IoT", "driver"],
    },
    "redhat": {
        "name": "Red Hat",
        "industry": "open_source",
        "career_urls": [],
        "keywords": ["linux", "kernel", "RHEL", "driver", "embedded", "firmware"],
    },
    "suse": {
        "name": "SUSE",
        "industry": "open_source",
        "career_urls": [],
        "keywords": ["linux", "kernel", "embedded", "driver", "firmware"],
    },
    "sifive": {
        "name": "SiFive",
        "industry": "silicon",
        "career_urls": [],
        "keywords": ["RISC-V", "linux", "kernel", "embedded", "driver", "firmware"],
    },
    "tenstorrent": {
        "name": "Tenstorrent",
        "industry": "silicon",
        "career_urls": [],
        "keywords": ["RISC-V", "AI", "linux", "kernel", "driver", "firmware"],
    },
    "cerebras": {
        "name": "Cerebras",
        "industry": "silicon",
        "career_urls": [],
        "keywords": ["AI", "linux", "kernel", "driver", "firmware", "wafer-scale"],
    },
}

# ── Helpers ────────────────────────────────────────────────────────────────

def log(msg):
    print(f"  {msg}", flush=True)


def signal_send(msg):
    try:
        payload = json.dumps({
            "jsonrpc": "2.0", "method": "send",
            "params": {"account": SIGNAL_FROM, "recipient": [SIGNAL_TO], "message": msg},
            "id": "career-think",
        }).encode()
        req = urllib.request.Request(SIGNAL_RPC, data=payload,
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15)
        log(f"Signal alert sent ({len(msg)} chars)")
    except Exception as e:
        log(f"Signal send failed: {e}")


def call_ollama(system_prompt, user_prompt, temperature=0.5, max_tokens=4000, think=True):
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=10) as r:
            tags = json.loads(r.read())
            models = [m["name"] for m in tags.get("models", [])]
            if not any(OLLAMA_MODEL in m for m in models):
                log(f"Model {OLLAMA_MODEL} not found")
                return None
    except Exception as e:
        log(f"Ollama health check failed: {e}")
        return None

    prefix = "" if think else "/nothink\n"
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prefix + user_prompt},
        ],
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": 24576},
    }).encode()

    req = urllib.request.Request(OLLAMA_CHAT, data=payload,
        headers={"Content-Type": "application/json"})

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            result = json.loads(resp.read())
            content = result.get("message", {}).get("content", "")
            elapsed = time.time() - t0
            tokens = result.get("eval_count", len(content.split()))
            tps = tokens / elapsed if elapsed > 0 else 0
            if "</think>" in content:
                content = content.split("</think>", 1)[1].strip()
            content = sanitize_llm_output(content)
            log(f"LLM: {elapsed:.0f}s, {tokens} tok ({tps:.1f} t/s)")
            return content
    except Exception as e:
        log(f"Ollama call failed: {e}")
        return None


def strip_html(text):
    """Strip HTML tags and decode entities."""
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', text, flags=re.S)
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.S)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def fetch_url(url, timeout=20):
    """Fetch a URL and return text content."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return raw.decode('utf-8')
            except UnicodeDecodeError:
                return raw.decode('latin-1')
    except Exception as e:
        log(f"  Fetch error {url[:60]}: {e}")
        return None


def fetch_workday_jobs(api_base, searches, limit=20):
    """Fetch jobs from Workday API."""
    results = []
    for search_term in searches[:3]:
        try:
            payload = json.dumps({
                "appliedFacets": {},
                "limit": limit,
                "offset": 0,
                "searchText": search_term,
            }).encode()
            url = f"{api_base}/jobs"
            req = urllib.request.Request(url, data=payload, headers={
                "Content-Type": "application/json",
                "User-Agent": UA,
            })
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())
                postings = data.get("jobPostings", [])
                for p in postings:
                    results.append({
                        "title": p.get("title", "?"),
                        "location": p.get("locationsText", "?"),
                        "posted": p.get("postedOn", ""),
                        "url": api_base.split("/wday/")[0] + p.get("externalPath", ""),
                    })
            time.sleep(1)
        except Exception as e:
            log(f"  Workday search '{search_term}': {e}")
    # Deduplicate by title
    seen = set()
    unique = []
    for r in results:
        key = r["title"]
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


# ── Data fetcher ───────────────────────────────────────────────────────────

def fetch_company_careers(slug, company):
    """Fetch career data for one company."""
    data = {
        "name": company["name"],
        "industry": company["industry"],
        "jobs_found": [],
        "page_snippets": [],
    }

    # Workday API
    if company.get("workday_api"):
        log(f"  Workday API: {company['name']}")
        jobs = fetch_workday_jobs(
            company["workday_api"],
            company.get("workday_searches", []),
        )
        data["jobs_found"].extend(jobs)
        log(f"    Found {len(jobs)} positions")

    # Career page URLs
    for url in company.get("career_urls", [])[:3]:
        log(f"  Fetching: {url[:60]}...")
        raw = fetch_url(url)
        if raw:
            text = strip_html(raw)[:4000]
            data["page_snippets"].append({
                "url": url,
                "text": text[:2000],
                "length": len(text),
            })
            # Try to extract job titles from text
            keywords = company.get("keywords", [])
            for kw in keywords:
                matches = re.findall(rf'[^\n.]*{re.escape(kw)}[^\n.]*', text, re.I)
                for m in matches[:3]:
                    m = m.strip()[:200]
                    if len(m) > 20:
                        data["jobs_found"].append({"title": m, "source": "page_extract"})
        time.sleep(1)

    # Load previous career-scan data if available
    latest_scan = CAREERS_DIR / "latest-careers.json"
    if latest_scan.exists():
        try:
            scan_data = json.load(open(latest_scan))
            company_results = scan_data.get("career_results", {}).get(slug, {})
            if company_results:
                data["previous_scan"] = {
                    "jobs_found": company_results.get("jobs_found", 0),
                    "matches": company_results.get("matches", [])[:10],
                    "scan_time": scan_data.get("meta", {}).get("timestamp", ""),
                }
                log(f"  Previous scan: {company_results.get('jobs_found', 0)} jobs at {data['previous_scan']['scan_time']}")
        except Exception:
            pass

    log(f"  Total data points: {len(data['jobs_found'])} jobs, {len(data['page_snippets'])} page snippets")
    return data


# ── Deep analysis prompt ───────────────────────────────────────────────────

def get_career_prompt(slug, company, career_data, focus=None):
    """Build a deep career analysis prompt for one company, optionally focused."""
    name = company["name"]
    industry = company["industry"]
    keywords = ", ".join(company.get("keywords", []))

    # Format job listings
    jobs_text = ""
    if career_data.get("jobs_found"):
        job_lines = []
        for j in career_data["jobs_found"][:15]:
            loc = j.get("location", "")
            title = j.get("title", "")
            if loc:
                job_lines.append(f"  - {title} ({loc})")
            else:
                job_lines.append(f"  - {title}")
        jobs_text = "\n".join(job_lines)

    # Format page snippets
    snippet_text = ""
    for ps in career_data.get("page_snippets", [])[:2]:
        text = ps.get("text", "")[:1000]
        if text:
            snippet_text += f"\n[Career page excerpt ({ps.get('url', '')[:50]})]:\n{text[:800]}\n"

    # Previous scan context
    prev_text = ""
    prev = career_data.get("previous_scan", {})
    if prev:
        prev_text = f"\nPrevious scan ({prev.get('scan_time', 'N/A')}): {prev.get('jobs_found', 0)} jobs found"
        matches = prev.get("matches", [])
        if matches:
            prev_text += "\nPrevious matching positions:\n"
            for m in matches[:5]:
                prev_text += f"  - {m.get('title', '?')} @ {m.get('location', '?')}\n"

    # Profile context
    profile_ctx = ""
    if PROFILE_FILE.exists():
        try:
            pf = json.load(open(PROFILE_FILE))
            profile_ctx = f"""
User profile:
- Role: {pf.get('role', 'embedded Linux engineer')}
- Experience: {pf.get('experience_years', '15+')} years
- Key skills: {', '.join(pf.get('interests', [])[:8])}
- Location: Poland (Łódź)
- Keywords of interest: {keywords}"""
        except Exception:
            pass

    # Data block (shared)
    data_block = f"""Current job listings found ({len(career_data.get('jobs_found', []))}) positions):
{jobs_text or '  (no current listings found)'}
{snippet_text}
{prev_text}"""

    # ── Focused prompt ──
    if focus and focus in CAREER_FOCUS_AREAS:
        fa = CAREER_FOCUS_AREAS[focus]
        focus_label = fa["label"]
        focus_sections = fa["sections"].format(name=name)

        system = f"""You are a senior career intelligence analyst specializing in {industry} industry hiring.
Write ONLY in English. This is a FOCUSED deep-dive on {focus_label} for {name}.
Go deeper than a general overview — provide specific, actionable career intelligence.
Think about HOW hiring patterns reflect corporate strategy and technology bets.
Date: {datetime.now().strftime('%Y-%m-%d')}"""

        user = f"""FOCUSED career intelligence: {focus_label} for {name} ({industry})
{profile_ctx}

{data_block}

Perform a FOCUSED DEEP ANALYSIS on {focus_label}:
{focus_sections}

Be specific. Reference actual job titles and requirements when possible.
Go DEEPER than a general overview. Provide actionable intelligence.
Target: 500-700 words. English only."""

        return system, user

    # ── Full (unfocused) prompt ──
    system = f"""You are a senior career intelligence analyst specializing in {industry} industry hiring.
Write ONLY in English. Provide deep, strategic career analysis — not just job listing summaries.
Think about HOW hiring patterns reflect corporate strategy, team building, and technology bets.
Date: {datetime.now().strftime('%Y-%m-%d')}"""

    user = f"""Deep career intelligence analysis for {name} ({industry}):
{profile_ctx}

{data_block}

Perform a DEEP CAREER STRATEGY ANALYSIS:

1. HIRING PATTERN ANALYSIS
   What types of roles is {name} actively hiring for?
   Which teams are expanding? Which skill areas are in demand?
   Compare to typical hiring patterns — is this expansion, replacement, or restructuring?

2. TECHNOLOGY STACK SIGNALS
   What technologies does the job language reveal {name} is investing in?
   New platform bets, architecture transitions, toolchain evolving?
   Any signals about kernel version targets, GPU compute, camera ISP, ADAS?

3. TEAM & ORGANIZATION SIGNALS
   Are they building new teams or backfilling existing ones?
   Senior vs junior ratio — investing in leadership or execution?
   Poland/Europe specific office expansion or contraction signals?

4. COMPETITIVE HIRING CONTEXT
   How does {name}'s hiring compare to competitors in {industry}?
   Are they competing for the same talent pool?
   Salary competitiveness signals from job descriptions.

5. STRATEGIC IMPLICATIONS
   What does the hiring pattern say about {name}'s 12-month technology roadmap?
   New product lines, market entry, or platform transitions visible through hiring?

6. PERSONAL FIT ASSESSMENT
   For an experienced embedded Linux / camera / kernel engineer in Łódź:
   - Best-fit positions from the listings
   - Skills gaps to address
   - Timing recommendation (apply now vs wait)
   - Negotiation leverage indicators

Be specific. Reference actual job titles and requirements when possible.
Target: 500-700 words. English only."""

    return system, user


# ── Per-company think ──────────────────────────────────────────────────────

def think_one_company(slug, focus=None):
    """Deep chain-of-thought career analysis of one company.
    
    If focus is given, produces a narrow deep-dive on that specific dimension.
    """
    if slug not in COMPANIES:
        log(f"Unknown company: {slug}")
        log(f"Available: {', '.join(sorted(COMPANIES.keys()))}")
        sys.exit(1)

    if focus and focus not in CAREER_FOCUS_AREAS:
        log(f"Unknown focus area: {focus}")
        log(f"Available: {', '.join(sorted(CAREER_FOCUS_AREAS.keys()))}")
        sys.exit(1)

    company = COMPANIES[slug]
    t_start = time.time()

    focus_label = f" [{CAREER_FOCUS_AREAS[focus]['label']}]" if focus else ""
    log(f"{'='*60}")
    log(f"CAREER THINK — {company['name']}{focus_label}")
    log(f"{'='*60}")

    THINK_DIR.mkdir(parents=True, exist_ok=True)

    # Check if already analyzed today
    today = datetime.now().strftime("%Y%m%d")
    suffix = f"-{focus}" if focus else ""
    out_file = THINK_DIR / f"{slug}{suffix}-{today}.json"
    if out_file.exists():
        log(f"Already analyzed today: {out_file.name}")
        return

    # 1. Fetch career data
    log("\n── Fetching career data ──")
    career_data = fetch_company_careers(slug, company)

    # 2. Deep LLM analysis
    log(f"\n── Deep analysis{focus_label} (chain-of-thought enabled) ──")
    system, user = get_career_prompt(slug, company, career_data, focus=focus)
    analysis = call_ollama(system, user, temperature=0.5, max_tokens=4000, think=True)

    if not analysis:
        log("LLM analysis failed")
        analysis = ""

    # 3. Save
    elapsed = time.time() - t_start
    output = {
        "meta": {
            "company": slug,
            "name": company["name"],
            "industry": company["industry"],
            "focus": focus,
            "focus_label": CAREER_FOCUS_AREAS[focus]["label"] if focus else None,
            "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "date": today,
            "duration_s": round(elapsed),
        },
        "career_data": {
            "jobs_count": len(career_data.get("jobs_found", [])),
            "pages_fetched": len(career_data.get("page_snippets", [])),
            "has_previous_scan": bool(career_data.get("previous_scan")),
        },
        "analysis": analysis,
    }

    with open(out_file, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    log(f"\nSaved: {out_file.name} ({out_file.stat().st_size:,} bytes) in {elapsed:.0f}s")


# ── Summary aggregation ───────────────────────────────────────────────────

def think_summary():
    """Aggregate all daily career analyses into a career market overview."""
    t_start = time.time()

    log(f"{'='*60}")
    log(f"CAREER THINK SUMMARY — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    log(f"{'='*60}")

    THINK_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")

    analyses = []
    focused_analyses = []
    for slug in sorted(COMPANIES.keys()):
        think_file = THINK_DIR / f"{slug}-{today}.json"
        if think_file.exists():
            try:
                analyses.append(json.load(open(think_file)))
            except Exception as e:
                log(f"  {slug}: read error — {e}")
        # Load focused analyses
        for focus_key in CAREER_FOCUS_AREAS:
            focused_file = THINK_DIR / f"{slug}-{focus_key}-{today}.json"
            if focused_file.exists():
                try:
                    focused_analyses.append(json.load(open(focused_file)))
                except Exception:
                    pass

    total = len(analyses) + len(focused_analyses)
    log(f"\nFound {len(analyses)} main + {len(focused_analyses)} focused analyses for today ({total} total)")

    if len(analyses) < 3:
        log("Not enough analyses to summarize (need ≥3). Skipping.")
        return

    # Build combined text
    summaries = []
    for a in analyses:
        name = a["meta"]["name"]
        industry = a["meta"]["industry"]
        jobs = a.get("career_data", {}).get("jobs_count", 0)
        analysis_text = a.get("analysis", "")[:500]
        # Append focused excerpts
        focus_excerpts = []
        for fa in focused_analyses:
            if fa["meta"]["company"] == a["meta"]["company"]:
                fl = fa["meta"].get("focus_label", fa["meta"].get("focus", ""))
                focus_excerpts.append(f"  [{fl}]: {fa.get('analysis', '')[:250]}")
        focus_block = "\n".join(focus_excerpts) if focus_excerpts else ""
        entry = f"### {name} ({industry}, {jobs} positions)\n{analysis_text}"
        if focus_block:
            entry += f"\nFocused deep-dives:\n{focus_block}"
        summaries.append(entry)

    company_block = "\n\n---\n\n".join(summaries)

    system = """You are the Chief Career Intelligence Officer. Write ONLY in English.
Synthesize per-company career analyses into a comprehensive job market overview.
The reader is a senior embedded Linux engineer (15+ years, kernel/camera/BSP) in Poland."""

    user = f"""Today's deep career analyses across {len(analyses)} companies:

{company_block}

Create a CAREER MARKET INTELLIGENCE BRIEF:

1. HIRING HEAT MAP
   Which companies are actively hiring? Which are quiet/contracting?
   Net sentiment: is the market expanding or tightening?

2. MOST DEMANDED SKILLS
   Top 5 technical skills across all companies.
   Emerging skill demands vs declining ones.

3. BEST OPPORTUNITIES RIGHT NOW
   Top 3 positions to apply for, ranked by fit and timing.
   Which companies offer strongest career growth potential?

4. INDUSTRY TECHNOLOGY SHIFTS
   What technology transitions are visible through hiring patterns?
   New platform bets, architecture changes, tooling evolution.

5. SALARY & NEGOTIATION SIGNALS
   Any salary competitiveness indicators from listings?
   Leverage points for negotiation.

6. ACTION ITEMS (2-3 specific recommendations)
   What to do this week: apply, upskill, network, or wait.

Target: 500-700 words. English only."""

    log("\n── Running summary analysis (chain-of-thought) ──")
    summary = call_ollama(system, user, temperature=0.4, max_tokens=4000, think=True)

    if not summary:
        log("Summary LLM call failed")
        return

    elapsed = time.time() - t_start
    output = {
        "meta": {
            "type": "summary",
            "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "date": today,
            "companies_analyzed": len(analyses),
            "duration_s": round(elapsed),
        },
        "summary": summary,
        "companies": [a["meta"]["company"] for a in analyses],
    }

    out_file = THINK_DIR / f"summary-{today}.json"
    with open(out_file, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    log(f"\nSaved: {out_file.name} ({out_file.stat().st_size:,} bytes)")

    latest = THINK_DIR / "latest-summary.json"
    latest.unlink(missing_ok=True)
    latest.symlink_to(out_file.name)

    # Regenerate dashboard
    try:
        import subprocess
        subprocess.run(["python3", "/opt/netscan/generate-html.py"],
                       timeout=120, capture_output=True)
        log("Dashboard regenerated")
    except Exception as e:
        log(f"Dashboard regen failed: {e}")

    # Cleanup (keep 14 days ≈ 400+ files with focused analyses)
    all_files = sorted(THINK_DIR.glob("*.json"))
    if len(all_files) > 500:
        for f in all_files[:-500]:
            if f.name.startswith("latest"):
                continue
            f.unlink()
            log(f"  Pruned: {f.name}")

    log(f"\nSummary complete in {elapsed:.0f}s")


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Deep per-company career intelligence")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--company', '-c', help="Company slug to analyze")
    group.add_argument('--summary', '-s', action='store_true', help="Generate daily career summary")
    group.add_argument('--list', '-l', action='store_true', help="List available companies")
    parser.add_argument('--focus', '-f', help="Focus area for deep-dive (skills, opportunity)")
    args = parser.parse_args()

    if args.list:
        print("\nCompanies:")
        for slug, info in sorted(COMPANIES.items()):
            print(f"  {slug:12s}  {info['name']:28s}  [{info['industry']}]")
        print(f"\nFocus areas:")
        for fk, fv in CAREER_FOCUS_AREAS.items():
            print(f"  {fk:15s}  {fv['label']}")
        return

    if args.summary:
        think_summary()
    else:
        think_one_company(args.company, focus=args.focus)


if __name__ == "__main__":
    main()
