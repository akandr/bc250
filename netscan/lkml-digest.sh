#!/bin/bash
# lkml-digest.sh — Daily linux-media mailing list digest
# Fetches yesterday's linux-media activity from lore.kernel.org,
# filters for camera/sensor/V4L2 topics, summarizes via local LLM
# using a multi-pass chunked architecture, delivers Signal bulletin.
#
# Multi-pass pipeline (safe for slow GPUs / large feeds):
#   Pass 1: Fetch Atom feed, parse, group threads, score relevance
#   Pass 2: Per-thread LLM analysis (one call per thread, saved to disk)
#   Pass 3: Synthesis — combine thread summaries into final bulletin
#   Each intermediate result is saved to disk, so crashes are recoverable.
#
# Cron: 0 4 * * * /opt/netscan/lkml-digest.sh >> /var/log/netscan-lkml.log 2>&1
# (runs 4 AM — 2h safety margin after the 2 AM network scan)
#
# Location on bc250: /opt/netscan/lkml-digest.sh
set -euo pipefail

DATA_DIR="/opt/netscan/data"
LKML_DIR="$DATA_DIR/lkml"
mkdir -p "$LKML_DIR"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] lkml-digest starting"

python3 - "$LKML_DIR" << 'PYEOF'
import sys, os, json, re, time, html as html_mod, hashlib
import xml.etree.ElementTree as ET
import urllib.request, urllib.error
from datetime import datetime, timedelta, timezone
from collections import defaultdict

LKML_DIR = sys.argv[1]
DATA_DIR = os.path.dirname(LKML_DIR)

# ─── Config ───

LORE_BASE = "https://lore.kernel.org/linux-media"
USER_AGENT = "netscan-bc250-lkml/1.0 (daily digest bot)"

OLLAMA_URL = "http://localhost:11434"
OLLAMA_CHAT = f"{OLLAMA_URL}/api/chat"
OLLAMA_MODEL = "qwen3-14b-abl-nothink:latest"
OLLAMA_TIMEOUT_PER_CALL = 600     # 10 min max per LLM call

SIGNAL_RPC = "http://127.0.0.1:8080/api/v1/rpc"
SIGNAL_FROM = "+48532825716"
SIGNAL_TO = "+48503326388"

# Date range: look back 36h for safety (script runs 4 AM,
# covers all of yesterday + early morning edge messages)
now = datetime.now(timezone.utc)
dt_start = (now - timedelta(hours=36)).strftime("%Y%m%d")
dt_end = now.strftime("%Y%m%d")
dt_label = (now - timedelta(days=1)).strftime("%d %b %Y")
dt_file = (now - timedelta(days=1)).strftime("%Y%m%d")

# Work directory for this run (intermediate files)
WORK_DIR = os.path.join(LKML_DIR, f"work-{dt_file}")
os.makedirs(WORK_DIR, exist_ok=True)

# Camera/V4L2/sensor relevance keywords with weights
RELEVANCE = {
    # Core camera subsystem — high weight
    'v4l2': 4, 'v4l': 3, 'libcamera': 4, 'isp': 4, 'mipi': 4,
    'csi-2': 4, 'csi': 3, 'camera': 3, 'uvc': 3, 'videobuf2': 3,
    'subdev': 2, 'media-ctl': 2, 'mc-centric': 2,
    # Sensor drivers
    'sensor': 3, 'omnivision': 4, 'imx2': 3, 'imx3': 3, 'imx4': 3,
    'imx5': 3, 'imx7': 3, 'imx8': 3, 'ov2': 3, 'ov5': 3, 'ov7': 3,
    'ov8': 3, 'ov9': 3, 'ov13': 3, 'gc0': 2, 'gc2': 2, 'gc5': 2,
    'ar0': 2, 'hi8': 2, 'hi5': 2, 'sony imx': 4,
    # ISP / pipeline
    'rkisp': 3, 'mali-c55': 3, 'sun6i': 2, 'stm32-dcmi': 2,
    'starfive': 2, 'camss': 2, 'intel-ipu': 3, 'pisp': 3,
    # Serializer / deserializer
    'max96': 3, 'rdacm': 3, 'gmsl': 3, 'fpd-link': 3,
    'deserializer': 3, 'serializer': 3, 'ds90': 2,
    # Formats / standards
    'pixel format': 2, 'colorspace': 2, 'bayer': 2, 'raw format': 2,
    'metadata': 2, 'dt-binding': 2, 'device tree': 2, 'devicetree': 2,
    # Key people (maintainers)
    'pinchart': 2, 'sakari': 2, 'verkuil': 2, 'hans verkuil': 2,
    'laurent pinchart': 2,
    # Lower-priority media topics (still relevant, less weight)
    'vimc': 1, 'vivid': 1, 'cedrus': 1, 'hantro': 1, 'stateless': 1,
    'verisilicon': 1, 'request api': 1, 'fence': 1,
    # Negative signals (DVB / radio / CEC — not camera)
    'dvb': -1, 'tuner': -1, 'cec': -1, 'remote control': -1, 'rc core': -1,
}

