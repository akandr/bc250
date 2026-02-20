#!/usr/bin/env python3
"""career-scan.py — Automated OSINT career intelligence scanner.

Scans company career pages, job boards, and industry intel sites for
job opportunities matching AK's profile (kernel/driver/camera/embedded).
Feeds results to local Ollama LLM for semantic analysis and scoring.
Saves structured JSON + think-system note for the dashboard.

Targets:
  - Direct career pages: Nvidia, Google, AMD, Intel, Samsung, Amazon,
    TCL Research Europe, Harman (employer monitoring)
  - Job aggregators: LinkedIn, Indeed, justjoin.it, nofluffjobs.com
  - Company intel: gowork.pl, layoffs.fyi, levels.fyi, glassdoor
  - Tech job boards: kernel.org/jobs, lwn.net/jobs

Filters:
  - Remote-from-Poland OR hybrid in Łódź/Warsaw
  - Kernel, drivers, embedded, camera, V4L2, MIPI, ISP, BSP, SoC
  - Silicon / automotive / consumer electronics industry

Usage:
    career-scan.py                  (full scan — all sources)
    career-scan.py --quick          (career pages only, skip intel)
    career-scan.py --signal-test    (send test notification)

Schedule (cron): Mon/Thu at 11:00
    0 11 * * 1,4  /usr/bin/python3 /opt/netscan/career-scan.py

Location on bc250: /opt/netscan/career-scan.py
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse
import hashlib
from datetime import datetime, timedelta

# ─── Config ───

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = "/opt/netscan/data"
CAREER_DIR = os.path.join(DATA_DIR, "career")
THINK_DIR = os.path.join(DATA_DIR, "think")
PROFILE_PATH = os.path.join(SCRIPT_DIR, "profile.json")
PROFILE_PRIVATE_PATH = os.path.join(SCRIPT_DIR, "profile-private.json")

OLLAMA_URL = "http://localhost:11434"
OLLAMA_CHAT = f"{OLLAMA_URL}/api/chat"
OLLAMA_MODEL = "huihui_ai/qwen3-abliterated:14b"  # best model — batch runs during quiet hours

QUIET_START = 0   # 00:00
QUIET_END   = 6   # 06:00  — no chat, GPU free for batch jobs

def is_quiet_hours():
    """True if we're in the 00:00-06:00 quiet window (no Signal chat)."""
    return QUIET_START <= datetime.now().hour < QUIET_END

SIGNAL_RPC = "http://127.0.0.1:8080/api/v1/rpc"
SIGNAL_FROM = "+48532825716"
SIGNAL_TO = "+48503326388"

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

os.makedirs(CAREER_DIR, exist_ok=True)
os.makedirs(THINK_DIR, exist_ok=True)

# ─── Target companies and their career page URLs ───

