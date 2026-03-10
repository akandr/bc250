#!/usr/bin/env python3
"""
academic-watch.py — Scientific publications, dissertations & patents monitor
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Monitors academic literature across 4 topic areas:
  - kernel-drivers:       Linux kernel driver development, V4L2, device tree, MIPI CSI
  - camera-drivers:       Camera/image sensor driver architecture, ISP pipelines, libcamera
  - embedded-inference:   Edge AI hardware, NPU/TPU accelerators, on-device ML inference
  - adas-cameras:         In-cabin ADAS cameras, DMS/OMS, surround view, automotive vision

Content types (--type):
  - publication:   High-quality scientific papers (journals, conferences, arXiv)
  - dissertation:  MSc/PhD theses from university repositories
  - patent:        Recent patent publications (complementing existing patent-watch.py)

Sources:
  - Google Scholar (via DuckDuckGo)
  - arXiv API (free, structured)
  - Semantic Scholar API (free tier)
  - Google Patents (via DuckDuckGo, for patent mode)
  - University thesis repositories (via DuckDuckGo)

Output:
  - Think notes in /opt/netscan/data/think/  (appears on dashboard notes.html)
  - Structured JSON in /opt/netscan/data/academic/

Usage:
  python3 academic-watch.py --topic kernel-drivers --type publication
  python3 academic-watch.py --topic adas-cameras --type dissertation
  python3 academic-watch.py --topic embedded-inference --type patent
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
from datetime import datetime, timedelta
from pathlib import Path
from llm_sanitize import sanitize_llm_output

# ── Config ─────────────────────────────────────────────────────────────────
OLLAMA_URL = "http://localhost:11434"
OLLAMA_CHAT = f"{OLLAMA_URL}/api/chat"
OLLAMA_MODEL = "qwen3:14b"

DATA_DIR = Path("/opt/netscan/data")
ACADEMIC_DIR = DATA_DIR / "academic"
THINK_DIR = DATA_DIR / "think"

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

VALID_TOPICS = ["kernel-drivers", "camera-drivers", "embedded-inference", "adas-cameras"]
VALID_TYPES = ["publication", "dissertation", "patent"]

# ── Topic definitions ──────────────────────────────────────────────────────

TOPIC_CONFIG = {
    "kernel-drivers": {
        "label": "Linux Kernel Drivers",
        "scholar_queries": [
            '"Linux kernel" driver architecture subsystem',
            '"device tree" "kernel module" embedded Linux',
            'V4L2 "video4linux" driver implementation',
            '"MIPI CSI" driver Linux kernel',
            '"I2C" OR "SPI" kernel driver sensor',
            '"DMA" "interrupt handler" Linux kernel driver',
        ],
        "arxiv_queries": [
            'linux kernel driver',
            'device driver operating system',
            'embedded linux real-time',
        ],
        "patent_queries": [
            '"Linux kernel" AND ("driver" OR "module") AND ("method" OR "apparatus")',
            '"device driver" AND "embedded system" AND ("interface" OR "bus")',
            '"kernel module" AND ("hardware interface" OR "register access")',
        ],
        "thesis_queries": [
            'thesis "Linux kernel driver" development',
            'dissertation "device driver" Linux embedded',
            'MSc thesis "kernel module" implementation',
            'PhD dissertation "operating system driver" Linux',
        ],
        "relevance_keywords": [
            "linux", "kernel", "driver", "module", "device tree", "dts", "dtb",
            "v4l2", "i2c", "spi", "mipi", "csi", "dma", "interrupt", "probe",
            "platform driver", "subsystem", "kconfig", "devicetree", "regmap",
            "clk", "power domain", "pinctrl", "gpio", "iommu",
        ],
    },
    "camera-drivers": {
        "label": "Camera Driver Architecture",
        "scholar_queries": [
            '"camera driver" architecture "image sensor"',
            '"ISP pipeline" camera "image signal processor"',
            '"libcamera" OR "V4L2" camera framework',
            '"MIPI CSI-2" camera sensor interface driver',
            '"image sensor" driver "register configuration"',
            '"camera subsystem" Linux embedded automotive',
        ],
        "arxiv_queries": [
            'camera driver image sensor pipeline',
            'image signal processor ISP architecture',
            'camera sensor interface embedded system',
        ],
        "patent_queries": [
            '"camera driver" AND ("image sensor" OR "ISP") AND ("architecture" OR "pipeline")',
            '"image signal processor" AND ("camera" OR "imaging") AND ("driver" OR "firmware")',
            '"MIPI CSI" AND ("camera" OR "sensor") AND ("driver" OR "interface")',
        ],
        "thesis_queries": [
            'thesis "camera driver" "image sensor"',
            'dissertation "ISP pipeline" camera',
            'MSc "camera system" driver implementation Linux',
            'PhD thesis "image signal processor" architecture',
        ],
        "relevance_keywords": [
            "camera", "image sensor", "isp", "image signal processor", "mipi",
            "csi", "csi-2", "d-phy", "c-phy", "bayer", "raw", "demosaic",
            "libcamera", "v4l2", "media controller", "sensor driver", "i2c",
            "register", "streaming", "pipeline", "subdev", "video node",
        ],
    },
    "embedded-inference": {
        "label": "Hardware for Embedded Inference",
        "scholar_queries": [
            '"edge AI" hardware accelerator inference',
            '"neural processing unit" NPU embedded',
            '"on-device inference" "deep learning" hardware',
            '"TinyML" OR "tiny machine learning" hardware',
            'FPGA "neural network" inference accelerator',
            '"RISC-V" AI ML inference extension',
            '"quantization" "model compression" embedded inference',
        ],
        "arxiv_queries": [
            'edge AI inference hardware accelerator',
            'neural network hardware embedded system',
            'NPU accelerator on-device inference',
            'TinyML hardware architecture',
            'RISC-V machine learning extension',
        ],
        "patent_queries": [
            '"neural network" AND "hardware accelerator" AND ("edge" OR "embedded")',
            '"inference engine" AND ("low power" OR "embedded") AND ("accelerator" OR "processor")',
            '"NPU" AND ("neural" OR "AI") AND ("edge computing" OR "embedded")',
        ],
        "thesis_queries": [
            'thesis "edge AI" hardware inference',
            'dissertation "neural network accelerator" FPGA',
            'MSc "embedded inference" "deep learning"',
            'PhD "neural processing unit" architecture',
            'thesis TinyML hardware implementation',
        ],
        "relevance_keywords": [
            "edge ai", "npu", "neural processing unit", "inference", "accelerator",
            "tinyml", "fpga", "asic", "quantization", "int8", "int4",
            "on-device", "embedded", "low power", "risc-v", "tflite",
            "onnx", "tensorrt", "model compression", "pruning", "distillation",
            "hardware architecture", "dataflow", "systolic array", "dsp",
        ],
    },
    "adas-cameras": {
        "label": "In-Cabin ADAS Automotive Cameras",
        "scholar_queries": [
            '"driver monitoring system" DMS camera in-cabin',
            '"occupant monitoring" OMS camera automotive',
            '"ADAS camera" "functional safety" ISO 26262',
            '"surround view" camera automotive parking',
            '"in-cabin" camera "driver monitoring" "face detection"',
            '"automotive camera" "infrared" OR "near-infrared" NIR',
        ],
        "arxiv_queries": [
            'driver monitoring system camera automotive',
            'in-cabin occupant monitoring camera deep learning',
            'automotive ADAS camera perception',
            'surround view camera system automotive',
        ],
        "patent_queries": [
            '"driver monitoring" AND "camera" AND ("in-cabin" OR "interior")',
            '"ADAS camera" AND ("surround view" OR "parking") AND ("automotive")',
            '"occupant monitoring" AND ("camera" OR "sensor") AND ("vehicle" OR "automotive")',
        ],
        "thesis_queries": [
            'thesis "driver monitoring system" camera',
            'dissertation "ADAS camera" automotive',
            'MSc "in-cabin monitoring" camera vehicle',
            'PhD "occupant monitoring" camera deep learning',
            'thesis "surround view" camera automotive',
        ],
        "relevance_keywords": [
            "dms", "oms", "driver monitoring", "occupant monitoring", "in-cabin",
            "adas", "automotive camera", "surround view", "parking assist",
            "iso 26262", "asil", "functional safety", "infrared", "nir",
            "face detection", "drowsiness", "gaze tracking", "head pose",
            "camera system", "perception", "automotive", "vehicle",
        ],
    },
}


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


def save_note(task_type, title, content, context=None):
    """Save a thinking note (same format as idle-think.sh)."""
    THINK_DIR.mkdir(parents=True, exist_ok=True)
    dt = datetime.now()
    note = {
        "type": task_type,
        "title": title,
        "content": content,
        "generated": dt.isoformat(timespec="seconds"),
        "model": OLLAMA_MODEL,
        "context": context or {},
    }
    fname = f"note-{task_type}-{dt.strftime('%Y%m%d-%H%M')}.json"
    path = THINK_DIR / fname
    with open(path, "w") as f:
        json.dump(note, f, indent=2)
    log(f"  Saved note: {path}")

    # Update notes index
    index_path = THINK_DIR / "notes-index.json"
    index = []
    if index_path.exists():
        try:
            with open(index_path) as f:
                index = json.load(f)
        except Exception:
            pass
    index.insert(0, {
        "file": fname, "type": task_type, "title": title,
        "generated": note["generated"], "chars": len(content),
    })
    index = index[:200]  # keep generous buffer — shared with idle-think.sh
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)

    return note


# ── Source: Google Scholar via DDG ─────────────────────────────────────────

def search_scholar_ddg(query, max_results=10):
    """Search Google Scholar results via DuckDuckGo."""
    results = []
    encoded = urllib.parse.quote(f'site:scholar.google.com {query}')
    url = f"https://html.duckduckgo.com/html/?q={encoded}"

    html = fetch_url(url, timeout=25)
    if not html:
        return results

    # Extract titles and snippets from DDG results
    titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, re.DOTALL)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</[^>]+>', html, re.DOTALL)
    urls_found = re.findall(r'class="result__url"[^>]*>(.*?)</a>', html, re.DOTALL)

    for i in range(min(max_results, len(titles))):
        title = strip_html(titles[i])
        snippet = strip_html(snippets[i])[:500] if i < len(snippets) else ""
        link = strip_html(urls_found[i]).strip() if i < len(urls_found) else ""
        if not link.startswith("http"):
            link = "https://" + link if link else ""
        if title:
            results.append({
                "title": title,
                "abstract": snippet,
                "url": link,
                "source": "google_scholar",
            })

    return results


def search_ddg_direct(query, max_results=10):
    """Direct DDG search for academic content."""
    results = []
    encoded = urllib.parse.quote(query)
    url = f"https://html.duckduckgo.com/html/?q={encoded}"

    html = fetch_url(url, timeout=25)
    if not html:
        return results

    titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, re.DOTALL)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</[^>]+>', html, re.DOTALL)
    urls_found = re.findall(r'class="result__url"[^>]*>(.*?)</a>', html, re.DOTALL)

    for i in range(min(max_results, len(titles))):
        title = strip_html(titles[i])
        snippet = strip_html(snippets[i])[:500] if i < len(snippets) else ""
        link = strip_html(urls_found[i]).strip() if i < len(urls_found) else ""
        if not link.startswith("http"):
            link = "https://" + link if link else ""
        if title:
            results.append({
                "title": title,
                "abstract": snippet,
                "url": link,
                "source": "ddg",
            })

    return results


# ── Source: arXiv API ──────────────────────────────────────────────────────

def search_arxiv(query, max_results=10):
    """Search arXiv via their open API (Atom XML)."""
    results = []
    encoded = urllib.parse.quote(query)
    url = f"http://export.arxiv.org/api/query?search_query=all:{encoded}&start=0&max_results={max_results}&sortBy=submittedDate&sortOrder=descending"

    xml_text = fetch_url(url, timeout=30)
    if not xml_text:
        return results

    try:
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(xml_text)
        for entry in root.findall("atom:entry", ns):
            title_el = entry.find("atom:title", ns)
            summary_el = entry.find("atom:summary", ns)
            published_el = entry.find("atom:published", ns)
            # Get PDF link
            pdf_url = ""
            for link in entry.findall("atom:link", ns):
                if link.get("title") == "pdf":
                    pdf_url = link.get("href", "")
                    break
            # Entry link
            entry_url = ""
            id_el = entry.find("atom:id", ns)
            if id_el is not None:
                entry_url = id_el.text or ""

            # Authors
            authors = []
            for author in entry.findall("atom:author", ns):
                name = author.find("atom:name", ns)
                if name is not None:
                    authors.append(name.text or "")

            # Categories
            categories = []
            for cat in entry.findall("atom:category", ns):
                categories.append(cat.get("term", ""))

            title = (title_el.text or "").strip().replace("\n", " ") if title_el is not None else ""
            abstract = (summary_el.text or "").strip().replace("\n", " ")[:500] if summary_el is not None else ""
            published = (published_el.text or "")[:10] if published_el is not None else ""

            if title:
                results.append({
                    "title": title,
                    "abstract": abstract,
                    "url": entry_url or pdf_url,
                    "pdf_url": pdf_url,
                    "authors": authors[:5],
                    "published": published,
                    "categories": categories,
                    "source": "arxiv",
                })
    except ET.ParseError as e:
        log(f"  arXiv XML parse error: {e}")

    return results


# ── Source: Semantic Scholar API ───────────────────────────────────────────

def search_semantic_scholar(query, max_results=10):
    """Search Semantic Scholar API (free, no key for basic access)."""
    results = []
    encoded = urllib.parse.quote(query)
    url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={encoded}&limit={max_results}&fields=title,abstract,url,year,citationCount,authors,externalIds"

    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())

        for paper in data.get("data", []):
            title = paper.get("title", "")
            abstract = (paper.get("abstract") or "")[:500]
            paper_url = paper.get("url", "")
            year = paper.get("year", "")
            citations = paper.get("citationCount", 0)
            authors = [a.get("name", "") for a in (paper.get("authors") or [])[:5]]
            ext_ids = paper.get("externalIds") or {}
            arxiv_id = ext_ids.get("ArXiv", "")
            doi = ext_ids.get("DOI", "")

            if title:
                results.append({
                    "title": title,
                    "abstract": abstract,
                    "url": paper_url,
                    "year": year,
                    "citations": citations,
                    "authors": authors,
                    "arxiv_id": arxiv_id,
                    "doi": doi,
                    "source": "semantic_scholar",
                })
    except Exception as e:
        log(f"  Semantic Scholar error: {e}")

    return results


# ── Source: Google Patents via DDG ─────────────────────────────────────────

def search_google_patents(query_text, max_results=10):
    """Search Google Patents via DDG (same pattern as patent-watch.py)."""
    patents = []
    google_q = urllib.parse.quote(f'site:patents.google.com {query_text}')
    ddg_url = f"https://html.duckduckgo.com/html/?q={google_q}"

    html = fetch_url(ddg_url, timeout=25)
    if not html:
        return patents

    patent_ids = re.findall(
        r'patents\.google\.com/patent/([A-Z]{2}\d{4,}[A-Z0-9]*)', html
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
                "title": title,
                "abstract": abstract,
                "patent_id": patent_id,
                "assignee": assignee,
                "pub_date": pub_date,
                "url": detail_url,
                "source": "google_patents",
            })

    return patents


# ── Source: Thesis/Dissertation search via DDG ─────────────────────────────

def search_theses(query, max_results=10):
    """Search for theses/dissertations via DDG targeting academic repositories."""
    results = []
    encoded = urllib.parse.quote(query)
    url = f"https://html.duckduckgo.com/html/?q={encoded}"

    html = fetch_url(url, timeout=25)
    if not html:
        return results

    titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, re.DOTALL)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</[^>]+>', html, re.DOTALL)
    urls_found = re.findall(r'class="result__url"[^>]*>(.*?)</a>', html, re.DOTALL)

    # Also try NDLTD and university repo specific searches
    for i in range(min(max_results, len(titles))):
        title = strip_html(titles[i])
        snippet = strip_html(snippets[i])[:500] if i < len(snippets) else ""
        link = strip_html(urls_found[i]).strip() if i < len(urls_found) else ""
        if not link.startswith("http"):
            link = "https://" + link if link else ""
        if title:
            results.append({
                "title": title,
                "abstract": snippet,
                "url": link,
                "source": "thesis_search",
            })

    return results


# ── Relevance scoring ─────────────────────────────────────────────────────

def score_result(item, topic_config, content_type):
    """Score an academic result for relevance."""
    text = f"{item.get('title', '')} {item.get('abstract', '')}".lower()
    score = 0

    # Keyword matching
    for kw in topic_config.get("relevance_keywords", []):
        if kw.lower() in text:
            score += 2

    # Citation bonus (publications)
    citations = item.get("citations", 0)
    if citations and citations > 50:
        score += 5
    elif citations and citations > 20:
        score += 3
    elif citations and citations > 5:
        score += 1

    # Source quality bonus
    source = item.get("source", "")
    if source == "arxiv":
        score += 2
    elif source == "semantic_scholar":
        score += 2
    elif source == "google_scholar":
        score += 1

    # Recency bonus
    year = item.get("year") or item.get("published", "")[:4]
    if year:
        try:
            yr = int(year)
            if yr >= 2024:
                score += 4
            elif yr >= 2023:
                score += 3
            elif yr >= 2022:
                score += 2
            elif yr >= 2020:
                score += 1
        except ValueError:
            pass

    # Thesis/dissertation indicator words
    if content_type == "dissertation":
        thesis_kw = ["thesis", "dissertation", "msc", "phd", "master", "doctoral", "degree"]
        for kw in thesis_kw:
            if kw in text:
                score += 2

    # Patent indicators
    if content_type == "patent":
        patent_kw = ["patent", "assignee", "claims", "apparatus", "method"]
        for kw in patent_kw:
            if kw in text:
                score += 1

    # Quality indicators for publications
    if content_type == "publication":
        quality_kw = ["ieee", "acm", "springer", "elsevier", "journal", "transactions",
                      "conference", "proceedings", "cvpr", "iccv", "eccv", "nips", "neurips",
                      "icml", "aaai", "dac", "date", "isscc", "vlsi"]
        for kw in quality_kw:
            if kw in text:
                score += 2

    item["relevance_score"] = score
    return score


# ── Data collection per type ───────────────────────────────────────────────

def collect_publications(topic_id, topic_config):
    """Collect scientific publications from multiple sources."""
    all_results = []

    # Google Scholar via DDG
    for i, query in enumerate(topic_config["scholar_queries"]):
        log(f"Scholar query {i+1}/{len(topic_config['scholar_queries'])}: {query[:60]}...")
        results = search_scholar_ddg(query, max_results=6)
        all_results.extend(results)
        log(f"  → {len(results)} results")
        time.sleep(3)

    # arXiv
    for i, query in enumerate(topic_config["arxiv_queries"]):
        log(f"arXiv query {i+1}/{len(topic_config['arxiv_queries'])}: {query[:60]}...")
        results = search_arxiv(query, max_results=8)
        all_results.extend(results)
        log(f"  → {len(results)} results")
        time.sleep(2)

    # Semantic Scholar
    for i, query in enumerate(topic_config["scholar_queries"][:3]):  # use first 3 scholar queries
        log(f"Semantic Scholar query {i+1}/3: {query[:60]}...")
        results = search_semantic_scholar(query, max_results=8)
        all_results.extend(results)
        log(f"  → {len(results)} results")
        time.sleep(2)

    return all_results


def collect_dissertations(topic_id, topic_config):
    """Collect MSc/PhD dissertations."""
    all_results = []

    # DDG thesis search
    for i, query in enumerate(topic_config["thesis_queries"]):
        log(f"Thesis query {i+1}/{len(topic_config['thesis_queries'])}: {query[:60]}...")
        results = search_theses(query, max_results=8)
        all_results.extend(results)
        log(f"  → {len(results)} results")
        time.sleep(3)

    # Additional: search specific thesis repositories
    repo_queries = [
        f'site:etd.ohiolink.edu {topic_config["scholar_queries"][0]}',
        f'site:open.library.ubc.ca thesis {topic_config["scholar_queries"][0]}',
        f'site:repository.tudelft.nl {topic_config["scholar_queries"][0]}',
    ]
    for i, query in enumerate(repo_queries):
        log(f"Repo query {i+1}/{len(repo_queries)}: {query[:60]}...")
        results = search_ddg_direct(query, max_results=5)
        all_results.extend(results)
        log(f"  → {len(results)} results")
        time.sleep(3)

    return all_results


def collect_patents(topic_id, topic_config):
    """Collect patent publications."""
    all_results = []

    for i, query in enumerate(topic_config["patent_queries"]):
        log(f"Patent query {i+1}/{len(topic_config['patent_queries'])}: {query[:60]}...")
        results = search_google_patents(query, max_results=6)
        all_results.extend(results)
        log(f"  → {len(results)} results")
        time.sleep(4)

    # Also search DDG for patent news
    for query in topic_config["scholar_queries"][:2]:
        log(f"Patent news: {query[:60]}...")
        results = search_ddg_direct(f"{query} patent", max_results=5)
        all_results.extend(results)
        log(f"  → {len(results)} results")
        time.sleep(3)

    return all_results


# ── Deduplication ──────────────────────────────────────────────────────────

def dedup_results(results):
    """Deduplicate results by title similarity."""
    seen_titles = set()
    unique = []
    for r in results:
        title_key = re.sub(r'[^a-zA-Z0-9]', '', r.get("title", "").lower())[:80]
        if title_key and title_key not in seen_titles:
            seen_titles.add(title_key)
            unique.append(r)
    return unique


# ── LLM Analysis ──────────────────────────────────────────────────────────

def analyze_results(results, topic_id, topic_label, content_type):
    """Use LLM to analyze collected results and produce a summary."""
    if not results:
        return "No results found for analysis."

    type_labels = {
        "publication": "scientific publications (journals, conferences, preprints)",
        "dissertation": "MSc/PhD theses and dissertations",
        "patent": "patent publications",
    }

    system_prompt = f"""You are an expert academic researcher analyzing {type_labels.get(content_type, content_type)}
