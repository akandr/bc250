#!/usr/bin/env python3
"""radio-scan.py — Radioscanner.pl forum scraper for radio activity monitoring.

Scrapes the radioscanner.pl MyBB forum for recent threads about:
  - Local radio activity (Łódź, 93 region)
  - Space / satellite communications (NOAA, ISS, AO-91, etc.)
  - Shortwave / HF DX catches and monitoring
  - Ham radio / amateur radio activity
  - Broadcast FM/AM/DAB news

Requires login (MyBB with CSRF token).
Credentials stored in /opt/netscan/radio-creds.json (plaintext).

Output: /opt/netscan/data/radio/radio-latest.json

Usage:
    radio-scan.py                   (full scan)
    radio-scan.py --dry-run         (fetch only, no save)

Schedule (cron): every 6 hours
    0 */6 * * *  /usr/bin/python3 /opt/netscan/radio-scan.py

Location on bc250: /opt/netscan/radio-scan.py
"""

import argparse
import json
import os
import re
import ssl
import sys
import time
import gzip
import hashlib
import http.cookiejar
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from llm_sanitize import sanitize_llm_output

# ─── Config ───

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = "/opt/netscan/data"
RADIO_DIR = os.path.join(DATA_DIR, "radio")
CREDS_PATH = "/opt/netscan/radio-creds.json"
PROFILE_PATH = "/opt/netscan/profile.json"

BASE_URL = "https://radioscanner.pl"
LOGIN_URL = f"{BASE_URL}/member.php"

# ── Ollama LLM ──
OLLAMA_URL = "http://localhost:11434"
OLLAMA_CHAT = f"{OLLAMA_URL}/api/chat"
OLLAMA_MODEL = "qwen3:14b"

# ── Signal notifications ──
SIGNAL_RPC = "http://127.0.0.1:8080/api/v1/rpc"
SIGNAL_FROM = "+<BOT_PHONE>"
SIGNAL_TO = "+<OWNER_PHONE>"

# Forum sections to scrape (fid -> metadata)
FORUM_SECTIONS = {
    5:  {"name": "Nasłuchy",           "category": "monitoring",  "emoji": "📡"},
    37: {"name": "Częstotliwości",     "category": "frequencies", "emoji": "📻"},
    40: {"name": "Satelity",           "category": "satellite",   "emoji": "🛰️"},
    41: {"name": "KF (Shortwave)",     "category": "shortwave",   "emoji": "🌊"},
    22: {"name": "Krótkofalarstwo",    "category": "ham",         "emoji": "📟"},
    55: {"name": "Broadcast FM/AM",    "category": "broadcast",   "emoji": "📺"},
    38: {"name": "Komunikacja Radiowa","category": "comms",       "emoji": "🔊"},
    36: {"name": "AirBand",            "category": "airband",     "emoji": "✈️"},
}

# Thread age cutoff (days) — only include threads with activity in this window
THREAD_AGE_DAYS = 7

# Keywords for relevance scoring
KEYWORDS_HIGH = [
    "łódź", "lodz", "93", "łódzk", "lodzk",
    "noaa", "iss", "amsat", "ao-91", "ao-92", "meteor-m", "sstv",
    "satelit", "sat ", "satkom", "satcom",
    "dx", "propagacj", "sporadic", "e-skip", "tropo",
    "tetra", "dmr", "p25", "nxdn", "dstar", "d-star", "c4fm",
    "sdr", "rtl-sdr", "hackrf", "airspy",
    # Wardriving & WiFi hacking
    "wardriving", "wardrive", "wifi hack", "wpa", "wpa2", "wpa3",
    "kismet", "wigle", "wifite", "aircrack", "deauth",
    "flipperzero", "flipper zero", "pwnagotchi",
    # Bluetooth hacking
    "bluetooth", "ble", "blueborne", "btlejuice", "ubertooth",
    "bluetooth hack", "bluetooth sniff", "btle",
    # Satellite phone / Iridium / Inmarsat / Thuraya
    "iridium", "inmarsat", "thuraya", "satphone", "sat phone",
    "globalstar", "orbcomm",
    # Fun satellites to listen
    "cubesat", "funcube", "fox-1", "amsat", "ao-91", "ao-92",
    "eo-88", "lilacsat", "cas-4", "xw-2",
    "goes", "hrpt", "lrpt", "apt",
    "starlink", "gps", "gnss", "galileo", "glonass",
    # Practical RF attacks
    "replay attack", "jamm", "spoof", "evil twin",
    "imsi catcher", "stingray", "gsm crack",
    "rf hack", "radio hack",
]

