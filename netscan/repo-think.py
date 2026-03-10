#!/usr/bin/env python3
"""repo-think.py — Deep per-repo analysis with chain-of-thought LLM reasoning.

Analyzes each tracked repository's recent issues, MRs, and patches to provide:
- Technical trend analysis for the project
- Contribution opportunity identification
- Cross-project impact assessment
- Similar project discovery (via DDG search + LLM reasoning)

Usage:
    python3 repo-think.py --repo gstreamer                    # full repo analysis
    python3 repo-think.py --repo gstreamer --focus v4l2-camera # sub-topic deep dive
    python3 repo-think.py --repo gstreamer --list-focus        # list focus areas
    python3 repo-think.py --summary                            # cross-repo synthesis
    python3 repo-think.py --list                               # list available repos
"""

import os, sys, json, time, re
import urllib.request, urllib.error, urllib.parse
from datetime import datetime
from llm_sanitize import sanitize_llm_output

# ─── Config ───

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = "/opt/netscan/data"
REPO_FEEDS = os.path.join(SCRIPT_DIR, "repo-feeds.json")
PROFILE_JSON = os.path.join(SCRIPT_DIR, "profile.json")
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL = "qwen3:14b"

# ─── Per-repo sub-topic focus areas ───
# Each focus area gets its own dedicated LLM thinking session for deeper analysis.
# Keys must match repo IDs in repo-feeds.json.
REPO_FOCUS_AREAS = {
    "gstreamer": {
        "v4l2-camera": {
            "name": "V4L2 & Camera Plugins",
            "keywords": ["v4l2", "v4l2src", "v4l2sink", "v4l2dec", "v4l2enc",
                         "camera", "libcamera", "libcamerasrc", "uvc"],
            "prompt_focus": "V4L2 source/sink/codec plugins, camera integration, "
                "libcamerasrc element, UVC devices, and video capture/output pipeline elements",
            "ddg_query": "GStreamer V4L2 camera plugin libcamerasrc video capture Linux",
        },
        "va-vulkan": {
            "name": "VA-API & Vulkan Video",
            "keywords": ["vaapi", "va", "vah264", "vah265", "vulkan",
                         "vulkan video", "hwaccel", "gpu"],
            "prompt_focus": "VA-API encoder/decoder elements (vah264dec, vah265enc, etc.), "
                "Vulkan Video decode/encode, hardware acceleration architecture, GPU offload",
            "ddg_query": "GStreamer VA-API Vulkan Video hardware acceleration decode encode",
        },
        "dmabuf-drm": {
            "name": "DMA-BUF & Memory Management",
            "keywords": ["dmabuf", "dma-buf", "drm", "prime", "mmap",
                         "allocator", "memory", "zero-copy"],
            "prompt_focus": "DMA-BUF buffer sharing, DRM prime, zero-copy pipelines, "
                "GstAllocator subsystem, memory management between hardware accelerators",
            "ddg_query": "GStreamer DMA-BUF zero-copy buffer sharing DRM prime allocator",
        },
    },
    "libcamera": {
        "isp-pipeline": {
            "name": "ISP Pipeline Handlers",
            "keywords": ["rkisp", "mali-c55", "pisp", "camss", "imx8",
                         "pipeline", "ipa", "3a", "tuning"],
            "prompt_focus": "ISP-specific pipeline handler implementations (rkisp1, mali-c55, "
                "PiSP, camss), IPA module development, 3A algorithms, tuning data management",
            "ddg_query": "libcamera ISP pipeline handler rkisp1 mali-c55 IPA algorithm",
        },
        "sensor-api": {
            "name": "Sensors & Application API",
            "keywords": ["sensor", "v4l2", "subdev", "media-ctl", "format",
                         "control", "property", "gstreamer", "android", "application"],
            "prompt_focus": "Camera sensor driver integration, V4L2 subdev interaction, "
                "the libcamera public API for applications, GStreamer/PipeWire/Android integration",
            "ddg_query": "libcamera sensor API GStreamer PipeWire camera application integration",
        },
    },
    "v4l-utils": {
        "ctl-compliance": {
            "name": "v4l2-ctl & Compliance Testing",
            "keywords": ["v4l2-ctl", "v4l2-compliance", "v4l2-info", "media-ctl",
                         "test", "compliance", "ioctl"],
            "prompt_focus": "v4l2-ctl userspace tool changes, v4l2-compliance test suite "
                "updates (new tests, stricter checks, regression tests), media-ctl topology "
                "management, v4l2-info, and testing infrastructure for V4L2 drivers",
            "ddg_query": "v4l2-ctl v4l2-compliance testing V4L2 driver validation media-ctl",
        },
        "libv4l-convert": {
            "name": "libv4l & Format Conversion",
            "keywords": ["libv4l", "v4l2convert", "fourcc", "format",
                         "streaming", "dmabuf", "mmap", "userptr"],
            "prompt_focus": "libv4l2 userspace library changes, v4l2convert format conversion, "
                "pixel format support, streaming modes (MMAP/DMABUF/USERPTR), buffer management",
            "ddg_query": "libv4l2 v4l2convert pixel format conversion DMABUF streaming Linux",
        },
    },
    "ffmpeg": {
        "hwaccel-gpu": {
            "name": "Hardware Acceleration",
            "keywords": ["vaapi", "vulkan", "vulkan video", "amf", "hwaccel",
                         "hwcontext", "hwframe", "spir-v", "spirv", "gpu"],
            "prompt_focus": "VA-API codec support, Vulkan Video decode/encode, AMF (AMD Media "
                "Framework), hwaccel/hwcontext framework, SPIR-V shaders, GPU-accelerated filters",
            "ddg_query": "FFmpeg VA-API Vulkan Video AMF hardware acceleration GPU decode encode",
        },
        "v4l2-devices": {
            "name": "V4L2 & Device Input/Output",
            "keywords": ["v4l2", "v4l2_m2m", "libcamera", "libavdevice",
                         "camera", "drm", "dmabuf", "kms"],
            "prompt_focus": "V4L2 M2M (stateless/stateful codec) support, libavdevice v4l2 input, "
                "libcamera integration, DRM/KMS output, DMA-BUF handling in FFmpeg",
            "ddg_query": "FFmpeg V4L2 M2M stateless codec libavdevice camera DRM output",
        },
    },
    "linuxtv": {
        "camera-isp": {
            "name": "Camera & ISP Drivers",
            "keywords": ["camera", "sensor", "isp", "rkisp1", "rkisp", "camss",
                         "mali-c55", "pisp", "imx", "omnivision", "sony", "mipi"],
            "prompt_focus": "Camera sensor drivers (IMX, OmniVision, Sony), ISP pipeline drivers "
                "(rkisp1, camss, mali-c55, PiSP), MIPI CSI-2 PHY, sensor tuning in the kernel",
            "ddg_query": "Linux kernel camera sensor ISP driver rkisp1 camss MIPI CSI-2",
        },
        "codec-m2m": {
            "name": "V4L2 M2M Codec Drivers",
            "keywords": ["v4l2-mem2mem", "hantro", "rkmpp", "cedrus", "wave5",
                         "stateless", "stateful", "codec", "decode", "encode"],
            "prompt_focus": "V4L2 memory-to-memory codec drivers (Hantro, RockChip VPU, Cedrus, "
                "Wave5), stateless vs stateful codec API, hardware video codec development",
            "ddg_query": "Linux V4L2 M2M codec driver Hantro Cedrus Wave5 stateless",
        },
        "media-framework": {
            "name": "V4L2 Core & Media Controller",
            "keywords": ["v4l2", "media-ctl", "subdev", "v4l2-ctl", "v4l2-compliance",
                         "format", "fourcc", "dma-buf", "framework"],
            "prompt_focus": "V4L2 core framework changes, media controller subsystem, subdev API "
                "evolution, format/fourcc additions, DMA-BUF import/export, compliance tests",
            "ddg_query": "Linux V4L2 media controller subdev framework core changes",
        },
        "gmsl-serdes": {
            "name": "GMSL & SerDes Camera Links",
            "keywords": ["gmsl", "max96712", "max9295", "max96714", "max96717",
                         "max9296", "max96724", "deserializer", "serializer", "serdes",
                         "fpd-link", "ds90ub", "coax", "fakra"],
            "prompt_focus": "GMSL (Gigabit Multimedia Serial Link) camera serializer/deserializer "
                "drivers in the kernel — MAX96712, MAX9295, MAX96714, MAX96717 line of Analog Devices "
                "(formerly Maxim) chips. Also TI FPD-Link III/IV (DS90UBxxx). Focus on V4L2 subdev "
                "drivers, I2C address translation, virtual channel routing, MIPI CSI-2 output config, "
                "and device tree bindings for multi-camera automotive setups",
            "ddg_query": "Linux kernel GMSL MAX96712 MAX9295 serializer deserializer V4L2 driver",
        },
    },
    "v4l2loopback": {
        "features": {
            "name": "Features & Integration",
            "keywords": ["loopback", "virtual", "device", "format", "v4l2",
                         "obs", "ffmpeg", "gstreamer", "camera"],
            "prompt_focus": "Virtual V4L2 device features, format support, integration with "
                "OBS/FFmpeg/GStreamer, kernel compatibility, and use cases",
            "ddg_query": "v4l2loopback virtual camera device Linux features integration",
        },
    },
    "pipewire": {
        "camera-video": {
            "name": "Camera & Video Routing",
            "keywords": ["camera", "v4l2", "libcamera", "video", "spa",
                         "portal", "pipewire-v4l2", "filter"],
            "prompt_focus": "PipeWire camera support (PipeWire-V4L2 compat layer, libcamera "
                "integration), video routing/filtering, camera portal for sandboxed apps",
            "ddg_query": "PipeWire camera V4L2 libcamera video routing portal Linux",
        },
    },
    "adi-linux": {
        "gmsl-drivers": {
            "name": "GMSL SerDes Drivers",
            "keywords": ["gmsl", "max96712", "max9295", "max96714", "max96717",
                         "max9296", "max96724", "max96793", "deserializer", "serializer",
                         "serdes", "i2c", "virtual channel"],
            "prompt_focus": "Analog Devices (formerly Maxim) GMSL serializer/deserializer Linux "
                "drivers in the ADI kernel tree — MAX96712/MAX9296 deserializers, MAX9295/MAX96717 "
                "serializers. I2C address translation, MIPI CSI-2 virtual channel management, "
                "multi-camera topology, remote GPIO/I2C tunneling, coax link management",
            "ddg_query": "Analog Devices GMSL Linux driver MAX96712 MAX9295 camera serializer",
        },
        "camera-sensor": {
            "name": "Camera Sensor Integration",
            "keywords": ["camera", "sensor", "imx", "ov", "ar0", "isp",
                         "mipi", "csi", "v4l2", "subdev", "media"],
            "prompt_focus": "Camera sensor drivers in the ADI kernel fork — sensor register "
                "configuration, V4L2 subdev operations, MIPI CSI-2 lane config, device tree "
                "bindings for automotive camera modules behind GMSL links",
            "ddg_query": "ADI Linux camera sensor driver GMSL MIPI CSI-2 automotive",
        },
    },
    "adi-gmsl": {
        "standalone-driver": {
            "name": "Standalone GMSL Driver",
            "keywords": ["gmsl", "max96712", "max9295", "max96714", "max96717",
                         "max9296", "max96724", "driver", "i2c", "dt-binding"],
            "prompt_focus": "Standalone GMSL driver package from Analog Devices — out-of-tree "
                "module build, DKMS packaging, device tree overlay examples, platform compatibility, "
                "API usage for multi-camera setups with MAX96712 hub deserializer",
            "ddg_query": "analogdevicesinc gmsl standalone driver module Linux",
        },
    },
}