in the field of {topic_label}. You work at the hardware-software boundary in embedded systems,
with deep knowledge of Linux kernel development, camera drivers, and automotive ADAS.

Your analysis should be:
- Technically precise and actionable
- Focused on practical relevance for an embedded systems engineer
- Highlighting cutting-edge developments and research directions
- Noting which papers/works are from top venues or have high impact

Be concise but thorough. Format with clear sections and bullet points. /no_think"""

    # Build paper summaries for LLM
    paper_lines = []
    for i, r in enumerate(results[:15], 1):
        authors = ", ".join(r.get("authors", [])[:3]) if r.get("authors") else "Unknown"
        year = r.get("year") or r.get("published", "")[:4] or "?"
        citations = r.get("citations", "")
        cit_str = f" [{citations} citations]" if citations else ""
        score = r.get("relevance_score", 0)
        source = r.get("source", "?")

        paper_lines.append(
            f"{i}. [{source}] (score:{score}) \"{r.get('title', 'N/A')}\"\n"
            f"   Authors: {authors} | Year: {year}{cit_str}\n"
            f"   Abstract: {r.get('abstract', 'N/A')[:200]}"
        )

    papers_text = "\n\n".join(paper_lines)

    user_prompt = f"""Analyze these {len(results)} {content_type}s found for topic: {topic_label}

