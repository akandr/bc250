#!/usr/bin/env python3
"""company-think.py — Deep per-company corporate intelligence.

Runs extended LLM chain-of-thought analysis on one company's strategy,
news, and market position. Fetches fresh news + GoWork reviews + forum
discussions, then produces deep strategic analysis.

Usage:
  company-think.py --company nvidia   # Deep intel on NVIDIA
  company-think.py --summary          # Aggregate all daily analyses
  company-think.py --list             # Show available companies

Output: /opt/netscan/data/intel/think/<company>-YYYYMMDD.json
"""

import argparse, html, json, os, re, sys, time, urllib.request, urllib.error, urllib.parse
from datetime import datetime
from pathlib import Path
from llm_sanitize import sanitize_llm_output

# ── Config ─────────────────────────────────────────────────────────────────

OLLAMA_URL   = "http://localhost:11434"
OLLAMA_CHAT  = f"{OLLAMA_URL}/api/chat"
OLLAMA_MODEL = "qwen3:14b"

THINK_DIR    = Path("/opt/netscan/data/intel/think")
INTEL_DIR    = Path("/opt/netscan/data/intel")
PROFILE_FILE = Path("/opt/netscan/profile.json")

SIGNAL_RPC   = "http://127.0.0.1:8080/api/v1/rpc"
SIGNAL_FROM  = "+<BOT_PHONE>"
SIGNAL_TO    = "+<OWNER_PHONE>"

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

# ── Focus areas for per-company sub-topic analysis ─────────────────────────
# Each focus produces a deeper, narrower analysis instead of the broad 8-section report.
COMPANY_FOCUS_AREAS = {
    "strategy": {
        "label": "Corporate Strategy & Risk",
        "sections": """
1. STRATEGIC DIRECTION (expanded)
   What is {name}'s current strategy in detail? Recent pivots, new initiatives?
   Where are they investing R&D resources? Budget shifts? New markets?
   Compare stated strategy vs actual execution evidence.

2. FINANCIAL HEALTH & RESTRUCTURING (expanded)
   Revenue trajectory signals, margin pressures, capex changes.
   Cost-cutting programs, layoffs, hiring freezes — timeline and scale.
   Restructuring plans, division spin-offs, asset sales.
   Debt levels, cash reserves, runway assessment.

3. M&A AND PARTNERSHIPS (expanded)
   Recent acquisitions, divestitures, or partnership announcements.
   Potential M&A targets or acquirers? Activist investor activity?
   Joint ventures, licensing deals, ecosystem partnerships.
   Integration progress of recent acquisitions.

4. COMPETITIVE POSITIONING (expanded)
   Market share trends in key segments — quantify where possible.
   Competitive threats: who is gaining, who is losing ground?
   Technology moat: IP, patents, ecosystem lock-in, switching costs.
   Pricing power and customer concentration risks.

5. RISK MATRIX (expanded)
   Top 5 risks for {name} in the next 12 months.
   Geopolitical risks (tariffs, export controls, China exposure).
   Technology risks (platform transitions, architecture bets).
   Execution risks (leadership changes, talent drain, integration failures).
   Regulatory risks (EU regulations, antitrust, data privacy).
""",
    },
    "tech-talent": {
        "label": "Technology & Talent Intelligence",
        "sections": """
1. TECHNOLOGY BETS (expanded)
   What technology platforms is {name} betting on? AI/ML integration?
   New chip architectures, software-defined products, compute paradigms?
   Open source strategy — upstream contributions, community engagement.
   Relevance to embedded Linux / camera / ADAS / SoC engineering.
   Technology stack evolution: languages, tools, frameworks, SDKs.

2. ENGINEERING CULTURE & LEADERSHIP
   Executive and engineering leadership changes.
   Employee sentiment from reviews — cultural red flags or improvements.
   Management credibility, communication quality, vision clarity.
   Engineering org structure: centralized vs distributed teams.
   Developer experience: tooling, CI/CD, code review practices.
   Work-life balance signals, remote/hybrid policy.

3. HIRING & TALENT SIGNALS
   What types of roles are they actively hiring for?
   Which teams are expanding? Senior vs junior ratio?
   Compensation competitiveness signals from job descriptions.
   Talent drain indicators — where are ex-employees going?
   Skills in demand: what technologies appear most in job listings?

4. POLAND / EUROPE PRESENCE (expanded)
   Office expansion or contraction in Poland — headcount trends.
   European operations strategy: Brexit effects, EU regulation impact.
   Local team maturity: satellite office vs. engineering hub.
   R&D center investment signals.
   Competition for local talent with other companies.
   Salary benchmarks for the Polish market.
""",
    },
    "financial": {
        "label": "Financial Deep-Dive & Investment Signals",
        "sections": """
1. REVENUE & EARNINGS ANALYSIS
   Latest quarterly/annual revenue, YoY growth trajectory.
   Revenue mix by segment — which units are growing/shrinking?
   Earnings quality: one-time items, accounting changes, guidance credibility.
   For private companies: funding rounds, valuation signals, runway.

2. VALUATION & INVESTOR SENTIMENT
   Current market cap vs historical range, P/E ratios, EV/EBITDA.
   Institutional ownership changes — who's buying/selling?
   Analyst consensus: upgrades, downgrades, target price moves.
   Insider transactions: executive buying/selling signals.
   For non-public: estimated valuation from latest fundraising.

3. CAPITAL ALLOCATION & BALANCE SHEET
   Cash position, debt levels, debt maturity profile.
   CapEx trends: where are they investing capital?
   Stock buybacks, dividend policy changes.
   R&D spend as % of revenue — trend direction.
   Working capital efficiency signals.

4. INVESTMENT THESIS ASSESSMENT
   Bull case: 3 strongest arguments for investing NOW.
   Bear case: 3 biggest risks that could destroy value.
   Catalyst timeline: upcoming events that could move the stock.
   Relative value: cheaper/expensive vs peers?
   Position sizing consideration: conviction level 1-10.
""",
    },
    "competitive": {
        "label": "Competitive Landscape & Market Position",
        "sections": """
1. MARKET SHARE & POSITIONING
   {name}'s market share in key segments — quantified where possible.
   Share trend: gaining or losing ground? In which segments?
   Total addressable market (TAM) and serviceable addressable market (SAM).
   Pricing tier: premium, mid-range, value — any shifts?

2. COMPETITIVE MOAT ANALYSIS
   Technology moat: proprietary IP, patents, trade secrets.
   Ecosystem moat: developer tools, SDK adoption, partner lock-in.
   Switching costs for customers — high, medium, or low?
   Network effects or platform dynamics at play?
   Brand and reputation moat in hiring and customer acquisition.

3. COMPETITOR COMPARISON
   Top 3-5 direct competitors and their relative strengths.
   Recent competitive wins/losses (design wins, customer defections).
   Where is {name} being disrupted from below?
   Where is {name} disrupting incumbents above?
   Technology generation gaps: who has the leading product?

4. STRATEGIC THREATS & OPPORTUNITIES
   New market entrants or business model disruptors.
   Vertical integration threats (customers/suppliers becoming competitors).
   Geopolitical shifts affecting competitive dynamics (tariffs, export bans).
   Platform transitions that could reshuffle market share.
   Open source disruption risks to proprietary business models.
""",
    },
}

