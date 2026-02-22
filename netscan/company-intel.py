#!/usr/bin/env python3
"""
company-intel.py — Deep company intelligence tracker
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Deepens intelligence on tracked companies beyond basic GoWork reviews.

Sources per company:
  - GoWork.pl reviews (new reviews since last scan)
  - Company news pages (press releases, blog)
  - layoffs.fyi (layoff events)
  - DuckDuckGo news search (recent articles)
  - LinkedIn activity signals (public company page)

LLM analysis per company:
  - Sentiment trend (improving/declining/stable)
  - Red flags (layoffs, reorgs, bad reviews)
  - Growth signals (hiring, new offices, products)
  - Relevance to user (ADAS, camera, embedded Linux)

Output: /opt/netscan/data/intel/
  - intel-YYYYMMDD.json       (daily deep-dive)
  - company-intel-deep.json   (rolling knowledge base)
  - latest-intel.json         (symlink)

Cron: 30 2 * * * flock -w 1200 /tmp/ollama-gpu.lock python3 /opt/netscan/company-intel.py
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

INTEL_DIR = Path("/opt/netscan/data/intel")
CAREER_DIR = Path("/opt/netscan/data/career")
INTEL_DB = INTEL_DIR / "company-intel-deep.json"
PROFILE_FILE = Path("/opt/netscan/profile-private.json")

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
DDG_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

# Companies to track — from career-scan COMPANIES + GoWork entity IDs
COMPANIES = {
    "nvidia": {
        "name": "NVIDIA",
        "gowork_id": "21622584",
        "news_url": "https://nvidianews.nvidia.com/",
        "search_terms": ["NVIDIA Poland", "NVIDIA embedded", "NVIDIA automotive"],
        "industry": "silicon",
    },
    "google": {
        "name": "Google",
        "gowork_id": "949234",
        "news_url": "https://blog.google/",
        "search_terms": ["Google Poland", "Google embedded hardware"],
        "industry": "faang",
    },
    "amd": {
        "name": "AMD",
        "gowork_id": "26904732",
        "news_url": "https://www.amd.com/en/newsroom.html",
        "search_terms": ["AMD Poland", "AMD embedded"],
        "industry": "silicon",
    },
    "intel": {
        "name": "Intel",
        "gowork_id": "930747",
        "news_url": "https://newsroom.intel.com/",
        "search_terms": ["Intel Poland", "Intel embedded", "Intel layoffs"],
        "industry": "silicon",
    },
    "samsung": {
        "name": "Samsung Electronics",
        "gowork_id": "21451047",
        "news_url": "https://news.samsung.com/global/",
        "search_terms": ["Samsung Poland R&D", "Samsung semiconductor"],
        "industry": "silicon",
    },
    "qualcomm": {
        "name": "Qualcomm",
        "gowork_id": "20727487",
        "news_url": "https://www.qualcomm.com/news",
        "search_terms": ["Qualcomm Poland", "Qualcomm automotive", "Snapdragon Ride"],
        "industry": "silicon",
    },
    "arm": {
        "name": "Arm",
        "gowork_id": "23971017",
        "news_url": "https://newsroom.arm.com/",
        "search_terms": ["Arm Poland", "Arm automotive"],
        "industry": "silicon",
    },
    "harman": {
        "name": "HARMAN International",
        "gowork_id": "1036892",
        "news_url": "https://www.harman.com/news",
        "search_terms": ["HARMAN ADAS", "HARMAN automotive", "HARMAN ZF acquisition"],
        "industry": "automotive",
    },
    "ericsson": {
        "name": "Ericsson",
        "gowork_id": "8528",
        "news_url": "https://www.ericsson.com/en/newsroom",
        "search_terms": ["Ericsson Poland", "Ericsson R&D"],
        "industry": "telecom",
    },
    "tcl": {
        "name": "TCL Research Europe",
        "gowork_id": "23966243",
        "news_url": None,
        "search_terms": ["TCL Research Europe Poland"],
        "industry": "consumer_electronics",
    },
    "fujitsu": {
        "name": "Fujitsu",
        "gowork_id": "365816",
        "news_url": "https://www.fujitsu.com/global/about/resources/news/",
        "search_terms": ["Fujitsu Poland", "Fujitsu automotive"],
        "industry": "telecom",
    },
    "thales": {
        "name": "Thales",
        "gowork_id": "239192",
        "news_url": "https://www.thalesgroup.com/en/worldwide/group/press_release",
        "search_terms": ["Thales Poland", "Thales defence embedded"],
        "industry": "defence",
    },
    "amazon": {
        "name": "Amazon",
        "gowork_id": "1026920",
        "news_url": None,
        "search_terms": ["Amazon Development Center Poland"],
        "industry": "faang",
    },
}


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

def strip_html(html):
    """Remove HTML tags, scripts, styles; return clean text."""
    if not html:
        return ""
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def call_ollama(system_prompt, user_prompt, temperature=0.3, max_tokens=2000):
    """Call Ollama for LLM analysis."""
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

    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens},
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


# ── GoWork Scraping ────────────────────────────────────────────────────────

def fetch_gowork_reviews(entity_id, max_pages=2):
    """Fetch recent reviews from GoWork.pl for a company entity."""
    reviews = []
    for page in range(1, max_pages + 1):
        url = f"https://www.gowork.pl/opinie_czytaj,{entity_id},page,{page}"
        html = fetch_url(url, timeout=20)
        if not html:
            break

        # Extract reviews by date pattern (DD.MM.YYYY)
        blocks = re.split(r'(?=\d{2}\.\d{2}\.\d{4})', html)
        for block in blocks[1:]:  # skip pre-first-date content
            date_m = re.match(r'(\d{2}\.\d{2}\.\d{4})', block)
            if not date_m:
                continue
            date_str = date_m.group(1)
            text = strip_html(block)[:500]
            reviews.append({"date": date_str, "text": text})

        time.sleep(2)  # rate limit

    return reviews


# ── DuckDuckGo News Search ─────────────────────────────────────────────────

def search_ddg_news(query, max_results=5):
    """Search DuckDuckGo for recent news about a company."""
    results = []
    try:
        encoded = urllib.parse.quote(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded}&t=h_&iar=news&ia=news"
        html = fetch_url(url, timeout=20)
        if not html:
            return results

        # Extract result snippets
        # DuckDuckGo HTML results have class="result__snippet"
        snippets = re.findall(
            r'class="result__snippet"[^>]*>(.*?)</[^>]+>',
            html, re.DOTALL
        )
        titles = re.findall(
            r'class="result__a"[^>]*>(.*?)</a>',
            html, re.DOTALL
        )
        links = re.findall(
            r'class="result__url"[^>]*href="([^"]*)"',
            html, re.DOTALL
        )

        for i in range(min(max_results, len(snippets))):
            results.append({
                "title": strip_html(titles[i]) if i < len(titles) else "",
                "snippet": strip_html(snippets[i]),
                "url": links[i] if i < len(links) else "",
            })

    except Exception as e:
        log(f"  DDG search error: {e}")

    return results


# ── Layoffs Tracking ───────────────────────────────────────────────────────

def check_layoffs_fyi(company_name):
    """Check layoffs.fyi for recent layoff events."""
    url = "https://layoffs.fyi/"
    html = fetch_url(url, timeout=20)
    if not html:
        return []

    events = []
    # layoffs.fyi has a table; search for company name
    name_lower = company_name.lower()
    lines = html.split("\n")
    for line in lines:
        if name_lower in line.lower():
            text = strip_html(line)[:300]
            if text.strip():
                events.append(text)

    return events[:3]  # last 3 mentions


# ── Per-Company Analysis ───────────────────────────────────────────────────

def analyze_company(key, company, db_entry):
    """Gather intel and run LLM analysis for one company."""
    log(f"Analyzing: {company['name']}")
    intel = {"key": key, "name": company["name"], "industry": company["industry"]}

    # 1. GoWork reviews
    reviews = fetch_gowork_reviews(company["gowork_id"])
    intel["gowork_reviews"] = reviews[:10]
    intel["gowork_review_count"] = len(reviews)
    log(f"  GoWork: {len(reviews)} reviews")

    # 2. News search
    all_news = []
    for term in company["search_terms"][:2]:  # limit to 2 searches
        news = search_ddg_news(term)
        all_news.extend(news)
        time.sleep(2)
    intel["news"] = all_news[:8]
    log(f"  News: {len(all_news)} articles")

    # 3. Layoffs check
    layoffs = check_layoffs_fyi(company["name"])
    intel["layoffs_mentions"] = layoffs
    log(f"  Layoffs.fyi: {len(layoffs)} mentions")

    # 4. Company news page (if available)
    company_news_text = ""
    if company.get("news_url"):
        html = fetch_url(company["news_url"], timeout=20)
        if html:
            company_news_text = strip_html(html)[:3000]
        time.sleep(1)

    # 5. Previous intel from DB
    prev_rating = None
    prev_sentiment = None
    if db_entry:
        prev_snapshots = db_entry.get("snapshots", [])
        if prev_snapshots:
            last = prev_snapshots[-1]
            prev_rating = last.get("gowork_rating")
            prev_sentiment = last.get("sentiment")

    # 6. LLM analysis
    system = """You are a corporate intelligence analyst specializing in tech companies
