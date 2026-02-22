#!/usr/bin/env python3
"""career-scan.py — Automated OSINT career intelligence scanner (multi-pass).

Two-phase scanning architecture to prevent LLM hallucination:
  Phase 1 — EXTRACTION: List job titles/URLs from pages (no candidate profile)
  Phase 2 — ANALYSIS: Analyze each job individually against candidate profile

This separation ensures the LLM cannot fabricate jobs by mixing the candidate's
skills into job requirements.  The extraction phase has NO knowledge of the
candidate, and the analysis phase sees only ONE real job posting at a time.

Targets:
  - Direct career pages: Nvidia, Google, AMD, Intel, Samsung, Amazon,
    TCL Research Europe, Harman, Qualcomm, Arm, Ericsson, Fujitsu, Thales
  - Software houses: Codelime, SII, GlobalLogic, Sysgo (dashboard only, no Signal)
  - Job aggregators: LinkedIn, nofluffjobs.com
  - Company intel: gowork.pl, layoffs.fyi, levels.fyi, HN Algolia, nofluff salaries

Filters:
  - Remote-from-Poland OR hybrid in Łódź/Warsaw
  - Kernel, drivers, embedded, camera, V4L2, MIPI, ISP, BSP, SoC
  - Silicon / automotive / consumer electronics industry

Usage:
    career-scan.py                  (full scan — all sources)
    career-scan.py --quick          (career pages + boards, skip intel)
    career-scan.py --signal-test    (send test notification)

Schedule (cron): nightly at 01:00 during quiet hours (23:00-08:00)
    0 1 * * *  flock -w 2400 /tmp/career-scan.lock /usr/bin/python3 /opt/netscan/career-scan.py

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
OLLAMA_MODEL = "phi4:14b"  # Microsoft Phi-4 -- English-only, batch runs during quiet hours

QUIET_START = 23  # 23:00
QUIET_END   = 8   # 08:00  — no chat, GPU free for batch jobs

def is_quiet_hours():
    """True if we're in the 23:00-08:00 quiet window (no Signal chat)."""
    h = datetime.now().hour
    # Spans midnight: 23:00 → 08:00
    return h >= QUIET_START or h < QUIET_END

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
            "https://www.google.com/about/careers/applications/jobs/results/?location=Poland&q=linux%20kernel%20driver",
            "https://www.google.com/about/careers/applications/jobs/results/?location=Poland&q=embedded%20software%20camera",
        ],
        "keywords": ["kernel", "driver", "linux", "chromeos", "camera", "pixel", "embedded", "firmware"],
        "industry": "silicon",
        "fetcher": "google_careers",
    },
    "amd": {
        "name": "AMD",
        "career_urls": [
            "https://careers.amd.com/careers/SearchJobs",
        ],
        "keywords": ["kernel", "driver", "linux", "gpu", "rdna", "rocm", "embedded", "firmware"],
        "industry": "silicon",
    },
    "intel": {
        "name": "Intel",
        "career_urls": [
            "https://intel.wd1.myworkdayjobs.com/en-US/External?q=linux+kernel+driver&locationCountry=ccd3a10a0e81473fa33cc8e77a452b8c",
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
            "https://www.amazon.jobs/en/search.json?base_query=linux+kernel+driver&loc_query=Poland&country=POL&result_limit=25",
            "https://www.amazon.jobs/en/search.json?base_query=embedded+software&loc_query=Poland&country=POL&result_limit=25",
        ],
        "keywords": ["kernel", "driver", "linux", "embedded", "camera", "ring", "alexa", "firmware"],
        "industry": "tech",
        "fetcher": "amazon_api",
    },
    "tcl": {
        "name": "TCL Research Europe",
        "career_urls": [
            "https://tcl-research.pl/careers/",
        ],
        "keywords": ["linux", "driver", "camera", "video", "AI", "embedded", "computer vision"],
        "industry": "consumer_electronics",
    },
    "harman": {
        "name": "HARMAN International (Employer)",
        "career_urls": [
            "https://jobsearch.harman.com/en_US/careers/SearchJobs?202=59341&524=3944&524_format=1483&listFilterMode=1&jobSort=relevancy",
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
    "ericsson": {
        "name": "Ericsson",
        "career_urls": [
            "https://jobs.ericsson.com/careers?query=linux+embedded&location=Poland",
        ],
        "keywords": ["linux", "embedded", "kernel", "driver", "5G", "RAN", "firmware", "telecom"],
        "industry": "telecom",
    },
    "fujitsu": {
        "name": "Fujitsu",
        "career_urls": [
            "https://edzt.fa.em4.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber=CX,limit=100,sortBy=POSTING_DATES_DESC",
            "https://www.fujitsu.com/pl/about/careers/",
        ],
        "keywords": ["linux", "embedded", "kernel", "driver", "HPC", "firmware", "ARM"],
        "industry": "tech",
        "fetcher": "fujitsu_api",
    },
    "thales": {
        "name": "Thales (TNS/Defence)",
        "career_urls": [
            "https://careers.thalesgroup.com/global/en/search-results?keywords=linux+embedded",
        ],
        "keywords": ["linux", "embedded", "kernel", "driver", "defence", "security", "firmware", "RTOS"],
        "industry": "defence",
    },
    # ── Software houses ── no Signal alerts, dashboard only ──
    "codelime": {
        "name": "Codelime",
        "career_urls": [
            "https://codelime.com/careers/",
        ],
        "keywords": ["linux", "embedded", "driver", "firmware", "C", "kernel"],
        "industry": "software_house",
        "software_house": True,
    },
    "sii": {
        "name": "SII Poland",
        "career_urls": [
            "https://sii.pl/en/job-offers/",
        ],
        "keywords": ["linux", "embedded", "driver", "firmware", "kernel", "C", "automotive"],
        "industry": "software_house",
        "software_house": True,
    },
    "globallogic": {
        "name": "GlobalLogic",
        "career_urls": [
            "https://www.globallogic.com/career-search-page/?keywords=linux&experience=experienced-professional&regions=europe",
        ],
        "keywords": ["linux", "embedded", "driver", "kernel", "firmware", "automotive"],
        "industry": "software_house",
        "software_house": True,
    },
    "sysgo": {
        "name": "Sysgo (RTOS/Hypervisor)",
        "career_urls": [
            "https://www.sysgo.com/careers",
        ],
        "keywords": ["linux", "embedded", "RTOS", "hypervisor", "PikeOS", "kernel", "driver", "safety"],
        "industry": "software_house",
        "software_house": True,
    },
}

# ─── Job boards & aggregators ───

JOB_BOARDS = {
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
        "fetcher": "gowork_reviews",
        "urls": [
            # OLD opinie_czytaj format — server-rendered, scrapeable
            # (new /opinie/ SPA format is client-rendered, returns no reviews)
            "https://www.gowork.pl/opinie_czytaj,8528",         # Ericsson (Warszawa)
            "https://www.gowork.pl/opinie_czytaj,21451047",     # Samsung Electronics Polska
            "https://www.gowork.pl/opinie_czytaj,1036892",      # Harman Connected Services
            "https://www.gowork.pl/opinie_czytaj,930747",       # Intel Technology Poland
            "https://www.gowork.pl/opinie_czytaj,21622584",     # Nvidia Poland
            "https://www.gowork.pl/opinie_czytaj,20727487",     # Qualcomm Wireless Business Solutions
            "https://www.gowork.pl/opinie_czytaj,26904732",     # AMD Poland
            "https://www.gowork.pl/opinie_czytaj,949234",       # Google Poland
            "https://www.gowork.pl/opinie_czytaj,23971017",     # Arm Poland
            "https://www.gowork.pl/opinie_czytaj,365816",       # Fujitsu Technology Solutions
            "https://www.gowork.pl/opinie_czytaj,239192",       # Thales DIS Polska
            "https://www.gowork.pl/opinie_czytaj,1026920",      # Amazon Development Center
            "https://www.gowork.pl/opinie_czytaj,23966243",     # TCL Research Europe
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
            "https://www.levels.fyi/t/hardware-engineer/locations/poland",
            "https://www.levels.fyi/t/embedded-firmware-engineer/locations/poland",
        ],
    },
    "hn": {
        "name": "Hacker News (job market)",
        "urls": [
            "https://hn.algolia.com/api/v1/search?query=embedded+linux+kernel&tags=comment&hitsPerPage=20&numericFilters=created_at_i>RECENT_EPOCH",
            "https://hn.algolia.com/api/v1/search?query=who+is+hiring+embedded&tags=comment&hitsPerPage=15&numericFilters=created_at_i>RECENT_EPOCH",
            "https://hn.algolia.com/api/v1/search?query=remote+embedded+engineer+europe&tags=comment&hitsPerPage=10&numericFilters=created_at_i>RECENT_EPOCH",
        ],
        "fetcher": "hn_api",
    },
    "nofluff_salaries": {
        "name": "nofluffjobs.com salary data",
        "urls": [
            "https://nofluffjobs.com/api/posting?category=embedded",
            "https://nofluffjobs.com/api/posting?keyword=linux+kernel",
        ],
        "fetcher": "nofluff_salary_api",
    },
    "fourprog": {
        "name": "4programmers.net",
        "urls": [
            "https://4programmers.net/Praca?q=embedded",
            "https://4programmers.net/Praca?q=linux+kernel",
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
        t0 = time.time()
        resp = urllib.request.urlopen(req, timeout=timeout)
        elapsed = time.time() - t0
        http_code = resp.getcode()
        raw = resp.read().decode("utf-8", errors="replace")
        # Strip scripts, styles, HTML tags
        text = re.sub(r'<script[^>]*>.*?</script>', '', raw, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        fetch_page._last_health = {"http_code": http_code, "response_time": round(elapsed, 2)}
        return text
    except Exception as ex:
        fetch_page._last_health = {"http_code": 0, "response_time": 0, "error": str(ex)[:120]}
        return f"[fetch_error: {ex}]"

fetch_page._last_health = {}


def fetch_amazon_api(url, timeout=25):
    """Fetch Amazon Jobs JSON API and convert to LLM-readable text."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "application/json",
        })
        t0 = time.time()
        resp = urllib.request.urlopen(req, timeout=timeout)
        elapsed = time.time() - t0
        http_code = resp.getcode()
        raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        jobs = data.get("jobs", [])
        hits = data.get("hits", 0)
        lines = [f"Amazon Jobs API — {hits} results:"]
        for j in jobs:
            lines.append(f"\nTitle: {j.get('title', '?')}")
            job_path = j.get('job_path', '')
            if job_path:
                lines.append(f"URL: https://www.amazon.jobs{job_path}")
            lines.append(f"Location: {j.get('location', '?')} ({j.get('city', '?')})")
            lines.append(f"Posted: {j.get('posted_date', '?')}")
            lines.append(f"Team: {j.get('team', {}).get('label', '?') if isinstance(j.get('team'), dict) else j.get('team', '?')}")
            lines.append(f"Category: {j.get('job_category', '?')}")
            lines.append(f"Schedule: {j.get('job_schedule_type', '?')}")
            desc = (j.get('description_short', '') or '')[:300]
            lines.append(f"Description: {desc}")
            quals = (j.get('basic_qualifications', '') or '')[:300]
            lines.append(f"Qualifications: {quals}")
            pref = (j.get('preferred_qualifications', '') or '')[:200]
            if pref:
                lines.append(f"Preferred: {pref}")
        text = "\n".join(lines)
        fetch_page._last_health = {"http_code": http_code, "response_time": round(elapsed, 2)}
        return text
    except Exception as ex:
        fetch_page._last_health = {"http_code": 0, "response_time": 0, "error": str(ex)[:120]}
        return f"[fetch_error: {ex}]"


def fetch_fujitsu_api(url, timeout=25):
    """Fetch Fujitsu Oracle HCM API and convert to LLM-readable text."""
    if "oraclecloud.com" not in url:
        return fetch_page(url, timeout)
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "application/json",
        })
        t0 = time.time()
        resp = urllib.request.urlopen(req, timeout=timeout)
        elapsed = time.time() - t0
        http_code = resp.getcode()
        raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        items = data.get("items", [{}])
        reqs = items[0].get("requisitionList", []) if items else []
        lines = [f"Fujitsu Oracle HCM Jobs — {len(reqs)} total positions:"]
        for r in reqs:
            loc = r.get("PrimaryLocation", "")
            title = r.get("Title", "")
            posted = r.get("PostedDate", "")
            workplace = r.get("WorkplaceTypeCode", "")
            req_id = r.get("Id", "")
            lines.append(f"\nTitle: {title}")
            if req_id:
                lines.append(f"URL: https://edzt.fa.em4.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/jobs/{req_id}")
            lines.append(f"Location: {loc}")
            lines.append(f"Posted: {posted}")
            if workplace:
                lines.append(f"Workplace: {workplace}")
            sec_locs = r.get("secondaryLocations", [])
            if sec_locs:
                sec_names = [sl.get("Name", "") for sl in sec_locs]
                lines.append(f"Also: {', '.join(sec_names)}")
        text = "\n".join(lines)
        fetch_page._last_health = {"http_code": http_code, "response_time": round(elapsed, 2)}
        return text
    except Exception as ex:
        fetch_page._last_health = {"http_code": 0, "response_time": 0, "error": str(ex)[:120]}
        return f"[fetch_error: {ex}]"


def fetch_google_careers(url, timeout=25):
    """Fetch Google Careers and extract job data from AF_initDataCallback."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "identity",
        })
        t0 = time.time()
        resp = urllib.request.urlopen(req, timeout=timeout)
        elapsed = time.time() - t0
        http_code = resp.getcode()
        raw = resp.read().decode("utf-8", errors="replace")
        fetch_page._last_health = {"http_code": http_code, "response_time": round(elapsed, 2)}

        marker = "AF_initDataCallback({key: 'ds:1'"
        idx = raw.find(marker)
        if idx >= 0:
            data_start = raw.find('data:', idx) + 5
            depth = 0
            end = data_start
            for ch in raw[data_start:data_start + 80000]:
                if ch == '[': depth += 1
                elif ch == ']':
                    depth -= 1
                    if depth == 0: break
                end += 1
            data_str = raw[data_start:end + 1]

            if len(data_str) > 100:
                entries = re.findall(r'\["(\d{15,25})","([^"]+)","([^"]+)"', data_str)
                if entries:
                    locations = re.findall(
                        r'"((?:Warsaw|Krak[oó]w|Wroc[łl]aw|Gda[nń]sk|[ŁL][oó]d[zź]|Poland|Remote)[^"]{0,40})"',
                        data_str, re.I)
                    loc_str = ", ".join(sorted(set(locations[:5]))) if locations else "Poland"
                    lines = [f"Google Careers — {len(entries)} jobs found (location: {loc_str}):"]
                    for jid, title, job_url in entries:
                        job_idx = data_str.find(jid)
                        chunk = data_str[job_idx:job_idx + 3000] if job_idx >= 0 else ""
                        jloc = re.findall(r'"((?:Warsaw|Krak|Wroc|Gda|Poland|Remote)[^"]{0,40})"', chunk, re.I)
                        jloc_str = jloc[0] if jloc else loc_str
                        descs = re.findall(r'"([^"]{50,300})"', chunk)
                        desc = descs[0] if descs else ""
                        lines.append(f"\nTitle: {title}")
                        lines.append(f"URL: {job_url}")
                        lines.append(f"Location: {jloc_str}")
                        if desc:
                            lines.append(f"Details: {desc[:200]}")
                    return "\n".join(lines)

        # Fallback: standard HTML strip
        text = re.sub(r'<script[^>]*>.*?</script>', '', raw, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text
    except Exception as ex:
        fetch_page._last_health = {"http_code": 0, "response_time": 0, "error": str(ex)[:120]}
        return f"[fetch_error: {ex}]"


def fetch_hn_api(url, timeout=25):
    """Fetch Hacker News Algolia API and convert to LLM-readable text."""
    epoch_6mo = int(time.time()) - 180 * 86400
    url = url.replace("RECENT_EPOCH", str(epoch_6mo))
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "application/json",
        })
        t0 = time.time()
        resp = urllib.request.urlopen(req, timeout=timeout)
        elapsed = time.time() - t0
        http_code = resp.getcode()
        raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        hits = data.get("hits", [])
        nb_hits = data.get("nbHits", 0)
        lines = [f"Hacker News discussions — {nb_hits} total hits, showing {len(hits)}:"]
        for h in hits:
            text = h.get("comment_text", "") or h.get("story_text", "")
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            author = h.get("author", "?")
            created = h.get("created_at", "?")[:10]
            story = h.get("story_title", "")
            lines.append(f"\n[{created}] by {author}" + (f" in: {story}" if story else ""))
            lines.append(text[:400])
        text = "\n".join(lines)
        fetch_page._last_health = {"http_code": http_code, "response_time": round(elapsed, 2)}
        return text
    except Exception as ex:
        fetch_page._last_health = {"http_code": 0, "response_time": 0, "error": str(ex)[:120]}
        return f"[fetch_error: {ex}]"


def fetch_nofluff_salary_api(url, timeout=25):
    """Fetch NoFluffJobs posting API and extract salary data for LLM analysis."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "application/json",
        })
        t0 = time.time()
        resp = urllib.request.urlopen(req, timeout=timeout)
        elapsed = time.time() - t0
        http_code = resp.getcode()
        raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        postings = data.get("postings", [])
        total = data.get("totalCount", 0)
        relevant = []
        for p in postings:
            title = p.get("title", "")
            title_lower = title.lower()
            cats = p.get("category", "")
            if any(k in title_lower for k in ["embedded", "linux", "driver", "kernel",
                            "firmware", "c++", "c/c", "bsp", "rtos", "fpga", "hw ", "hardware"]):
                relevant.append(p)
            elif isinstance(cats, str) and "embedded" in cats.lower():
                relevant.append(p)
        lines = [f"NoFluffJobs salary data — {total} total postings, {len(relevant)} embedded/Linux relevant:"]
        for p in relevant[:25]:
            sal = p.get("salary", {})
            seniority = ",".join(p.get("seniority", []))
            title = p.get("title", "?")
            company = p.get("name", "?")
            sal_from = sal.get("from", "?")
            sal_to = sal.get("to", "?")
            sal_type = sal.get("type", "?")
            sal_curr = sal.get("currency", "PLN")
            lines.append(f"\n[{seniority}] {title} @ {company}")
            lines.append(f"  Salary: {sal_type} {sal_from}-{sal_to} {sal_curr}/month")
        text = "\n".join(lines)
        fetch_page._last_health = {"http_code": http_code, "response_time": round(elapsed, 2)}
        return text
    except Exception as ex:
        fetch_page._last_health = {"http_code": 0, "response_time": 0, "error": str(ex)[:120]}
        return f"[fetch_error: {ex}]"


def fetch_gowork_reviews(url, timeout=25):
    """Fetch GoWork.pl old-style opinie_czytaj page and extract reviews.

    The NEW /opinie/ URLs are Next.js SPAs that render client-side only.
    The OLD /opinie_czytaj,{id} format returns server-rendered HTML with
    actual review content, ratings, and dates — fully scrapeable.
    """
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Accept-Language": "pl,en;q=0.9",
        })
        t0 = time.time()
        resp = urllib.request.urlopen(req, timeout=timeout)
        elapsed = time.time() - t0
        http_code = resp.getcode()
        raw = resp.read().decode("utf-8", errors="replace")
        fetch_page._last_health = {"http_code": http_code, "response_time": round(elapsed, 2)}

        # Extract company name from title: "Opinie Company City - N opinii - GoWork.pl"
        title_m = re.search(r'<title>(.*?)</title>', raw)
        title = title_m.group(1) if title_m else ""
        company_m = re.match(r'Opinie\s+(.+?)(?:\s+-\s+\d+\s+opini|\s+-\s+GoWork)', title)
        company_name = company_m.group(1).strip() if company_m else "Unknown"

        # Strip scripts/styles, then split into text lines
        text = re.sub(r'<script[^>]*>.*?</script>', ' ', raw, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', '\n', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        lines = [l.strip() for l in text.split('\n') if l.strip()]

        # Extract rating and review count from meta/title
        rating_m = re.search(r'(\d+[.,]\d+)\s*/\s*5', ' '.join(lines[:50]))
        rating = rating_m.group(1).replace(',', '.') if rating_m else "?"
        count_m = re.search(r'Opinie\s*\(\s*(\d+)', ' '.join(lines[:50]))
        review_count = count_m.group(1) if count_m else "?"

        # Extract individual reviews: date + role + text
        reviews = []
        date_pattern = re.compile(r'^(\d{1,2}\.\d{1,2}\.\d{4})\s+(\d{1,2}:\d{2})$')
        role_keywords = {"Pracownik", "Były pracownik", "Kandydat", "Inne", "Pytanie"}
        i = 0
        while i < len(lines):
            dm = date_pattern.match(lines[i])
            if dm:
                date_str = dm.group(1)
                role = ""
                review_text = []
                j = i + 1
                # Next line(s) might be role type
                if j < len(lines) and lines[j] in role_keywords:
                    role = lines[j]
                    j += 1
                # Skip '@' reply indicators
                if j < len(lines) and lines[j] == "@":
                    j += 1
                if j < len(lines) and lines[j] in role_keywords:
                    j += 1
                # Collect review text until next date or stopper
                while j < len(lines):
                    if date_pattern.match(lines[j]):
                        break
                    if lines[j] in ("Odpowiedz", "Zgłoś sygnał"):
                        j += 1
                        continue
                    # Skip short numeric-only lines (vote counts)
                    if re.match(r'^\d{1,3}$', lines[j]):
                        j += 1
                        continue
                    review_text.append(lines[j])
                    j += 1
                    if len(review_text) > 5:
                        break
                body = " ".join(review_text).strip()
                if body and len(body) > 10:
                    reviews.append({"date": date_str, "role": role, "text": body[:500]})
                i = j
            else:
                i += 1

        # Build LLM-readable output
        out = [f"GoWork.pl — {company_name}"]
        out.append(f"Rating: {rating}/5 | Reviews: {review_count} | URL: {url}")
        out.append(f"Recent reviews ({len(reviews)} extracted):")
        for r in reviews[:15]:
            out.append(f"\n[{r['date']}] ({r['role']}) {r['text']}")

        return "\n".join(out)
    except Exception as ex:
        fetch_page._last_health = {"http_code": 0, "response_time": 0, "error": str(ex)[:120]}
        return f"[fetch_error: {ex}]"


# Registry of custom fetchers
API_FETCHERS = {
    "amazon_api": fetch_amazon_api,
    "fujitsu_api": fetch_fujitsu_api,
    "google_careers": fetch_google_careers,
    "hn_api": fetch_hn_api,
    "nofluff_salary_api": fetch_nofluff_salary_api,
    "gowork_reviews": fetch_gowork_reviews,
}


def call_ollama(system_prompt, user_prompt, temperature=0.3, max_tokens=4000):
    """Call local Ollama for analysis with retry on 500 errors."""
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        models = [m["name"] for m in data.get("models", [])]
        if OLLAMA_MODEL not in models:
            print(f"    Model {OLLAMA_MODEL} not found")
            return None
    except Exception as ex:
        print(f"    Ollama not reachable: {ex}")
        return None

    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": 6144},
        "keep_alive": "60m",
    })

    for attempt in range(2):
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
            print(f"    LLM: {elapsed:.0f}s, {tokens} tok ({tps:.1f} t/s)")
            return content
        except urllib.error.HTTPError as ex:
            if ex.code == 500 and attempt == 0:
                print(f"    LLM 500 error, retrying after model reload wait...")
                time.sleep(45)  # wait for model to reload
                continue
            print(f"    LLM failed: {ex}")
            return None
        except Exception as ex:
            print(f"    LLM failed: {ex}")
            return None
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


def extract_json_array(text):
    """Robustly extract a JSON array from LLM output."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r'^```\w*\n?', '', text)
        text = re.sub(r'\n?```$', '', text)
        text = text.strip()
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except (json.JSONDecodeError, ValueError):
        pass
    start = text.find("[")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == '\\':
            escape = True
            continue
        if c == '"' and not escape:
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i+1])
                except (json.JSONDecodeError, ValueError):
                    return None
    return None


