#!/usr/bin/env python3
"""
patent-watch.py — IR/RGB camera & kernel driver patent monitor
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Monitors patent publications related to:
  - IR/RGB camera systems, dual/multi-spectral imaging
  - Camera driver architectures, ISP pipelines
  - MIPI CSI-2 / D-PHY / C-PHY implementations
  - DMS/OMS driver monitoring, occupant sensing
  - Automotive camera systems, surround view
  - V4L2, libcamera, sensor fusion
  - Functional safety in camera systems

Sources:
  - Google Patents (public search, no API key needed)
  - European Patent Office (EPO) Open Patent Services
  - USPTO PatFT/AppFT (full-text search)
  - Lens.org (open scholarly patent search)

Output: /opt/netscan/data/patents/
  - patents-YYYYMMDD.json     (daily new findings)
  - patent-db.json            (rolling knowledge base, deduped)
  - latest-patents.json       (symlink)

Cron: 0 3 * * * flock -w 1200 /tmp/ollama-gpu.lock python3 /opt/netscan/patent-watch.py
"""

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

# ── Config ─────────────────────────────────────────────────────────────────
OLLAMA_URL = "http://localhost:11434"
OLLAMA_CHAT = f"{OLLAMA_URL}/api/chat"
OLLAMA_MODEL = "huihui_ai/qwen3-abliterated:14b"

PATENT_DIR = Path("/opt/netscan/data/patents")
PATENT_DB = PATENT_DIR / "patent-db.json"
DB_MAX_ENTRIES = 2000  # rolling cap
DB_MAX_DAYS = 365

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

# Patent search queries — targeting IR/RGB camera at kernel/driver level
SEARCH_QUERIES = [
    # Core camera driver / hardware interface
    {
        "id": "mipi_csi_camera",
        "query": "MIPI CSI camera driver image sensor interface",
        "google_q": '("MIPI CSI" OR "MIPI CSI-2") AND ("camera driver" OR "image sensor" OR "ISP")',
        "relevance_keywords": ["mipi", "csi", "driver", "sensor", "interface", "register",
                               "i2c", "spi", "d-phy", "c-phy", "dphy", "cphy"],
    },
    {
        "id": "ir_rgb_dual_camera",
        "query": "infrared RGB dual camera system driver monitoring",
        "google_q": '("infrared" OR "IR camera" OR "near-infrared") AND ("RGB" OR "visible") AND ("driver monitoring" OR "DMS" OR "occupant")',
        "relevance_keywords": ["infrared", "ir", "nir", "rgb", "dual", "multi-spectral",
                               "driver monitoring", "dms", "oms", "occupant", "face detection"],
    },
    {
        "id": "isp_pipeline",
        "query": "image signal processor pipeline camera kernel",
        "google_q": '("image signal processor" OR "ISP pipeline") AND ("camera" OR "imaging") AND ("kernel" OR "driver" OR "firmware")',
        "relevance_keywords": ["isp", "image signal", "pipeline", "raw", "bayer",
                               "demosaic", "denoise", "tone mapping", "hdr"],
    },
    {
        "id": "automotive_camera_adas",
        "query": "automotive camera system ADAS surround view functional safety",
        "google_q": '("automotive camera" OR "ADAS camera") AND ("surround view" OR "driver monitoring" OR "functional safety")',
        "relevance_keywords": ["automotive", "adas", "surround view", "parking",
                               "functional safety", "iso 26262", "asil", "camera system"],
    },
    {
        "id": "camera_sensor_fusion",
        "query": "camera radar sensor fusion embedded system",
        "google_q": '("sensor fusion" OR "multi-modal") AND ("camera" AND ("radar" OR "lidar")) AND ("embedded" OR "automotive")',
        "relevance_keywords": ["sensor fusion", "multi-modal", "camera", "radar",
                               "lidar", "perception", "embedded", "real-time"],
    },
    {
        "id": "v4l2_libcamera",
        "query": "video4linux V4L2 camera framework linux kernel",
        "google_q": '("V4L2" OR "video4linux" OR "libcamera") AND ("patent" OR "method" OR "apparatus")',
        "relevance_keywords": ["v4l2", "video4linux", "libcamera", "media controller",
                               "linux", "kernel", "framework", "camera subsystem"],
    },
]

# Key patent assignees to watch
WATCHED_ASSIGNEES = [
    "qualcomm", "nvidia", "samsung", "intel", "arm", "sony",
    "omnivision", "onsemi", "texas instruments", "nxp",
    "mobileye", "ambarella", "renesas", "mediatek",
    "harman", "continental", "bosch", "valeo", "aptiv",
    "google", "apple", "microsoft", "meta", "amazon",
]


# ── Helpers ────────────────────────────────────────────────────────────────