COMPANIES = {
    "nvidia": {
        "name": "NVIDIA",
        "career_urls": [
            "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite?locationCountry=ccd3a10a0e81473fa33cc8e77a452b8c&workerSubType=0c40f6bd1d7f10adf6dae161b1844a15&jobFamilyGroup=0c40f6bd1d7f10af2bf81a8e2a0935f3",
        ],
        "keywords": ["kernel", "driver", "linux", "camera", "tegra", "embedded", "V4L2", "BSP"],
        "industry": "silicon",
    },
    "google": {
        "name": "Google",
        "career_urls": [
            "https://www.google.com/about/careers/applications/jobs/results/?location=Poland&location=Remote&q=linux%20kernel%20driver",
            "https://www.google.com/about/careers/applications/jobs/results/?location=Poland&location=Remote&q=embedded%20software%20camera",
        ],
        "keywords": ["kernel", "driver", "linux", "chromeos", "camera", "pixel", "embedded", "firmware"],
        "industry": "silicon",
    },
    "amd": {
        "name": "AMD",
        "career_urls": [
            "https://careers.amd.com/careers/SearchJobs?3_56_3=19606&3_56_3=19610&15=8662&listFilterMode=1",
        ],
        "keywords": ["kernel", "driver", "linux", "gpu", "rdna", "rocm", "embedded", "firmware"],
        "industry": "silicon",
    },
    "intel": {
        "name": "Intel",
        "career_urls": [
            "https://jobs.intel.com/en/search-jobs?k=linux+kernel+driver&l=Poland&orgIds=599",
        ],
        "keywords": ["kernel", "driver", "linux", "camera", "ipu", "embedded", "firmware", "BSP"],
        "industry": "silicon",
    },
    "samsung": {
        "name": "Samsung",
        "career_urls": [
            "https://sec.wd3.myworkdayjobs.com/Samsung_Careers?locationCountry=ccd3a10a0e81473fa33cc8e77a452b8c",
        ],
        "keywords": ["kernel", "driver", "linux", "camera", "embedded", "exynos", "firmware"],
        "industry": "silicon",
    },
    "amazon": {
        "name": "Amazon",
        "career_urls": [
            "https://www.amazon.jobs/en/search?base_query=linux+kernel+driver&loc_query=Poland&country=POL",
            "https://www.amazon.jobs/en/search?base_query=embedded+linux+camera&loc_query=&country=&job_type=Full-Time",
        ],
        "keywords": ["kernel", "driver", "linux", "embedded", "camera", "ring", "alexa", "firmware"],
        "industry": "tech",
    },
    "tcl": {
        "name": "TCL Research Europe",
        "career_urls": [
            "https://tcl-research.pl/career/",
            "https://www.tcl.com/global/en/careers.html",
        ],
        "keywords": ["linux", "driver", "camera", "video", "AI", "embedded", "computer vision"],
        "industry": "consumer_electronics",
    },
    "harman": {
        "name": "HARMAN International (Employer)",
        "career_urls": [
            "https://jobs.harman.com/en_US/careers/SearchJobs/?523=%5B8662%5D&523_format=1482&524=%5B3944%5D&524_format=1483&listFilterMode=1&jobRecordsPerPage=25&jobSort=relevancy",
        ],
        "keywords": ["linux", "driver", "camera", "embedded", "ADAS", "automotive", "kernel"],
        "industry": "automotive",
        "employer": True,
    },
    "qualcomm": {
        "name": "Qualcomm",
        "career_urls": [
            "https://careers.qualcomm.com/careers?query=linux%20kernel%20driver&pid=446700572793&domain=qualcomm.com&location=Poland&triggerGoButton=false",
        ],
        "keywords": ["kernel", "driver", "linux", "camera", "snapdragon", "embedded", "BSP", "MIPI"],
        "industry": "silicon",
    },
    "arm": {
        "name": "Arm",
        "career_urls": [
            "https://careers.arm.com/search-jobs?k=linux+kernel&l=Poland&orgIds=3529",
        ],
        "keywords": ["kernel", "driver", "linux", "embedded", "GPU", "mali", "firmware"],
        "industry": "silicon",
    },
}

# ─── Job boards & aggregators ───

JOB_BOARDS = {
    "justjoin": {
        "name": "justjoin.it",
        "urls": [
            "https://justjoin.it/offers?keyword=linux+kernel&remote=true",
            "https://justjoin.it/offers?keyword=embedded+linux&remote=true",
            "https://justjoin.it/offers?keyword=driver+developer&remote=true",
        ],
    },
    "nofluff": {
        "name": "nofluffjobs.com",
        "urls": [
            "https://nofluffjobs.com/pl/praca-zdalna/linux?criteria=keyword%3Dlinux%20kernel",
            "https://nofluffjobs.com/pl/praca-zdalna/embedded?criteria=keyword%3Dembedded",
        ],
    },
    "linkedin": {
        "name": "LinkedIn",
        "urls": [
            "https://www.linkedin.com/jobs/search/?keywords=linux%20kernel%20driver&location=Poland&f_WT=2",
            "https://www.linkedin.com/jobs/search/?keywords=embedded%20linux%20camera&location=Poland&f_WT=2",
        ],
    },
}

# ─── Company intelligence sources ───

INTEL_SOURCES = {
    "gowork": {
        "name": "GoWork.pl",
        "urls": [
            "https://www.gowork.pl/opinie/harman-connected-services;eid,2830399",
            "https://www.gowork.pl/opinie/nvidia;eid,5060921",
            "https://www.gowork.pl/opinie/intel;eid,5065655",
            "https://www.gowork.pl/opinie/samsung;eid,5060924",
            "https://www.gowork.pl/opinie/amd;eid,5065659",
        ],
    },
    "layoffs": {
        "name": "layoffs.fyi",
        "urls": [
            "https://layoffs.fyi/",
        ],
    },
    "levels": {
        "name": "levels.fyi",
        "urls": [
            "https://www.levels.fyi/t/software-engineer/locations/poland",
        ],
    },
}