in Poland, with focus on embedded systems and automotive sectors.
Analyze the provided data and produce a structured intelligence brief.
Respond in JSON format with these keys:
- sentiment: "positive" | "negative" | "neutral" | "mixed"
- sentiment_score: -5 to +5 integer
- red_flags: list of concerning signals
- growth_signals: list of positive indicators
- adas_relevance: how relevant to ADAS/camera/embedded work (0-10)
- key_developments: list of 2-3 most important recent developments
- recommendation: one-line action item for the user
Output ONLY valid JSON, no markdown. /no_think"""

    review_text = "\n".join(f"  [{r['date']}] {r['text'][:200]}" for r in reviews[:5])
    news_text = "\n".join(f"  • {n['title']}: {n['snippet'][:150]}" for n in all_news[:5])

    prompt = f"""Company: {company['name']} ({company['industry']})
GoWork entity: {company['gowork_id']}
Previous sentiment: {prev_sentiment or 'N/A'}, previous GoWork rating: {prev_rating or 'N/A'}

Recent GoWork reviews ({len(reviews)} found):
{review_text or '  (none)'}

Recent news:
{news_text or '  (none)'}

Company news page excerpt:
{company_news_text[:1500] if company_news_text else '  (unavailable)'}

Layoffs.fyi mentions:
{chr(10).join(f'  • {l}' for l in layoffs) if layoffs else '  (none)'}

