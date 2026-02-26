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
    # V4L2 / media subsystem deep keywords
    "v4l2-subdev", "media controller api", "videobuf2", "dma-buf",
    "media subsystem", "camera pipeline", "video4linux",
    # Hardware interfaces
    "mipi csi-2", "i2c driver", "spi driver", "dma engine",
    "iommu", "device tree binding", "dt binding",
    # Embedded recipes / specific conferences
    "embedded recipes", "jesień linuksowa", "jesien linuksowa",
    "linux autumn", "code::dive", "codedive",
    # Edge/NPU
    "edge inference", "npu", "hardware accelerat",
    "own your silicon", "neural processing unit",
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
    # From research report
    "hackerspace", "hakierspejs", "hardware hacking", "reverse engineer",
    "electronics workshop", "soldering", "pcb",
    "bootlin", "collabora", "pengutronix", "linutronix",
    "free software", "foss", "floss",
    "plug", "polish linux", "linux user group",
    "qt embedded", "framebuffer",
    "zero-copy", "memory management", "dma",
    "microconference", "devroom", "lightning talk",
    "ceh", "c-embedded-hardware",
    "tenstorrent", "lora", "peft", "edge computing",
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
                     "rybnik", "toruń", "torun", "lublin", "szczecin",
                     "polska", "poland"],
        "label": "Poland",
        "travel_score": 5,
    },
    "tier4_europe": {
        "keywords": ["berlin", "prague", "praha", "vienna", "wien",
                     "amsterdam", "brussels", "bruxelles", "munich", "münchen",
                     "paris", "barcelona", "copenhagen", "stockholm",
                     "nice", "nuremberg", "nürnberg", "lyon", "toulouse",
                     "dublin", "hamburg", "zurich", "zürich", "helsinki",
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
    # ── Added from deep research ──
    {
        "name": "Jesień Linuksowa (Linux Autumn)",
        "url": "https://jesien.org/",
        "alt_urls": ["https://jesien.org/2025/en/", "https://jesien.org/2026/"],
        "keywords": ["jesień linuksowa", "linux autumn", "plug", "polish linux"],
        "relevance": 9,
    },
    {
        "name": "Embedded Recipes",
        "url": "https://embedded-recipes.org/",
        "alt_urls": ["https://embedded-recipes.org/2026/"],
        "keywords": ["embedded recipes", "isp", "libcamera", "yocto"],
        "relevance": 10,
    },
    {
        "name": "code::dive",
        "url": "https://codedive.pl/",
        "alt_urls": ["https://www.codedive.pl/"],
        "keywords": ["code::dive", "codedive", "c++ conference wrocław"],
        "relevance": 7,
    },
    {
        "name": "Kernel Recipes",
        "url": "https://kernel-recipes.org/",
        "alt_urls": ["https://kernel-recipes.org/en/2026/"],
        "keywords": ["kernel recipes", "linux kernel conference"],
        "relevance": 9,
    },
    {
        "name": "Linux Security Summit Europe",
        "url": "https://events.linuxfoundation.org/linux-security-summit-europe/",
        "alt_urls": [],
        "keywords": ["linux security summit"],
        "relevance": 5,
    },
]

# ── Community Sources (RSS / iCal / Discourse) ────────────────────────────
# Structured endpoints from hackerspaces, user groups, and local communities
# Tier priority maps to geographic weighting in scoring

COMMUNITY_SOURCES = [
    # ── Tier 1: Łódź ──
    {
        "name": "Hakierspejs Łódź (Mobilizon)",
        "type": "rss",
        "url": "https://mobilizon.pl/@hakierspejs_lodz/feed/atom",
        "alt_urls": [
            "https://mobilizon.pl/@hakierspejs_lodz/feed/rss",
            "https://hs-ldz.pl/events.xml",
        ],
        "city": "Łódź", "country": "Poland",
        "tier": "tier1_local",
        "keywords_boost": ["hardware", "electronics", "embedded", "hacking"],
        "relevance_base": 6,  # local community — always interesting
    },
    {
        "name": "Hakierspejs Łódź (Wiki)",
        "type": "html",
        "url": "https://wiki.hackerspaces.org/Hackerspace_Lodz",
        "alt_urls": ["https://hs-ldz.pl/"],
        "city": "Łódź", "country": "Poland",
        "tier": "tier1_local",
        "keywords_boost": ["open day", "workshop", "meeting", "spotkanie"],
        "relevance_base": 5,
    },
    {
        "name": "Crossweb Łódź Embedded/IoT",
        "type": "html",
        "url": "https://crossweb.pl/en/events/lodz/?category=development",
        "alt_urls": [],
        "city": "Łódź", "country": "Poland",
        "tier": "tier1_local",
        "keywords_boost": ["embedded", "c++", "hardware", "iot", "ceh"],
        "relevance_base": 5,
    },
    # ── Tier 2: Warsaw ──
    {
        "name": "Warszawski Hackerspace (iCal)",
        "type": "ical",
        "url": "https://hsp.sh/calendar.ics",
        "alt_urls": ["https://hackerspace.pl/calendar.ics"],
        "city": "Warsaw", "country": "Poland",
        "tier": "tier2_easy",
        "keywords_boost": ["hardware", "reverse engineer", "embedded",
                           "short talk", "lightning", "electronics"],
        "relevance_base": 5,
    },
    {
        "name": "Warsaw C++ Users Group",
        "type": "html",
        "url": "https://cpp.mimuw.edu.pl/",
        "alt_urls": [],
        "city": "Warsaw", "country": "Poland",
        "tier": "tier2_easy",
        "keywords_boost": ["c++", "systems", "memory", "performance",
                           "embedded", "low-level", "hardware"],
        "relevance_base": 5,
    },
    {
        "name": "Crossweb Warsaw Embedded/C++",
        "type": "html",
        "url": "https://crossweb.pl/en/events/warszawa/?category=development",
        "alt_urls": [],
        "city": "Warsaw", "country": "Poland",
        "tier": "tier2_easy",
        "keywords_boost": ["embedded", "c++", "iot", "hardware", "qt"],
        "relevance_base": 4,
    },
    # ── Tier 3: Poland (other cities) ──
    {
        "name": "Hackerspace Wrocław (Discourse)",
        "type": "rss",
        "url": "https://forum.hswro.org/c/public/public-ogloszenia/5.rss",
        "alt_urls": [
            "https://forum.hswro.org/c/public/public-ogloszenia/5.json",
        ],
        "city": "Wrocław", "country": "Poland",
        "tier": "tier3_poland",
        "keywords_boost": ["hardware", "embedded", "workshop", "electronics"],
        "relevance_base": 4,
    },
    {
        "name": "Spejs Kraków",
        "type": "html",
        "url": "https://spejs.pl/",
        "alt_urls": ["https://wiki.hackerspaces.org/Spejs"],
        "city": "Kraków", "country": "Poland",
        "tier": "tier3_poland",
        "keywords_boost": ["hardware", "embedded", "hacking", "electronics"],
        "relevance_base": 3,
    },
    {
        "name": "Crossweb PL — Embedded Meetups",
        "type": "html",
        "url": "https://crossweb.pl/en/events/?category=development&tag=embedded",
        "alt_urls": [
            "https://crossweb.pl/en/events/?category=iot",
        ],
        "city": "", "country": "Poland",
        "tier": "tier3_poland",
        "keywords_boost": ["embedded", "iot", "automotive", "hardware"],
        "relevance_base": 4,
    },
    # ── Tier 4: Europe (major conferences — structured feeds) ──
    {
        "name": "FOSDEM Schedule",
        "type": "xml_schedule",
        "url": "https://fosdem.org/2026/schedule/xml",
        "alt_urls": [
            "https://fosdem.org/2027/schedule/xml",
            "https://fosdem.org/2026/schedule/xml/",
        ],
        "city": "Brussels", "country": "Belgium",
        "tier": "tier4_europe",
        # Target devrooms for embedded/mobile/microkernel
        "keywords_boost": ["embedded", "automotive", "mobile", "microkernel",
                           "camera", "v4l2", "kernel", "boot", "hardware",
                           "risc-v", "fpga", "driver"],
        "devroom_filter": ["embedded", "mobile", "automotive", "kernel",
                          "microkernel", "risc-v", "hardware", "bsd"],
        "relevance_base": 7,
    },
    {
        "name": "Embedded Recipes",
        "type": "rss",
        "url": "https://embedded-recipes.org/feed/",
        "alt_urls": ["https://embedded-recipes.org/2026/feed/"],
        "city": "Nice", "country": "France",
        "tier": "tier4_europe",
        "keywords_boost": ["embedded", "isp", "libcamera", "v4l2", "yocto",
                           "bootlin", "kernel", "driver"],
        "relevance_base": 8,
    },
    {
        "name": "Kernel Recipes",
        "type": "rss",
        "url": "https://kernel-recipes.org/feed/",
        "alt_urls": ["https://kernel-recipes.org/en/2026/feed/"],
        "city": "Paris", "country": "France",
        "tier": "tier4_europe",
        "keywords_boost": ["kernel", "driver", "media", "v4l2", "subsystem"],
        "relevance_base": 8,
    },
    {
        "name": "Linux Plumbers Conference",
        "type": "html",
        "url": "https://lpc.events/",
        "alt_urls": ["https://lpc.events/event/18/"],
        "city": "Prague", "country": "Czechia",
        "tier": "tier4_europe",
        "keywords_boost": ["media", "camera", "v4l2", "microconference",
                           "kernel", "plumbing"],
        "relevance_base": 9,
    },
    {
        "name": "Embedded World Press/News",
        "type": "rss",
        "url": "https://www.embedded-world.de/en/press/feed.xml",
        "alt_urls": ["https://www.embedded-world.de/en/news/feed.xml"],
        "city": "Nuremberg", "country": "Germany",
        "tier": "tier4_europe",
        "keywords_boost": ["soc", "camera", "sensor", "automotive",
                           "embedded", "arm", "risc-v"],
        "relevance_base": 5,
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


# ── Source: Community RSS/Atom feeds ───────────────────────────────────────

def parse_rss_events(xml_text, source_name, city="", country=""):
    """Parse RSS or Atom feed XML into event dicts."""
    events = []
    if not xml_text:
        return events

    cutoff = datetime.now() - timedelta(days=60)

    # Try Atom format first (more common for Mobilizon/Discourse)
    # <entry> ... <title>X</title> <updated>Y</updated> <link href="Z"/> <content>W</content>
    entries = re.findall(r'<entry>(.*?)</entry>', xml_text, re.DOTALL)
    if entries:
        for entry in entries[:25]:
            title = ""
            date = ""
            link = ""
            desc = ""

            t_m = re.search(r'<title[^>]*>(.*?)</title>', entry, re.DOTALL)
            if t_m:
                title = strip_html(t_m.group(1)).strip()

            # Atom dates: <updated>, <published>
            d_m = re.search(r'<(?:updated|published)>(.*?)</(?:updated|published)>', entry)
            if d_m:
                date = d_m.group(1).strip()[:25]

            l_m = re.search(r'<link[^>]*href="([^"]+)"', entry)
            if l_m:
                link = l_m.group(1)

            c_m = re.search(r'<(?:content|summary)[^>]*>(.*?)</(?:content|summary)>', entry, re.DOTALL)
            if c_m:
                desc = strip_html(c_m.group(1))[:500]

            if title:
                # Skip old posts
                skip = False
                if date:
                    for dfmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]:
                        try:
                            if datetime.strptime(date[:19], dfmt) < cutoff:
                                skip = True
                        except ValueError:
                            continue
                if not skip:
                    events.append({
                        "name": title, "date": date, "url": link,
                        "source": f"community_rss:{source_name}",
                        "city": city, "country": country,
                        "location": f"{city}, {country}" if city else country,
                        "description": desc,
                    })
        return events

    # Fallback to RSS 2.0: <item> ... <title>X</title> <pubDate>Y</pubDate> ...
    items = re.findall(r'<item>(.*?)</item>', xml_text, re.DOTALL)
    for item in items[:25]:
        title = ""
        date = ""
        link = ""
        desc = ""

        t_m = re.search(r'<title[^>]*>(.*?)</title>', item, re.DOTALL)
        if t_m:
            title = strip_html(t_m.group(1)).strip()

        d_m = re.search(r'<pubDate>(.*?)</pubDate>', item)
        if d_m:
            date = d_m.group(1).strip()[:25]

        l_m = re.search(r'<link[^>]*>(.*?)</link>', item)
        if not l_m:
            l_m = re.search(r'<link[^>]*href="([^"]+)"', item)
        if l_m:
            link = l_m.group(1).strip()

        d_m = re.search(r'<description[^>]*>(.*?)</description>', item, re.DOTALL)
        if d_m:
            desc = strip_html(d_m.group(1))[:500]

        if title:
            # Skip old posts
            skip = False
            if date:
                # Try RFC 2822 (pubDate) and ISO formats
                for dfmt in ["%a, %d %b %Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]:
                    try:
                        if datetime.strptime(date[:25].strip().rstrip(" +0000 GMT UTC"), dfmt) < cutoff:
                            skip = True
                        break
                    except ValueError:
                        continue
            if not skip:
                events.append({
                    "name": title, "date": date, "url": link,
                    "source": f"community_rss:{source_name}",
                    "city": city, "country": country,
                    "location": f"{city}, {country}" if city else country,
                    "description": desc,
                })

    return events


def parse_ical_events(ics_text, source_name, city="", country=""):
    """Parse iCalendar .ics text into event dicts."""
    events = []
    if not ics_text:
        return events

    # Split into VEVENT blocks
    vevents = re.findall(
        r'BEGIN:VEVENT(.*?)END:VEVENT', ics_text, re.DOTALL
    )

    for vevent in vevents[:30]:
        title = ""
        date = ""
        location = ""
        desc = ""
        url = ""

        # SUMMARY
        s_m = re.search(r'SUMMARY[^:]*:(.*)', vevent)
        if s_m:
            title = s_m.group(1).strip().replace('\\n', ' ').replace('\\,', ',')

        # DTSTART
        d_m = re.search(r'DTSTART[^:]*:(\d{4}\d{2}\d{2})', vevent)
        if d_m:
            raw = d_m.group(1)
            date = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"

        # LOCATION
        l_m = re.search(r'LOCATION[^:]*:(.*)', vevent)
        if l_m:
            location = l_m.group(1).strip().replace('\\n', ' ').replace('\\,', ',')

        # DESCRIPTION
        desc_m = re.search(r'DESCRIPTION[^:]*:(.*?)(?=^[A-Z])', vevent, re.DOTALL | re.MULTILINE)
        if desc_m:
            desc = desc_m.group(1).strip().replace('\\n', ' ').replace('\\,', ',')[:500]

        # URL
        u_m = re.search(r'URL[^:]*:(.*)', vevent)
        if u_m:
            url = u_m.group(1).strip()

        if not title:
            continue

        # Skip past events
        if date:
            try:
                ev_date = datetime.strptime(date, "%Y-%m-%d")
                if ev_date < datetime.now() - timedelta(days=7):
                    continue
            except ValueError:
                pass

        events.append({
            "name": title, "date": date, "url": url,
            "source": f"community_ical:{source_name}",
            "city": city, "country": country,
            "location": location or (f"{city}, {country}" if city else country),
            "description": desc,
        })

    return events


def parse_fosdem_xml(xml_text, devroom_filter=None):
    """Parse FOSDEM Pentabarf XML schedule, filtering by devroom."""
    events = []
    if not xml_text:
        return events

    # Extract <event> blocks with their containing <room>
    # Structure: <room name="X"> ... <event> ... </event> ... </room>
    rooms = re.findall(
        r'<room\s+name="([^"]+)">(.*?)</room>', xml_text, re.DOTALL
    )

    for room_name, room_content in rooms:
        # Apply devroom filter if specified
        if devroom_filter:
            room_lower = room_name.lower()
            if not any(f in room_lower for f in devroom_filter):
                continue

        event_blocks = re.findall(r'<event[^>]*>(.*?)</event>', room_content, re.DOTALL)

        for block in event_blocks:
            title = ""
            date = ""
            desc = ""
            track = ""
            persons = ""

            t_m = re.search(r'<title>(.*?)</title>', block)
            if t_m:
                title = strip_html(t_m.group(1))

            d_m = re.search(r'<date>(.*?)</date>', block)
            if d_m:
                date = d_m.group(1).strip()

            tr_m = re.search(r'<track>(.*?)</track>', block)
            if tr_m:
                track = strip_html(tr_m.group(1))

            abs_m = re.search(r'<abstract>(.*?)</abstract>', block, re.DOTALL)
            if abs_m:
                desc = strip_html(abs_m.group(1))[:500]

            p_m = re.findall(r'<person[^>]*>(.*?)</person>', block)
            if p_m:
                persons = ", ".join(strip_html(p) for p in p_m[:3])

            if title:
                events.append({
                    "name": f"[FOSDEM] {title}",
                    "date": date,
                    "url": "",  # FOSDEM URLs built from slug
                    "source": "fosdem_schedule",
                    "city": "Brussels", "country": "Belgium",
                    "location": "Brussels, Belgium",
                    "description": f"Room: {room_name}. Track: {track}. {desc}",
                    "topics": f"{track} {room_name} {desc}",
                    "speakers": persons,
                })

    return events


def search_community_sources():
    """Scan all community sources (hackerspaces, user groups, conference feeds)."""
    all_events = []

    for src in COMMUNITY_SOURCES:
        name = src["name"]
        stype = src["type"]
        city = src.get("city", "")
        country = src.get("country", "")
        tier = src.get("tier", "")
        base_relevance = src.get("relevance_base", 3)
        boost_kw = src.get("keywords_boost", [])

        log(f"  Community: {name}")
        events = []

        # Try primary URL, fall back to alternates
        urls_to_try = [src["url"]] + src.get("alt_urls", [])
        text = None
        for url in urls_to_try:
            text = fetch_url(url, timeout=20)
            if text:
                break
            time.sleep(1)

        if not text:
            log(f"    → no data from any URL")
            time.sleep(1)
            continue

        if stype == "rss":
            events = parse_rss_events(text, name, city, country)

        elif stype == "ical":
            events = parse_ical_events(text, name, city, country)

        elif stype == "xml_schedule":
            devroom_filter = src.get("devroom_filter", None)
            events = parse_fosdem_xml(text, devroom_filter)

        elif stype == "html":
            # Generic HTML scraping — extract event-like blocks
            page_text = strip_html(text)[:8000]

            # Look for event/meetup/workshop/spotkanie patterns
            # Extract anything that looks like a dated event
            patterns = [
                # "Title — Date" or "Title, Date" patterns
                r'([A-ZŁŹŻŚĆŃÓĄĘa-ząćęłńóśźż][^\n]{5,80})\s*[-–—,]\s*(\d{1,2}[./]\d{1,2}[./]\d{2,4})',
                r'(\d{1,2}[./]\d{1,2}[./]\d{2,4})\s*[-–—,]\s*([A-ZŁŹŻa-z][^\n]{5,80})',
                # "Month DD" style
                r'([A-Z][a-z]+ \d{1,2}(?:[-–]\d{1,2})?,?\s*\d{4})[^\n]*?([A-Z][^\n]{5,60})',
            ]

            for pat in patterns:
                matches = re.findall(pat, page_text)
                for m in matches[:10]:
                    # m is (title, date) or (date, title)
                    a, b = m[0].strip(), m[1].strip()
                    # Heuristic: the one with digits is the date
                    if re.search(r'\d{4}|\d{1,2}[./]\d', a):
                        date_str, title = a, b
                    else:
                        title, date_str = a, b

                    events.append({
                        "name": title[:100],
                        "date": date_str,
                        "url": src["url"],
                        "source": f"community_html:{name}",
                        "city": city, "country": country,
                        "location": f"{city}, {country}" if city else country,
                        "description": "",
                    })

            # If no dated events found, still record the source as a "hub"
            # but with low relevance so it doesn't outrank real events
            if not events:
                # Check if page text contains any interesting keywords
                page_lower = page_text.lower()
                kw_hits = [kw for kw in boost_kw if kw in page_lower]
                if kw_hits:
                    events.append({
                        "name": f"{name} — active community hub",
                        "date": "",
                        "url": src["url"],
                        "source": f"community_hub:{name}",
                        "city": city, "country": country,
                        "location": f"{city}, {country}" if city else country,
                        "description": f"Active community with keywords: {', '.join(kw_hits)}. Check directly for schedule.",
                        "_is_hub": True,  # mark for lower scoring
                    })

        # For RSS/Atom sources: filter out posts that have NO keyword relevance
        # (Discourse feeds include all forum topics — concerts, moving, etc.)
        if stype == "rss" and events:
            relevance_kws = set(boost_kw + [
                "embedded", "linux", "kernel", "hardware", "workshop",
                "meetup", "conference", "hackathon", "devops",
                "c++", "programming", "hacking", "electronics",
                "iot", "automotive", "driver", "camera", "fpga",
                "risc-v", "arm", "yocto", "python", "rust",
                "sensor", "firmware", "microcontroller", "pcb",
            ])
            filtered = []
            for ev in events:
                ev_text = f"{ev.get('name', '')} {ev.get('description', '')}".lower()
                if any(kw in ev_text for kw in relevance_kws):
                    filtered.append(ev)
            events = filtered

        # Apply relevance boost from community source config
        for ev in events:
            ev_text = f"{ev.get('name', '')} {ev.get('description', '')}".lower()
            boost = sum(1 for kw in boost_kw if kw in ev_text)
            if ev.get("_is_hub"):
                # Hub entries: cap at 2 so they don't outrank real events
                ev["community_relevance"] = min(2, base_relevance)
            else:
                ev["community_relevance"] = base_relevance + boost
            ev["community_tier"] = tier

        all_events.extend(events)
        log(f"    → {len(events)} events")
        time.sleep(2)

    log(f"Community sources total: {len(all_events)} events")
    return all_events


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
Community sources include local hackerspaces (Hakierspejs Łódź, Warsaw Hackerspace),
user groups (CEH UG, Warsaw C++), and European conferences (FOSDEM, Embedded Recipes,
Linux Plumbers, Kernel Recipes). Prioritize Łódź > Warsaw > Poland > Europe.

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

    # Community sources (hackerspaces, user groups, conference feeds)
    all_events.extend(search_community_sources())
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
        ("embedded systems", "Łódź"),
        ("C++ hardware", "Warsaw"),
        ("automotive software", "Poland"),
        ("IoT embedded", "Łódź"),
        ("camera imaging", "Warsaw"),
        ("kernel linux", "Poland"),
        ("Qt embedded", "Warsaw"),
        ("hackerspace", "Łódź"),
        ("hackerspace", "Warsaw"),
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
        # From research report — targeted searches
        ("hakierspejs łódź spotkanie", "Polska"),
        ("CEH embedded meetup łódź", "Polska"),
        ("C-Embedded-Hardware user group", "Poland"),
        ("embedded meetup katowice wrocław", "Polska"),
        ("jesień linuksowa 2026", "Polska"),
        ("embedded recipes 2026 conference", "Europe"),
        ("linux plumbers conference 2026 media microconference", "Europe"),
        ("FOSDEM embedded automotive devroom 2026", "Europe"),
        ("code::dive conference wrocław 2026", "Polska"),
    ]
    for query, region in ddg_queries:
        all_events.extend(search_ddg_events(query, region))
        time.sleep(2)

    log(f"Total raw events: {len(all_events)}")

    # ── Phase 2: Score and filter ──
    log("Phase 2: Scoring events...")
    for event in all_events:
        score_event(event)

    # Merge community_relevance into combined_score if not yet scored
    for e in all_events:
        comm_rel = e.get("community_relevance", 0)
        if comm_rel > 0 and e.get("combined_score", 0) == 0:
            e["combined_score"] = comm_rel
        elif comm_rel > 0:
            e["combined_score"] = max(e.get("combined_score", 0), comm_rel)

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

    # Regenerate dashboard HTML
    try:
        import subprocess as _sp
        _sp.run(["python3", "/opt/netscan/generate-html.py"],
               capture_output=True, timeout=60)
        log("Dashboard HTML regenerated")
    except Exception:
        pass

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] event-scout done ({duration}s)", flush=True)


if __name__ == "__main__":
    main()
