#!/usr/bin/env python3
"""
city-watch.py — SkyscraperCity Łódź forum crawler for neighborhood intelligence.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Monitors the SkyscraperCity Łódź subforum for urban development news
relevant to the Do Folwarku / Widzew area (~2km radius).

Focus: trasa konstytucyjna, park nad jasieniem, milionowa,
       skrzyżowanie marszałków, and nearby investments.

Sources:
  - https://www.skyscrapercity.com/forums/łódź.4185/  (XenForo forum)
  - Crawls thread listing + last page of active threads
  - Keyword scoring for local relevance
  - Optional LLM analysis of top findings

Output: /opt/netscan/data/city/
  - city-watch-YYYYMMDD.json   (daily snapshot)
  - latest-city.json           (symlink to latest)

Schedule: Daily via openclaw cron (night window)
Location on bc250: /opt/netscan/city-watch.py
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
import html as html_mod
from datetime import datetime, timezone
from pathlib import Path
from llm_sanitize import sanitize_llm_output

# ── Config ─────────────────────────────────────────────────────────────────
OLLAMA_URL = "http://localhost:11434"
OLLAMA_CHAT = f"{OLLAMA_URL}/api/chat"
OLLAMA_MODEL = "qwen3:14b"
OLLAMA_TIMEOUT = 900

SIGNAL_RPC = "http://127.0.0.1:8080/api/v1/rpc"
SIGNAL_FROM = "+<BOT_PHONE>"
SIGNAL_TO = "+<OWNER_PHONE>"

DATA_DIR = Path("/opt/netscan/data/city")
THINK_DIR = Path("/opt/netscan/data/think")

FORUM_BASE = "https://www.skyscrapercity.com"
FORUM_URL = f"{FORUM_BASE}/forums/%C5%82%C3%B3d%C5%BA.4185/"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

# BIP Łódź — public building permit / planning portal
BIP_BASE = "https://bip.uml.lodz.pl"
BIP_SEARCH_URLS = [
    # Town planning decisions
    "https://bip.uml.lodz.pl/urzad-miasta/wydzialy-i-biura/wydzial-urbanistyki-i-architektury/",
    # Building permits public register
    "https://wyszukiwarka.gunb.gov.pl/",
]
# Geoportal Łódź for local plans (MPZP)
GEOPORTAL_URL = "https://mapa.lodz.pl/portal/"

# Streets to monitor for BIP building permits
BIP_WATCH_STREETS = [
    "do folwarku", "milionowa", "rokicińska", "szparagowa",
    "marszałków", "przybyszewskiego", "widzewska", "ziarnista",
    "konstytucyjna", "jasieniem", "jasień", "chocianowicka",
    "pabianicka", "rzgowska", "taborowa", "augustów",
]

# How many pages of thread listing to scan (1 = front page only, ~30 threads)
FORUM_PAGES = 2
# How many recent posts to fetch per matching thread
POSTS_PER_THREAD = 15

# ── Location: Do Folwarku, Widzew, Łódź ──────────────────────────────────
# ~2km radius keyword sets with priority tiers

# Tier 1 — PRIMARY: Direct mentions of user's immediate area (highest score)
PRIMARY_KEYWORDS = [
    "trasa konstytucyjna",
    "do folwarku",
    "park nad jasieniem",
    "jasieniem", "jasień",
    "milionowa",
    "skrzyżowanie marszałków",
]

# Tier 2 — NEARBY STREETS: Within ~1-2km of Do Folwarku
NEARBY_STREETS = [
    # Main arteries nearby
    "rokicińska", "przybyszewskiego", "puszkina",
    "piłsudskiego", "śmigłego-rydza", "śmigłego rydza",
    "hetmańska", "inflancka",
    # Nearby residential streets
    "olechowska", "tomaszowska", "bartoka",
    "józefów", "nowosolna", "andrzejów",
    "augustów", "widzew", "stoki",
    "dąbrowa", "olechów",
    # Cross streets in ~2km
    "kilińskiego", "nawrot", "narutowicza",
    "targowa", "wojska polskiego",
    "lumumby", "lodowa", "łomżyńska",
    "krakowska", "niciarniana", "widzewska",
    "chojny", "zarzew", "górna",
    # Nearby landmarks
    "atlas arena", "port łódź", "galeria łódzka",
    "łódź fabryczna", "widzew wschód",
    "ekspresowa", "s14", "obwodnica",
]

# Tier 3 — TOPIC KEYWORDS: Infrastructure & construction topics
TOPIC_KEYWORDS = [
    # Infrastructure
    "budowa drogi", "przebudowa", "remont",
    "inwestycja drogowa", "rondo", "wiadukt",
    "ścieżka rowerowa", "tramwaj", "mpk",
    "kanalizacja", "wodociąg",
    # Housing / development
    "osiedle", "deweloper", "blok", "mieszkania",
    "pozwolenie na budowę", "mpzp", "plan miejscowy",
    # Parks & green
    "park", "zieleń", "rewitalizacja",
    "plac zabaw", "teren zielony",
    # Transport
    "autobus", "przystanek", "ztm",
    "parking", "strefa płatnego parkowania",
]

# Thread title patterns — always interesting regardless of content
ALWAYS_WATCH_THREADS = [
    r"trasa konstytucyjna",
    r"inwestycje mieszkaniowe poza stref",
    r"plan og[oó]lny.*mpzp",
    r"monitoring miejskich zapowiedzi",
    r"estetyka miejska",
    r"infrastruktura drogowa",
    r"widzew",
]

def log(msg):
    print(f"  [{datetime.now().strftime('%H:%M:%S')}] {msg}")

# ── HTTP helpers ───────────────────────────────────────────────────────────

def fetch_page(url, timeout=30):
    """Fetch a web page, return HTML string."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.read().decode("utf-8", errors="replace")
    except Exception as ex:
        log(f"Fetch failed: {url} — {ex}")
        return ""

