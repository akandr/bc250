#!/usr/bin/env python3
"""
book-watch.py — New book publication tracker for topics of interest.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Monitors new technical book releases across user's interest areas:
  - Linux kernel & driver development
  - Embedded systems & real-time programming
  - Camera systems & image processing
  - Software-defined radio & RF engineering
  - Wireless & network security / pentesting
  - RISC-V architecture
  - Edge AI / ML on hardware
  - GStreamer & multimedia
  - Automotive software (AUTOSAR, ADAS)
  - Rust for systems programming

Sources:
  - DuckDuckGo search (recent books, publisher sites)
  - O'Reilly, Manning, No Starch Press, Apress, Packt, Springer (via DDG)

Output:
  - Think notes in /opt/netscan/data/think/  (appears on dashboard)
  - JSON in /opt/netscan/data/books/

Usage:
  python3 book-watch.py                        # scan all topics
  python3 book-watch.py --topic linux-kernel    # single topic
  python3 book-watch.py --list                  # list topics

Schedule: Weekly via queue-runner
Location on bc250: /opt/netscan/book-watch.py
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
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from llm_sanitize import sanitize_llm_output

# ── Config ─────────────────────────────────────────────────────────────────
OLLAMA_URL = "http://localhost:11434"
OLLAMA_CHAT = f"{OLLAMA_URL}/api/chat"
OLLAMA_MODEL = "qwen3:14b"

DATA_DIR = Path("/opt/netscan/data")
BOOKS_DIR = DATA_DIR / "books"
THINK_DIR = DATA_DIR / "think"
PROFILE_PATH = Path("/opt/netscan/profile.json")

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

# ── Signal ─────────────────────────────────────────────────────────────────
SIGNAL_RPC = "http://127.0.0.1:8080/api/v1/rpc"
SIGNAL_FROM = os.environ.get("SIGNAL_ACCOUNT", "+<BOT_PHONE>")
SIGNAL_TO = os.environ.get("SIGNAL_OWNER", "+<OWNER_PHONE>")

# ── Book topic definitions ─────────────────────────────────────────────────

BOOK_TOPICS = {
    "linux-kernel": {
        "label": "Linux Kernel & Driver Development",
        "search_queries": [
            '"Linux kernel" book 2024 OR 2025 new',
            '"Linux device driver" book new release',
            '"kernel development" book embedded Linux',
            'O\'Reilly OR Manning OR "No Starch" Linux kernel book',
        ],
        "relevance_keywords": [
            "linux", "kernel", "driver", "device tree", "module",
            "v4l2", "embedded", "real-time", "lkd", "ldd",
        ],
    },
    "embedded-systems": {
        "label": "Embedded Systems & Real-Time Programming",
        "search_queries": [
            '"embedded systems" book 2024 OR 2025 programming',
            '"real-time" programming book embedded Linux',
            'Yocto OR Buildroot book new release',
            '"bare metal" OR "RTOS" programming book ARM',
        ],
        "relevance_keywords": [
            "embedded", "real-time", "rtos", "yocto", "buildroot",
            "arm", "cortex", "bare metal", "firmware", "microcontroller",
        ],
    },
    "camera-imaging": {
        "label": "Camera Systems & Image Processing",
        "search_queries": [
            '"image processing" book 2024 OR 2025 new',
            '"computer vision" book camera pipeline',
            '"image sensor" OR "camera system" book engineering',
            'OpenCV OR "image pipeline" book new release',
        ],
        "relevance_keywords": [
            "camera", "image", "sensor", "isp", "computer vision",
            "opencv", "pipeline", "bayer", "raw", "imaging",
        ],
    },
    "sdr-rf": {
        "label": "Software-Defined Radio & RF Engineering",
        "search_queries": [
            '"software defined radio" OR SDR book 2024 OR 2025',
            '"RF engineering" book new release',
            'GNURadio OR "RTL-SDR" book tutorial',
            '"radio frequency" OR "wireless communications" book engineering',
            '"signal processing" DSP book new release',
        ],
        "relevance_keywords": [
            "sdr", "software defined radio", "rf", "radio", "gnuradio",
            "rtl-sdr", "hackrf", "antenna", "dsp", "signal processing",
            "spectrum", "frequency", "modulation", "demodulation",
        ],
    },
    "security-pentesting": {
        "label": "Wireless & Network Security / Pentesting",
        "search_queries": [
            '"wireless security" OR "WiFi hacking" book 2024 OR 2025',
            '"penetration testing" book new release',
            '"Kali Linux" OR "ethical hacking" book new',
            '"bluetooth hacking" OR "IoT security" book',
            '"network security" book practical new release',
        ],
        "relevance_keywords": [
            "hacking", "penetration", "security", "pentest", "kali",
            "wifi", "wireless", "bluetooth", "exploit", "vulnerability",
            "iot", "network", "forensics", "reverse engineering",
        ],
    },
    "riscv": {
        "label": "RISC-V Architecture & Programming",
        "search_queries": [
            '"RISC-V" book 2024 OR 2025 new',
            '"RISC-V" architecture programming book',
            '"RISC-V" assembly OR "instruction set" book',
        ],
        "relevance_keywords": [
            "risc-v", "riscv", "instruction set", "architecture",
            "assembly", "starfive", "sifive", "isa",
        ],
    },
    "edge-ai": {
        "label": "Edge AI / ML on Hardware",
        "search_queries": [
            '"edge AI" OR "TinyML" book 2024 OR 2025',
            '"machine learning" hardware book embedded',
            '"neural network" OR "deep learning" edge device book',
            'Jetson OR "NPU" OR "inference" book new release',
        ],
        "relevance_keywords": [
            "edge", "tinyml", "inference", "npu", "neural network",
            "machine learning", "deep learning", "jetson", "quantization",
            "onnx", "tensorflow lite", "embedded ai",
        ],
    },
    "multimedia": {
        "label": "GStreamer & Multimedia Frameworks",
        "search_queries": [
            'GStreamer book tutorial 2024 OR 2025',
            '"multimedia framework" book Linux',
            'FFmpeg OR PipeWire book new',
            '"video streaming" OR "media pipeline" book programming',
        ],
        "relevance_keywords": [
            "gstreamer", "ffmpeg", "pipewire", "multimedia", "video",
            "streaming", "codec", "pipeline", "media", "audio",
        ],
    },
    "automotive-sw": {
        "label": "Automotive Software (AUTOSAR, ADAS)",
        "search_queries": [
            'AUTOSAR book 2024 OR 2025 new',
            '"ADAS" OR "autonomous driving" software book',
            '"automotive embedded" software book',
            '"functional safety" OR ISO26262 book new',
        ],
        "relevance_keywords": [
            "autosar", "adas", "automotive", "autonomous", "iso26262",
            "functional safety", "can", "lin", "ethernet", "some/ip",
        ],
    },
    "rust-systems": {
        "label": "Rust for Systems Programming",
        "search_queries": [
            'Rust book 2024 OR 2025 systems programming new',
            '"Rust programming" book embedded OR kernel',
            'Rust "low-level" OR "systems" book new release',
        ],
        "relevance_keywords": [
            "rust", "systems programming", "embedded", "kernel",
            "memory safety", "zero-cost", "no_std", "async",
        ],
    },
}

# ── Publisher RSS Feeds ────────────────────────────────────────────────────
# Direct feeds from major tech publishers for more reliable book discovery

PUBLISHER_FEEDS = [
    {
        "id": "oreilly",
        "name": "O'Reilly Media",
        "url": "https://www.oreilly.com/content/feed/",
        "alt_url": "https://feeds.feedburner.com/oreilly/newbooks",
        "publisher_score": 8,
    },
    {
        "id": "manning",
        "name": "Manning Publications",
        "url": "https://www.manning.com/feed",
        "alt_url": "https://freecontent.manning.com/feed/",
        "publisher_score": 7,
    },
    {
        "id": "nostarch",
        "name": "No Starch Press",
        "url": "https://nostarch.com/rss.xml",
        "alt_url": None,
        "publisher_score": 8,
    },
    {
        "id": "pragprog",
        "name": "Pragmatic Programmers",
        "url": "https://pragprog.com/feed.xml",
        "alt_url": None,
        "publisher_score": 7,
    },
    {
        "id": "apress",
        "name": "Apress (Springer)",
        "url": "https://www.apress.com/us/rss/catalog/new",
        "alt_url": "https://link.springer.com/search.rss?facet-content-type=%22Book%22&query=embedded+linux",
        "publisher_score": 5,
    },
    {
        "id": "packt",
        "name": "Packt Publishing",
        "url": "https://www.packtpub.com/rss.xml",
        "alt_url": None,
        "publisher_score": 3,
    },
]


# ── Helpers ────────────────────────────────────────────────────────────────

def log(msg):
    print(f"  [{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def fetch_url(url, timeout=30):
    """Fetch URL, return text or empty string."""
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "identity",
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                charset = resp.headers.get_content_charset() or "utf-8"
                return data.decode(charset, errors="replace")
        except Exception as e:
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
            else:
                log(f"  fetch error {url}: {e}")
        return ""


def search_ddg(query, max_results=8):
    """Search DuckDuckGo HTML, return list of {title, url, snippet}."""
    import html as html_mod
    results = []
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
    html = fetch_url(url, timeout=15)
    if not html:
        return results

    for m in re.finditer(
        r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
        r'class="result__snippet"[^>]*>(.*?)</(?:span|div)',
        html, re.S
    ):
        raw_url = m.group(1)
        title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        snippet = re.sub(r'<[^>]+>', '', m.group(3)).strip()

        # DDG wraps URLs in a redirect — extract real URL
        if "uddg=" in raw_url:
            real_m = re.search(r'uddg=([^&]+)', raw_url)
            if real_m:
                raw_url = urllib.parse.unquote(real_m.group(1))

        title = html_mod.unescape(title)
        snippet = html_mod.unescape(snippet)

        if title and len(title) > 3:
            results.append({
                "title": title[:200],
                "url": raw_url[:500],
                "snippet": snippet[:300],
            })

        if len(results) >= max_results:
            break

    return results


def fetch_publisher_rss():
    """Fetch books from publisher RSS feeds (more reliable than DDG)."""
    log("📡 Fetching publisher RSS feeds...")
    all_books = []

    for pub in PUBLISHER_FEEDS:
        for url in [pub["url"], pub.get("alt_url")]:
            if not url:
                continue
            log(f"  Feed: {pub['name']}...")
            xml_text = fetch_url(url, timeout=20)
            if not xml_text:
                continue

            try:
                root = ET.fromstring(xml_text)
            except ET.ParseError:
                continue

            # Parse RSS items
            ns_atom = "http://www.w3.org/2005/Atom"
            items = root.findall(".//item")
            if not items:
                items = root.findall(f".//{{{ns_atom}}}entry")

            for item in items[:20]:  # Max 20 per feed
                title = ""
                link = ""
                desc = ""

                # RSS 2.0
                t = item.find("title")
                if t is None:
                    t = item.find(f"{{{ns_atom}}}title")
                if t is not None and t.text:
                    title = t.text.strip()

                l = item.find("link")
                if l is None:
                    l = item.find(f"{{{ns_atom}}}link")
                if l is not None:
                    link = (l.text or l.get("href", "")).strip()

                d = item.find("description")
                if d is None:
                    d = item.find(f"{{{ns_atom}}}summary")
                    if d is None:
                        d = item.find(f"{{{ns_atom}}}content")
                if d is not None and d.text:
                    desc = re.sub(r'<[^>]+>', ' ', d.text).strip()[:300]

                if title:
                    all_books.append({
                        "title": title[:200],
                        "url": link[:500],
                        "snippet": desc[:300],
                        "source": f"rss-{pub['id']}",
                        "publisher": pub["name"],
                        "publisher_score": pub["publisher_score"],
                    })

            break  # Got data from primary URL

        time.sleep(1)

    log(f"  Got {len(all_books)} books from RSS feeds")
    return all_books


def score_book_result(result, topic_config):
    """Score a search result for book relevance. Returns (score, reasons)."""
    combined = f"{result.get('title', '')} {result.get('snippet', '')}".lower()
    score = 0
    reasons = []

    # Must look like a book
    book_indicators = [
        "book", "isbn", "edition", "publisher", "paperback", "hardcover",
        "ebook", "pages", "o'reilly", "manning", "packt", "apress",
        "springer", "no starch", "wiley", "addison", "pearson",
        "pragmatic", "early access", "pre-order", "new release",
    ]
    is_book = any(ind in combined for ind in book_indicators)
    if not is_book:
        return 0, []

    score += 10
    reasons.append("book")

    # Premium publishers
    premium = {
        "o'reilly": 8, "manning": 7, "no starch": 8, "pragmatic": 7,
        "apress": 5, "packt": 3, "springer": 6, "wiley": 5,
        "addison-wesley": 6, "pearson": 4,
    }
    for pub, pts in premium.items():
        if pub in combined:
            score += pts
            reasons.append(pub)
            break

    # Recency bonus
    current_year = datetime.now().year
    for year in [str(current_year), str(current_year - 1)]:
        if year in combined:
            score += 5
            reasons.append(f"year:{year}")
            break

    # Early access / pre-order
    if "early access" in combined or "pre-order" in combined or "meap" in combined:
        score += 3
        reasons.append("early-access")

    # Topic relevance keywords
    for kw in topic_config.get("relevance_keywords", []):
        if kw in combined:
            score += 2
            reasons.append(kw)

    return score, reasons


def call_ollama(system_prompt, user_prompt, temperature=0.4, max_tokens=2000):
    """Call Ollama for LLM analysis."""
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
            log(f"  LLM: {elapsed:.0f}s, {tokens} tok")
            content = _strip_thinking(content)
            return content
    except Exception as e:
        log(f"  Ollama call failed: {e}")
        return None


def _strip_thinking(text):
    """Remove chain-of-thought reasoning and Chinese text from LLM output."""
    return sanitize_llm_output(text) if text else text


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
            "id": "book-watch",
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


# ── Main scan logic ────────────────────────────────────────────────────────

def scan_topic(topic_id, topic_config, rss_books=None):
    """Scan for new books in a single topic. Returns list of book results."""
    log(f"📚 Scanning: {topic_config['label']}")
    all_results = []
    seen_urls = set()

    # Include matching books from publisher RSS feeds
    if rss_books:
        for r in rss_books:
            url = r.get("url", "")
            if url in seen_urls:
                continue
            seen_urls.add(url)

            score, reasons = score_book_result(r, topic_config)
            if score >= 10:
                r["score"] = score
                r["reasons"] = reasons
                r["topic"] = topic_id
                r["source"] = r.get("source", "rss")
                all_results.append(r)

    for query in topic_config["search_queries"]:
        results = search_ddg(query)
        for r in results:
            url = r.get("url", "")
            if url in seen_urls:
                continue
            seen_urls.add(url)

            score, reasons = score_book_result(r, topic_config)
            if score >= 10:  # must at least look like a book
                r["score"] = score
                r["reasons"] = reasons
                r["topic"] = topic_id
                all_results.append(r)

        time.sleep(2)  # rate limit DDG

    # Sort by score
    all_results.sort(key=lambda x: -x.get("score", 0))
    log(f"  Found {len(all_results)} book results")
    return all_results


def llm_analyze_books(all_books, profile=None):
    """Use LLM to analyze found books and produce reading recommendations."""
    if not all_books:
        return ""

    user_name = "AK"
    interests_str = ""
    if profile:
        user_name = profile.get("name", "AK")
        interests = profile.get("book_interests", profile.get("interests", []))
        interests_str = ", ".join(interests)

    # Group by topic
    by_topic = {}
    for b in all_books:
        tid = b.get("topic", "unknown")
        by_topic.setdefault(tid, []).append(b)

    books_text = ""
    for tid, books in sorted(by_topic.items()):
        label = BOOK_TOPICS.get(tid, {}).get("label", tid)
        books_text += f"\n## {label}\n"
        for b in books[:5]:
            books_text += f"  [{b.get('score', 0)}] {b['title']}\n"
            books_text += f"    {b.get('snippet', '')[:200]}\n"
            books_text += f"    URL: {b.get('url', '')}\n\n"

    system_prompt = f"""You are a technical book advisor for {user_name}, an embedded Linux engineer
