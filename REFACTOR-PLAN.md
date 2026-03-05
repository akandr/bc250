# REFACTOR PLAN: Split Scraping from LLM Analysis

**Created:** 2025-06-01  
**Purpose:** Implementation guide for splitting all 14 hybrid netscan scripts into `--scrape-only` / `--analyze-only` modes. Written as a self-contained reference for a fresh session with no prior context.

---

## Table of Contents
1. [System Overview](#system-overview)
2. [Why We're Doing This](#why)
3. [Architecture: Before and After](#architecture)
4. [The 14 Scripts to Split](#scripts-to-split)
5. [Per-Script Split Plans](#per-script-plans)
6. [Intermediate Data Format](#intermediate-data-format)
7. [Queue-Runner Changes](#queue-runner-changes)
8. [Dashboard Changes](#dashboard-changes)
9. [Signal Notification Policy](#signal-policy)
10. [Jobs.json Changes](#jobs-json-changes)
11. [Deployment Procedure](#deployment)
12. [Dangers and Pitfalls](#dangers)
13. [Testing Checklist](#testing)
14. [Migration Order](#migration-order)

---

## 1. System Overview <a name="system-overview"></a>

**Hardware:** BC-250 — AMD Zen 2 + Cyan Skillfish RDNA1 (8GB VRAM), Fedora 43, IP: 192.168.3.151  
**LLM:** Ollama with huihui_ai/qwen3-abliterated:14b, 16K context, Vulkan backend, port 11434  
**Orchestrator:** queue-runner.py v5.1 at /opt/netscan/queue-runner.py, runs as systemd service  
**Jobs:** 310 jobs in /home/akandr/.openclaw/cron/jobs.json  
**Dashboard:** generate-html.py → 26 HTML pages at /opt/netscan/web/, served via nginx :8888  
**Signal:** JSON-RPC at http://127.0.0.1:8080/api/v1/rpc for notifications  
**Deploy path:** Edit in /Users/akandr/projects/bc250/netscan/ → scp to bc250:/tmp/ → ssh sudo cp to /opt/netscan/  

**Queue-runner v5.1 architecture:**
- Nightly batch starts 23:00, NIGHTLY_MAX_HOURS=8
- Batch ordering: infra → academic → misc → company-think → career-think → repo-think → lore → repo-scan → weekly  
- Think jobs run BEFORE slow data-gathering (feeds next night's analysis with prior night's data)
- CATEGORY_TIMEOUT_CAPS: lore- → 600s (10min), repo-scan → 1500s (25min)
- SKIPPABLE_PREFIXES after time budget: lore-, repo-scan-, repo-digest
- HA observations run opportunistically during GPU idle (daytime)
- GPU idle detection: pp_dpm_sclk < 1200 MHz

---

## 2. Why We're Doing This <a name="why"></a>

**Current problem:** All 14 hybrid scripts do both web scraping AND LLM analysis in one run. This means:
- A scraping failure (network timeout, site down) also kills the LLM analysis
- Can't retry LLM analysis without re-scraping (wasting 10-30 min)
- Can't see on dashboard when data was last scraped vs. last analyzed
- Can't schedule scraping independently (e.g. more frequent scraping, less frequent LLM)
- Debugging is harder: is it a scraping bug or an LLM prompt bug?

**User's directive (exact):**
> "Rework all of them and make sure dashboard will also indicate dates of latest thinking and scraping so I can easily monitor health of that"
> "Don't want Signal notifications on scraping done, only if LLM thinks it found something very interesting. Or there is a scraping failure I need to look at."

**What we gain:**
1. **Reliability:** Scraping can fail and retry without wasting GPU time
2. **Debuggability:** Separate timestamps for "when was data collected" vs "when was it analyzed"
3. **Flexibility:** Can scrape more often than analyze (or vice versa)
4. **Dashboard health:** User can see at a glance if scraping or analysis is stale
5. **Better GPU utilization:** Scrape-only jobs don't need GPU at all

---

## 3. Architecture: Before and After <a name="architecture"></a>

### BEFORE (current):
```
career-scan.py  →  [scrape websites] → [LLM analyze] → latest-scan.json
                                                         (one timestamp)
```

### AFTER (target):
```
career-scan.py --scrape-only  →  [scrape websites] → raw-careers.json
                                                      (scrape_timestamp)
career-scan.py --analyze-only →  [read raw-careers.json] → [LLM analyze] → latest-scan.json
                                                            (scrape_timestamp + analyze_timestamp)
```

### Key Design Decisions:
- **Same script, different modes** — NOT separate files. Keeps code co-located, reduces drift.
- **Intermediate file:** `raw-*.json` in each module's data directory (e.g., `data/career/raw-careers.json`)
- **Both timestamps in final output:** `meta.scrape_timestamp` + `meta.analyze_timestamp`
- **Backward compatible:** Running without flags = old behavior (scrape + analyze). This is CRITICAL for safety during rollout.
- **Scrape-only never needs GPU** — can run without ollama being available
- **Analyze-only never needs network** — can run on cached data

---

## 4. The 14 Scripts to Split <a name="scripts-to-split"></a>

### Script Classification

| # | Script | Lines | Difficulty | Signal? | Notes |
|---|--------|-------|-----------|---------|-------|
| 1 | event-scout.py | 1566 | EASY | No | Clean phases: collect→score→LLM |
| 2 | career-scan.py | 1313 | EASY | YES (score≥70) | 3 scrape phases, then LLM summary |
| 3 | salary-tracker.py | 866 | EASY | No | Collectors→stats→LLM trends |
| 4 | patent-watch.py | 709 | MEDIUM | No | Per-query LLM + cross-synthesis |
| 5 | city-watch.py | 560 | EASY | YES (score≥5) | Single scrape→single LLM |
| 6 | car-tracker.py | 836 | EASY | No | GPS data→trips/stops→LLM |
| 7 | csi-sensor-watch.py | 953 | MEDIUM | YES (new findings) | 7 scrape steps→LLM |
| 8 | radio-scan.py | 775 | EASY | YES (score≥40) | Forum scrape→LLM briefing |
| 9 | ha-correlate.py | 1601 | MEDIUM | YES (health thresholds) | Heavy stats computation, then LLM |
| 10 | ha-journal.py | 542 | EASY | No | Calls ha-observe→LLM journal |
| 11 | leak-monitor.py | 1756 | MEDIUM | YES (critical/high) | 11 scanners; DIFFERENT LLM MODEL! |
| 12 | company-intel.py | 1582 | **HARD** | No | Interleaved scrape+LLM per company |
| 13 | lore-digest.sh | 991 | **HARD** | YES (always) | Shell+Python heredoc, 3 passes |
| 14 | repo-think.py | 687 | EASY | No | DDG search→LLM; uses /api/generate not /api/chat! |

### Scripts that DON'T need changes:
- **3 LLM-only:** career-think.py, company-think.py, market-think.py (already read from JSON)
- **8 Pure scrape:** watchdog.py, gpu-monitor.py, ha-observe.py, generate-html.py, repo-watch.sh, scan.sh, enumerate.sh, vulnscan.sh

### Additional scripts NOT in netscan/ but in jobs.json:
- academic-watch.py (HYBRID — not audited yet, uses --topic/--type args already)
- system-think.py (LLM_ONLY — reads system data)
- life-think.py (LLM_ONLY — cross-domain synthesis)

---

## 5. Per-Script Split Plans <a name="per-script-plans"></a>

### Pattern for ALL scripts:

```python
# At the top of main():
import argparse
parser = argparse.ArgumentParser()
parser.add_argument('--scrape-only', action='store_true', help='Only scrape, save raw data')
parser.add_argument('--analyze-only', action='store_true', help='Only LLM analyze from raw data')
args = parser.parse_args()

if args.scrape_only:
    run_scrape()
    sys.exit(0)
elif args.analyze_only:
    run_analyze()
    sys.exit(0)
else:
    # Legacy: full run (backward compatible)
    run_scrape()
    run_analyze()
```

---

### 5.1 event-scout.py (EASY)

**File:** /opt/netscan/event-scout.py (1566 lines)  
**main():** line 1374  
**Scraping ends:** line ~1486 (after Phase 2 scoring, before Phase 3 LLM)  
**First LLM:** line 1487 — `llm_analyze_events(deduped[:25])`  
**Raw data variables:** `all_events` (list), `deduped` (list), `sources_meta` (dict)  
**Output dir:** `/opt/netscan/data/events/`  
**Current output:** `latest-events.json`  
**Intermediate file:** `data/events/raw-events.json`  

**Split plan:**
- `--scrape-only`: Run Phase 1 (collect from all sources) + Phase 2 (dedup/score). Save `raw-events.json` with: `{"scrape_timestamp": ..., "events": deduped, "sources_meta": sources_meta}`
- `--analyze-only`: Load `raw-events.json`. Run Phase 3 (llm_analyze_events on top 25). Save final `latest-events.json` with both scrape_timestamp and analyze_timestamp.
- Phase 2 scoring is keyword-based (no LLM) — goes with scraping.

**Signal:** No signal_send in this script.

---

### 5.2 career-scan.py (EASY)

**File:** /opt/netscan/career-scan.py (1313 lines)  
**main():** line 1225  
**Scraping phases:** Phase 1 scan_career_pages() → Phase 2 scan_job_boards() → Phase 3 scan_intel_sources()  
**LLM call:** generate_summary(all_jobs, intel_data, scan_meta) — single call  
**Signal:** send_hot_alerts() for jobs with score≥70 — **this is LLM-independent**, based on keyword matching during scraping  
**Raw data variables:** `career_jobs`, `career_results`, `board_jobs`, `board_results`, `intel_data`, `intel_results`  
**Output dir:** `/opt/netscan/data/career/`  
**Current output:** `latest-scan.json`  
**Intermediate file:** `data/career/raw-careers.json`  

**Split plan:**
- `--scrape-only`: Run all 3 scraping phases. Save `raw-careers.json` with all_jobs, intel_data, scan metadata, scrape_timestamp.
- `--analyze-only`: Load `raw-careers.json`. Run generate_summary() LLM call. Save `latest-scan.json` with both timestamps.
- **DANGER:** send_hot_alerts() currently sends Signal during scraping — it's based on keyword score, NOT LLM. Keep it in scrape phase but only for very-hot matches (score≥85?). Or move to analyze phase so LLM can judge importance. **Decision needed: ask user.** Default: keep in scrape for score≥85, also let LLM flag in analyze phase.

---

### 5.3 salary-tracker.py (EASY)

**File:** /opt/netscan/salary-tracker.py (866 lines)  
**main():** line 785  
**Scraping:** lines 798-809: 6 collectors (career_scans, nofluffjobs, justjoinit, bulldogjob, levelsfyi, glassdoor)  
**Stats:** lines 813-815: compute_statistics() — pure math, no LLM  
**LLM:** line 818: llm_analyze_trends(stats, history)  
**Output dir:** `/opt/netscan/data/salary/`  
**Current output:** `salary-YYYYMMDD.json`, symlink `latest-salary.json`  
**Intermediate file:** `data/salary/raw-salary.json`  

**Split plan:**
- `--scrape-only`: Run 6 collectors + compute_statistics(). Save `raw-salary.json` with entries + stats + scrape_timestamp.
- `--analyze-only`: Load `raw-salary.json`. Run llm_analyze_trends(). Save final with both timestamps.
- Stats computation goes with scraping (no GPU needed).
- **No Signal** in this script.

---

### 5.4 patent-watch.py (MEDIUM)

**File:** /opt/netscan/patent-watch.py (709 lines)  
**main():** line 550  
**Scraping:** lines 565-608: per-query loop, 5 search functions each  
**First LLM:** line 621: analyze_patents_batch() per query + line 645: cross-query synthesis  
**Output dir:** `/opt/netscan/data/patents/`  
**Current output:** `patents-YYYYMMDD.json`, symlink `latest-patents.json`  
**Intermediate file:** `data/patents/raw-patents.json`  

**Split plan:**
- `--scrape-only`: Run all per-query search functions. Save `raw-patents.json` with: `{"scrape_timestamp": ..., "queries": {qid: {"patents": [...], "news": [...]}}, "all_new_patents": [...]}`
- `--analyze-only`: Load raw. Run per-query analyze_patents_batch() + cross-query synthesis. Save final.
- **MEDIUM difficulty** because LLM is called per-query AND cross-query. Both go in analyze phase.
- **No Signal** in this script.

---

### 5.5 city-watch.py (EASY)

**File:** /opt/netscan/city-watch.py (560 lines)  
**main():** line 526  
**Scraping:** line 531: `data = crawl_forum()`  
**LLM:** line 535: `analysis = llm_analyze(data)`  
**Signal:** line 552: score ≥ 5  
**Output dir:** `/opt/netscan/data/city/`  
**Current output:** `city-YYYYMMDD.json`, symlink `latest-city.json`  
**Intermediate file:** `data/city/raw-city.json`  

**Split plan:**
- `--scrape-only`: Run crawl_forum(). Save `raw-city.json` with {scrape_timestamp, threads, meta}.
- `--analyze-only`: Load raw. Run llm_analyze(). Save final with both timestamps.
- **Signal moves to analyze phase** — let LLM judge importance, not just keyword score.

---

### 5.6 car-tracker.py (EASY)

**File:** /opt/netscan/car-tracker.py (836 lines)  
**main():** line 711  
**Data gathering:** lines 722-768: tracker_login(), tracker_get_position(), tracker_get_track(), tracker_get_mileage(), tracker_get_alarms(), detect_trips(), detect_stops(), cluster_locations(), daily_summary()  
**LLM:** line 773: llm_analyze(status, daily, mileage, location_clusters, trips)  
**Output dir:** `/opt/netscan/data/car-tracker/`  
**Current output:** `car-tracker-YYYY-MM-DD.json`, symlink `latest-car-tracker.json`  
**Also saves:** `data/think/note-car-tracker-YYYY-MM-DD.json`  
**Intermediate file:** `data/car-tracker/raw-car-tracker.json`  

**Split plan:**
- `--scrape-only`: Login, fetch all GPS data, detect trips/stops/clusters. Save `raw-car-tracker.json`.
- `--analyze-only`: Load raw. Run llm_analyze(). Save both final JSON + think note.
- detect_trips(), detect_stops(), cluster_locations(), daily_summary() go with scraping (pure computation).
- **No Signal** in this script.

---

### 5.7 csi-sensor-watch.py (MEDIUM)

**File:** /opt/netscan/csi-sensor-watch.py (953 lines)  
**Entry:** line 932 → run_scan() at line 511  
**Scraping:** lines 520-651: Steps 1-7 (kernel drivers, linuxtv patches, github issues, libcamera issues, lore mentions, vendor products, sensor merging/dedup)  
**LLM:** line 670: Step 8: llm_analyze_sensors()  
**Signal:** line 716: when new_findings is non-empty  
**Output dir:** `/opt/netscan/data/csi-sensors/` (note: plural name "csi-sensors" not "csi")  
**Current output:** `latest-csi.json`  
**Intermediate file:** `data/csi-sensors/raw-csi.json`  

**Split plan:**
- `--scrape-only`: Run Steps 1-7, save raw sensor data + new_candidates + new_findings.
- `--analyze-only`: Load raw. Run LLM analysis. Save final.
- **Signal:** Move to analyze phase.
- **NOTE:** Entry point is `run_scan()` not `main()`. Also has `--discover` and `--improve` modes already! Need to be careful with argparse integration.
- **DANGER:** csi-sensor-watch already has multiple modes (discover, improve, default scan). Jobs.json has 3 separate jobs for this: csi-sensor-watch, csi-sensor-discover, csi-sensor-improve. Need to check if --discover and --improve also need splitting. The jobs for discover/improve are agent tasks (not direct commands), so they may use openclaw routing — **verify before changing**.

---

### 5.8 radio-scan.py (EASY)

**File:** /opt/netscan/radio-scan.py (775 lines)  
**Entry:** line 774 → run_scan() at line 579  
**Scraping:** lines 601-674: login, fetch_forum_section() per section, scoring, preview fetching  
**LLM:** line 726: llm_analyze_radio()  
**Signal:** line 758: score ≥ 40 or briefing exists  
**Output dir:** `/opt/netscan/data/radio/`  
**Current output:** `radio-latest.json`, `radio-YYYYMMDD.json`  
**Intermediate file:** `data/radio/raw-radio.json`  

**Split plan:**
- `--scrape-only`: Login, fetch, score. Save `raw-radio.json`.
- `--analyze-only`: Load raw. Generate LLM briefing. Save final.
- **Signal:** Move to analyze-only phase (briefing is LLM-generated).
- **NOTE:** Entry point is `run_scan()` not `main()`.

---

### 5.9 ha-correlate.py (MEDIUM)

**File:** /opt/netscan/ha-correlate.py (1601 lines)  
**main():** line 896  
**Data gathering:** lines 918-1231: Steps 1-7 (HA states, history bulk, per-sensor stats, duty cycle, room occupancy, env deltas, garage, cross-correlations)  
**LLM:** line 1253: Step 8: call_ollama() for synthesis (inline in main)  
**Signal:** line 1549: concerns list (CO₂ >1200, VOC >0.5, PM2.5 >25, ≥3 anomalies, temp extremes, garage events)  
**Output dir:** `/opt/netscan/data/correlate/`  
**Current output:** `correlate-YYYYMMDD-HHMM.json`, symlink `latest-correlate.json`  
**Intermediate file:** `data/correlate/raw-correlate.json`  

**Split plan:**
- `--scrape-only`: Steps 1-7 (HA API fetch + statistical computation). Build `report` dict. Save `raw-correlate.json`.
- `--analyze-only`: Load raw. Run LLM synthesis (Step 8). Evaluate concerns for Signal. Save final.
- **MEDIUM** because statistics computation is heavy (~300 lines) and tightly integrated. All goes with scraping since it's CPU-only.
- **Signal concerns logic** could go in either phase. Some concerns are threshold-based (no LLM needed), but sending during analyze phase is cleaner. However: HA safety alerts (CO₂, temperature) should probably alert ASAP in scrape phase. **Decision: keep critical threshold alerts in scrape-only, move LLM-based alerts to analyze-only.**
- **NOTE:** jobs.json has 4 copies: ha-correlate, ha-correlate-d1, ha-correlate-d2, ha-correlate-d3. All run the same script. Only one should run as scrape + analyze (or split). **Check which are used and which are leftovers from dedup.**

---

### 5.10 ha-journal.py (EASY)

**File:** /opt/netscan/ha-journal.py (542 lines)  
**main():** line 418  
**Data gathering:** lines 460-466: run_ha("climate"), run_ha("rooms"), run_ha("anomalies"), run_ha("lights"), get_switch_activity()  
**LLM:** line 514: call_ollama(SYSTEM_PROMPT, user_prompt)  
**Output:** `data/think/note-home-{timestamp}.json` (think note, not a data file)  
**Intermediate file:** `data/ha-journal/raw-ha-data.json`  

**Split plan:**
- `--scrape-only`: Fetch all HA data, save `raw-ha-data.json` with all 5 data sections + scrape_timestamp.
- `--analyze-only`: Load raw. Build prompt. Run LLM. Save think note with both timestamps.
- **No Signal.** **No symlink output** — saves timestamped think notes.
- **NOTE:** run_ha() actually invokes ha-observe.py subprocess. This is a coupling to watch for.
- **NOTE:** jobs.json has 7 copies: ha-journal-d1..d5, ha-journal-n1, ha-journal-n2. All identical commands. HA_JOURNAL_NAMES in queue-runner only has 'ha-journal-n1' for the opportunistic set.

---

### 5.11 leak-monitor.py (MEDIUM)

**File:** /opt/netscan/leak-monitor.py (1756 lines)  
**main():** line 1737 → dispatches to run_full_scan()  
**Scraping:** lines 1612-1632: 11 scanners run sequentially  
**LLM:** line 1637: llm_analyze_findings(db, total_new)  
**Signal:** line 1669: critical/high severity findings  
**Output:** `/opt/netscan/data/leaks/leak-intel.json` (persistent DB, not timestamped!)  
**Intermediate file:** `data/leaks/raw-leak-scan.json`  

**Split plan:**
- `--scrape-only`: Run all 11 scanners. Save `raw-leak-scan.json` with new findings + scan metadata + scrape_timestamp. Also update persistent DB (findings only, no analysis).
- `--analyze-only`: Load raw. Run llm_analyze_findings(). Update DB with analysis. Send Signal if critical. Save final.
- **⚠️ ANOMALY:** leak-monitor uses a DIFFERENT LLM model/config than other scripts. **Must preserve this!** Check call_ollama() at line 357 for model override.
- **⚠️ ANOMALY:** leak-monitor sends Signal to a different recipient than other scripts. **Must preserve!**
- **⚠️ PERSISTENT DB:** leak-intel.json is a rolling database, not a timestamped snapshot. The scrape phase should update findings in DB, analyze phase should update analysis field. This is more complex than other scripts.
- **⚠️ Current job:** "leak-monitor-night" invokes with `scan` subcommand: `python3 /opt/netscan/leak-monitor.py scan`. Need to check if argparse is already in play.

---

### 5.12 company-intel.py (HARD — interleaved scraping+LLM)

**File:** /opt/netscan/company-intel.py (1582 lines)  
**main():** line 1446  
**Problem:** analyze_company() at line 1258 does BOTH scraping (steps 1-6) AND LLM analysis (step 7) for EACH company. Then main() does another cross-company LLM call.

**Current flow:**
```
for company in COMPANIES:
    intel = analyze_company(key, company, db_entry)
    # This function scrapes 8+ sources AND calls LLM for EACH company
    day_results.append(intel)

# Then cross-company LLM summary
cross_summary = call_ollama(summary_system, summary_prompt)
```

**analyze_company() internals (lines 1258-1443):**
1. GoWork reviews (scrape)
2. News search DDG (scrape)
3. Layoffs.fyi check (scrape)
4. Careers page monitoring (scrape)
5. Company news page (scrape)
5a. 4programmers.net (scrape)
5b. Reddit (scrape)
5c. SemiWiki (scrape)
5d. Hacker News (scrape)
6. Previous intel from DB (local read)
7. LLM analysis (call_ollama) — THIS IS THE ONLY LLM CALL PER COMPANY

**Split plan:**
- **Refactor analyze_company() into scrape_company() + llm_analyze_company()**
- `--scrape-only`: Loop over COMPANIES, call scrape_company() for each. Save `raw-intel.json` with per-company scraped data.
- `--analyze-only`: Load `raw-intel.json`. Loop over companies, call llm_analyze_company() for each. Then do cross-company LLM summary. Save final `latest-intel.json`.
- **DANGER:** The LLM prompt in step 7 uses data from steps 1-6 directly. The raw data must include ALL the scraped fields so the prompt can be reconstructed in analyze mode.
- **DANGER:** COMPANIES dict (~45 entries) at line 60 is shared between company-intel.py and company-think.py. Don't break the dict structure.
- **DANGER:** HN "Who is Hiring" scan at line 1510 runs once per invocation (not per-company). Goes in scrape phase.
- **Intermediate file:** `data/intel/raw-intel.json`

---

### 5.13 lore-digest.sh (HARD — shell+Python)

**File:** /opt/netscan/lore-digest.sh (991 lines)  
**Structure:** Bash wrapper with large Python heredoc (`python3 -u << 'PYEOF'`)  
**Pass 1 (scrape):** lines 337-636: Fetch Atom feed or Mailman mbox, parse messages, group threads, score relevance  
**Pass 2 (LLM):** lines 640-762: Per-thread LLM analysis → thread_summaries  
**Pass 3 (LLM):** lines 766-845: Cross-thread LLM synthesis → bulletin_text  
**Signal:** line 963: Always sends summary  
**Output:** `{FEED_DIR}/digest-{date}.json`, `.txt`, `threads-{date}.json`  
**Intermediate file:** `{FEED_DIR}/raw-threads-{date}.json`  

**Split plan:**
- Option A: Add `--scrape-only` / `--analyze-only` flags to the bash script, pass them through to the Python heredoc.
- Option B: Extract the Python heredoc into a separate .py file and use args. ← **BETTER** but more work.
- **Recommended: Option A** for now (less disruption).
- `--scrape-only`: Run Pass 1 only. Save `raw-threads-{date}.json` with scored_threads_data + messages.
- `--analyze-only`: Load raw. Run Pass 2 (per-thread LLM) + Pass 3 (synthesis). Save final digest.
- **DANGER:** Signal is currently "always sends" — this MUST change to only send from analyze phase when LLM finds something interesting.
- **DANGER:** The bash script has error handling that sends Signal on failures (lines 388, 502, 552). Keep those in scrape phase — scraping failures should still alert.
- **DANGER:** 8 feed-specific jobs in jobs.json (lore-devicetree, lore-dri-devel, lore-jetson-tegra, lore-libcamera, lore-linux-media, lore-linux-riscv, lore-linux-usb, lore-soc-bringup). Each needs to be split into 2 jobs.
- **DANGER:** lore jobs have CATEGORY_TIMEOUT_CAPS of 600s (10min). Scrape-only should be faster, analyze-only may still need 10min for multi-thread LLM.

---

### 5.14 repo-think.py (EASY but ANOMALOUS)

**File:** /opt/netscan/repo-think.py (687 lines)  
**main():** line 631 → argparse → think_one_repo() or think_summary()  
**Data gathering in think_one_repo():** line 487: loads latest.json from repo-watch, line 510: fetch_ddg_search(), line 515: fetch_hn_threads()  
**LLM:** line 519: call_ollama(prompt) — SINGLE prompt arg, system prompt baked in  
**Output dir:** `/opt/netscan/data/repos/{repo_id}/think/`  
**Current output:** `{repo_id}-{date}.json`, symlink `latest-{repo_id}.json`  
**Intermediate file:** `data/repos/{repo_id}/raw-think-data.json`  

**Split plan:**
- `--scrape-only`: Load latest.json from repo-watch, fetch DDG + HN. Save `raw-think-data.json`.
- `--analyze-only`: Load raw. Run LLM. Save final.
- **⚠️ ANOMALY:** call_ollama() at line 175 uses `/api/generate` endpoint (single prompt) instead of `/api/chat` (messages array). This is different from ALL other scripts. **Must preserve this behavior.**
- **⚠️ Already has argparse** with --repo, --summary, --company args. Add --scrape-only / --analyze-only to existing parser.
- **NOTE:** For --summary mode, there's no scraping — it reads existing think notes. Only think_one_repo() needs splitting.
- **NOTE:** Multiple jobs: repo-think-{repo_id} for each monitored repo.

---

### 5.15 academic-watch.py (NOT YET AUDITED)

**File:** /opt/netscan/academic-watch.py  
**Jobs:** 12 jobs in jobs.json (4 topics × 3 types)  
**Status:** Not yet audited — NEEDS AUDIT before implementation.  
**Action:** Read the script first in the implementation session.

---

## 6. Intermediate Data Format <a name="intermediate-data-format"></a>

All `raw-*.json` files follow this structure:

```json
{
  "scrape_timestamp": "2025-06-01T23:15:00",
  "scrape_duration_seconds": 180,
  "scrape_version": 1,
  "data": {
    // Script-specific scraped data
  },
  "scrape_errors": [
    // Any non-fatal errors during scraping
    {"source": "glassdoor", "error": "timeout", "timestamp": "..."}
  ]
}
```

The final output files get BOTH timestamps:

```json
{
  "meta": {
    "scrape_timestamp": "2025-06-01T23:15:00",
    "analyze_timestamp": "2025-06-02T01:30:00",
    "timestamp": "2025-06-02T01:30:00",  // Keep for backward compat!
    "duration_seconds": 45,
    // ... other existing meta fields
  },
  // ... existing output structure unchanged
}
```

**CRITICAL:** Keep `meta.timestamp` pointing to analyze_timestamp for backward compatibility with generate-html.py during rollout. Old dashboard code reads `meta.timestamp` — if we remove it, dashboard breaks.

---

## 7. Queue-Runner Changes <a name="queue-runner-changes"></a>

### New batch ordering concept:

Currently all hybrid scripts run as a single job. After split:

```
NIGHTLY BATCH ORDER:
1. Infra (no LLM): netscan, netscan-enum, watchdog
2. SCRAPE PHASE (no GPU needed):
   - career-scan --scrape-only
   - company-intel --scrape-only
   - event-scout --scrape-only
   - patent-watch --scrape-only
   - salary-tracker --scrape-only
   - city-watch --scrape-only
   - car-tracker --scrape-only
   - csi-sensor-watch --scrape-only
   - radio-scan --scrape-only
   - ha-correlate --scrape-only
   - leak-monitor --scrape-only
   - repo-think --scrape-only (per repo)
   - lore-digest.sh --scrape-only (per feed)
3. ACADEMIC WATCH (hybrid — leave as-is initially, split later)
4. ANALYZE PHASE (GPU needed):
   - career-scan --analyze-only
   - company-intel --analyze-only
   - event-scout --analyze-only
   - ... etc
5. THINK JOBS (already LLM-only):
   - company-think, career-think, repo-think-summary
6. Slow data gathering tail:
   - repo-scan, repo-digest
7. Report + weekly
```

### Changes to queue-runner.py:

1. **New block categories in build_batch_queue():**
   - `'scrape'` — jobs with `--scrape-only` flag
   - Keep existing blocks for analyze jobs

2. **Update extract_direct_command():** Current regex matches `python3 /opt/netscan/...`. Adding `--scrape-only` or `--analyze-only` flag will still match. No change needed.

3. **Scrape jobs don't need GPU idle check:** Add a list of scrape-only job names that skip the pre-flight ollama health check. Or detect `--scrape-only` in the command string.

4. **Timeout adjustments:**
   - Scrape-only: typically shorter (5-15 min)
   - Analyze-only: shorter too (2-10 min per script)
   - Current full-run timeouts were for both phases combined

5. **SKIPPABLE_PREFIXES update:** After time budget, may want to skip scrape-only jobs too (they feed NEXT night anyway).

### Alternative (SIMPLER): Don't change queue-runner ordering now
Instead, just split the jobs in jobs.json and let the existing block ordering handle it naturally. Scrape jobs get the same category as the original (e.g., career-scan-scrape goes in 'infra' block, career-scan-analyze goes in 'career' block). This is less optimal but way less risky.

**RECOMMENDATION:** Start with the simpler approach. Optimize ordering in a follow-up.

---

## 8. Dashboard Changes <a name="dashboard-changes"></a>

### generate-html.py (6408 lines)

Currently reads `meta.timestamp` (or `meta.get("timestamp")`) at these locations:
- Line 1394: city_ts (city-watch)
- Line 2107: leak monitor timestamp
- Line 3485: academic watch
- Line 3779: events (event-scout)
- Line 3949: radio-scan
- Line 4849: more timestamps
- Line 5042: deep intel (company)
- Line 5313: presence timestamps

**Changes needed:**
For each module's dashboard page, show TWO timestamps:
```
🔍 Scraped: 2025-06-01 23:15  |  🧠 Analyzed: 2025-06-02 01:30
```

**Implementation:**
1. Add helper function:
```python
def format_dual_timestamps(meta):
    scrape_ts = meta.get("scrape_timestamp", "")[:16]
    analyze_ts = meta.get("analyze_timestamp", meta.get("timestamp", ""))[:16]
    if scrape_ts and analyze_ts and scrape_ts != analyze_ts:
        return f'🔍 Scraped: {e(scrape_ts)} | 🧠 Analyzed: {e(analyze_ts)}'
    return f'Updated: {e(analyze_ts or scrape_ts)}'
```

2. Replace single-timestamp displays with dual_timestamp calls at each of the ~8 locations.

3. **Index page health summary:** Add color coding:
   - Green: scraped AND analyzed within last 36h
   - Yellow: scraped within 36h but analysis older than 36h
   - Red: scraping older than 48h

**DANGER:** generate-html.py is 6408 lines. Changes need to be surgical. Test locally before deploying by running `python3 generate-html.py` on bc250 and checking output.

---

## 9. Signal Notification Policy <a name="signal-policy"></a>

### User's directive:
> "Don't want Signal notifications on scraping done, only if LLM thinks it found something very interesting. Or there is a scraping failure I need to look at."

### Policy:

| Phase | Send Signal? | Condition |
|-------|-------------|-----------|
| Scrape-only | YES — failures only | Network errors, auth failures, total scraping failure (0 results from all sources) |
| Scrape-only | NO | Successful completion → no notification |
| Analyze-only | YES — interesting findings | LLM output contains high-priority items (per-script criteria) |
| Analyze-only | NO | Routine analysis with nothing noteworthy |

### Per-script Signal changes:

| Script | Current behavior | New behavior |
|--------|-----------------|-------------|
| career-scan | send_hot_alerts(score≥70) | Scrape: alert on total failure only. Analyze: send if LLM flags something. BUT score≥85 match still alerts from scrape (hot job postings shouldn't wait for LLM). |
| city-watch | score≥5 alerts | Move to analyze-only phase. LLM decides. |
| csi-sensor-watch | new_findings non-empty | Move to analyze. Let LLM evaluate importance. |
| radio-scan | score≥40 or briefing exists | Scrape: never. Analyze: only if LLM says "important". |
| ha-correlate | threshold-based (CO₂, VOC, PM2.5, temp) | **KEEP IN SCRAPE for safety thresholds** (these are health/safety critical). LLM summary → analyze phase. |
| leak-monitor | critical/high severity | Scrape: alert on critical findings immediately (security-critical). Analyze: LLM commentary alert if warranted. |
| lore-digest | always sends | Scrape: only on error. Analyze: only if LLM finds particularly relevant patches. |

---

## 10. Jobs.json Changes <a name="jobs-json-changes"></a>

For each hybrid script, create 2 new jobs and disable the old one.

### Example: career-scan

**Old job:**
```json
{
  "name": "career-scan",
  "payload": {
    "message": "...\npython3 /opt/netscan/career-scan.py 2>&1 | tail -50",
    "timeoutSeconds": 2400
  }
}
```

**New jobs:**
```json
{
  "name": "career-scan-scrape",
  "payload": {
    "message": "...\npython3 /opt/netscan/career-scan.py --scrape-only 2>&1 | tail -50",
    "timeoutSeconds": 1200
  }
},
{
  "name": "career-scan-analyze",
  "payload": {
    "message": "...\npython3 /opt/netscan/career-scan.py --analyze-only 2>&1 | tail -50",
    "timeoutSeconds": 900
  }
}
```

### Jobs to split (14 scripts → 28 new jobs, replace 14+duplicates):

| Old job name | New scrape job | New analyze job | Scrape timeout | Analyze timeout |
|-------------|----------------|-----------------|---------------|----------------|
| career-scan | career-scan-scrape | career-scan-analyze | 1200s | 900s |
| company-intel | company-intel-scrape | company-intel-analyze | 1800s | 1200s |
| event-scout | event-scout-scrape | event-scout-analyze | 900s | 600s |
| patent-watch | patent-watch-scrape | patent-watch-analyze | 900s | 600s |
| salary-tracker | salary-tracker-scrape | salary-tracker-analyze | 900s | 600s |
| city-watch | city-watch-scrape | city-watch-analyze | 600s | 600s |
| car-tracker | car-tracker-scrape | car-tracker-analyze | 600s | 600s |
| csi-sensor-watch | csi-sensor-scrape | csi-sensor-analyze | 600s | 600s |
| radio-scan-n1 | radio-scan-scrape | radio-scan-analyze | 600s | 600s |
| ha-correlate | ha-correlate-scrape | ha-correlate-analyze | 900s | 600s |
| ha-journal-n1 | ha-journal-scrape | ha-journal-analyze | 600s | 600s |
| leak-monitor-night | leak-monitor-scrape | leak-monitor-analyze | 1200s | 600s |
| lore-{feed} (×8) | lore-{feed}-scrape | lore-{feed}-analyze | 300s | 600s |
| repo-think-{repo} (×N) | repo-think-{repo}-scrape | repo-think-{repo}-analyze | 300s | 600s |

**DANGER:** ha-journal has 7 job entries (d1-d5, n1, n2) and ha-correlate has 4 (plus d1-d3). These are duplicates. During the split, KEEP ONLY ONE of each as scrape+analyze pair. Disable the d1/d2/d3/d4/d5/n2 duplicates.

### Queue-runner block assignment for new jobs:

```python
# In build_batch_queue():
# Scrape jobs → classify by prefix, run early
if name.endswith('-scrape'):
    blocks['scrape'].append(job)  # NEW block, runs first
elif name.endswith('-analyze'):
    # Route to existing category for ordering
    if name.startswith('company-'):  blocks['company'].append(job)
    elif name.startswith('career-'): blocks['career'].append(job)
    # etc.
```

---

## 11. Deployment Procedure <a name="deployment"></a>

### Per-script deployment cycle:

1. Edit script locally in /Users/akandr/projects/bc250/netscan/
2. Test `--scrape-only` locally if possible (some need network)
3. scp to bc250:/tmp/
4. ssh bc250 sudo cp /tmp/{script} /opt/netscan/
5. Test on bc250: `python3 /opt/netscan/{script} --scrape-only` → verify raw JSON created
6. Test on bc250: `python3 /opt/netscan/{script} --analyze-only` → verify it reads raw and produces output
7. Test on bc250: `python3 /opt/netscan/{script}` (no flags) → verify backward compat
8. Update jobs.json: add -scrape and -analyze jobs, disable old job
9. Restart queue-runner: `sudo systemctl restart queue-runner`

### Rollback plan:
- Each script works without flags (backward compat)
- To rollback: just re-enable old job, disable -scrape/-analyze jobs
- Raw JSON files don't interfere with existing operation

---

## 12. Dangers and Pitfalls <a name="dangers"></a>

### CRITICAL DANGERS:

1. **company-intel.py interleaved scraping+LLM (§5.12):** Must split analyze_company() into two functions. The LLM prompt reconstruction in analyze mode must have ALL the same data fields available. If any field is missing from raw, the prompt will be incomplete → bad analysis.

2. **leak-monitor.py uses different LLM model (§5.11):** call_ollama() at line 357 may have a different model= parameter or endpoint. **Must audit this before changing.** If we break the model selection, leak analysis quality will change.

3. **leak-monitor.py sends to different Signal recipient:** The signal_send() in leak-monitor may use a different phone number / group. Must preserve.

4. **repo-think.py uses /api/generate not /api/chat (§5.14):** call_ollama() at line 175 is fundamentally different from other scripts. Don't "normalize" this during the refactor — just preserve it.

5. **lore-digest.sh is bash+Python (§5.13):** Can't use Python argparse directly. Must handle args in bash or pass through to Python heredoc.

6. **csi-sensor-watch.py has existing --discover/--improve modes (§5.7):** Jobs csi-sensor-discover and csi-sensor-improve may be openclaw agent tasks. Don't break their routing.

7. **generate-html.py backward compat:** If `meta.timestamp` is removed, ALL dashboard pages break. Must keep it AND add scrape_timestamp + analyze_timestamp.

8. **HA safety alerts must not be delayed (§5.9):** CO₂, VOC, PM2.5 threshold alerts in ha-correlate are health/safety critical. They MUST fire from scrape-only, not wait for LLM analysis.

### MODERATE DANGERS:

9. **Persistent DB in leak-monitor:** leak-intel.json is a rolling database. Scrape phase should add findings, analyze phase should add analysis. Both phases write to the same file — need file locking or sequential guarantee (queue-runner provides this).

10. **Race conditions on raw JSON:** If scrape-only and analyze-only could theoretically run simultaneously (they shouldn't in queue-runner), the raw file could be partially written when the analyzer reads it. Queue-runner serializes, so this is safe AS LONG AS we don't add parallel execution later.

11. **Stale raw data:** If scraping fails repeatedly, analyze-only will re-analyze the same old raw data. Should check raw file age and skip analysis if too stale (>48h?). Or let dashboard show the stale timestamp and let user notice.

12. **Timestamp format consistency:** All scripts format timestamps slightly differently (some ISO, some strftime). Standardize on ISO format for both new timestamp fields.

13. **Jobs.json editing is manual JSON editing:** One syntax error breaks ALL 310 jobs. Always backup before editing. Always validate JSON after editing.

14. **academic-watch.py not yet audited:** May have its own patterns. Audit before implementing.

15. **Some scrape-only runs may still be slow:** company-intel scrapes ~45 companies with 8+ sources each. Even without LLM, may take 15-20 minutes. Timeouts should account for this.

### LOW DANGERS:

16. **Existing raw-*.json files:** If a raw file already exists in a data dir for unrelated reasons, we'd overwrite it. Use distinctive naming (raw-{module}-{date}.json?) or just accept overwrite since each dir is module-specific.

17. **Queue-runner name-based routing:** build_batch_queue() uses name.startswith() to classify jobs. New names like `career-scan-scrape` still start with `career-` so they'll route to the career block. But `career-scan-analyze` also starts with `career-` — need to differentiate. The `-scrape` suffix should route to scrape block. **This needs code changes.**

18. **flock wrappers in job commands:** Some jobs have `flock -w 1200 /tmp/ollama-gpu.lock` wrappers. Scrape-only jobs don't need GPU lock. Clean up flock from scrape commands.

---

## 13. Testing Checklist <a name="testing"></a>

### Per-script:
- [ ] `--scrape-only` produces raw-*.json with correct structure
- [ ] `--scrape-only` does NOT call call_ollama() (verify with `strace` or add assert)
- [ ] `--analyze-only` reads raw-*.json successfully
- [ ] `--analyze-only` produces correct final JSON with both timestamps
- [ ] `--analyze-only` fails gracefully if raw-*.json is missing (print error, exit 1)
- [ ] No flags = old behavior (backward compat)
- [ ] Signal notifications follow new policy
- [ ] Existing output format is preserved (nothing breaks generate-html.py)

### Dashboard:
- [ ] New dual-timestamp display works for all module pages
- [ ] Falls back to single timestamp for modules not yet split
- [ ] Color coding for stale data works
- [ ] Index page shows correct status

### Queue-runner:
- [ ] New jobs register correctly in batch queue
- [ ] Scrape jobs don't trigger ollama health check
- [ ] Analyze jobs do trigger ollama health check
- [ ] Time budget still works with split jobs
- [ ] `--dry-run` shows correct ordering

### Integration:
- [ ] Full nightly cycle completes all scrape + analyze jobs within 8h budget
- [ ] Think jobs (career-think, company-think) still work with new output format
- [ ] Dashboard auto-refreshes correctly

---

## 14. Migration Order <a name="migration-order"></a>

Implement in this order (easiest → hardest, with most value first):

### Wave 1: Easy scripts with high value (do first)
1. **event-scout.py** — Clean phases, no Signal, easy test
2. **salary-tracker.py** — Clean phases, no Signal
3. **career-scan.py** — High value (career monitoring), has Signal
4. **car-tracker.py** — Clean phases, no Signal

### Wave 2: Medium scripts
5. **city-watch.py** — Simple but has Signal
6. **radio-scan.py** — Simple but has Signal
7. **patent-watch.py** — Per-query LLM pattern
8. **ha-journal.py** — Clean but depends on ha-observe

### Wave 3: Complex scripts
9. **csi-sensor-watch.py** — Existing modes to preserve
10. **ha-correlate.py** — Safety alerts + heavy stats
11. **leak-monitor.py** — Different model/Signal, persistent DB

### Wave 4: Hard scripts
12. **company-intel.py** — Interleaved scraping+LLM per company
13. **lore-digest.sh** — Shell+Python refactor

### Wave 5: Remaining
14. **repo-think.py** — Anomalous API usage, many per-repo jobs
15. **academic-watch.py** — Needs audit first

### After all scripts:
16. **generate-html.py** — Add dual timestamps (can be done incrementally per module)
17. **queue-runner.py** — Optimize ordering with scrape/analyze blocks
18. **jobs.json** — Mass update (or incremental per wave)
19. **Signal policy audit** — Verify all scripts follow new policy
20. **Cleanup HA duplicates** — Remove ha-journal-d1..d5, ha-correlate-d1..d3

---

## Appendix A: Script Line References

Quick reference for implementing each split:

| Script | main() | Scraping ends | First LLM | call_ollama def | signal_send |
|--------|--------|--------------|-----------|-----------------|-------------|
| event-scout.py | L1374 | L1486 | L1487 | L?? | — |
| career-scan.py | L1225 | L~1280 | generate_summary() | L?? | send_hot_alerts() |
| company-intel.py | L1446 | analyze_company:L1360 | analyze_company:L1370 | L?? | — |
| leak-monitor.py | L1737→run_full_scan | L1632 | L1637 | L357 | L1669 |
| salary-tracker.py | L785 | L815 | L818 | L97 | — |
| patent-watch.py | L550 | L608 | L621 | L142 | — |
| city-watch.py | L526 | L531 | L535 | L270 | L552 |
| car-tracker.py | L711 | L768 | L773 | L589 | — |
| csi-sensor-watch.py | run_scan:L511 | L651 | L670 | L108 | L716 |
| radio-scan.py | run_scan:L579 | L674 | L726 | L128 | L758 |
| ha-correlate.py | L896 | L1231 | L1253 | L664 | L1549 |
| ha-journal.py | L418 | L466 | L514 | L252 | — |
| repo-think.py | L631 | L515 | L519 | L175 | — |
| lore-digest.sh | linear | L636 | L661 | L267 | L963 |

## Appendix B: Data Directory Structure

```
/opt/netscan/data/
├── career/          ← career-scan output
│   ├── latest-scan.json
│   └── raw-careers.json  (NEW)
├── events/          ← event-scout output
│   ├── latest-events.json
│   └── raw-events.json  (NEW)
├── intel/           ← company-intel output
│   ├── latest-intel.json
│   └── raw-intel.json  (NEW)
├── leaks/           ← leak-monitor output
│   ├── leak-intel.json  (persistent DB)
│   └── raw-leak-scan.json  (NEW)
├── salary/          ← salary-tracker output
│   ├── latest-salary.json
│   └── raw-salary.json  (NEW)
├── patents/         ← patent-watch output
│   ├── latest-patents.json
│   └── raw-patents.json  (NEW)
├── city/            ← city-watch output
│   ├── latest-city.json
│   └── raw-city.json  (NEW)
├── car-tracker/     ← car-tracker output
│   ├── latest-car-tracker.json
│   └── raw-car-tracker.json  (NEW)
├── csi-sensors/     ← csi-sensor-watch output (NOTE: plural!)
│   ├── latest-csi.json
│   └── raw-csi.json  (NEW)
├── radio/           ← radio-scan output
│   ├── radio-latest.json
│   └── raw-radio.json  (NEW)
├── correlate/       ← ha-correlate output
│   ├── latest-correlate.json
│   └── raw-correlate.json  (NEW)
├── ha-journal/      ← ha-journal intermediate (NEW directory!)
│   └── raw-ha-data.json  (NEW)
├── repos/{id}/      ← repo-think output
│   ├── think/latest-{id}.json
│   └── raw-think-data.json  (NEW)
├── lore/{feed}/     ← lore-digest output
│   ├── digest-{date}.json
│   └── raw-threads-{date}.json  (NEW)
└── think/           ← think notes (career-think, company-think, etc.)
    └── note-*.json
```

## Appendix C: Job Count Impact

Current: 310 jobs
- 14 hybrid jobs → 28 split jobs (+14 net)
- 8 lore feeds: 8 → 16 (+8)
- ~15 repo-think jobs: 15 → 30 (+15)
- ha-journal duplicates removed: -6 (keep n1 only)
- ha-correlate duplicates removed: -3 (keep 1 only)
- Net: ~310 + 14 + 8 + 15 - 6 - 3 = ~338 jobs

This is manageable. Queue-runner already handles 310.

## Appendix D: Current Signal Usage

Scripts currently sending Signal notifications:
1. career-scan.py → send_hot_alerts() for score≥70
2. city-watch.py → score≥5 threads  
3. csi-sensor-watch.py → new_findings non-empty
4. radio-scan.py → score≥40 or briefing exists
5. ha-correlate.py → threshold violations (CO₂, VOC, PM, temp, garage)
6. leak-monitor.py → critical/high severity findings
7. lore-digest.sh → always sends (summary of digested threads)

After refactor:
1. career-scan: scrape → alert only on score≥85 or total failure; analyze → LLM flags interesting
2. city-watch: analyze only → LLM decides
3. csi-sensor: analyze only → LLM decides
4. radio-scan: analyze only → LLM decides
5. ha-correlate: **scrape → immediate safety alerts (CO₂/VOC/PM/temp)**; analyze → LLM commentary
6. leak-monitor: **scrape → immediate critical/high security alerts**; analyze → LLM analysis alert
7. lore-digest: scrape → error alerts only; analyze → LLM flags relevant patches only

---

*End of plan. Implementation starts with Wave 1: event-scout.py*