# Load repo feeds
with open(REPO_FEEDS) as f:
    ALL_REPOS = {k: v for k, v in json.load(f).items() if isinstance(v, dict)}

# Load profile
PROFILE = {}
if os.path.exists(PROFILE_JSON):
    with open(PROFILE_JSON) as f:
        PROFILE = json.load(f)


def call_ollama(prompt, timeout=900):
    """Call Ollama with full chain-of-thought (no /nothink)."""
    payload = json.dumps({
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"num_ctx": 24576, "temperature": 0.7}
    })
    req = urllib.request.Request(
        OLLAMA_URL, data=payload.encode(),
        headers={"Content-Type": "application/json"}
    )
    t0 = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = json.loads(resp.read())
        content = data.get("response", "")
        # Strip think blocks
        if "</think>" in content:
            content = content.split("</think>", 1)[1].strip()
        content = sanitize_llm_output(content)
        elapsed = time.time() - t0
        tokens = data.get("eval_count", 0)
        print(f"  LLM: {elapsed:.0f}s, {tokens} tokens, {len(content)} chars")
        return content, elapsed, tokens
    except Exception as ex:
        print(f"  LLM error: {ex}")
        return None, time.time() - t0, 0


def fetch_ddg_search(query, max_results=5):
    """Search DuckDuckGo for related/similar projects."""
    try:
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) netscan-bc250/1.0"
        })
        resp = urllib.request.urlopen(req, timeout=15)
        html = resp.read().decode("utf-8", errors="replace")
        results = []
        # Extract titles and snippets from DDG HTML results
        for m in re.finditer(
            r'class="result__a"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</span>',
            html, re.S
        ):
            title = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            snippet = re.sub(r'<[^>]+>', '', m.group(2)).strip()
            if title:
                results.append(f"{title}: {snippet}")
            if len(results) >= max_results:
                break
        return results
    except Exception as ex:
        print(f"  DDG search failed: {ex}")
        return []