def extract_json_object(text):
    """Robustly extract a JSON object from LLM output."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r'^```\w*\n?', '', text)
        text = re.sub(r'\n?```$', '', text)
        text = text.strip()
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        pass
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == '\\':
            escape = True
            continue
        if c == '"' and not escape:
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i+1])
                except (json.JSONDecodeError, ValueError):
                    return None
    return None


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
    path = os.path.join(CAREER_DIR, "latest-scan.json")
    with open(path, "w") as f:
        json.dump(scan_data, f, indent=2)

    dt = datetime.now().strftime("%Y%m%d-%H%M")
    archive_path = os.path.join(CAREER_DIR, f"scan-{dt}.json")
    with open(archive_path, "w") as f:
        json.dump(scan_data, f, indent=2)

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


# ─── Profile matching keywords (for pre-filtering, NOT sent to extraction LLM) ───

MUST_MATCH_ANY = [
    "kernel", "driver", "embedded", "firmware", "bsp", "linux",
    "v4l2", "camera", "mipi", "csi", "isp", "soc", "dma",
    "device tree", "devicetree", "iommu", "i2c", "spi", "pcie",
    "gstreamer", "libcamera", "drm", "kms", "gpu", "vulkan",
    "adas", "automotive", "sensor driver", "imaging pipeline",
    "low-level", "bare-metal", "rtos", "bootloader", "u-boot",
    "fpga",
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


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1 — EXTRACTION (no candidate profile — prevents hallucination)
# ═══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT_EXTRACT = """\
You are a job listing extractor. Your ONLY task is to identify and list job
postings that appear on this page.