NS = {'atom': 'http://www.w3.org/2005/Atom',
      'thr': 'http://purl.org/syndication/thread/1.0'}

# ─── Helpers ───

def fetch_url(url, max_retries=3, timeout=30):
    """Fetch URL with retries and polite delay."""
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
            resp = urllib.request.urlopen(req, timeout=timeout)
            return resp.read()
        except Exception as ex:
            print(f"  Fetch attempt {attempt+1} failed: {ex}")
            if attempt < max_retries - 1:
                time.sleep(3 * (attempt + 1))
    return None

def strip_html(text):
    """Extract plain text from lore's XHTML content."""
    text = re.sub(r'<span[^>]*class="q"[^>]*>', '', text)
    text = re.sub(r'</span>', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = html_mod.unescape(text)
    return text.strip()

def normalize_subject(subj):
    """Strip Re:, [PATCH vN M/N] etc to get base thread topic."""
    s = re.sub(r'^\s*(Re|Fwd):\s*', '', subj, flags=re.I)
    s = re.sub(r'\[PATCH[^\]]*\]\s*', '', s)
    s = re.sub(r'^\s*(Re|Fwd):\s*', '', s, flags=re.I)
    return s.strip()

def relevance_score(text):
    """Score text by camera/sensor relevance."""
    low = text.lower()
    score = 0
    matched = []
    for kw, w in RELEVANCE.items():
        if kw in low:
            score += w
            if w > 0:
                matched.append(kw)
    return score, matched

def truncate(text, max_chars=2000):
    """Truncate text preserving word boundaries."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(' ', 1)[0] + ' [...]'

def _signal_send_one(msg):
    """Send a single message via Signal JSON-RPC."""
    try:
        payload = json.dumps({
            "jsonrpc": "2.0",
            "method": "send",
            "params": {
                "account": SIGNAL_FROM,
                "recipient": [SIGNAL_TO],
                "message": msg
            },
            "id": "lkml-digest"
        })
        req = urllib.request.Request(
            SIGNAL_RPC, data=payload.encode(),
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception as ex:
        print(f"  Signal send failed: {ex}")
        return False

SIGNAL_CHUNK_SIZE = 2000  # chars per message — Signal displays ~4096 but shorter is more readable

def send_signal(msg):
    """Send via Signal, splitting long messages into multiple parts at paragraph boundaries."""
    if len(msg) <= SIGNAL_CHUNK_SIZE:
        return _signal_send_one(msg)

    # Split at double-newline (paragraph) boundaries
    paragraphs = msg.split("\n\n")
    chunks = []
    current = ""
    for para in paragraphs:
        # If adding this paragraph would exceed limit, flush current chunk
        if current and len(current) + 2 + len(para) > SIGNAL_CHUNK_SIZE:
            chunks.append(current.strip())
            current = para
        else:
            current = current + "\n\n" + para if current else para
    if current.strip():
        chunks.append(current.strip())

    # If any single chunk is still too long, hard-split at newlines
    final_chunks = []
    for chunk in chunks:
        if len(chunk) <= SIGNAL_CHUNK_SIZE:
            final_chunks.append(chunk)
        else:
            lines = chunk.split("\n")
            sub = ""
            for line in lines:
                if sub and len(sub) + 1 + len(line) > SIGNAL_CHUNK_SIZE:
                    final_chunks.append(sub.strip())
                    sub = line
                else:
                    sub = sub + "\n" + line if sub else line
            if sub.strip():
                final_chunks.append(sub.strip())

    total = len(final_chunks)
    ok = True
    for i, chunk in enumerate(final_chunks, 1):
        header = f"[{i}/{total}] " if total > 1 else ""
        if not _signal_send_one(header + chunk):
            ok = False
        if i < total:
            time.sleep(1)  # small pause between messages
    return ok

def ollama_health():
    """Check if Ollama is alive and has the model loaded."""
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        models = [m["name"] for m in data.get("models", [])]
        return OLLAMA_MODEL in models
    except Exception as ex:
        print(f"  Ollama health check failed: {ex}")
        return False

def call_ollama(system_prompt, user_prompt, temperature=0.3, max_tokens=2048,
                label=""):
    """Call Ollama with health monitoring and retry."""
    if not ollama_health():
        print(f"  [{label}] Ollama not healthy, waiting 30s...")
        time.sleep(30)
        if not ollama_health():
            print(f"  [{label}] Ollama still not healthy, aborting this call")
            return None

    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        }
    })

    for attempt in range(2):
        try:
            req = urllib.request.Request(
                OLLAMA_CHAT, data=payload.encode(),
                headers={"Content-Type": "application/json"}
            )
            t0 = time.time()
            resp = urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT_PER_CALL)
            result = json.loads(resp.read())
            elapsed = time.time() - t0
            content = result.get("message", {}).get("content", "")
            tokens = result.get("eval_count", 0)
            tps = tokens / elapsed if elapsed > 0 else 0
            print(f"  [{label}] OK {elapsed:.0f}s, {tokens} tok ({tps:.1f} t/s)")
            return content
        except Exception as ex:
            print(f"  [{label}] Attempt {attempt+1} failed: {ex}")
            if attempt == 0:
                time.sleep(10)
                if not ollama_health():
                    print(f"  [{label}] Ollama crashed, waiting 60s...")
                    time.sleep(60)
                else:
                    time.sleep(5)
    return None

def save_intermediate(name, data):
    """Save intermediate result to work dir."""
    path = os.path.join(WORK_DIR, name)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)

def load_intermediate(name):
    """Load intermediate result from work dir."""
    path = os.path.join(WORK_DIR, name)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None

def thread_hash(key):
    """Short hash for thread filenames."""
    return hashlib.md5(key.encode()).hexdigest()[:8]

# ═══════════════════════════════════════════════════════════
# PASS 1: Fetch, parse, group, score
# ═══════════════════════════════════════════════════════════

threads_data = load_intermediate("pass1-threads.json")

if threads_data:
    print("[PASS 1] Recovered from disk — loading cached threads")
    camera_threads_data = threads_data["camera_threads"]
    other_threads_data = threads_data["other_threads"]
    total_messages = threads_data["total_messages"]
    total_threads = threads_data["total_threads"]
else:
    print(f"[PASS 1] Fetching linux-media Atom feed ({dt_start}..{dt_end})")
    feed_url = f"{LORE_BASE}/?q=d:{dt_start}..{dt_end}&x=A"
    raw_xml = fetch_url(feed_url, timeout=45)

    if not raw_xml:
        print("  FATAL: could not fetch Atom feed")
        send_signal(f"📡 LKML DIGEST — {dt_label}\n\n❌ Failed to fetch linux-media feed from lore.kernel.org")
        sys.exit(1)

    with open(os.path.join(WORK_DIR, "feed-raw.xml"), "wb") as f:
        f.write(raw_xml)

    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError as ex:
        print(f"  XML parse error: {ex}")
        send_signal(f"📡 LKML DIGEST — {dt_label}\n\n❌ Failed to parse Atom feed XML")
        sys.exit(1)

    messages = []
    for entry in root.findall('atom:entry', NS):
        author_el = entry.find('atom:author/atom:name', NS)
        email_el = entry.find('atom:author/atom:email', NS)
        title_el = entry.find('atom:title', NS)
        updated_el = entry.find('atom:updated', NS)
        link_el = entry.find('atom:link', NS)
        content_el = entry.find('atom:content', NS)
        reply_el = entry.find('thr:in-reply-to', NS)

        author = author_el.text if author_el is not None else "?"
        email = email_el.text if email_el is not None else ""
        subject = title_el.text if title_el is not None else "(no subject)"
        updated = updated_el.text if updated_el is not None else ""
        link = link_el.get('href', '') if link_el is not None else ""

        body_text = ""
        if content_el is not None:
            raw_content = ET.tostring(content_el, encoding='unicode', method='html')
            body_text = strip_html(raw_content)

        is_reply = reply_el is not None
        messages.append({
            "author": author, "email": email, "subject": subject,
            "updated": updated, "link": link, "body": body_text,
            "is_reply": is_reply, "norm_subject": normalize_subject(subject),
        })

    print(f"  Parsed {len(messages)} messages")

    if not messages:
        send_signal(f"📡 LKML DIGEST — {dt_label}\n\n😴 Quiet day — no messages on linux-media")
        sys.exit(0)

    threads = defaultdict(lambda: {"messages": [], "authors": set(),
                                    "score": 0, "keywords": set(),
                                    "subject": "", "is_patch": False,
                                    "patch_version": "", "patch_parts": "",
                                    "links": []})

    for msg in messages:
        ns = msg["norm_subject"]
        key = re.sub(r'\s+', ' ', ns.lower().strip())
        if not key:
            key = msg["subject"].lower().strip()

        t = threads[key]
        t["messages"].append(msg)
        t["authors"].add(msg["author"])
        if msg["link"]:
            t["links"].append(msg["link"])

        if not t["subject"] or not msg["is_reply"]:
            t["subject"] = msg["subject"]

        patch_m = re.search(r'\[PATCH\s*(v\d+)?\s*(\d+/\d+)?\]', msg["subject"], re.I)
        if patch_m:
            t["is_patch"] = True
            if patch_m.group(1):
                t["patch_version"] = patch_m.group(1)
            if patch_m.group(2):
                t["patch_parts"] = patch_m.group(2)

        full_text = msg["subject"] + " " + msg["body"][:500]
        sc, kws = relevance_score(full_text)
        t["score"] += sc
        t["keywords"].update(kws)

    for key, t in threads.items():
        t["score"] += len(t["messages"]) * 0.5
        t["score"] += len(t["authors"]) * 0.3

    ranked = sorted(threads.items(), key=lambda x: -x[1]["score"])

    def serialize_thread(key, t):
        return {
            "key": key, "subject": t["subject"],
            "score": round(t["score"], 1), "messages": t["messages"],
            "authors": sorted(t["authors"]),
            "keywords": sorted(t["keywords"]),
            "is_patch": t["is_patch"],
            "patch_version": t.get("patch_version", ""),
            "patch_parts": t.get("patch_parts", ""),
            "links": t.get("links", [])[:3],
        }

    camera_threads_data = [serialize_thread(k, t) for k, t in ranked if t["score"] >= 3]
    other_threads_data = [serialize_thread(k, t) for k, t in ranked if 0 <= t["score"] < 3]

    total_messages = len(messages)
    total_threads = len(threads)

    save_intermediate("pass1-threads.json", {
        "camera_threads": camera_threads_data,
        "other_threads": other_threads_data,
        "total_messages": total_messages,
        "total_threads": total_threads,
    })
    print(f"  {total_threads} threads, {len(camera_threads_data)} camera-relevant, {len(other_threads_data)} other — saved")


# ═══════════════════════════════════════════════════════════
# PASS 2: Per-thread LLM analysis (chunked, resumable)
# ═══════════════════════════════════════════════════════════

THREAD_SYSTEM = """You are a Linux kernel media subsystem expert. Analyze this mailing list thread about camera drivers, V4L2, ISP, MIPI CSI, sensors, libcamera, or UVC.

Produce a structured analysis in plain text:

SUBJECT: (clean one-line subject)
TYPE: patch | discussion | bug-report | review | rfc
SUBSYSTEM: (driver or subsystem name, e.g. uvcvideo, camss, imx283, rkisp1, v4l2-core, dt-bindings)
IMPORTANCE: high | medium | low

SUMMARY: (4-6 sentences explaining WHAT is being changed/discussed, WHY it matters, and HOW it works technically. Be specific about kernel structures, register layouts, pixel formats, DT properties, or API changes. A reader should understand the technical content without reading the original emails.)

KEY PEOPLE: (who is involved — author, reviewer, maintainer)
STATUS: (accepted / needs-revision / under-review / discussion-ongoing, main review concerns if any)
IMPACT: (2-3 sentences: what does this affect for someone building camera pipelines on embedded Linux? Does it break anything? Enable new hardware? Change APIs? What boards or SoCs benefit?)

Be detailed. Reference struct names, function names, format fourcc codes, DT compatible strings where relevant."""

print(f"\n[PASS 2] Per-thread LLM analysis ({min(len(camera_threads_data), 15)} of {len(camera_threads_data)} camera threads)")

thread_summaries = []
failed = 0
pass2_total_time = 0

for i, t in enumerate(camera_threads_data[:15]):
    thash = thread_hash(t["key"])
    cache_file = f"thread-{thash}.json"
    cached = load_intermediate(cache_file)

    if cached:
        print(f"  [{i+1}] CACHED: {t['subject'][:65]}")
        thread_summaries.append(cached)
        continue

    # Build focused per-thread prompt
    msgs = t["messages"]
    authors = ", ".join(t["authors"])
    kws = ", ".join(t["keywords"])

    initial_post = ""
    for m in msgs:
        if not m["is_reply"]:
            initial_post = truncate(m["body"], 2500)
            break
    if not initial_post and msgs:
        initial_post = truncate(msgs[0]["body"], 2500)

    review_snippets = []
    for m in msgs:
        if m["is_reply"]:
            review_snippets.append(f"[{m['author']}]: {truncate(m['body'], 600)}")
    reviews_text = "\n\n".join(review_snippets[:5])

    patch_tag = ""
    if t["is_patch"]:
        parts = []
        if t["patch_version"]: parts.append(t["patch_version"])
        if t["patch_parts"]: parts.append(t["patch_parts"])
        patch_tag = f" [PATCH {' '.join(parts)}]" if parts else " [PATCH]"

    prompt = f"""Analyze this linux-media thread:

Subject: {t['subject']}{patch_tag}
Authors: {authors}
Messages: {len(msgs)}
Keywords: {kws}
Link: {t['links'][0] if t.get('links') else 'N/A'}

=== INITIAL POST ===
{initial_post}

=== REVIEWS ({len(review_snippets)} replies) ===
{reviews_text if reviews_text else "(no replies yet)"}

Produce the structured analysis now."""

    with open(os.path.join(WORK_DIR, f"prompt-thread-{thash}.txt"), "w") as f:
        f.write(prompt)

    prompt_kb = len(THREAD_SYSTEM + prompt) / 1024
    print(f"  [{i+1}] Analyzing ({prompt_kb:.1f}KB): {t['subject'][:55]}...")

    t0 = time.time()
    result = call_ollama(THREAD_SYSTEM, prompt, temperature=0.2,
                         max_tokens=800, label=f"T{i+1}")
    call_time = time.time() - t0
    pass2_total_time += call_time

    if result:
        summary_data = {
            "subject": t["subject"],
            "score": t["score"],
            "is_patch": t["is_patch"],
            "patch_version": t.get("patch_version", ""),
            "authors": t["authors"],
            "keywords": t["keywords"],
            "n_messages": len(msgs),
            "links": t.get("links", [])[:2],
            "llm_analysis": result,
            "llm_time_s": round(call_time, 1),
        }
        save_intermediate(cache_file, summary_data)
        thread_summaries.append(summary_data)
        # Brief pause between calls to let GPU breathe
        time.sleep(3)
    else:
        failed += 1
        print(f"    ⚠ LLM failed for this thread")
        thread_summaries.append({
            "subject": t["subject"], "score": t["score"],
            "is_patch": t["is_patch"], "authors": t["authors"],
            "keywords": t["keywords"], "n_messages": len(msgs),
            "links": t.get("links", [])[:2], "llm_analysis": None,
        })

if failed:
    print(f"  ⚠ {failed} thread(s) failed LLM analysis")

save_intermediate("pass2-summaries.json", thread_summaries)
print(f"  Pass 2 complete: {len(thread_summaries)} analyzed, {failed} failed, {pass2_total_time:.0f}s total")


# ═══════════════════════════════════════════════════════════
# PASS 3: Synthesis — combine into final bulletin
# ═══════════════════════════════════════════════════════════

print(f"\n[PASS 3] Synthesizing final bulletin")

synth_parts = []
for i, ts in enumerate(thread_summaries):
    analysis = ts.get("llm_analysis")
    if analysis:
        synth_parts.append(f"--- THREAD {i+1} (score {ts['score']}, {ts['n_messages']} msgs) ---\n{analysis}")
    else:
        synth_parts.append(f"--- THREAD {i+1} (score {ts['score']}, {ts['n_messages']} msgs) ---\n"
                          f"Subject: {ts['subject']}\nAuthors: {', '.join(ts['authors'])}\n"
                          f"Keywords: {', '.join(ts['keywords'])}\n(analysis unavailable)")

synth_data = "\n\n".join(synth_parts)

other_lines = []
for t in other_threads_data[:15]:
    other_lines.append(f"- {t['subject']} ({t['score']:.0f} score, {len(t['messages'])} msgs)")
other_text = "\n".join(other_lines) if other_lines else "(none)"

SYNTH_SYSTEM = """You produce the daily digest of the linux-media kernel mailing list. You receive pre-analyzed thread summaries and synthesize them into a comprehensive, technically detailed bulletin for a developer who works on embedded Linux camera systems.

Output format (plain text, Signal messenger, aim for 3000-4500 chars):

📡 LINUX-MEDIA DIGEST — [date]

For each significant thread (up to 6-7), write a DETAILED paragraph:
• Title line with ** bold markers **
• 4-7 sentences of real technical explanation:
  - What specific driver, module, or subsystem is affected
  - What kernel structures (struct v4l2_*, struct media_*, etc.) or APIs change
  - What hardware this targets (sensor model, SoC, ISP block)
  - Why the change is needed (bug, new feature, API cleanup, new hardware support)
  - What the reviewers said — any objections, requested changes, nit-picks
  - Current patch status (accepted, needs revision, RFC)
• Authors with their roles (submitter, reviewer, maintainer)
• lore.kernel.org link if available

📋 MINOR ACTIVITY:
Brief lines for less important threads.

📊 STATS line at the end.

GUIDELINES:
- Explain acronyms on first use: CSI-2 (Camera Serial Interface), ISP (Image Signal Processor), DT (Device Tree), CCS (MIPI Camera Command Set)
- Mention specific function names, struct fields, compatible strings, fourcc codes
- Note practical impact: "sensor X now works on board Y", "this breaks userspace ABI for Z"
- For patches: note submission status, version, part count
- Be technical — the reader is a kernel developer, not a manager"""

synth_prompt = f"""Synthesize these {len(thread_summaries)} pre-analyzed linux-media threads into a daily digest for {dt_label}.

=== THREAD ANALYSES ===

{synth_data}

=== OTHER ACTIVITY ({len(other_threads_data)} threads) ===
{other_text}

=== STATS ===
Total: {total_messages} messages, {total_threads} threads, {len(camera_threads_data)} camera-relevant

Produce the comprehensive digest bulletin now."""

with open(os.path.join(WORK_DIR, "prompt-synthesis.txt"), "w") as f:
    f.write(f"=== SYSTEM ===\n{SYNTH_SYSTEM}\n\n=== USER ===\n{synth_prompt}\n")

synth_kb = len(SYNTH_SYSTEM + synth_prompt) / 1024
print(f"  Synthesis prompt: {synth_kb:.1f}KB")

t0 = time.time()
bulletin_text = call_ollama(SYNTH_SYSTEM, synth_prompt, temperature=0.3,
                            max_tokens=3000, label="SYNTH")
synth_elapsed = time.time() - t0


# ═══════════════════════════════════════════════════════════
# OUTPUT: Format, save, send
# ═══════════════════════════════════════════════════════════

print(f"\n[OUTPUT] Formatting and sending")

if not bulletin_text:
    print("  ⚠ Synthesis LLM failed — building fallback from per-thread analyses")
    lines = [f"📡 LINUX-MEDIA DIGEST — {dt_label}", ""]
    for ts in thread_summaries:
        analysis = ts.get("llm_analysis")
        if analysis:
            subj_line = ts["subject"]
            summary_line = ""
            for al in analysis.split("\n"):
                al_s = al.strip()
                if al_s.startswith("SUBJECT:"):
                    subj_line = al_s.replace("SUBJECT:", "").strip()
                elif al_s.startswith("SUMMARY:"):
                    summary_line = al_s.replace("SUMMARY:", "").strip()
            lines.append(f"🔧 **{subj_line}**")
            if summary_line:
                lines.append(f"   {summary_line[:400]}")
            lines.append(f"   — {', '.join(ts['authors'][:3])}")
            lines.append("")
        else:
            patch_tag = " 📦" if ts["is_patch"] else ""
            lines.append(f"• {ts['subject']}{patch_tag} ({ts['n_messages']} msgs)")
    lines.append(f"\n📊 {total_messages} messages, {total_threads} threads, {len(camera_threads_data)} camera-relevant")
    bulletin_text = "\n".join(lines)

bulletin_text = bulletin_text.strip()
if len(bulletin_text) > 4500:
    bulletin_text = bulletin_text[:4400] + "\n\n[...truncated]"

total_llm_time = pass2_total_time + synth_elapsed

# Build digest JSON
digest = {
    "date": dt_label,
    "date_file": dt_file,
    "generated": datetime.now().isoformat(timespec="seconds"),
    "total_messages": total_messages,
    "total_threads": total_threads,
    "camera_threads": len(camera_threads_data),
    "other_threads": len(other_threads_data),
    "top_threads": [
        {
            "subject": ts["subject"],
            "score": ts["score"],
            "messages": ts["n_messages"],
            "authors": ts["authors"],
            "keywords": ts["keywords"],
            "is_patch": ts["is_patch"],
            "patch_version": ts.get("patch_version", ""),
            "links": ts.get("links", []),
            "llm_analysis": ts.get("llm_analysis", ""),
        }
        for ts in thread_summaries
    ],
    "other_thread_subjects": [t["subject"] for t in other_threads_data[:20]],
    "ollama_model": OLLAMA_MODEL,
    "pass2_time_s": round(pass2_total_time, 1),
    "synthesis_time_s": round(synth_elapsed, 1),
    "total_llm_time_s": round(total_llm_time, 1),
    "threads_analyzed": len(thread_summaries),
    "threads_failed": failed,
    "bulletin": bulletin_text,
    "pipeline": "multi-pass-v2",
}

digest_path = os.path.join(LKML_DIR, f"digest-{dt_file}.json")
with open(digest_path, "w") as f:
    json.dump(digest, f, indent=2, default=str)
print(f"  Saved: {digest_path}")

txt_path = os.path.join(LKML_DIR, f"digest-{dt_file}.txt")
with open(txt_path, "w") as f:
    f.write(bulletin_text)
    f.write(f"\n\n--- Generated {digest['generated']} by {OLLAMA_MODEL} ---\n")
    f.write(f"--- Pipeline: multi-pass v2 ({len(thread_summaries)} threads analyzed, then synthesized) ---\n")
    f.write(f"--- LLM time: {pass2_total_time:.0f}s analysis + {synth_elapsed:.0f}s synthesis = {total_llm_time:.0f}s total ---\n")
    f.write(f"--- {total_messages} messages, {total_threads} threads, {len(camera_threads_data)} camera-relevant ---\n")
print(f"  Saved: {txt_path}")

detail_path = os.path.join(LKML_DIR, f"threads-{dt_file}.json")
with open(detail_path, "w") as f:
    json.dump(thread_summaries, f, indent=2, default=str)
print(f"  Saved: {detail_path}")

if send_signal(bulletin_text):
    n_chunks = max(1, (len(bulletin_text) + SIGNAL_CHUNK_SIZE - 1) // SIGNAL_CHUNK_SIZE)
    print(f"  ✅ Signal bulletin sent ({len(bulletin_text)} chars, {n_chunks} message{'s' if n_chunks > 1 else ''})")
else:
    print("  ⚠ Signal send failed — bulletin saved to file only")

# Regenerate web dashboard
if os.path.exists("/opt/netscan/generate-html.py"):
    import subprocess
    try:
        subprocess.run(["python3", "/opt/netscan/generate-html.py"],
                       timeout=30, capture_output=True)
        print("  Dashboard regenerated")
    except:
        pass

# Clean up old work dirs (keep last 7 days)
for d in sorted(os.listdir(LKML_DIR)):
    if d.startswith("work-") and d < f"work-{(now - timedelta(days=7)).strftime('%Y%m%d')}":
        import shutil
        shutil.rmtree(os.path.join(LKML_DIR, d), ignore_errors=True)

print(f"\n[DONE] {len(camera_threads_data)} camera threads from {total_messages} messages")
print(f"  Bulletin: {len(bulletin_text)} chars")
print(f"  LLM: {pass2_total_time:.0f}s pass2 + {synth_elapsed:.0f}s synth = {total_llm_time:.0f}s total")

PYEOF

echo "[$(date '+%Y-%m-%d %H:%M:%S')] lkml-digest done"