def log(msg):
    print(f"  {msg}", flush=True)

def fetch_url(url, timeout=30):
    """Fetch URL, return text or None."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/json,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "identity",
            "Referer": "https://www.google.com/",
            "DNT": "1",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return data.decode(charset, errors="replace")
    except Exception as e:
        log(f"  fetch error {url}: {e}")
        return None

def strip_html(html):
    """Remove HTML tags."""
    if not html:
        return ""
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def call_ollama(system_prompt, user_prompt, temperature=0.2, max_tokens=2000):
    """Call Ollama for LLM analysis."""
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
        "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": 12288},
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
            return content
    except Exception as e:
        log(f"  Ollama call failed: {e}")
        return None


# ── Google Patents Scraping ────────────────────────────────────────────────

def search_google_patents(query_text, max_results=15):
    """Search Google Patents via DuckDuckGo (search page is JS-rendered SPA).
    Discover patent IDs through DDG, then fetch detail pages (server-rendered)."""
    patents = []
    # Use DDG to find patents on Google Patents
    google_q = urllib.parse.quote(f'site:patents.google.com {query_text}')
    ddg_url = f"https://html.duckduckgo.com/html/?q={google_q}"

    html = fetch_url(ddg_url, timeout=25)
    if not html:
        return patents

    # Extract patent IDs from DDG result URLs
    patent_ids = re.findall(
        r'patents\.google\.com/patent/([A-Z]{2}\d{4,}[A-Z0-9]*)',
        html
    )
    seen = set()
    unique_ids = []
    for pid in patent_ids:
        if pid not in seen:
            seen.add(pid)
            unique_ids.append(pid)

    for patent_id in unique_ids[:max_results]:
        detail_url = f"https://patents.google.com/patent/{patent_id}/en"
        detail = fetch_url(detail_url, timeout=20)
        title = ""
        abstract = ""
        assignee = ""
        pub_date = ""
        if detail:
            abs_m = re.search(r'<div class="abstract[^"]*"[^>]*>(.*?)</div>', detail, re.DOTALL)
            if abs_m:
                abstract = strip_html(abs_m.group(1))[:500]
            title_m = re.search(r'<span class="title[^"]*"[^>]*>(.*?)</span>', detail, re.DOTALL)
            if not title_m:
                title_m = re.search(r'<title>(.*?)[-\u2013]', detail)
            if title_m:
                title = strip_html(title_m.group(1))
            assignee_m = re.search(r'(?:Current Assignee|Original Assignee)[^<]*<[^>]*>([^<]+)', detail, re.IGNORECASE)
            if assignee_m:
                assignee = strip_html(assignee_m.group(1))
            date_m = re.search(r'(?:Publication date|Filing date)[^<]*<[^>]*>(\d{4}-\d{2}-\d{2})', detail)
            if date_m:
                pub_date = date_m.group(1)
            time.sleep(2)

        if title or abstract:
            patents.append({
                "patent_id": patent_id,
                "title": title,
                "abstract": abstract,
                "assignee": assignee,
                "pub_date": pub_date,
                "url": detail_url,
                "source": "google_patents",
            })

    return patents

    html = fetch_url(url, timeout=30)


# ── Lens.org Search ────────────────────────────────────────────────────────

def search_lens_org(query_text, max_results=10):
    """Search for patents via DuckDuckGo (Lens.org is a JS SPA, not scrapeable).
    Uses DDG to find patent-related results and extracts patent IDs."""
    patents = []
    encoded = urllib.parse.quote(f'{query_text} patent publication')
    url = f"https://html.duckduckgo.com/html/?q={encoded}"

    html = fetch_url(url, timeout=25)
    if not html:
        return patents

    # Extract patent IDs from DDG results (any source)
    patent_ids = re.findall(
        r'\b((?:US|EP|WO|CN|JP|KR)\d{7,}[A-Z0-9]*)\b',
        html
    )
    seen = set()
    unique_ids = []
    for pid in patent_ids:
        if pid not in seen:
            seen.add(pid)
            unique_ids.append(pid)

    # Also extract DDG result titles and snippets as supplementary data
    ddg_titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, re.DOTALL)
    ddg_snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</[^>]+>', html, re.DOTALL)

    for i, patent_id in enumerate(unique_ids[:max_results]):
        title = strip_html(ddg_titles[i]) if i < len(ddg_titles) else ""
        snippet = strip_html(ddg_snippets[i])[:300] if i < len(ddg_snippets) else ""
        patents.append({
            "patent_id": patent_id,
            "title": title,
            "abstract": snippet,
            "assignee": "",
            "pub_date": "",
            "url": f"https://patents.google.com/patent/{patent_id}/en",
            "source": "ddg_patent_search",
        })

    return patents


# ── DuckDuckGo Patent News ────────────────────────────────────────────────

def search_ddg_patent_news(query):
    """Search DDG for recent patent-related news articles."""
    results = []
    encoded = urllib.parse.quote(f"{query} patent")
    url = f"https://html.duckduckgo.com/html/?q={encoded}&t=h_&iar=news&ia=news"

    html = fetch_url(url, timeout=20)
    if not html:
        return results

    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</[^>]+>', html, re.DOTALL)
    titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, re.DOTALL)

    for i in range(min(5, len(snippets))):
        results.append({
            "title": strip_html(titles[i]) if i < len(titles) else "",
            "snippet": strip_html(snippets[i])[:300],
            "source": "ddg_news",
        })

    return results


# ── Patent Relevance Scoring ──────────────────────────────────────────────

def score_patent(patent, query_config):
    """Score a patent's relevance to our interests."""
    text = f"{patent.get('title', '')} {patent.get('abstract', '')}".lower()
    score = 0

    # Keyword matching from query config
    for kw in query_config.get("relevance_keywords", []):
        if kw.lower() in text:
            score += 2

    # Watched assignee bonus
    assignee_lower = patent.get("assignee", "").lower()
    for wa in WATCHED_ASSIGNEES:
        if wa in assignee_lower:
            score += 3
            break

    # Kernel/driver level indicators
    driver_kw = ["driver", "kernel", "firmware", "register", "hardware",
                 "interface", "bus", "controller", "dma", "interrupt"]
    for kw in driver_kw:
        if kw in text:
            score += 1

    # IR/RGB specific indicators
    ir_kw = ["infrared", "near-infrared", "nir", "thermal",
             "multi-spectral", "dual-band", "rgb-ir"]
    for kw in ir_kw:
        if kw in text:
            score += 2

    patent["relevance_score"] = score
    return score


