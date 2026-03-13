#!/usr/bin/env python3
"""
news-watch.py — RSS/Atom feed reader for curated tech news sources
LWN.net, Phoronix, CNX-Software, Hackaday, EETimes, etc.
with LLM filtering for user's interests from profile.json
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from llm_sanitize import sanitize_llm_output

# ── Config ─────────────────────────────────────────────────────────────────

OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen3:14b"
OLLAMA_CHAT = f"{OLLAMA_URL}/api/chat"

DATA_DIR = Path("/opt/netscan/data/news")
THINK_DIR = Path("/opt/netscan/data/think")
PROFILE_PATH = Path("/opt/netscan/profile.json")

SIGNAL_RPC = "http://127.0.0.1:8080/api/v1/rpc"
SIGNAL_FROM = os.environ.get("SIGNAL_ACCOUNT", "+<BOT_PHONE>")
SIGNAL_TO = os.environ.get("SIGNAL_OWNER", "+<OWNER_PHONE>")

TODAY = datetime.now().strftime("%Y%m%d")
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
MAX_AGE_DAYS = 3  # only consider articles from last N days

# ── Feed Sources ───────────────────────────────────────────────────────────

FEEDS = [
    # ── Tier 1: Core embedded Linux / kernel sources ──
    {
        "id": "lwn",
        "name": "LWN.net",
        "url": "https://lwn.net/headlines/rss",
        "type": "rss",
        "relevance_boost": 3,
        "topics": ["kernel", "linux", "security", "development"],
    },
    {
        "id": "phoronix",
        "name": "Phoronix",
        "url": "https://www.phoronix.com/rss.php",
        "type": "rss",
        "relevance_boost": 2,
        "topics": ["linux", "gpu", "amd", "mesa", "vulkan", "kernel", "benchmark"],
    },
    {
        "id": "cnx-software",
        "name": "CNX Software",
        "url": "https://www.cnx-software.com/feed/",
        "type": "rss",
        "relevance_boost": 3,
        "topics": ["sbc", "embedded", "arm", "risc-v", "rockchip", "allwinner"],
    },
    # ── Tier 2: Hacker / maker / security ──
    {
        "id": "hackaday",
        "name": "Hackaday",
        "url": "https://hackaday.com/feed/",
        "type": "rss",
        "relevance_boost": 1,
        "topics": ["embedded", "hardware", "sdr", "rf", "security", "iot"],
    },
    {
        "id": "rtlsdr-blog",
        "name": "RTL-SDR Blog",
        "url": "https://www.rtl-sdr.com/feed/",
        "type": "rss",
        "relevance_boost": 2,
        "topics": ["sdr", "radio", "satellite", "ads-b", "gnss"],
    },
    # ── Tier 3: Industry / semiconductor ──
    {
        "id": "eetimes",
        "name": "EETimes",
        "url": "https://www.eetimes.com/feed/",
        "type": "rss",
        "relevance_boost": 1,
        "topics": ["semiconductor", "automotive", "embedded", "ai"],
    },
    {
        "id": "eenews-automotive",
        "name": "eeNews Automotive",
        "url": "https://www.eenewsautomotive.com/rss.xml",
        "type": "rss",
        "relevance_boost": 2,
        "topics": ["automotive", "adas", "ev", "semiconductor"],
    },
    {
        "id": "embedded-com",
        "name": "Embedded.com",
        "url": "https://www.embedded.com/feed/",
        "type": "rss",
        "relevance_boost": 2,
        "topics": ["embedded", "rtos", "firmware", "iot"],
    },
    # ── Tier 4: Open source / Linux ecosystem ──
    {
        "id": "planet-kde",
        "name": "Planet KDE (Linux desktop)",
        "url": "https://planet.kde.org/global/atom.xml",
        "type": "atom",
        "relevance_boost": 0,
        "topics": ["linux", "desktop", "qt"],
    },
    {
        "id": "fedora-magazine",
        "name": "Fedora Magazine",
        "url": "https://fedoramagazine.org/feed/",
        "type": "rss",
        "relevance_boost": 1,
        "topics": ["fedora", "linux", "packaging"],
    },
    {
        "id": "kernelnewbies",
        "name": "KernelNewbies (kernel changes)",
        "url": "https://kernelnewbies.org/LinuxChanges?action=rss_rc",
        "type": "rss",
        "relevance_boost": 2,
        "topics": ["kernel", "driver", "subsystem"],
    },
    # ── Tier 5: GStreamer / multimedia specific ──
    {
        "id": "gstreamer-news",
        "name": "GStreamer News",
        "url": "https://gstreamer.freedesktop.org/news/rss-1.0.xml",
        "type": "rss",
        "relevance_boost": 3,
        "topics": ["gstreamer", "multimedia", "video", "codec"],
    },
]

# ── Relevance keywords (loaded from profile.json + hardcoded essentials) ──

ESSENTIAL_KEYWORDS = {
    "high": [
        "v4l2", "libcamera", "isp", "mipi", "csi-2", "camera", "uvc",
        "gstreamer", "vaapi", "vulkan video", "vulkan compute",
        "gmsl", "max96712", "max9295", "serdes", "fpd-link",
        "rockchip", "qualcomm", "mediatek", "amlogic", "allwinner", "nxp",
        "rk3588", "imx8", "device tree", "devicetree",
        "rdna", "amdgpu", "radv", "cyan skillfish",
        "ollama", "llama.cpp", "gguf",
        "sdr", "rtl-sdr", "hackrf", "satellite",
        "risc-v", "riscv", "starfive",
        "harman", "samsung semiconductor",
        "embedded linux", "linux kernel", "linux driver",
    ],
    "medium": [
        "sensor", "omnivision", "bayer", "debayer",
        "ffmpeg", "hwaccel", "encode", "decode",
        "usb", "gadget", "xhci",
        "mesa", "drm", "kms",
        "wardriving", "bluetooth", "ble",
        "ads-b", "gnss", "gps",
        "analog devices", "adi", "maxim",
        "automotive", "adas", "surround view",
        "yocto", "buildroot", "cross-compile",
        "rust", "ebpf", "io_uring",
    ],
}


# ── Helpers ────────────────────────────────────────────────────────────────

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def fetch_url(url, timeout=30):
    """Fetch URL with retries."""
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
            else:
                log(f"  Failed {url[:50]}...: {e}")
                return None


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.rename(path)


def strip_html(text):
    """Remove HTML tags and decode entities."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&#39;", "'", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_date(date_str):
    """Parse various RSS/Atom date formats."""
    if not date_str:
        return None

    # Try ISO 8601
    for fmt in [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S",
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue

    # Fallback: try to extract date portion
    m = re.search(r"(\d{4}-\d{2}-\d{2})", date_str)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    return None


def call_ollama(system_prompt, user_prompt, temperature=0.3, max_tokens=2500):
    """Call Ollama LLM."""
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
            log(f"  LLM: {elapsed:.0f}s, {tokens} tok")
            return sanitize_llm_output(content)
    except Exception as e:
        log(f"  Ollama call failed: {e}")
        return None


def signal_send(msg):
    """Send Signal message."""
    payload = json.dumps({
        "jsonrpc": "2.0", "method": "send", "id": 1,
        "params": {
            "account": SIGNAL_FROM,
            "recipient": [SIGNAL_TO],
            "message": msg,
        }
    }).encode()
    req = urllib.request.Request(SIGNAL_RPC, data=payload, headers={
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
            log("  Signal alert sent")
    except Exception as e:
        log(f"  Signal send failed: {e}")


def llm_notification_filter(articles):
    """Ask LLM which articles truly deserve a push notification.

    Returns only articles the LLM marks as NOTIFY, with a reason.
    Keeps the dashboard must-read list unchanged — this only gates Signal alerts.
    """
    if not articles:
        return []

    lines = []
    for i, a in enumerate(articles):
        lines.append(f"[{i}] [{a.get('source_name', '?')}] {a.get('title', '?')}")
        if a.get("summary"):
            lines.append(f"    {a['summary'][:150]}")

    prompt = "\n".join(lines)

    system = """\
You are a personal news notification filter for a Principal Embedded SW Engineer
who works on V4L2, libcamera, GStreamer, GMSL/SerDes, camera ISP, Linux kernel
drivers, MIPI CSI-2, AMD GPU/Vulkan, and home automation (Home Assistant).

The user gets these articles on their dashboard anyway. The question is:
which ones are important enough to INTERRUPT them with a phone notification?

ONLY mark as NOTIFY if the article is:
- A critical security vulnerability affecting Linux/embedded systems
- A major kernel release or subsystem change in areas they work on
- Breaking news directly about their tech stack (V4L2, libcamera, GStreamer, AMD RDNA)
- A significant new product/chip they'd need to evaluate for work
- Something they'd genuinely regret missing if they didn't check the dashboard today

Do NOT notify for:
- Generic new SBC/board announcements (Raspberry Pi accessories, random dev boards)
- Routine Mesa/driver point releases unless they fix AMD RDNA bugs
- General industry news, market trends, AI hype
- Products/chips outside their direct work scope

Reply with ONLY a JSON array. For each article, output:
{"idx": N, "action": "NOTIFY" or "SKIP", "reason": "one-sentence why"}

Output ONLY the JSON array, no other text."""

    result = call_ollama(system, prompt, temperature=0.1, max_tokens=1500)
    if not result:
        log("  LLM notification filter failed — sending all")
        return articles[:3]

    # Parse JSON from LLM response
    try:
        # Find JSON array in response
        match = re.search(r'\[.*\]', result, re.DOTALL)
        if not match:
            log("  LLM filter: no JSON found — sending all")
            return articles[:3]
        decisions = json.loads(match.group())
    except (json.JSONDecodeError, ValueError) as e:
        log(f"  LLM filter parse error: {e} — sending all")
        return articles[:3]

    notify = []
    for d in decisions:
        if d.get("action") == "NOTIFY":
            idx = d.get("idx", -1)
            if 0 <= idx < len(articles):
                articles[idx]["notify_reason"] = d.get("reason", "")
                notify.append(articles[idx])

    log(f"  LLM notification filter: {len(notify)}/{len(articles)} pass")
    return notify


# ── Feed Parsing ───────────────────────────────────────────────────────────

def parse_rss(xml_text, feed_config):
    """Parse RSS 2.0 / RDF / RSS 1.0 feed."""
    articles = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log(f"  XML parse error for {feed_config['id']}: {e}")
        return articles

    # Handle various RSS formats
    ns = {
        "dc": "http://purl.org/dc/elements/1.1/",
        "content": "http://purl.org/rss/1.0/modules/content/",
        "atom": "http://www.w3.org/2005/Atom",
        "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
        "rss10": "http://purl.org/rss/1.0/",
    }

    items = root.findall(".//item")
    if not items:
        items = root.findall(".//{http://purl.org/rss/1.0/}item")

    for item in items:
        title = ""
        link = ""
        desc = ""
        pub_date = None

        # Title
        t = item.find("title")
        if t is None:
            t = item.find("{http://purl.org/rss/1.0/}title")
        if t is not None and t.text:
            title = strip_html(t.text)

        # Link
        l = item.find("link")
        if l is None:
            l = item.find("{http://purl.org/rss/1.0/}link")
        if l is not None:
            link = (l.text or "").strip()
            if not link:
                link = l.get("href", "")

        # Description
        d = item.find("description")
        if d is None:
            d = item.find("{http://purl.org/rss/1.0/}description")
        if d is not None and d.text:
            desc = strip_html(d.text)[:500]

        # Content (prefer full content)
        c = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")
        if c is not None and c.text:
            desc = strip_html(c.text)[:500]

        # Date
        for date_tag in ["pubDate", "published", "{http://purl.org/dc/elements/1.1/}date"]:
            dt = item.find(date_tag)
            if dt is not None and dt.text:
                pub_date = parse_date(dt.text)
                break

        if title:
            articles.append({
                "title": title,
                "url": link,
                "summary": desc,
                "date": pub_date.isoformat() if pub_date else None,
                "source": feed_config["id"],
                "source_name": feed_config["name"],
            })

    return articles


def parse_atom(xml_text, feed_config):
    """Parse Atom feed."""
    articles = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log(f"  Atom parse error for {feed_config['id']}: {e}")
        return articles

    ns = "http://www.w3.org/2005/Atom"

    for entry in root.findall(f"{{{ns}}}entry"):
        title = ""
        link = ""
        summary = ""
        pub_date = None

        t = entry.find(f"{{{ns}}}title")
        if t is not None:
            title = strip_html(t.text or "")

        # Prefer alternate link
        for l in entry.findall(f"{{{ns}}}link"):
            rel = l.get("rel", "alternate")
            if rel == "alternate":
                link = l.get("href", "")
                break
        if not link:
            l = entry.find(f"{{{ns}}}link")
            if l is not None:
                link = l.get("href", "")

        s = entry.find(f"{{{ns}}}summary")
        if s is None:
            s = entry.find(f"{{{ns}}}content")
        if s is not None and s.text:
            summary = strip_html(s.text)[:500]

        for date_tag in [f"{{{ns}}}published", f"{{{ns}}}updated"]:
            d = entry.find(date_tag)
            if d is not None and d.text:
                pub_date = parse_date(d.text)
                break

        if title:
            articles.append({
                "title": title,
                "url": link,
                "summary": summary,
                "date": pub_date.isoformat() if pub_date else None,
                "source": feed_config["id"],
                "source_name": feed_config["name"],
            })

    return articles


def fetch_feed(feed_config):
    """Fetch and parse a single feed."""
    log(f"  Fetching {feed_config['name']}...")
    xml = fetch_url(feed_config["url"], timeout=20)
    if not xml:
        return []

    if feed_config.get("type") == "atom":
        articles = parse_atom(xml, feed_config)
    else:
        articles = parse_rss(xml, feed_config)

    # Filter by age
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    filtered = []
    for a in articles:
        if a["date"]:
            try:
                dt = datetime.fromisoformat(a["date"])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt < cutoff:
                    continue
            except (ValueError, TypeError):
                pass
        filtered.append(a)

    log(f"    {len(filtered)}/{len(articles)} articles within {MAX_AGE_DAYS} days")
    return filtered


# ── Scoring ────────────────────────────────────────────────────────────────

def load_profile_keywords():
    """Load keywords from profile.json."""
    try:
        profile = json.loads(PROFILE_PATH.read_text())
        kw = profile.get("interest_keywords", {})
        return {
            "high": kw.get("high", []),
            "medium": kw.get("medium", []),
            "low": kw.get("low", []),
        }
    except Exception:
        return {"high": [], "medium": [], "low": []}


def score_article(article, profile_keywords, feed_config):
    """Score article relevance based on keywords and source."""
    text = (article.get("title", "") + " " + article.get("summary", "")).lower()
    score = 0

    # Source boost
    score += feed_config.get("relevance_boost", 0)

    # Essential keywords (hardcoded)
    for kw in ESSENTIAL_KEYWORDS["high"]:
        if kw.lower() in text:
            score += 5
    for kw in ESSENTIAL_KEYWORDS["medium"]:
        if kw.lower() in text:
            score += 2

    # Profile keywords
    for kw in profile_keywords.get("high", []):
        if kw.lower() in text:
            score += 4
    for kw in profile_keywords.get("medium", []):
        if kw.lower() in text:
            score += 2
    for kw in profile_keywords.get("low", []):
        if kw.lower() in text:
            score += 1

    # Penalize generic/off-topic
    off_topic = ["cryptocurrency", "nft", "web3", "blockchain", "social media", "tiktok", "instagram"]
    for kw in off_topic:
        if kw in text:
            score -= 5

    article["score"] = score
    return score


# ── LLM Analysis ──────────────────────────────────────────────────────────

def llm_filter_and_summarize(articles, profile):
    """LLM filters articles for relevance and produces a digest."""
    # Sort by score, take top articles
    top = sorted(articles, key=lambda a: a.get("score", 0), reverse=True)[:40]

    if not top:
        return None

    lines = []
    lines.append(f"=== Tech News Digest — {datetime.now().strftime('%Y-%m-%d')} ===")
    lines.append(f"Total articles collected: {len(articles)}")
    lines.append(f"Top {len(top)} by relevance score shown below.")
    lines.append("")

    for i, a in enumerate(top):
        lines.append(f"[{i+1}] (score={a.get('score', 0)}) [{a['source_name']}] {a['title']}")
        if a.get("summary"):
            lines.append(f"    {a['summary'][:200]}")
        if a.get("url"):
            lines.append(f"    {a['url']}")
        lines.append("")

    # User profile context
    if profile:
        interests = profile.get("interests", [])
        lines.append("=== USER PROFILE ===")
        for interest in interests[:10]:
            lines.append(f"  - {interest}")
        lines.append("")

    prompt = "\n".join(lines)

    system = """\
You are a curated tech news filter for a Principal Embedded SW Engineer.
Analyze the articles and produce a CURATED DIGEST focusing on what matters most.

Your output MUST include:

## 🔥 Must-Read (2-5 articles)
Articles directly relevant to: V4L2, libcamera, GStreamer, GMSL SerDes, camera ISP,
Linux kernel drivers, MIPI CSI-2, AMD GPU, Vulkan, embedded Linux, RISC-V, SDR.
For each: title, source, 1-sentence why it matters, URL.

## 📰 Worth Knowing (3-7 articles)
Industry trends, semiconductor news, new hardware, security advisories, Rust/eBPF/io_uring.
Less directly actionable but professionally relevant.

## 🔬 Deep Dive Candidates (1-3 articles)
Articles worth reading in full — long-form technical content, kernel changelogs,
new hardware reviews, conference talk recordings.

## 🗑️ Filtered Out
Brief note on what was skipped and why (e.g., "15 generic AI hype articles, 3 Windows-only").

Rules:
- Be ruthlessly selective — better to highlight 3 amazing articles than 15 mediocre ones
- Always explain WHY each article matters to this specific user
- If nothing relevant found, say so honestly
- Write ONLY in English. Be concise.
- 300-500 words total."""

    return call_ollama(system, prompt, temperature=0.3, max_tokens=2000)


# ── Main Run ───────────────────────────────────────────────────────────────

def run_scrape():
    """Phase 1: Fetch all feeds, score articles."""
    log("=== news-watch scrape ===")

    profile_kw = load_profile_keywords()
    all_articles = []

    for feed in FEEDS:
        articles = fetch_feed(feed)
        for a in articles:
            score_article(a, profile_kw, feed)
        all_articles.extend(articles)
        time.sleep(1)  # Be polite

    # Dedup by title similarity
    seen_titles = set()
    deduped = []
    for a in all_articles:
        title_key = re.sub(r"[^a-z0-9]", "", a["title"].lower())[:60]
        if title_key not in seen_titles:
            seen_titles.add(title_key)
            deduped.append(a)

    log(f"Total: {len(all_articles)} articles, {len(deduped)} after dedup")

    raw = {
        "timestamp": datetime.now().isoformat(),
        "total_articles": len(all_articles),
        "deduped_articles": len(deduped),
        "feeds_checked": len(FEEDS),
        "articles": sorted(deduped, key=lambda a: a.get("score", 0), reverse=True),
    }

    raw_path = DATA_DIR / "raw-news.json"
    save_json(raw_path, raw)
    log(f"Saved {len(deduped)} articles to {raw_path}")
    return raw


def run_analyze():
    """Phase 2: LLM digest + save + alert."""
    log("=== news-watch analyze ===")

    raw_path = DATA_DIR / "raw-news.json"
    if not raw_path.exists():
        log("No raw data — run scrape first")
        return

    raw = json.loads(raw_path.read_text())
    articles = raw.get("articles", [])

    # Load profile
    profile = None
    try:
        profile = json.loads(PROFILE_PATH.read_text())
    except Exception:
        pass

    # LLM analysis
    analysis = llm_filter_and_summarize(articles, profile)
    if not analysis:
        log("LLM analysis failed")
        analysis = "No analysis available"

    # Find must-read articles (score >= 10)
    must_read = [a for a in articles if a.get("score", 0) >= 10][:10]

    result = {
        "timestamp": datetime.now().isoformat(),
        "date": TODAY,
        "feeds_checked": raw.get("feeds_checked", 0),
        "total_articles": raw.get("total_articles", 0),
        "must_read_count": len(must_read),
        "must_read": must_read,
        "analysis": analysis,
    }

    out_path = DATA_DIR / f"news-{TODAY}.json"
    save_json(out_path, result)

    latest = DATA_DIR / "news-latest.json"
    latest.unlink(missing_ok=True)
    latest.symlink_to(out_path.name)

    # Think note
    note = {
        "timestamp": datetime.now().isoformat(),
        "source": "news-watch",
        "category": "tech-news",
        "title": f"Tech News Digest — {datetime.now().strftime('%Y-%m-%d')}",
        "content": analysis,
        "metadata": {
            "feeds_checked": raw.get("feeds_checked", 0),
            "total_articles": raw.get("total_articles", 0),
            "must_read_count": len(must_read),
        },
    }
    ts = datetime.now().strftime("%Y%m%d-%H%M")
    note_path = THINK_DIR / f"note-news-{ts}.json"
    save_json(note_path, note)

    log(f"Saved {out_path}, think note ({len(must_read)} must-read)")

    # Signal alert — only for NEW highly relevant articles (not already sent)
    sent_path = DATA_DIR / f"sent-{TODAY}.json"
    already_sent = set()
    try:
        already_sent = set(json.loads(sent_path.read_text()))
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    new_articles = [a for a in must_read if a.get("title", "") not in already_sent]

    if new_articles:
        # LLM relevance gate — only push-notify truly important articles
        notify_articles = llm_notification_filter(new_articles)

        if notify_articles:
            msg_parts = [f"📰 Tech News — {len(notify_articles)} important"]
            msg_parts.append("")
            for a in notify_articles:
                reason = a.get("notify_reason", "")
                msg_parts.append(f"• [{a['source_name']}] {a['title']}")
                if reason:
                    msg_parts.append(f"  → {reason}")
            msg = "\n".join(msg_parts)
            signal_send(msg[:1500])
        else:
            log(f"  LLM filtered out all {len(new_articles)} candidates — no notification")

        # Track ALL new must-read titles as sent (even if filtered from notification)
        already_sent.update(a.get("title", "") for a in new_articles)
        sent_path.write_text(json.dumps(sorted(already_sent)))
    elif must_read:
        log(f"  {len(must_read)} must-read articles already sent — no duplicate alert")
    else:
        log("  No must-read articles — no Signal alert")


def main():
    parser = argparse.ArgumentParser(description="Tech news RSS aggregator with LLM filtering")
    parser.add_argument("--scrape-only", action="store_true", help="Only fetch feeds")
    parser.add_argument("--analyze-only", action="store_true", help="Only run LLM analysis")
    parser.add_argument("--list-feeds", action="store_true", help="List configured feeds")
    args = parser.parse_args()

    if args.list_feeds:
        for f in FEEDS:
            print(f"  {f['id']}: {f['name']} ({f['url'][:60]}...)")
        return

    if args.scrape_only:
        run_scrape()
    elif args.analyze_only:
        run_analyze()
    else:
        run_scrape()
        run_analyze()


if __name__ == "__main__":
    main()
