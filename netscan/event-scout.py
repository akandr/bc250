#!/usr/bin/env python3
"""
event-scout.py — Meetup, conference & tech event tracker
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Discovers and tracks tech events matching user interests:
  - Embedded Linux, camera/imaging, automotive ADAS
  - Kernel, V4L2, libcamera, GStreamer
  - Sensor fusion, edge AI, functional safety
  - Open source hardware, RISC-V

Geographic priority:
  1. Łódź (highest — local, no travel)
  2. Warsaw (easy — 1.5h train)
  3. Other Poland (Kraków, Wrocław, Poznań, Gdańsk)
  4. Europe (if strong match — ELC, FOSDEM, Automotive Linux Summit)

Sources:
  - Meetup.com API / scraping
  - Eventbrite search
  - Crossweb.pl (Polish tech events aggregator)
  - Konfeo.com (Polish conference platform)
  - Conference websites (LPC, ELC, FOSDEM, ALS, ELCE)
  - DuckDuckGo event search
  - Evenea.pl (Polish events)

Output: /opt/netscan/data/events/
  - events-YYYYMMDD.json      (daily scan)
  - event-db.json             (rolling calendar, deduped)
  - latest-events.json        (symlink)

Cron: 30 3 * * * flock -w 1200 /tmp/ollama-gpu.lock python3 /opt/netscan/event-scout.py
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

EVENT_DIR = Path("/opt/netscan/data/events")
EVENT_DB = EVENT_DIR / "event-db.json"

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
MAX_FUTURE_DAYS = 180  # Look ahead 6 months

# ── Interest Matching ──────────────────────────────────────────────────────

# High-value keywords — event MUST match at least one
PRIMARY_KEYWORDS = [
    "embedded linux", "linux kernel", "camera driver", "v4l2", "libcamera",
    "mipi csi", "isp", "image sensor", "device tree",
    "automotive software", "adas", "dms", "oms", "driver monitoring",
    "sensor fusion", "edge ai", "embedded ai", "tinyml",
    "gstreamer", "video pipeline", "multimedia embedded",
    "functional safety", "iso 26262", "autosar adaptive",
    "risc-v", "open hardware", "fpga",
    "linux plumbers", "embedded linux conference", "fosdem",
    "yocto", "buildroot", "open embedded",
]

# Secondary keywords — boost relevance
SECONDARY_KEYWORDS = [
    "c/c++", "rust embedded", "kernel", "driver", "firmware",
    "arm", "soc", "bsp", "bootloader", "u-boot",
    "computer vision", "opencv", "neural network",
    "automotive", "can bus", "some/ip",
    "open source", "linux foundation",
    "qualcomm", "nvidia", "nxp", "renesas", "mediatek",
    "camera", "imaging", "video", "display", "drm", "kms",
    "python", "ci/cd", "devops embedded",
    "security embedded", "secure boot", "tee",
]

# Location tiers with distance scores
LOCATIONS = {
    "tier1_local": {
        "keywords": ["łódź", "lodz", "lódź"],
        "label": "Łódź",
        "travel_score": 10,
    },
    "tier2_easy": {
        "keywords": ["warszawa", "warsaw", "warsaw poland"],
        "label": "Warsaw",
        "travel_score": 8,
    },
    "tier3_poland": {
        "keywords": ["kraków", "krakow", "cracow", "wrocław", "wroclaw",
                     "poznań", "poznan", "gdańsk", "gdansk", "katowice",
                     "polska", "poland"],
        "label": "Poland",
        "travel_score": 5,
    },
    "tier4_europe": {
        "keywords": ["berlin", "prague", "praha", "vienna", "wien",
                     "amsterdam", "brussels", "bruxelles", "munich", "münchen",
                     "paris", "barcelona", "copenhagen", "stockholm",
                     "europe", "eu", "emea"],
        "label": "Europe",
        "travel_score": 3,
    },
    "tier5_online": {
        "keywords": ["online", "virtual", "remote", "webinar", "livestream"],
        "label": "Online",
        "travel_score": 9,
    },
}

# Known major conferences to always check
KNOWN_CONFERENCES = [
    {
        "name": "Embedded Linux Conference (ELC)",
        "url": "https://events.linuxfoundation.org/embedded-linux-conference/",
        "alt_urls": ["https://elinux.org/ELC"],
        "keywords": ["embedded linux conference", "elc", "elinux"],
        "relevance": 10,
    },
    {
        "name": "Linux Plumbers Conference (LPC)",
        "url": "https://lpc.events/",
        "alt_urls": ["https://www.linuxplumbersconf.org/"],
        "keywords": ["linux plumbers", "lpc"],
        "relevance": 10,
    },
    {
        "name": "FOSDEM",
        "url": "https://fosdem.org/",
        "alt_urls": [],
        "keywords": ["fosdem"],
        "relevance": 9,
    },
    {
        "name": "Automotive Linux Summit",
        "url": "https://events.linuxfoundation.org/automotive-linux-summit/",
        "alt_urls": [],
        "keywords": ["automotive linux summit", "als"],
        "relevance": 9,
    },
    {
        "name": "Embedded World",
        "url": "https://www.embedded-world.de/en",
        "alt_urls": [],
        "keywords": ["embedded world", "nuremberg embedded"],
        "relevance": 8,
    },
    {
        "name": "Open Source Summit Europe",
        "url": "https://events.linuxfoundation.org/open-source-summit-europe/",
        "alt_urls": [],
        "keywords": ["open source summit europe", "osseu"],
        "relevance": 7,
    },
    {
        "name": "GStreamer Conference",
        "url": "https://gstreamer.freedesktop.org/conference/",
        "alt_urls": [],
        "keywords": ["gstreamer conference"],
        "relevance": 9,
    },
    {
        "name": "Yocto Project Summit",
        "url": "https://www.yoctoproject.org/",
        "alt_urls": [],
        "keywords": ["yocto summit", "yocto project summit"],
        "relevance": 7,
    },
    {
        "name": "KernelCI Hackfest",
        "url": "https://kernelci.org/",
        "alt_urls": [],
        "keywords": ["kernelci"],
        "relevance": 6,
    },
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
    """Fetch URL expecting JSON."""
    text = fetch_url(url, timeout)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None

def strip_html(html):
    """Remove HTML tags."""
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


# ── Event Scoring ──────────────────────────────────────────────────────────

def score_event(event):
    """Score an event based on topic relevance and location."""
    text = f"{event.get('name', '')} {event.get('description', '')} {event.get('topics', '')}".lower()
    location = f"{event.get('location', '')} {event.get('city', '')} {event.get('country', '')}".lower()

    # Topic relevance
    topic_score = 0
    matched_primary = []
    for kw in PRIMARY_KEYWORDS:
        if kw in text:
            topic_score += 3
            matched_primary.append(kw)
    for kw in SECONDARY_KEYWORDS:
        if kw in text:
            topic_score += 1

    # Location score
    travel_score = 0
    location_tier = "unknown"
    for tier_name, tier in LOCATIONS.items():
        for kw in tier["keywords"]:
            if kw in location:
                travel_score = tier["travel_score"]
                location_tier = tier["label"]
                break
        if travel_score > 0:
            break

    # Combined score: topic × location multiplier
    # Online/local events get full topic score; distant events need stronger match
    if travel_score >= 8:  # local or online
        combined = topic_score * 1.5
    elif travel_score >= 5:  # Poland
        combined = topic_score * 1.0
    elif travel_score >= 3:  # Europe
        combined = topic_score * 0.7
    else:
        combined = topic_score * 0.3

    event["topic_score"] = topic_score
    event["travel_score"] = travel_score
    event["combined_score"] = round(combined, 1)
    event["location_tier"] = location_tier
    event["matched_keywords"] = matched_primary[:5]
    return combined


# ── Source: Crossweb.pl (Polish tech events aggregator) ────────────────────

def search_crossweb():
    """Search Crossweb.pl for Polish tech events."""
    events = []
    categories = ["development", "embedded", "iot", "ai", "hardware"]

    for cat in categories:
        url = f"https://crossweb.pl/en/events/?category={cat}"
        html = fetch_url(url, timeout=25)
        if not html:
            continue

        # Extract event blocks
        blocks = re.findall(
            r'<(?:div|article)[^>]*class="[^"]*event[^"]*"[^>]*>(.*?)</(?:div|article)>',
            html, re.DOTALL
        )

        for block in blocks[:20]:
            name = ""
            date_str = ""
            city = ""
            link = ""

            name_m = re.search(r'<(?:h2|h3|a)[^>]*>(.*?)</(?:h2|h3|a)>', block, re.DOTALL)
            if name_m:
                name = strip_html(name_m.group(1))

            date_m = re.search(r'(\d{1,2}[./]\d{1,2}[./]\d{2,4}|\d{4}-\d{2}-\d{2})', block)
            if date_m:
                date_str = date_m.group(1)

            city_m = re.search(r'(?:city|location|miejsce)[^>]*>([^<]+)', block, re.IGNORECASE)
            if city_m:
                city = strip_html(city_m.group(1))

            link_m = re.search(r'href="(https?://[^"]+)"', block)
            if link_m:
                link = link_m.group(1)

            if name:
                events.append({
                    "name": name, "date": date_str, "city": city,
                    "url": link, "source": "crossweb", "country": "Poland",
                    "location": f"{city}, Poland" if city else "Poland",
                    "description": strip_html(block)[:500],
                })

        time.sleep(2)

    log(f"Crossweb.pl: {len(events)} events")
    return events


# ── Source: Meetup.com search ──────────────────────────────────────────────

def search_meetup(query, city="Łódź"):
    """Search Meetup.com for events."""
    events = []
    encoded_q = urllib.parse.quote(query)
    encoded_city = urllib.parse.quote(city)

    # Meetup search page (HTML scraping)
    url = f"https://www.meetup.com/find/?keywords={encoded_q}&location={encoded_city}&source=EVENTS&distance=hundredMiles"
    html = fetch_url(url, timeout=25)
    if not html:
        return events

    # Extract event data — Meetup puts JSON-LD in the page
    jsonld_blocks = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>',
        html, re.DOTALL
    )

    for block in jsonld_blocks:
        try:
            data = json.loads(block)
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") == "Event":
                    loc = item.get("location", {})
                    addr = loc.get("address", {})
                    events.append({
                        "name": item.get("name", ""),
                        "date": item.get("startDate", ""),
                        "end_date": item.get("endDate", ""),
                        "city": addr.get("addressLocality", ""),
                        "country": addr.get("addressCountry", ""),
                        "location": f"{addr.get('addressLocality', '')}, {addr.get('addressCountry', '')}",
                        "url": item.get("url", ""),
                        "description": item.get("description", "")[:500],
                        "source": "meetup",
                        "organizer": item.get("organizer", {}).get("name", ""),
                    })
        except (json.JSONDecodeError, TypeError):
            pass

    # Fallback: extract from HTML directly
    if not events:
        event_links = re.findall(
            r'<a[^>]*href="(https://www\.meetup\.com/[^/]+/events/[^"]+)"[^>]*>(.*?)</a>',
            html, re.DOTALL
        )
        for link, text in event_links[:10]:
            name = strip_html(text)
            if name and len(name) > 5:
                events.append({
                    "name": name, "url": link, "source": "meetup",
                    "city": city, "location": city, "description": "",
                    "date": "",
                })

    log(f"Meetup ({city}, '{query}'): {len(events)} events")
    return events


# ── Source: Eventbrite search ──────────────────────────────────────────────

def search_eventbrite(query, location="Poland"):
    """Search Eventbrite for events."""
    events = []
    encoded_q = urllib.parse.quote(query)
    encoded_loc = urllib.parse.quote(location)
    url = f"https://www.eventbrite.com/d/{encoded_loc}/{encoded_q}/"

    html = fetch_url(url, timeout=25)
    if not html:
        return events

    # Extract JSON-LD
    jsonld_blocks = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>',
        html, re.DOTALL
    )

    for block in jsonld_blocks:
        try:
            data = json.loads(block)
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") == "Event":
                    loc = item.get("location", {})
                    addr = loc.get("address", {})
                    events.append({
                        "name": item.get("name", ""),
                        "date": item.get("startDate", ""),
                        "city": addr.get("addressLocality", ""),
                        "country": addr.get("addressCountry", ""),
                        "location": f"{addr.get('addressLocality', '')}, {addr.get('addressCountry', '')}",
                        "url": item.get("url", ""),
                        "description": item.get("description", "")[:500],
                        "source": "eventbrite",
                    })
        except (json.JSONDecodeError, TypeError):
            pass

    log(f"Eventbrite ('{query}'): {len(events)} events")
    return events


# ── Source: Known conference websites ──────────────────────────────────────

def check_known_conferences():
    """Check known conference websites for upcoming dates."""
    events = []

    for conf in KNOWN_CONFERENCES:
        log(f"  Checking: {conf['name']}")
        urls_to_try = [conf["url"]] + conf.get("alt_urls", [])

        for url in urls_to_try:
            html = fetch_url(url, timeout=20)
            if not html:
                continue

            text = strip_html(html)[:5000]

            # Try to extract dates
            # Common patterns: "Month DD-DD, YYYY" or "DD-DD Month YYYY" or "YYYY-MM-DD"
            date_patterns = [
                r'(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:\s*[-–]\s*\d{1,2})?,?\s*\d{4}',
                r'\d{1,2}(?:\s*[-–]\s*\d{1,2})?\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}',
                r'\d{4}-\d{2}-\d{2}',
            ]

            found_dates = []
            for pat in date_patterns:
                matches = re.findall(pat, text, re.IGNORECASE)
                found_dates.extend(matches)

            # Try to extract location
            loc_patterns = [
                r'(?:held in|taking place in|location:\s*|venue:\s*)([^.,:;\n]+)',
                r'((?:online|virtual|hybrid|[A-Z][a-z]+(?:,\s*[A-Z][a-z]+)?))\s*(?:·|—|–|-)\s*(?:January|February|March|April|May|June|July|August|September|October|November|December)',
            ]
            location = ""
            for pat in loc_patterns:
                loc_m = re.search(pat, text, re.IGNORECASE)
                if loc_m:
                    location = loc_m.group(1).strip()[:100]
                    break

            events.append({
                "name": conf["name"],
                "url": conf["url"],
                "source": "known_conference",
                "dates_found": found_dates[:3],
                "date": found_dates[0] if found_dates else "",
                "location": location,
                "city": "",
                "description": text[:500],
                "relevance": conf["relevance"],
                "matched_keywords": conf["keywords"],
            })

            time.sleep(2)
            break  # got a response, don't try alt URLs

    log(f"Known conferences: {len(events)} checked")
    return events


# ── Source: DuckDuckGo event search ────────────────────────────────────────

def search_ddg_events(query, region="Poland"):
    """Search DuckDuckGo for tech events."""
    events = []
    now = datetime.now()
    year = now.year

    full_query = f"{query} {region} {year} conference meetup event"
    encoded = urllib.parse.quote(full_query)
    url = f"https://html.duckduckgo.com/html/?q={encoded}&t=h_"

    html = fetch_url(url, timeout=20)
    if not html:
        return events

    # Extract results
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</[^>]+>', html, re.DOTALL)
    titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, re.DOTALL)
    links = re.findall(r'class="result__url"[^>]*href="([^"]*)"', html)

    for i in range(min(8, len(snippets))):
        title = strip_html(titles[i]) if i < len(titles) else ""
        snippet = strip_html(snippets[i])
        link = links[i] if i < len(links) else ""

        # Only include if it looks like an event
        combined = f"{title} {snippet}".lower()
        event_indicators = ["conference", "meetup", "summit", "workshop",
                           "hackathon", "event", "seminar", "webinar",
                           "konferencja", "spotkanie", "warsztat"]
        if not any(ind in combined for ind in event_indicators):
            continue

        events.append({
            "name": title,
            "description": snippet[:500],
            "url": link,
            "source": "ddg_search",
            "location": region,
            "date": "",
        })

    log(f"DDG events ('{query}'): {len(events)} results")
    return events


# ── Source: Konfeo.com ─────────────────────────────────────────────────────

def search_konfeo():
    """Search Konfeo.com (Polish conference platform)."""
    events = []
    categories = ["it", "technologie"]

    for cat in categories:
        url = f"https://konfeo.com/pl/events?category={cat}"
        html = fetch_url(url, timeout=20)
        if not html:
            continue

        # Extract event listings
        blocks = re.findall(
            r'<(?:div|article|li)[^>]*class="[^"]*event[^"]*"[^>]*>(.*?)</(?:div|article|li)>',
            html, re.DOTALL
        )

        for block in blocks[:15]:
            name_m = re.search(r'<(?:h2|h3|a|span)[^>]*>(.*?)</(?:h2|h3|a|span)>', block, re.DOTALL)
            name = strip_html(name_m.group(1)) if name_m else ""

            date_m = re.search(r'(\d{1,2}[./]\d{1,2}[./]\d{2,4})', block)
            date_str = date_m.group(1) if date_m else ""

            link_m = re.search(r'href="(https?://[^"]*konfeo[^"]*)"', block)
            link = link_m.group(1) if link_m else ""

            if name:
                events.append({
                    "name": name, "date": date_str, "url": link,
                    "source": "konfeo", "location": "Poland", "city": "",
                    "description": strip_html(block)[:300],
                })

        time.sleep(2)

    log(f"Konfeo.com: {len(events)} events")
    return events


# ── LLM Analysis ──────────────────────────────────────────────────────────

def llm_analyze_events(events):
    """Use LLM to prioritize and analyze found events."""
    if not events:
        return None

    system = """You are a tech event advisor for an embedded Linux camera driver engineer
