# Netscan Monitoring Ecosystem

This is a comprehensive research and monitoring system built by AK on this BC-250 box.
Everything lives under `/opt/netscan/`. You (Clawd) have full read access to all of it.

## Quick Reference

| What | Where |
|------|-------|
| Dashboard | `http://192.168.3.151:8888` (nginx) |
| Web root | `/opt/netscan/web/` (auto-generated HTML) |
| Scripts | `/opt/netscan/*.sh`, `/opt/netscan/generate-html.py` |
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
- **presence.sh** — Phone presence tracker (AK's phone MAC detection)
- **report.sh** — Daily HTML report generation
- **gpu-monitor.sh** — Samples Ollama `/api/ps` every minute, logs to TSV
- **generate-html.py** — Builds the entire dashboard from all data sources

## Data Locations

### Notes (idle-think output)
- Index: `/opt/netscan/data/think/notes-index.json`
- Notes: `/opt/netscan/data/think/note-<type>-<date>-<time>.json`
- Each note has: type, title, content (markdown), tags, created timestamp

### Repository Data
- `/opt/netscan/data/repos/<project>/items-<date>.json` — daily items
- `/opt/netscan/data/repos/<project>/digest-<date>.json` — LLM digest

### Mailing List Digests
- `/opt/netscan/data/lkml/digest-<date>.json` — linux-media digest
- `/opt/netscan/data/soc/digest-<date>.json` — soc-bringup digest

### Network/Presence
- `/opt/netscan/data/hosts-db.json` — all discovered network hosts
- `/opt/netscan/data/presence-state.json` — current phone presence
- `/opt/netscan/data/gpu-load.tsv` — GPU utilization log

### Watchlist
`/opt/netscan/watchlist.json` — JSON with items array. Each item has:
- topic, context, source, status (active/resolved), added date, auto flag
- The signal task reads this to decide what is worth alerting about
- LLM can add new items or resolve existing ones automatically

## Cron Schedule

### Quiet Hours (23:00–08:00)
```
55 22 * * *     systemctl --user stop openclaw-gateway
30 23 * * *     repo-watch.sh --all
30 0  * * *     ha-journal.py              # GPU locked
0  1  * * *     career-scan.py             # GPU locked, two-phase, 15-60 min
0  4  * * *     scan.sh                    # Network scan
30 4  * * *     enumerate.sh               # Deep service enum
0  5  * * *     lore-digest.sh --all       # GPU locked
30 5  * * 0     vulnscan.sh                # Sundays only
0  6  * * *     watchdog.py                # Full run
30 6  * * *     idle-think.sh              # GPU locked
0  7  * * *     idle-think.sh              # GPU locked
0  8  * * *     systemctl --user start openclaw-gateway
```

### Daytime (08:00–22:59)
```
0  8,14 * * *   repo-watch.sh --all
30 8  * * *     report.sh                  # Morning HTML rebuild
30 9,15,20 * *  ha-journal.py              # GPU locked
0  16 * * *     idle-think.sh              # GPU locked
0  18 * * *     repo-watch.sh --all --notify
0  19 * * *     idle-think.sh --task signal # GPU locked, THE notification
```

### Always
```
*/5 * * * *     presence.sh
*   * * * *     gpu-monitor.sh
*/30 * * * *    watchdog.py --live-only
```

## Dashboard
The web dashboard at http://192.168.3.151:8888 has these pages:
- **DASHBOARD** — Overview with host count, presence, latest notes
- **HOSTS** — All discovered network devices
- **PRESENCE** — Phone detection timeline
- **LINUX-MEDIA** — linux-media mailing list digest
- **SOC-BRINGUP** — soc-bringup mailing list digest
- **GSTREAMER / LIBCAMERA / V4L-UTILS / FFMPEG / LINUXTV** — Repo feeds
- **ISSUES** — LLM-generated issues/concerns from analysis
- **NOTES** — All idle-think notes (research, trends, career, etc.)
- **LOAD** — GPU utilization heatmap and breakdown
- **SECURITY** — Host security scoring
- **HISTORY** — Changelog
- **LOG** — Raw scan logs

## How to Help AK With This Data

When AK asks about monitoring results, you can:

1. **Read the latest notes:**
   ```bash
   cat /opt/netscan/data/think/notes-index.json
   ```
   Then read individual note files for details.

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

5. **See what is running now:**
   ```bash
   curl -s http://localhost:11434/api/ps
   ```

6. **Read the profile for context:**
   ```bash
   cat /opt/netscan/profile.json         # public interests
   cat /opt/netscan/profile-private.json  # career context
   ```

7. **Trigger a specific task manually:**
   ```bash
   /opt/netscan/idle-think.sh --task research
   ```
   (But be aware this uses the GPU heavily for ~2-5 minutes)