STRICT RULES — FOLLOW EXACTLY:
1. Extract ONLY jobs that are EXPLICITLY listed on the page with a visible title.
2. Do NOT invent, fabricate, or add ANY jobs that are not clearly on the page.
3. Do NOT guess at requirements, skills, or technologies — only title/URL/location.
4. If the page is a company overview with no specific job listings, output: []
5. If the page shows "0 results" or "no jobs found", output: []
6. Copy job titles EXACTLY as they appear — do not modify or "improve" them.

For each job found on the page, output:
{
  "title": "exact job title as shown on page (do not modify)",
  "url": "full job URL if visible on page, otherwise empty string",
  "location": "location if stated on page, otherwise empty string",
  "snippet": "brief description if visible (max 200 chars), otherwise empty string"
}

Output ONLY a valid JSON array. No markdown, no explanation, no commentary.
If no job listings found, output: []
"""


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2 — INDIVIDUAL JOB ANALYSIS (one job at a time, with candidate profile)
# ═══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT_ANALYZE = """\
You are analyzing a SINGLE, SPECIFIC job posting for a candidate.

CANDIDATE PROFILE:
- Principal Embedded Software Engineer, 15 years experience
- Specializes in: Linux kernel camera drivers (V4L2, MIPI CSI-2, ISP, libcamera),
  SoC BSP development, automotive ADAS imaging