def fetch_hn_threads(query, limit=3):
    """Search HN for recent discussions about a project."""
    try:
        url = f"https://hn.algolia.com/api/v1/search_by_date?query={urllib.parse.quote(query)}&tags=story&hitsPerPage={limit}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "netscan-bc250/1.0"
        })
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        results = []
        for hit in data.get("hits", []):
            title = hit.get("title", "")
            points = hit.get("points", 0)
            comments = hit.get("num_comments", 0)
            date = hit.get("created_at", "")[:10]
            if title:
                results.append(f"{title} ({points} pts, {comments} comments, {date})")
        return results
    except Exception:
        return []


def filter_items_by_focus(items, focus_cfg):
    """Filter items to only those matching the focus area keywords."""
    if not focus_cfg:
        return items
    focus_kws = [kw.lower() for kw in focus_cfg.get("keywords", [])]
    if not focus_kws:
        return items
    filtered = []
    for item in items:
        text = f"{item.get('title', '')} {item.get('body_preview', '')} {' '.join(item.get('labels', []))} {' '.join(item.get('keywords', []))}".lower()
        if any(kw in text for kw in focus_kws):
            filtered.append(item)
    return filtered


def get_repo_prompt(repo_id, repo_cfg, items, similar_projects, hn_threads, profile, focus_cfg=None):
    """Build the per-repo analysis prompt. If focus_cfg is set, narrows to sub-topic."""
    repo_name = repo_cfg.get("name", repo_id)
    repo_type = repo_cfg.get("type", "unknown")
    web_url = repo_cfg.get("web_url", "")
    relevance_keys = list(repo_cfg.get("relevance", {}).keys())

    # Focus area context
    focus_name = focus_cfg["name"] if focus_cfg else None
    focus_desc = focus_cfg["prompt_focus"] if focus_cfg else None

    # User context
    user_role = profile.get("role", "Embedded Linux / multimedia developer")
    high_kw = profile.get("interest_keywords", {}).get("high", [])
    user_focus = ", ".join(high_kw[:12])

    # Format items
    items_text = ""
    for i, item in enumerate(items[:20], 1):
        itype = item.get("type", "issue")
        title = item.get("title", "?")
        score = item.get("score", 0)
        author = item.get("author", "?")
        keywords = ", ".join(item.get("keywords", []))
        body = item.get("body_preview", "")[:300]
        labels = ", ".join(item.get("labels", []))
        state = item.get("state", "?")
        comments = item.get("comments", 0)
        is_new = "NEW" if item.get("is_new") else ""

        items_text += f"\n{i}. [{itype}] (score:{score}) {title} {is_new}\n"
        items_text += f"   Author: {author} | State: {state} | Comments: {comments}\n"
        if keywords:
            items_text += f"   Keywords: {keywords}\n"
        if labels:
            items_text += f"   Labels: {labels}\n"
        if body:
            items_text += f"   Preview: {body[:200]}\n"

    # Similar projects from DDG
    similar_text = ""
    if similar_projects:
        similar_text = "\nSIMILAR/RELATED PROJECTS FOUND (via web search):\n"
        for s in similar_projects:
            similar_text += f"  - {s}\n"

    # HN discussions
    hn_text = ""
    if hn_threads:
        hn_text = "\nRECENT HACKER NEWS DISCUSSIONS:\n"
        for h in hn_threads:
            hn_text += f"  - {h}\n"

    # Build different prompts for focused vs full analysis
    if focus_cfg:
        return f"""You are a senior open-source software engineer performing a DEEP FOCUSED analysis of the {focus_name} subsystem within the {repo_name} project ({repo_type} repository at {web_url}).

FOCUS AREA: {focus_desc}

The user is a {user_role} focusing on: {user_focus}

FOCUS KEYWORDS: {', '.join(focus_cfg.get('keywords', []))}
PROJECT RELEVANCE KEYWORDS: {', '.join(relevance_keys[:25])}

ITEMS MATCHING THIS FOCUS AREA ({len(items)} items filtered by topic relevance):
{items_text if items_text.strip() else "(No items matching this focus area this period)"}
{similar_text}{hn_text}
Provide an IN-DEPTH technical analysis focused specifically on {focus_name} in English. Go deeper than a general overview — this is a specialist analysis. Structure your response:

## {repo_name} — {focus_name} Deep Dive

### Current State of {focus_name}
What is the current state of this subsystem? Recent architectural decisions, ongoing refactors, API changes, or design debates. Be very specific about code/API details.

### Active Development Threads
For each relevant item in detail:
- Technical summary of the change/issue
- Code-level implications (what functions/modules are affected)
- Whether it introduces breaking changes or new capabilities
- Review status and likelihood of merging

### Technical Deep Dive
Pick the 2-3 most significant items and analyze them at code level. What are the implementation trade-offs? Alternative approaches? Testing gaps? Performance implications?

### Impact on User's Work
How do these specific {focus_name} changes affect someone working on {user_focus}? Be concrete: which APIs change, which workflows break, which new capabilities unlock.

### Contribution Opportunities
Specific, detailed contribution opportunities in this focus area:
- Exact issue/MR numbers to engage with
- What skills are needed
- Estimated effort (trivial/small/medium/large)
- Potential impact of the contribution

### Emerging Patterns & Predictions
Based on the activity pattern, what direction is {focus_name} heading? What to prepare for in the next 3-6 months?

Be very technical, cite specific items, and provide actionable intelligence. Write in English only."""

    return f"""You are a senior open-source software engineer analyzing the {repo_name} project ({repo_type} repository at {web_url}).

The user is a {user_role} focusing on: {user_focus}

PROJECT RELEVANCE KEYWORDS: {', '.join(relevance_keys[:25])}

RECENT ITEMS ({len(items)} interesting items, shown by relevance score):
{items_text if items_text.strip() else "(No interesting items this period — project may be quiet)"}
{similar_text}{hn_text}
Provide a deep technical analysis in English. Structure your response:

## {repo_name} — Intelligence Brief

### Hot Topics & Trends
The most significant technical discussions, architectural changes, or feature developments happening now. What direction is this project heading?

### Items Relevant to Your Work
Which specific issues/MRs/patches are most relevant to the user's expertise ({user_focus})? For each relevant item:
- Why it matters to them specifically
- Whether they could contribute or should track it
- Technical implications

### Contributor Patterns
Notable contributors this period. New significant contributors? Changes in maintainer activity?

### Cross-Project Implications
How do these changes affect related projects? (e.g., libcamera↔GStreamer, FFmpeg↔V4L2, LinuxTV kernel patches↔userspace tools)

### Contribution Opportunities
Concrete opportunities where the user could contribute based on their expertise. Prioritize by impact and feasibility. Include specific item references.

### Similar Projects Worth Watching
Based on the web search results and your knowledge, suggest 2-3 related or competing projects/repos not currently tracked. For each: name, URL if known, why it's relevant, activity level.

### Action Items
Top 3 specific actions the user should take right now based on this analysis.

Be specific, cite issue/MR numbers, and provide actionable intelligence. Write in English only."""