# ── Companies (mirrors company-intel.py) ───────────────────────────────────

COMPANIES = {
    "nvidia": {
        "name": "NVIDIA",
        "industry": "silicon",
        "gowork_id": "21622584",
        "news_url": "https://nvidianews.nvidia.com/",
        "search_terms": ["NVIDIA Poland", "NVIDIA embedded", "NVIDIA automotive ADAS"],
    },
    "google": {
        "name": "Google",
        "industry": "faang",
        "gowork_id": "949234",
        "news_url": "https://blog.google/",
        "search_terms": ["Google Poland", "Google embedded hardware", "Google Pixel camera"],
    },
    "amd": {
        "name": "AMD",
        "industry": "silicon",
        "gowork_id": "26904732",
        "news_url": "https://www.amd.com/en/newsroom.html",
        "search_terms": ["AMD Poland", "AMD embedded", "AMD RDNA layoffs"],
    },
    "intel": {
        "name": "Intel",
        "industry": "silicon",
        "gowork_id": "930747",
        "news_url": "https://newsroom.intel.com/",
        "search_terms": ["Intel Poland", "Intel foundry", "Intel layoffs restructuring"],
    },
    "samsung": {
        "name": "Samsung Electronics",
        "industry": "silicon",
        "gowork_id": "21451047",
        "news_url": "https://news.samsung.com/global/",
        "search_terms": ["Samsung Poland R&D", "Samsung semiconductor foundry", "Samsung Exynos"],
    },
    "qualcomm": {
        "name": "Qualcomm",
        "industry": "silicon",
        "gowork_id": "20727487",
        "news_url": "https://www.qualcomm.com/news",
        "search_terms": ["Qualcomm Poland", "Qualcomm automotive Snapdragon Ride"],
    },
    "arm": {
        "name": "Arm",
        "industry": "silicon",
        "gowork_id": "23971017",
        "news_url": "https://newsroom.arm.com/",
        "search_terms": ["Arm Poland", "Arm automotive", "Arm IPO valuation"],
    },
    "harman": {
        "name": "HARMAN International",
        "industry": "automotive",
        "gowork_id": "1036892",
        "news_url": "https://www.harman.com/news",
        "search_terms": ["HARMAN ADAS camera", "HARMAN automotive", "Samsung HARMAN acquisition"],
    },
    "ericsson": {
        "name": "Ericsson",
        "industry": "telecom",
        "gowork_id": "8528",
        "news_url": "https://www.ericsson.com/en/newsroom",
        "search_terms": ["Ericsson Poland", "Ericsson layoffs", "Ericsson 5G"],
    },
    "tcl": {
        "name": "TCL Research Europe",
        "industry": "consumer_electronics",
        "gowork_id": "23966243",
        "news_url": None,
        "search_terms": ["TCL Research Europe Poland", "TCL display camera"],
    },
    "fujitsu": {
        "name": "Fujitsu",
        "industry": "telecom",
        "gowork_id": "365816",
        "news_url": "https://www.fujitsu.com/global/about/resources/news/",
        "search_terms": ["Fujitsu Poland", "Fujitsu automotive embedded"],
    },
    "thales": {
        "name": "Thales",
        "industry": "defence",
        "gowork_id": "239192",
        "news_url": "https://www.thalesgroup.com/en/worldwide/group/press_release",
        "search_terms": ["Thales Poland defence", "Thales embedded systems"],
    },
    "amazon": {
        "name": "Amazon",
        "industry": "faang",
        "gowork_id": "1026920",
        "news_url": None,
        "search_terms": ["Amazon Development Center Poland", "Amazon Ring camera"],
    },
    "hexagon": {
        "name": "Hexagon / Leica Geosystems",
        "industry": "metrology",
        "gowork_id": "70870",
        "news_url": "https://hexagon.com/newsroom",
        "search_terms": ["Hexagon Manufacturing Intelligence Poland", "Hexagon ADAS lidar"],
    },
    "cerence": {
        "name": "Cerence AI",
        "industry": "automotive",
        "gowork_id": None,
        "news_url": "https://www.cerence.com/news",
        "search_terms": ["Cerence AI Poland", "Cerence AI automotive", "Cerence AI layoffs hiring"],
    },
    "apple": {
        "name": "Apple",
        "industry": "faang",
        "gowork_id": None,
        "news_url": "https://www.apple.com/newsroom/",
        "search_terms": ["Apple Poland", "Apple embedded camera", "Apple silicon kernel"],
    },
    "dell": {
        "name": "Dell Technologies",
        "industry": "hardware",
        "gowork_id": None,
        "news_url": "https://www.dell.com/en-us/blog/",
        "search_terms": ["Dell Technologies Poland", "Dell embedded firmware", "Dell layoffs hiring"],
    },
    # ── Group A: open-source / embedded / automotive companies ──
    "aptiv": {
        "name": "Aptiv",
        "industry": "automotive",
        "gowork_id": None,
        "news_url": "https://www.aptiv.com/en/newsroom",
        "search_terms": ["Aptiv Poland", "Aptiv ADAS embedded", "Aptiv autonomous driving"],
    },
    "continental": {
        "name": "Continental",
        "industry": "automotive",
        "gowork_id": None,
        "news_url": "https://www.continental.com/en/press/",
        "search_terms": ["Continental Poland", "Continental ADAS camera", "Continental automotive embedded"],
    },
    "tesla": {
        "name": "Tesla",
        "industry": "automotive",
        "gowork_id": None,
        "news_url": None,
        "search_terms": ["Tesla autopilot camera", "Tesla embedded software", "Tesla Europe hiring"],
    },
    "waymo": {
        "name": "Waymo",
        "industry": "automotive",
        "gowork_id": None,
        "news_url": "https://blog.waymo.com/",
        "search_terms": ["Waymo hiring", "Waymo autonomous driving", "Waymo Europe remote"],
    },
    "hailo": {
        "name": "Hailo",
        "industry": "silicon",
        "gowork_id": None,
        "news_url": "https://hailo.ai/company-overview/newsroom/",
        "search_terms": ["Hailo AI chip edge", "Hailo NPU embedded", "Hailo hiring"],
    },
    "bootlin": {
        "name": "Bootlin",
        "industry": "open_source",
        "gowork_id": None,
        "news_url": "https://bootlin.com/blog/",
        "search_terms": ["Bootlin embedded Linux", "Bootlin kernel driver", "Bootlin hiring"],
    },
    "collabora": {
        "name": "Collabora",
        "industry": "open_source",
        "gowork_id": None,
        "news_url": "https://www.collabora.com/news-and-blog/",
        "search_terms": ["Collabora Linux kernel", "Collabora multimedia camera", "Collabora hiring"],
    },
    "pengutronix": {
        "name": "Pengutronix",
        "industry": "open_source",
        "gowork_id": None,
        "news_url": None,
        "search_terms": ["Pengutronix embedded Linux", "Pengutronix kernel", "Pengutronix hiring"],
    },
    "igalia": {
        "name": "Igalia",
        "industry": "open_source",
        "gowork_id": None,
        "news_url": "https://www.igalia.com/24-7",
        "search_terms": ["Igalia Linux kernel", "Igalia open source", "Igalia hiring"],
    },
    "toradex": {
        "name": "Toradex",
        "industry": "silicon",
        "gowork_id": None,
        "news_url": "https://www.toradex.com/news",
        "search_terms": ["Toradex embedded Linux", "Toradex Arm SoM", "Toradex Torizon"],
    },
    "linaro": {
        "name": "Linaro",
        "industry": "open_source",
        "gowork_id": None,
        "news_url": "https://www.linaro.org/news/",
        "search_terms": ["Linaro Arm kernel", "Linaro embedded multimedia", "Linaro hiring"],
    },
    "canonical": {
        "name": "Canonical",
        "industry": "open_source",
        "gowork_id": None,
        "news_url": "https://canonical.com/blog",
        "search_terms": ["Canonical Ubuntu kernel", "Canonical embedded IoT", "Canonical Poland remote"],
    },
    "redhat": {
        "name": "Red Hat",
        "industry": "open_source",
        "gowork_id": None,
        "news_url": "https://www.redhat.com/en/about/newsroom",
        "search_terms": ["Red Hat kernel Poland", "Red Hat embedded", "Red Hat RHEL driver"],
    },
    "suse": {
        "name": "SUSE",
        "industry": "open_source",
        "gowork_id": None,
        "news_url": "https://www.suse.com/news/",
        "search_terms": ["SUSE kernel Poland", "SUSE embedded", "SUSE hiring"],
    },
    "sifive": {
        "name": "SiFive",
        "industry": "silicon",
        "gowork_id": None,
        "news_url": "https://www.sifive.com/press",
        "search_terms": ["SiFive RISC-V", "SiFive Linux kernel", "SiFive hiring"],
    },
    "tenstorrent": {
        "name": "Tenstorrent",
        "industry": "silicon",
        "gowork_id": None,
        "news_url": None,
        "search_terms": ["Tenstorrent RISC-V AI", "Tenstorrent Warsaw Poland", "Tenstorrent hiring"],
    },
    "cerebras": {
        "name": "Cerebras",
        "industry": "silicon",
        "gowork_id": None,
        "news_url": "https://www.cerebras.ai/blog",
        "search_terms": ["Cerebras AI wafer-scale", "Cerebras hiring", "Cerebras IPO"],
    },
    # ── Group B: new semiconductor / automotive companies ──
    "mobileye": {
        "name": "Mobileye (Intel)",
        "industry": "automotive",
        "gowork_id": None,
        "news_url": "https://www.mobileye.com/news/",
        "search_terms": ["Mobileye EyeQ camera ADAS", "Mobileye autonomous driving", "Mobileye hiring"],
    },
    "valeo": {
        "name": "Valeo",
        "industry": "automotive",
        "gowork_id": None,
        "news_url": "https://www.valeo.com/en/press-releases/",
        "search_terms": ["Valeo ADAS camera Poland", "Valeo automotive embedded", "Valeo hiring"],
    },
    "bosch": {
        "name": "Bosch",
        "industry": "automotive",
        "gowork_id": None,
        "news_url": "https://www.bosch.com/stories/",
        "search_terms": ["Bosch ADAS Poland", "Bosch automotive embedded", "Bosch hiring Poland"],
    },
    "zf": {
        "name": "ZF Friedrichshafen",
        "industry": "automotive",
        "gowork_id": None,
        "news_url": "https://press.zf.com/",
        "search_terms": ["ZF ADAS camera", "ZF automotive embedded", "ZF Poland hiring"],
    },
    "nxp": {
        "name": "NXP Semiconductors",
        "industry": "silicon",
        "gowork_id": None,
        "news_url": "https://media.nxp.com/",
        "search_terms": ["NXP i.MX Poland", "NXP automotive embedded", "NXP camera BSP"],
    },
    "renesas": {
        "name": "Renesas",
        "industry": "silicon",
        "gowork_id": None,
        "news_url": "https://www.renesas.com/en/about/newsroom",
        "search_terms": ["Renesas R-Car automotive", "Renesas embedded Linux", "Renesas Europe hiring"],
    },
    "mediatek": {
        "name": "MediaTek",
        "industry": "silicon",
        "gowork_id": None,
        "news_url": "https://corp.mediatek.com/news-events/press-releases",
        "search_terms": ["MediaTek camera ISP", "MediaTek SoC Linux", "MediaTek Europe hiring"],
    },
    "ambarella": {
        "name": "Ambarella",
        "industry": "silicon",
        "gowork_id": None,
        "news_url": "https://www.ambarella.com/news/",
        "search_terms": ["Ambarella camera SoC", "Ambarella computer vision ADAS", "Ambarella hiring"],
    },
    "onsemi": {
        "name": "onsemi",
        "industry": "silicon",
        "gowork_id": None,
        "news_url": "https://www.onsemi.com/company/news-media",
        "search_terms": ["onsemi image sensor ADAS", "onsemi Hyperlux camera", "onsemi Poland hiring"],
    },
    "infineon": {
        "name": "Infineon",
        "industry": "silicon",
        "gowork_id": None,
        "news_url": "https://www.infineon.com/cms/en/about-infineon/press/",
        "search_terms": ["Infineon automotive embedded", "Infineon Poland", "Infineon hiring"],
    },
    "stmicro": {
        "name": "STMicroelectronics",
        "industry": "silicon",
        "gowork_id": None,
        "news_url": "https://newsroom.st.com/",
        "search_terms": ["STMicroelectronics automotive embedded", "STMicro Linux BSP", "STMicro Poland hiring"],
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
            "id": "company-think",
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
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', text, flags=re.S)
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.S)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# ── NVIDIA Developer Forum Search (Discourse JSON API) ────────────────────

NVIDIA_FORUM_BASE = "https://forums.developer.nvidia.com"

def search_nvidia_devforum(keywords, category_ids=None, max_results=8):
    """Search NVIDIA Developer Forums for topics matching keywords.
    Uses Discourse JSON search API."""
    results = []
    seen_ids = set()
    if category_ids is None:
        category_ids = [486, 487, 632, 15, 636]

    for query in keywords[:3]:
        for cat_id in category_ids[:4]:
            search_q = urllib.parse.quote(f"{query} #c/{cat_id}")
            url = f"{NVIDIA_FORUM_BASE}/search.json?q={search_q}&order=latest"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read())
                for topic in data.get("topics", []):
                    tid = topic.get("id")
                    if tid in seen_ids:
                        continue
                    seen_ids.add(tid)
                    slug = topic.get("slug", "")
                    results.append({
                        "title": topic.get("title", "")[:200],
                        "url": f"{NVIDIA_FORUM_BASE}/t/{slug}/{tid}",
                        "category_id": topic.get("category_id"),
                        "views": topic.get("views", 0),
                        "replies": topic.get("posts_count", 1) - 1,
                        "date": topic.get("created_at", "")[:10],
                        "last_posted": topic.get("last_posted_at", "")[:10],
                    })
            except Exception as e:
                log(f"  NVIDIA forum search error: {e}")
            time.sleep(0.5)
        if len(results) >= max_results:
            break
        time.sleep(1)

    results.sort(key=lambda r: r.get("last_posted", ""), reverse=True)
    return results[:max_results]