Context: The user is a Principal Embedded SW Engineer at HARMAN (Samsung subsidiary),
working on automotive camera drivers (V4L2, MIPI CSI-2, ADAS DMS/OMS).
They track these companies for career opportunities and industry intelligence."""

    analysis_raw = call_ollama(system, prompt, temperature=0.2, max_tokens=1500)

    # Parse LLM JSON response
    analysis = {}
    if analysis_raw:
        try:
            # Try to extract JSON from response
            json_match = re.search(r'\{[^{}]*\}', analysis_raw, re.DOTALL)
            if json_match:
                analysis = json.loads(json_match.group())
            else:
                analysis = json.loads(analysis_raw)
        except json.JSONDecodeError:
            analysis = {"raw_analysis": analysis_raw[:1000]}

    intel["analysis"] = analysis
    return intel


# ── DB Management ──────────────────────────────────────────────────────────

def load_db():
    """Load the rolling intelligence database."""
    if INTEL_DB.exists():
        try:
            return json.load(open(INTEL_DB))
        except Exception:
            pass
    return {"companies": {}, "version": 1}

def save_db(db):
    """Save DB, keeping last 90 snapshots per company."""
    for key, entry in db.get("companies", {}).items():
        snaps = entry.get("snapshots", [])
        if len(snaps) > 90:
            entry["snapshots"] = snaps[-90:]
    with open(INTEL_DB, "w") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

    dt = datetime.now()
    today = dt.strftime("%Y-%m-%d")
    print(f"[{dt.strftime('%Y-%m-%d %H:%M:%S')}] company-intel starting", flush=True)

    INTEL_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    db = load_db()
    day_results = []

    for key, company in COMPANIES.items():
        db_entry = db.get("companies", {}).get(key)

        try:
            intel = analyze_company(key, company, db_entry)
            day_results.append(intel)

            # Update DB
            if key not in db.get("companies", {}):
                db.setdefault("companies", {})[key] = {"snapshots": []}

            db["companies"][key]["snapshots"].append({
                "date": today,
                "gowork_rating": None,  # will be populated if available
                "gowork_review_count": intel.get("gowork_review_count", 0),
                "sentiment": intel.get("analysis", {}).get("sentiment"),
                "sentiment_score": intel.get("analysis", {}).get("sentiment_score"),
                "red_flags": intel.get("analysis", {}).get("red_flags", []),
                "growth_signals": intel.get("analysis", {}).get("growth_signals", []),
                "adas_relevance": intel.get("analysis", {}).get("adas_relevance"),
            })

        except Exception as e:
            log(f"  ERROR analyzing {key}: {e}")
            day_results.append({"key": key, "name": company["name"], "error": str(e)})

        time.sleep(3)  # breathing room between companies

    # ── LLM cross-company summary ──
    log("Cross-company summary...")
    summary_items = []
    for r in day_results:
        a = r.get("analysis", {})
        summary_items.append(
            f"- {r['name']}: sentiment={a.get('sentiment','?')}, "
            f"score={a.get('sentiment_score','?')}, "
            f"adas_rel={a.get('adas_relevance','?')}, "
            f"flags={a.get('red_flags',[])} "
            f"signals={a.get('growth_signals',[])}"
        )

    summary_system = """You are a career intelligence advisor for an embedded Linux camera driver