def get_summary_prompt(repo_analyses):
    """Build the cross-repo summary prompt."""
    combined = ""
    for repo_id, analysis in repo_analyses.items():
        combined += f"\n\n=== {repo_id.upper()} ===\n{analysis[:3000]}\n"

    return f"""You are a senior open-source intelligence analyst synthesizing insights across multiple Linux multimedia/camera stack projects being monitored.

Here are today's deep analyses for each tracked repository:
{combined}

Provide a cross-project synthesis in English:

## Cross-Project Intelligence Summary

### Most Important Developments Today
The 3-5 most significant things happening across ALL tracked projects. What's the big picture?

### Cross-Project Connections
Where are changes in one project going to impact another? Identify dependency chains and coordination needs. For example: kernel driver changes → libcamera adaptation → GStreamer plugin updates → FFmpeg compatibility.

### Industry Trend Signals
What do these combined activities tell us about the broader Linux multimedia/camera ecosystem direction?

### Unified Priority Actions
Top 5 actions ranked by importance across all projects. Be specific about which project and which item.

### Emerging Opportunities
New areas, projects, or niches opening up based on the combined intelligence.

### Project Health Scorecard
Brief status for each tracked project: activity level (high/medium/low), community engagement, momentum direction (up/stable/down).

Be concise, data-driven, and focus on actionable cross-project intelligence. Write in English only."""