# ── Analysis ───────────────────────────────────────────────────────────────

def analyze_patents_batch(patents, query_id):
    """Use LLM to analyze a batch of patents for relevance and insights."""
    if not patents:
        return None

    system = """You are a patent analyst specializing in camera/imaging systems at the
hardware-software interface level (Linux kernel drivers, ISP, MIPI CSI, sensor interfaces).
You focus on IR/RGB dual imaging, automotive camera (DMS/OMS), and embedded vision.
Analyze the patents and identify which are most relevant to practical driver-level work.
Respond in JSON with keys:
- top_patents: list of {id, title, why_relevant, potential_impact} for the 3 most relevant
- technology_trends: list of 2-3 emerging patterns across these patents
- assignee_activity: which companies are most active in this area
- practical_relevance: one paragraph on what this means for a camera driver engineer
Output ONLY valid JSON. /no_think"""

    patent_text = "\n".join(
        f"[{p['patent_id']}] {p.get('title', 'N/A')}\n"
        f"  Assignee: {p.get('assignee', 'N/A')}\n"
        f"  Abstract: {p.get('abstract', 'N/A')[:200]}\n"
        for p in patents[:10]
    )

    prompt = f"""Patent search: {query_id}
Found {len(patents)} patents. Top entries:

{patent_text}

Analyze relevance to:
- Linux camera driver development (V4L2, MIPI CSI-2, ISP)
- IR/RGB dual imaging for driver monitoring (DMS/OMS)
- Automotive camera systems (surround view, ADAS)
- Practical implementation considerations at the kernel/driver level"""

    raw = call_ollama(system, prompt, temperature=0.2, max_tokens=2000)
    if raw:
        try:
            json_m = re.search(r'\{.*\}', raw, re.DOTALL)
            if json_m:
                return json.loads(json_m.group())
        except json.JSONDecodeError:
            return {"raw_analysis": raw[:1000]}
    return None


# ── DB Management ──────────────────────────────────────────────────────────

def load_db():
    """Load patent database."""
    if PATENT_DB.exists():
        try:
            return json.load(open(PATENT_DB))
        except Exception:
            pass
    return {"patents": {}, "version": 1, "last_updated": ""}

