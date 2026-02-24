# Heartbeat / Periodic Awareness

## Orchestration Architecture (updated Feb 2026)

All GPU tasks now run through **openclaw cron → Clawd agent turns → shell tools → scripts → Ollama**.
The gateway runs **24/7** — no more stop/start schedule.
Signal messages queue and process after the current agent turn completes.

Non-GPU tasks (network scan, presence, syslog, watchdog) remain in **system crontab**.

## What openclaw cron Runs (38 jobs/day)

### Night Batch (23:00–07:59) — 24 jobs, back-to-back every ~20 min
| Time  | What | Duration |
|-------|------|----------|
| 23:00 | leak-monitor CTI scan | ~5–10 min |
| 23:20 | idle-think trends | ~3–5 min |
| 23:40 | idle-think research | ~3–5 min |
| 00:00 | ha-journal | ~2–3 min |
| 00:20 | **career-scan** (long) | ~15–60 min |
| 01:30 | salary-tracker | ~5–10 min |
| 01:50 | company-intel | ~5–10 min |
| 02:10 | patent-watch | ~5–10 min |
| 02:30 | event-scout | ~5–10 min |
| 02:50 | idle-think crossfeed | ~3–5 min |
| 03:10 | idle-think career | ~3–5 min |
| 03:30 | idle-think crawl | ~3–5 min |
| 03:50 | idle-think learn | ~3–5 min |
| 04:10 | idle-think weekly | ~3–5 min |
| 04:30 | lore-digest | ~10–15 min |
| 05:00 | idle-think research (round 2) | ~3–5 min |
| 05:20 | idle-think trends (round 2) | ~3–5 min |
| 05:40 | ha-correlate | ~5–10 min |
| 06:00 | idle-think crossfeed (round 2) | ~3–5 min |
| 06:20 | idle-think research (round 3) | ~3–5 min |
| 06:40 | ha-journal (round 2) | ~2–3 min |
| 07:00 | idle-think crawl (round 2) | ~3–5 min |
| 07:20 | idle-think research (round 4) | ~3–5 min |
| 07:40 | leak-monitor morning scan | ~5–10 min |

### Daytime (08:00–22:59) — 14 jobs, hourly cadence
| Time  | What | Duration |
|-------|------|----------|
| 09:00 | ha-journal | ~2–3 min |
| 10:00 | idle-think research | ~3–5 min |
| 11:00 | leak-monitor midday scan | ~5–10 min |
| 12:00 | ha-journal | ~2–3 min |
| 13:00 | idle-think trends | ~3–5 min |
| 14:00 | idle-think crossfeed | ~3–5 min |
| 15:00 | ha-journal | ~2–3 min |
| 16:00 | idle-think crawl | ~3–5 min |
| 17:00 | idle-think career | ~3–5 min |
| 18:00 | ha-journal | ~2–3 min |
| 19:00 | **idle-think signal** 📱 → sent to AK | ~3–5 min |
| 20:00 | idle-think research | ~3–5 min |
| 21:00 | ha-journal | ~2–3 min |
| 22:00 | idle-think research | ~3–5 min |

### System Crontab (non-GPU, always running)
| Freq | What |
|------|------|
| Every 1 min | gpu-monitor.sh + gpu-monitor.py collect |
| Every 5 min | presence.sh + syslog.sh |
| Every 30 min | watchdog.py --live-only |
| 00:05 | syslog.sh --rotate |
| 04:00 | scan.sh (nmap) |
| 04:30 | enumerate.sh |
| 05:30 Sun | vulnscan.sh |
| 06:00 | watchdog.py (full) |
| 08:00, 14:00 | repo-watch.sh --all |
| 08:30 | report.sh |
| 18:00 | repo-watch.sh --all --notify |
| 22:55 | gpu-monitor.py chart |
| 23:30 | repo-watch.sh --all |

## Your Role
You are the conversational interface to all this data.
When AK messages you, he likely wants to discuss:
- What the monitoring found today
- A kernel patch or repo change that came through
- Career strategy or technical deep-dives
- Something he read that connects to the watchlist
- Home status (temperature, air quality, lights)
- Leak/breach intelligence findings

## Checking What Is Fresh
```bash
# Latest notes (most recent research/analysis)
ls -t /opt/netscan/data/think/note-*.json | head -5

# Latest career scan
python3 -c "import json; d=json.load(open('/opt/netscan/data/career/latest-scan.json')); m=d['meta']; print(f'{m[\"timestamp\"]}: {m[\"total_jobs_found\"]} jobs, {m[\"hot_matches\"]} hot, {m[\"remote_compatible\"]} remote')"

# Latest repo items
find /opt/netscan/data/repos -name "items-*.json" -mtime -1

# Latest digests
ls -t /opt/netscan/data/lkml/digest-*.json /opt/netscan/data/soc/digest-*.json 2>/dev/null | head -3

# Cron logs (check for failures)
tail -10 /opt/netscan/data/think-cron.log
tail -5 /opt/netscan/data/career-cron.log
tail -5 /opt/netscan/data/ha-journal-cron.log

# openclaw cron job status
openclaw cron list
openclaw cron runs --last 10
```

## Memory Protocol
Save conversation summaries to `memory/YYYY-MM-DD.md`.
Read today + yesterday on every context reset.
This is how you remember across sessions — your only persistent memory.