based in Łódź, Poland. They work on automotive ADAS (DMS/OMS) camera systems,
Linux kernel V4L2 drivers, MIPI CSI-2, and ISP pipelines at HARMAN/Samsung.
They're interested in T5 promotion, industrial PhD, and expanding into sensor fusion and edge AI.

Analyze the events and recommend which ones to attend.
Consider: topic relevance, networking value, learning opportunities, travel effort.

Respond in JSON:
- must_attend: list of {name, date, location, why} — high relevance, worth the travel
- worth_considering: list of {name, date, location, why} — good match, moderate effort
- skip: list of {name, reason} — low relevance or too far for the topic
- networking_opportunities: specific people/companies likely at top events
- calendar_conflicts: any events that overlap
- preparation_tips: what to do before the top events (submit talks, prepare papers)
Output ONLY valid JSON. /no_think"""

    event_text = "\n".join(
        f"• {e.get('name', '?')} | {e.get('date', '?')} | {e.get('location', '?')} | "
        f"score={e.get('combined_score', 0)} | keywords={e.get('matched_keywords', [])}"
        for e in sorted(events, key=lambda e: e.get("combined_score", 0), reverse=True)[:25]
    )

    prompt = f"""Found {len(events)} tech events. Top candidates:

{event_text}

User context:
- Based in Łódź, Poland (Warsaw = 1.5h train, Kraków = 3h)
- Principal Embedded SW Engineer at HARMAN, camera team lead
- Interested in: kernel camera drivers, V4L2, MIPI CSI-2, ISP, automotive ADAS,
  sensor fusion, edge AI, functional safety, libcamera, GStreamer