KEYWORDS_MEDIUM = [
    "nasłuch", "monitoring", "skan", "skaner",
    "krótkofalow", "kf ", "hf ", "uhf", "vhf",
    "antena", "antenna", "dipol", "yagi",
    "icom", "yaesu", "kenwood", "baofeng",
    "fm ", "dab", "am ", "broadcast",
    "lotnic", "airband", "air ", "119.", "118.",
    "morsk", "marine", "ais",
    "straż", "policj", "pogotow", "ratunk",
    "space", "kosm", "rakiet", "starlink",
    "signal", "sygnał",
    # Extended wireless security
    "wifi", "wlan", "802.11", "hotspot", "captive portal",
    "zigbee", "z-wave", "lora", "lorawan", "meshtastic",
    "ads-b", "adsb", "dump1090", "fr24",
    "nrf24", "nrf52", "cc1101", "rfcat",
    "gnuradio", "gnu radio", "sdr++", "sdr#", "gqrx",
    "osmocom", "gr-gsm", "kalibrate",
    # Satellite & space
    "meteor", "fengyun", "metop", "weather sat",
    "ham satellite", "transponder", "beacon",
]

# SSL context (some MyBB sites have cert issues)
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = True
SSL_CTX.verify_mode = ssl.CERT_REQUIRED

# ─── Utilities ───

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# ─── Ollama LLM ───

def call_ollama(system_prompt, user_prompt, temperature=0.3, max_tokens=2000):
    """Call Ollama for LLM analysis. Returns text or None."""
    # Health check
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=10) as r:
            tags = json.loads(r.read())
            models = [m["name"] for m in tags.get("models", [])]
            if not any(OLLAMA_MODEL in m for m in models):
                log(f"  Model {OLLAMA_MODEL} not available")
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
        "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": 24576},
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
            # Strip thinking/reasoning preamble (qwen3 sometimes leaks CoT)
            content = _strip_thinking(content)
            return content
    except Exception as e:
        log(f"  Ollama call failed: {e}")
        return None


def _strip_thinking(text):
    """Remove chain-of-thought reasoning and Chinese text from LLM output."""
    return sanitize_llm_output(text) if text else text


# ─── Signal Notification ───

