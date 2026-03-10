#!/usr/bin/env python3
"""csi-sensor-watch.py — CSI camera sensor driver issue monitor

Tracks CSI camera sensors across Jetson Orin Nano, RPi 2/4/5 platforms.
Finds driver issues, missing features, documentation & purchase availability.
Auto-discovers new sensors and extends its own watchlist.

Modes:
  (default)    Full scan — check sources, find issues, LLM analysis
  --discover   Discovery-only — search for new sensor models
  --improve    Self-improvement — LLM-guided watchlist expansion

Data:
  /opt/netscan/data/csi-sensors/latest-csi.json   Current state
  /opt/netscan/data/csi-sensors/history/           Snapshots
  /opt/netscan/sensor-watchlist.json               Growing sensor watchlist
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
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from pathlib import Path
from llm_sanitize import sanitize_llm_output

# ── Config ─────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = Path("/opt/netscan/data/csi-sensors")
HISTORY_DIR = DATA_DIR / "history"
WATCHLIST_PATH = os.path.join(SCRIPT_DIR, "sensor-watchlist.json")
LATEST_PATH = DATA_DIR / "latest-csi.json"
RAW_CSI_FILE = DATA_DIR / "raw-csi.json"
PROFILE_PATH = os.path.join(SCRIPT_DIR, "profile.json")

OLLAMA_URL = "http://localhost:11434"
OLLAMA_CHAT = f"{OLLAMA_URL}/api/chat"
OLLAMA_MODEL = "qwen3:14b"
OLLAMA_TIMEOUT = 900

SIGNAL_RPC = "http://127.0.0.1:8080/api/v1/rpc"
SIGNAL_FROM = "+<BOT_PHONE>"
SIGNAL_TO = "+<OWNER_PHONE>"

USER_AGENT = "NetscanCSIWatch/1.0 (Linux SBC camera sensor monitor)"

# Platforms the user owns
PLATFORMS = {
    "rpi2":             {"name": "Raspberry Pi 2", "isp": "unicam", "max_lanes": 2},
    "rpi4":             {"name": "Raspberry Pi 4", "isp": "unicam", "max_lanes": 4},
    "rpi5":             {"name": "Raspberry Pi 5", "isp": "pisp",   "max_lanes": 4},
    "jetson_orin_nano": {"name": "Jetson Orin Nano", "isp": "nvidia", "max_lanes": 4},
}

# Days to look back for patches / issues
LOOKBACK_DAYS = 60


# ── Helpers ────────────────────────────────────────────────────────────────
def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def fetch_url(url, timeout=30):
    """Fetch URL and return (status, text).  Returns (0, '') on error."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as ex:
        log(f"  HTTP {ex.code}: {url}")
        return ex.code, ""
    except Exception as ex:
        log(f"  Fetch error: {url} — {ex}")
        return 0, ""


def fetch_json(url, timeout=30):
    """Fetch URL, parse JSON. Returns None on error."""
    status, text = fetch_url(url, timeout)
    if status == 200 and text:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            log(f"  JSON parse error: {url}")
    return None