def fetch_url(url, timeout=20):
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


# ── Data fetchers ──────────────────────────────────────────────────────────

def search_ddg_news(query, max_results=5):
    """Search DuckDuckGo for recent news."""
    results = []
    try:
        q = urllib.parse.quote_plus(query)
        url = f"https://html.duckduckgo.com/html/?q={q}&t=h_&iar=news&ia=news"
        raw = fetch_url(url, timeout=15)
        if not raw:
            return results

        # Extract result snippets
        blocks = re.findall(r'class="result__body">(.*?)</div>', raw, re.S)
        if not blocks:
            blocks = re.findall(r'class="result__snippet">(.*?)</[as]', raw, re.S)

        for block in blocks[:max_results]:
            text = strip_html(block)[:300]
            if text:
                results.append({"query": query, "snippet": text})
    except Exception as e:
        log(f"  DDG search error: {e}")
    return results


def fetch_gowork_reviews(gowork_id, max_reviews=5):
    """Fetch GoWork reviews for rating context."""
    reviews = []
    try:
        url = f"https://www.gowork.pl/opinie_czytaj,{gowork_id}"
        raw = fetch_url(url, timeout=15)
        if not raw:
            return reviews

        # Extract review blocks
        blocks = re.findall(r'<div[^>]*class="[^"]*opinion-text[^"]*"[^>]*>(.*?)</div>', raw, re.S)
        for block in blocks[:max_reviews]:
            text = strip_html(block)[:300]
            if text and len(text) > 20:
                reviews.append(text)

        # Try to extract rating
        rating_match = re.search(r'class="[^"]*rating-value[^"]*"[^>]*>([\d.]+)', raw)
        if rating_match:
            reviews.insert(0, f"[Rating: {rating_match.group(1)}/5]")
    except Exception as e:
        log(f"  GoWork error: {e}")
    return reviews