- Located in Łódź, Poland

═══ ANTI-HALLUCINATION RULES (CRITICAL — READ CAREFULLY) ═══
• Base your analysis ONLY on what the job posting text actually says.
• For key_requirements: list ONLY skills/requirements that are EXPLICITLY mentioned
  in the posting text. If the posting says "Linux kernel development", write that —
  do NOT add "V4L2, MIPI CSI-2" unless the posting LITERALLY says those words.
• If the posting is vague or minimal (just a title, no details), give a LOWER
  match_score (40-55) and note "minimal posting, requirements unclear" in reasons.
• Do NOT project the candidate's skills onto the job. The job requirements come
  from the POSTING TEXT, NOT from the candidate profile.
• A posting that says "embedded Linux" does NOT automatically require V4L2/camera.
• NEVER add technologies to key_requirements that aren't in the posting text.

Output a JSON object:
{
  "title": "Job title (from the posting, not modified)",
  "company": "ACTUAL EMPLOYER company name (NOT the job board). E.g. if posting says 'Dell' on nofluffjobs, company is 'Dell'.",
  "location": "Location / remote status as stated in posting",
  "match_score": 0-100,
  "match_reasons": ["reason1 based on ACTUAL posting content", "reason2"],
  "key_requirements": ["req1 FROM POSTING TEXT", "req2 FROM POSTING TEXT"],
  "job_url": "URL to this specific job posting",
  "remote_details": "fully remote / hybrid city / on-site city / unclear",
  "remote_compatible": true/false,
  "remote_feasibility": "for non-EMEA jobs: analysis; for EMEA jobs: empty string",
  "salary_b2b_net_pln": "range PLN net/month if stated in offer, else null",
  "salary_uop_gross_pln": "range PLN gross/month if stated in offer, else null",
  "salary_has_zus_akup": true/false/null,
  "salary_source": "from_offer" or null,
  "salary_note": "salary info from offer, or empty string",
  "via_software_house": false,
  "red_flags": ["if any"]
}

SCORING RULES — be honest, not optimistic:
  90-100: Posting EXPLICITLY mentions V4L2 or camera drivers or MIPI AND remote-compatible
  80-89:  Posting mentions Linux kernel drivers AND camera/media/multimedia explicitly
  70-79:  Posting requires embedded Linux kernel work (drivers, BSP, etc.)
  55-69:  Embedded/firmware but different domain (networking, storage, telecom)
  40-54:  General embedded/Linux/C but not specifically kernel-level
  <40:    Not relevant — do not output

  VAGUE/MINIMAL posting = LOWER score. Uncertainty → caution, not inflation.

SALARY: Only extract salary if EXPLICITLY stated in the posting. If not stated, use null.
  Salary estimation is handled externally. Do NOT guess.

REMOTE RULES:
  remote_compatible=true ONLY if can work from Łódź, Poland:
    - Fully remote worldwide/EU/EMEA → true
    - Hybrid Łódź or Warsaw → true
    - Everything else → false
  FOR NON-EMEA JOBS: fill remote_feasibility with timezone/entity/visa analysis.

Output ONLY valid JSON. No markdown, no explanation.
"""


# ═══════════════════════════════════════════════════════════════════════════════
# INTEL & SUMMARY prompts
# ═══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT_INTEL = """\
You are a career intelligence analyst monitoring company news, layoffs, hiring
trends, salary data, and market conditions for a senior embedded Linux engineer
in Poland (Łódź). 15 years of kernel/driver/camera experience.