engineer in Poland. Synthesize the company intelligence into actionable insights.
Be concise — bullet points only. /no_think"""

    summary_prompt = f"""Today's intelligence scan across {len(day_results)} companies:

{chr(10).join(summary_items)}

Provide:
1. Top 3 companies showing strongest positive signals for embedded/ADAS roles
2. Any companies with concerning red flags
3. Market mood: is the embedded/automotive sector in Poland hiring or contracting?
4. One specific action the user should consider this week"""

    cross_summary = call_ollama(summary_system, summary_prompt, temperature=0.3, max_tokens=1500)

    # ── Save output ──
    duration = int(time.time() - t0)
    output = {
        "meta": {
            "timestamp": dt.isoformat(timespec="seconds"),
            "duration_seconds": duration,
            "companies_analyzed": len(day_results),
            "companies_with_errors": sum(1 for r in day_results if "error" in r),
        },
        "companies": day_results,
        "cross_summary": cross_summary or "Summary unavailable.",
    }

    fname = f"intel-{dt.strftime('%Y%m%d')}.json"
    out_path = INTEL_DIR / fname
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    latest = INTEL_DIR / "latest-intel.json"
    latest.unlink(missing_ok=True)
    latest.symlink_to(fname)

    save_db(db)

    # Cleanup: keep last 60 daily reports
    reports = sorted(INTEL_DIR.glob("intel-2*.json"))
    for old in reports[:-60]:
        old.unlink(missing_ok=True)

    log(f"Saved: {out_path}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] company-intel done ({duration}s)", flush=True)


if __name__ == "__main__":
    main()