def fetch_hackernews(company_name, max_results=3):
    """Search Hacker News for recent discussions."""
    results = []
    try:
        q = urllib.parse.quote_plus(company_name)
        url = f"https://hn.algolia.com/api/v1/search_by_date?query={q}&tags=story&hitsPerPage={max_results}"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            for hit in data.get("hits", [])[:max_results]:
                results.append({
                    "title": hit.get("title", ""),
                    "points": hit.get("points", 0),
                    "comments": hit.get("num_comments", 0),
                    "date": hit.get("created_at", "")[:10],
                })
    except Exception as e:
        log(f"  HN search error: {e}")
    return results


# ── Data aggregation ───────────────────────────────────────────────────────

def fetch_company_intel(slug, company):
    """Fetch fresh intelligence data for one company."""
    data = {
        "name": company["name"],
        "industry": company["industry"],
        "news": [],
        "gowork_reviews": [],
        "hackernews": [],
        "news_page": "",
    }

    # 1. DDG news search
    for term in company.get("search_terms", [])[:2]:
        log(f"  DDG: {term}")
        results = search_ddg_news(term)
        data["news"].extend(results)
        time.sleep(2)
    log(f"  News: {len(data['news'])} results")

    # 2. GoWork reviews
    if company.get("gowork_id"):
        log(f"  GoWork: {company['gowork_id']}")
        data["gowork_reviews"] = fetch_gowork_reviews(company["gowork_id"])
        log(f"  GoWork: {len(data['gowork_reviews'])} reviews")
        time.sleep(1)

    # 3. Hacker News
    log(f"  Hacker News: {company['name']}")
    data["hackernews"] = fetch_hackernews(company["name"])
    log(f"  HN: {len(data['hackernews'])} threads")
    time.sleep(1)

    # 4. Company news page
    if company.get("news_url"):
        log(f"  News page: {company['news_url'][:50]}...")
        raw = fetch_url(company["news_url"], timeout=15)
        if raw:
            data["news_page"] = strip_html(raw)[:3000]
            log(f"  News page: {len(data['news_page'])} chars")

    # 5. NVIDIA Developer Forum (camera/ISP/CSI topics)
    if slug == "nvidia":
        log(f"  NVIDIA DevForum: searching CSI/camera topics...")
        data["nvidia_devforum"] = search_nvidia_devforum(
            keywords=["CSI camera driver", "ISP pipeline", "Argus libargus"],
            category_ids=[486, 487, 632, 636, 15],
            max_results=8,
        )
        log(f"  NVIDIA DevForum: {len(data['nvidia_devforum'])} topics")

    # 6. Previous intel data
    latest_intel = INTEL_DIR / "latest-intel.json"
    if latest_intel.exists():
        try:
            intel_data = json.load(open(latest_intel))
            company_data = intel_data.get("companies", {}).get(slug, {})
            if company_data:
                data["previous_intel"] = {
                    "sentiment": company_data.get("sentiment", "unknown"),
                    "sentiment_score": company_data.get("sentiment_score", 0),
                    "red_flags": company_data.get("red_flags", [])[:3],
                    "growth_signals": company_data.get("growth_signals", [])[:3],
                    "scan_time": intel_data.get("meta", {}).get("timestamp", ""),
                }
        except Exception:
            pass

    return data