def think_one_repo(repo_id, focus=None, mode="full"):
    """Run deep analysis for a single repo, optionally focused on a sub-topic.
    mode: 'full' (default), 'scrape-only', or 'analyze-only'.
    """
    if repo_id not in ALL_REPOS:
        print(f"FATAL: unknown repo '{repo_id}'")
        print(f"Available: {', '.join(ALL_REPOS.keys())}")
        sys.exit(1)

    # Validate focus area if specified
    focus_cfg = None
    if focus:
        repo_focuses = REPO_FOCUS_AREAS.get(repo_id, {})
        if focus not in repo_focuses:
            print(f"FATAL: unknown focus '{focus}' for repo '{repo_id}'")
            print(f"Available: {', '.join(repo_focuses.keys())}")
            sys.exit(1)
        focus_cfg = repo_focuses[focus]

    repo_cfg = ALL_REPOS[repo_id]
    repo_name = repo_cfg.get("name", repo_id)
    repo_dir = os.path.join(DATA_DIR, repo_cfg.get("data_dir", f"repos/{repo_id}"))
    think_dir = os.path.join(repo_dir, "think")
    os.makedirs(think_dir, exist_ok=True)

    today = datetime.now().strftime("%Y%m%d")
    suffix = f"-{focus}" if focus else ""
    out_file = os.path.join(think_dir, f"{repo_id}{suffix}-{today}.json")
    raw_file = os.path.join(think_dir, f"raw-{repo_id}{suffix}-{today}.json")

    # Check if already done today (skip for scrape-only)
    if mode != "scrape-only" and os.path.exists(out_file):
        with open(out_file) as f:
            existing = json.load(f)
        if existing.get("analysis"):
            label = f"{repo_name}/{focus_cfg['name']}" if focus_cfg else repo_name
            print(f"  Already analyzed {label} today ({len(existing['analysis'])} chars)")
            return existing["analysis"]

    # ── Scrape phase ──
    scrape_timestamp = None
    items = []
    total_items = 0
    checked = "?"
    similar = []
    hn = []

    if mode == "analyze-only":
        # Load from raw scrape data
        if not os.path.exists(raw_file):
            print(f"  ERROR: Raw scrape data not found: {raw_file}")
            print("  Run with --scrape-only first.")
            return None
        with open(raw_file) as f:
            raw = json.load(f)
        rd = raw.get("data", {})
        items = rd.get("items", [])
        total_items = rd.get("total_items", 0)
        checked = rd.get("checked", "?")
        similar = rd.get("similar_projects", [])
        hn = rd.get("hn_threads", [])
        scrape_timestamp = raw.get("scrape_timestamp", "")
        label = f"{repo_name}/{focus_cfg['name']}" if focus_cfg else repo_name
        print(f"  [ANALYZE-ONLY] Loaded scrape data for {label} (scraped {scrape_timestamp})")
    else:
        # Load latest repo data
        latest_path = os.path.join(repo_dir, "latest.json")
        if not os.path.exists(latest_path):
            print(f"  No data for {repo_name} — run repo-watch first")
            return None

        with open(latest_path) as f:
            data = json.load(f)

        items = data.get("interesting", [])
        total_items = data.get("total_items", 0)
        checked = data.get("checked", "?")

        # Filter items by focus keywords if focused
        if focus_cfg:
            items = filter_items_by_focus(items, focus_cfg)
            print(f"  Analyzing {repo_name}/{focus_cfg['name']}: {len(items)} matching items of {total_items} total (last scan: {checked})")
        else:
            print(f"  Analyzing {repo_name}: {len(items)} interesting of {total_items} total (last scan: {checked})")

        # Fetch similar projects via DDG
        if focus_cfg and focus_cfg.get("ddg_query"):
            query = focus_cfg["ddg_query"]
        else:
            search_queries = {
                "gstreamer": "GStreamer alternatives multimedia framework Linux open source",
                "libcamera": "libcamera alternatives Linux camera framework ISP pipeline",
                "v4l-utils": "v4l-utils alternatives Linux video4linux camera tools",
                "ffmpeg": "FFmpeg alternatives video processing framework open source 2024 2025",
                "linuxtv": "Linux media subsystem camera ISP driver development",
            }
            query = search_queries.get(repo_id, f"{repo_name} alternatives similar projects open source")
        print(f"  Searching for similar projects...")
        similar = fetch_ddg_search(query)

        # Fetch HN discussions
        hn_query = f"{repo_name} {focus_cfg['name']}" if focus_cfg else repo_name
        print(f"  Checking Hacker News...")
        hn = fetch_hn_threads(hn_query)

        scrape_timestamp = datetime.now().isoformat(timespec="seconds")

    # ── Scrape-only: save raw data and exit ──
    if mode == "scrape-only":
        raw_data = {
            "scrape_timestamp": scrape_timestamp,
            "scrape_version": 1,
            "repo_id": repo_id,
            "focus": focus,
            "data": {
                "items": items,
                "total_items": total_items,
                "checked": checked,
                "similar_projects": similar,
                "hn_threads": hn,
            },
            "scrape_errors": [],
        }
        _tmp = raw_file + ".tmp"
        with open(_tmp, "w") as f:
            json.dump(raw_data, f, indent=2, ensure_ascii=False, default=str)
        os.replace(_tmp, raw_file)
        label = f"{repo_name}/{focus_cfg['name']}" if focus_cfg else repo_name
        print(f"  [SCRAPE-ONLY] Saved: {raw_file}")
        print(f"  {len(items)} items, {len(similar)} DDG results, {len(hn)} HN threads")
        return None

    # ── Analyze phase: LLM ──
    prompt = get_repo_prompt(repo_id, repo_cfg, items, similar, hn, PROFILE, focus_cfg)
    analysis, elapsed, tokens = call_ollama(prompt)

    if not analysis:
        label = f"{repo_name}/{focus_cfg['name']}" if focus_cfg else repo_name
        print(f"  Failed to analyze {label}")
        return None

    result = {
        "repo_id": repo_id,
        "repo_name": repo_name,
        "focus": focus,
        "focus_name": focus_cfg["name"] if focus_cfg else None,
        "date": today,
        "scrape_timestamp": scrape_timestamp,
        "analyze_timestamp": datetime.now().isoformat(timespec="seconds"),
        "timestamp": datetime.now().isoformat(),
        "items_analyzed": len(items),
        "total_items": total_items,
        "similar_projects_found": len(similar),
        "hn_threads_found": len(hn),
        "analysis": analysis,
        "elapsed_seconds": round(elapsed, 1),
        "tokens": tokens,
    }

    with open(out_file, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"  Saved: {out_file}")

    # Update latest symlink
    link_name = f"latest-{repo_id}{suffix}.json"
    latest_think = os.path.join(think_dir, link_name)
    try:
        if os.path.exists(latest_think):
            os.remove(latest_think)
        os.symlink(out_file, latest_think)
    except Exception:
        pass

    return analysis