def save_db(db):
    """Save patent DB, enforcing size limits."""
    patents = db.get("patents", {})
    if len(patents) > DB_MAX_ENTRIES:
        # Remove oldest entries
        cutoff = (datetime.now() - timedelta(days=DB_MAX_DAYS)).strftime("%Y-%m-%d")
        db["patents"] = {
            k: v for k, v in patents.items()
            if v.get("first_seen", "") >= cutoff
        }
    db["last_updated"] = datetime.now().isoformat(timespec="seconds")
    with open(PATENT_DB, "w") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

    dt = datetime.now()
    today = dt.strftime("%Y-%m-%d")
    print(f"[{dt.strftime('%Y-%m-%d %H:%M:%S')}] patent-watch starting", flush=True)

    PATENT_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    db = load_db()
    all_new_patents = []
    query_results = {}

    for qcfg in SEARCH_QUERIES:
        qid = qcfg["id"]
        log(f"Search: {qid}")

        patents = []

        # Google Patents
        gp = search_google_patents(qcfg["google_q"], max_results=10)
        patents.extend(gp)
        log(f"  Google Patents: {len(gp)} results")
        time.sleep(3)

        # Lens.org
        lo = search_lens_org(qcfg["query"], max_results=8)
        patents.extend(lo)
        log(f"  Lens.org: {len(lo)} results")
        time.sleep(2)

        # DDG patent news
        news = search_ddg_patent_news(qcfg["query"])
        log(f"  Patent news: {len(news)} articles")

        # Dedup by patent_id
        seen_ids = set()
        unique_patents = []
        for p in patents:
            pid = p.get("patent_id", "")
            if pid and pid not in seen_ids:
                seen_ids.add(pid)
                score_patent(p, qcfg)
                unique_patents.append(p)

        # Sort by relevance
        unique_patents.sort(key=lambda p: p.get("relevance_score", 0), reverse=True)

        # Filter new patents (not in DB)
        new_patents = [p for p in unique_patents if p["patent_id"] not in db.get("patents", {})]
        all_new_patents.extend(new_patents)

        # LLM analysis if we have enough patents
        analysis = None
        if len(unique_patents) >= 3:
            analysis = analyze_patents_batch(unique_patents[:10], qid)
            time.sleep(3)

        query_results[qid] = {
            "query": qcfg["query"],
            "total_found": len(unique_patents),
            "new_patents": len(new_patents),
            "top_patents": unique_patents[:5],
            "news": news,
            "analysis": analysis,
        }

        # Update DB with all found patents
        for p in unique_patents:
            pid = p["patent_id"]
            if pid not in db.get("patents", {}):
                db.setdefault("patents", {})[pid] = {
                    "first_seen": today,
                    "title": p.get("title", ""),
                    "assignee": p.get("assignee", ""),
                    "relevance_score": p.get("relevance_score", 0),
                    "query_source": qid,
                }

    # ── Cross-query synthesis ──
    if all_new_patents:
        log("Synthesis: cross-query patent trends...")
        synthesis_system = """You are a patent intelligence analyst focused on camera/imaging
technology at the hardware-software boundary. Synthesize findings across multiple search queries.
Be concise and actionable. Focus on what matters for a camera driver engineer. /no_think"""

        new_summary = "\n".join(
            f"• [{p['patent_id']}] {p.get('title','?')} (assignee: {p.get('assignee','?')}, score: {p.get('relevance_score',0)})"
            for p in sorted(all_new_patents, key=lambda p: p.get("relevance_score", 0), reverse=True)[:15]
        )

        synthesis_prompt = f"""New patents found today: {len(all_new_patents)} across {len(SEARCH_QUERIES)} queries.

Top new patents:
{new_summary}

Query breakdown:
{chr(10).join(f'  {qid}: {qr["total_found"]} found, {qr["new_patents"]} new' for qid, qr in query_results.items())}

Provide:
1. Most significant new patent(s) and why they matter
2. Technology direction: where is camera driver IP heading?
3. Which companies are most aggressively patenting in this space?
4. Any patents that could affect open-source camera subsystems (V4L2, libcamera)?"""

        synthesis = call_ollama(synthesis_system, synthesis_prompt, temperature=0.3, max_tokens=1500)
    else:
        synthesis = "No new patents found today."

    # ── Save output ──
    duration = int(time.time() - t0)
    output = {
        "meta": {
            "timestamp": dt.isoformat(timespec="seconds"),
            "duration_seconds": duration,
            "queries_run": len(SEARCH_QUERIES),
            "total_patents_found": sum(qr["total_found"] for qr in query_results.values()),
            "new_patents": len(all_new_patents),
            "db_total": len(db.get("patents", {})),
        },
        "queries": query_results,
        "synthesis": synthesis,
    }

    fname = f"patents-{dt.strftime('%Y%m%d')}.json"
    out_path = PATENT_DIR / fname
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    latest = PATENT_DIR / "latest-patents.json"
    latest.unlink(missing_ok=True)
    latest.symlink_to(fname)

    save_db(db)

    # Cleanup: keep last 60 reports
    reports = sorted(PATENT_DIR.glob("patents-2*.json"))
    for old in reports[:-60]:
        old.unlink(missing_ok=True)

    log(f"Saved: {out_path}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] patent-watch done ({duration}s)", flush=True)


if __name__ == "__main__":
    main()