with expertise in camera drivers, ADAS systems, and a passion for SDR, security research, and hardware.

Their reading interests include: {interests_str}

Analyze the book search results below and produce a curated reading list recommendation.

Structure your response as:

## 📚 NEW BOOKS WORTH READING
For each truly recommended book:
- Title & publisher
- Why it's relevant to {user_name}'s work & interests
- Priority: MUST READ / GOOD TO HAVE / NICE TO KNOW

## 🔥 HOT OFF THE PRESS
Any books published in the last 3 months or in early access that are particularly interesting

## 📋 SUMMARY
- Top 3-5 books you'd prioritize for {user_name}
- Any notable gaps (topics with no good new books)

Rules:
- Skip duplicates, outdated editions, or irrelevant results
- Focus on genuinely new or updated books (2024-2025)
- Skip very niche academic textbooks unless particularly relevant
- Write in English, be concise and practical
- 300-500 words max
- Do NOT include any thinking/reasoning tags"""

    log("🤖 Running LLM book analysis...")
    return call_ollama(system_prompt, books_text, temperature=0.4, max_tokens=1500) or ""


def run_scan(topic_filter=None):
    """Full scan: search for books, analyze with LLM, save results."""
    t0 = time.time()
    BOOKS_DIR.mkdir(parents=True, exist_ok=True)
    THINK_DIR.mkdir(parents=True, exist_ok=True)

    # Load profile
    profile = {}
    if PROFILE_PATH.exists():
        try:
            with open(PROFILE_PATH) as f:
                profile = json.load(f)
        except Exception:
            pass

    # Scan topics
    topics_to_scan = BOOK_TOPICS
    if topic_filter:
        if topic_filter not in BOOK_TOPICS:
            log(f"ERROR: Unknown topic '{topic_filter}'. Use --list to see topics.")
            sys.exit(1)
        topics_to_scan = {topic_filter: BOOK_TOPICS[topic_filter]}

    # Fetch publisher RSS feeds once (shared across all topics)
    log("📡 Fetching publisher RSS feeds...")
    rss_books = fetch_publisher_rss()
    log(f"  Got {len(rss_books)} books from publisher RSS feeds")

    all_books = []
    topic_stats = {}
    for tid, tconfig in topics_to_scan.items():
        books = scan_topic(tid, tconfig, rss_books=rss_books)
        all_books.extend(books)
        topic_stats[tid] = {
            "label": tconfig["label"],
            "found": len(books),
            "top_score": books[0]["score"] if books else 0,
        }

    log(f"\nTotal books found: {len(all_books)} across {len(topics_to_scan)} topics")

    # LLM analysis
    analysis = ""
    if all_books:
        analysis = llm_analyze_books(all_books, profile)

    # Save results
    now = datetime.now()
    output = {
        "meta": {
            "timestamp": now.isoformat(timespec="seconds"),
            "duration_seconds": round(time.time() - t0, 1),
            "topics_scanned": len(topics_to_scan),
            "total_books_found": len(all_books),
        },
        "topic_stats": topic_stats,
        "books": [
            {
                "title": b["title"],
                "url": b.get("url", ""),
                "snippet": b.get("snippet", ""),
                "score": b.get("score", 0),
                "reasons": b.get("reasons", []),
                "topic": b.get("topic", ""),
                "source": b.get("source", "ddg"),
                "publisher": b.get("publisher", ""),
            }
            for b in all_books[:50]  # cap at 50
        ],
        "analysis": analysis,
    }

    # Save daily file
    daily_path = BOOKS_DIR / f"books-{now.strftime('%Y%m%d')}.json"
    tmp = daily_path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    tmp.rename(daily_path)
    log(f"Saved: {daily_path}")

    # Save latest symlink
    latest = BOOKS_DIR / "books-latest.json"
    try:
        latest.unlink(missing_ok=True)
    except TypeError:
        try:
            latest.unlink()
        except FileNotFoundError:
            pass
    latest.symlink_to(daily_path.name)

    # Save as think note
    if analysis:
        ts = now.strftime("%Y%m%d-%H%M")
        scope = topic_filter or "all"
        note = {
            "type": "book-watch",
            "title": f"Book Watch — {scope} — {now.strftime('%d %b %Y')}",
            "content": analysis,
            "generated": now.isoformat(timespec="seconds"),
            "model": OLLAMA_MODEL,
            "context": f"{len(all_books)} books found across {len(topics_to_scan)} topics",
        }
        note_path = THINK_DIR / f"note-book-watch-{scope}-{ts}.json"
        with open(note_path, "w") as f:
            json.dump(note, f, indent=2, ensure_ascii=False)
        log(f"Think note: {note_path}")

    # Signal — only if we found interesting books not already sent today
    hot_books = [b for b in all_books if b.get("score", 0) >= 25]
    sent_path = BOOKS_DIR / f"sent-{now.strftime('%Y%m%d')}.json"
    already_sent = set()
    try:
        already_sent = set(json.loads(sent_path.read_text()))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    new_books = [b for b in hot_books if b.get("title", "") not in already_sent]
    if new_books:
        alert = f"📚 Book Watch — {len(new_books)} hot new books found:\n"
        for b in new_books[:5]:
            alert += f"  → [{b['score']}] {b['title'][:80]}\n"
        if analysis:
            summary_lines = analysis.strip().split("\n")[:6]
            alert += "\n" + "\n".join(summary_lines)
        signal_send(alert[:1500])
        already_sent.update(b.get("title", "") for b in new_books)
        sent_path.write_text(json.dumps(sorted(already_sent)))
    elif hot_books:
        log(f"  {len(hot_books)} hot books already sent today — no duplicate alert")

    duration = time.time() - t0
    log(f"\nDone: {len(all_books)} books, {duration:.0f}s")


def main():
    parser = argparse.ArgumentParser(description="Book publication tracker")
    parser.add_argument("--topic", type=str, default=None,
                        help="Scan a single topic (e.g. linux-kernel)")
    parser.add_argument("--list", action="store_true",
                        help="List available topics")
    args = parser.parse_args()

    if args.list:
        print("Available book topics:")
        for tid, tc in BOOK_TOPICS.items():
            print(f"  {tid:25s} — {tc['label']}")
        return

    log("book-watch.py — Technical book publication tracker")
    run_scan(topic_filter=args.topic)


if __name__ == "__main__":
    main()
