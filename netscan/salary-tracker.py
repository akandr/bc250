#!/usr/bin/env python3
"""
salary-tracker.py — Nightly salary & rate intelligence tracker
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Collects salary data from multiple sources and tracks trends for
embedded Linux / camera driver roles in Poland.

Sources:
  - Existing career-scan JSON (extract salary fields already parsed)
  - NoFluffJobs salary API (live, structured)
  - JustJoinIT salary data (live, structured)
  - Bulldogjob (live, structured)
  - levels.fyi Poland data (live, TC → monthly PLN)
  - Glassdoor Poland salary explore (JSON-LD + GraphQL API)

Output: /opt/netscan/data/salary/
  - salary-YYYYMMDD.json      (daily snapshot)
  - salary-history.json       (rolling 180-day trend DB)
  - latest-salary.json        (symlink to latest)

Cron: 0 2 * * * flock -w 1200 /tmp/ollama-gpu.lock python3 /opt/netscan/salary-tracker.py
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
from datetime import datetime, timedelta
from pathlib import Path
from llm_sanitize import sanitize_llm_output

# ── Config ─────────────────────────────────────────────────────────────────
OLLAMA_URL = "http://localhost:11434"
OLLAMA_CHAT = f"{OLLAMA_URL}/api/chat"
OLLAMA_MODEL = "qwen3:14b"

SALARY_DIR = Path("/opt/netscan/data/salary")
CAREER_DIR = Path("/opt/netscan/data/career")
HISTORY_FILE = SALARY_DIR / "salary-history.json"
PROFILE_FILE = Path("/opt/netscan/profile-private.json")

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
HISTORY_DAYS = 180

# Keywords for filtering relevant listings
ROLE_KEYWORDS = [
    "embedded", "kernel", "driver", "linux", "firmware", "bsp",
    "camera", "v4l2", "mipi", "csi", "isp", "libcamera",
    "soc", "automotive", "adas", "sensor", "imaging",
    "gstreamer", "device tree", "dma", "pcie", "i2c", "spi",
    "fpga", "rtos", "c/c++", "low-level", "bare-metal",
    "gpu", "vulkan", "drm",
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
    """Fetch URL expecting JSON, return dict or None."""
    text = fetch_url(url, timeout)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        log(f"  JSON parse error {url}: {e}")
        return None

def matches_role(text):
    """Check if text matches our role keywords."""
    if not text:
        return False
    lower = text.lower()
    return any(kw in lower for kw in ROLE_KEYWORDS)

def call_ollama(system_prompt, user_prompt, temperature=0.3, max_tokens=3000):
    """Call Ollama for LLM analysis. Returns content string or None."""
    try:
        # Health check
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=10) as r:
            tags = json.loads(r.read())
            models = [m["name"] for m in tags.get("models", [])]
            if not any(OLLAMA_MODEL in m for m in models):
                log(f"  Model {OLLAMA_MODEL} not found in Ollama")
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
            return sanitize_llm_output(content)
    except Exception as e:
        log(f"  Ollama call failed: {e}")
        return None


# ── Source: Existing career-scan JSONs ─────────────────────────────────────

def collect_from_career_scans():
    """Extract salary data from recent career-scan output files."""
    entries = []
    if not CAREER_DIR.exists():
        return entries

    # Look at scans from last 7 days
    cutoff = datetime.now() - timedelta(days=7)
    for f in sorted(CAREER_DIR.glob("scan-*.json")):
        try:
            # Parse date from filename: scan-YYYYMMDD-HHMM.json
            parts = f.stem.split("-")
            fdate = datetime.strptime(f"{parts[1]}-{parts[2]}", "%Y%m%d-%H%M")
            if fdate < cutoff:
                continue

            data = json.load(open(f))
            for job in data.get("jobs", []):
                sal_b2b = job.get("salary_b2b_net_pln")
                sal_uop = job.get("salary_uop_gross_pln")
                if not sal_b2b and not sal_uop:
                    continue
                entries.append({
                    "source": "career-scan",
                    "scan_date": fdate.strftime("%Y-%m-%d"),
                    "title": job.get("title", ""),
                    "company": job.get("company", ""),
                    "location": job.get("location", ""),
                    "salary_b2b_net_pln": sal_b2b,
                    "salary_uop_gross_pln": sal_uop,
                    "salary_source": job.get("salary_source", "unknown"),
                    "salary_note": job.get("salary_note", ""),
                    "match_score": job.get("match_score", 0),
                    "remote_compatible": job.get("remote_compatible", False),
                    "url": job.get("job_url", ""),
                })
        except Exception as e:
            log(f"  Error reading {f.name}: {e}")

    log(f"Career scans: {len(entries)} salary records from last 7 days")
    return entries


# ── Source: NoFluffJobs API ────────────────────────────────────────────────

def collect_from_nofluffjobs():
    """Fetch salary data from NoFluffJobs API."""
    entries = []
    keywords = ["embedded", "linux", "kernel", "camera", "driver", "firmware",
                "bsp", "automotive", "adas", "c++"]
    seen_ids = set()

    for kw in keywords:
        url = f"https://nofluffjobs.com/api/posting?salaryCurrency=PLN&requirement={urllib.parse.quote(kw)}&country=PL"
        data = fetch_json(url, timeout=30)
        if not data:
            continue

        postings = data if isinstance(data, list) else data.get("postings", [])
        for p in postings:
            pid = p.get("id", p.get("url", ""))
            if pid in seen_ids:
                continue
            seen_ids.add(pid)

            title = p.get("title", p.get("name", ""))
            if not matches_role(title + " " + " ".join(p.get("mulesoftTags", [])) +
                               " " + " ".join(str(r) for r in p.get("requirements", []))):
                continue

            salary = p.get("salary", {})
            if isinstance(salary, dict):
                sal_from = salary.get("from", 0)
                sal_to = salary.get("to", 0)
                sal_type = salary.get("type", "b2b")
                sal_currency = salary.get("currency", "PLN")
            else:
                continue

            if sal_currency != "PLN" or sal_from == 0:
                continue

            entry = {
                "source": "nofluffjobs",
                "scan_date": datetime.now().strftime("%Y-%m-%d"),
                "title": title,
                "company": p.get("company", {}).get("name", p.get("companyName", "")),
                "location": p.get("location", {}).get("places", [{}])[0].get("city", "remote")
                            if isinstance(p.get("location"), dict) else "unknown",
                "remote_compatible": p.get("fullyRemote", False) or p.get("location", {}).get("fullyRemote", False),
                "salary_type": sal_type,
                "salary_from": sal_from,
                "salary_to": sal_to,
                "salary_currency": sal_currency,
                "url": f"https://nofluffjobs.com/pl/job/{p.get('url', pid)}",
            }

            # Normalize to B2B net PLN monthly
            if sal_type == "b2b":
                entry["salary_b2b_net_pln"] = f"{sal_from}-{sal_to}"
            elif sal_type in ("uop", "permanent"):
                entry["salary_uop_gross_pln"] = f"{sal_from}-{sal_to}"
                entry["salary_b2b_net_pln"] = f"{int(sal_from/0.82)}-{int(sal_to/0.82)}"

            entries.append(entry)

        time.sleep(1)  # rate limit

    log(f"NoFluffJobs: {len(entries)} relevant salary listings")
    return entries


# ── Source: JustJoinIT ─────────────────────────────────────────────────────

def collect_from_justjoinit():
    """Fetch salary data from JustJoin.it API."""
    entries = []
    url = "https://api.justjoin.it/v2/user-panel/offers?categories[]=embedded&categories[]=devops&categories[]=other&page=1&sortBy=newest&orderBy=DESC&perPage=100&salaryCurrencies[]=PLN"
    data = fetch_json(url, timeout=30)
    if not data:
        # Fallback: try older API
        url = "https://justjoin.it/api/offers"
        data = fetch_json(url, timeout=30)

    if not data:
        log("JustJoinIT: API unavailable")
        return entries

    offers = data if isinstance(data, list) else data.get("data", data.get("offers", []))
    for o in offers:
        title = o.get("title", "")
        skills = " ".join(s.get("name", "") for s in o.get("skills", o.get("requiredSkills", [])))
        marker = o.get("marker_icon", o.get("category", ""))

        if not matches_role(f"{title} {skills} {marker}"):
            continue

        # Extract salary
        emp_types = o.get("employment_types", o.get("employmentTypes", []))
        for et in emp_types:
            sal = et.get("salary", et.get("from_pln", None))
            if isinstance(sal, dict):
                sal_from = sal.get("from", 0)
                sal_to = sal.get("to", 0)
                sal_currency = sal.get("currency", "PLN")
            elif isinstance(et.get("from"), (int, float)):
                sal_from = et.get("from", 0)
                sal_to = et.get("to", 0)
                sal_currency = et.get("currency", "PLN")
            else:
                continue

            if sal_currency != "PLN" or sal_from == 0:
                continue

            sal_type = et.get("type", "b2b")
            entry = {
                "source": "justjoinit",
                "scan_date": datetime.now().strftime("%Y-%m-%d"),
                "title": title,
                "company": o.get("company_name", o.get("companyName", "")),
                "location": o.get("city", o.get("workplace", {}).get("city", "remote")),
                "remote_compatible": o.get("remote", o.get("workplace_type", "")) in (True, "remote", "partly_remote"),
                "salary_type": sal_type,
                "salary_from": sal_from,
                "salary_to": sal_to,
                "url": f"https://justjoin.it/offers/{o.get('slug', o.get('id', ''))}",
            }

            if sal_type == "b2b":
                entry["salary_b2b_net_pln"] = f"{sal_from}-{sal_to}"
            elif sal_type in ("uop", "permanent"):
                entry["salary_uop_gross_pln"] = f"{sal_from}-{sal_to}"
                entry["salary_b2b_net_pln"] = f"{int(sal_from/0.82)}-{int(sal_to/0.82)}"

            entries.append(entry)
            break  # one salary per offer

    log(f"JustJoinIT: {len(entries)} relevant salary listings")
    return entries


# ── Source: Bulldogjob ─────────────────────────────────────────────────────

def collect_from_bulldogjob():
    """Fetch salary data from Bulldogjob."""
    entries = []
    for kw in ["embedded", "linux", "kernel", "camera", "driver", "firmware"]:
        url = f"https://bulldogjob.pl/api/companies/jobs?q={urllib.parse.quote(kw)}"
        data = fetch_json(url, timeout=30)
        if not data:
            continue

        jobs = data if isinstance(data, list) else data.get("jobs", data.get("data", []))
        for j in jobs:
            title = j.get("title", j.get("position", ""))
            if not matches_role(title):
                continue

            sal = j.get("salary", {})
            if isinstance(sal, dict) and sal.get("from"):
                entries.append({
                    "source": "bulldogjob",
                    "scan_date": datetime.now().strftime("%Y-%m-%d"),
                    "title": title,
                    "company": j.get("company", {}).get("name", ""),
                    "location": j.get("city", j.get("location", "")),
                    "salary_from": sal.get("from", 0),
                    "salary_to": sal.get("to", 0),
                    "salary_type": sal.get("employment_type", "b2b"),
                    "salary_currency": sal.get("currency", "PLN"),
                    "url": j.get("url", j.get("apply_url", "")),
                })
        time.sleep(1)

    log(f"Bulldogjob: {len(entries)} relevant salary listings")
    return entries


# ── Source: levels.fyi ─────────────────────────────────────────────────────

def collect_from_levelsfyi():
    """Fetch salary data from levels.fyi Poland salaries.

    levels.fyi exposes a public search endpoint that returns structured
    compensation data.  We search for embedded/firmware/driver roles
    in Poland and convert TC to monthly PLN (B2B equivalent).
    """
    entries = []
    # levels.fyi search API — returns JSON with salary datapoints
    search_terms = [
        "embedded engineer Poland",
        "firmware engineer Poland",
        "linux driver Poland",
        "camera engineer Poland",
        "BSP engineer Poland",
    ]

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Accept": "application/json",
    }

    for term in search_terms:
        try:
            # levels.fyi public salary search
            url = f"https://www.levels.fyi/js/salaryData.json"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                all_data = json.loads(resp.read())
        except Exception as e:
            log(f"levels.fyi: bulk data fetch failed: {e}")
            # Fallback: try the search API
            try:
                search_url = (
                    f"https://www.levels.fyi/api/v1/salaries?"
                    f"countryId=178&title=Software+Engineer&limit=100"
                )
                req = urllib.request.Request(search_url, headers=headers)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    api_data = json.loads(resp.read())
                all_data = api_data.get("results", api_data) if isinstance(api_data, dict) else api_data
            except Exception as e2:
                log(f"levels.fyi: API also failed: {e2}")
                all_data = []
            break  # Only need one fetch for all terms

        break  # Bulk data fetched once

    if not isinstance(all_data, list):
        all_data = all_data.get("results", []) if isinstance(all_data, dict) else []

    # Filter for Poland + relevant roles
    role_kw = re.compile(
        r'embedded|firmware|driver|bsp|linux|kernel|camera|sensor|fpga|hw.?sw|'
        r'system.?software|platform|low.?level', re.I
    )

    for entry in all_data:
        location = entry.get("location", entry.get("cityName", ""))
        company = entry.get("company", entry.get("companyName", ""))
        title = entry.get("title", entry.get("level", ""))
        country = entry.get("country", "")

        # Filter: Poland only
        if not (country.lower() == "poland" or "poland" in location.lower()
                or "warszaw" in location.lower() or "kraków" in location.lower()
                or "wrocław" in location.lower() or "gdańsk" in location.lower()
                or "łódź" in location.lower() or "poznań" in location.lower()):
            continue

        # Role filter
        combined = f"{title} {entry.get('tag', '')} {entry.get('specialization', '')}"
        if not role_kw.search(combined):
            continue

        # Extract total compensation (yearly USD) → monthly PLN
        tc = entry.get("totalyearlycompensation", entry.get("totalComp", 0))
        base = entry.get("basesalary", entry.get("baseSalary", 0))
        try:
            tc = int(tc) if tc else 0
            base = int(base) if base else 0
        except (ValueError, TypeError):
            continue

        if tc <= 0 and base <= 0:
            continue

        # Convert: yearly USD → monthly PLN (approximate rate)
        usd_pln = 4.05  # approximate exchange rate
        yearly_pln = (tc or base) * usd_pln
        monthly_pln = int(yearly_pln / 12)

        if monthly_pln < 8000 or monthly_pln > 100000:
            continue  # Outlier filter

        entries.append({
            "source": "levels.fyi",
            "scan_date": datetime.now().strftime("%Y-%m-%d"),
            "title": title,
            "company": company,
            "location": location,
            "salary_from": monthly_pln,
            "salary_to": monthly_pln,
            "salary_type": "b2b",  # TC equivalent
            "salary_b2b_net_pln": f"{monthly_pln}-{monthly_pln}",
            "salary_currency": "PLN",
            "salary_note": f"TC {tc or base} USD/yr → {monthly_pln} PLN/mo",
            "url": "https://www.levels.fyi/t/software-engineer/locations/poland",
        })

    # Deduplicate by company+title
    seen = set()
    deduped = []
    for e in entries:
        key = (e["company"].lower(), e["title"].lower())
        if key not in seen:
            seen.add(key)
            deduped.append(e)

    log(f"levels.fyi: {len(deduped)} relevant salary records (Poland, embedded/driver)")
    return deduped


# ── Source: Glassdoor ──────────────────────────────────────────────────────

def collect_from_glassdoor():
    """Scrape Glassdoor salary explore pages for embedded/driver roles in Poland.

    Glassdoor doesn't have a public API, but their salary explore pages
    contain structured JSON-LD data we can extract.  We search for specific
    job titles and extract the salary ranges.
    """
    entries = []
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9,pl;q=0.8",
    }

    search_urls = [
        # Glassdoor salary explore — Poland, embedded roles
        "https://www.glassdoor.com/Salaries/poland-embedded-software-engineer-salary-SRCH_IL.0,6_IN193_KO7,33.htm",
        "https://www.glassdoor.com/Salaries/poland-firmware-engineer-salary-SRCH_IL.0,6_IN193_KO7,24.htm",
        "https://www.glassdoor.com/Salaries/poland-linux-engineer-salary-SRCH_IL.0,6_IN193_KO7,21.htm",
        "https://www.glassdoor.com/Salaries/poland-bsp-engineer-salary-SRCH_IL.0,6_IN193_KO7,19.htm",
    ]

    for url in search_urls:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            log(f"Glassdoor: failed to fetch {url.split('/')[-1][:40]}: {e}")
            continue

        # Extract JSON-LD structured data (salary schema)
        for m in re.finditer(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            html, re.S
        ):
            try:
                ld = json.loads(m.group(1))
            except (json.JSONDecodeError, ValueError):
                continue

            # Handle both single objects and arrays
            items = ld if isinstance(ld, list) else [ld]
            for item in items:
                if item.get("@type") not in ("OccupationAggregation",
                                              "Occupation",
                                              "OccupationAggregationByEmployer"):
                    continue

                sal = item.get("estimatedSalary", item.get("baseSalary", {}))
                if isinstance(sal, list):
                    sal = sal[0] if sal else {}

                val = sal.get("value", sal)
                if isinstance(val, dict):
                    low = val.get("minValue", val.get("value", 0))
                    high = val.get("maxValue", low)
                    currency = val.get("currency", sal.get("currency", "PLN"))
                    unit = val.get("unitText", sal.get("unitText", "MONTH"))
                else:
                    continue

                try:
                    low = int(float(low))
                    high = int(float(high))
                except (ValueError, TypeError):
                    continue

                # Convert yearly to monthly if needed
                if unit and "YEAR" in str(unit).upper():
                    low = low // 12
                    high = high // 12

                # Convert USD to PLN if needed
                if currency == "USD":
                    low = int(low * 4.05)
                    high = int(high * 4.05)
                    currency = "PLN"
                elif currency == "EUR":
                    low = int(low * 4.30)
                    high = int(high * 4.30)
                    currency = "PLN"

                if currency != "PLN" or low < 5000:
                    continue

                title = item.get("name", item.get("occupationName", ""))
                company = item.get("hiringOrganization", {})
                if isinstance(company, dict):
                    company = company.get("name", "")

                entries.append({
                    "source": "glassdoor",
                    "scan_date": datetime.now().strftime("%Y-%m-%d"),
                    "title": title or url.split("_KO")[1].split(".")[0].replace("-", " ") if "_KO" in url else "engineer",
                    "company": company,
                    "location": "Poland",
                    "salary_from": low,
                    "salary_to": high,
                    "salary_type": "gross",  # Glassdoor reports gross
                    "salary_b2b_net_pln": f"{int(low*0.77)}-{int(high*0.77)}",
                    "salary_currency": "PLN",
                    "url": url,
                })

        time.sleep(3)  # Be polite

    # Also try Glassdoor salary API-like endpoint
    try:
        api_url = "https://www.glassdoor.com/graph"
        payload = json.dumps([{
            "operationName": "SalarySearchResultsQuery",
            "variables": {
                "keyword": "embedded engineer",
                "locationId": 193, "locationType": "COUNTRY",
                "numResults": 20,
            },
        }]).encode()
        req = urllib.request.Request(
            api_url, data=payload, headers={
                **headers,
                "Content-Type": "application/json",
            }
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            api_data = json.loads(resp.read())
        if isinstance(api_data, list) and api_data:
            results = api_data[0].get("data", {}).get("salarySearchResults", {}).get("results", [])
            for r in results:
                pay = r.get("payPercentile", r.get("medianPay", {}))
                if isinstance(pay, dict):
                    median = pay.get("p50", pay.get("median", 0))
                    low = pay.get("p25", pay.get("p10", median))
                    high = pay.get("p75", pay.get("p90", median))
                    try:
                        median, low, high = int(median), int(low), int(high)
                    except (ValueError, TypeError):
                        continue
                    if median > 0:
                        entries.append({
                            "source": "glassdoor",
                            "scan_date": datetime.now().strftime("%Y-%m-%d"),
                            "title": r.get("jobTitle", "engineer"),
                            "company": r.get("employer", {}).get("name", ""),
                            "location": "Poland",
                            "salary_from": low,
                            "salary_to": high,
                            "salary_type": "gross",
                            "salary_b2b_net_pln": f"{int(low*0.77)}-{int(high*0.77)}",
                            "salary_currency": "PLN",
                            "url": "https://www.glassdoor.com/Salaries/poland-embedded-engineer-salary.htm",
                        })
    except Exception as e:
        log(f"Glassdoor API: {e}")

    log(f"Glassdoor: {len(entries)} salary records (Poland, embedded/driver)")
    return entries


# ── Analysis ───────────────────────────────────────────────────────────────

def parse_salary_range(s):
    """Parse '20000-30000' or '20000' into (low, high) ints."""
    if not s:
        return None, None
    s = str(s).replace(" ", "").replace(",", "")
    m = re.match(r"(\d+)\s*[-–]\s*(\d+)", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"(\d+)", s)
    if m:
        v = int(m.group(1))
        return v, v
    return None, None

def compute_statistics(entries):
    """Compute salary statistics from collected entries."""
    b2b_ranges = []
    for e in entries:
        sal = e.get("salary_b2b_net_pln") or e.get("salary_from")
        if e.get("salary_b2b_net_pln"):
            low, high = parse_salary_range(e["salary_b2b_net_pln"])
        elif e.get("salary_type") == "b2b" and e.get("salary_from"):
            low, high = e["salary_from"], e.get("salary_to", e["salary_from"])
        else:
            continue

        if low and high and low > 0:
            b2b_ranges.append({
                "low": low, "high": high, "mid": (low + high) // 2,
                "title": e.get("title", ""),
                "company": e.get("company", ""),
                "source": e.get("source", ""),
            })

    if not b2b_ranges:
        return {"sample_size": 0}

    mids = sorted(r["mid"] for r in b2b_ranges)
    lows = sorted(r["low"] for r in b2b_ranges)
    highs = sorted(r["high"] for r in b2b_ranges)

    def percentile(data, p):
        k = (len(data) - 1) * p / 100
        f = int(k)
        c = f + 1 if f + 1 < len(data) else f
        return data[f] + (k - f) * (data[c] - data[f])

    stats = {
        "sample_size": len(b2b_ranges),
        "b2b_net_pln_monthly": {
            "min": min(lows),
            "max": max(highs),
            "median_mid": int(percentile(mids, 50)),
            "p25_mid": int(percentile(mids, 25)),
            "p75_mid": int(percentile(mids, 75)),
            "avg_mid": int(sum(mids) / len(mids)),
        },
        "by_source": {},
        "top_paying": sorted(b2b_ranges, key=lambda r: r["high"], reverse=True)[:5],
    }

    # Breakdown by source
    sources = set(r["source"] for r in b2b_ranges)
    for src in sources:
        src_mids = [r["mid"] for r in b2b_ranges if r["source"] == src]
        if src_mids:
            stats["by_source"][src] = {
                "count": len(src_mids),
                "avg_mid": int(sum(src_mids) / len(src_mids)),
                "min": min(src_mids),
                "max": max(src_mids),
            }

    return stats

def llm_analyze_trends(today_stats, history):
    """Use LLM to analyze salary trends."""
    if today_stats.get("sample_size", 0) < 3:
        return "Insufficient data for trend analysis."

    # Build history context
    hist_lines = []
    recent = sorted(history.get("daily_snapshots", []), key=lambda s: s.get("date", ""))[-30:]
    for snap in recent:
        s = snap.get("stats", {}).get("b2b_net_pln_monthly", {})
        if s:
            hist_lines.append(f"{snap['date']}: median={s.get('median_mid',0)} PLN, "
                            f"n={snap.get('stats',{}).get('sample_size',0)}, "
                            f"range={s.get('min',0)}-{s.get('max',0)}")

    system = """You are a salary market analyst specializing in embedded Linux and camera driver