# ── Deep analysis prompt ───────────────────────────────────────────────────

def get_intel_prompt(slug, company, intel_data, focus=None):
    """Build a deep corporate intelligence prompt, optionally focused on a sub-topic."""
    name = company["name"]
    industry = company["industry"]

    # Format news
    news_text = ""
    for n in intel_data.get("news", [])[:8]:
        news_text += f"  • {n.get('snippet', '')[:200]}\n"

    # Format GoWork
    gowork_text = "\n".join(f"  • {r}" for r in intel_data.get("gowork_reviews", [])[:5])

    # Format HN
    hn_text = ""
    for h in intel_data.get("hackernews", [])[:5]:
        hn_text += f"  • [{h.get('date','')}] {h.get('title','')} ({h.get('points',0)}pts, {h.get('comments',0)} comments)\n"

    # News page excerpt
    news_page = intel_data.get("news_page", "")[:1500]

    # Previous intel
    prev_text = ""
    prev = intel_data.get("previous_intel", {})
    if prev:
        prev_text = f"""
Previous intelligence assessment:
  Sentiment: {prev.get('sentiment', 'N/A')} (score: {prev.get('sentiment_score', 'N/A')})
  Red flags: {', '.join(prev.get('red_flags', ['none'])[:3])}
  Growth signals: {', '.join(prev.get('growth_signals', ['none'])[:3])}
  Last scan: {prev.get('scan_time', 'N/A')}"""

    # Profile
    profile_ctx = ""
    if PROFILE_FILE.exists():
        try:
            pf = json.load(open(PROFILE_FILE))
            profile_ctx = f"\nAnalyst context: {pf.get('role', 'embedded Linux engineer')} tracking {name} for career opportunity and industry intelligence."
        except Exception:
            pass

    # Format NVIDIA Developer Forum topics
    nvidia_forum_text = ""
    for nf in intel_data.get("nvidia_devforum", [])[:6]:
        nvidia_forum_text += f"  • [{nf.get('date','')}] {nf.get('title','')[:120]} ({nf.get('replies',0)} replies, {nf.get('views',0)} views)\n"

    # Data block (shared between focused and unfocused prompts)
    data_block = f"""Recent news and discussions:
{news_text or '  (no recent news found)'}

Employee reviews (GoWork.pl):
{gowork_text or '  (no reviews available)'}

Hacker News discussions:
{hn_text or '  (no recent HN threads)'}

{f"NVIDIA Developer Forum (Jetson/DRIVE camera & ISP):{chr(10)}{nvidia_forum_text}" if nvidia_forum_text else ""}
{f"Company news page excerpt:{chr(10)}{news_page[:1000]}" if news_page else ""}
{prev_text}"""

    # ── Focused prompt ──
    if focus and focus in COMPANY_FOCUS_AREAS:
        fa = COMPANY_FOCUS_AREAS[focus]
        focus_label = fa["label"]
        focus_sections = fa["sections"].format(name=name)

        system = f"""You are a senior corporate intelligence analyst specializing in {industry} companies.
Write ONLY in English. This is a FOCUSED deep-dive on {focus_label} for {name}.
Go deeper than a general overview — provide specific, actionable intelligence.
Think about second-order effects and implications for embedded Linux engineers.
Date: {datetime.now().strftime('%Y-%m-%d')}"""

        user = f"""FOCUSED corporate intelligence: {focus_label} for {name} ({industry})
{profile_ctx}

{data_block}

Perform a FOCUSED DEEP ANALYSIS on {focus_label}:
{focus_sections}

Be SPECIFIC — name events, dates, people, products, technologies when possible.
Go DEEPER than a general overview. Provide actionable intelligence.
Target: 500-700 words. English only."""

        return system, user

    # ── Full (unfocused) prompt ──
    system = f"""You are a senior corporate intelligence analyst specializing in {industry} companies.
Write ONLY in English. Provide deep, strategic analysis of corporate developments.
Think about second-order effects: how strategy changes affect hiring, technology direction,
competitive positioning, and the embedded Linux / semiconductor engineering job market.
Date: {datetime.now().strftime('%Y-%m-%d')}"""

    user = f"""Deep corporate intelligence analysis for {name} ({industry}):
{profile_ctx}

{data_block}

Perform a DEEP CORPORATE STRATEGY ANALYSIS:

1. STRATEGIC DIRECTION
   What is {name}'s current strategy? Recent pivots, new initiatives?
   Where are they investing R&D resources? What markets are they entering/exiting?

2. FINANCIAL HEALTH & RESTRUCTURING
   Any signs of cost-cutting, layoffs, hiring freezes?
   Revenue trajectory signals, margin pressures, capex changes?
   Restructuring plans, division spin-offs, asset sales?

3. M&A AND PARTNERSHIPS
   Recent acquisitions, divestitures, or partnership announcements?
   Potential M&A targets or acquirers? Activist investor activity?

4. LEADERSHIP & CULTURE
   Executive changes, board movements?
   Employee sentiment from reviews — cultural red flags or improvements?
   Management credibility and communication quality.

5. COMPETITIVE POSITIONING
   Market share trends in key segments.
   Competitive threats: who is gaining, who is losing?
   Technology moat: IP, patents, ecosystem lock-in.

6. TECHNOLOGY BETS
   What technology platforms is {name} betting on?
   AI integration, new chip architectures, software-defined products?
   Relevance to embedded Linux / camera / ADAS / SoC engineering?

7. POLAND / EUROPE PRESENCE
   Office expansion or contraction in Poland?
   European operations strategy — Brexit effects, EU regulation impact.
   Local hiring signals.

8. RISK MATRIX
   Top 3 risks for {name} in the next 12 months.
   Geopolitical risks, technology risks, execution risks.

Be SPECIFIC — name events, dates, people, products when possible.
Target: 600-800 words. English only."""

    return system, user