def unescape(text):
    """Unescape HTML entities."""
    return html_mod.unescape(text)

# ── Forum parser ──────────────────────────────────────────────────────────

def parse_thread_listing(html_text):
    """Parse XenForo thread listing page. Returns list of thread dicts."""
    threads = []

    # XenForo thread links: <a href="/threads/TITLE.ID/" ...>TITLE</a>
    # Look for thread title links in the listing
    thread_pattern = re.compile(
        r'<a\s+href="(/threads/[^"]+\.(\d+)/)"[^>]*'
        r'data-(?:tp-title|thread-title)="([^"]*)"',
        re.IGNORECASE
    )

    # Fallback: simpler pattern for thread links
    simple_pattern = re.compile(
        r'href="(/threads/([^"]+)\.(\d+)/)"[^>]*>([^<]+)</a>',
        re.IGNORECASE
    )

    # Try data-attribute pattern first
    for m in thread_pattern.finditer(html_text):
        url, tid, title = m.group(1), m.group(2), unescape(m.group(3))
        threads.append({
            "url": FORUM_BASE + url,
            "thread_id": tid,
            "title": title,
        })

    if not threads:
        # Fallback to simple pattern
        seen_ids = set()
        for m in simple_pattern.finditer(html_text):
            url_path, slug, tid, title = m.group(1), m.group(2), m.group(3), unescape(m.group(4))
            title_clean = title.strip()
            if tid not in seen_ids and len(title_clean) > 5 and "threads/" in url_path:
                seen_ids.add(tid)
                threads.append({
                    "url": FORUM_BASE + url_path,
                    "thread_id": tid,
                    "title": title_clean,
                })

    # Deduplicate
    seen = set()
    unique = []
    for t in threads:
        if t["thread_id"] not in seen:
            seen.add(t["thread_id"])
            unique.append(t)
    return unique

def parse_thread_posts(html_text, limit=15):
    """Extract recent posts from a thread page. Returns list of post dicts."""
    posts = []

    # XenForo post content blocks
    # Pattern: <article ... data-content="post-NNNN" ...> ... <div class="bbWrapper">CONTENT</div>
    post_blocks = re.findall(
        r'<article[^>]*data-content="post-(\d+)"[^>]*>(.*?)</article>',
        html_text, re.DOTALL
    )

    for post_id, block in post_blocks[-limit:]:
        # Extract author
        author_m = re.search(r'data-user-id="\d+"[^>]*>([^<]+)', block)
        author = unescape(author_m.group(1).strip()) if author_m else "?"

        # Extract date
        date_m = re.search(r'<time[^>]*datetime="([^"]+)"', block)
        date_str = date_m.group(1) if date_m else ""

        # Extract post body from bbWrapper
        body_m = re.search(r'<div class="bbWrapper">(.*?)</div>', block, re.DOTALL)
        if body_m:
            raw = body_m.group(1)
            # Strip HTML tags
            text = re.sub(r'<[^>]+>', ' ', raw)
            text = re.sub(r'\s+', ' ', text).strip()
            text = unescape(text)[:500]  # cap per post
        else:
            text = ""

        if text and len(text) > 10:
            posts.append({
                "post_id": post_id,
                "author": author,
                "date": date_str[:16],
                "text": text,
            })

    return posts