engineering roles in Poland. You analyze B2B net monthly rates in PLN.
Respond in concise bullet points. No markdown headers. /no_think"""

    prompt = f"""Today's salary snapshot ({today_stats['sample_size']} data points):
- Median midpoint: {today_stats['b2b_net_pln_monthly']['median_mid']} PLN/month B2B net
- Range: {today_stats['b2b_net_pln_monthly']['min']} – {today_stats['b2b_net_pln_monthly']['max']} PLN
- P25-P75: {today_stats['b2b_net_pln_monthly']['p25_mid']} – {today_stats['b2b_net_pln_monthly']['p75_mid']} PLN
- Top paying: {json.dumps(today_stats['top_paying'][:3], ensure_ascii=False)}

Recent 30-day history:
{chr(10).join(hist_lines) if hist_lines else "No prior history yet (first run)."}

Analyze:
1. Current market rate for this niche (embedded Linux camera drivers, Poland)
2. Any visible trends (rising/falling/stable) if history available
3. How this compares to FAANG vs silicon vs automotive tiers
4. Actionable insights for someone at T4 level (15yr exp) considering T5 promotion or external move"""

    return call_ollama(system, prompt, temperature=0.3, max_tokens=2000)


# ── History Management ─────────────────────────────────────────────────────

def load_history():
    """Load rolling salary history."""
    if HISTORY_FILE.exists():
        try:
            return json.load(open(HISTORY_FILE))
        except Exception:
            pass
    return {"daily_snapshots": [], "version": 1}

def save_history(history):
    """Save history, pruning entries older than HISTORY_DAYS."""
    cutoff = (datetime.now() - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")
    history["daily_snapshots"] = [
        s for s in history["daily_snapshots"]
        if s.get("date", "") >= cutoff
    ]
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


# ── Raw data file for scrape/analyze split ─────────────────────────────────
RAW_SALARY_FILE = SALARY_DIR / "raw-salary.json"


# ── Scrape phase ───────────────────────────────────────────────────────────

def run_scrape():
    """Collect salary data from all sources, compute statistics. Save raw JSON."""
    dt = datetime.now()
    today = dt.strftime("%Y-%m-%d")
    SALARY_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    # ── Collect from all sources ──
    all_entries = []
    scrape_errors = []

    log("Phase 1: Collecting salary data...")
    all_entries.extend(collect_from_career_scans())
    all_entries.extend(collect_from_nofluffjobs())
    time.sleep(2)
    all_entries.extend(collect_from_justjoinit())
    time.sleep(2)
    all_entries.extend(collect_from_bulldogjob())
    time.sleep(2)
    all_entries.extend(collect_from_levelsfyi())
    time.sleep(2)
    all_entries.extend(collect_from_glassdoor())

    log(f"Total: {len(all_entries)} salary records collected")

    # ── Compute statistics (pure math, no LLM) ──
    log("Phase 2: Computing statistics...")
    stats = compute_statistics(all_entries)
    log(f"Stats: {stats.get('sample_size', 0)} valid B2B salary ranges")

    # ── Update history (scrape phase owns this) ──
    history = load_history()
    history["daily_snapshots"].append({
        "date": today,
        "stats": stats,
        "record_count": len(all_entries),
    })
    save_history(history)

    # ── Save raw intermediate data ──
    scrape_duration = int(time.time() - t0)
    raw_data = {
        "scrape_timestamp": dt.isoformat(timespec="seconds"),
        "scrape_duration_seconds": scrape_duration,
        "scrape_version": 1,
        "data": {
            "entries": all_entries,
            "stats": stats,
            "sources": list(set(e.get("source", "unknown") for e in all_entries)),
        },
        "scrape_errors": scrape_errors,
    }
    tmp = RAW_SALARY_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(raw_data, f, indent=2, ensure_ascii=False)
    tmp.rename(RAW_SALARY_FILE)

    log(f"Scrape done: {len(all_entries)} records saved to {RAW_SALARY_FILE}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] salary-tracker scrape done ({scrape_duration}s)", flush=True)


# ── Analyze phase ──────────────────────────────────────────────────────────

def run_analyze():
    """Load raw data, run LLM trend analysis, save final output."""
    dt = datetime.now()
    SALARY_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    # Load raw data
    if not RAW_SALARY_FILE.exists():
        print(f"ERROR: Raw data file not found: {RAW_SALARY_FILE}", file=sys.stderr)
        print("Run with --scrape-only first to collect salary data.", file=sys.stderr)
        sys.exit(1)

    try:
        with open(RAW_SALARY_FILE) as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"ERROR: Failed to read raw data: {e}", file=sys.stderr)
        sys.exit(1)

    scrape_ts = raw.get("scrape_timestamp", "")
    all_entries = raw.get("data", {}).get("entries", [])
    stats = raw.get("data", {}).get("stats", {})
    sources = raw.get("data", {}).get("sources", [])

    # Check staleness
    if scrape_ts:
        try:
            scrape_dt = datetime.fromisoformat(scrape_ts)
            age_hours = (dt - scrape_dt).total_seconds() / 3600
            if age_hours > 48:
                log(f"WARNING: Raw data is {age_hours:.0f}h old (scraped {scrape_ts})")
        except ValueError:
            pass

    log(f"Loaded {len(all_entries)} salary records from raw data (scraped {scrape_ts})")

    # ── LLM trend analysis ──
    log("Phase 3: LLM trend analysis...")
    history = load_history()
    analysis = llm_analyze_trends(stats, history) or "LLM analysis unavailable."

    # ── Build output ──
    duration = int(time.time() - t0)
    snapshot = {
        "meta": {
            "scrape_timestamp": scrape_ts,
            "analyze_timestamp": dt.isoformat(timespec="seconds"),
            "timestamp": dt.isoformat(timespec="seconds"),  # backward compat
            "duration_seconds": duration,
            "total_records": len(all_entries),
            "sources": sources,
        },
        "stats": stats,
        "analysis": analysis,
        "entries": all_entries,
    }

    # Save daily snapshot
    fname = f"salary-{dt.strftime('%Y%m%d')}.json"
    out_path = SALARY_DIR / fname
    with open(out_path, "w") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)

    # Update latest symlink
    latest = SALARY_DIR / "latest-salary.json"
    latest.unlink(missing_ok=True)
    latest.symlink_to(fname)

    # Cleanup: keep last 60 daily snapshots
    snapshots = sorted(SALARY_DIR.glob("salary-2*.json"))
    for old in snapshots[:-60]:
        old.unlink(missing_ok=True)

    log(f"Saved: {out_path}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] salary-tracker analyze done ({duration}s)", flush=True)


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(description="Salary tracker — salary & rate intelligence")
    parser.add_argument('--scrape-only', action='store_true',
                        help='Only collect salary data, save raw (no LLM)')
    parser.add_argument('--analyze-only', action='store_true',
                        help='Only run LLM analysis on previously scraped raw data')
    args = parser.parse_args()

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] salary-tracker starting"
          f"{' (scrape-only)' if args.scrape_only else ' (analyze-only)' if args.analyze_only else ''}", flush=True)

    if args.scrape_only:
        run_scrape()
    elif args.analyze_only:
        run_analyze()
    else:
        # Legacy: full run (backward compatible)
        run_scrape()
        run_analyze()


if __name__ == "__main__":
    main()