# ── Per-company think ──────────────────────────────────────────────────────

def think_one_company(slug, focus=None):
    """Deep chain-of-thought corporate intelligence on one company.
    
    If focus is given, produces a narrow deep-dive on that specific dimension.
    """
    if slug not in COMPANIES:
        log(f"Unknown company: {slug}")
        log(f"Available: {', '.join(sorted(COMPANIES.keys()))}")
        sys.exit(1)

    if focus and focus not in COMPANY_FOCUS_AREAS:
        log(f"Unknown focus area: {focus}")
        log(f"Available: {', '.join(sorted(COMPANY_FOCUS_AREAS.keys()))}")
        sys.exit(1)

    company = COMPANIES[slug]
    t_start = time.time()

    focus_label = f" [{COMPANY_FOCUS_AREAS[focus]['label']}]" if focus else ""
    log(f"{'='*60}")
    log(f"COMPANY THINK — {company['name']}{focus_label}")
    log(f"{'='*60}")

    THINK_DIR.mkdir(parents=True, exist_ok=True)

    # Check if already analyzed today
    today = datetime.now().strftime("%Y%m%d")
    suffix = f"-{focus}" if focus else ""
    out_file = THINK_DIR / f"{slug}{suffix}-{today}.json"
    if out_file.exists():
        log(f"Already analyzed today: {out_file.name}")
        return

    # 1. Fetch intel data (reuse cached data from unfocused run if available)
    cached_unfocused = THINK_DIR / f"{slug}-{today}.json"
    if focus and cached_unfocused.exists():
        try:
            cached = json.load(open(cached_unfocused))
            intel_data = cached.get("_intel_cache", None)
        except Exception:
            intel_data = None
    else:
        intel_data = None

    if intel_data is None:
        log("\n── Gathering intelligence ──")
        intel_data = fetch_company_intel(slug, company)

    # 2. Deep LLM analysis
    log(f"\n── Deep analysis{focus_label} (chain-of-thought enabled) ──")
    system, user = get_intel_prompt(slug, company, intel_data, focus=focus)
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
            "focus_label": COMPANY_FOCUS_AREAS[focus]["label"] if focus else None,
            "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "date": today,
            "duration_s": round(elapsed),
        },
        "intel_summary": {
            "news_count": len(intel_data.get("news", [])),
            "gowork_count": len(intel_data.get("gowork_reviews", [])),
            "hn_count": len(intel_data.get("hackernews", [])),
            "has_previous": bool(intel_data.get("previous_intel")),
        },
        "analysis": analysis,
    }

    with open(out_file, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    log(f"\nSaved: {out_file.name} ({out_file.stat().st_size:,} bytes) in {elapsed:.0f}s")


# ── Summary aggregation ───────────────────────────────────────────────────

def think_summary():
    """Aggregate all daily company analyses into an intelligence overview."""
    t_start = time.time()

    log(f"{'='*60}")
    log(f"COMPANY THINK SUMMARY — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    log(f"{'='*60}")

    THINK_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")

    analyses = []
    focused_analyses = []
    for slug in sorted(COMPANIES.keys()):
        # Load main (unfocused) analysis
        think_file = THINK_DIR / f"{slug}-{today}.json"
        if think_file.exists():
            try:
                analyses.append(json.load(open(think_file)))
            except Exception as e:
                log(f"  {slug}: read error — {e}")
        # Load focused analyses
        for focus_key in COMPANY_FOCUS_AREAS:
            focused_file = THINK_DIR / f"{slug}-{focus_key}-{today}.json"
            if focused_file.exists():
                try:
                    focused_analyses.append(json.load(open(focused_file)))
                except Exception:
                    pass

    total = len(analyses) + len(focused_analyses)
    log(f"\nFound {len(analyses)} main + {len(focused_analyses)} focused analyses for today ({total} total)")

    if len(analyses) < 5:
        log("Not enough analyses to summarize (need ≥5). Skipping.")
        return

    # Build combined text
    summaries = []
    for a in analyses:
        name = a["meta"]["name"]
        industry = a["meta"]["industry"]
        analysis_text = a.get("analysis", "")[:500]
        # Append focused excerpts if available
        focus_excerpts = []
        for fa in focused_analyses:
            if fa["meta"]["company"] == a["meta"]["company"]:
                fl = fa["meta"].get("focus_label", fa["meta"].get("focus", ""))
                focus_excerpts.append(f"  [{fl}]: {fa.get('analysis', '')[:250]}")
        focus_block = "\n".join(focus_excerpts) if focus_excerpts else ""
        entry = f"### {name} ({industry})\n{analysis_text}"
        if focus_block:
            entry += f"\nFocused deep-dives:\n{focus_block}"
        summaries.append(entry)

    company_block = "\n\n---\n\n".join(summaries)

    system = """You are the Chief Corporate Intelligence Officer. Write ONLY in English.
Synthesize per-company deep analyses into a comprehensive market intelligence overview.
The reader is a senior embedded Linux engineer tracking these companies for career opportunities."""

    user = f"""Today's deep corporate analyses across {len(analyses)} companies:

{company_block}

Create a CORPORATE INTELLIGENCE EXECUTIVE BRIEF:

1. SECTOR HEALTH OVERVIEW
   Overall sentiment across tracked companies.
   Which sectors are expanding vs contracting?

2. RED FLAGS
   Top 3-5 concerning signals across all companies.
   Layoff risks, restructuring, leadership instability.

3. GROWTH SIGNALS
   Top 3-5 positive indicators.
   New investments, hiring surges, technology breakthroughs.

4. M&A AND DEAL ACTIVITY
   Any acquisition/partnership signals across tracked companies?
   Industry consolidation trends.

5. TECHNOLOGY EVOLUTION
   Cross-cutting technology trends visible across companies.
   Platform shifts, architectural changes, new compute paradigms.

6. CAREER INTELLIGENCE (3-4 sentences)
   Net hiring outlook across all tracked companies.
   Best companies to target for embedded Linux roles.
   Timing recommendations.

7. WATCHLIST UPDATES
   Companies to watch more closely this week and why.

Target: 600-900 words. English only."""

    log("\n── Running summary analysis (chain-of-thought) ──")
    summary = call_ollama(system, user, temperature=0.4, max_tokens=5000, think=True)

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

    # Cleanup (keep 14 days ≈ 500+ files with focused analyses)
    all_files = sorted(THINK_DIR.glob("*.json"))
    if len(all_files) > 600:
        for f in all_files[:-600]:
            if f.name.startswith("latest"):
                continue
            f.unlink()
            log(f"  Pruned: {f.name}")

    log(f"\nSummary complete in {elapsed:.0f}s")


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Deep per-company corporate intelligence")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--company', '-c', help="Company slug to analyze")
    group.add_argument('--summary', '-s', action='store_true', help="Generate daily intelligence summary")
    group.add_argument('--list', '-l', action='store_true', help="List available companies")
    parser.add_argument('--focus', '-f', help="Focus area for deep-dive (strategy, tech-talent)")
    args = parser.parse_args()

    if args.list:
        print("\nCompanies:")
        for slug, info in sorted(COMPANIES.items()):
            print(f"  {slug:12s}  {info['name']:28s}  [{info['industry']}]")
        print(f"\nFocus areas:")
        for fk, fv in COMPANY_FOCUS_AREAS.items():
            print(f"  {fk:15s}  {fv['label']}")
        return

    if args.summary:
        think_summary()
    else:
        think_one_company(args.company, focus=args.focus)


if __name__ == "__main__":
    main()