Analyze the raw text from company intel sources and produce a thorough briefing.
Pay special attention to GoWork.pl employee reviews — they contain insider
information about company culture, salaries, management, and hiring activity.

Output a JSON object:
{
  "alerts": [
    {
      "company": "Company name",
      "type": "layoff|hiring_surge|salary_data|company_news|warning|employee_sentiment",
      "severity": "info|notable|urgent",
      "summary": "One-line summary",
      "details": "2-3 sentences with specifics"
    }
  ],
  "gowork_summary": [
    {"company": "Name", "rating": "X.X/5", "reviews": N, "sentiment": "positive|mixed|negative", "key_themes": "top 2-3 themes from reviews"}
  ],
  "salary_benchmarks": [
    {"role": "description", "range": "salary range in PLN", "type": "B2B net or UoP gross", "source": "source name", "notes": "any context"}
  ],
  "market_mood": "2-3 paragraphs on overall job market for embedded Linux/kernel engineers in Poland."
}

Rules:
- Focus on target companies: Nvidia, Google, AMD, Intel, Samsung, Amazon, Harman, Qualcomm, Arm, Ericsson, Fujitsu, Thales, TCL
- Flag layoffs as URGENT, hiring surges as NOTABLE
- For GoWork reviews: identify sentiment trends, salary mentions, management complaints, hiring/firing signals
- Extract ALL salary data points found. Convert to PLN (4.3/EUR, 4.0/USD).
- Distinguish B2B net vs UoP gross
- nofluffjobs.com is a JOB BOARD (aggregator), NOT a hiring company

Output ONLY valid JSON. No markdown.
"""

SYSTEM_PROMPT_SUMMARY = """\
You are ClawdBot writing a career intelligence briefing for AK.
Combine job matches and company intel into a clear, actionable summary.

Format:
🎯 TOP MATCHES (if any score >= 70)
Brief each hot match with company, role, why it fits, remote status.
For each: show salary as "B2B net: X PLN | UoP gross: Y PLN [from_offer/estimated]"
Mark software house listings with [SW House] tag.

📊 MARKET INTEL
Key company movements, layoffs, hiring trends.

💰 SALARY BENCHMARKS
For each match show: B2B net PLN and UoP gross PLN.

⚠️ ALERTS (anything urgent)

📋 FULL SCAN SUMMARY
X companies scanned, Y pages fetched, Z potential matches found.

