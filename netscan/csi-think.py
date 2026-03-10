#!/usr/bin/env python3
"""csi-think.py — Deep LLM analysis of CSI camera enablement, ISP, and sensor ecosystem.

Aggregates intelligence from multiple sources to produce deep analysis on
camera bring-up, ISP pipelines, MIPI CSI sensor enablement, and related topics.

Sources:
  - NVIDIA Developer Forum (Jetson/DRIVE camera & ISP topics via Discourse API)
  - lore.kernel.org linux-media mailing list (V4L2, ISP, CSI driver patches)
  - lore.kernel.org jetson-tegra mailing list (Tegra camera subsystem)
  - libcamera project activity (recent MRs/issues)
  - DuckDuckGo news search (camera SoC, ISP, MIPI CSI industry news)

Focus areas:
  --focus csi-drivers    : MIPI CSI-2 sensor driver bring-up, I2C, DT bindings
  --focus isp-pipeline   : ISP architecture, tuning, raw→YUV processing
  --focus jetson-camera   : NVIDIA Jetson/DRIVE camera stack (Argus, V4L2, DeepStream)
  --focus serdes          : GMSL/FPD-Link camera serializer/deserializer ecosystem
  --focus libcamera       : libcamera framework, IPA modules, pipeline handlers

Usage:
    python3 csi-think.py --focus csi-drivers       # CSI driver analysis
    python3 csi-think.py --focus jetson-camera      # Jetson camera deep-dive
    python3 csi-think.py --summary                  # Cross-topic synthesis
    python3 csi-think.py --list                     # List focus areas

Output: /opt/netscan/data/csi-think/
"""

import argparse, html, json, os, re, sys, time, urllib.request, urllib.error, urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from llm_sanitize import sanitize_llm_output

# ── Config ─────────────────────────────────────────────────────────────────

OLLAMA_URL   = "http://localhost:11434"
OLLAMA_CHAT  = f"{OLLAMA_URL}/api/chat"
OLLAMA_MODEL = "qwen3:14b"

DATA_DIR     = Path("/opt/netscan/data")
CSI_DIR      = DATA_DIR / "csi-think"
PROFILE_FILE = Path("/opt/netscan/profile.json")

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

NVIDIA_FORUM_BASE = "https://forums.developer.nvidia.com"

# ── Focus areas ────────────────────────────────────────────────────────────