- Pursuing T5 promotion — conference talks would help visibility
- Considering industrial PhD on multi-modal driver monitoring
- Budget: personal budget for Poland events, would need employer sponsorship for Europe

Prioritize and recommend."""

    return call_ollama(system, prompt, temperature=0.3, max_tokens=2500)


# ── DB Management ──────────────────────────────────────────────────────────

def load_db():
    """Load event database."""
    if EVENT_DB.exists():
        try:
            return json.load(open(EVENT_DB))
        except Exception:
            pass
    return {"events": {}, "version": 1}

def save_db(db):
    """Save event DB, pruning past events."""
    today = datetime.now().strftime("%Y-%m-%d")
    # Keep events that are upcoming or recent (last 30 days)
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    events = db.get("events", {})
    pruned = {}
    for eid, ev in events.items():
        ev_date = ev.get("date_normalized", ev.get("first_seen", ""))
        if ev_date >= cutoff or not ev_date:
            pruned[eid] = ev
    db["events"] = pruned
    db["last_updated"] = datetime.now().isoformat(timespec="seconds")
    with open(EVENT_DB, "w") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)

def event_id(event):
    """Generate a unique ID for an event."""
    name = event.get("name", "").lower().strip()
    date = event.get("date", "")
    # Simple hash-like ID from name
    return re.sub(r'[^a-z0-9]+', '-', name)[:60] + (f"_{date}" if date else "")


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

    dt = datetime.now()
    today = dt.strftime("%Y-%m-%d")
    print(f"[{dt.strftime('%Y-%m-%d %H:%M:%S')}] event-scout starting", flush=True)

    EVENT_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    db = load_db()
    all_events = []

    # ── Phase 1: Collect from all sources ──
    log("Phase 1: Collecting events...")

    # Known conferences (always check)
    all_events.extend(check_known_conferences())
    time.sleep(2)

    # Crossweb.pl (Polish aggregator)
    all_events.extend(search_crossweb())
    time.sleep(2)

    # Konfeo.com
    all_events.extend(search_konfeo())
    time.sleep(2)

    # Meetup.com — search by location × topic
    meetup_queries = [
        ("embedded linux", "Łódź"),
        ("embedded linux", "Warsaw"),
        ("automotive software", "Poland"),
        ("IoT embedded", "Łódź"),
        ("camera imaging", "Warsaw"),
        ("kernel linux", "Poland"),
    ]
    for query, city in meetup_queries:
        all_events.extend(search_meetup(query, city))
        time.sleep(3)

    # Eventbrite
    for query in ["embedded linux", "automotive software", "ADAS camera"]:
        all_events.extend(search_eventbrite(query, "Poland"))
        time.sleep(2)

    # DDG search for niche events
    ddg_queries = [
        ("embedded linux conference", "Poland"),
        ("automotive ADAS meetup", "Europe"),
        ("camera imaging workshop", "Poland"),
        ("RISC-V event", "Europe"),
        ("konferencja embedded IoT", "Polska"),
        ("spotkanie linux kernel", "Polska"),
    ]
    for query, region in ddg_queries:
        all_events.extend(search_ddg_events(query, region))
        time.sleep(2)

    log(f"Total raw events: {len(all_events)}")

    # ── Phase 2: Score and filter ──
    log("Phase 2: Scoring events...")
    for event in all_events:
        score_event(event)

    # Filter: must have some relevance
    relevant = [e for e in all_events if e.get("combined_score", 0) > 0 or e.get("relevance", 0) > 0]
    relevant.sort(key=lambda e: e.get("combined_score", 0), reverse=True)

    # Dedup by name similarity
    seen_names = set()
    deduped = []
    for e in relevant:
        name_key = re.sub(r'[^a-z0-9]+', '', e.get("name", "").lower())[:30]
        if name_key and name_key not in seen_names:
            seen_names.add(name_key)
            deduped.append(e)

    log(f"After scoring/dedup: {len(deduped)} relevant events")

    # ── Phase 3: LLM analysis ──
    log("Phase 3: LLM event analysis...")
    analysis_raw = llm_analyze_events(deduped[:25])

    analysis = {}
    if analysis_raw:
        try:
            json_m = re.search(r'\{.*\}', analysis_raw, re.DOTALL)
            if json_m:
                analysis = json.loads(json_m.group())
        except json.JSONDecodeError:
            analysis = {"raw_analysis": analysis_raw[:2000]}

    # ── Update DB ──
    for e in deduped:
        eid = event_id(e)
        if eid not in db.get("events", {}):
            db.setdefault("events", {})[eid] = {
                "first_seen": today,
                "name": e.get("name", ""),
                "date": e.get("date", ""),
                "location": e.get("location", ""),
                "url": e.get("url", ""),
                "combined_score": e.get("combined_score", 0),
                "location_tier": e.get("location_tier", ""),
            }
        else:
            # Update score if higher
            existing = db["events"][eid]
            if e.get("combined_score", 0) > existing.get("combined_score", 0):
                existing["combined_score"] = e["combined_score"]
            existing["last_seen"] = today

    # ── Save output ──
    duration = int(time.time() - t0)
    output = {
        "meta": {
            "timestamp": dt.isoformat(timespec="seconds"),
            "duration_seconds": duration,
            "total_found": len(all_events),
            "relevant": len(deduped),
            "sources": list(set(e.get("source", "unknown") for e in all_events)),
        },
        "events": deduped[:50],  # top 50 by score
        "analysis": analysis,
    }

    fname = f"events-{dt.strftime('%Y%m%d')}.json"
    out_path = EVENT_DIR / fname
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    latest = EVENT_DIR / "latest-events.json"
    latest.unlink(missing_ok=True)
    latest.symlink_to(fname)

    save_db(db)

    # Cleanup: keep last 60 reports
    reports = sorted(EVENT_DIR.glob("events-2*.json"))
    for old in reports[:-60]:
        old.unlink(missing_ok=True)

    log(f"Saved: {out_path}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] event-scout done ({duration}s)", flush=True)


if __name__ == "__main__":
    main()
