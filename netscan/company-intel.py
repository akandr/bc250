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
  - 4programmers.net (Polish dev forum — employer opinions, career threads)
  - Reddit (r/embedded, r/semiconductor, r/cscareerquestionsEU, r/poland, …)
  - SemiWiki.com forum (semiconductor industry intel — silicon/auto companies)
  - Hacker News (company gossip, tech discussions via Algolia API)

Global sources (once per run, not per company):
  - HN "Who is Hiring" monthly thread — remote jobs matching embedded/ADAS/camera

LLM analysis per company:
  - Sentiment trend (improving/declining/stable)
  - Red flags (layoffs, reorgs, bad reviews)
  - Growth signals (hiring, new offices, products)
  - Community pulse (developer forum & industry chatter)
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
        "careers_urls": [
            "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite?locations=91c5e2a7058101c99fbb5b4e17044b0e&workerSubType=0c40f6bd1d8f10adf6dae161b458a5d0",
        ],
        "careers_location_filter": ["poland", "polska", "warsaw", "warszawa", "remote"],
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
        "careers_urls": [
            "https://jobs.intel.com/en/search-jobs/Poland/599/1/6695072/52/20/50/2",
        ],
        "careers_location_filter": ["poland", "polska", "gdańsk", "gdansk", "remote"],
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
        "careers_urls": [
            "https://careers.qualcomm.com/careers?location=Poland&pid=446700590469",
        ],
        "careers_location_filter": ["poland", "polska", "wrocław", "wroclaw", "remote"],
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
    "hexagon": {
        "name": "Hexagon / Leica Geosystems",
        "gowork_id": "70870",
        "news_url": "https://hexagon.com/newsroom",
        "search_terms": ["Hexagon Manufacturing Intelligence Poland", "Leica Geosystems Łódź", "Hexagon ADAS lidar"],
        "industry": "metrology",
        "ats_api": {
            "type": "brassring",
            "endpoint": "https://sjobs.brassring.com/TGnewUI/Search/Ajax/ProcessSortAndShowMoreJobs",
            "partnerId": "25872",
            "siteId": "5512",
        },
        "careers_urls": [
            "https://hexagon.com/company/careers/job-listings?locations=Poland",
        ],
        "careers_location_filter": ["łódź", "lódz", "lodz", "poland", "polska", "remote"],
    },
}


# ── Helpers ────────────────────────────────────────────────────────────────

def log(msg):
    print(f"  {msg}", flush=True)