def think_summary():
    """Generate cross-repo summary from today's analyses (including focused ones)."""
    today = datetime.now().strftime("%Y%m%d")
    summary_dir = os.path.join(DATA_DIR, "repos", "think")
    os.makedirs(summary_dir, exist_ok=True)

    out_file = os.path.join(summary_dir, f"summary-{today}.json")

    # Load today's per-repo analyses (full + focused)
    analyses = {}
    for repo_id, repo_cfg in ALL_REPOS.items():
        repo_dir = os.path.join(DATA_DIR, repo_cfg.get("data_dir", f"repos/{repo_id}"))

        # Full analysis
        think_path = os.path.join(repo_dir, "think", f"{repo_id}-{today}.json")
        if os.path.exists(think_path):
            with open(think_path) as f:
                data = json.load(f)
            if data.get("analysis"):
                analyses[repo_id] = data["analysis"]

        # Focused analyses
        focuses = REPO_FOCUS_AREAS.get(repo_id, {})
        for focus_id in focuses:
            focus_path = os.path.join(repo_dir, "think", f"{repo_id}-{focus_id}-{today}.json")
            if os.path.exists(focus_path):
                with open(focus_path) as f:
                    data = json.load(f)
                if data.get("analysis"):
                    key = f"{repo_id}/{focus_id}"
                    analyses[key] = data["analysis"]

    if not analyses:
        print(f"  No repo analyses found for today")
        return None

    print(f"  Summarizing {len(analyses)} repo analyses: {', '.join(analyses.keys())}")

    prompt = get_summary_prompt(analyses)
    analysis, elapsed, tokens = call_ollama(prompt, timeout=1200)

    if not analysis:
        print(f"  Failed to generate summary")
        return None

    result = {
        "date": today,
        "timestamp": datetime.now().isoformat(),
        "repos_analyzed": list(analyses.keys()),
        "analysis": analysis,
        "elapsed_seconds": round(elapsed, 1),
        "tokens": tokens,
    }

    with open(out_file, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"  Saved: {out_file}")

    # Latest symlink
    latest = os.path.join(summary_dir, "latest-summary.json")
    try:
        if os.path.exists(latest):
            os.remove(latest)
        os.symlink(out_file, latest)
    except Exception:
        pass

    return analysis


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Deep per-repo LLM analysis")
    parser.add_argument("--repo", help="Repo ID to analyze")
    parser.add_argument("--focus", help="Sub-topic focus area (e.g. v4l2-camera, hwaccel-gpu)")
    parser.add_argument("--summary", action="store_true", help="Cross-repo synthesis")
    parser.add_argument("--list", action="store_true", help="List available repos")
    parser.add_argument("--list-focus", action="store_true", help="List focus areas for repo")
    parser.add_argument("--scrape-only", action="store_true", help="Gather data only (DDG, HN) — no LLM")
    parser.add_argument("--analyze-only", action="store_true", help="LLM analysis only — load previous scrape")
    args = parser.parse_args()

    if args.list:
        for rid, rcfg in ALL_REPOS.items():
            name = rcfg.get("name", rid)
            rtype = rcfg.get("type", "?")
            n_items = rcfg.get("relevance", {})
            focuses = REPO_FOCUS_AREAS.get(rid, {})
            print(f"  {rid}: {name} ({rtype}, {len(n_items)} kw, {len(focuses)} focus areas)")
            for fid, fcfg in focuses.items():
                print(f"    → {fid}: {fcfg['name']}")
        return

    if args.list_focus:
        rid = args.repo
        if not rid:
            # List all
            for rid, focuses in REPO_FOCUS_AREAS.items():
                name = ALL_REPOS.get(rid, {}).get("name", rid)
                print(f"  {rid} ({name}):")
                for fid, fcfg in focuses.items():
                    print(f"    {fid}: {fcfg['name']} — {fcfg['prompt_focus'][:80]}...")
        else:
            focuses = REPO_FOCUS_AREAS.get(rid, {})
            if not focuses:
                print(f"  No focus areas defined for '{rid}'")
            else:
                for fid, fcfg in focuses.items():
                    print(f"  {fid}: {fcfg['name']}")
                    print(f"    Keywords: {', '.join(fcfg['keywords'])}")
                    print(f"    Focus: {fcfg['prompt_focus']}")
        return

    if args.summary:
        if args.scrape_only:
            print("  --scrape-only not applicable to --summary (no network calls)")
            return
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] repo-think: cross-repo summary")
        think_summary()
        return

    if args.repo:
        mode = "scrape-only" if args.scrape_only else "analyze-only" if args.analyze_only else "full"
        label = f"{args.repo}/{args.focus}" if args.focus else args.repo
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] repo-think: {label} [{mode}]")
        think_one_repo(args.repo, focus=args.focus, mode=mode)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