def load_json(path):
    """Load JSON file, return None on error."""
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def save_json(path, data):
    """Save JSON with mkdir."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


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
        "keep_alive": "10m",
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
        log(f"  Ollama error: {ex}")
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
    }).encode()
    req = urllib.request.Request(
        SIGNAL_RPC, data=payload,
        headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        log("Signal alert sent")
    except Exception as ex:
        log(f"Signal send failed: {ex}")


class HTMLTextExtractor(HTMLParser):
    """Simple HTML to text extractor."""
    def __init__(self):
        super().__init__()
        self.parts = []
    def handle_data(self, data):
        self.parts.append(data)
    def get_text(self):
        return " ".join(self.parts)


def html_to_text(html_str):
    ext = HTMLTextExtractor()
    ext.feed(html_str or "")
    return ext.get_text()


# ── Sensor model pattern matching ─────────────────────────────────────────
# Patterns from watchlist + common sensor naming conventions
SENSOR_PATTERNS = [
    re.compile(r'\b(IMX[0-9]{3,4}[A-Z]?)\b', re.IGNORECASE),
    re.compile(r'\b(OV[0-9]{4,5}[A-Z]?)\b', re.IGNORECASE),
    re.compile(r'\b(AR[0-9]{3,4}[A-Z]?)\b', re.IGNORECASE),
    re.compile(r'\b(GC[0-9]{4}[A-Z]?)\b', re.IGNORECASE),
    re.compile(r'\b(OS[0-9]{2}[A-Z][0-9]{2})\b', re.IGNORECASE),
    re.compile(r'\b(SC[0-9]{4}[A-Z]?)\b', re.IGNORECASE),
    re.compile(r'\b(S5K[A-Z0-9]{3,6})\b', re.IGNORECASE),
    re.compile(r'\b(HI[0-9]{3,4})\b', re.IGNORECASE),
    re.compile(r'\b(MT9[A-Z][0-9]{3})\b', re.IGNORECASE),
]

# False positives to ignore (non-sensor model numbers)
SENSOR_IGNORE = {
    "ar0000", "ar1234", "ov0000", "imx000",
    "gc0000", "sc0000", "hi0000",
}


def extract_sensor_models(text):
    """Extract potential sensor model numbers from text. Returns set of uppercase models."""
    found = set()
    for pat in SENSOR_PATTERNS:
        for m in pat.finditer(text):
            model = m.group(1).upper()
            if model.lower() not in SENSOR_IGNORE:
                found.add(model)
    return found


# ── Source: Linux kernel tree (check which sensors have mainline drivers) ──
KERNEL_TREE_API = "https://api.github.com/repos/torvalds/linux/contents/drivers/media/i2c"

def check_kernel_drivers():
    """Fetch kernel media/i2c driver listing via GitHub API. Returns dict of sensor_model -> driver_path."""
    log("Checking kernel tree for CSI sensor drivers (via GitHub API)...")
    data = fetch_json(KERNEL_TREE_API, timeout=30)
    if not data or not isinstance(data, list):
        log("  Failed to fetch kernel tree from GitHub API")
        return {}

    sensor_re = re.compile(r'^(imx|ov|ar|gc|sc|s5k|hi|mt9|os)\d', re.IGNORECASE)
    drivers = {}
    for item in data:
        name = item.get("name", "")
        base = name.replace(".c", "").replace(".h", "")
        if sensor_re.match(base):
            model = base.upper().split("_")[0]
            path = item.get("path", f"drivers/media/i2c/{name}")
            if item.get("type") == "file" and name.endswith(".c"):
                drivers[model] = path
            elif item.get("type") == "dir":
                drivers[model] = path + "/"

    log(f"  Found {len(drivers)} sensor-related entries in kernel tree")
    return drivers


# ── Source: Patchwork LinuxTV (recent sensor patches) ──────────────────────
PATCHWORK_BASE = "https://patchwork.linuxtv.org/api/1.3"
PATCHWORK_PROJECT_ID = 1

def fetch_linuxtv_patches(sensor_names):
    """Fetch recent patches from LinuxTV patchwork mentioning any tracked sensors."""
    since = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT00:00:00")
    log(f"Fetching LinuxTV patches since {since}...")

    all_patches = []
    page = 1
    max_pages = 5  # ~500 patches max

    while page <= max_pages:
        url = (
            f"{PATCHWORK_BASE}/patches/"
            f"?project={PATCHWORK_PROJECT_ID}"
            f"&since={since}"
            f"&order=-date"
            f"&per_page=100"
            f"&page={page}"
        )
        data = fetch_json(url, timeout=30)
        if not data:
            break
        all_patches.extend(data)
        if len(data) < 100:
            break
        page += 1
        time.sleep(0.5)

    log(f"  Fetched {len(all_patches)} patches total")

    # Match patches against sensor names
    sensor_set = {s.lower() for s in sensor_names}
    results = {}
    discovered_models = set()

    for patch in all_patches:
        title = (patch.get("name") or "").lower()
        # Check if any sensor is mentioned
        matched_sensors = set()
        for sensor in sensor_set:
            if sensor.lower() in title:
                matched_sensors.add(sensor.upper())

        # Also discover new sensors in patch titles
        new_models = extract_sensor_models(title)
        discovered_models.update(new_models)

        if matched_sensors:
            for sensor in matched_sensors:
                if sensor not in results:
                    results[sensor] = []
                state = patch.get("state", "unknown")
                results[sensor].append({
                    "id": patch.get("id"),
                    "title": patch.get("name", "")[:200],
                    "state": state,
                    "date": patch.get("date", "")[:10],
                    "url": patch.get("web_url") or patch.get("mbox", ""),
                    "submitter": (patch.get("submitter") or {}).get("name", "unknown"),
                    "source": "linuxtv",
                })

    log(f"  Matched patches for {len(results)} sensors, discovered {len(discovered_models)} model refs")
    return results, discovered_models


# ── Source: GitHub Issues (RPi linux, libcamera) ───────────────────────────
GITHUB_REPOS = [
    ("raspberrypi", "linux", "RPi Linux Kernel"),
    ("raspberrypi", "libcamera", "RPi libcamera"),
]

def fetch_github_issues(sensor_names):
    """Fetch recent open issues from RPi GitHub repos mentioning sensors."""
    log("Fetching GitHub issues...")
    results = {}
    discovered_models = set()
    sensor_set = {s.lower() for s in sensor_names}

    for owner, repo, label in GITHUB_REPOS:
        url = f"https://api.github.com/repos/{owner}/{repo}/issues?state=open&per_page=100&sort=updated"
        data = fetch_json(url, timeout=30)
        if not data:
            log(f"  Failed to fetch {owner}/{repo}")
            continue

        count = 0
        for issue in data:
            if issue.get("pull_request"):
                continue  # Skip PRs
            title = (issue.get("title") or "").lower()
            body = (issue.get("body") or "")[:500].lower()
            text = title + " " + body

            # Check sensor mentions
            matched = set()
            for sensor in sensor_set:
                if sensor in text:
                    matched.add(sensor.upper())

            # Also check for CSI/camera keywords even without specific sensor
            csi_keywords = {"csi", "camera", "sensor", "i2c", "v4l2", "libcamera", "mipi"}
            has_csi_context = any(k in text for k in csi_keywords)

            # Discover new sensors
            new_models = extract_sensor_models(text)
            if has_csi_context:
                discovered_models.update(new_models)

            if matched:
                for sensor in matched:
                    if sensor not in results:
                        results[sensor] = []
                    labels = [l.get("name", "") for l in issue.get("labels", [])]
                    results[sensor].append({
                        "id": issue.get("number"),
                        "title": issue.get("title", "")[:200],
                        "state": issue.get("state", "open"),
                        "date": (issue.get("updated_at") or "")[:10],
                        "url": issue.get("html_url", ""),
                        "author": (issue.get("user") or {}).get("login", "?"),
                        "comments": issue.get("comments", 0),
                        "labels": labels,
                        "source": f"github:{owner}/{repo}",
                        "repo_label": label,
                    })
                    count += 1

        log(f"  {owner}/{repo}: {count} sensor-related issues")
        time.sleep(1)  # Rate limit

    return results, discovered_models


# ── Source: GitLab libcamera (freedesktop.org) ─────────────────────────────
LIBCAMERA_GITLAB = "https://gitlab.freedesktop.org/api/v4"
LIBCAMERA_PROJECT = "camera%2Flibcamera"

def fetch_libcamera_issues(sensor_names):
    """Fetch recent issues from libcamera GitLab."""
    log("Fetching libcamera GitLab issues...")
    since = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT00:00:00Z")
    url = (
        f"{LIBCAMERA_GITLAB}/projects/{LIBCAMERA_PROJECT}/issues"
        f"?state=opened&updated_after={since}&per_page=100"
    )
    data = fetch_json(url, timeout=30)
    if not data:
        log("  Failed to fetch libcamera issues")
        return {}, set()

    results = {}
    discovered_models = set()
    sensor_set = {s.lower() for s in sensor_names}

    for issue in data:
        title = (issue.get("title") or "").lower()
        desc = (issue.get("description") or "")[:500].lower()
        text = title + " " + desc

        matched = set()
        for sensor in sensor_set:
            if sensor in text:
                matched.add(sensor.upper())

        new_models = extract_sensor_models(text)
        discovered_models.update(new_models)

        if matched:
            for sensor in matched:
                if sensor not in results:
                    results[sensor] = []
                results[sensor].append({
                    "id": issue.get("iid"),
                    "title": issue.get("title", "")[:200],
                    "state": issue.get("state", "opened"),
                    "date": (issue.get("updated_at") or "")[:10],
                    "url": issue.get("web_url", ""),
                    "author": (issue.get("author") or {}).get("username", "?"),
                    "labels": issue.get("labels", []),
                    "source": "gitlab:libcamera",
                })

    log(f"  Found issues for {len(results)} sensors")
    return results, discovered_models


# ── Source: lore.kernel.org (linux-media mailing list) ─────────────────────
LORE_BASE = "https://lore.kernel.org/linux-media/"

def fetch_lore_mentions(sensor_names):
    """Search lore.kernel.org for recent sensor discussions. Uses Atom feed search."""
    log("Searching lore.kernel.org/linux-media for sensor mentions...")
    results = {}
    discovered_models = set()

    # Group sensors to minimize API calls - search for top-interest ones
    # lore search is expensive, limit to most important sensors
    priority_sensors = sorted(sensor_names, key=lambda s: s)[:15]

    for sensor in priority_sensors:
        # lore search URL with Atom output
        query = urllib.parse.quote(sensor)
        url = f"{LORE_BASE}?q={query}&x=A"
        status, text = fetch_url(url, timeout=20)

        if status != 200 or not text:
            continue

        # Parse Atom entries (simplified)
        entries = re.findall(
            r'<entry>.*?<title[^>]*>(.*?)</title>.*?<updated>(.*?)</updated>.*?<link[^>]*href="([^"]*)".*?<name>(.*?)</name>.*?</entry>',
            text, re.DOTALL
        )

        recent_cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).isoformat()
        sensor_upper = sensor.upper()

        for title, updated, link, author in entries[:10]:
            title = html_to_text(title).strip()
            if updated[:10] >= recent_cutoff[:10]:
                if sensor_upper not in results:
                    results[sensor_upper] = []
                results[sensor_upper].append({
                    "title": title[:200],
                    "date": updated[:10],
                    "url": link,
                    "author": html_to_text(author).strip(),
                    "source": "lore:linux-media",
                })

            # Discover from title
            new_models = extract_sensor_models(title)
            discovered_models.update(new_models)

        time.sleep(1)  # Be polite to lore

    log(f"  Found mentions for {len(results)} sensors")
    return results, discovered_models


# ── Vendor product checks ──────────────────────────────────────────────────
PRODUCT_PAGE_URLS = [
    "https://www.arducam.com/raspberry-pi-camera-module/",
    "https://www.arducam.com/product-category/cameras/mipi-camera-module/",
    "https://www.waveshare.com/product/raspberry-pi/cameras.htm",
]

def check_vendor_products():
    """Scrape vendor product pages for available CSI sensor models."""
    log("Checking vendor product listings...")
    all_text = ""
    for url in PRODUCT_PAGE_URLS:
        status, html = fetch_url(url, timeout=20)
        if status == 200:
            all_text += " " + html_to_text(html)
        else:
            log(f"  Could not reach {url} (status {status})")
        time.sleep(1)

    discovered = extract_sensor_models(all_text)
    log(f"  Found {len(discovered)} sensor models from vendors")
    return discovered


# ── Main scan logic ────────────────────────────────────────────────────────
def run_scrape():
    """Scrape all sources for sensor issues/patches. Save raw JSON (no LLM)."""
    t_start = time.time()
    dt = datetime.now()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    log(f"[{dt:%Y-%m-%d %H:%M:%S}] csi-sensor-watch scrape starting")

    # Load watchlist
    watchlist = load_json(WATCHLIST_PATH)
    if not watchlist or "sensors" not in watchlist:
        log("ERROR: No sensor watchlist found!")
        return
    sensors = watchlist["sensors"]
    sensor_names = list(sensors.keys())
    log(f"Loaded watchlist: {len(sensor_names)} sensors")

    # Load previous results for comparison
    prev_data = load_json(LATEST_PATH)

    # Step 1: Check kernel tree for mainline drivers
    kernel_drivers = check_kernel_drivers()

    # Step 2: Fetch patches from LinuxTV patchwork
    patch_results, patch_discovered = fetch_linuxtv_patches(sensor_names)

    # Step 3: Fetch GitHub issues
    github_results, github_discovered = fetch_github_issues(sensor_names)

    # Step 4: Fetch libcamera GitLab issues
    libcamera_results, libcamera_discovered = fetch_libcamera_issues(sensor_names)

    # Step 5: Search lore.kernel.org
    lore_results, lore_discovered = fetch_lore_mentions(sensor_names)

    # Step 6: Check vendor product pages
    vendor_discovered = check_vendor_products()

    # Merge all results per sensor
    all_discovered = set()
    all_discovered.update(patch_discovered, github_discovered,
                         libcamera_discovered, lore_discovered, vendor_discovered)

    # Kernel driver alias map: some sensors share drivers
    DRIVER_ALIASES = {
        "OV9281": "OV9282", "IMX327": "IMX290", "IMX462": "IMX290",
        "IMX378": "IMX477", "OV2311": "OV9282", "IMX708": "IMX708",
    }

    sensor_data = {}
    sensors_with_issues = 0
    sensors_needing_driver = 0
    total_issues = 0
    new_findings = []

    for sid, sinfo in sensors.items():
        model = sinfo.get("model", sid.upper())
        model_upper = model.upper()
        model_lower = model.lower()

        # Check mainline driver existence (direct or via alias)
        has_mainline = model_upper in kernel_drivers
        driver_path = kernel_drivers.get(model_upper, "")
        if not has_mainline:
            alias = DRIVER_ALIASES.get(model_upper)
            if alias and alias in kernel_drivers:
                has_mainline = True
                driver_path = kernel_drivers[alias] + f" (via {alias})"

        # Collect all issues/patches for this sensor
        issues = []
        for src_results in [patch_results, github_results, libcamera_results, lore_results]:
            if model_upper in src_results:
                issues.extend(src_results[model_upper])

        # Remove duplicates by title similarity
        seen_titles = set()
        unique_issues = []
        for iss in issues:
            title_key = iss.get("title", "")[:60].lower()
            if title_key not in seen_titles:
                seen_titles.add(title_key)
                unique_issues.append(iss)
        issues = sorted(unique_issues, key=lambda x: x.get("date", ""), reverse=True)[:15]

        # Determine driver status
        driver_status = "mainline" if has_mainline else "missing"
        if not has_mainline and issues:
            for iss in issues:
                title_lower = (iss.get("title") or "").lower()
                if any(kw in title_lower for kw in ["add support", "introduce", "add driver", "new driver"]):
                    driver_status = "in-progress"
                    break

        if driver_status != "mainline":
            sensors_needing_driver += 1

        if issues:
            sensors_with_issues += 1
            total_issues += len(issues)

        # Check if there are NEW issues compared to previous scan
        prev_sensor = {}
        if prev_data and "sensors" in prev_data:
            prev_sensor = prev_data["sensors"].get(sid, {})
        prev_issue_ids = {(i.get("source", ""), i.get("id")) for i in prev_sensor.get("issues", [])}
        for iss in issues:
            key = (iss.get("source", ""), iss.get("id"))
            if key not in prev_issue_ids and iss.get("id"):
                new_findings.append(f"{model}: {iss.get('title', '')[:80]}")

        sensor_data[sid] = {
            "model": model,
            "vendor": sinfo.get("vendor", "?"),
            "resolution": sinfo.get("resolution", "?"),
            "interface": sinfo.get("interface", "CSI-2"),
            "products": sinfo.get("products", []),
            "datasheet": sinfo.get("datasheet", "unknown"),
            "purchase": sinfo.get("purchase", "unknown"),
            "notes": sinfo.get("notes", ""),
            "platforms": sinfo.get("platforms", []),
            "interest": sinfo.get("interest", 3),
            "driver_mainline": has_mainline,
            "driver_path": driver_path,
            "driver_status": driver_status,
            "issues": issues,
            "issue_count": len(issues),
        }

    # Step 7: Process discovered sensors (candidates for watchlist expansion)
    known_models = {sinfo.get("model", sid).upper() for sid, sinfo in sensors.items()}
    existing_candidates = {c.get("model", "").upper() for c in watchlist.get("candidates", [])}
    new_candidates = []

    for model in all_discovered:
        model_upper = model.upper()
        if model_upper not in known_models and model_upper not in existing_candidates:
            if re.match(r'^(IMX|OV|AR|GC|SC|S5K|HI|MT9|OS)\d{2,}', model_upper):
                new_candidates.append({
                    "model": model_upper,
                    "discovered": datetime.now().strftime("%Y-%m-%d"),
                    "source": "auto-discovery",
                    "status": "unresearched",
                })

    # Update watchlist candidates
    if new_candidates:
        existing = watchlist.get("candidates", [])
        existing.extend(new_candidates)
        watchlist["candidates"] = existing[-50:]
        watchlist["__updated"] = datetime.now().strftime("%Y-%m-%d")
        save_json(WATCHLIST_PATH, watchlist)
        log(f"Added {len(new_candidates)} new candidates to watchlist")

    # Save raw intermediate data
    scrape_duration = round(time.time() - t_start, 1)
    raw_data = {
        "scrape_timestamp": dt.isoformat(timespec="seconds"),
        "scrape_duration_seconds": scrape_duration,
        "scrape_version": 1,
        "data": {
            "sensor_data": sensor_data,
            "new_candidates": new_candidates,
            "new_findings": new_findings,
            "sensors_with_issues": sensors_with_issues,
            "sensors_needing_driver": sensors_needing_driver,
            "total_issues": total_issues,
            "all_candidates_count": len(watchlist.get("candidates", [])),
        },
        "scrape_errors": [],
    }
    tmp = RAW_CSI_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(raw_data, f, indent=2, ensure_ascii=False)
    tmp.rename(RAW_CSI_FILE)

    log(f"Scrape done: {len(sensor_data)} sensors, {len(new_findings)} new findings ({scrape_duration}s)")


def run_analyze():
    """Load raw data, run LLM analysis, save final output. Signal on new findings."""
    dt = datetime.now()
    t_start = time.time()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    log(f"[{dt:%Y-%m-%d %H:%M:%S}] csi-sensor-watch analyze starting")

    if not RAW_CSI_FILE.exists():
        log(f"ERROR: Raw data file not found: {RAW_CSI_FILE}")
        log("Run with --scrape-only first.")
        return

    try:
        with open(RAW_CSI_FILE) as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log(f"ERROR: Failed to read raw data: {e}")
        return

    scrape_ts = raw.get("scrape_timestamp", "")
    d = raw.get("data", {})
    sensor_data = d.get("sensor_data", {})
    new_candidates = d.get("new_candidates", [])
    new_findings = d.get("new_findings", [])
    sensors_with_issues = d.get("sensors_with_issues", 0)
    sensors_needing_driver = d.get("sensors_needing_driver", 0)
    total_issues = d.get("total_issues", 0)
    all_candidates_count = d.get("all_candidates_count", 0)

    # Check staleness
    if scrape_ts:
        try:
            scrape_dt = datetime.fromisoformat(scrape_ts)
            age_hours = (dt - scrape_dt).total_seconds() / 3600
            if age_hours > 48:
                log(f"WARNING: Raw data is {age_hours:.0f}h old (scraped {scrape_ts})")
        except ValueError:
            pass

    log(f"Loaded raw data: {len(sensor_data)} sensors (scraped {scrape_ts})")

    # Step 8: LLM analysis
    llm_analysis = ""
    if sensor_data:
        llm_analysis = llm_analyze_sensors(sensor_data, new_candidates, new_findings)

    # Step 9: Build final output with dual timestamps
    stats = {
        "total_sensors": len(sensor_data),
        "with_mainline_driver": sum(1 for s in sensor_data.values() if s["driver_mainline"]),
        "needs_driver": sensors_needing_driver,
        "active_issues": total_issues,
        "sensors_with_issues": sensors_with_issues,
        "new_candidates": len(new_candidates),
        "new_findings": len(new_findings),
    }

    output = {
        "scrape_timestamp": scrape_ts,
        "analyze_timestamp": dt.isoformat(timespec="seconds"),
        "checked": dt.strftime("%Y-%m-%d %H:%M"),  # backward compat
        "platforms": {pid: pinfo["name"] for pid, pinfo in PLATFORMS.items()},
        "sensors": sensor_data,
        "candidates": new_candidates[:10],
        "all_candidates_count": all_candidates_count,
        "stats": stats,
        "llm_analysis": llm_analysis,
        "scan_time_sec": round(time.time() - t_start, 1),
    }

    save_json(LATEST_PATH, output)

    # Save history snapshot
    hist_path = HISTORY_DIR / f"csi-{dt.strftime('%Y%m%d-%H%M')}.json"
    save_json(hist_path, output)

    # Clean old history (keep 30 days)
    clean_history()

    log(f"\nAnalyze complete in {output['scan_time_sec']}s")
    log(f"  Sensors: {stats['total_sensors']} tracked, {stats['with_mainline_driver']} mainline, {stats['needs_driver']} need driver")
    log(f"  Issues: {stats['active_issues']} total, {stats['new_findings']} new")
    log(f"  Candidates: {stats['new_candidates']} new auto-discovered")

    # Step 10: Signal alert for important new findings
    if new_findings:
        alert_lines = [f"📷 CSI Sensor Watch: {len(new_findings)} new findings"]
        for finding in new_findings[:5]:
            alert_lines.append(f"  • {finding}")
        if len(new_findings) > 5:
            alert_lines.append(f"  ... +{len(new_findings) - 5} more")
        alert_lines.append(f"\nhttp://192.168.3.151:8888/issues.html")
        signal_send("\n".join(alert_lines))

    # Regenerate dashboard
    gen_html = os.path.join(SCRIPT_DIR, "generate-html.py")
    if os.path.exists(gen_html):
        log("Regenerating dashboard...")
        os.system(f"python3 {gen_html} 2>/dev/null")
        log("Dashboard updated")


def run_scan():
    """Legacy: full scan (backward compatible)."""
    run_scrape()
    run_analyze()


def llm_analyze_sensors(sensor_data, new_candidates, new_findings):
    """LLM summary of CSI sensor situation."""
    log("LLM analysis...")

    # Build compact summary for LLM
    summary_lines = []
    for sid, s in sensor_data.items():
        status = "✅mainline" if s["driver_mainline"] else ("🔧in-progress" if s["driver_status"] == "in-progress" else "❌missing")
        issues_note = f", {s['issue_count']} issues" if s["issue_count"] else ""
        summary_lines.append(
            f"- {s['model']} ({s['vendor']}, {s['resolution']}): {status}, "
            f"datasheet={s['datasheet']}, buy={s['purchase']}{issues_note}"
        )

    sensor_summary = "\n".join(summary_lines)

    new_findings_text = ""
    if new_findings:
        new_findings_text = "\n\nNew findings since last scan:\n" + "\n".join(f"- {f}" for f in new_findings[:10])

    candidates_text = ""
    if new_candidates:
        models = [c["model"] for c in new_candidates[:10]]
        candidates_text = f"\n\nNewly discovered sensor models (not yet tracked): {', '.join(models)}"

    system = (
        "You are an expert Linux camera driver engineer specializing in CSI-2/MIPI sensors. "
        "The user owns: Jetson Orin Nano, Raspberry Pi 2, RPi 4, RPi 5. "
        "Analyze the sensor landscape and highlight: "
        "1) Sensors most worth developing drivers for (missing driver + available docs + buyable) "
        "2) Active driver development to watch "
        "3) New sensors that appeared and may be interesting "
        "4) Recommendations for learning CSI driver development. "
        "Be concise and actionable. Max 500 words."
    )

    user = f"Current CSI sensor status:\n{sensor_summary}{new_findings_text}{candidates_text}"

    analysis = call_ollama(system, user)
    if analysis:
        log(f"  LLM analysis: {len(analysis)} chars")
    return analysis


# ── Discovery mode ─────────────────────────────────────────────────────────
def run_discover():
    """Discovery mode: search additional sources for new CSI sensors."""
    log("=== Discovery mode ===")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    watchlist = load_json(WATCHLIST_PATH)
    if not watchlist:
        log("ERROR: No watchlist found")
        return

    known_models = {s.get("model", sid).upper() for sid, s in watchlist.get("sensors", {}).items()}
    existing_candidates = {c.get("model", "").upper() for c in watchlist.get("candidates", [])}

    discovered = set()

    # Check vendor products
    vendor_models = check_vendor_products()
    discovered.update(vendor_models)

    # Check recent LinuxTV patches for any sensor references
    since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00")
    url = f"{PATCHWORK_BASE}/patches/?project={PATCHWORK_PROJECT_ID}&since={since}&per_page=100"
    data = fetch_json(url, timeout=30)
    if data:
        for patch in data:
            title = patch.get("name", "")
            discovered.update(extract_sensor_models(title))

    # Check lore for recent "media: add" patches (new drivers being submitted)
    status, text = fetch_url(f"{LORE_BASE}?q=media+add+support+sensor&x=A", timeout=20)
    if status == 200:
        discovered.update(extract_sensor_models(text))

    # Filter to truly new sensors
    new = discovered - known_models - existing_candidates
    if new:
        candidates = watchlist.get("candidates", [])
        for model in new:
            if re.match(r'^(IMX|OV|AR|GC|SC|S5K|HI|MT9|OS)\d{2,}', model):
                candidates.append({
                    "model": model,
                    "discovered": datetime.now().strftime("%Y-%m-%d"),
                    "source": "discovery-scan",
                    "status": "unresearched",
                })
        watchlist["candidates"] = candidates[-50:]
        watchlist["__updated"] = datetime.now().strftime("%Y-%m-%d")
        save_json(WATCHLIST_PATH, watchlist)
        log(f"Discovery found {len(new)} new sensor models: {', '.join(sorted(new))}")
    else:
        log("No new sensors discovered")


# ── Self-improvement mode ──────────────────────────────────────────────────
def run_improve():
    """Self-improvement: Use LLM to research candidates and expand watchlist."""
    log("=== Self-improvement mode ===")

    watchlist = load_json(WATCHLIST_PATH)
    if not watchlist:
        log("ERROR: No watchlist found")
        return

    candidates = [c for c in watchlist.get("candidates", []) if c.get("status") == "unresearched"]
    if not candidates:
        log("No unresearched candidates to improve")
        # Still ask LLM for general suggestions
        candidates = []

    sensors = watchlist.get("sensors", {})
    known_models = sorted(s.get("model", sid).upper() for sid, s in sensors.items())

    # Ask LLM to research candidates and suggest additions
    system = (
        "You are an expert in CSI-2/MIPI camera sensors for embedded Linux platforms. "
        "The user develops camera drivers for: Jetson Orin Nano, RPi 2, RPi 4, RPi 5. "
        "They track CSI sensors for driver development opportunities.\n\n"
        "Your task: Research the candidate sensors and suggest which should be added to the watchlist. "
        "Also suggest any missing sensors not in the current list.\n\n"
        "For each suggested sensor, provide EXACTLY this JSON format (one per line):\n"
        'SENSOR_ADD: {"id":"imx999","vendor":"Sony","model":"IMX999","resolution":"XMP","interface":"N-lane CSI-2",'
        '"products":["Example Module"],"datasheet":"available|restricted|unknown","purchase":"easy|moderate|hard|unknown",'
        '"notes":"Brief description","platforms":["rpi4","rpi5","jetson_orin_nano"],"interest":3}\n\n'
        "Interest scale: 1=low, 3=medium, 5=high (based on driver development value + documentation + availability)\n"
        "Only suggest sensors with CSI-2/MIPI interface that work with Linux SBCs. Skip phone-only sensors."
    )

    candidate_text = ""
    if candidates:
        candidate_text = "Unresearched candidates found by auto-discovery:\n"
        for c in candidates[:20]:
            candidate_text += f"  - {c['model']} (discovered {c.get('discovered', '?')})\n"
    else:
        candidate_text = "No pending candidates.\n"

    user = (
        f"Currently tracked sensors ({len(known_models)}):\n{', '.join(known_models)}\n\n"
        f"{candidate_text}\n"
        "Please:\n"
        "1) Research any candidates and decide if they should be added\n"
        "2) Suggest any important CSI sensors we're missing\n"
        "3) Mark each with SENSOR_ADD: JSON line so I can parse it"
    )

    response = call_ollama(system, user, timeout=OLLAMA_TIMEOUT)
    if not response:
        log("LLM returned empty response")
        return

    # Parse SENSOR_ADD lines from LLM response
    added = 0
    for line in response.split("\n"):
        if "SENSOR_ADD:" in line:
            json_str = line.split("SENSOR_ADD:", 1)[1].strip()
            try:
                entry = json.loads(json_str)
                sid = entry.get("id", entry.get("model", "").lower())
                if sid and sid not in sensors:
                    sensors[sid] = {
                        "vendor": entry.get("vendor", "?"),
                        "model": entry.get("model", sid.upper()),
                        "resolution": entry.get("resolution", "?"),
                        "interface": entry.get("interface", "CSI-2"),
                        "products": entry.get("products", []),
                        "datasheet": entry.get("datasheet", "unknown"),
                        "purchase": entry.get("purchase", "unknown"),
                        "notes": entry.get("notes", ""),
                        "platforms": entry.get("platforms", ["rpi5", "jetson_orin_nano"]),
                        "interest": entry.get("interest", 3),
                    }
                    added += 1
                    log(f"  Added sensor: {entry.get('model', sid)}")
            except json.JSONDecodeError:
                continue

    # Mark candidates as researched
    for c in watchlist.get("candidates", []):
        if c.get("status") == "unresearched":
            c["status"] = "researched"
            c["researched_date"] = datetime.now().strftime("%Y-%m-%d")

    if added:
        watchlist["sensors"] = sensors
        watchlist["__updated"] = datetime.now().strftime("%Y-%m-%d")
        save_json(WATCHLIST_PATH, watchlist)
        log(f"Self-improvement: added {added} new sensors to watchlist")
    else:
        save_json(WATCHLIST_PATH, watchlist)  # Still save researched status
        log("Self-improvement: no new sensors to add (watchlist is comprehensive)")


# ── Utilities ──────────────────────────────────────────────────────────────
def clean_history(max_days=30):
    """Remove history files older than max_days."""
    if not HISTORY_DIR.exists():
        return
    cutoff = datetime.now() - timedelta(days=max_days)
    for f in HISTORY_DIR.glob("csi-*.json"):
        try:
            ts = f.stem.replace("csi-", "")
            fdate = datetime.strptime(ts[:8], "%Y%m%d")
            if fdate < cutoff:
                f.unlink()
        except (ValueError, OSError):
            pass


# ── Entry point ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CSI camera sensor driver issue monitor")
    parser.add_argument('--scrape-only', action='store_true',
                        help='Only scrape sources, save raw data (no LLM)')
    parser.add_argument('--analyze-only', action='store_true',
                        help='Only run LLM analysis on previously scraped raw data')
    parser.add_argument('--discover', action='store_true',
                        help='Discovery mode — search for new sensor models')
    parser.add_argument('--improve', action='store_true',
                        help='Self-improvement — LLM-guided watchlist expansion')
    args = parser.parse_args()

    if args.discover:
        log("csi-sensor-watch.py — mode: discover")
        run_discover()
    elif args.improve:
        log("csi-sensor-watch.py — mode: improve")
        run_improve()
    elif args.scrape_only:
        log("csi-sensor-watch.py — mode: scrape-only")
        run_scrape()
    elif args.analyze_only:
        log("csi-sensor-watch.py — mode: analyze-only")
        run_analyze()
    else:
        log("csi-sensor-watch.py — mode: scan (full)")
        run_scan()
        # Dashboard regen is now handled inside run_analyze()