def fetch_url(url, timeout=10):
    """Fetch URL, return text or None."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/json,*/*",
            "Accept-Language": "en-US,en;q=0.9,pl;q=0.8",
            "Accept-Encoding": "identity",
            "Referer": "https://www.google.com/",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "cross-site",
            "DNT": "1",
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


# ── ATS API Integrations ──────────────────────────────────────────────────

def _query_brassring_api(ats_cfg, loc_filter):
    """Query BrassRing ATS AJAX API directly — returns structured job list."""
    import ssl
    endpoint = ats_cfg["endpoint"]
    payload = json.dumps({
        "partnerId": ats_cfg["partnerId"],
        "siteId": ats_cfg["siteId"],
        "keyword": "",
        "location": "Poland",
    }).encode()
    req = urllib.request.Request(endpoint, data=payload, headers={
        "Content-Type": "application/json",
        "User-Agent": UA,
        "Accept": "application/json",
    })
    jobs = []
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            data = json.loads(resp.read())
        job_list = data.get("Jobs", {})
        if isinstance(job_list, dict):
            job_list = job_list.get("Job", [])
        if isinstance(job_list, dict):  # single result
            job_list = [job_list]
        if not isinstance(job_list, list):
            job_list = []

        for j in job_list:
            qs = {q["QuestionName"]: q["Value"] for q in j.get("Questions", [])}
            country = qs.get("formtext2", "").strip()
            title = qs.get("jobtitle", "").replace("&amp;", "&")
            reqid = qs.get("autoreq", "")
            link = j.get("Link", "")

            # Filter by location
            country_lower = country.lower()
            if any(lf in country_lower for lf in loc_filter):
                jobs.append({
                    "title": title[:120],
                    "location": country,
                    "job_id": reqid,
                    "url": link,
                    "source": "brassring_api",
                })

        log(f"  BrassRing API: {data.get('JobsCount', 0)} total jobs, {len(jobs)} match location filter")
    except Exception as e:
        log(f"  BrassRing API error: {e}")

    return jobs


# ── Careers Page Monitoring ────────────────────────────────────────────────

def check_careers_pages(company):
    """Find job listings for a company, filtered by target locations.

    Strategy:
    0. Query ATS API directly (BrassRing, Workday) — most reliable
    1. Try scraping configured careers_urls (works for server-rendered pages)
    2. Fall back to DDG search for jobs at this company in Poland/Łódź
    """
    loc_filter = company.get("careers_location_filter", ["łódź", "lodz", "poland"])
    # Also match HTML-mangled versions (DDG returns "Łó d ź" etc.)
    loc_filter_expanded = list(loc_filter) + [
        lf.replace("ó", "ó").replace("ł", "ł")  # HTML entities
        for lf in loc_filter
    ]
    # Add substring variants that survive HTML mangling
    for lf in list(loc_filter):
        # "łódź" → also match "lod" (ASCII core)
        ascii_core = lf.replace("ł", "l").replace("ó", "o").replace("ź", "z").replace("ż", "z").replace("ś", "s").replace("ń", "n")
        if ascii_core not in loc_filter_expanded and len(ascii_core) >= 3:
            loc_filter_expanded.append(ascii_core)
    loc_filter = list(set(loc_filter_expanded))
    company_name = company["name"].split("/")[0].strip()  # "Hexagon / Leica" → "Hexagon"
    all_jobs = []

    # ── Phase 0: ATS API (structured data — most reliable) ──
    ats_cfg = company.get("ats_api")
    if ats_cfg:
        ats_type = ats_cfg.get("type", "")
        if ats_type == "brassring":
            api_jobs = _query_brassring_api(ats_cfg, loc_filter)
            all_jobs.extend(api_jobs)
        # Future: elif ats_type == "workday": ...

    # ── Phase 1: Direct URL scraping (skip if ATS API found jobs) ──
    if not all_jobs:
        careers_urls = company.get("careers_urls", [])
        for url in careers_urls:
            html = fetch_url(url, timeout=25)
            if not html:
                continue
            text = strip_html(html)
            if len(text) < 50:
                continue

            jobs_found = _extract_jobs_from_text(text, loc_filter, url)
            all_jobs.extend(jobs_found)
            time.sleep(1)

    # ── Phase 2: DDG search fallback (only if no jobs found yet) ──
    if not all_jobs:
        ddg_queries = [
            f'"{company_name}" "Łódź" OR "Lodz" praca engineer',
            f'"{company_name}" Poland job engineer software',
        ]
        for q in ddg_queries:
            url = f"https://html.duckduckgo.com/html/?q={urllib.request.quote(q)}"
            html = fetch_url(url, timeout=20)
            if not html:
                time.sleep(2)
                continue

            # Extract DDG result titles and snippets
            titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, re.DOTALL)
            snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
            links = re.findall(r'class="result__a"[^>]*href="([^"]+)"', html)

            for idx in range(min(len(titles), len(snippets), 8)):
                title = strip_html(titles[idx]).strip()
                snippet = strip_html(snippets[idx]).strip()
                link = links[idx] if idx < len(links) else ""
                combined = f"{title} {snippet}".lower()

                # Must be about this company
                if company_name.lower() not in combined:
                    continue

                # Normalize for location matching (handle HTML-mangled Polish chars)
                combined_norm = re.sub(r'\s+', '', combined)  # collapse spaces: "łó d ź" → "łódź"

                # Must mention a target location and be a job posting
                is_job = any(kw in combined for kw in [
                    "engineer", "developer", "architect", "manager", "analyst",
                    "specialist", "lead", "job", "career", "hiring", "vacancy",
                    "praca", "stanowisko", "rekrutacja",
                ])
                has_location = any(lf in combined for lf in loc_filter) or \
                               any(lf in combined_norm for lf in loc_filter)

                if is_job and has_location:
                    all_jobs.append({
                        "title": title[:120],
                        "location": "Poland",
                        "job_id": "",
                        "url": link or q,
                        "source": "ddg_search",
                    })

            time.sleep(2)

    # Dedup by title similarity
    seen = set()
    deduped = []
    for j in all_jobs:
        key = re.sub(r'[^a-z0-9]+', '', j["title"].lower())[:40]
        if key not in seen:
            seen.add(key)
            deduped.append(j)

    return deduped[:20]


def _extract_jobs_from_text(text, loc_filter, source_url):
    """Extract job-like entries from page text, filtered by location."""
    jobs = []

    # Pattern 1: "Job Title  Location  JOBID NNN" (BrassRing style)
    for m in re.finditer(
        r'([A-Z][A-Za-zÀ-ž /,&()\-]{5,80}?)\s+'
        r'([A-ZÀ-ž][A-Za-zÀ-ž ,]+(?:Poland|Polska|Remote|Łódź|Lodz)[A-Za-zÀ-ž ,]*)\s*'
        r'(?:JOBID|Job ID|Req ID)?\s*([A-Z0-9]{3,15})?',
        text
    ):
        title = m.group(1).strip()
        location = m.group(2).strip()
        job_id = (m.group(3) or "").strip()
        if any(lf in location.lower() for lf in loc_filter):
            jobs.append({
                "title": title[:120],
                "location": location[:80],
                "job_id": job_id,
                "url": source_url,
            })

    # Pattern 2: role keywords near location keywords
    role_kws = ["engineer", "developer", "architect", "manager", "analyst",
                "scientist", "lead", "specialist", "designer", "technician"]
    chunks = re.split(r'[\n\r•·|]+', text)
    for i, chunk in enumerate(chunks):
        chunk_lower = chunk.lower().strip()
        if len(chunk_lower) < 10 or len(chunk_lower) > 200:
            continue
        loc_match = any(lf in chunk_lower for lf in loc_filter)
        if not loc_match:
            nearby = " ".join(chunks[max(0, i-1):i+2]).lower()
            loc_match = any(lf in nearby for lf in loc_filter)
        if loc_match and any(rk in chunk_lower for rk in role_kws):
            if not any(j["title"].lower()[:20] in chunk_lower for j in jobs):
                jobs.append({
                    "title": chunk.strip()[:120],
                    "location": "Poland",
                    "job_id": "",
                    "url": source_url,
                })

    return jobs


# ── 4programmers.net Forum Search ──────────────────────────────────────────

def search_4programmers(company_name, max_results=5):
    """Search 4programmers.net for employer opinions & career threads."""
    results = []
    # Two searches: employer opinions + general career discussion
    queries = [
        f"{company_name} opinie",
        f"{company_name} praca",
    ]
    for query in queries:
        try:
            encoded = urllib.parse.quote(query)
            url = f"https://4programmers.net/Search?q={encoded}"
            html = fetch_url(url, timeout=20)
            if not html:
                continue

            # Extract thread titles and snippets from search results
            # Threads appear as <a> links to /Forum/ paths with post text below
            threads = re.findall(
                r'<a[^>]*href="(https://4programmers\.net/Forum/[^"]*)"[^>]*>\s*(.*?)\s*</a>',
                html, re.DOTALL,
            )
            # Extract snippet text near the results
            snippets = re.findall(
                r'class="[^"]*search[^"]*"[^>]*>(.*?)</(?:div|p|li)>',
                html, re.DOTALL | re.IGNORECASE,
            )

            seen_urls = {r["url"] for r in results}
            for i, (href, title_raw) in enumerate(threads):
                if href in seen_urls:
                    continue
                title = strip_html(title_raw).strip()
                if not title or len(title) < 5:
                    continue
                # Identify valuable sections
                section = ""
                for sec in ("Opinie_o_pracodawcach", "Kariera", "Embedded",
                            "Off-Topic", "Flame"):
                    if sec.lower() in href.lower():
                        section = sec.replace("_", " ")
                        break
                snippet = strip_html(snippets[i]) if i < len(snippets) else ""
                results.append({
                    "title": title[:200],
                    "url": href,
                    "section": section,
                    "snippet": snippet[:300],
                })
                if len(results) >= max_results:
                    break

            time.sleep(2)  # rate limit
        except Exception as e:
            log(f"  4programmers search error: {e}")

    return results[:max_results]


# ── Reddit Search (via DuckDuckGo site: operator) ─────────────────────────

REDDIT_SUBREDDITS = [
    "r/poland", "r/embedded", "r/semiconductor",
    "r/cscareerquestionsEU", "r/ExperiencedDevs",
]

def search_reddit(company_name, search_terms=None, max_results=5):
    """Search Reddit via DuckDuckGo for company mentions in relevant subs."""
    results = []
    subs_str = " OR ".join(REDDIT_SUBREDDITS)
    queries = [f"site:reddit.com ({subs_str}) {company_name}"]
    if search_terms:
        queries.append(f"site:reddit.com {search_terms[0]}")

    for query in queries:
        try:
            encoded = urllib.parse.quote(query)
            url = f"https://html.duckduckgo.com/html/?q={encoded}"
            html = fetch_url(url, timeout=20)
            if not html:
                continue

            snippets = re.findall(
                r'class="result__snippet"[^>]*>(.*?)</[^>]+>',
                html, re.DOTALL,
            )
            titles = re.findall(
                r'class="result__a"[^>]*>(.*?)</a>',
                html, re.DOTALL,
            )
            links = re.findall(
                r'class="result__url"[^>]*href="([^"]*)"',
                html, re.DOTALL,
            )

            seen_urls = {r["url"] for r in results}
            for i in range(min(8, len(snippets))):
                link = links[i] if i < len(links) else ""
                if link in seen_urls or "reddit.com" not in link:
                    continue
                sub_m = re.search(r'reddit\.com/(r/\w+)', link)
                subreddit = sub_m.group(1) if sub_m else ""
                results.append({
                    "title": strip_html(titles[i]) if i < len(titles) else "",
                    "snippet": strip_html(snippets[i])[:300],
                    "url": link,
                    "subreddit": subreddit,
                })
                if len(results) >= max_results:
                    break

            time.sleep(2)
        except Exception as e:
            log(f"  Reddit DDG search error: {e}")

    # Fallback: old.reddit.com search if DDG returned nothing
    if not results:
        try:
            encoded = urllib.parse.quote(company_name)
            url = f"https://old.reddit.com/search?q={encoded}&sort=new&t=month&restrict_sr=off"
            html = fetch_url(url, timeout=20)
            if html:
                # old.reddit.com has <a class="search-title"> with titles
                title_links = re.findall(
                    r'class="search-title[^"]*"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                    html, re.DOTALL,
                )
                if not title_links:
                    # Alternative pattern: data-click-id
                    title_links = re.findall(
                        r'<a[^>]*href="(https://(?:old\.)?reddit\.com/r/[^"]*)"[^>]*>\s*<span[^>]*>(.*?)</span>',
                        html, re.DOTALL,
                    )
                for href, title_raw in title_links[:max_results]:
                    title = strip_html(title_raw).strip()
                    if not title:
                        continue
                    sub_m = re.search(r'reddit\.com/(r/\w+)', href)
                    subreddit = sub_m.group(1) if sub_m else ""
                    results.append({
                        "title": title[:200],
                        "snippet": "",
                        "url": href,
                        "subreddit": subreddit,
                    })
                time.sleep(2)
        except Exception as e:
            log(f"  Reddit fallback search error: {e}")

    return results[:max_results]


# ── SemiWiki Forum Search (via RSS feed + keyword filtering) ───────────────

SEMIWIKI_RSS = "https://semiwiki.com/forum/forums/-/index.rss"

def search_semiwiki(company_name, max_results=5):
    """Search SemiWiki forum for semiconductor industry intel on a company.
    Uses the public RSS feed and filters by company name keywords."""
    results = []
    try:
        rss = fetch_url(SEMIWIKI_RSS, timeout=20)
        if not rss:
            return results

        # Parse RSS items: <title> + <link> + <description>
        items = re.findall(
            r'<item>(.*?)</item>', rss, re.DOTALL
        )
        name_lower = company_name.lower()
        # Also match short forms (e.g. "Intel" in "Intel Foundry")
        keywords = [name_lower]
        # Add common abbreviations
        if name_lower == "samsung electronics":
            keywords.extend(["samsung", "exynos"])
        elif name_lower == "harman international":
            keywords.extend(["harman", "jbl"])
        elif name_lower == "arm":
            keywords.extend(["arm holdings", "cortex"])

        for item in items:
            title_m = re.search(r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', item)
            link_m = re.search(r'<link>(.*?)</link>', item)
            desc_m = re.search(r'<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>', item, re.DOTALL)

            title = title_m.group(1).strip() if title_m else ""
            link = link_m.group(1).strip() if link_m else ""
            desc = strip_html(desc_m.group(1)) if desc_m else ""

            text_lower = (title + " " + desc).lower()
            if any(kw in text_lower for kw in keywords):
                results.append({
                    "title": title[:200],
                    "snippet": desc[:300],
                    "url": link,
                })
                if len(results) >= max_results:
                    break

    except Exception as e:
        log(f"  SemiWiki search error: {e}")

    # If RSS had no matches, try DDG news as fallback (semiwiki blog posts)
    if not results:
        try:
            query = f"semiwiki {company_name}"
            encoded = urllib.parse.quote(query)
            url = f"https://html.duckduckgo.com/html/?q={encoded}&t=h_&iar=news&ia=news"
            html = fetch_url(url, timeout=20)
            if html:
                snippets = re.findall(
                    r'class="result__snippet"[^>]*>(.*?)</[^>]+>',
                    html, re.DOTALL,
                )
                titles = re.findall(
                    r'class="result__a"[^>]*>(.*?)</a>',
                    html, re.DOTALL,
                )
                links = re.findall(
                    r'class="result__url"[^>]*href="([^"]*)"',
                    html, re.DOTALL,
                )
                for i in range(min(max_results, len(snippets))):
                    link = links[i] if i < len(links) else ""
                    if "semiwiki" in link.lower():
                        results.append({
                            "title": strip_html(titles[i]) if i < len(titles) else "",
                            "snippet": strip_html(snippets[i])[:300],
                            "url": link,
                        })
                time.sleep(2)
        except Exception as e:
            log(f"  SemiWiki DDG fallback error: {e}")

    return results[:max_results]


# ── Hacker News Search (via Algolia API) ──────────────────────────────────

HN_API = "http://hn.algolia.com/api/v1"

def _hn_api(path, params, timeout=15):
    """Query HN Algolia API, return parsed JSON or empty dict."""
    url = f"{HN_API}/{path}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log(f"  HN API error ({path}): {e}")
        return {}


def search_hackernews(company_name, search_terms=None, max_results=5):
    """Search Hacker News for recent stories/comments mentioning a company.
    Uses Algolia Search API — stories from last 30 days."""
    import html as html_mod
    results = []
    seen_ids = set()
    ts_30d = int(time.time()) - 30 * 86400

    # Build search queries — company name + optional industry terms
    queries = [f'"{company_name}"']  # exact match to avoid username noise
    if search_terms:
        queries.extend(search_terms[:2])

    for query in queries:
        # Search stories first (higher signal)
        for tag_type in ["story", "comment"]:
            data = _hn_api("search_by_date", {
                "query": query,
                "tags": tag_type,
                "numericFilters": f"created_at_i>{ts_30d}",
                "hitsPerPage": "10",
            })
            for hit in data.get("hits", []):
                oid = hit["objectID"]
                if oid in seen_ids:
                    continue
                seen_ids.add(oid)

                if tag_type == "story":
                    title = hit.get("title", "")
                    text = hit.get("story_text") or ""
                    url = hit.get("url") or f"https://news.ycombinator.com/item?id={oid}"
                    points = hit.get("points", 0)
                    # Skip low-signal stories (< 5 points) unless title mentions company
                    if points < 5 and company_name.lower() not in title.lower():
                        continue
                else:
                    title = f"Comment by {hit.get('author', '?')}"
                    text = hit.get("comment_text", "")
                    url = f"https://news.ycombinator.com/item?id={oid}"
                    points = hit.get("points", 0)
                    # For comments, require company name in text (not just matching)
                    text_clean = re.sub(r'<[^>]+>', ' ', text).lower()
                    if company_name.lower() not in text_clean:
                        continue

                # Clean HTML from text
                snippet = re.sub(r'<[^>]+>', ' ', text)
                snippet = html_mod.unescape(snippet).strip()[:300]

                results.append({
                    "title": html_mod.unescape(title)[:200],
                    "snippet": snippet,
                    "url": url,
                    "points": points,
                    "type": tag_type,
                })

            if len(results) >= max_results:
                break
            time.sleep(0.3)  # be nice to API

        if len(results) >= max_results:
            break

    # Sort by points (stories first, then by engagement)
    results.sort(key=lambda r: (r["type"] != "story", -r.get("points", 0)))
    return results[:max_results]


def search_hn_who_is_hiring(max_results=15):
    """Find the latest HN 'Who is Hiring' thread and extract remote jobs
    matching embedded/ADAS/camera/Linux/automotive interests.

    Returns list of matching job postings with company, text, and URL."""
    import html as html_mod

    # Step 1: Find latest "Who is Hiring" thread (posted by whoishiring bot)
    data = _hn_api("search_by_date", {
        "query": "Ask HN: Who is hiring",
        "tags": "story,author_whoishiring",
        "hitsPerPage": "3",
    })
    threads = data.get("hits", [])
    if not threads:
        log("  HN Who is Hiring: no threads found")
        return [], None

    latest = threads[0]
    story_id = latest["objectID"]
    thread_title = latest.get("title", "")
    thread_url = f"https://news.ycombinator.com/item?id={story_id}"
    log(f"  HN Who is Hiring: {thread_title} ({latest.get('num_comments', 0)} comments)")

    # Step 2: Search comments for relevant keywords
    # These keywords match user's area of interest
    search_queries = [
        "remote embedded",
        "remote Linux kernel",
        "remote ADAS",
        "remote camera",
        "remote automotive",
        "remote firmware",
        "Europe embedded",
        "Europe automotive",
        "Poland",
        "remote C++ Linux",
        "remote driver development",
        "remote FPGA",
        "remote hardware",
    ]

    seen_ids = set()
    matches = []

    for q in search_queries:
        data = _hn_api("search", {
            "query": q,
            "tags": f"comment,story_{story_id}",
            "hitsPerPage": "10",
        })
        for hit in data.get("hits", []):
            oid = hit["objectID"]
            if oid in seen_ids:
                continue
            seen_ids.add(oid)

            raw_text = hit.get("comment_text", "")
            text = re.sub(r'<[^>]+>', ' ', raw_text)
            text = html_mod.unescape(text).strip()
            text_lower = text.lower()

            # Must be a top-level job posting (usually starts with company name)
            # Skip replies/discussions (they tend to be shorter)
            if len(text) < 100:
                continue

            # Check for REMOTE availability
            is_remote = any(kw in text_lower for kw in [
                "remote", "fully distributed", "work from anywhere",
                "wfh", "100% distributed",
            ])
            is_europe = any(kw in text_lower for kw in [
                "europe", "eu ", "poland", "germany", "uk ",
                "emea", "cet", "gmt", "utc",
            ])

            # Check for domain relevance
            relevance_kws = [
                "embedded", "firmware", "adas", "automotive",
                "camera", "v4l2", "mipi", "csi",
                "linux kernel", "driver", "bsp",
                "fpga", "rtos", "arm ", "aarch64",
                "c/c++", "c++", "yocto", "buildroot",
                "lidar", "radar", "sensor fusion",
                "robotics", "drone", "autonomous",
            ]
            relevance_score = sum(1 for kw in relevance_kws if kw in text_lower)

            if (is_remote or is_europe) and relevance_score >= 1:
                # Extract company name (usually first line or before | or —)
                first_line = text.split('\n')[0].strip()
                company = first_line.split('|')[0].split('—')[0].split('–')[0].strip()[:80]

                matches.append({
                    "company": company,
                    "text": text[:500],
                    "url": f"https://news.ycombinator.com/item?id={oid}",
                    "is_remote": is_remote,
                    "is_europe": is_europe,
                    "relevance_score": relevance_score,
                    "matched_query": q,
                })

        time.sleep(0.3)

    # Sort by relevance score (highest first), then Europe preference
    matches.sort(key=lambda m: (-m["relevance_score"], not m["is_europe"]))

    log(f"  HN Who is Hiring: {len(matches)} relevant remote/EU jobs found")
    return matches[:max_results], thread_url


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

    # 4. Careers page monitoring
    careers_jobs = check_careers_pages(company)
    intel["careers_openings"] = careers_jobs
    log(f"  Careers pages: {len(careers_jobs)} job(s) in target locations")

    # 5. Company news page (if available)
    company_news_text = ""
    if company.get("news_url"):
        html = fetch_url(company["news_url"], timeout=20)
        if html:
            company_news_text = strip_html(html)[:3000]
        time.sleep(1)

    # 5a. 4programmers.net forum threads (Polish dev community)
    fourp_results = search_4programmers(company["name"])
    intel["4programmers"] = fourp_results
    log(f"  4programmers.net: {len(fourp_results)} threads")

    # 5b. Reddit discussions (global embedded/semiconductor communities)
    reddit_results = search_reddit(company["name"], company.get("search_terms"))
    intel["reddit"] = reddit_results
    log(f"  Reddit: {len(reddit_results)} threads")

    # 5c. SemiWiki forum (semiconductor industry intel)
    # Only search SemiWiki for silicon/semiconductor companies
    semiwiki_results = []
    if company.get("industry") in ("silicon", "automotive", "defence"):
        semiwiki_results = search_semiwiki(company["name"])
    intel["semiwiki"] = semiwiki_results
    log(f"  SemiWiki: {len(semiwiki_results)} threads")

    # 5d. Hacker News (tech community gossip & discussions)
    hn_results = search_hackernews(company["name"], company.get("search_terms"))
    intel["hackernews"] = hn_results
    log(f"  Hacker News: {len(hn_results)} threads")

    # 6. Previous intel from DB
    prev_rating = None
    prev_sentiment = None
    if db_entry:
        prev_snapshots = db_entry.get("snapshots", [])
        if prev_snapshots:
            last = prev_snapshots[-1]
            prev_rating = last.get("gowork_rating")
            prev_sentiment = last.get("sentiment")

    # 7. LLM analysis
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
- community_pulse: one-line summary of developer/industry forum sentiment
- hiring_activity: summary of open positions found (titles, locations, relevance)
- recommendation: one-line action item for the user
Output ONLY valid JSON, no markdown. /no_think"""

    review_text = "\n".join(f"  [{r['date']}] {r['text'][:200]}" for r in reviews[:5])
    news_text = "\n".join(f"  • {n['title']}: {n['snippet'][:150]}" for n in all_news[:5])
    fourp_text = "\n".join(
        f"  • [{r.get('section','forum')}] {r['title']}: {r['snippet'][:150]}"
        for r in fourp_results[:3]
    )
    reddit_text = "\n".join(
        f"  • [{r.get('subreddit','')}] {r['title'][:100]}: {r['snippet'][:150]}"
        for r in reddit_results[:3]
    )
    semiwiki_text = "\n".join(
        f"  • {r['title'][:100]}: {r['snippet'][:150]}"
        for r in semiwiki_results[:3]
    )
    hn_text = "\n".join(
        f"  • [{r.get('type','?')}|{r.get('points',0)}pts] {r['title'][:100]}: {r['snippet'][:150]}"
        for r in hn_results[:4]
    )

    careers_text = "\n".join(
        f"  • {j['title']} — {j['location']}" + (f" (ID: {j['job_id']})" if j.get('job_id') else "")
        for j in careers_jobs[:10]
    )

    prompt = f"""Company: {company['name']} ({company['industry']})
GoWork entity: {company['gowork_id']}
Previous sentiment: {prev_sentiment or 'N/A'}, previous GoWork rating: {prev_rating or 'N/A'}

Open positions in Poland/Łódź ({len(careers_jobs)} found):
{careers_text or '  (none found on careers page)'}

Recent GoWork reviews ({len(reviews)} found):
{review_text or '  (none)'}

Recent news:
{news_text or '  (none)'}

Company news page excerpt:
{company_news_text[:1500] if company_news_text else '  (unavailable)'}

Layoffs.fyi mentions:
{chr(10).join(f'  • {l}' for l in layoffs) if layoffs else '  (none)'}

4programmers.net threads (Polish dev community):
{fourp_text or '  (none)'}

Reddit discussions:
{reddit_text or '  (none)'}

SemiWiki forum (semiconductor industry):
{semiwiki_text or '  (none)'}

Hacker News discussions:
{hn_text or '  (none)'}

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
                "community_pulse": intel.get("analysis", {}).get("community_pulse"),
                "hiring_activity": intel.get("analysis", {}).get("hiring_activity"),
                "careers_openings": len(intel.get("careers_openings", [])),
                "sources_4p": len(intel.get("4programmers", [])),
                "sources_reddit": len(intel.get("reddit", [])),
                "sources_semiwiki": len(intel.get("semiwiki", [])),
                "sources_hn": len(intel.get("hackernews", [])),
            })

        except Exception as e:
            log(f"  ERROR analyzing {key}: {e}")
            day_results.append({"key": key, "name": company["name"], "error": str(e)})

        time.sleep(3)  # breathing room between companies

    # ── HN "Who is Hiring" scan (once per run) ──
    log("Scanning HN 'Who is Hiring' thread...")
    hn_hiring_jobs, hn_hiring_url = search_hn_who_is_hiring()
    log(f"  HN Who is Hiring: {len(hn_hiring_jobs)} matching remote/EU jobs")

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

HN "Who is Hiring" — remote/EU jobs matching embedded/ADAS/camera/Linux ({len(hn_hiring_jobs)} found):
{chr(10).join(f"- {j['company'][:60]} (relevance={j['relevance_score']}, remote={j['is_remote']}, europe={j['is_europe']}): {j['text'][:120]}" for j in hn_hiring_jobs[:8]) or '(none found)'}

Provide:
1. Top 3 companies showing strongest positive signals for embedded/ADAS roles
2. Any companies with concerning red flags
3. Market mood: is the embedded/automotive sector in Poland hiring or contracting?
4. Most interesting remote opportunities from HN "Who is Hiring" for an embedded Linux/camera driver engineer
5. One specific action the user should consider this week"""

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
        "hn_who_is_hiring": {
            "thread_url": hn_hiring_url,
            "matching_jobs": hn_hiring_jobs,
            "total_matches": len(hn_hiring_jobs),
        },
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

    # Regenerate dashboard HTML
    try:
        import subprocess as _sp
        _sp.run(["python3", "/opt/netscan/generate-html.py"],
               capture_output=True, timeout=60)
        log("Dashboard HTML regenerated")
    except Exception:
        pass

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] company-intel done ({duration}s)", flush=True)


if __name__ == "__main__":
    main()