Be concise. Use emoji. English only. Under 600 words.
"""


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1 HELPERS — Listing extraction (no LLM profile bias)
# ═══════════════════════════════════════════════════════════════════════════════

def extract_listings_from_structured_text(text):
    """Parse structured API output (Title:/URL:/Location: blocks) into listings.

    Works for output from fetch_amazon_api, fetch_fujitsu_api, fetch_google_careers.
    Returns list of dicts with title, url, location, full_text.
    """
    listings = []
    parts = re.split(r'\n(?=Title: )', text)
    for part in parts:
        title_m = re.search(r'^Title: (.+)', part, re.MULTILINE)
        if not title_m:
            continue
        url_m = re.search(r'URL: (https?://\S+)', part)
        loc_m = re.search(r'Location: (.+)', part, re.MULTILINE)
        desc_m = re.search(r'(?:Description|Details|Qualifications|Preferred): (.+)', part, re.MULTILINE)
        listings.append({
            "title": title_m.group(1).strip(),
            "url": url_m.group(1).strip() if url_m else "",
            "location": loc_m.group(1).strip() if loc_m else "",
            "snippet": desc_m.group(1).strip()[:300] if desc_m else "",
            "full_text": part.strip(),
        })
    return listings


def extract_listings_via_llm(company_name, text):
    """Use LLM to extract job listings from unstructured HTML text.

    SYSTEM_PROMPT_EXTRACT has NO candidate profile — pure factual extraction.
    """
    prompt = f"Company: {company_name}\nPage content ({len(text)} chars):\n\n{text[:8000]}"
    result = call_ollama(SYSTEM_PROMPT_EXTRACT, prompt, temperature=0.1, max_tokens=2000)
    if result:
        listings = extract_json_array(result)
        if isinstance(listings, list):
            cleaned = []
            for l in listings:
                if isinstance(l, dict) and l.get("title"):
                    cleaned.append({
                        "title": l.get("title", ""),
                        "url": l.get("url", ""),
                        "location": l.get("location", ""),
                        "snippet": l.get("snippet", ""),
                        "full_text": "",
                    })
            return cleaned
    return []


def keyword_prefilter(listings, company_keywords=None):
    """Pre-filter extracted listings by relevance keywords (title/snippet/location)."""
    check_kw = [kw.lower() for kw in MUST_MATCH_ANY]
    if company_keywords:
        check_kw.extend(kw.lower() for kw in company_keywords)

    relevant = []
    for l in listings:
        blob = f"{l.get('title','')} {l.get('snippet','')} {l.get('location','')}".lower()
        if any(kw in blob for kw in check_kw):
            relevant.append(l)
    return relevant


def fetch_individual_job_page(url, timeout=20):
    """Try to fetch an individual job posting page for detailed analysis."""
    if not url or not url.startswith("http"):
        return None
    try:
        text = fetch_page(url, timeout)
        if text.startswith("[fetch_error"):
            return None
        if len(text) < 100:
            return None
        return text
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2 HELPER — Single job analysis
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_single_job(company_name, job_text, listing_meta, source_url=""):
    """Analyze ONE job posting against candidate profile.

    job_text: full text of the job posting (from individual page or API block)
    listing_meta: dict with title, url, location from extraction phase
    Returns: analyzed job dict or None
    """
    job_url = listing_meta.get("url", "") or source_url
    title = listing_meta.get("title", "Unknown")

    prompt = f"Company: {company_name}\n"
    prompt += f"Job title from listing page: {title}\n"
    if job_url:
        prompt += f"Job URL: {job_url}\n"
    if listing_meta.get("location"):
        prompt += f"Location from listing: {listing_meta['location']}\n"
    prompt += f"\nJob posting content ({len(job_text)} chars):\n\n{job_text[:8000]}"

    result = call_ollama(SYSTEM_PROMPT_ANALYZE, prompt, max_tokens=1500)
    if not result:
        return None

    job = extract_json_object(result)
    if not isinstance(job, dict):
        return None

    if not job.get("job_url"):
        job["job_url"] = job_url

    # Post-process: fill missing salary estimates deterministically
    estimate_salary(job, company_name)

    return job


# ═══════════════════════════════════════════════════════════════════════════════
# SALARY ESTIMATION (deterministic, not LLM-dependent)
# ═══════════════════════════════════════════════════════════════════════════════

SALARY_TIERS = {
    # B2B net PLN/month ranges for Principal/Senior level
    "faang":     {"b2b_low": 35000, "b2b_high": 50000, "tier": "FAANG/Big Tech"},
    "silicon":   {"b2b_low": 30000, "b2b_high": 42000, "tier": "Big Silicon"},
    "telecom":   {"b2b_low": 26000, "b2b_high": 35000, "tier": "Telecom/Defence"},
    "automotive":{"b2b_low": 24000, "b2b_high": 32000, "tier": "Automotive/CE"},
    "shouse":    {"b2b_low": 18000, "b2b_high": 24000, "tier": "Software house"},
    "startup":   {"b2b_low": 20000, "b2b_high": 30000, "tier": "Startup/Mid-size"},
}

COMPANY_TIER_MAP = {
    "google": "faang", "amazon": "faang", "meta": "faang", "apple": "faang",
    "qualcomm": "silicon", "amd": "silicon", "intel": "silicon", "nvidia": "silicon",
    "arm": "silicon", "samsung": "silicon", "broadcom": "silicon",
    "ericsson": "telecom", "nokia": "telecom", "thales": "telecom",
    "harman": "automotive", "continental": "automotive", "tcl": "automotive",
    "fujitsu": "telecom",
    "sii": "shouse", "globallogic": "shouse", "sysgo": "shouse", "codelime": "shouse",
    "canonical": "startup",
}


def estimate_salary(job, company_name):
    """Fill salary fields if LLM left them blank. Deterministic tier-based estimation."""
    # If LLM already filled salary, keep it
    if job.get("salary_b2b_net_pln") and job.get("salary_source") == "from_offer":
        return

    # Find tier by company name (fuzzy match)
    cn_lower = company_name.lower().strip()
    tier_key = None
    for name, tkey in COMPANY_TIER_MAP.items():
        if name in cn_lower or cn_lower in name:
            tier_key = tkey
            break

    # For job boards, try matching company from job dict
    if not tier_key and job.get("company"):
        jc = job["company"].lower()
        for name, tkey in COMPANY_TIER_MAP.items():
            if name in jc:
                tier_key = tkey
                break

    if not tier_key:
        # Check via_software_house flag
        if job.get("via_software_house"):
            tier_key = "shouse"
        else:
            tier_key = "startup"  # default for unknown companies

    tier = SALARY_TIERS[tier_key]
    b2b_low, b2b_high = tier["b2b_low"], tier["b2b_high"]
    uop_low = int(b2b_low * 0.82)
    uop_high = int(b2b_high * 0.82)

    job["salary_b2b_net_pln"] = f"{b2b_low}-{b2b_high}"
    job["salary_uop_gross_pln"] = f"{uop_low}-{uop_high}"
    job["salary_source"] = "estimated"
    job["salary_note"] = f"Estimated for {tier['tier']} tier (Principal/Senior level)"

def scan_career_pages():
    """Scan company career pages using two-phase extraction → analysis."""

    # ── Phase 1: Extract listings from all pages ──
    print("\n  ═══ Phase 1: Extracting job listings (no profile bias) ═══")
    all_extracted = []  # list of (cid, listing, source_url)
    page_results = {}

    for cid, company in COMPANIES.items():
        name = company["name"]
        fetcher_name = company.get("fetcher")
        fetcher_fn = API_FETCHERS.get(fetcher_name, fetch_page) if fetcher_name else fetch_page

        for url in company["career_urls"]:
            print(f"  [{name}] Fetching: {url[:80]}...")
            text = fetcher_fn(url)

            if text.startswith("[fetch_error"):
                print(f"    ✗ {text}")
                health = getattr(fetch_page, "_last_health", {})
                page_results[url] = {
                    "status": "error", "error": text,
                    "http_code": health.get("http_code", 0),
                    "response_time": health.get("response_time", 0),
                    "company": name, "company_id": cid,
                }
                continue

            chars = len(text)
            print(f"    Got {chars} chars")

            # Try structured text parsing first (works for API fetchers)
            listings = extract_listings_from_structured_text(text)

            if listings:
                print(f"    Parsed {len(listings)} structured listings")
            else:
                # Quick keyword check on raw text
                text_lower = text.lower()
                kw_hits = sum(1 for kw in company["keywords"] if kw.lower() in text_lower)
                if kw_hits == 0 and chars < 500:
                    print(f"    ✗ No keyword hits in small page, skipping")
                    health = getattr(fetch_page, "_last_health", {})
                    page_results[url] = {
                        "status": "no_keywords", "chars": chars,
                        "http_code": health.get("http_code", 0),
                        "response_time": health.get("response_time", 0),
                        "company": name, "company_id": cid,
                    }
                    continue

                # Use LLM to extract listings (profile-free prompt)
                print(f"    Extracting via LLM (no profile)...")
                listings = extract_listings_via_llm(name, text)
                print(f"    LLM found {len(listings)} listings")

            # Pre-filter by relevance keywords
            relevant = keyword_prefilter(listings, company.get("keywords"))
            skipped = len(listings) - len(relevant)
            if skipped:
                print(f"    Pre-filter: {len(relevant)} relevant, {skipped} irrelevant skipped")
            else:
                print(f"    All {len(relevant)} pass keyword filter")

            for l in relevant:
                all_extracted.append((cid, l, url))

            health = getattr(fetch_page, "_last_health", {})
            page_results[url] = {
                "status": "ok", "chars": chars,
                "hash": content_hash(text),
                "listings_found": len(listings),
                "relevant_listings": len(relevant),
                "http_code": health.get("http_code", 0),
                "response_time": health.get("response_time", 0),
                "company": name, "company_id": cid,
            }

    print(f"\n  Phase 1 done: {len(all_extracted)} relevant listings from {len(page_results)} pages")

    # ── Phase 2: Analyze each listing individually ──
    print(f"\n  ═══ Phase 2: Analyzing {len(all_extracted)} jobs individually ═══")
    all_jobs = []
    analyzed = 0
    skipped_low = 0

    for cid, listing, source_url in all_extracted:
        name = COMPANIES[cid]["name"]
        title = listing.get("title", "?")
        job_url = listing.get("url", "")

        print(f"  [{name}] {title[:60]}...")

        # Determine best available text for analysis
        job_text = None

        # 1. Use full_text from API (has description, qualifications, etc.)
        if listing.get("full_text") and len(listing["full_text"]) > 100:
            job_text = listing["full_text"]
            print(f"    Using API data ({len(job_text)} chars)")

        # 2. Try fetching individual job page for richer data
        if not job_text or len(job_text) < 300:
            if job_url and job_url != source_url:
                print(f"    Fetching individual page...")
                page_text = fetch_individual_job_page(job_url)
                if page_text and len(page_text) > 200:
                    job_text = page_text
                    print(f"    Got individual page ({len(job_text)} chars)")
                else:
                    print(f"    ✗ Individual page unavailable")

        # 3. Fallback to minimal data
        if not job_text or len(job_text) < 100:
            snippet = listing.get("snippet", "")
            location = listing.get("location", "")
            job_text = f"Title: {title}\nLocation: {location}\n"
            if snippet:
                job_text += f"Description: {snippet}\n"
            job_text += "\n(Note: this is a minimal listing — limited details available)"
            print(f"    Using minimal data ({len(job_text)} chars)")

        # Analyze with LLM (single job + candidate profile)
        analysis = analyze_single_job(name, job_text, listing, source_url)

        if not analysis:
            print(f"    ✗ Analysis failed")
            continue

        score = analysis.get("match_score", 0)
        if score < 40:
            print(f"    ✗ Score {score}% < 40, skipped")
            skipped_low += 1
            continue

        # Tag with metadata
        analysis["source_company"] = cid
        analysis["company"] = analysis.get("company", name)
        if not analysis.get("job_url"):
            analysis["job_url"] = job_url or source_url

        all_jobs.append(analysis)
        analyzed += 1
        remote_icon = " 🏠" if analysis.get("remote_compatible") else ""
        print(f"    ✓ Score: {score}%{remote_icon}")

    print(f"\n  Phase 2 done: {analyzed} jobs scored ≥40, {skipped_low} below threshold")
    return all_jobs, page_results


def scan_job_boards():
    """Scan job board aggregators using two-phase extraction → analysis."""
    print("\n  ═══ Scanning job boards (two-phase) ═══")
    all_jobs = []
    board_results = {}

    for bid, board in JOB_BOARDS.items():
        name = board["name"]
        combined_text = ""

        for url in board["urls"]:
            print(f"  [{name}] Fetching: {url[:80]}...")
            text = fetch_page(url)
            if not text.startswith("[fetch_error"):
                combined_text += f"\n--- {url} ---\n{text[:6000]}\n"
                print(f"    Got {len(text)} chars")
            else:
                print(f"    ✗ {text}")

        if len(combined_text) < 200:
            board_results[bid] = {"status": "insufficient_data"}
            continue

        # Phase 1: Extract listings (no profile)
        print(f"  [{name}] Extracting listings via LLM (no profile)...")
        listings = extract_listings_via_llm(name, combined_text[:8000])
        print(f"    Extracted {len(listings)} listings")

        relevant = keyword_prefilter(listings)
        print(f"    Pre-filter: {len(relevant)} relevant")

        # Phase 2: Analyze each
        for listing in relevant:
            title = listing.get("title", "?")
            job_url = listing.get("url", "")
            print(f"  [{name}] Analyzing: {title[:50]}...")

            job_text = None
            if job_url:
                page_text = fetch_individual_job_page(job_url)
                if page_text and len(page_text) > 200:
                    job_text = page_text

            if not job_text:
                snippet = listing.get("snippet", "")
                job_text = f"Title: {title}\nLocation: {listing.get('location','')}\n"
                if snippet:
                    job_text += f"Description: {snippet}\n"
                job_text += f"\n(Minimal listing from {name})"

            analysis = analyze_single_job(name, job_text, listing,
                                          board["urls"][0] if board["urls"] else "")
            if analysis and analysis.get("match_score", 0) >= 40:
                analysis["source_board"] = bid
                if not analysis.get("job_url"):
                    analysis["job_url"] = job_url or (board["urls"][0] if board["urls"] else "")
                all_jobs.append(analysis)
                print(f"    ✓ Score: {analysis.get('match_score', 0)}%")
            elif analysis:
                print(f"    ✗ Score {analysis.get('match_score', 0)}% < 40")
            else:
                print(f"    ✗ Analysis failed")

        board_results[bid] = {
            "status": "ok",
            "chars": len(combined_text),
            "listings_found": len(listings),
            "relevant": len(relevant),
            "jobs_analyzed": len([j for j in all_jobs if j.get("source_board") == bid]),
        }

    return all_jobs, board_results


def scan_intel_sources():
    """Scan company intelligence sources with persistent Gowork tracking."""
    print("\n  ═══ Scanning company intel ═══")
    combined_text = ""
    intel_results = {}

    # ── Persistent Gowork intel storage ──
    gowork_db_path = os.path.join(CAREER_DIR, "company-intel.json")
    gowork_db = {}
    if os.path.exists(gowork_db_path):
        try:
            with open(gowork_db_path) as f:
                gowork_db = json.load(f)
        except (json.JSONDecodeError, OSError):
            gowork_db = {}

    today = datetime.now().strftime("%Y-%m-%d")

    for sid, source in INTEL_SOURCES.items():
        name = source["name"]
        fetcher_name = source.get("fetcher")
        fetcher_fn = API_FETCHERS.get(fetcher_name, fetch_page) if fetcher_name else fetch_page
        for url in source["urls"]:
            print(f"  [{name}] Fetching: {url[:80]}...")
            text = fetcher_fn(url)
            # Rate-limit Gowork to avoid 429s (13 company pages)
            if sid == "gowork":
                time.sleep(3)
            if not text.startswith("[fetch_error"):
                combined_text += f"\n=== {name}: {url[:60]} ===\n{text[:5000]}\n"
                print(f"    Got {len(text)} chars")

                # ── Persist Gowork review snapshots ──
                if sid == "gowork" and fetcher_name == "gowork_reviews":
                    # Parse the structured output from fetch_gowork_reviews
                    header = text.split("\n")[0] if text else ""
                    rating_m = re.search(r'Rating:\s*([\d.?]+)/5', text)
                    count_m = re.search(r'Reviews:\s*(\d+)', text)
                    company_m = re.search(r'^GoWork\.pl\s*[—–-]\s*(.+)', text)
                    entity_m = re.search(r'opinie_czytaj,(\d+)', url)
                    entity_id = entity_m.group(1) if entity_m else url

                    company_key = company_m.group(1).strip() if company_m else entity_id
                    rating = rating_m.group(1) if rating_m else "?"
                    reviews = count_m.group(1) if count_m else "?"

                    # Extract review texts for this snapshot
                    review_texts = re.findall(
                        r'\[(\d{1,2}\.\d{1,2}\.\d{4})\]\s*\(([^)]*)\)\s*(.+)',
                        text)
                    latest_reviews = [
                        {"date": d, "role": r, "text": t[:300]}
                        for d, r, t in review_texts[:10]
                    ]

                    if company_key not in gowork_db:
                        gowork_db[company_key] = {
                            "entity_id": entity_id,
                            "url": url,
                            "snapshots": [],
                        }

                    entry = gowork_db[company_key]
                    # Avoid duplicate same-day snapshots
                    existing_dates = {s["scan_date"] for s in entry.get("snapshots", [])}
                    if today not in existing_dates:
                        entry["snapshots"].append({
                            "scan_date": today,
                            "rating": rating,
                            "review_count": reviews,
                            "latest_reviews": latest_reviews,
                        })
                        # Keep last 90 days of snapshots
                        entry["snapshots"] = entry["snapshots"][-90:]

            else:
                print(f"    ✗ {text}")

        intel_results[sid] = {"status": "ok" if len(combined_text) > 200 else "insufficient"}

    # ── Save persistent Gowork DB ──
    if gowork_db:
        try:
            with open(gowork_db_path, "w") as f:
                json.dump(gowork_db, f, indent=2, ensure_ascii=False)
            print(f"  Saved company intel DB ({len(gowork_db)} companies)")
        except OSError as ex:
            print(f"  ✗ Failed to save intel DB: {ex}")

    if len(combined_text) < 300:
        return None, intel_results

    prompt = f"Company intelligence data:\n\n{combined_text[:14000]}"
    result = call_ollama(SYSTEM_PROMPT_INTEL, prompt, max_tokens=4000)
    intel_data = None
    if result:
        try:
            intel_data = extract_json_object(result)
        except (json.JSONDecodeError, ValueError):
            print(f"    ✗ Intel JSON parse error")

    return intel_data, intel_results


def deduplicate_jobs(jobs):
    """Remove duplicate job listings."""
    seen = set()
    unique = []
    for j in jobs:
        key = f"{j.get('company', '').lower()[:20]}|{j.get('title', '').lower()[:40]}"
        if key not in seen:
            seen.add(key)
            unique.append(j)
    return unique


def generate_summary(jobs, intel_data, scan_meta):
    """Generate human-readable career briefing."""
    hot_jobs = [j for j in jobs if j.get("match_score", 0) >= 70]
    good_jobs = [j for j in jobs if 40 <= j.get("match_score", 0) < 70]
    remote_jobs = [j for j in jobs if j.get("remote_compatible", False)]

    parts = [f"Scan date: {datetime.now().strftime('%A, %d %B %Y')}"]
    parts.append(f"Architecture: two-phase (extract → analyze) anti-hallucination")
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
            b2b = j.get("salary_b2b_net_pln", "")
            uop = j.get("salary_uop_gross_pln", "")
            sal_src = j.get("salary_source", "")
            if b2b or uop:
                parts.append(f"  Salary: B2B net {b2b} | UoP gross {uop} [{sal_src}]")
            if j.get("via_software_house"):
                parts.append("  [SOFTWARE HOUSE - dashboard only, no Signal]")

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


# ─── Signal alerts ───

def send_hot_alerts(jobs, intel_data):
    """Send Signal notifications for hot matches and urgent intel."""
    software_house_ids = {cid for cid, c in COMPANIES.items() if c.get("software_house")}
    software_house_names = {c["name"].lower() for cid, c in COMPANIES.items() if c.get("software_house")}
    software_house_names |= {"sii", "sii poland", "codelime", "codilime",
        "globallogic", "sysgo", "spyrosoft", "antal", "hays", "randstad", "squad",
        "alten", "capgemini engineering", "avenga", "luxoft", "epam", "infosys",
        "tata", "wipro", "cognizant", "accenture"}

    def is_software_house(j):
        if j.get("source_company", "") in software_house_ids:
            return True
        if j.get("via_software_house", False):
            return True
        company = j.get("company", "").lower()
        return any(sh in company for sh in software_house_names)

    # Hot matches: >=70 score, not software houses — alert regardless of remote
    hot_jobs = [j for j in jobs if j.get("match_score", 0) >= 70
                and not is_software_house(j)]

    # Worth checking: 55-69% with remote capability
    worth_checking = [j for j in jobs if 55 <= j.get("match_score", 0) < 70
                      and j.get("remote_compatible", False)
                      and not is_software_house(j)]

    urgent_intel = []
    if intel_data and "alerts" in intel_data:
        urgent_intel = [a for a in intel_data["alerts"] if a.get("severity") == "urgent"]

    alerts_sent = 0

    for j in hot_jobs[:5]:
        b2b = j.get('salary_b2b_net_pln') or '?'
        uop = j.get('salary_uop_gross_pln') or '?'
        src = j.get('salary_source', '?')
        sal_line = f'B2B net: {b2b} | UoP gross: {uop} [{src}]'
        location = j.get('remote_details', j.get('location', '?'))
        remote = j.get('remote_compatible', False)
        icon = "🎯" if remote else "⚡"
        label = "HOT REMOTE MATCH" if remote else "HOT MATCH"
        loc_icon = "🏠" if remote else "📍"
        job_link = j.get('job_url', '')
        link_line = f"🔗 {job_link}" if job_link else "🔗 Check dashboard → career.html"
        msg = (
            f"{icon} {label} ({j.get('match_score', 0)}%)\n"
            f"{j.get('title', '?')} @ {j.get('company', '?')}\n"
            f"{loc_icon} {location}\n"
            f"✅ {chr(44).join(j.get('match_reasons', [])[:3])}\n"
            f"💰 {sal_line}\n"
            f"{link_line}"
        )
        if signal_send(msg):
            alerts_sent += 1
            print(f"  📡 Signal alert sent: {j.get('title', '?')}")

    for j in worth_checking[:2]:
        b2b = j.get('salary_b2b_net_pln') or '?'
        uop = j.get('salary_uop_gross_pln') or '?'
        src = j.get('salary_source', '?')
        sal_line = f'B2B net: {b2b} | UoP gross: {uop} [{src}]'
        job_link = j.get('job_url', '')
        link_line = f"🔗 {job_link}" if job_link else "🔗 Check dashboard → career.html"
        msg = (
            f"🌍 WORTH CHECKING ({j.get('match_score', 0)}%)\n"
            f"{j.get('title', '?')} @ {j.get('company', '?')}\n"
            f"🏠 {j.get('remote_details', j.get('location', '?'))}\n"
            f"✅ {chr(44).join(j.get('match_reasons', [])[:3])}\n"
            f"💰 {sal_line}\n"
            f"{link_line}"
        )
        if signal_send(msg):
            alerts_sent += 1
            print(f"  📡 Signal worth-checking alert: {j.get('title', '?')}")

    for a in urgent_intel[:2]:
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
    # Ensure line-buffered stdout so cron output is never lost
    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except Exception:
        pass

    quick = "--quick" in sys.argv
    signal_test = "--signal-test" in sys.argv

    if signal_test:
        ok = signal_send("🧪 Career scanner test — Signal notifications working!")
        print("Signal test:", "OK" if ok else "FAILED")
        return

    quiet = is_quiet_hours()
    mode_str = f"{'quick' if quick else 'full'}, {'QUIET HOURS' if quiet else 'daytime'}"
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] career-scan starting ({mode_str})")
    print(f"  Architecture: two-phase (extract → analyze) anti-hallucination")

    # Guard: don't compete with other batch scripts
    import subprocess
    for proc in ["lore-digest.sh", "repo-watch.sh", "idle-think.sh", "ha-journal.py"]:
        try:
            r = subprocess.run(["pgrep", "-f", proc], capture_output=True, timeout=5)
            if r.returncode == 0:
                print(f"  {proc} is running — skipping")
                return
        except Exception:
            pass

    # GPU guard — during quiet hours we own the GPU
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

    # Evict competing models before loading ours
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/ps")
        resp = urllib.request.urlopen(req, timeout=5)
        ps = json.loads(resp.read())
        for m in ps.get("models", []):
            name = m.get("name", "")
            if name and name != OLLAMA_MODEL:
                print(f"  Evicting {name} from GPU...")
                evict = json.dumps({"model": name, "keep_alive": 0}).encode()
                ereq = urllib.request.Request(f"{OLLAMA_URL}/api/generate",
                                             data=evict,
                                             headers={"Content-Type": "application/json"})
                try:
                    eresp = urllib.request.urlopen(ereq, timeout=30)
                    eresp.read()
                    print(f"  Evicted {name}")
                    time.sleep(2)  # let GPU memory settle
                except Exception as ex:
                    print(f"  Evict {name} failed: {ex} — continuing")
    except Exception:
        pass

    # Preload the LLM model
    print(f"  Loading {OLLAMA_MODEL}...")
    try:
        preload = json.dumps({"model": OLLAMA_MODEL, "prompt": "", "stream": False,
                              "options": {"num_predict": 1, "num_ctx": 6144}, "keep_alive": "60m"}).encode()
        req = urllib.request.Request(f"{OLLAMA_URL}/api/generate",
                                    data=preload,
                                    headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=300)
        resp.read()
        print(f"  {OLLAMA_MODEL} ready")
    except Exception as ex:
        print(f"  Warning: model preload failed: {ex}")

    # Phase 1+2: Career pages (extract → analyze)
    career_jobs, career_results = scan_career_pages()

    # Phase 1+2: Job boards (extract → analyze)
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
        "architecture": "two-phase-extract-analyze",
    }

    print(f"\n  ═══ Results ═══")
    print(f"  Jobs found: {len(all_jobs)}")
    print(f"  Hot matches (>=70): {scan_meta['hot_matches']}")
    print(f"  Remote-compatible: {scan_meta['remote_compatible']}")
    print(f"  Duration: {scan_duration:.0f}s")

    # Generate summary
    print("\n  ═══ Generating summary ═══")
    summary = generate_summary(all_jobs, intel_data, scan_meta)

    # URL health for dashboard
    url_health = {}
    for url, res in career_results.items():
        cid = res.get("company_id", "")
        company = COMPANIES.get(cid, {})
        url_health[url] = {
            "company": res.get("company", company.get("name", "?")),
            "company_id": cid,
            "software_house": company.get("software_house", False),
            "http_code": res.get("http_code", 0),
            "response_time": res.get("response_time", 0),
            "status": res.get("status", "unknown"),
            "chars": res.get("chars", 0),
        }
    for cid, company in COMPANIES.items():
        for url in company["career_urls"]:
            if url not in url_health:
                url_health[url] = {
                    "company": company["name"],
                    "company_id": cid,
                    "software_house": company.get("software_house", False),
                }

    scan_data = {
        "meta": scan_meta,
        "jobs": all_jobs[:50],
        "intel": intel_data,
        "source_results": {
            "career_pages": career_results,
            "job_boards": board_results,
            "intel_sources": intel_results,
        },
        "url_health": url_health,
        "summary": summary,
    }
    save_scan(scan_data)
    print(f"  Saved to {CAREER_DIR}/latest-scan.json")

    save_note(
        f"Career Scan — {datetime.now().strftime('%d %b %Y')}",
        summary,
        context=scan_meta,
    )
    print(f"  Saved think note")

    alerts = send_hot_alerts(all_jobs, intel_data)
    if alerts:
        print(f"  Sent {alerts} Signal alert(s)")

    print(f"\n  Done in {scan_duration:.0f}s.")


if __name__ == "__main__":
    main()