FOCUS_AREAS = {
    "csi-drivers": {
        "label": "MIPI CSI-2 Sensor Driver Enablement",
        "nvidia_keywords": ["CSI camera driver", "MIPI sensor", "device tree camera"],
        "nvidia_categories": [486, 487, 632, 75, 76],  # All Jetson boards
        "lore_feed": "linux-media",
        "lore_dir": "lkml",
        "ddg_queries": [
            "MIPI CSI-2 sensor driver Linux kernel 2025 2026",
            "V4L2 camera sensor bring-up embedded",
        ],
        "analysis_prompt": """MIPI CSI-2 SENSOR DRIVER ENABLEMENT — Deep Technical Analysis

Analyze the current state of CSI camera sensor driver development:

1. NEW SENSOR DRIVERS & PATCHES
   What new image sensor drivers are being upstreamed to the Linux kernel?
   Which sensors are getting major updates (new features, bug fixes)?
   What device tree binding changes are happening for camera sensors?
   Highlight any new Omnivision, Sony IMX, Samsung, or ON Semi sensor support.

2. V4L2 SUBDEV & MEDIA FRAMEWORK EVOLUTION
   Changes to v4l2-subdev, media controller, or streams API.
   New or modified pixel formats, colorspace handling, metadata support.
   Any deprecations or API transitions happening?

3. CSI-2 RECEIVER & PHY DRIVERS
   Changes to MIPI CSI-2 receiver drivers (DPHY, CPHY).
   Platform-specific CSI changes (Tegra, Renesas, Samsung, Rockchip, etc.).
   Link frequency handling, lane configuration, virtual channel support.

4. DEVICE TREE & FIRMWARE INTERFACE
   New dt-bindings for camera subsystems, sensors, or ISPs.
   Changes to the camera port/endpoint model.
   ACPI vs DT approaches for sensor discovery.

5. PRACTICAL BRING-UP INSIGHTS
   Common problems discussed in forums (I2C address conflicts, power sequencing,
   clock configuration, lane mapping).
   Debugging techniques, useful tools (media-ctl, v4l2-ctl, v4l2-compliance).
   Tips from NVIDIA Jetson forum posts about real-world sensor bring-up.

6. CONTRIBUTION OPPORTUNITIES
   Areas where upstream help is needed (TODO items, RFC patches).
   Sensors with incomplete or outdated drivers.
   Testing gaps (v4l2-compliance failures, untested configurations).
""",
    },
    "isp-pipeline": {
        "label": "ISP Architecture & Pipeline Processing",
        "nvidia_keywords": ["ISP pipeline", "image signal processor", "raw processing camera"],
        "nvidia_categories": [486, 487, 632, 189],  # Jetson + Video Processing
        "lore_feed": "linux-media",
        "lore_dir": "lkml",
        "ddg_queries": [
            "Linux ISP image signal processor driver 2025 2026",
            "RKISP Mali-C55 PiSP kernel driver",
        ],
        "analysis_prompt": """ISP ARCHITECTURE & PIPELINE PROCESSING — Deep Technical Analysis

Analyze the state of Image Signal Processor (ISP) support in the Linux ecosystem:

1. ISP DRIVER LANDSCAPE
   Status of major ISP drivers: RKISP1, Mali-C55, PiSP, Intel IPU, Renesas VSP,
   Samsung FIMC-IS/IS, Mediatek, Qualcomm CAMSS, StarFive.
   New ISP drivers being developed or upstreamed.
   Which SOC vendors are most active in ISP upstreaming?

2. ISP TUNING & 3A ALGORITHMS
   How is ISP parameter tuning evolving in the open-source ecosystem?
   libcamera IPA (Image Processing Algorithm) module status for different ISPs.
   Auto-exposure, auto-white-balance, auto-focus — current state of open implementations.
   Tuning file formats and tools (libcamera tuning scripts, vendor-specific tools).

3. RAW → YUV PROCESSING PIPELINE
   How the kernel ISP driver interfaces with userspace (V4L2 M2M, media-centric).
   Buffer management, DMA-BUF sharing between ISP stages.
   Throughput/latency considerations for real-time processing.

4. HDR & ADVANCED FEATURES
   Multi-exposure HDR support in ISP drivers.
   Wide dynamic range processing approaches.
   Computational photography features reaching the kernel/libcamera.

5. NVIDIA-SPECIFIC ISP (Jetson)
   NVIDIA VI/ISP pipeline architecture on Orin platform.
   Argus camera framework vs V4L2 direct access.
   NVIDIA-specific ISP tuning, camera_overrides.isp, nvarguscamerasrc.

6. CROSS-PLATFORM COMPARISON
   How different SoC ISP stacks compare in capability and openness.
   Which platforms offer best Linux camera experience?
   Practical recommendations for production camera pipelines.
""",
    },
    "jetson-camera": {
        "label": "NVIDIA Jetson/DRIVE Camera Stack",
        "nvidia_keywords": [
            "CSI camera Orin", "Argus camera", "V4L2 Jetson camera",
            "nvarguscamerasrc", "libargus", "GMSL camera Orin",
        ],
        "nvidia_categories": [486, 487, 632, 636, 741, 15],  # All Jetson + DRIVE + DeepStream
        "lore_feed": "jetson-tegra",
        "lore_dir": "jetson",
        "ddg_queries": [
            "NVIDIA Jetson Orin camera bring-up CSI 2025 2026",
            "NVIDIA DRIVE AGX camera ISP pipeline",
        ],
        "analysis_prompt": """NVIDIA JETSON/DRIVE CAMERA STACK — Deep Technical Analysis

Analyze the NVIDIA Jetson and DRIVE camera ecosystem:

1. JETSON ORIN CAMERA SUBSYSTEM
   Camera pipeline architecture: VI → ISP → output.
   V4L2 media controller topology on Jetson Orin (nvcsi, vi, isp).
   Common camera configurations: single CSI, multi-camera, GMSL aggregator.
   JetPack version compatibility and camera driver changes across releases.

2. ARGUS vs V4L2 DIRECT ACCESS
   When to use libargus/nvarguscamerasrc vs direct V4L2 access.
   Performance comparison, feature availability differences.
   How Argus interacts with the ISP vs bypassing it.
   GStreamer pipeline examples for both approaches.

3. COMMON BRING-UP CHALLENGES (from forum posts)
   Sensor driver registration issues (device tree, I2C).
   CSI lane configuration problems, error recovery (timeout, CRC errors).
   Power sequencing for camera modules (regulator, GPIO, clock ordering).
   GMSL deserializer setup (MAX9296/MAX96712 + MAX9295 serializer).

4. DEEPSTREAM & AI VISION PIPELINE
   Camera integration with DeepStream SDK.
   Multi-camera synchronization for surround view / ADAS applications.
   GPU preprocessing (scaling, colorspace conversion, distortion correction).

5. DRIVE PLATFORM DIFFERENCES
   How DRIVE AGX Orin/Thor camera stack differs from Jetson.
   SafetyNet camera diagnostics, redundancy features.
   DRIVE OS vs JetPack camera capabilities.

6. PRODUCTION DEPLOYMENT INSIGHTS
   Thermal management for multi-camera systems.
   Camera watchdog and recovery mechanisms.
   Frame rate stability, latency optimization techniques.
   Real-world forum discussions about production camera issues.
""",
    },
    "serdes": {
        "label": "GMSL/FPD-Link Camera SerDes Ecosystem",
        "nvidia_keywords": [
            "GMSL camera", "MAX96712 MAX9296", "FPD-Link camera",
            "serializer deserializer camera", "GMSL2 Orin",
        ],
        "nvidia_categories": [486, 487, 632, 75],  # Jetson boards
        "lore_feed": "linux-media",
        "lore_dir": "lkml",
        "ddg_queries": [
            "GMSL2 camera serializer Linux driver 2025 2026",
            "Maxim MAX96712 FPD-Link DS90UB9 Linux kernel",
        ],
        "analysis_prompt": """GMSL/FPD-LINK CAMERA SERDES ECOSYSTEM — Deep Technical Analysis

Analyze the camera serializer/deserializer ecosystem for automotive and embedded:

1. GMSL DRIVER STATUS
   MAX9296/MAX96712 deserializer Linux driver status (upstream + vendor trees).
   MAX9295/MAX96717 serializer support.
   V4L2 subdev model for SerDes: how the topology is represented.
   Device tree bindings for multi-camera SerDes aggregators.

2. FPD-LINK III/IV
   TI DS90UB9xx series driver status in Linux kernel.
   Comparison with GMSL in terms of bandwidth, reach, feature set.
   Multi-camera aggregation approaches.

3. LINUX KERNEL PATCHES & UPSTREAM STATUS
   Recent patches for camera SerDes drivers on linux-media.
   What's being actively developed vs stalled RFC patches.
   Key maintainers and review bottlenecks.

4. JETSON/ORIN INTEGRATION
   How GMSL cameras connect to Jetson Orin (via dedicated GMSL board or CSI adapter).
   Common Jetson GMSL configurations discussed in NVIDIA forums.
   Tier1 GMSL camera module vendors (Leopard Imaging, SENSING, e-con, Arducam).

5. AUTOMOTIVE APPLICATIONS
   Surround view, DMS/OMS, ADAS camera configurations.
   Multi-camera synchronization over GMSL.
   Long-reach camera cabling considerations.

6. PRACTICAL BRING-UP TIPS
   Common SerDes debugging techniques (I2C address remapping, link lock status).
   GMSL link training issues, error reporting.
   Forum posts about real-world SerDes problems and solutions.
""",
    },
    "libcamera": {
        "label": "libcamera Framework & Pipeline Handlers",
        "nvidia_keywords": ["libcamera", "camera framework"],
        "nvidia_categories": [486, 487, 632],
        "lore_feed": "linux-media",
        "lore_dir": "lkml",
        "ddg_queries": [
            "libcamera pipeline handler IPA module 2025 2026",
            "libcamera embedded Linux camera framework",
        ],
        "analysis_prompt": """LIBCAMERA FRAMEWORK & PIPELINE HANDLERS — Deep Technical Analysis

Analyze the state of the libcamera camera framework:

1. PIPELINE HANDLER DEVELOPMENT
   Status of pipeline handlers: IPU3, RKISP1, Raspberry Pi, Simple, UVC, Mali-C55.
   New pipeline handlers in development.
   How pipeline handlers abstract platform-specific camera stacks.

2. IPA MODULE ECOSYSTEM
   Image Processing Algorithm module status per platform.
   Algorithm implementations: AGC, AWB, AF, lens shading, noise reduction.
   IPA isolation (sandbox) architecture and security model.
   How to write a new IPA module for a custom ISP.

3. GSTREAMER & APPLICATION INTEGRATION
   libcamerasrc GStreamer element: features, performance, limitations.
   Integration with applications: PipeWire, Chromium, Firefox.
   Android camera HAL via libcamera.

4. RECENT DEVELOPMENT ACTIVITY
   Latest merge requests and commits in the libcamera GitLab.
   Conference talks, developer discussions, roadmap items.
   Key contributors and their focus areas.

5. TESTING & QUALITY
   libcamera test infrastructure, CI/CD pipeline.
   Camera compliance testing (v4l2-compliance, libcamera-compliance).
   Platform availability for testing.

6. PRODUCTION READINESS
   Which platforms are production-ready with libcamera?
   Known limitations and workarounds.
   Comparison with vendor-specific camera stacks (NVIDIA Argus, Qualcomm CAMX).
""",
    },
}