{papers_text}

Provide:
1. **Top 5 Most Relevant**: Which {content_type}s are most relevant and why?
2. **Key Research Directions**: What are the emerging trends in this space?
3. **Practical Impact**: What implications do these have for a camera driver / embedded systems engineer?
4. **Notable Authors/Groups**: Which research groups are leading this area?
5. **Read List**: Top 3 must-read {content_type}s from this batch, with brief justification.
6. **Gaps**: What important topics are under-represented in the current findings?"""

    return call_ollama(system_prompt, user_prompt, temperature=0.3, max_tokens=3000)


# ── DB management ──────────────────────────────────────────────────────────

def load_db(topic_id, content_type):
    """Load academic database for a topic+type combination."""
    db_path = ACADEMIC_DIR / f"db-{topic_id}-{content_type}.json"
    if db_path.exists():
        try:
            return json.load(open(db_path))
        except Exception:
            pass
    return {"entries": {}, "version": 1, "last_updated": ""}


def save_db(db, topic_id, content_type):
    """Save academic DB, enforcing size limits."""
    entries = db.get("entries", {})
    if len(entries) > 2000:
        cutoff = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        db["entries"] = {
            k: v for k, v in entries.items()
            if v.get("first_seen", "") >= cutoff
        }
    db["last_updated"] = datetime.now().isoformat(timespec="seconds")
    db_path = ACADEMIC_DIR / f"db-{topic_id}-{content_type}.json"
    with open(db_path, "w") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(description="Academic literature monitor")
    parser.add_argument("--topic", required=True, choices=VALID_TOPICS,
                        help="Research topic to monitor")
    parser.add_argument("--type", required=True, choices=VALID_TYPES,
                        dest="content_type", help="Type of academic content")
    parser.add_argument("--scrape-only", action="store_true",
                        help="Collect and score results — no LLM analysis")
    parser.add_argument("--analyze-only", action="store_true",
                        help="LLM analysis only — load previous scrape")
    args = parser.parse_args()

    mode = "scrape-only" if args.scrape_only else "analyze-only" if args.analyze_only else "full"

    topic_id = args.topic
    content_type = args.content_type
    topic_config = TOPIC_CONFIG[topic_id]
    topic_label = topic_config["label"]

    dt = datetime.now()
    print(f"[{dt.strftime('%Y-%m-%d %H:%M:%S')}] academic-watch starting: {topic_id} / {content_type} [{mode}]", flush=True)

    ACADEMIC_DIR.mkdir(parents=True, exist_ok=True)
    RAW_FILE = ACADEMIC_DIR / f"raw-{topic_id}-{content_type}.json"
    t0 = time.time()

    # ── Scrape phase ──
    results = []
    new_count = 0
    db = load_db(topic_id, content_type)
    scrape_timestamp = None

    if mode == "analyze-only":
        # Load from raw scrape data
        if not RAW_FILE.exists():
            print(f"ERROR: Raw scrape data not found: {RAW_FILE}")
            print("Run with --scrape-only first.")
            sys.exit(1)
        with open(RAW_FILE) as f:
            raw = json.load(f)
        rd = raw.get("data", {})
        results = rd.get("results", [])
        new_count = rd.get("new_count", 0)
        scrape_timestamp = raw.get("scrape_timestamp", "")
        log(f"[ANALYZE-ONLY] Loaded {len(results)} results (scraped {scrape_timestamp})")
    else:
        # Collect results based on content type
        log(f"Collecting {content_type}s for {topic_label}...")
        if content_type == "publication":
            raw_results = collect_publications(topic_id, topic_config)
        elif content_type == "dissertation":
            raw_results = collect_dissertations(topic_id, topic_config)
        elif content_type == "patent":
            raw_results = collect_patents(topic_id, topic_config)
        else:
            raw_results = []

        log(f"Raw results: {len(raw_results)}")

        # Dedup
        results = dedup_results(raw_results)
        log(f"After dedup: {len(results)}")

        # Score
        for r in results:
            score_result(r, topic_config, content_type)

        # Sort by relevance
        results.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)

        # Identify new entries
        new_count = 0
        for r in results:
            title_key = re.sub(r'[^a-zA-Z0-9]', '', r.get("title", "").lower())[:100]
            if title_key and title_key not in db.get("entries", {}):
                new_count += 1
                db.setdefault("entries", {})[title_key] = {
                    "first_seen": dt.strftime("%Y-%m-%d"),
                    "title": r.get("title", ""),
                    "score": r.get("relevance_score", 0),
                    "source": r.get("source", ""),
                }

        log(f"New entries: {new_count} / {len(results)} total")
        scrape_timestamp = datetime.now().isoformat(timespec="seconds")

    # ── Scrape-only: save raw data and exit ──
    if mode == "scrape-only":
        raw_data = {
            "scrape_timestamp": scrape_timestamp,
            "scrape_version": 1,
            "topic_id": topic_id,
            "content_type": content_type,
            "data": {
                "results": results,
                "new_count": new_count,
            },
            "scrape_errors": [],
        }
        _tmp = str(RAW_FILE) + ".tmp"
        with open(_tmp, "w") as f:
            json.dump(raw_data, f, indent=2, ensure_ascii=False, default=str)
        os.replace(_tmp, str(RAW_FILE))
        save_db(db, topic_id, content_type)
        log(f"[SCRAPE-ONLY] Saved: {RAW_FILE}")
        print(f"  {len(results)} results ({new_count} new), DB updated")
        sys.exit(0)

    # ── Analyze phase: LLM ──
    analysis = None
    if results:
        log("Running LLM analysis...")
        analysis = analyze_results(results, topic_id, topic_label, content_type)

    # Build output
    duration = int(time.time() - t0)
    output = {
        "meta": {
            "topic": topic_id,
            "topic_label": topic_label,
            "content_type": content_type,
            "scrape_timestamp": scrape_timestamp,
            "analyze_timestamp": datetime.now().isoformat(timespec="seconds"),
            "timestamp": dt.isoformat(timespec="seconds"),
            "duration_seconds": duration,
            "total_found": len(results),
            "new_entries": new_count,
            "db_total": len(db.get("entries", {})),
        },
        "top_results": [
            {
                "title": r.get("title", ""),
                "abstract": r.get("abstract", "")[:300],
                "url": r.get("url", ""),
                "source": r.get("source", ""),
                "score": r.get("relevance_score", 0),
                "authors": r.get("authors", []),
                "year": r.get("year") or r.get("published", "")[:4],
                "citations": r.get("citations", ""),
            }
            for r in results[:20]
        ],
        "analysis": analysis,
    }

    # Save structured JSON
    fname = f"{topic_id}-{content_type}-{dt.strftime('%Y%m%d')}.json"
    out_path = ACADEMIC_DIR / fname
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    # Symlink latest
    latest = ACADEMIC_DIR / f"latest-{topic_id}-{content_type}.json"
    latest.unlink(missing_ok=True)
    latest.symlink_to(fname)

    # Save think note for dashboard
    type_emoji = {"publication": "📄", "dissertation": "🎓", "patent": "📜"}
    note_type = content_type  # will be "publication", "dissertation", or "patent"

    note_title = f"{type_emoji.get(content_type, '📝')} {topic_label}: {content_type.title()} Scan — {dt.strftime('%d %b %Y')}"

    if analysis:
        note_content = f"Topic: {topic_label}\nType: {content_type.title()}\n"
        note_content += f"Found: {len(results)} results ({new_count} new)\n"
        note_content += f"Duration: {duration}s\n\n"
        note_content += "─" * 60 + "\n\n"
        note_content += analysis
        note_content += "\n\n" + "─" * 60 + "\n"
        note_content += f"\nTop results by relevance:\n"
        for i, r in enumerate(results[:5], 1):
            note_content += f"\n{i}. [{r.get('source', '?')}] (score: {r.get('relevance_score', 0)})\n"
            note_content += f"   {r.get('title', 'N/A')}\n"
            authors = ", ".join(r.get("authors", [])[:3]) if r.get("authors") else ""
            if authors:
                note_content += f"   Authors: {authors}\n"
            if r.get("url"):
                note_content += f"   URL: {r['url']}\n"
    else:
        note_content = f"Topic: {topic_label}\nType: {content_type.title()}\n"
        note_content += f"Found: {len(results)} results ({new_count} new)\n"
        note_content += "No LLM analysis generated (insufficient results or model unavailable).\n"

    save_note(note_type, note_title, note_content, context={
        "topic": topic_id,
        "content_type": content_type,
        "total_found": len(results),
        "new_entries": new_count,
    })

    # Save DB
    save_db(db, topic_id, content_type)

    # Cleanup: keep last 30 reports per topic+type
    pattern = f"{topic_id}-{content_type}-2*.json"
    reports = sorted(ACADEMIC_DIR.glob(pattern))
    for old in reports[:-30]:
        old.unlink(missing_ok=True)

    log(f"Saved: {out_path}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] academic-watch done ({duration}s): "
          f"{len(results)} results, {new_count} new", flush=True)


if __name__ == "__main__":
    main()