# ── Scoring ───────────────────────────────────────────────────────────────

def score_text(text, title=""):
    """Score text for relevance to user's neighborhood. Returns (score, matched_keywords)."""
    combined = (title + " " + text).lower()
    score = 0.0
    matched = []

    for kw in PRIMARY_KEYWORDS:
        if kw.lower() in combined:
            score += 5.0
            matched.append(kw)

    for kw in NEARBY_STREETS:
        if kw.lower() in combined:
            score += 2.0
            matched.append(kw)

    for kw in TOPIC_KEYWORDS:
        if kw.lower() in combined:
            score += 0.5
            matched.append(kw)

    return score, list(dict.fromkeys(matched))  # deduplicate preserving order

def is_always_watch(title):
    """Check if thread title matches always-watch patterns."""
    title_lower = title.lower()
    for pat in ALWAYS_WATCH_THREADS:
        if re.search(pat, title_lower):
            return True
    return False

# ── LLM ───────────────────────────────────────────────────────────────────

def call_ollama(system_prompt, user_content, timeout=OLLAMA_TIMEOUT):
    """Call Ollama chat API. Returns response text."""
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "stream": False,
        "options": {"num_ctx": 24576, "temperature": 0.4},
    }).encode()

    req = urllib.request.Request(
        OLLAMA_CHAT, data=payload,
        headers={"Content-Type": "application/json"}
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = json.loads(resp.read())
        text = data.get("message", {}).get("content", "")
        return _strip_thinking(text)
    except Exception as ex:
        log(f"Ollama error: {ex}")
        return ""

def _strip_thinking(text):
    """Strip CoT thinking tags and Chinese text from LLM output."""
    return sanitize_llm_output(text)

def signal_send(msg):
    """Send Signal notification."""
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "send",
        "params": {
            "account": SIGNAL_FROM,
            "recipient": [SIGNAL_TO],
            "message": msg,
        }
    })
    req = urllib.request.Request(
        SIGNAL_RPC, data=payload.encode(),
        headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        log("Signal alert sent")
    except Exception as ex:
        log(f"Signal send failed: {ex}")

# ── Main crawl ────────────────────────────────────────────────────────────

def crawl_forum():
    """Crawl SkyscraperCity Łódź forum and score threads for neighborhood relevance."""
    t_start = time.time()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    log("Crawling SkyscraperCity Łódź forum...")

    # Step 1: Fetch thread listing pages
    all_threads = []
    for page_num in range(1, FORUM_PAGES + 1):
        url = FORUM_URL if page_num == 1 else f"{FORUM_URL}page-{page_num}?sorting=latest-activity"
        log(f"  Fetching listing page {page_num}: {url}")
        html = fetch_page(url)
        if html:
            threads = parse_thread_listing(html)
            log(f"    Found {len(threads)} threads")
            all_threads.extend(threads)
        time.sleep(2)  # be polite

    # Deduplicate
    seen_ids = set()
    unique_threads = []
    for t in all_threads:
        if t["thread_id"] not in seen_ids:
            seen_ids.add(t["thread_id"])
            unique_threads.append(t)

    log(f"Total unique threads from listing: {len(unique_threads)}")

    # Step 2: Score each thread by title
    scored_threads = []
    for t in unique_threads:
        title_score, title_kw = score_text("", t["title"])
        always = is_always_watch(t["title"])
        t["title_score"] = title_score
        t["title_keywords"] = title_kw
        t["always_watch"] = always
        scored_threads.append(t)

    # Step 3: Identify threads worth deep-crawling
    # - Always-watch threads
    # - Threads with title score > 0
    # - Top 5 most active threads (even if unscored, they might mention our area)
    deep_crawl = [t for t in scored_threads if t["always_watch"] or t["title_score"] > 0]
    # Also add a few unscored active threads from front page
    unscored = [t for t in scored_threads if t["title_score"] == 0 and not t["always_watch"]]
    deep_crawl.extend(unscored[:5])

    log(f"Deep crawling {len(deep_crawl)} threads for recent posts...")

    # Step 4: Fetch last page of each thread for recent posts
    results = []
    for t in deep_crawl:
        # Fetch the last page
        thread_url = t["url"]
        # Try to get /latest which redirects to last page
        latest_url = thread_url.rstrip("/") + "/page-99999"  # XenForo clamps to last
        log(f"  Fetching: {t['title'][:50]}...")
        html = fetch_page(latest_url)

        posts = []
        if html:
            posts = parse_thread_posts(html, limit=POSTS_PER_THREAD)

        # Score posts for neighborhood relevance
        post_texts = " ".join(p["text"] for p in posts)
        content_score, content_kw = score_text(post_texts, t["title"])
        total_score = t["title_score"] + content_score
        all_kw = list(dict.fromkeys(t["title_keywords"] + content_kw))

        result = {
            "thread_id": t["thread_id"],
            "title": t["title"],
            "url": t["url"],
            "always_watch": t["always_watch"],
            "title_score": t["title_score"],
            "content_score": content_score,
            "total_score": total_score,
            "matched_keywords": all_kw,
            "recent_posts": len(posts),
            "posts": posts[:8],  # keep top 8 posts per thread
        }
        results.append(result)
        time.sleep(1.5)  # be polite

    # Sort by score (highest first)
    results.sort(key=lambda x: -x["total_score"])

    # Step 5: Filter to relevant results
    relevant = [r for r in results if r["total_score"] > 0 or r["always_watch"]]

    elapsed = time.time() - t_start
    log(f"Crawl complete: {len(relevant)} relevant threads, {sum(r['recent_posts'] for r in relevant)} posts, {elapsed:.0f}s")

    return {
        "meta": {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "duration_seconds": round(elapsed),
            "total_threads_scanned": len(unique_threads),
            "deep_crawled": len(deep_crawl),
            "relevant": len(relevant),
            "forum_url": FORUM_URL,
            "location": "Do Folwarku, Widzew, Łódź",
            "radius_km": 2,
        },
        "threads": relevant,
        "all_threads_summary": [
            {"title": t["title"], "thread_id": t["thread_id"],
             "title_score": t["title_score"], "always_watch": t["always_watch"]}
            for t in scored_threads[:50]
        ],
    }


def scrape_bip_permits():
    """Scrape BIP Łódź and GUNB for building permits near our area."""
    log("Scraping BIP Łódź / GUNB for building permits...")
    permits = []

    # GUNB public register — search for Łódź permits
    # We use DDG to search GUNB since their search form uses POST/JS
    for street in BIP_WATCH_STREETS[:8]:  # Top 8 streets
        query = f"site:wyszukiwarka.gunb.gov.pl OR site:bip.uml.lodz.pl \"{street}\" łódź pozwolenie budowlane"
        encoded = urllib.parse.quote(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded}&t=h_"

        html_text = fetch_page(url)
        if not html_text:
            continue

        # Extract results
        results = re.findall(
            r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.+?)</a>.*?'
            r'<a[^>]+class="result__snippet"[^>]*>(.+?)</a>',
            html_text, re.DOTALL
        )
        # Also try simpler pattern
        if not results:
            results = re.findall(
                r'<a[^>]+rel="noopener"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
                r'class="result__snippet"[^>]*>(.*?)</(?:a|div)',
                html_text, re.DOTALL
            )

        for link, title, snippet in results[:3]:
            title_clean = re.sub(r'<[^>]+>', '', title).strip()
            snippet_clean = re.sub(r'<[^>]+>', '', snippet).strip()

            # Check if it mentions our streets
            combined = (title_clean + " " + snippet_clean).lower()
            matched = [s for s in BIP_WATCH_STREETS if s in combined]
            if matched:
                permits.append({
                    "street": street,
                    "title": title_clean[:200],
                    "snippet": snippet_clean[:300],
                    "url": link,
                    "matched_streets": matched,
                    "source": "bip-ddg",
                })

        time.sleep(3)

    # Direct BIP UML scrape — architecture department announcements
    for bip_url in BIP_SEARCH_URLS[:1]:
        html_text = fetch_page(bip_url)
        if not html_text:
            continue

        # Look for announcements mentioning our streets
        # BIP pages typically have lists of decisions/announcements
        text = re.sub(r'<[^>]+>', ' ', html_text)
        text = re.sub(r'\s+', ' ', text)

        for street in BIP_WATCH_STREETS:
            if street in text.lower():
                # Extract context around the mention
                idx = text.lower().find(street)
                context = text[max(0, idx-150):idx+200].strip()
                permits.append({
                    "street": street,
                    "title": f"BIP UML mention: {street}",
                    "snippet": context[:300],
                    "url": bip_url,
                    "matched_streets": [street],
                    "source": "bip-direct",
                })

    log(f"  Found {len(permits)} permit-related results")
    return permits


