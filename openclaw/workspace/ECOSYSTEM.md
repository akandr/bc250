# Netscan Monitoring Ecosystem

A comprehensive research, monitoring, and intelligence system built by AK on this BC-250 box.
Everything lives under `/opt/netscan/`. You (Clawd) have full read access to all of it.

## Architecture

```
openclaw cron → Clawd agent turns → shell tools → scripts → Ollama (qwen3-abliterated:14b)
                                                           → JSON data → generate-html.py → Dashboard
                                                           → Signal alerts (career, watchlist, leaks)
```

**Key change (Feb 2026):** All GPU tasks are now orchestrated by `openclaw cron` (38 jobs/day).
The gateway runs 24/7. Signal messages preempt background work (queue until current turn completes).
Non-GPU tasks (nmap, presence, syslog, watchdog) remain in system crontab.

## Quick Reference

| What | Where |
|------|-------|
| Dashboard | `http://192.168.3.151:8888` (nginx) |
| Web root | `/opt/netscan/web/` (auto-generated HTML) |
| Scripts | `/opt/netscan/*.sh`, `/opt/netscan/*.py` |
| Data | `/opt/netscan/data/` |
| Config | `/opt/netscan/profile.json` (public), `/opt/netscan/profile-private.json` (career, gitignored) |
| Watchlist | `/opt/netscan/watchlist.json` (auto-evolving interest tracker) |
| Git repo | `git@github.com:akandr/bc250.git` (local commits only, no push) |

## Scripts

### idle-think.sh — The Brain
The main intelligence script. Contains 8 task types, each producing JSON notes:
- **weekly** — Weekly summary of all accumulated data
- **trends** — Cross-reference feeds for emerging patterns
- **crossfeed** — Find connections between different data sources
- **research** — Deep-dive into a topic from the watchlist
- **career** — Career intelligence (T5 path, skills gaps, industry moves)
- **crawl** — Discover new information sources
- **learn** — Extract learnable insights from recent data
- **signal** — THE ONLY Signal notification source. Scans all fresh data against the watchlist, sends one daily ping if something matches

Run: `/opt/netscan/idle-think.sh --task <type>`

### leak-monitor.py — Cyber Threat Intelligence
Monitors 8 breach/leak sources with Poland-focused hunting:
- **ransomware.live** — Active ransomware incidents
- **ransomlook.io** — Ransomware group monitoring
- **GitHub** — Exposed credentials, leaked databases
- **CISA KEV** — Known Exploited Vulnerabilities
- **Telegram** — Breach/leak channels (data dumps, stealer logs)
- **Feodo Tracker** — C2 botnet infrastructure
- **HIBP** — New breach notifications
- **Hudson Rock** — Infostealer/compromised credentials

Run: `python3 /opt/netscan/leak-monitor.py scan`
Data: `/opt/netscan/data/leaks/`

### career-scan.py — Career Intelligence
Two-phase anti-hallucination architecture. Phase 1: extract jobs without candidate profile.
Phase 2: score individual jobs against profile. Includes salary-tracker, company-intel, patent-watch, event-scout.

Run: `python3 /opt/netscan/career-scan.py`
Data: `/opt/netscan/data/career/`

### ha-journal.py — Home Assistant Analysis
Reads HA sensor data (climate, energy, air quality) and writes observation notes.

Run: `python3 /opt/netscan/ha-journal.py`
Data: `/opt/netscan/data/ha-journal/`

### ha-correlate.py — HA Cross-Sensor Correlation
Correlates multiple HA sensor streams to find anomalies and patterns.

Run: `python3 /opt/netscan/ha-correlate.py`
Data: `/opt/netscan/data/ha-correlate/`

### repo-watch.sh — Repository Monitor
Tracks 5 upstream projects relevant to AK's work:
- **GStreamer** (GitLab MRs) — multimedia framework
- **libcamera** (GitLab issues) — camera stack
- **v4l-utils** (Patchwork patches) — V4L2 userspace tools
- **FFmpeg** (Patchwork patches) — multimedia swiss army knife
- **LinuxTV** (Patchwork patches) — Linux TV/media subsystem

Run: `/opt/netscan/repo-watch.sh --all`
Data: `/opt/netscan/data/repos/<project>/`

### lore-digest.sh — Mailing List Digest
Monitors kernel mailing lists:
- **linux-media** (lore.kernel.org) — media subsystem patches
- **soc-bringup** (lore.kernel.org) — SoC/DT patches

Run: `/opt/netscan/lore-digest.sh --all`
Data: `/opt/netscan/data/lkml/` and `/opt/netscan/data/soc/`