# ─── Helpers ───

def fetch_page(url, timeout=25):
    """Fetch a URL and return stripped text content."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Accept-Language": "en-US,en;q=0.9,pl;q=0.8",
        })
        resp = urllib.request.urlopen(req, timeout=timeout)
        raw = resp.read().decode("utf-8", errors="replace")
        # Strip scripts, styles, HTML tags
        text = re.sub(r'<script[^>]*>.*?</script>', '', raw, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text
    except Exception as ex:
        return f"[fetch_error: {ex}]"


def call_ollama(system_prompt, user_prompt, temperature=0.3, max_tokens=3000):
    """Call local Ollama for analysis."""
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        models = [m["name"] for m in data.get("models", [])]
        if OLLAMA_MODEL not in models:
            print(f"  Model {OLLAMA_MODEL} not found")
            return None
    except Exception as ex:
        print(f"  Ollama not reachable: {ex}")
        return None

    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    })

    try:
        req = urllib.request.Request(
            OLLAMA_CHAT, data=payload.encode(),
            headers={"Content-Type": "application/json"},
        )
        t0 = time.time()
        resp = urllib.request.urlopen(req, timeout=600)
        result = json.loads(resp.read())
        elapsed = time.time() - t0
        content = result.get("message", {}).get("content", "")
        tokens = result.get("eval_count", 0)
        tps = tokens / elapsed if elapsed > 0 else 0
        print(f"  Ollama: {elapsed:.0f}s, {tokens} tok ({tps:.1f} t/s)")
        return content
    except Exception as ex:
        print(f"  Ollama failed: {ex}")
        return None


def signal_send(msg):
    """Send Signal notification."""
    try:
        payload = json.dumps({
            "jsonrpc": "2.0", "method": "send",
            "params": {"account": SIGNAL_FROM, "recipient": [SIGNAL_TO], "message": msg},
            "id": "career-scan",
        })
        req = urllib.request.Request(
            SIGNAL_RPC, data=payload.encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception:
        return False


def content_hash(text):
    """Hash content for change detection."""
    return hashlib.sha256(text[:5000].encode()).hexdigest()[:16]


def load_previous_scan():
    """Load most recent career scan data."""
    path = os.path.join(CAREER_DIR, "latest-scan.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return None


def save_scan(scan_data):
    """Save career scan results."""
    # Save latest
    path = os.path.join(CAREER_DIR, "latest-scan.json")
    with open(path, "w") as f:
        json.dump(scan_data, f, indent=2)

    # Save dated archive
    dt = datetime.now().strftime("%Y%m%d-%H%M")
    archive_path = os.path.join(CAREER_DIR, f"scan-{dt}.json")
    with open(archive_path, "w") as f:
        json.dump(scan_data, f, indent=2)

    # Prune old archives (keep last 20)
    archives = sorted(
        [f for f in os.listdir(CAREER_DIR) if f.startswith("scan-") and f.endswith(".json")],
        reverse=True,
    )
    for old in archives[20:]:
        os.remove(os.path.join(CAREER_DIR, old))


def save_note(title, content, context=None):
    """Save a career-scan note in the think system."""
    dt = datetime.now()
    note = {
        "type": "career-scan",
        "title": title,
        "content": content,
        "generated": dt.isoformat(timespec="seconds"),
        "model": OLLAMA_MODEL,
        "context": context or {},
    }
    fname = f"note-career-scan-{dt.strftime('%Y%m%d-%H%M')}.json"
    path = os.path.join(THINK_DIR, fname)
    with open(path, "w") as f:
        json.dump(note, f, indent=2)

    # Update notes index
    index_path = os.path.join(THINK_DIR, "notes-index.json")
    index = []
    if os.path.exists(index_path):
        try:
            with open(index_path) as f:
                index = json.load(f)
        except Exception:
            pass

    index.insert(0, {
        "file": fname, "type": "career-scan", "title": title,
        "generated": note["generated"], "chars": len(content),
    })
    index = index[:50]
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)


# ─── Profile matching keywords ───

MUST_MATCH_ANY = [
    "kernel", "driver", "embedded", "firmware", "bsp", "linux",
    "v4l2", "camera", "mipi", "csi", "isp", "soc", "dma",
    "device tree", "devicetree", "iommu", "i2c", "spi", "pcie",
    "gstreamer", "libcamera", "drm", "kms", "gpu", "vulkan",
    "adas", "automotive", "sensor", "imaging", "video",
    "low-level", "bare-metal", "rtos", "bootloader", "u-boot",
]

STRONG_MATCH = [
    "v4l2", "camera driver", "mipi csi", "isp", "libcamera",
    "kernel driver", "linux kernel", "device tree", "bsp",
    "tegra", "snapdragon", "qualcomm", "exynos",
    "camera subsystem", "sensor driver", "image signal",
]

LOCATION_OK = [
    "remote", "fully remote", "work from home", "wfh",
    "poland", "polska", "łódź", "lodz", "warszawa", "warsaw",
    "emea", "europe", "anywhere", "global",
]

LOCATION_REJECT = [
    "on-site only", "onsite only", "no remote",
    "relocation required", "must be located in",
    "united states only", "us only",
]

# ─── LLM Analysis Prompts ───

SYSTEM_PROMPT_JOBS = """\
You are a career intelligence analyst for a Principal Embedded Software Engineer
specializing in Linux kernel camera drivers (V4L2, MIPI CSI-2, ISP, libcamera),
SoC BSP, and automotive ADAS. 15 years experience. Located in Łódź, Poland.