def llm_analyze(data):
    """Use LLM to analyze crawled forum data + BIP permits for neighborhood briefing."""
    relevant = data.get("threads", [])
    bip_permits = data.get("bip_permits", [])
    if not relevant and not bip_permits:
        return ""

    # Build summary for LLM
    parts = []

    # Forum threads
    if relevant:
        parts.append("=== SKYSCRAPERCITY FORUM THREADS ===")
        for t in relevant[:10]:
            parts.append(f"\n### {t['title']} (score: {t['total_score']:.0f})")
            parts.append(f"Keywords: {', '.join(t['matched_keywords'][:6])}")
            for p in t.get("posts", [])[:4]:
                parts.append(f"  [{p['date']}] {p['author']}: {p['text'][:250]}")

    # BIP permits
    if bip_permits:
        parts.append("\n=== BUILDING PERMITS / PLANNING DECISIONS (BIP) ===")
        for p in bip_permits[:10]:
            parts.append(f"  {p['street']}: {p['title']}")
            parts.append(f"    {p['snippet'][:200]}")

    forum_text = "\n".join(parts)

    system_prompt = """\
You are a neighborhood watch analyst for a family living at Do Folwarku street
in Widzew district, Łódź, Poland.

Analyze the SkyscraperCity forum posts below and write a concise briefing:

## 🏗️ KEY DEVELOPMENTS
What's new near Do Folwarku? Focus on:
- Trasa Konstytucyjna progress (the #1 priority)
- Any construction/investment near Park nad Jasieniem, Milionowa, Rokicińska
- Housing developments in Widzew area
- Road works, detours, closures affecting the area

## 🚧 IMPACT ON DAILY LIFE
- Traffic changes, road closures, detours
- Noise, construction disruption
- New amenities opening

## 📋 SUMMARY
- 3-5 bullet points of most actionable intel
- Flag anything that needs attention this week

## 🏛️ BUILDING PERMITS & PLANNING
- Any new building permits or planning decisions near the area
- Development projects in the pipeline
- Zoning changes or MPZP (local spatial plan) updates

Rules:
- Write in English
- Be specific with street names and dates
- Skip threads that don't affect the Do Folwarku area
- 200-400 words maximum
- Do NOT include any thinking/reasoning tags
"""
    log("Running LLM analysis...")
    return call_ollama(system_prompt, forum_text)