# ── Utility functions ──────────────────────────────────────────────────────

def log(msg):
    print(f"  {msg}", flush=True)


def strip_html(text):
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', text, flags=re.S)
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.S)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


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


def call_ollama(system, user, temperature=0.5, max_tokens=4000, think=True):
    """Call Ollama LLM with chain-of-thought enabled."""
    prefix = "" if think else "/nothink\n"
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prefix + user},
        ],
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
            "num_ctx": 24576,
        },
    }).encode()

    req = urllib.request.Request(OLLAMA_CHAT, data=payload, headers={
        "Content-Type": "application/json",
    })

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            result = json.loads(resp.read())
            content = result.get("message", {}).get("content", "")
            elapsed = time.time() - t0
            tokens = result.get("eval_count", len(content.split()))
            tps = tokens / elapsed if elapsed > 0 else 0
            log(f"LLM: {elapsed:.0f}s, {tokens} tok ({tps:.1f} t/s)")
            return sanitize_llm_output(content)
    except Exception as e:
        log(f"Ollama call failed: {e}")
        return None


# ── Data gathering ─────────────────────────────────────────────────────────

def search_nvidia_devforum(keywords, category_ids, max_results=10):
    """Search NVIDIA Developer Forums for topics matching keywords."""
    results = []
    seen_ids = set()

    for query in keywords[:4]:
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
                log(f"  NVIDIA forum search error (cat={cat_id}): {e}")
            time.sleep(0.5)

        if len(results) >= max_results:
            break
        time.sleep(1)

    # Also fetch latest topics from key categories
    for cat_id in category_ids[:3]:
        url = f"{NVIDIA_FORUM_BASE}/c/{cat_id}.json?order=created"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            for topic in data.get("topic_list", {}).get("topics", [])[:8]:
                tid = topic.get("id")
                if tid in seen_ids:
                    continue
                seen_ids.add(tid)
                title_lower = topic.get("title", "").lower()
                camera_kws = ["camera", "csi", "isp", "v4l2", "argus", "mipi",
                              "sensor", "imx", "driver", "capture", "gstreamer",
                              "gmsl", "deserializ", "serializ", "fpd-link",
                              "libcamera", "nvargus", "video", "raw"]
                if any(kw in title_lower for kw in camera_kws):
                    slug = topic.get("slug", "")
                    results.append({
                        "title": topic.get("title", "")[:200],
                        "url": f"{NVIDIA_FORUM_BASE}/t/{slug}/{tid}",
                        "category_id": cat_id,
                        "views": topic.get("views", 0),
                        "replies": topic.get("posts_count", 1) - 1,
                        "date": topic.get("created_at", "")[:10],
                        "last_posted": topic.get("last_posted_at", "")[:10],
                    })
        except Exception as e:
            log(f"  NVIDIA forum latest error: {e}")
        time.sleep(0.5)

    results.sort(key=lambda r: r.get("last_posted", ""), reverse=True)
    return results[:max_results]