### Other Scripts
- **scan.sh** — Network scan (nmap), discovers hosts on 192.168.3.0/24
- **enumerate.sh** — Deep service enumeration of discovered hosts
- **vulnscan.sh** — Weekly vulnerability scan (Sundays)
- **presence.sh** — Phone presence tracker (AK's phone MAC detection)
- **syslog.sh** — System activity logger (health TSV + journal capture + events)
- **gpu-monitor.sh** — Per-minute GPU utilization (3-state: generating/loaded/idle via pp_dpm_sclk clock)
- **gpu-monitor.py** — GPU data collector + daily heatmap chart generator
- **watchdog.py** — Integrity checks (cron health, disk space, service status)
- **report.sh** — Morning HTML report rebuild
- **generate-html.py** — Builds the entire dashboard from all data sources

## Data Locations

### Notes (idle-think output)
- Index: `/opt/netscan/data/think/notes-index.json`
- Notes: `/opt/netscan/data/think/note-<type>-<date>-<time>.json`
- Each note has: type, title, content (markdown), tags, created timestamp

### Leak Intelligence
- `/opt/netscan/data/leaks/leak-scan-<date>-<time>.json` — individual scan results
- `/opt/netscan/data/leaks/leak-db.json` — rolling database (90 days)

### Career Intelligence
- `/opt/netscan/data/career/scan-<date>.json` — daily career scan
- `/opt/netscan/data/career/latest-scan.json` (symlink)
- `/opt/netscan/data/salary/salary-<date>.json` — salary snapshots
- `/opt/netscan/data/intel/intel-<date>.json` — company intelligence
- `/opt/netscan/data/patents/patents-<date>.json` — patent watch
- `/opt/netscan/data/events/events-<date>.json` — event scout

### Repository Data
- `/opt/netscan/data/repos/<project>/items-<date>.json` — daily items
- `/opt/netscan/data/repos/<project>/digest-<date>.json` — LLM digest

### Mailing List Digests
- `/opt/netscan/data/lkml/digest-<date>.json` — linux-media digest
- `/opt/netscan/data/soc/digest-<date>.json` — soc-bringup digest

### Home Assistant
- `/opt/netscan/data/ha-journal/note-home-<date>-<seq>.json` — HA observation journals
- `/opt/netscan/data/ha-correlate/note-hacorr-<date>-<seq>.json` — correlation analysis

### Network/Presence/GPU
- `/opt/netscan/data/hosts-db.json` — all discovered network hosts
- `/opt/netscan/data/presence-state.json` — current phone presence
- `/opt/netscan/data/gpu-load.tsv` — GPU utilization log (7-col TSV: timestamp, status, model, script, vram_mb, gpu_mhz, temp_c)

### System Health
- `/opt/netscan/data/syslog/health-<date>.tsv` — 5-min health snapshots
- `/opt/netscan/data/syslog/events-<date>.log` — timeouts, OOM, restarts
- `/opt/netscan/data/syslog/gateway-<date>.log` — openclaw gateway journal
- `/opt/netscan/data/syslog/ollama-<date>.log` — Ollama journal

### Watchlist
`/opt/netscan/watchlist.json` — JSON with items array. Each item has:
- topic, context, source, status (active/resolved), added date, auto flag
- The signal task reads this to decide what is worth alerting about

## Dashboard
The web dashboard at http://192.168.3.151:8888 has these pages:
- **DASHBOARD** — Overview with host count, presence, latest notes
- **HOSTS** — All discovered network devices
- **PRESENCE** — Phone detection timeline
- **LINUX-MEDIA / SOC-BRINGUP** — Kernel mailing list digests
- **GSTREAMER / LIBCAMERA / V4L-UTILS / FFMPEG / LINUXTV** — Repo feeds
- **ISSUES** — LLM-generated issues/concerns from analysis
- **NOTES** — All idle-think research notes
- **LOAD** — GPU utilization heatmap (3-state: generating/loaded/idle) and per-script breakdown
- **SECURITY** — Host security scoring
- **CAREER** — Career scan results
- **LEAKS** — Cyber threat intelligence dashboard
- **HISTORY** — Changelog
- **LOG** — Raw scan logs

## How to Help AK With This Data

1. **Read the latest notes:**
   ```bash
   cat /opt/netscan/data/think/notes-index.json
   ```

2. **Check the watchlist:**
   ```bash
   cat /opt/netscan/watchlist.json
   ```

3. **See latest repo activity:**
   ```bash
   ls -t /opt/netscan/data/repos/*/items-*.json | head -5
   ```

4. **Check GPU load:**
   ```bash
   tail -20 /opt/netscan/data/gpu-load.tsv
   ```

5. **Check leak intelligence:**
   ```bash
   ls -t /opt/netscan/data/leaks/leak-scan-*.json | head -3
   ```

6. **See what is running now:**
   ```bash
   curl -s http://localhost:11434/api/ps
   ```

7. **Read the profile for context:**
   ```bash
   cat /opt/netscan/profile.json         # public interests
   cat /opt/netscan/profile-private.json  # career context
   ```

8. **Check openclaw cron status:**
   ```bash
   openclaw cron list
   openclaw cron runs --last 5
   ```