def save_results(data, analysis=""):
    """Save crawl results to JSON files."""
    today = datetime.now().strftime("%Y%m%d")
    fname = f"city-watch-{today}.json"

    data["llm_analysis"] = analysis

    out_path = DATA_DIR / fname
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    latest = DATA_DIR / "latest-city.json"
    try:
        latest.unlink(missing_ok=True)
    except TypeError:
        try: latest.unlink()
        except: pass
    latest.symlink_to(fname)

    log(f"Saved: {out_path} ({out_path.stat().st_size:,} bytes)")

    # Save as think note for dashboard if we have analysis
    if analysis:
        THINK_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M")
        note = {
            "type": "city-watch",
            "title": f"Neighborhood Watch — {datetime.now().strftime('%d %b %Y')}",
            "content": analysis,
            "generated": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "model": OLLAMA_MODEL,
            "context": f"SkyscraperCity Łódź — {data['meta']['relevant']} relevant threads, "
                       f"{data['meta']['total_threads_scanned']} scanned",
        }
        note_path = THINK_DIR / f"note-city-watch-{ts}.json"
        with open(note_path, "w") as f:
            json.dump(note, f, indent=2, ensure_ascii=False)
        log(f"Think note: {note_path}")


# ── Raw data file for scrape/analyze split ─────────────────────────────────
RAW_CITY_FILE = DATA_DIR / "raw-city.json"