def fetch_nvidia_topic_content(topic_url, max_posts=3):
    """Fetch first few posts from a NVIDIA forum topic for deeper context."""
    # Discourse returns topic content as JSON
    json_url = topic_url + ".json"
    try:
        req = urllib.request.Request(json_url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        posts = data.get("post_stream", {}).get("posts", [])[:max_posts]
        texts = []
        for p in posts:
            cooked = p.get("cooked", "")
            text = strip_html(cooked)[:500]
            if text:
                texts.append(text)
        return "\n---\n".join(texts)[:2000]
    except Exception:
        return ""


def load_lore_threads(lore_dir, max_days=7):
    """Load recent mailing list thread analyses from digest data."""
    threads = []
    data_path = DATA_DIR / lore_dir
    if not data_path.exists():
        return threads

    # Find recent thread files
    today = datetime.now()
    for days_ago in range(max_days):
        d = today - timedelta(days=days_ago)
        fname = data_path / f"threads-{d.strftime('%Y%m%d')}.json"
        if fname.exists():
            try:
                data = json.load(open(fname))
                if isinstance(data, list):
                    for t in data:
                        threads.append({
                            "subject": t.get("subject", ""),
                            "score": t.get("score", 0),
                            "is_patch": t.get("is_patch", False),
                            "authors": t.get("authors", []),
                            "keywords": t.get("keywords", []),
                            "n_messages": t.get("n_messages", 0),
                            "analysis": t.get("llm_analysis", ""),
                            "date": d.strftime("%Y-%m-%d"),
                        })
            except Exception:
                pass

    # Sort by score (highest first)
    threads.sort(key=lambda t: t.get("score", 0), reverse=True)
    return threads[:20]


def load_repo_watch_data(repo_name, max_days=7):
    """Load recent repo-watch data (issues, MRs) for a repo."""
    items = []
    repo_dir = DATA_DIR / "repos" / repo_name
    if not repo_dir.exists():
        return items

    today = datetime.now()
    for days_ago in range(max_days):
        d = today - timedelta(days=days_ago)
        fname = repo_dir / f"watch-{d.strftime('%Y%m%d')}.json"
        if fname.exists():
            try:
                data = json.load(open(fname))
                if isinstance(data, list):
                    items.extend(data[:15])
                elif isinstance(data, dict):
                    items.extend(data.get("items", [])[:15])
            except Exception:
                pass

    return items[:20]


def search_ddg_news(query, max_results=5):
    """Search DuckDuckGo for recent news."""
    results = []
    try:
        q = urllib.parse.quote_plus(query)
        url = f"https://html.duckduckgo.com/html/?q={q}&t=h_&iar=news&ia=news"
        raw = fetch_url(url, timeout=15)
        if not raw:
            return results

        blocks = re.findall(r'class="result__body">(.*?)</div>', raw, re.S)
        if not blocks:
            blocks = re.findall(r'class="result__snippet">(.*?)</[as]', raw, re.S)

        for block in blocks[:max_results]:
            text = strip_html(block)[:300]
            if text:
                results.append(text)
    except Exception as e:
        log(f"  DDG search error: {e}")
    return results


# ── Analysis ───────────────────────────────────────────────────────────────

def gather_focus_data(focus_key):
    """Gather all data sources for a specific focus area."""
    fa = FOCUS_AREAS[focus_key]
    data = {
        "focus": focus_key,
        "label": fa["label"],
        "nvidia_forum": [],
        "nvidia_topic_excerpts": [],
        "lore_threads": [],
        "repo_items": [],
        "news": [],
    }

    # 1. NVIDIA Developer Forum
    log(f"\n── NVIDIA Developer Forum ──")
    data["nvidia_forum"] = search_nvidia_devforum(
        keywords=fa["nvidia_keywords"],
        category_ids=fa["nvidia_categories"],
        max_results=12,
    )
    log(f"  Found {len(data['nvidia_forum'])} topics")

    # Fetch content from top 3 most-replied topics for deeper context
    top_topics = sorted(data["nvidia_forum"], key=lambda t: t.get("replies", 0), reverse=True)[:3]
    for topic in top_topics:
        log(f"  Fetching: {topic['title'][:50]}...")
        content = fetch_nvidia_topic_content(topic["url"])
        if content:
            data["nvidia_topic_excerpts"].append({
                "title": topic["title"],
                "content": content[:1500],
            })
        time.sleep(1)

    # 2. Kernel mailing list threads
    log(f"\n── Mailing list: {fa['lore_feed']} ──")
    data["lore_threads"] = load_lore_threads(fa["lore_dir"])
    log(f"  Found {len(data['lore_threads'])} threads (7-day window)")

    # 3. Repo activity (libcamera if relevant)
    if focus_key == "libcamera":
        log(f"\n── libcamera repo activity ──")
        data["repo_items"] = load_repo_watch_data("libcamera")
        log(f"  Found {len(data['repo_items'])} items")

    # 4. Industry news
    log(f"\n── Industry news ──")
    for q in fa.get("ddg_queries", [])[:2]:
        log(f"  DDG: {q[:50]}...")
        data["news"].extend(search_ddg_news(q))
        time.sleep(2)
    log(f"  Found {len(data['news'])} articles")

    return data


def build_analysis_prompt(focus_key, data):
    """Build the LLM analysis prompt from gathered data."""
    fa = FOCUS_AREAS[focus_key]

    # Format NVIDIA forum topics
    nvidia_text = ""
    for t in data.get("nvidia_forum", [])[:10]:
        nvidia_text += f"  • [{t['date']}] {t['title'][:120]} ({t['replies']} replies, {t['views']} views)\n"

    # Format NVIDIA topic excerpts
    excerpts_text = ""
    for ex in data.get("nvidia_topic_excerpts", [])[:3]:
        excerpts_text += f"\n  === {ex['title'][:80]} ===\n  {ex['content'][:800]}\n"

    # Format mailing list threads
    lore_text = ""
    for t in data.get("lore_threads", [])[:12]:
        patch_tag = " [PATCH]" if t.get("is_patch") else ""
        authors = ", ".join(t.get("authors", [])[:2])
        lore_text += f"  • [{t['date']}]{patch_tag} {t['subject'][:100]} (score={t['score']}, {t['n_messages']} msgs, by {authors})\n"
        if t.get("analysis"):
            lore_text += f"    Analysis: {t['analysis'][:200]}\n"

    # Format repo items
    repo_text = ""
    for item in data.get("repo_items", [])[:8]:
        kind = item.get("type", item.get("kind", "?"))
        title = item.get("title", "")[:100]
        repo_text += f"  • [{kind}] {title}\n"

    # Format news
    news_text = ""
    for n in data.get("news", [])[:6]:
        news_text += f"  • {n[:200]}\n"

    # Profile context
    profile_ctx = ""
    if PROFILE_FILE.exists():
        try:
            pf = json.load(open(PROFILE_FILE))
            profile_ctx = f"\nAnalyst context: {pf.get('role', 'embedded Linux camera engineer')} specializing in V4L2, MIPI CSI-2, ISP, and camera driver development."
        except Exception:
            pass

    system = f"""You are a senior embedded Linux camera systems engineer and technical analyst.
You have deep expertise in MIPI CSI-2, V4L2, ISP pipelines, libcamera, and NVIDIA Jetson.
Write ONLY in English. Provide deep, actionable technical analysis.
Think about practical implications for a camera driver engineer working on automotive ADAS systems.
Date: {datetime.now().strftime('%Y-%m-%d')}"""

    user = f"""CSI CAMERA ENABLEMENT ANALYSIS: {fa['label']}
{profile_ctx}

NVIDIA Developer Forum topics ({len(data.get('nvidia_forum', []))} found):
{nvidia_text or '  (none found)'}

{f"NVIDIA Forum Discussion Excerpts:{excerpts_text}" if excerpts_text else ""}

Kernel mailing list threads ({fa['lore_feed']}, {len(data.get('lore_threads', []))} threads):
{lore_text or '  (no recent activity)'}

{f"libcamera repo activity:{chr(10)}{repo_text}" if repo_text else ""}

Industry news:
{news_text or '  (none)'}

{fa['analysis_prompt']}

Be SPECIFIC — name patches, drivers, sensor models, register details, DT properties.
Reference specific NVIDIA forum posts and kernel patches discussed above.
Target: 600-800 words. English only."""

    return system, user


# ── Per-focus think ────────────────────────────────────────────────────────

def think_one_focus(focus_key):
    """Deep chain-of-thought analysis on one CSI camera focus area."""
    if focus_key not in FOCUS_AREAS:
        log(f"Unknown focus: {focus_key}")
        log(f"Available: {', '.join(sorted(FOCUS_AREAS.keys()))}")
        sys.exit(1)

    fa = FOCUS_AREAS[focus_key]
    t_start = time.time()

    log(f"{'='*60}")
    log(f"CSI THINK — {fa['label']}")
    log(f"{'='*60}")

    CSI_DIR.mkdir(parents=True, exist_ok=True)

    # Check if already analyzed today
    today = datetime.now().strftime("%Y%m%d")
    out_file = CSI_DIR / f"{focus_key}-{today}.json"
    if out_file.exists():
        log(f"Already analyzed today: {out_file.name}")
        return

    # 1. Gather data
    data = gather_focus_data(focus_key)

    # 2. Deep LLM analysis
    log(f"\n── Deep analysis (chain-of-thought enabled) ──")
    system, user = build_analysis_prompt(focus_key, data)
    analysis = call_ollama(system, user, temperature=0.5, max_tokens=4000, think=True)

    if not analysis:
        log("LLM analysis failed")
        analysis = ""

    # Sanitize: strip think blocks and reasoning preamble
    analysis = re.sub(r'<think>.*?</think>', '', analysis, flags=re.S).strip()
    # Strip stray unicode and reasoning preamble before section headers
    if analysis and not analysis[0].isascii():
        analysis = analysis.lstrip()
    # Try to find the actual analysis start (numbered section or markdown heading)
    preamble_end = re.search(r'^(?:#{1,3}\s|\*\*1[\.\):]|1[\.\):])', analysis, re.M)
    if preamble_end and preamble_end.start() > 20:
        analysis = analysis[preamble_end.start():]

    # 3. Save
    elapsed = time.time() - t_start
    output = {
        "meta": {
            "focus": focus_key,
            "label": fa["label"],
            "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "date": today,
            "duration_s": round(elapsed),
        },
        "data_summary": {
            "nvidia_forum_count": len(data.get("nvidia_forum", [])),
            "nvidia_excerpts": len(data.get("nvidia_topic_excerpts", [])),
            "lore_threads_count": len(data.get("lore_threads", [])),
            "repo_items_count": len(data.get("repo_items", [])),
            "news_count": len(data.get("news", [])),
        },
        "analysis": analysis,
        "_raw_data": data,  # Cache for summary
    }

    with open(out_file, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    log(f"\nSaved: {out_file.name} ({out_file.stat().st_size:,} bytes) in {elapsed:.0f}s")


# ── Summary ────────────────────────────────────────────────────────────────

def think_summary():
    """Synthesize all focus area analyses into a CSI camera intelligence overview."""
    t_start = time.time()

    log(f"{'='*60}")
    log(f"CSI THINK SUMMARY — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    log(f"{'='*60}")

    CSI_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")

    analyses = []
    for focus_key in sorted(FOCUS_AREAS.keys()):
        think_file = CSI_DIR / f"{focus_key}-{today}.json"
        if think_file.exists():
            try:
                analyses.append(json.load(open(think_file)))
            except Exception as e:
                log(f"  {focus_key}: read error — {e}")

    log(f"\nFound {len(analyses)} focus analyses for today")

    if len(analyses) < 2:
        log("Not enough analyses to summarize (need >= 2). Skipping.")
        return

    # Build combined text
    summaries = []
    for a in analyses:
        label = a["meta"]["label"]
        analysis_text = a.get("analysis", "")[:600]
        summaries.append(f"### {label}\n{analysis_text}")

    combined = "\n\n---\n\n".join(summaries)

    system = """You are a senior camera systems architect synthesizing technical intelligence.
Write ONLY in English. The reader is an embedded Linux camera engineer working on
automotive ADAS systems (V4L2, MIPI CSI-2, ISP, GMSL, Jetson/DRIVE platforms)."""

    user = f"""Today's CSI camera enablement analyses across {len(analyses)} focus areas:

{combined}

Create a CSI CAMERA INTELLIGENCE BRIEF:

1. ECOSYSTEM HEALTH
   Overall state of Linux camera ecosystem — momentum, gaps, risks.
   Which areas are most active? Which are stagnant?

2. KEY TECHNICAL DEVELOPMENTS
   Top 5 most significant changes across all analyzed areas.
   New sensor drivers, ISP features, framework improvements.

3. NVIDIA PLATFORM STATUS
   Summary of NVIDIA Jetson/DRIVE camera ecosystem state.
   Key issues, improvements, and community activity.

4. UPSTREAM ACTIVITY
   Kernel mailing list activity summary — hot topics, active developers.
   Patch series to watch, RFC discussions.

5. PRACTICAL RECOMMENDATIONS
   For someone doing camera bring-up TODAY, what should they know?
   Tools, approaches, pitfalls to avoid.

6. WATCH LIST
   Items to monitor over the next 1-2 weeks.
   Merge windows, conferences, driver releases.

Target: 500-700 words. English only."""

    log("\n── Running summary analysis (chain-of-thought) ──")
    summary = call_ollama(system, user, temperature=0.4, max_tokens=4000, think=True)

    if not summary:
        log("Summary LLM call failed")
        return

    # Sanitize
    summary = re.sub(r'<think>.*?</think>', '', summary, flags=re.S).strip()
    preamble_end = re.search(r'^(?:#{1,3}\s|\*\*1[\.\):]|1[\.\):])', summary, re.M)
    if preamble_end and preamble_end.start() > 20:
        summary = summary[preamble_end.start():]

    elapsed = time.time() - t_start
    output = {
        "meta": {
            "type": "summary",
            "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "date": today,
            "focus_areas_analyzed": len(analyses),
            "duration_s": round(elapsed),
        },
        "summary": summary,
        "focus_areas": [a["meta"]["focus"] for a in analyses],
    }

    out_file = CSI_DIR / f"summary-{today}.json"
    with open(out_file, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    log(f"\nSaved: {out_file.name} ({out_file.stat().st_size:,} bytes)")

    latest = CSI_DIR / "latest-summary.json"
    latest.unlink(missing_ok=True)
    latest.symlink_to(out_file.name)

    # Cleanup (keep ~30 days)
    all_files = sorted(CSI_DIR.glob("*.json"))
    if len(all_files) > 200:
        for f in all_files[:-200]:
            if f.name.startswith("latest"):
                continue
            f.unlink()
            log(f"  Pruned: {f.name}")

    log(f"\nSummary complete in {elapsed:.0f}s")


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="CSI camera enablement deep analysis")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--focus', '-f', help="Focus area to analyze")
    group.add_argument('--summary', '-s', action='store_true', help="Generate cross-topic synthesis")
    group.add_argument('--list', '-l', action='store_true', help="List available focus areas")
    args = parser.parse_args()

    if args.list:
        print("\nCSI Think Focus Areas:")
        for key, fa in sorted(FOCUS_AREAS.items()):
            print(f"  {key:18s}  {fa['label']}")
        return

    if args.summary:
        think_summary()
    else:
        think_one_focus(args.focus)


if __name__ == "__main__":
    main()