def signal_send(msg):
    """Send Signal notification."""
    try:
        payload = json.dumps({
            "jsonrpc": "2.0", "method": "send",
            "params": {
                "account": SIGNAL_FROM,
                "recipient": [SIGNAL_TO],
                "message": msg,
            },
            "id": "radio-scan",
        })
        req = urllib.request.Request(
            SIGNAL_RPC, data=payload.encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception as e:
        log(f"  Signal send failed: {e}")
        return False


# ─── LLM Radio Analysis ───

def llm_analyze_radio(highlights, all_threads, section_stats):
    """Use local LLM to analyze radio scan findings and produce an AI briefing."""
    if not highlights:
        log("  No highlights to analyze — skipping LLM")
        return ""

    # Load profile for context (interests, location)
    profile = load_json(PROFILE_PATH) or {}
    user_name = profile.get("name", "operator")
    user_location = profile.get("location", "Łódź, Poland")
    interests = profile.get("interests", [])
    interests_str = ", ".join(interests) if interests else "radio monitoring, SDR, satellite comms, shortwave DX"

    # Prepare highlights for LLM
    highlights_text = ""
    for i, h in enumerate(highlights[:20]):
        preview_snip = h.get("preview", "")[:200]
        highlights_text += (
            f"{i+1}. [{h.get('score', 0)}] {h.get('section_emoji', '📻')} {h.get('section', '?')} — {h.get('title', '?')}\n"
            f"   Category: {h.get('category', '?')}, Reasons: {', '.join(h.get('reasons', []))}\n"
        )
        if preview_snip:
            highlights_text += f"   Preview: {preview_snip}\n"
        highlights_text += "\n"

    # Section activity summary
    section_summary = ""
    for name, st in sorted(section_stats.items(), key=lambda x: -x[1].get("recent", 0)):
        section_summary += f"  {st.get('emoji', '📻')} {name}: {st.get('recent', 0)} recent / {st.get('total', 0)} total threads\n"

    # Thread category breakdown
    cat_counts = {}
    for t in all_threads:
        cat = t.get("section_category", "other")
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    cat_str = ", ".join(f"{c}: {n}" for c, n in sorted(cat_counts.items(), key=lambda x: -x[1]))

    system_prompt = f"""You are an AI radio monitoring & wireless security analyst for {user_name}, a radio enthusiast located in {user_location}.
Their interests include: {interests_str}.

Your job is to analyze scraped forum threads from radioscanner.pl (the largest Polish radio monitoring community)
and produce a concise intelligence briefing about what's happening in the radio world that's relevant to this operator.

Focus on:
1. Local activity near Łódź / region 93 — any monitoring reports, new frequencies, interesting catches
2. Satellite communications — NOAA APT, Meteor-M LRPT, ISS SSTV, AMSAT, cubesat signals, fun satellites to listen to
3. Shortwave / HF propagation — DX catches, sporadic-E, tropo openings, propagation forecasts
4. SDR & equipment — new SDR releases, antenna builds, software updates (SDR#, SDR++, GNURadio)
5. Airband & scanner activity — aviation monitoring, ADS-B, trunked radio (TETRA, DMR, P25)
6. Any unusual or emergency signals reported by the community
7. **WARDRIVING & WIFI SECURITY** — wardriving reports, WiFi hacking techniques (WPA2/WPA3 cracking,
   deauth attacks, evil twin APs, Pwnagotchi/Flipper Zero captures), Kismet/WiGLE mapping,
   practical techniques the operator could try at home with RTL-SDR or HackRF
8. **BLUETOOTH HACKING** — BLE sniffing, Ubertooth captures, BlueBorne-style vulnerabilities,
   BtleJuice MITM, practical Bluetooth reconnaissance & attack techniques
9. **SATELLITE PHONE SNIFFING** — Iridium/Inmarsat/Thuraya/Globalstar signal monitoring,
   L-band reception, pager decoding, ORBCOMM data capture
10. **FUN SATELLITES** — cubesats worth listening to (FunCube, AO-91, AO-92, LILACSAT),
    weather satellite imaging (GOES HRPT, Meteor-M LRPT, NOAA APT), GPS/GNSS experiments,
    upcoming satellite passes, DIY ground station ideas

Structure your briefing as:
- SITUATION OVERVIEW (2-3 sentences on overall radio activity level)
- KEY FINDINGS (bullet points, most interesting items, with WHY they matter)
- PROPAGATION & CONDITIONS (if any propagation reports found)
- WIRELESS SECURITY & HACKING (any wardriving, WiFi, Bluetooth, or RF attack news — prioritize
  practical techniques the user could experiment with at home safely and legally)
- SATELLITE CORNER (interesting satellites, upcoming passes, new reception techniques)
- RECOMMENDATIONS (what to tune into, when to listen, what to try, any upcoming events)

Be concise and actionable. Write in English. Output plain text, no markdown formatting.
For the security/hacking section, focus on educational and research aspects."""

    user_prompt = f"""Radio scanner forum scan completed. Here are the results from radioscanner.pl:

SECTION ACTIVITY:
{section_summary}
CATEGORY BREAKDOWN: {cat_str}
Total threads scanned: {len(all_threads)}, Highlights (score≥25): {len(highlights)}

HIGHLIGHTED THREADS (scored by relevance):
{highlights_text}

Analyze these findings and produce an intelligence briefing. What should {user_name} know about?
What should they listen to? Are there any time-sensitive opportunities (propagation openings, satellite passes, events)?"""

    log("🤖 Running LLM radio analysis...")
    analysis = call_ollama(system_prompt, user_prompt, temperature=0.4, max_tokens=1500)
    return analysis or ""


class ThreadHTMLParser(HTMLParser):
    """Parse MyBB forumdisplay.php HTML to extract thread listings."""

    def __init__(self):
        super().__init__()
        self.threads = []
        self._in_thread_link = False
        self._current = {}
        self._capture_date = False
        self._last_text = ""

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        # Thread links: <a href="showthread.php?tid=XXXX" ...>
        href = attrs_d.get("href", "")
        if tag == "a" and "showthread.php?tid=" in href:
            m = re.search(r'tid=(\d+)', href)
            if m:
                tid = m.group(1)
                title = unescape(attrs_d.get("title", ""))
                # Skip "action=lastpost" variant — we want the normal title link
                if "action=lastpost" not in href or not title:
                    if not title:
                        # Will capture text content
                        self._in_thread_link = True
                        self._current = {"tid": tid, "title": "", "href": href}
                    else:
                        self._current = {"tid": tid, "title": title, "href": href}
                elif "action=lastpost" in href and title:
                    # Use lastpost link as it has the title in the title attr
                    if not any(t.get("tid") == tid for t in self.threads):
                        self._current = {"tid": tid, "title": title, "href": href}
                        self.threads.append(self._current)
                        self._current = {}

        # Date spans: <span title="DD-MM-YYYY, HH:MM" ...>
        if tag == "span":
            span_title = attrs_d.get("title", "")
            if re.match(r'\d{2}-\d{2}-\d{4}', span_title) and self.threads:
                self.threads[-1]["date_raw"] = span_title

    def handle_data(self, data):
        if self._in_thread_link:
            self._current["title"] += data.strip()
        self._last_text = data.strip()

    def handle_endtag(self, tag):
        if tag == "a" and self._in_thread_link:
            self._in_thread_link = False
            if self._current.get("title") and self._current.get("tid"):
                if not any(t.get("tid") == self._current["tid"] for t in self.threads):
                    self.threads.append(self._current)
            self._current = {}


# ─── Session / Auth ───

class RadioScanner:
    def __init__(self):
        self.cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookie_jar),
            urllib.request.HTTPSHandler(context=SSL_CTX),
        )
        self.opener.addheaders = [
            ("User-Agent", "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"),
            ("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
            ("Accept-Language", "pl,en-US;q=0.7,en;q=0.3"),
            ("Accept-Encoding", "gzip, deflate"),
        ]

    def _fetch(self, url, data=None, max_retries=2):
        """Fetch a URL, handle gzip, return text."""
        for attempt in range(max_retries + 1):
            try:
                if data and isinstance(data, str):
                    data = data.encode("utf-8")
                req = urllib.request.Request(url, data=data)
                resp = self.opener.open(req, timeout=30)
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8", errors="replace")
            except Exception as ex:
                if attempt < max_retries:
                    log(f"  retry {attempt+1} for {url}: {ex}")
                    time.sleep(2 * (attempt + 1))
                else:
                    log(f"  FAILED {url}: {ex}")
                    return ""

    def login(self, username, password):
        """Login to MyBB forum with CSRF token handling."""
        log(f"Logging in as {username}...")

        # Step 1: GET login page to extract CSRF token (my_post_key)
        login_page = self._fetch(f"{LOGIN_URL}?action=login")
        if not login_page:
            log("  ERROR: Could not fetch login page")
            return False

        m = re.search(r'name="my_post_key"\s+value="([^"]+)"', login_page)
        if not m:
            log("  ERROR: Could not find CSRF token (my_post_key)")
            return False
        post_key = m.group(1)

        # Step 2: POST login credentials
        login_data = urllib.parse.urlencode({
            "action": "do_login",
            "url": f"{BASE_URL}/index.php",
            "my_post_key": post_key,
            "username": username,
            "password": password,
            "remember": "yes",
            "submit": "Zaloguj",
        })

        result = self._fetch(LOGIN_URL, data=login_data)
        if not result:
            log("  ERROR: Login POST failed")
            return False

        # Verify login success — check for username in response
        if username.lower() in result.lower():
            log(f"  Login successful (found '{username}' in response)")
            return True
        else:
            log("  WARNING: Login may have failed (username not found in response)")
            # Still continue — cookies might be set
            return True

    def fetch_forum_section(self, fid, pages=1):
        """Fetch thread list from a forum section."""
        all_threads = []
        for page in range(1, pages + 1):
            url = f"{BASE_URL}/forumdisplay.php?fid={fid}"
            if page > 1:
                url += f"&page={page}"

            html = self._fetch(url)
            if not html:
                continue

            parser = ThreadHTMLParser()
            parser.feed(html)
            all_threads.extend(parser.threads)
            time.sleep(0.5)  # Be polite

        # Deduplicate by tid
        seen = set()
        unique = []
        for t in all_threads:
            if t["tid"] not in seen:
                seen.add(t["tid"])
                unique.append(t)
        return unique

    def fetch_thread_preview(self, tid, max_chars=2000):
        """Fetch first post content of a thread for LLM analysis."""
        url = f"{BASE_URL}/showthread.php?tid={tid}"
        html = self._fetch(url)
        if not html:
            return ""

        # Extract post content — MyBB uses <div class="post_body" ...>
        m = re.search(r'<div[^>]*class="post_body"[^>]*>(.*?)</div>', html, re.DOTALL)
        if not m:
            # Fallback: try post_content
            m = re.search(r'<div[^>]*id="pid_\d+"[^>]*>(.*?)</div>\s*</div>', html, re.DOTALL)
        if not m:
            return ""

        text = m.group(1)
        # Strip HTML tags
        text = re.sub(r'<br\s*/?>', '\n', text)
        text = re.sub(r'<[^>]+>', '', text)
        text = unescape(text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()[:max_chars]


# ─── Scoring ───

def score_thread(thread, section_info):
    """Score a thread for relevance (0-100)."""
    title = thread.get("title", "").lower()
    preview = thread.get("preview", "").lower()
    combined = f"{title} {preview}"
    score = 0
    reasons = []

    # Category base scores
    cat = section_info.get("category", "")
    if cat == "satellite":
        score += 20
        reasons.append("satellite")
    elif cat == "shortwave":
        score += 15
        reasons.append("shortwave")
    elif cat == "monitoring":
        score += 10
        reasons.append("monitoring")
    elif cat == "ham":
        score += 5
    elif cat == "airband":
        score += 10
        reasons.append("airband")

    # Keyword matching
    for kw in KEYWORDS_HIGH:
        if kw in combined:
            score += 15
            reasons.append(kw)
    for kw in KEYWORDS_MEDIUM:
        if kw in combined:
            score += 5
            # Don't add too many medium reasons
            if len(reasons) < 8:
                reasons.append(kw)

    # Recency boost
    if thread.get("age_days") is not None:
        if thread["age_days"] <= 1:
            score += 15
            reasons.append("today")
        elif thread["age_days"] <= 3:
            score += 10
            reasons.append("recent")
        elif thread["age_days"] <= 7:
            score += 5

    # Cap at 100
    score = min(score, 100)

    # Deduplicate reasons
    seen = set()
    unique_reasons = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            unique_reasons.append(r)

    return score, unique_reasons


def parse_thread_date(date_raw):
    """Parse MyBB date string like '04-02-2026, 15:30' or relative dates."""
    if not date_raw:
        return None

    # Try DD-MM-YYYY, HH:MM format
    m = re.match(r'(\d{2})-(\d{2})-(\d{4}),?\s*(\d{2}):(\d{2})', date_raw)
    if m:
        day, month, year, hour, minute = m.groups()
        try:
            return datetime(int(year), int(month), int(day), int(hour), int(minute))
        except ValueError:
            return None

    # Relative dates
    lower = date_raw.lower()
    now = datetime.now()
    if "dzisiaj" in lower or "today" in lower:
        m2 = re.search(r'(\d{2}):(\d{2})', date_raw)
        if m2:
            return now.replace(hour=int(m2.group(1)), minute=int(m2.group(2)), second=0, microsecond=0)
        return now
    if "wczoraj" in lower or "yesterday" in lower:
        m2 = re.search(r'(\d{2}):(\d{2})', date_raw)
        yesterday = now - timedelta(days=1)
        if m2:
            return yesterday.replace(hour=int(m2.group(1)), minute=int(m2.group(2)), second=0, microsecond=0)
        return yesterday

    return None


# ─── Main scan logic ───

# ── Raw data file for scrape/analyze split ─────────────────────────────────
RAW_RADIO_FILE = os.path.join(RADIO_DIR, "raw-radio.json")


def run_scrape(dry_run=False):
    """Scrape forum threads, score them, fetch previews. Save raw JSON (no LLM)."""
    start_time = time.time()
    log("=" * 60)
    log("RADIO SCAN — scrape mode")
    log("=" * 60)

    # Load credentials
    creds = load_json(CREDS_PATH)
    if not creds:
        log(f"ERROR: No credentials found at {CREDS_PATH}")
        sys.exit(1)

    scanner = RadioScanner()

    if not scanner.login(creds["username"], creds["password"]):
        log("ERROR: Login failed, aborting")
        sys.exit(1)

    now = datetime.now()
    cutoff = now - timedelta(days=THREAD_AGE_DAYS)
    all_threads = []
    section_stats = {}

    for fid, info in FORUM_SECTIONS.items():
        log(f"Scanning [{info['emoji']} {info['name']}] (fid={fid})...")
        threads = scanner.fetch_forum_section(fid, pages=1)
        log(f"  Found {len(threads)} threads")

        recent_count = 0
        for t in threads:
            t["section_fid"] = fid
            t["section_name"] = info["name"]
            t["section_category"] = info["category"]
            t["section_emoji"] = info["emoji"]

            dt = parse_thread_date(t.get("date_raw", ""))
            if dt:
                t["date_parsed"] = dt.isoformat()
                t["age_days"] = (now - dt).total_seconds() / 86400
            else:
                t["date_parsed"] = None
                t["age_days"] = None

            if dt and dt >= cutoff:
                recent_count += 1
                all_threads.append(t)
            elif t["age_days"] is None:
                t["age_days"] = 999
                all_threads.append(t)

        section_stats[info["name"]] = {
            "fid": fid,
            "total": len(threads),
            "recent": recent_count,
            "category": info["category"],
            "emoji": info["emoji"],
        }
        time.sleep(1)

    log(f"\nTotal recent threads (< {THREAD_AGE_DAYS}d): {len(all_threads)}")

    # Score all threads
    for t in all_threads:
        fid = t["section_fid"]
        info = FORUM_SECTIONS.get(fid, {})
        score, reasons = score_thread(t, info)
        t["score"] = score
        t["match_reasons"] = reasons

    # Fetch previews for top-scoring threads
    top_threads = sorted(all_threads, key=lambda x: -x.get("score", 0))
    preview_count = 0
    for t in top_threads[:10]:
        if t["score"] >= 25:
            log(f"  Fetching preview: [{t['score']}] {t['title'][:60]}")
            preview = scanner.fetch_thread_preview(t["tid"])
            if preview:
                t["preview"] = preview
                preview_count += 1
                score, reasons = score_thread(t, FORUM_SECTIONS.get(t["section_fid"], {}))
                t["score"] = score
                t["match_reasons"] = reasons
            time.sleep(0.5)

    log(f"Fetched {preview_count} thread previews")

    all_threads.sort(key=lambda x: (-x.get("score", 0), x.get("age_days", 999)))

    # Build output structure
    duration = time.time() - start_time
    output = {
        "meta": {
            "timestamp": now.isoformat(),
            "source": "radioscanner.pl",
            "duration_seconds": round(duration, 1),
            "thread_age_cutoff_days": THREAD_AGE_DAYS,
            "sections_scanned": len(FORUM_SECTIONS),
            "threads_found": len(all_threads),
            "previews_fetched": preview_count,
        },
        "section_stats": section_stats,
        "threads": [
            {
                "tid": t["tid"],
                "title": t["title"],
                "url": f"{BASE_URL}/showthread.php?tid={t['tid']}",
                "section": t["section_name"],
                "section_emoji": t["section_emoji"],
                "category": t["section_category"],
                "date_raw": t.get("date_raw", ""),
                "date_parsed": t.get("date_parsed"),
                "age_days": round(t["age_days"], 1) if t.get("age_days") is not None and t["age_days"] != 999 else None,
                "score": t["score"],
                "match_reasons": t["match_reasons"],
                "preview": t.get("preview", "")[:500],
            }
            for t in all_threads
        ],
        "highlights": [
            {
                "tid": t["tid"],
                "title": t["title"],
                "url": f"{BASE_URL}/showthread.php?tid={t['tid']}",
                "section": t["section_name"],
                "section_emoji": t["section_emoji"],
                "category": t["section_category"],
                "score": t["score"],
                "reasons": t["match_reasons"],
                "date": t.get("date_parsed", ""),
                "preview": t.get("preview", "")[:300],
            }
            for t in all_threads if t["score"] >= 25
        ],
    }

    if dry_run:
        log("\n--- DRY RUN — would save: ---")
        log(json.dumps(output["meta"], indent=2))
        return

    # Save raw intermediate data
    raw_data = {
        "scrape_timestamp": now.isoformat(timespec="seconds"),
        "scrape_duration_seconds": round(duration, 1),
        "scrape_version": 1,
        "data": output,
        "scrape_errors": [],
    }
    tmp_path = RAW_RADIO_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(raw_data, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, RAW_RADIO_FILE)

    log(f"Scrape done: {len(all_threads)} threads saved to {RAW_RADIO_FILE} ({duration:.1f}s)")


def run_analyze():
    """Load raw data, run LLM briefing, save final output + Signal."""
    start_time = time.time()
    log("=" * 60)
    log("RADIO SCAN — analyze mode")
    log("=" * 60)

    if not os.path.exists(RAW_RADIO_FILE):
        log(f"ERROR: Raw data file not found: {RAW_RADIO_FILE}")
        print("Run with --scrape-only first.", file=sys.stderr)
        sys.exit(1)

    try:
        with open(RAW_RADIO_FILE) as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log(f"ERROR: Failed to read raw data: {e}")
        sys.exit(1)

    scrape_ts = raw.get("scrape_timestamp", "")
    output = raw.get("data", {})

    # Check staleness
    now = datetime.now()
    if scrape_ts:
        try:
            scrape_dt = datetime.fromisoformat(scrape_ts)
            age_hours = (now - scrape_dt).total_seconds() / 3600
            if age_hours > 48:
                log(f"WARNING: Raw data is {age_hours:.0f}h old (scraped {scrape_ts})")
        except ValueError:
            pass

    highlights = output.get("highlights", [])
    all_threads = output.get("threads", [])
    section_stats = output.get("section_stats", {})

    log(f"Loaded {len(all_threads)} threads, {len(highlights)} highlights (scraped {scrape_ts})")

    # LLM Analysis
    briefing = ""
    if highlights:
        briefing = llm_analyze_radio(highlights, all_threads, section_stats)
        if briefing:
            output["briefing"] = briefing
            output["meta"]["briefing_generated"] = now.isoformat()

    # Add dual timestamps
    output["meta"]["scrape_timestamp"] = scrape_ts
    output["meta"]["analyze_timestamp"] = now.isoformat(timespec="seconds")
    output["meta"]["timestamp"] = now.isoformat()  # backward compat

    # Save final output
    out_path = os.path.join(RADIO_DIR, "radio-latest.json")
    save_json(out_path, output)
    log(f"\nSaved to {out_path}")

    daily_path = os.path.join(RADIO_DIR, f"radio-{now.strftime('%Y%m%d')}.json")
    save_json(daily_path, output)
    log(f"Daily snapshot: {daily_path}")

    # Signal notification — only from analyze phase when LLM finds important content
    # Track sent thread titles to avoid duplicate alerts within the same day
    sent_path = os.path.join(RADIO_DIR, f"sent-{now.strftime('%Y%m%d')}.json")
    already_sent = set()
    try:
        with open(sent_path) as _f:
            already_sent = set(json.load(_f))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    if highlights and briefing:
        hot = [h for h in highlights if h.get("score", 0) >= 40]
        new_hot = [h for h in hot if h.get("title", "") not in already_sent]
        if new_hot:
            alert_lines = [f"📻 RADIO SCAN — {len(new_hot)} new highlights:\n"]
            for h in new_hot[:3]:
                alert_lines.append(f"[{h['score']}] {h.get('section_emoji', '📻')} {h['title'][:80]}")
            brief_lines = briefing.strip().split("\n")[:8]
            alert_lines.append(f"\n🤖 AI Briefing:\n" + "\n".join(brief_lines))
            signal_send("\n".join(alert_lines)[:1500])
            already_sent.update(h.get("title", "") for h in new_hot)
            with open(sent_path, "w") as _f:
                json.dump(sorted(already_sent), _f)
        elif hot:
            log(f"  {len(hot)} hot highlights already sent today — no duplicate alert")

    duration = time.time() - start_time
    log(f"\n{'='*60}")
    log(f"ANALYZE COMPLETE — {duration:.1f}s")
    log(f"  Highlights: {len(highlights)}")
    if briefing:
        log(f"\n🤖 AI Briefing:\n{briefing[:600]}")
    log(f"{'='*60}")


def run_scan(dry_run=False):
    """Legacy: run full scan (scrape + analyze). Backward compatible."""
    run_scrape(dry_run=dry_run)
    if not dry_run:
        run_analyze()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Radio scan — radioscanner.pl monitor")
    parser.add_argument('--scrape-only', action='store_true',
                        help='Only scrape forum, save raw data (no LLM)')
    parser.add_argument('--analyze-only', action='store_true',
                        help='Only run LLM analysis on previously scraped data')
    parser.add_argument('--dry-run', action='store_true',
                        help='Scrape but do not save or analyze')
    args = parser.parse_args()

    if args.scrape_only:
        run_scrape(dry_run=args.dry_run)
    elif args.analyze_only:
        run_analyze()
    else:
        run_scan(dry_run=args.dry_run)
