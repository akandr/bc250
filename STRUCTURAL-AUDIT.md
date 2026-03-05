# Structural Audit — netscan Scripts

**Date**: 2025  
**Scope**: All HYBRID (web scraping + LLM) scripts, PURE LLM scripts, `generate-html.py`, `lore-digest.sh`  
**Purpose**: Planning a comprehensive refactor  
**Total lines audited**: ~23,683 across 18 scripts

---

## Table of Contents

1. [Global Infrastructure](#1-global-infrastructure)
2. [HYBRID Scripts (12)](#2-hybrid-scripts)
   - [city-watch.py](#city-watchpy-560-lines)
   - [ha-journal.py](#ha-journalpy-542-lines)
   - [patent-watch.py](#patent-watchpy-709-lines)
   - [radio-scan.py](#radio-scanpy-775-lines)
   - [salary-tracker.py](#salary-trackerpy-866-lines)
   - [car-tracker.py](#car-trackerpy-836-lines)
   - [csi-sensor-watch.py](#csi-sensor-watchpy-953-lines)
   - [career-scan.py](#career-scanpy-1313-lines)
   - [event-scout.py](#event-scoutpy-1565-lines)
   - [company-intel.py](#company-intelpy-1583-lines)
   - [ha-correlate.py](#ha-correlatepy-1602-lines)
   - [leak-monitor.py](#leak-monitorpy-1757-lines)
3. [PURE LLM / Mixed Scripts (4)](#3-pure-llm--mixed-scripts)
   - [market-think.py](#market-thinkpy-628-lines)
   - [career-think.py](#career-thinkpy-811-lines)
   - [company-think.py](#company-thinkpy-1101-lines)
   - [repo-think.py](#repo-thinkpy-687-lines)
4. [Dashboard Generator](#4-dashboard-generator)
   - [generate-html.py](#generate-htmlpy-6408-lines)
5. [Shell+Python Hybrid](#5-shellpython-hybrid)
   - [lore-digest.sh](#lore-digestsh-992-lines)
6. [Cross-Cutting Concerns](#6-cross-cutting-concerns)
7. [Refactoring Opportunities](#7-refactoring-opportunities)

---

## 1. Global Infrastructure

### Ollama LLM

| Parameter | Standard Value | Exception |
|-----------|---------------|-----------|
| URL | `http://localhost:11434` | — |
| Chat endpoint | `/api/chat` | `repo-think.py` uses `/api/generate` |
| Model | `huihui_ai/qwen3-abliterated:14b` | `leak-monitor.py` uses `qwen3-14b-16k:latest` |
| `num_ctx` | `12288` | `company-think.py` + `lore-digest.sh` use `16384` |
| `stream` | `false` | — |
| `/nothink` prefix | Yes (prepended to user prompt) | `repo-think.py` (uses generate endpoint, no prefix) |
| Health check | `/api/tags` — verify model name in response | All scripts |

### Signal Notifications (JSON-RPC)

| Parameter | Standard Value | Exception |
|-----------|---------------|-----------|
| RPC URL | `http://127.0.0.1:8080/api/v1/rpc` | — |
| SIGNAL_FROM | `+<BOT_PHONE>` | — |
| SIGNAL_TO | `+<OWNER_PHONE>` | `leak-monitor.py` uses `+<OWNER_PHONE>` |

### Data Directory Structure

```
/opt/netscan/data/
├── academic/          ← academic-watch.py
├── car-tracker/       ← car-tracker.py
├── career/            ← career-scan.py
├── city/              ← city-watch.py
├── correlate/         ← ha-correlate.py
├── csi-sensors/       ← csi-sensor-watch.py
├── events/            ← event-scout.py
├── gpu/               ← gpu-monitor.py (CSV)
├── intel/             ← company-intel.py, company-think.py
├── leaks/             ← leak-monitor.py
├── market/            ← market-think.py
├── patents/           ← patent-watch.py
├── radio/             ← radio-scan.py
├── repos/             ← repo-watch.sh, repo-think.py
├── salary/            ← salary-tracker.py
├── think/             ← notes system (ha-journal, life-think, system-think)
├── <feed-dirs>/       ← lore-digest.sh (per digest-feeds.json)
└── (scan/health/enum/vuln/watchdog — netscan core)
```

### Config Files

| File | Used By |
|------|---------|
| `/opt/netscan/profile.json` | radio-scan, csi-sensor-watch, repo-think, market-think, company-think, lore-digest |
| `/opt/netscan/profile-private.json` | career-scan, salary-tracker, company-intel |
| `/opt/netscan/sensor-watchlist.json` | csi-sensor-watch |
| `/opt/netscan/radio-creds.json` | radio-scan |
| `/opt/netscan/forum-creds.json` | leak-monitor |
| `/opt/netscan/digest-feeds.json` | lore-digest, generate-html |
| `/opt/netscan/repo-feeds.json` | repo-think, generate-html |
| `~/.openclaw/.env` | ha-journal, ha-correlate (HASS_URL, HASS_TOKEN), leak-monitor (GITHUB_TOKEN) |

---

## 2. HYBRID Scripts

---

### city-watch.py (560 lines)

**Purpose**: Scrape SkyscraperCity Łódź forum for urban development news, analyze with LLM.

#### Functions

| Function | Lines | Purpose |
|----------|-------|---------|
| `fetch_page(url)` | ~L60 | HTTP GET with UA, retry |
| `parse_thread_listing(html)` | ~L100 | Extract thread titles/URLs from forum section HTML |
| `parse_thread_posts(html)` | ~L140 | Extract post content from thread page |
| `score_text(text)` | ~L180 | Keyword scoring (infrastructure, transport, development keywords) |
| `crawl_forum()` | ~L295 | Main scraping orchestrator: iterate sections → threads → posts → score |
| `call_ollama(system_prompt, user_content)` | L222 | Standard Ollama `/api/chat` call |
| `signal_send(msg)` | L243 | Signal JSON-RPC notification |
| `llm_analyze(data)` | ~L399 | Build prompt from scraped data, call LLM |
| `save_results(results)` | ~L450 | Write JSON + think note |
| `main()` | ~L500 | `crawl_forum()` → `llm_analyze()` → `save_results()` → Signal |

#### Call Flow
```
main()
  └→ crawl_forum()
       ├→ fetch_page() × N sections
       ├→ parse_thread_listing()
       ├→ fetch_page() × M threads
       ├→ parse_thread_posts()
       └→ score_text() per thread
  └→ llm_analyze(data)
       └→ call_ollama()
  └→ save_results()
  └→ signal_send()  [if total_score >= 5]
```

#### Data Flow
- **Raw variables**: `data` dict (threads+meta), `relevant` (filtered threads above score threshold)
- **Output**: `/opt/netscan/data/city/city-watch-YYYYMMDD.json`, `latest-city.json`, think note `note-city-watch-*.json`
- **Signal condition**: `hot_threads` exist AND `total_score >= 5`
- **Config**: All inline (no external config files)

#### LLM Interaction
- 1 call: `call_ollama(system_prompt, user_content)` with scraped forum highlights
- System prompt: urban development analyst persona

---

### ha-journal.py (542 lines)

**Purpose**: Collect Home Assistant data via `ha-observe.py` subprocess + HA REST API, synthesize into daily home note.

#### Functions

| Function | Lines | Purpose |
|----------|-------|---------|
| `get_switch_activity(hours=6)` | L104 | HA REST API `/api/states` + `/api/history` for switches |
| `run_ha(command)` | L277 | Run `ha-observe.py <cmd>` as subprocess, return stdout |
| `call_ollama(system_prompt, user_prompt)` | L238 | Standard Ollama `/api/chat` |
| `load_previous_home_note()` | ~L180 | Read last think note for continuity |
| `load_latest_insights()` | ~L200 | Read latest correlate insights |
| `save_note(content)` | ~L320 | Write think note + update notes-index.json |
| `main()` | ~L380 | Orchestrator |

#### Call Flow
```
main()
  ├→ GPU guard (check if GPU busy)
  ├→ run_ha("climate") → run_ha("rooms") → run_ha("anomalies") → run_ha("lights")
  ├→ get_switch_activity(hours=6)
  ├→ load_previous_home_note()
  ├→ load_latest_insights()
  ├→ call_ollama(system_prompt, combined_data)
  └→ save_note()
```

#### Data Flow
- **Raw variables**: `climate_data`, `rooms_data`, `anomalies_data`, `lights_data`, `room_activity`, `switch_timeline`
- **Output**: `/opt/netscan/data/think/note-home-YYYYMMDD-HHMM.json`, updates `notes-index.json`
- **Signal**: NONE
- **Config**: `~/.openclaw/.env` (HASS_URL, HASS_TOKEN), ROOM_PATTERNS inline

#### LLM Interaction
- 1 call: synthesize all HA data into cohesive home journal entry

---

### patent-watch.py (709 lines)

**Purpose**: Search multiple patent databases for camera/sensor-related patents, analyze with LLM.

#### Functions

| Function | Lines | Purpose |
|----------|-------|---------|
| `call_ollama(system_prompt, user_prompt)` | L168 | Standard `/api/chat` |
| `search_google_patents()` | ~L200 | DDG search with `site:patents.google.com` |
| `search_ddg_patents()` | ~L230 | DDG generic patent search |
| `search_epo_ops()` | ~L260 | European Patent Office OPS API |
| `search_uspto_ddg()` | ~L300 | DDG search with `site:patft.uspto.gov` |
| `search_ddg_patent_news()` | ~L340 | DDG patent news search |
| `score_patent(patent)` | ~L370 | Keyword scoring for relevance |
| `analyze_patents_batch(patents, query_id)` | L396 | Per-query LLM analysis |
| `load_db()` / `save_db()` | ~L140 | Patent database persistence |
| `main()` | ~L500 | Loop over 6 SEARCH_QUERIES, multi-source + LLM |

#### Constants
- **SEARCH_QUERIES**: 6 queries (camera ISP, MIPI CSI-2, automotive camera, image sensor, embedded vision, computational photography)

#### Call Flow
```
main()
  └→ for each of 6 SEARCH_QUERIES:
       ├→ search_google_patents()
       ├→ search_ddg_patents()
       ├→ search_epo_ops()
       ├→ search_uspto_ddg()
       ├→ search_ddg_patent_news()
       ├→ score_patent() per result
       └→ analyze_patents_batch(new_patents, query_id)
  └→ cross-query synthesis LLM call (~L600)
  └→ save JSON
```

#### Data Flow
- **Raw variables**: `all_new_patents`, `query_results` dict keyed by query_id, `db` (patent-db.json)
- **Output**: `/opt/netscan/data/patents/patents-YYYYMMDD.json`, `latest-patents.json`, `patent-db.json`
- **Signal**: NONE
- **Config**: No external config files

#### LLM Interaction
- N calls: 1 per query (`analyze_patents_batch`) + 1 cross-query synthesis
- Up to 7 LLM calls total

---

### radio-scan.py (775 lines)

**Purpose**: Scrape MyBB radio scanning forum, score threads, analyze with LLM.

#### Functions

| Function | Lines | Purpose |
|----------|-------|---------|
| `RadioScanner` class | ~L60 | Forum scraper with session management |
| `RadioScanner.login()` | ~L80 | MyBB forum auth |
| `RadioScanner.fetch_forum_section(fid)` | ~L120 | Fetch section page |
| `RadioScanner.fetch_thread_preview(tid)` | ~L150 | Fetch first post of thread |
| `ThreadHTMLParser` | ~L170 | MyBB HTML parser (thread listings) |
| `score_thread(thread)` | ~L210 | Keyword relevance scoring |
| `call_ollama(system_prompt, user_content)` | L155 | Standard `/api/chat` |
| `signal_send(msg)` | L211 | Signal JSON-RPC |
| `llm_analyze_radio(highlights, all_threads, section_stats)` | L326 | LLM analysis of radio intel |
| `run_scan()` | ~L400 | Main scan flow |

#### Constants
- **FORUM_SECTIONS**: 8 sections (IDs for different frequency bands/topics)

#### Call Flow
```
run_scan()
  ├→ load creds from /opt/netscan/radio-creds.json
  ├→ RadioScanner.login()
  ├→ for each of 8 FORUM_SECTIONS:
  │    ├→ fetch_forum_section(fid)
  │    ├→ ThreadHTMLParser.feed()
  │    └→ score_thread() per thread
  ├→ fetch_thread_preview() for top threads
  ├→ llm_analyze_radio(highlights, all_threads, section_stats)
  │    └→ call_ollama()
  ├→ save JSON
  └→ signal_send()  [if highlights exist AND (score≥40 OR briefing)]
```

#### Data Flow
- **Raw variables**: `all_threads`, `section_stats`, `highlights` (filtered high-score threads)
- **Output**: `/opt/netscan/data/radio/radio-latest.json`, `radio-YYYYMMDD.json`
- **Signal condition**: highlights exist AND (total_score ≥ 40 OR LLM briefing non-empty)
- **Config**: `/opt/netscan/radio-creds.json` (username, password), `/opt/netscan/profile.json`

#### LLM Interaction
- 1 call: `llm_analyze_radio()` with forum highlights + stats

---

### salary-tracker.py (866 lines)

**Purpose**: Collect salary data from 6 job board sources, compute statistics, analyze trends with LLM.

#### Functions

| Function | Lines | Purpose |
|----------|-------|---------|
| `call_ollama(system_prompt, user_prompt)` | L103 | Standard `/api/chat` |
| `collect_from_career_scans()` | ~L150 | Parse latest career-scan output for salary data |
| `collect_from_nofluffjobs()` | ~L200 | Scrape nofluffjobs.com API |
| `collect_from_justjoinit()` | ~L280 | Scrape justjoin.it API |
| `collect_from_bulldogjob()` | ~L350 | Scrape bulldogjob.pl |
| `collect_from_levelsfyi()` | ~L420 | DDG search for levels.fyi data |
| `collect_from_glassdoor()` | ~L480 | DDG search for glassdoor data |
| `compute_statistics(entries)` | ~L550 | Percentiles, medians, by-role, by-company stats |
| `llm_analyze_trends(today_stats, history)` | L656 | LLM trend analysis |
| `main()` | L780 | Three-phase orchestrator |

#### Call Flow
```
main()
  Phase 1: "Collecting salary data..."  (L780)
    ├→ collect_from_career_scans()
    ├→ collect_from_nofluffjobs()
    ├→ collect_from_justjoinit()
    ├→ collect_from_bulldogjob()
    ├→ collect_from_levelsfyi()
    └→ collect_from_glassdoor()
  Phase 2: "Computing statistics..."  (L790)
    └→ compute_statistics(all_entries)
  Phase 3: "LLM trend analysis..."  (L794)
    └→ llm_analyze_trends(stats, history)
  └→ save JSON
```

#### Data Flow
- **Raw variables**: `all_entries` (list of salary records with source, role, min/max/currency), `stats` from `compute_statistics()`
- **Output**: `/opt/netscan/data/salary/salary-YYYYMMDD.json`, `latest-salary.json`, `salary-history.json`
- **Signal**: NONE
- **Config**: `/opt/netscan/profile-private.json` (PROFILE_FILE — contains current salary for comparison)

#### LLM Interaction
- 1 call: `llm_analyze_trends()` with today's stats + historical data

---

### car-tracker.py (836 lines)

**Purpose**: Query SinoTrack GPS tracker API, detect trips/stops, cluster locations, analyze with LLM.

#### Functions

| Function | Lines | Purpose |
|----------|-------|---------|
| `tracker_api_call(cmd, data, user)` | ~L80 | SinoTrack HTTP API wrapper |
| `tracker_login()` | ~L120 | Authenticate with SinoTrack |
| `tracker_get_position()` | ~L150 | Current GPS position |
| `tracker_get_track(days)` | ~L180 | GPS track history (N days) |
| `tracker_get_mileage(days)` | ~L210 | Mileage data |
| `tracker_get_alarms(days)` | ~L240 | Alarm events (geofencing, etc.) |
| `detect_trips(track)` | ~L280 | Segment track into trips (speed thresholds) |
| `detect_stops(track)` | ~L340 | Find stop locations (dwell time) |
| `cluster_locations(stops)` | ~L400 | K-means clustering of stops |
| `analyze_mileage(mileage)` | ~L450 | Daily mileage aggregation |
| `daily_summary(trips, stops, mileage)` | ~L500 | Build summary dict |
| `call_ollama(system_prompt, user_prompt)` | L583 | Standard `/api/chat` |
| `llm_analyze(status, daily, mileage, clusters, trips)` | L615 | LLM vehicle analysis |
| `main()` | ~L700 | 11-step orchestrator |

#### Constants
- **TRACKER_IMEI**: `REDACTED`
- **TRACKER_PASSWORD**: `REDACTED` (hardcoded!)

#### Call Flow
```
main()
  Step 1: tracker_login()
  Step 2: tracker_get_position()
  Step 3: tracker_get_track(days=7)
  Step 4: tracker_get_mileage(days=30)
  Step 5: tracker_get_alarms(days=7)
  Step 6: detect_trips(track)
  Step 7: detect_stops(track)
  Step 8: cluster_locations(stops)
  Step 9: analyze_mileage() + daily_summary()
  Step 10: llm_analyze(status, daily, mileage, clusters, trips)
            └→ call_ollama()
  Step 11: save JSON + think note
```

#### Data Flow
- **Raw variables**: `position`, `track`, `mileage`, `alarms`, `trips`, `stops`, `clusters`, `daily`
- **Output**: `/opt/netscan/data/car-tracker/car-tracker-YYYYMMDD.json`, `latest-car-tracker.json`, think note
- **Signal**: NONE
- **Config**: Hardcoded SinoTrack credentials

#### LLM Interaction
- 1 call: `llm_analyze()` with all aggregated vehicle data

---

### csi-sensor-watch.py (953 lines)

**Purpose**: Monitor kernel driver development for CSI camera sensors across GitHub, GitLab, lore.kernel.org, vendor sites.

#### Functions

| Function | Lines | Purpose |
|----------|-------|---------|
| `call_ollama(system_prompt, user_prompt)` | L93 | Standard `/api/chat` |
| `signal_send(msg)` | L108 | Signal JSON-RPC |
| `check_kernel_drivers()` | ~L150 | GitHub API search for kernel driver changes |
| `fetch_linuxtv_patches()` | ~L220 | Fetch linux-media patch submissions |
| `fetch_github_issues()` | ~L300 | GitHub Issues API for related repos |
| `fetch_libcamera_issues()` | ~L370 | GitLab API for libcamera issues |
| `fetch_lore_mentions()` | ~L440 | lore.kernel.org Atom feed search |
| `check_vendor_products()` | ~L510 | Vendor product page scraping |
| `merge_discoveries(sources)` | ~L600 | Merge + deduplicate across sources |
| `llm_analyze_sensors()` | L757 | LLM analysis of sensor ecosystem |
| `run_scan()` | ~L800 | Main scan orchestrator |
| `run_discover()` | ~L870 | Discovery mode (find new sensors to track) |
| `run_improve()` | ~L920 | LLM-driven watchlist improvement |

#### Constants
- **Watchlist**: loaded from `/opt/netscan/sensor-watchlist.json` (sensor models, kernel drivers, compatible strings)

#### Call Flow
```
run_scan()
  Step 1: load watchlist from sensor-watchlist.json
  Step 2: check_kernel_drivers() — GitHub API
  Step 3: fetch_linuxtv_patches() — linuxtv.org
  Step 4: fetch_github_issues() — GitHub Issues
  Step 5: fetch_libcamera_issues() — GitLab API
  Step 6: fetch_lore_mentions() — lore.kernel.org Atom
  Step 7: check_vendor_products() — vendor websites
  Step 8: merge_discoveries()
  Step 9: llm_analyze_sensors()
           └→ call_ollama()
  Step 10: save JSON + Signal if new findings
```

#### Modes (argparse)
- `scan` (default): full scan pipeline
- `--discover`: find new sensors not on watchlist
- `--improve`: LLM suggests watchlist improvements

#### Data Flow
- **Raw variables**: `kernel_hits`, `patches`, `github_issues`, `libcamera_issues`, `lore_mentions`, `vendor_products`, `merged`
- **Output**: `/opt/netscan/data/csi-sensors/latest-csi.json`, history snapshots
- **Signal condition**: `new_findings` exist (any source returned new items not in DB)
- **Config**: `/opt/netscan/sensor-watchlist.json`, `/opt/netscan/profile.json`

#### LLM Interaction
- 1 call in scan mode: `llm_analyze_sensors()`
- 1 call in improve mode: watchlist improvement suggestions

---

### career-scan.py (1313 lines)

**Purpose**: Comprehensive career monitoring — scrape 30+ company career pages, 5 job boards, 4 intel sources, analyze + alert.

#### Functions

| Function | Lines | Purpose |
|----------|-------|---------|
| `call_ollama(system_prompt, user_prompt)` | ~L100 | Standard `/api/chat` |
| `fetch_page(url)` | ~L80 | HTTP GET with UA |
| `scan_career_pages()` | ~L400 | Iterate COMPANIES dict, fetch Workday CXS API + HTML scrape |
| `scan_job_boards()` | ~L550 | Scan 5 JOB_BOARDS (nofluffjobs, justjoin, bulldogjob, etc.) |
| `scan_intel_sources()` | ~L650 | Scan 4 INTEL_SOURCES (layoffs.fyi, blind, teamblind, glassdoor) |
| `deduplicate_jobs(jobs)` | ~L750 | Title + company fuzzy dedup |
| `generate_summary(all_data)` | ~L780 | Final LLM summary call |
| `send_hot_alerts(results)` | ~L850 | Signal alerts for hot matches |
| `save_scan(results)` | ~L900 | Write JSON + think note |
| `save_note(results)` | ~L950 | Write think note to notes system |
| `main()` | ~L1000 | Multi-phase orchestrator |

#### Constants
- **COMPANIES**: 30+ companies with career URLs, Workday CXS API endpoints
- **JOB_BOARDS**: 5 boards
- **INTEL_SOURCES**: 4 sources
- **MUST_MATCH_ANY / STRONG_MATCH / LOCATION_OK / LOCATION_REJECT**: keyword filter lists
- **SYSTEM_PROMPT_JOBS**: LLM prompt for career page → JSON job extraction
- **SYSTEM_PROMPT_INTEL**: LLM prompt for intel source → JSON briefing
- **SYSTEM_PROMPT_SUMMARY**: LLM prompt for combined → emoji briefing

#### Call Flow
```
main()
  ├→ GPU guard
  Phase 1: scan_career_pages()
    └→ for each company in COMPANIES:
         ├→ Workday CXS API (POST JSON) if configured
         ├→ HTML scrape fallback
         └→ call_ollama(SYSTEM_PROMPT_JOBS, page_content)
  Phase 2: scan_job_boards()
    └→ for each board in JOB_BOARDS:
         └→ fetch + parse (API or HTML)
  Phase 3: scan_intel_sources()  [skipped in --quick mode]
    └→ for each source in INTEL_SOURCES:
         └→ call_ollama(SYSTEM_PROMPT_INTEL, scraped_content)
  └→ deduplicate_jobs()
  └→ generate_summary()
       └→ call_ollama(SYSTEM_PROMPT_SUMMARY, combined)
  └→ save_scan() + save_note()
  └→ send_hot_alerts()
  └→ regenerate dashboard (subprocess: generate-html.py)
```

#### Data Flow
- **Raw variables**: `career_jobs` (from pages), `board_jobs`, `intel_briefings`, `all_jobs`, `summary`
- **Output**: `/opt/netscan/data/career/latest-scan.json`, `career-YYYYMMDD.json`, think note
- **Signal condition**: `send_hot_alerts()` — jobs with `match_score >= 80` AND `remote_compatible` (max 3 alerts), urgent intel alerts (max 2)
- **Config**: `/opt/netscan/profile-private.json`

#### LLM Interaction
- N calls in Phase 1: 1 per company with career page content
- M calls in Phase 3: 1 per intel source
- 1 call: `generate_summary()` final synthesis
- Total: potentially 35+ LLM calls

---

### event-scout.py (1565 lines)

**Purpose**: Find tech events/meetups/conferences from 9 source types, score by relevance, analyze with LLM.

#### Functions

| Function | Lines | Purpose |
|----------|-------|---------|
| `fetch_page(url)` | ~L80 | HTTP GET |
| `score_event(event)` | ~L140 | Multi-factor scoring (keywords × location tier) |
| `check_known_conferences()` | ~L200 | Check 15 KNOWN_CONFERENCES against dates |
| `search_community_sources()` | ~L260 | 13 COMMUNITY_SOURCES (RSS/iCal/HTML/xml_schedule types) |
| `parse_rss_events(xml_text)` | ~L900 | RSS/Atom XML parser |
| `parse_ical_events(ical_text)` | ~L940 | iCalendar parser |
| `parse_fosdem_xml(xml_text)` | ~L980 | FOSDEM devroom schedule XML parser with filter |
| `search_crossweb()` | ~L350 | crossweb.pl event search |
| `search_konfeo()` | ~L400 | konfeo.com event search |
| `search_evenea()` | ~L450 | evenea.pl event search |
| `search_meetup(query)` | ~L500 | meetup.com search (×11 queries) |
| `search_eventbrite(query)` | ~L600 | eventbrite search (×3 queries) |
| `search_ddg_events(query)` | ~L700 | DDG search (×15 queries) |
| `llm_analyze_events(events)` | ~L1100 | LLM final analysis (top 25) |
| `load_db()` / `save_db()` | ~L1200 | Event database persistence |
| `main()` | ~L1300 | Three-phase orchestrator |

#### Constants
- **KNOWN_CONFERENCES**: 15 entries (FOSDEM, ELCE, LinuxPlumbers, etc.)
- **COMMUNITY_SOURCES**: 13 sources (CCC, FOSDEM, LWN, kernelnewbies, etc.)
- **PRIMARY_KEYWORDS / SECONDARY_KEYWORDS**: scoring keyword sets
- **LOCATIONS**: 5 tiers (t1_Łódź → t5_offshore) with multipliers
- **SEARCH_QUERIES_MEETUP**: 11 queries
- **SEARCH_QUERIES_EVENTBRITE**: 3 queries
- **SEARCH_QUERIES_DDG**: 15 queries

#### Call Flow
```
main()
  Phase 1: Collect events
    ├→ check_known_conferences()
    ├→ search_community_sources() × 13 sources (RSS/iCal/HTML/XML)
    ├→ search_crossweb()
    ├→ search_konfeo()
    ├→ search_evenea()
    ├→ search_meetup() × 11 queries
    ├→ search_eventbrite() × 3 queries
    └→ search_ddg_events() × 15 queries
  Phase 2: Score + deduplicate
    └→ score_event() per event
  Phase 3: LLM analysis
    └→ llm_analyze_events(top_25)
  └→ update event-db.json + save
```

#### Data Flow
- **Raw variables**: `all_events` (collected from all sources), `scored` (post-scoring), `deduped`
- **Output**: `/opt/netscan/data/events/events-YYYYMMDD.json`, `latest-events.json`, `event-db.json`
- **Signal**: NONE
- **Config**: All inline (no external config files)

#### LLM Interaction
- 1 call: `llm_analyze_events()` with top 25 scored events
- System prompt: FOSDEM devroom filter, community tier weighting, location-aware

---

### company-intel.py (1583 lines)

**Purpose**: Deep per-company corporate intelligence from 8 source types across 45+ companies.

#### Functions

| Function | Lines | Purpose |
|----------|-------|---------|
| `call_ollama(system_prompt, user_prompt)` | ~L100 | Standard `/api/chat` |
| `fetch_page(url)` | ~L80 | HTTP GET |
| `fetch_gowork_reviews(company)` | ~L200 | GoWork.pl review scraping |
| `search_ddg_news(query)` | ~L250 | DDG news search |
| `check_layoffs_fyi(company)` | ~L300 | layoffs.fyi scraping |
| `check_careers_pages(company)` | ~L350 | 3-phase: BrassRing ATS API → HTML scrape → DDG fallback |
| `_query_brassring_api(url, params)` | ~L400 | IBM BrassRing ATS API POST |
| `_extract_jobs_from_text(text)` | ~L450 | Regex job extraction from HTML |
| `search_4programmers(company)` | ~L500 | 4programmers.net search |
| `search_reddit(company)` | ~L550 | Reddit search (old.reddit.com) |
| `search_semiwiki(company)` | ~L600 | SemiWiki.com search |
| `search_hackernews(company)` | ~L650 | Hacker News Algolia API search |
| `search_hn_who_is_hiring()` | ~L700 | HN "Who is Hiring" monthly thread scan |
| `analyze_company(key, company, db_entry)` | ~L800 | Per-company orchestrator: all 8 sources + LLM |
| `load_db()` / `save_db()` | ~L900 | Rolling DB (90 snapshots per company) |
| `main()` | ~L1000 | Company loop + cross-company synthesis |

#### Constants
- **COMPANIES**: 45+ companies organized by industry:
  - Silicon: nvidia, google, amd, intel, arm, qualcomm, broadcom, etc.
  - FAANG: amazon, meta, apple, microsoft
  - Automotive: harman, continental, aptiv, tesla, waymo, hailo, mobileye, valeo, bosch, zf
  - Telecom: ericsson, nokia
  - Defence: thales
  - Consumer electronics: samsung, tcl
  - Metrology: hexagon
  - Open source: bootlin, collabora, pengutronix, igalia, toradex, linaro, canonical, redhat, suse
  - Emerging silicon: sifive, tenstorrent, cerebras
  - Semiconductor: nxp, renesas, mediatek, ambarella, onsemi, infineon, stmicro
- Each company has: `gowork_id`, `news_url`, `search_terms`, `careers_urls`, `ats_api` config, `industry` tag

#### Call Flow
```
main()
  └→ for each company in COMPANIES:
       └→ analyze_company(key, company, db_entry)
            ├→ fetch_gowork_reviews()
            ├→ search_ddg_news()
            ├→ check_layoffs_fyi()
            ├→ check_careers_pages()
            │    ├→ _query_brassring_api()  [if ATS configured]
            │    ├→ HTML scrape fallback
            │    └→ DDG fallback
            ├→ search_4programmers()
            ├→ search_reddit()
            ├→ search_semiwiki()
            ├→ search_hackernews()
            └→ call_ollama()  [per-company analysis]
       └→ update DB snapshot
  └→ search_hn_who_is_hiring()  [global monthly thread]
  └→ call_ollama()  [cross-company summary]
  └→ save JSON
```

#### Data Flow
- **Raw variables**: per-company `intel` dict (gowork_reviews, news, layoffs, careers, 4prog, reddit, semiwiki, hn), `all_analyses`
- **Output**: `/opt/netscan/data/intel/intel-YYYYMMDD.json`, `latest-intel.json`, `company-intel-deep.json` (rolling DB, 90 snapshots/company)
- **Signal**: NONE
- **Config**: `/opt/netscan/profile-private.json`

#### LLM Interaction
- N calls: 1 per company (~45) + 1 cross-company summary = ~46 LLM calls

---

### ha-correlate.py (1602 lines)

**Purpose**: HA sensor time-series correlation analysis with statistical methods + garage car tracker + LLM synthesis.

#### Functions

| Function | Lines | Purpose |
|----------|-------|---------|
| `ha_api_get(endpoint)` | ~L80 | HA REST API GET |
| `ha_get_all_states()` | ~L100 | Fetch all entity states |
| `ha_get_history_bulk(entity_ids, hours=24)` | ~L130 | Bulk history fetch |
| `extract_numeric_timeseries(history)` | ~L180 | Parse numeric sensor values |
| `extract_onoff_timeseries(history)` | ~L220 | Parse binary sensor values |
| `resample_to_hourly(timeseries)` | ~L260 | Hourly aggregation |
| `pearson_correlation(ts1, ts2)` | ~L300 | Pearson correlation coefficient |
| `compute_lag_correlation(ts1, ts2, max_lag=3)` | ~L340 | Time-lagged cross-correlation |
| `compute_duty_cycle(ts)` | ~L380 | ON/OFF duty cycle computation |
| `detect_anomalies(ts, threshold=2.0)` | ~L420 | Z-score anomaly detection |
| `hourly_pattern(ts)` | ~L460 | 24h pattern extraction |
| `compute_room_usage(sensors, switches)` | ~L500 | Room occupancy estimation |
| `build_room_timeline(room_usage)` | ~L550 | Timeline visualization data |
| `compute_env_deltas(timeseries)` | ~L600 | Environment delta computation |
| `detect_garage_events(ts_dict)` | ~L700 | Car leaving/returning detection from temp + door switch |
| `interpret_correlation(r, p)` | ~L1500 | Natural language interpretation of r-value |
| `call_ollama(system_prompt, user_prompt)` | ~L750 | Standard `/api/chat` |
| `signal_send(msg)` | ~L800 | Signal JSON-RPC |
| `main()` | ~L850 | 10-step analysis pipeline |

#### Constants
- **SENSOR_GROUPS**: temperature, humidity, co2, pm25, voc — with entity_id patterns
- **KNOWN_DEVICES**: 4 devices (garage heater, kitchen LEDs, bathroom fans) with expected behaviors
- **GARAGE_TEMP_SENSOR, GARAGE_HUMIDITY, GARAGE_DOOR_SWITCH, GARAGE_AUTO_SWITCH, GATE_SWITCH**: garage car tracker entities
- Temperature rise/drop thresholds for car detection

#### Call Flow
```
main()
  Step 1:  ha_get_all_states() — discover sensors
  Step 2:  ha_get_history_bulk() — fetch 24h history
  Step 3:  extract_numeric_timeseries() + extract_onoff_timeseries()
  Step 4:  Per-sensor stats: detect_anomalies(), hourly_pattern()
  Step 5:  compute_duty_cycle() for switches
  Step 6:  compute_room_usage(), build_room_timeline()
  Step 7:  compute_env_deltas()
  Step 8:  detect_garage_events() — car tracker
  Step 9:  Cross-correlations (same-room + cross-room temp):
           pearson_correlation(), compute_lag_correlation()
  Step 10: call_ollama() — LLM synthesis
  └→ save JSON + think note
  └→ signal_send()  [conditional]
```

#### Data Flow
- **Raw variables**: `all_states`, `history_bulk`, `ts_numeric`, `ts_onoff`, `anomalies`, `duty_cycles`, `room_usage`, `env_deltas`, `garage_events`, `correlations`
- **Output**: `/opt/netscan/data/correlate/correlate-YYYYMMDD-HHMM.json`, `latest-correlate.json`, `note-home-insights-*.json`
- **Signal condition**: CO₂ > 1200 ppm, VOC > 0.5, PM2.5 > 25, anomalies ≥ 3, temps < 15°C or > 28°C, or garage events detected
- **Config**: `~/.openclaw/.env` (HASS_URL, HASS_TOKEN)

#### LLM Interaction
- 1 call: final synthesis of all statistical results

---

### leak-monitor.py (1757 lines)

**Purpose**: Cyber Threat Intelligence & Leak Monitor — white-hat OSINT across 11 sources.

> ⚠️ **ANOMALIES**: Uses **different LLM model** (`qwen3-14b-16k:latest`) and **different Signal recipient** (`+<OWNER_PHONE>`) than all other scripts.

#### Functions

| Function | Lines | Purpose |
|----------|-------|---------|
| `fetch_url(url)` | ~L120 | HTTP GET (supports Tor SOCKS5 on 127.0.0.1:9050) |
| `fetch_json(url)` | ~L150 | JSON fetch wrapper |
| `strip_html(text)` | ~L170 | HTML tag removal |
| `call_ollama(system, user, temperature, max_tokens)` | ~L200 | `/api/chat` with different model |
| `signal_send(msg)` | ~L240 | Signal JSON-RPC (different recipient!) |
| `add_finding(db, source, category, title, summary, ...)` | ~L280 | Dedup finding into DB by hash |
| `load_db()` / `save_db()` | ~L320 | Leak intel DB persistence |
| `log(msg)` / `save_log()` | ~L360 | File-backed logging |
| `scan_ransomware_live(db)` | ~L400 | Source 1: ransomware.live API |
| `scan_ransomlook(db)` | ~L470 | Source 2: ransomlook.io API |
| `scan_github(db)` | ~L540 | Source 3: GitHub code search (8 GITHUB_DORKS) |
| `scan_cisa_kev(db)` | ~L620 | Source 4: CISA Known Exploited Vulnerabilities |
| `scan_telegram_public(db)` | ~L700 | Source 5: 6 Telegram CTI channels |
| `scan_feodo_c2(db)` | ~L780 | Source 6: abuse.ch Feodo C2 tracker |
| `scan_hibp_breaches(db)` | L900 | Source 7: Have I Been Pwned breach catalog |
| `scan_hudson_rock(db)` | ~L1020 | Source 8: Hudson Rock infostealer exposure |
| `_load_forum_creds()` | ~L1100 | Load forum credentials |
| `_mybb_login(base, user, pass)` | ~L1110 | MyBB forum authentication |
| `_mybb_scrape_threads(opener, base, section, limit)` | ~L1200 | Forum section scraper |
| `_match_forum_thread(title, content)` | ~L1260 | Keyword matching for forum threads |
| `scan_leakforum(db)` | ~L1320 | Source 9: LeakForum.io Atom feed |
| `scan_cracked(db)` | ~L1400 | Source 10: Cracked.sh homepage + authenticated sections |
| `scan_ahmia_darkweb(db)` | ~L1490 | Source 11: Ahmia.fi dark web search |
| `llm_analyze_findings(db, new_count)` | ~L1580 | LLM triage of all findings |
| `run_full_scan()` | ~L1630 | Main scan orchestrator |
| `print_status()` | ~L1700 | Status display |
| `main()` | ~L1740 | argparse: scan / status |

#### Constants
- **OLLAMA_MODEL**: `qwen3-14b-16k:latest` (⚠️ DIFFERENT)
- **SIGNAL_TO**: `+<OWNER_PHONE>` (⚠️ DIFFERENT)
- **WATCH_DOMAINS**: harman.com, samsung.com, jbl.com, akg.com, harmanpro.com, harmaninternational.com
- **WATCH_KEYWORDS**: harman, samsung, HARMAN, SAMSUNG, JBL, AKG, Mark Levinson, Harman Kardon, etc.
- **SUPPLY_CHAIN**: qualcomm, nvidia, nxp, renesas, bosch, continental, tesla, bmw, etc.
- **GITHUB_DORKS**: 8 dork queries for exposed secrets
- **TRACKED_GROUPS**: 15 ransomware groups
- **TELEGRAM_CHANNELS**: 6 CTI channels
- **PL_WATCH_DOMAINS**: 24 Polish domains (allegro, olx, wp, gov.pl, mbank, etc.)
- **FORUM_PL_KEYWORDS / FORUM_CODE_KEYWORDS**: underground forum keyword sets
- **LEAKFORUM_SECTIONS / CRACKED_SECTIONS**: forum section IDs
- **HIBP_BREACHES_URL, HUDSON_ROCK_URL**: API endpoints
- Tor SOCKS5: `127.0.0.1:9050` for .onion access

#### Call Flow
```
run_full_scan()
  └→ for (name, scanner) in [11 scanners]:
       ├→ scan_ransomware_live(db)     — ransomware.live API
       ├→ scan_ransomlook(db)          — ransomlook.io API
       ├→ scan_github(db)              — GitHub dork searches
       ├→ scan_cisa_kev(db)            — CISA KEV catalog
       ├→ scan_telegram_public(db)     — 6 Telegram CTI channels
       ├→ scan_feodo_c2(db)            — abuse.ch Feodo C2
       ├→ scan_hibp_breaches(db)       — HIBP catalog (Polish + direct targets + large new)
       ├→ scan_hudson_rock(db)         — Infostealer exposure for 24 PL domains
       ├→ scan_leakforum(db)           — Atom feed + keyword matching
       ├→ scan_cracked(db)             — Homepage scrape + optional auth
       └→ scan_ahmia_darkweb(db)       — 10 dark web queries
  └→ llm_analyze_findings(db, total_new)   [if total_new > 0]
  └→ save_db()
  └→ signal_send()  [if critical/high findings from this run]
  └→ save_log()
```

#### Data Flow
- **Raw variables**: `db` dict with `findings` list, `runs`, `stats`, per-finding: `{source, category, title, summary, severity, relevance, url, first_seen, hash}`
- **Output**: `/opt/netscan/data/leaks/leak-intel.json`, `leak-monitor.log`
- **Signal condition**: Any `critical` or `high` severity findings from current run (max 5 in alert, includes LLM analysis snippet)
- **Config**: `/opt/netscan/forum-creds.json`, `~/.openclaw/.env` (GITHUB_TOKEN)

#### LLM Interaction
- 1 call: `llm_analyze_findings()` — triage and summarize all recent findings
- System prompt: CTI analyst persona, focus on HARMAN/Samsung, Polish entities, underground forums

---

## 3. PURE LLM / Mixed Scripts

---

### market-think.py (628 lines)

**Type**: PURE LLM (yfinance data, no web scraping)

**Purpose**: Chain-of-thought analysis of financial tickers using yfinance + LLM.

#### Functions

| Function | Lines | Purpose |
|----------|-------|---------|
| `fetch_ticker_data(symbol)` | ~L100 | yfinance library for price/fundamentals |
| `call_ollama(system, user)` | ~L150 | `/api/chat` with `think=True` (chain-of-thought) |
| `think_one_ticker(ticker_id)` | ~L200 | Per-ticker analysis |
| `think_summary()` | ~L350 | Cross-ticker synthesis |
| `main()` | ~L500 | argparse: --ticker / --summary / --list |

#### Constants
- **TICKERS**: 18 tickers — btc, eth, aapl, amd, arm, asml, intc, tsm, samsung, vz, wbd, fmc, smh, sp500, cnya, gldm, euny, gazp

#### Data Flow
- **Output**: `/opt/netscan/data/market/think/<ticker>-YYYYMMDD.json`, `summary-YYYYMMDD.json`
- **Signal**: Defined but NOT called
- **Config**: `/opt/netscan/profile.json`

#### LLM Interaction
- 1 call per ticker with `think=True` (extended reasoning)
- 1 call for summary

---

### career-think.py (811 lines)

**Type**: HYBRID (fetches Workday APIs + career URLs, then LLM analysis)

**Purpose**: Deep chain-of-thought career opportunity analysis per company.

#### Functions

| Function | Lines | Purpose |
|----------|-------|---------|
| `fetch_company_careers(company)` | ~L150 | Fetch Workday APIs + career page URLs |
| `call_ollama(system, user)` | ~L200 | `/api/chat` with `think=True` |
| `think_one_company(slug, focus)` | ~L300 | Per-company deep analysis |
| `think_summary()` | ~L500 | Cross-company synthesis |
| `main()` | ~L700 | argparse: --company / --summary / --list; --focus |

#### Constants
- **Companies**: nvidia, google, amd, samsung, amazon, tcl, harman, qualcomm, arm
- **CAREER_FOCUS_AREAS**: skills, opportunity, culture, compensation

#### Data Flow
- **Output**: `/opt/netscan/data/careers/think/<company>[-focus]-YYYYMMDD.json`
- **Signal**: Defined but NOT called
- **Config**: `/opt/netscan/profile-private.json`

#### LLM Interaction
- 1 call per company with `think=True`, temperature=0.5

---

### company-think.py (1101 lines)

**Type**: HYBRID (DDG news + GoWork + HN searches, then deep LLM)

**Purpose**: Deep chain-of-thought corporate intelligence per company (mirrors company-intel's company list).

#### Functions

| Function | Lines | Purpose |
|----------|-------|---------|
| `search_ddg_news(query)` | ~L100 | DDG news search |
| `fetch_gowork_reviews(company)` | ~L150 | GoWork.pl scraping |
| `fetch_hackernews(query)` | ~L200 | HN Algolia API |
| `fetch_company_intel(slug)` | ~L250 | Aggregate all sources + load previous intel from latest-intel.json |
| `call_ollama(system, user)` | ~L300 | `/api/chat` with `think=True`, `num_ctx=16384` |
| `think_one_company(slug, focus)` | ~L400 | Per-company deep analysis |
| `think_summary()` | ~L700 | Cross-company executive brief |
| `main()` | ~L900 | argparse: --company / --summary / --list; --focus |

#### Constants
- **COMPANIES**: 45+ companies (identical set to company-intel.py)
- **COMPANY_FOCUS_AREAS**: strategy, tech-talent, financial, competitive — each with 4-5 detailed section templates

#### Data Flow
- **Output**: `/opt/netscan/data/intel/think/<company>[-focus]-YYYYMMDD.json`, `summary-YYYYMMDD.json`, `latest-summary.json`
- **Signal**: Defined but NOT called
- **Config**: `/opt/netscan/profile.json`

#### LLM Interaction
- 1 call per company with `think=True`, temperature=0.5, max_tokens=4000, num_ctx=16384

---

### repo-think.py (687 lines)

**Type**: HYBRID (DDG + HN search, then LLM)

**Purpose**: Deep analysis of specific open-source repository developments.

> ⚠️ **API ANOMALY**: Uses `/api/generate` endpoint (not `/api/chat`), no system/user message split.

#### Functions

| Function | Lines | Purpose |
|----------|-------|---------|
| `fetch_ddg_search(query)` | L225 | DDG web search |
| `fetch_hn_threads(query)` | L247 | HN Algolia API |
| `call_ollama(prompt)` | L201 | `/api/generate` (⚠️ different endpoint) |
| `think_one_repo(repo_id, focus)` | ~L300 | Per-repo analysis: load latest.json + DDG + HN + LLM |
| `think_summary()` | ~L500 | Cross-repo synthesis |
| `main()` | ~L600 | argparse: --repo / --summary / --list; --focus |

#### Data Flow
- **Output**: `/opt/netscan/data/repos/<repo>/think/<repo>[-focus]-YYYYMMDD.json`, summary
- **Signal**: NONE
- **Config**: `/opt/netscan/repo-feeds.json`, `/opt/netscan/profile.json`

#### LLM Interaction
- 1 call per repo via `/api/generate` (no system/user distinction)
- Prompt contains both context and instructions inline

---

## 4. Dashboard Generator

---

### generate-html.py (6408 lines)

**Purpose**: Static HTML dashboard generator — reads all script outputs and renders Phrack/BBS-style web pages.

**NO LLM calls. NO web scraping. NO Signal notifications.**
Pure data aggregation → HTML rendering.

#### Structure Overview

| Section | Lines | Purpose |
|---------|-------|---------|
| Imports + config | L1-L18 | DATA_DIR, WEB_DIR, SCRIPT_DIR |
| Feed configs | L19-L40 | Load `digest-feeds.json`, `repo-feeds.json` |
| ASCII art/branding | L42-L65 | NETSCAN banner, skull art |
| CSS | L67-L796 | Full demoscene BBS aesthetic, responsive |
| Data loading | L797-L860 | `load_json()`, `get_scan_dates()`, `get_latest_*()`, `load_all_scans()` |
| HTML helpers | L871-L1037 | `e()`, `page_wrap()`, `badge()`, `score_badge()`, `port_chips()`, `health_bar()` |
| gen_home() | L1040-L1445 | Home page: correlate + think notes + car tracker + city watch |
| gen_dashboard() | L1448-L1797 | Network dashboard: host grid, port changes, security, presence |
| gen_hosts() | L1800-L1909 | Host inventory table |
| gen_leaks() | L1912-L2132 | Leak monitor findings display |
| gen_security() | L2135-L2261 | Security scoring page |
| gen_host_detail() | L2264-L2731 | Per-host detail: ports, history, enum, vuln, watchdog |
| gen_presence() | L2734-L2928 | Phone presence tracker |
| gen_feed_page() | L2931-L3124 | Generic digest feed page (LKML, lore, etc.) |
| gen_issues() | L3127-L3417 | GitHub/GitLab repo issues + CSI sensor data |
| gen_academic() | L3420-L3645 | Academic literature page |
| gen_notes() | L3647-L3752 | Think notes index page |
| gen_events() | L3755-L3919 | Events & meetups page |
| gen_radio() | L3922-L4113 | Radio scanner page |
| gen_car_tracker() | L4116-L4503 | Car tracker page with trip history |
| gen_advisor() | L4506-L4815 | Life advisor page (cross-think, system-think) |
| gen_careers() | L4818-L5285 | Career intelligence page + company intel |
| gen_load() | L5287-L6212 | GPU load page: heatmap, power cost, throttle, capacity |
| gen_lkml() | L6213-L6217 | Backward-compat alias for linux-media feed |
| gen_history() | L6219-L6350 | Network scan history with ASCII charts |
| gen_log() | L6352-L6397 | Scan log viewer |
| main() | L6399-L6408 | Generate all pages + per-host detail pages |

#### Data Files Read

| Data File | Page(s) | Source Script |
|-----------|---------|---------------|
| `correlate/latest-correlate.json` | home | ha-correlate.py |
| `think/note-home-*.json` | home | ha-journal.py |
| `think/note-home-insights-*.json` | home | ha-correlate.py |
| `car-tracker/latest-car-tracker.json` | home, car | car-tracker.py |
| `city/latest-city.json` | home | city-watch.py |
| `think/note-city-watch-*.json` | home | city-watch.py |
| `scan-*.json` | dashboard, history | netscan core |
| `health-*.json` | dashboard | bc250-health-check |
| `enum/enum-*.json` | host detail | enumerate.sh |
| `vuln/vuln-*.json` | host detail, security | vulnscan.sh |
| `watchdog/watchdog-*.json` | host detail | watchdog.py |
| `presence-state.json` | dashboard, presence | presence.sh |
| `phones.json` | dashboard, presence | presence.sh |
| `presence-log.json` | presence | presence.sh |
| `leaks/leak-intel.json` | leaks | leak-monitor.py |
| `<feed>/digest-*.json` | feed pages | lore-digest.sh |
| `repos/<repo>/latest.json` | issues | repo-watch.sh |
| `csi-sensors/latest-csi.json` | issues | csi-sensor-watch.py |
| `academic/latest-*-*.json` | academic | academic-watch.py |
| `think/note-publication-*.json` | academic | academic-watch.py |
| `think/notes-index.json` | notes | all think scripts |
| `events/latest-events.json` | events | event-scout.py |
| `radio/radio-latest.json` | radio | radio-scan.py |
| `car-tracker/car-tracker-*.json` | car | car-tracker.py |
| `think/latest-life-cross.json` | advisor | life-think.py |
| `think/latest-life-advisor.json` | advisor | life-think.py |
| `think/latest-system-gpu.json` | advisor | system-think.py |
| `think/latest-system-netsec.json` | advisor | system-think.py |
| `think/latest-system-health.json` | advisor | system-think.py |
| `career/latest-scan.json` | career | career-scan.py |
| `intel/latest-intel.json` | career | company-intel.py |
| `gpu/gpu-*.csv` | load | gpu-monitor.py |
| `gpu-load.tsv` | load | gpu-monitor.sh |
| `~/.openclaw/cron/jobs.json` | load (capacity) | queue-runner |

#### Output
- HTML pages → `/opt/netscan/web/` (main pages + per-host detail pages in `host/`)
- No configs consumed beyond `digest-feeds.json` and `repo-feeds.json`

#### main() Flow
```
main()
  ├→ get_latest_scan()
  ├→ load_all_scans(30)
  ├→ get_latest_enum() / get_latest_vuln() / get_latest_watchdog()
  ├→ Generate main pages dict (16+ static pages):
  │    index.html, hosts.html, presence.html, security.html,
  │    history.html, log.html, home.html, notes.html, academic.html,
  │    radio.html, events.html, career.html, car.html, advisor.html,
  │    load.html, leaks.html, issues.html
  ├→ Dynamic feed pages from digest-feeds.json (<slug>.html per feed)
  ├→ Write each page to WEB_DIR
  └→ Per-host detail pages: host/<IP>.html for each host in latest scan
```

---

## 5. Shell+Python Hybrid

---

### lore-digest.sh (992 lines)

**Purpose**: Config-driven daily mailing list digest — multi-pass chunked architecture with crash recovery.

**Structure**: Bash wrapper (lines 1-70) embedding a Python heredoc (lines 71-992).

#### Bash Wrapper

| Feature | Detail |
|---------|--------|
| Arguments | `--feed <feed-id>` (single feed) or `--all` (sequential all feeds) |
| Config | Reads feed list from `/opt/netscan/digest-feeds.json` |
| `--all` mode | Loops `$0 --feed "$fid"` for each feed with 5s delay |

#### Python Core (embedded heredoc)

| Function | Lines | Purpose |
|----------|-------|---------|
| `fetch_url(url)` | ~L140 | HTTP GET with retries + polite delay |
| `strip_html(text)` | ~L160 | XHTML content → plain text |
| `normalize_subject(subj)` | ~L170 | Strip Re:, [PATCH vN M/N] prefixes |
| `relevance_score(text)` | ~L180 | Feed-specific keyword scoring |
| `truncate(text, max)` | ~L195 | Word-boundary truncation |
| `_signal_send_one(msg)` | ~L200 | Single Signal message |
| `send_signal(msg)` | ~L220 | Multi-chunk Signal sender (2000 char limit per message) |
| `ollama_health()` | ~L270 | Health check via `/api/tags` |
| `call_ollama(system, user, ...)` | ~L285 | `/api/chat` with num_ctx=16384, /nothink prefix |
| `save_intermediate(name, data)` | ~L330 | Crash recovery: save to work dir |
| `load_intermediate(name)` | ~L340 | Crash recovery: load from work dir |
| `thread_hash(key)` | ~L350 | MD5 short hash for filenames |

#### Pipeline (3 Passes)

**Pass 1: Fetch, parse, group, score** (~L360-L640)

Two source modes:
- **`lore` source**: Fetch lore.kernel.org Atom feed → parse XML → extract messages
- **`mailman` source**: Fetch pipermail `.txt.gz` mbox archives → parse email messages

Both modes then:
1. Group messages into threads by normalized subject
2. Score threads using feed-specific `RELEVANCE` keywords
3. Filter threads by `MIN_SCORE`
4. Save to `work-YYYYMMDD/pass1-threads.json` (resumable)

**Pass 2: Per-thread LLM analysis** (~L640-L830)

For each scored thread (up to `MAX_THREADS`):
1. Check cache: `work-YYYYMMDD/thread-<hash>.json`
2. Build per-thread prompt: subject + initial post + review snippets
3. `call_ollama(THREAD_SYSTEM, prompt)` with feed-specific `thread_expert` persona
4. Save to cache (individual thread file — crash-resumable)
5. 3s pause between calls for GPU breathing

`THREAD_SYSTEM` prompt template uses feed config fields:
- `FEED['thread_expert']`: persona description
- `FEED['thread_tech_detail']`: additional technical prompts

Output format: SUBJECT, TYPE, SUBSYSTEM, IMPORTANCE, SUMMARY, KEY PEOPLE, STATUS, IMPACT

**Pass 3: Synthesis** (~L830-L930)

1. Combine all thread summaries into synthesis prompt
2. `call_ollama(SYNTH_SYSTEM, synth_prompt)` with feed-specific `synth_audience`, `synth_acronyms`, `synth_focus`
3. Fallback: if synthesis LLM fails, build bulletin from per-thread analyses

#### Output (~L930-L992)

1. `digest-YYYYMMDD.json` — full structured digest with metadata
2. `digest-YYYYMMDD.txt` — plain text bulletin
3. `threads-YYYYMMDD.json` — per-thread analysis details

#### Signal Notification

**Always sends** (not conditional):
- Short alert with top 4 thread subjects + dashboard link
- Multi-chunk if long (2000 char limit)
- Includes `DASHBOARD_URL/<page_slug>.html` link

#### Feed Config (from digest-feeds.json)

Each feed specifies:
- `name`, `emoji`, `lore_list`, `data_dir`, `page_slug`
- `source`: `"lore"` or `"mailman"`
- `mailman_url` (if mailman source)
- `relevance`: keyword → weight dict
- `min_score`, `max_threads`
- `thread_expert`: LLM persona for Pass 2
- `thread_tech_detail`: additional Pass 2 prompt
- `synth_audience`, `synth_acronyms`, `synth_focus`: Pass 3 LLM tuning

#### LLM Interaction
- N calls in Pass 2: 1 per thread (up to MAX_THREADS, typically 15)
- 1 call in Pass 3: synthesis
- Total: up to 16 LLM calls per feed
- num_ctx: 16384 (higher than standard 12288)
- Crash recovery: each intermediate result saved; re-runs skip completed work

---

## 6. Cross-Cutting Concerns

### Duplicated Code Patterns

1. **`call_ollama()` function**: Implemented independently in every script (~30-60 lines each). Nearly identical:
   - Health check via `/api/tags`
   - POST to `/api/chat` with system+user messages
   - `/nothink` prefix prepend
   - timeout/retry logic
   - `stream: false`, `keep_alive: 5m`
   - Exception: `repo-think.py` uses `/api/generate`

2. **`signal_send()` function**: Copied across 6 scripts with minor variations:
   - city-watch, radio-scan, career-scan, ha-correlate, leak-monitor, lore-digest
   - lore-digest has the most sophisticated version (multi-chunk splitting)

3. **`fetch_page()` / `fetch_url()`**: HTTP GET with UA string, copied ~10 times with slight variations.

4. **GPU guard pattern**: Several scripts check GPU availability before starting LLM work.

5. **Think note saving**: Multiple scripts write to `/opt/netscan/data/think/` + update `notes-index.json`.

6. **DDG search functions**: `search_ddg_news()`, `search_ddg_events()`, `fetch_ddg_search()` — reimplemented ~8 times.

7. **Company lists**: The COMPANIES dict (45+ companies) is duplicated between `company-intel.py` and `company-think.py`.

### Inconsistencies

| Issue | Scripts Affected |
|-------|-----------------|
| Different LLM model | leak-monitor.py (`qwen3-14b-16k:latest` vs standard `huihui_ai/qwen3-abliterated:14b`) |
| Different Signal recipient | leak-monitor.py (`+<OWNER_PHONE>` vs standard `+<OWNER_PHONE>`) |
| Different API endpoint | repo-think.py (`/api/generate` vs standard `/api/chat`) |
| Different num_ctx | company-think.py + lore-digest.sh (16384 vs standard 12288) |
| Signal defined but never called | market-think.py, career-think.py, company-think.py |
| Hardcoded credentials | car-tracker.py (TRACKER_IMEI, TRACKER_PASSWORD inline) |

### LLM Call Budget (per full cycle)

| Script | LLM Calls | Notes |
|--------|-----------|-------|
| city-watch.py | 1 | |
| ha-journal.py | 1 | |
| patent-watch.py | ~7 | 6 per-query + 1 synthesis |
| radio-scan.py | 1 | |
| salary-tracker.py | 1 | |
| car-tracker.py | 1 | |
| csi-sensor-watch.py | 1-2 | 1 scan + optional improve |
| career-scan.py | ~35+ | 1 per company + 1 per intel source + 1 summary |
| event-scout.py | 1 | |
| company-intel.py | ~46 | 1 per company + 1 summary |
| ha-correlate.py | 1 | |
| leak-monitor.py | 0-1 | Only if new findings |
| market-think.py | 1 per ticker | Called individually |
| career-think.py | 1 per company | Called individually |
| company-think.py | 1 per company | Called individually |
| repo-think.py | 1 per repo | Called individually |
| lore-digest.sh | ~16 per feed | N threads + 1 synthesis |

---

## 7. Refactoring Opportunities

### High Priority

1. **Extract shared `netscan_lib` module** containing:
   - `call_ollama()` — unified with model/endpoint as parameters
   - `signal_send()` — unified with chunking from lore-digest
   - `fetch_page()` / `fetch_json()` — with UA, retry, timeout
   - `search_ddg()` — unified DDG search
   - `save_think_note()` — unified notes system writer
   - `gpu_guard()` — GPU availability check
   - `load_json()` / `save_json()` — standard file I/O

2. **Consolidate COMPANIES dict**: Currently duplicated between `company-intel.py` and `company-think.py`. Extract to shared config file (e.g., `companies.json`).

3. **Standardize LLM model configuration**: Currently hardcoded in each script. Should be a single config or environment variable.

4. **Externalize credentials**: `car-tracker.py` has hardcoded GPS tracker credentials. Move to config file like other scripts.

### Medium Priority

5. **Unify Signal recipient**: Verify if leak-monitor.py's different number (`+<OWNER_PHONE>` vs `+<OWNER_PHONE>`) is intentional.

6. **Standardize `repo-think.py`**: Migrate from `/api/generate` to `/api/chat` for consistency.

7. **Enable Signal in think scripts**: market-think, career-think, company-think all define `signal_send()` but never call it. Either remove dead code or implement notifications.

8. **Create base class for scanners**: city-watch, radio-scan, csi-sensor-watch, etc. share a common pattern: scrape → score → LLM → save → optional Signal.

### Lower Priority

9. **generate-html.py decomposition**: At 6408 lines, this should be split into per-page modules or at least separate rendering functions into their own files.

10. **Config consolidation**: 7 different config files across scripts. Consider a unified config hierarchy.

11. **Think note format standardization**: Various scripts produce think notes with slightly different structures.

12. **Error handling**: Most scripts silently catch and log exceptions. Consider structured error reporting.