def run_scrape():
    """Crawl forum, score threads. Save raw JSON (no LLM)."""
    log("city-watch.py — scrape mode")
    log(f"Location: Do Folwarku, Widzew, Łódź (~2km radius)")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    dt = datetime.now()
    t0 = time.time()

    data = crawl_forum()

    # Scrape BIP for building permits
    bip_permits = scrape_bip_permits()
    data["bip_permits"] = bip_permits

    # Save raw intermediate data
    scrape_duration = round(time.time() - t0, 1)
    raw_data = {
        "scrape_timestamp": dt.isoformat(timespec="seconds"),
        "scrape_duration_seconds": scrape_duration,
        "scrape_version": 1,
        "data": data,
        "scrape_errors": [],
    }
    tmp = RAW_CITY_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(raw_data, f, indent=2, ensure_ascii=False)
    tmp.rename(RAW_CITY_FILE)

    log(f"Scrape done: {data['meta']['relevant']} relevant threads saved ({scrape_duration}s)")


def run_analyze():
    """Load raw data, run LLM analysis, save final output."""
    log("city-watch.py — analyze mode")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    THINK_DIR.mkdir(parents=True, exist_ok=True)
    dt = datetime.now()
    t0 = time.time()

    if not RAW_CITY_FILE.exists():
        log(f"ERROR: Raw data file not found: {RAW_CITY_FILE}")
        print("Run with --scrape-only first.", file=sys.stderr)
        sys.exit(1)

    try:
        with open(RAW_CITY_FILE) as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log(f"ERROR: Failed to read raw data: {e}")
        sys.exit(1)

    scrape_ts = raw.get("scrape_timestamp", "")
    data = raw.get("data", {})

    # Check staleness
    if scrape_ts:
        try:
            scrape_dt = datetime.fromisoformat(scrape_ts)
            age_hours = (dt - scrape_dt).total_seconds() / 3600
            if age_hours > 48:
                log(f"WARNING: Raw data is {age_hours:.0f}h old (scraped {scrape_ts})")
        except ValueError:
            pass

    relevant_count = data.get("meta", {}).get("relevant", 0)
    log(f"Loaded {relevant_count} relevant threads from raw data (scraped {scrape_ts})")

    # LLM analysis
    analysis = ""
    if relevant_count > 0:
        analysis = llm_analyze(data)

    # Add dual timestamps to data meta
    data.setdefault("meta", {})["scrape_timestamp"] = scrape_ts
    data["meta"]["analyze_timestamp"] = dt.isoformat(timespec="seconds")
    data["meta"]["timestamp"] = dt.isoformat(timespec="seconds")  # backward compat

    # Save
    save_results(data, analysis)

    # Signal alert — send LLM summary, but only once per day
    today_str = dt.strftime("%Y%m%d")
    sent_flag = DATA_DIR / f"sent-{today_str}.flag"
    if analysis and not sent_flag.exists():
        # Extract key points from analysis for compact Signal message
        alert_parts = [f"🏗️ Neighborhood Watch — {relevant_count} relevant threads"]
        # Send the LLM summary directly — user wants insights, not topic list
        # Trim to fit Signal nicely
        summary_text = analysis.strip()
        # Try to extract just the SUMMARY section if available
        summary_section = ""
        for marker in ["## 📋 SUMMARY", "SUMMARY", "## IMPACT", "ACTIONABLE"]:
            idx = summary_text.upper().find(marker.upper())
            if idx >= 0:
                summary_section = summary_text[idx:]
                break
        if summary_section:
            alert_parts.append(f"\n{summary_section[:800]}")
        else:
            # Use first ~800 chars of the full analysis
            alert_parts.append(f"\n{summary_text[:800]}")
        signal_send("\n".join(alert_parts)[:1200])
        sent_flag.write_text(dt.isoformat())
    elif analysis:
        log(f"City watch alert already sent today — suppressed")
    else:
        log(f"No LLM analysis — skipping Signal alert.")

    log(f"Analyze done ({time.time() - t0:.0f}s).")


def main():
    parser = argparse.ArgumentParser(description="City watch — neighborhood monitor")
    parser.add_argument('--scrape-only', action='store_true',
                        help='Only crawl forum, save raw data (no LLM)')
    parser.add_argument('--analyze-only', action='store_true',
                        help='Only run LLM analysis on previously scraped raw data')
    args = parser.parse_args()

    log("city-watch.py — SkyscraperCity Łódź neighborhood monitor"
        f"{' (scrape-only)' if args.scrape_only else ' (analyze-only)' if args.analyze_only else ''}")
    log(f"Primary keywords: {', '.join(PRIMARY_KEYWORDS)}")

    if args.scrape_only:
        run_scrape()
    elif args.analyze_only:
        run_analyze()
    else:
        # Legacy: full run (backward compatible)
        run_scrape()
        run_analyze()

    log("Done.")


if __name__ == "__main__":
    main()