Analyze the raw text from career pages and extract relevant job listings.

For each relevant job found, output a JSON array entry:
{
  "title": "Job title",
  "company": "Company name",
  "location": "Location / remote status",
  "match_score": 0-100,  // how well it matches the profile
  "match_reasons": ["reason1", "reason2"],
  "key_requirements": ["req1", "req2"],
  "url_hint": "any URL fragment found",
  "remote_compatible": true/false,  // can work from Poland?
  "salary_hint": "if mentioned",
  "red_flags": ["if any — e.g. AUTOSAR-only, pure management"]
}

Rules:
- Only include jobs with match_score >= 40
- STRONGLY prefer: kernel, drivers, camera, V4L2, BSP, embedded Linux, SoC
- ACCEPT: embedded C/C++, firmware, RTOS if low-level and relevant
- REJECT: pure application development, web/cloud, DevOps, test automation, AUTOSAR-only
- Remote-from-Poland or hybrid Łódź/Warsaw = remote_compatible:true
- US/Asia-only onsite = remote_compatible:false
- Be aggressive about filtering — quality over quantity

Output ONLY a valid JSON array. No markdown, no explanation.
If no relevant jobs found, output: []
"""

SYSTEM_PROMPT_INTEL = """\
You are a career intelligence analyst monitoring company news, layoffs, hiring
trends, and salary data for a senior embedded Linux engineer in Poland.

Analyze the raw text from company intel sources and produce a concise briefing.

Output a JSON object:
{
  "alerts": [
    {
      "company": "Company name",
      "type": "layoff|hiring_surge|salary_data|company_news|warning",
      "severity": "info|notable|urgent",
      "summary": "One-line summary",
      "details": "2-3 sentences with specifics"
    }
  ],
  "salary_benchmarks": [
    {"role": "description", "range": "salary range", "source": "source"}
  ],
  "market_mood": "One paragraph on overall job market for this profile"
}

Rules:
- Focus on: Nvidia, Google, AMD, Intel, Samsung, Amazon, TCL, Harman, Qualcomm, Arm
- Flag layoffs at target companies as URGENT
- Flag hiring surges at target companies as NOTABLE
- Include salary data for embedded/Linux/kernel roles in Poland if found
- Be concise and factual

Output ONLY valid JSON. No markdown.
"""

SYSTEM_PROMPT_SUMMARY = """\
You are ClawdBot writing a career intelligence briefing for AK.
Combine job matches and company intel into a clear, actionable summary.

Format:
🎯 TOP MATCHES (if any score >= 70)
Brief each hot match with company, role, why it fits, remote status.

📊 MARKET INTEL
Key company movements, layoffs, hiring trends.

💰 SALARY BENCHMARKS (if found)
Relevant ranges for the profile.

⚠️ ALERTS (anything urgent)
Layoffs at target companies, deadline-sensitive opportunities.

📋 FULL SCAN SUMMARY
X companies scanned, Y pages fetched, Z potential matches found.

Be concise. Use emoji. English only. Under 600 words.
"""


# ─── Main scanning logic ───

def scan_career_pages():
    """Scan all company career pages and extract job listings."""
    print("\n  === Scanning career pages ===")
    all_jobs = []
    page_results = {}

    for cid, company in COMPANIES.items():
        name = company["name"]
        jobs_for_company = []

        for url in company["career_urls"]:
            print(f"  [{name}] Fetching: {url[:80]}...")
            text = fetch_page(url)

            if text.startswith("[fetch_error"):
                print(f"    ✗ {text}")
                page_results[cid] = {"status": "error", "error": text}
                continue

            text_lower = text.lower()
            chars = len(text)
            print(f"    Got {chars} chars")

            # Quick keyword pre-filter — is this page even relevant?
            kw_hits = sum(1 for kw in company["keywords"] if kw.lower() in text_lower)
            if kw_hits == 0 and chars < 500:
                print(f"    ✗ No keyword hits, skipping LLM analysis")
                page_results[cid] = {"status": "no_keywords", "chars": chars}
                continue

            # Trim for LLM context
            text_for_llm = text[:6000]
            prompt = f"Company: {name}\nCareer page content ({chars} chars, trimmed):\n\n{text_for_llm}"

            result = call_ollama(SYSTEM_PROMPT_JOBS, prompt)
            if result:
                try:
                    # Extract JSON from response
                    result = result.strip()
                    if result.startswith("```"):
                        result = re.sub(r'^```\w*\n?', '', result)
                        result = re.sub(r'\n?```$', '', result)
                    jobs = json.loads(result)
                    if isinstance(jobs, list):
                        for j in jobs:
                            j["source_company"] = cid
                            j["company"] = j.get("company", name)
                        jobs_for_company.extend(jobs)
                        print(f"    ✓ Found {len(jobs)} potential matches")
                except (json.JSONDecodeError, ValueError) as e:
                    print(f"    ✗ JSON parse error: {e}")

            page_results[cid] = {
                "status": "ok",
                "chars": chars,
                "hash": content_hash(text),
                "jobs_found": len(jobs_for_company),
            }

        all_jobs.extend(jobs_for_company)

    return all_jobs, page_results


def scan_job_boards():
    """Scan job board aggregators."""
    print("\n  === Scanning job boards ===")
    all_jobs = []
    board_results = {}

    for bid, board in JOB_BOARDS.items():
        name = board["name"]
        combined_text = ""

        for url in board["urls"]:
            print(f"  [{name}] Fetching: {url[:80]}...")
            text = fetch_page(url)
            if not text.startswith("[fetch_error"):
                combined_text += f"\n--- {url} ---\n{text[:3000]}\n"
                print(f"    Got {len(text)} chars")
            else:
                print(f"    ✗ {text}")

        if len(combined_text) < 200:
            board_results[bid] = {"status": "insufficient_data"}
            continue

        prompt = f"Job board: {name}\nSearch results:\n\n{combined_text[:8000]}"
        result = call_ollama(SYSTEM_PROMPT_JOBS, prompt)
        if result:
            try:
                result = result.strip()
                if result.startswith("```"):
                    result = re.sub(r'^```\w*\n?', '', result)
                    result = re.sub(r'\n?```$', '', result)
                jobs = json.loads(result)
                if isinstance(jobs, list):
                    for j in jobs:
                        j["source_board"] = bid
                    all_jobs.extend(jobs)
                    print(f"    ✓ Found {len(jobs)} potential matches")
            except (json.JSONDecodeError, ValueError):
                print(f"    ✗ JSON parse error")

        board_results[bid] = {
            "status": "ok",
            "chars": len(combined_text),
            "jobs_found": len([j for j in all_jobs if j.get("source_board") == bid]),
        }

    return all_jobs, board_results


def scan_intel_sources():
    """Scan company intelligence sources."""
    print("\n  === Scanning company intel ===")
    combined_text = ""
    intel_results = {}

    for sid, source in INTEL_SOURCES.items():
        name = source["name"]
        for url in source["urls"]:
            print(f"  [{name}] Fetching: {url[:80]}...")
            text = fetch_page(url)
            if not text.startswith("[fetch_error"):
                combined_text += f"\n=== {name}: {url} ===\n{text[:3000]}\n"
                print(f"    Got {len(text)} chars")
            else:
                print(f"    ✗ {text}")

        intel_results[sid] = {"status": "ok" if len(combined_text) > 200 else "insufficient"}

    if len(combined_text) < 300:
        return None, intel_results

    prompt = f"Company intelligence data:\n\n{combined_text[:10000]}"
    result = call_ollama(SYSTEM_PROMPT_INTEL, prompt)
    intel_data = None
    if result:
        try:
            result = result.strip()
            if result.startswith("```"):
                result = re.sub(r'^```\w*\n?', '', result)
                result = re.sub(r'\n?```$', '', result)
            intel_data = json.loads(result)
        except (json.JSONDecodeError, ValueError):
            print(f"    ✗ Intel JSON parse error")

    return intel_data, intel_results


def deduplicate_jobs(jobs):
    """Remove duplicate job listings based on title+company similarity."""
    seen = set()
    unique = []
    for j in jobs:
        key = f"{j.get('company', '').lower()[:20]}|{j.get('title', '').lower()[:40]}"
        if key not in seen:
            seen.add(key)
            unique.append(j)
    return unique


def generate_summary(jobs, intel_data, scan_meta):
    """Use LLM to generate a human-readable career briefing."""
    hot_jobs = [j for j in jobs if j.get("match_score", 0) >= 70]
    good_jobs = [j for j in jobs if 40 <= j.get("match_score", 0) < 70]
    remote_jobs = [j for j in jobs if j.get("remote_compatible", False)]

    parts = [f"Scan date: {datetime.now().strftime('%A, %d %B %Y')}"]
    parts.append(f"Companies scanned: {scan_meta.get('companies_scanned', 0)}")
    parts.append(f"Pages fetched: {scan_meta.get('pages_fetched', 0)}")
    parts.append(f"Total matches: {len(jobs)} (hot: {len(hot_jobs)}, good: {len(good_jobs)})")
    parts.append(f"Remote-compatible: {len(remote_jobs)}")

    if hot_jobs:
        parts.append("\n=== HOT MATCHES (score >= 70) ===")
        for j in sorted(hot_jobs, key=lambda x: -x.get("match_score", 0)):
            parts.append(f"- [{j.get('match_score', 0)}%] {j.get('title', '?')} at {j.get('company', '?')}")
            parts.append(f"  Location: {j.get('location', '?')} | Remote: {j.get('remote_compatible', '?')}")
            parts.append(f"  Why: {', '.join(j.get('match_reasons', []))}")
            if j.get("salary_hint"):
                parts.append(f"  Salary: {j['salary_hint']}")

    if good_jobs:
        parts.append(f"\n=== GOOD MATCHES ({len(good_jobs)} jobs, score 40-69) ===")
        for j in sorted(good_jobs, key=lambda x: -x.get("match_score", 0))[:10]:
            parts.append(f"- [{j.get('match_score', 0)}%] {j.get('title', '?')} at {j.get('company', '?')}")

    if intel_data:
        parts.append("\n=== COMPANY INTELLIGENCE ===")
        parts.append(json.dumps(intel_data, indent=2)[:2000])

    prompt = "\n".join(parts)
    summary = call_ollama(SYSTEM_PROMPT_SUMMARY, prompt, max_tokens=2000)
    return summary or "\n".join(parts)


# ─── Signal alerts for hot matches ───

def send_hot_alerts(jobs, intel_data):
    """Send Signal notifications for very hot matches and urgent intel."""
    hot_jobs = [j for j in jobs if j.get("match_score", 0) >= 80 and j.get("remote_compatible", False)]
    urgent_intel = []
    if intel_data and "alerts" in intel_data:
        urgent_intel = [a for a in intel_data["alerts"] if a.get("severity") == "urgent"]

    alerts_sent = 0

    for j in hot_jobs[:3]:  # Max 3 job alerts per scan
        msg = (
            f"🎯 HOT JOB MATCH ({j.get('match_score', 0)}%)\n"
            f"{j.get('title', '?')} @ {j.get('company', '?')}\n"
            f"📍 {j.get('location', '?')}\n"
            f"✅ {', '.join(j.get('match_reasons', [])[:3])}\n"
            f"💰 {j.get('salary_hint', 'not disclosed')}\n"
            f"🔗 Check dashboard → career.html"
        )
        if signal_send(msg):
            alerts_sent += 1
            print(f"  📡 Signal alert sent: {j.get('title', '?')}")

    for a in urgent_intel[:2]:  # Max 2 intel alerts
        msg = (
            f"⚠️ CAREER ALERT: {a.get('type', 'unknown').upper()}\n"
            f"{a.get('company', '?')}: {a.get('summary', '?')}\n"
            f"{a.get('details', '')[:200]}"
        )
        if signal_send(msg):
            alerts_sent += 1
            print(f"  📡 Signal alert sent: {a.get('summary', '?')}")

    return alerts_sent


# ─── Main ───

def main():
    quick = "--quick" in sys.argv
    signal_test = "--signal-test" in sys.argv

    if signal_test:
        ok = signal_send("🧪 Career scanner test — Signal notifications working!")
        print("Signal test:", "OK" if ok else "FAILED")
        return

    quiet = is_quiet_hours()
    mode_str = f"{'quick' if quick else 'full'}, {'QUIET HOURS' if quiet else 'daytime'}"
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] career-scan starting ({mode_str})")

    # Guard: don't compete with other batch scripts for GPU
    import subprocess
    for proc in ["lore-digest.sh", "repo-watch.sh", "idle-think.sh", "ha-journal.py"]:
        try:
            r = subprocess.run(["pgrep", "-f", proc], capture_output=True, timeout=5)
            if r.returncode == 0:
                print(f"  {proc} is running — skipping")
                return
        except Exception:
            pass

    # GPU guard — during quiet hours (00-06) we own the GPU, skip guard entirely
    if not quiet:
        try:
            req = urllib.request.Request(f"{OLLAMA_URL}/api/ps")
            resp = urllib.request.urlopen(req, timeout=5)
            ps = json.loads(resp.read())
            for m in ps.get("models", []):
                name = m.get("name", "")
                if name and name != OLLAMA_MODEL:
                    from datetime import timezone
                    expires = m.get("expires_at", "")
                    if expires:
                        try:
                            exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                            remaining = (exp_dt - datetime.now(timezone.utc)).total_seconds()
                            if remaining > 25 * 60:
                                print(f"  {name} recently active ({remaining/60:.0f}m left) — skipping")
                                return
                        except Exception:
                            pass
                    print(f"  {name} warm/cached — will evict for batch job")
        except Exception:
            pass
    else:
        print("  Quiet hours — GPU free for batch, no chat guard needed")

    scan_start = time.time()

    # Phase 1: Career pages
    career_jobs, career_results = scan_career_pages()

    # Phase 2: Job boards
    board_jobs, board_results = scan_job_boards()

    # Phase 3: Company intel (skip in quick mode)
    intel_data = None
    intel_results = {}
    if not quick:
        intel_data, intel_results = scan_intel_sources()

    # Combine and deduplicate
    all_jobs = deduplicate_jobs(career_jobs + board_jobs)
    all_jobs.sort(key=lambda x: -x.get("match_score", 0))

    scan_duration = time.time() - scan_start

    scan_meta = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "mode": "quick" if quick else "full",
        "duration_seconds": round(scan_duration),
        "companies_scanned": len(career_results),
        "boards_scanned": len(board_results),
        "pages_fetched": sum(1 for r in career_results.values() if r.get("status") == "ok")
                       + sum(1 for r in board_results.values() if r.get("status") == "ok"),
        "total_jobs_found": len(all_jobs),
        "hot_matches": len([j for j in all_jobs if j.get("match_score", 0) >= 70]),
        "remote_compatible": len([j for j in all_jobs if j.get("remote_compatible", False)]),
    }

    print(f"\n  === Results ===")
    print(f"  Jobs found: {len(all_jobs)}")
    print(f"  Hot matches (>=70): {scan_meta['hot_matches']}")
    print(f"  Remote-compatible: {scan_meta['remote_compatible']}")
    print(f"  Duration: {scan_duration:.0f}s")

    # Generate LLM summary
    print("\n  === Generating summary ===")
    summary = generate_summary(all_jobs, intel_data, scan_meta)

    # Save scan data
    scan_data = {
        "meta": scan_meta,
        "jobs": all_jobs[:50],  # Keep top 50
        "intel": intel_data,
        "source_results": {
            "career_pages": career_results,
            "job_boards": board_results,
            "intel_sources": intel_results,
        },
        "summary": summary,
    }
    save_scan(scan_data)
    print(f"  Saved to {CAREER_DIR}/latest-scan.json")

    # Save as think-system note
    save_note(
        f"Career Scan — {datetime.now().strftime('%d %b %Y')}",
        summary,
        context=scan_meta,
    )
    print(f"  Saved think note")

    # Send Signal alerts for hot matches
    alerts = send_hot_alerts(all_jobs, intel_data)
    if alerts:
        print(f"  Sent {alerts} Signal alert(s)")

    print(f"\n  Done in {scan_duration:.0f}s.")


if __name__ == "__main__":
    main()
