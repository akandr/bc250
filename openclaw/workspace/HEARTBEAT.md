# Heartbeat / Periodic Awareness

## What Runs Automatically (cron, not you)
The netscan ecosystem runs on cron — you do NOT need to trigger these.
See ECOSYSTEM.md for the full schedule.

### Quiet Hours (23:00–08:00) — GPU free for batch
| Time  | What | GPU? |
|-------|------|------|
| 22:55 | Gateway STOP (no chat) | — |
| 23:30 | repo-watch --all | No |
| 00:30 | ha-journal (Home Assistant analysis) | Yes (locked) |
| 01:00 | **career-scan.py** (two-phase, 15-60 min) | Yes (locked) |
| 04:00 | Network scan (nmap) | No |
| 04:30 | Deep service enumeration | No |
| 05:00 | lore-digest (mailing lists) | Yes (locked) |
| 05:30 | Weekly vulnscan (Sundays) | No |
| 06:00 | Watchdog full run | No |
| 06:30 | idle-think #1 | Yes (locked) |
| 07:00 | idle-think #2 | Yes (locked) |
| 08:00 | Gateway START (chat resumes) | — |

### Daytime (08:00–22:59) — Signal chat active, GPU guard on
| Time  | What | GPU? |
|-------|------|------|
| 08:00, 14:00 | repo-watch --all | No |
| 08:30 | Morning health report (HTML) | No |
| 09:30, 15:30, 20:30 | ha-journal | Yes (locked) |
| 16:00 | idle-think (afternoon) | Yes (locked) |
| 18:00 | repo-watch +notify | No |
| 19:00 | **Signal filter** (scans data, pings AK if match) | Yes (locked) |

### Always Running
| Freq | What |
|------|------|
| Every 5 min | Phone presence check |
| Every 1 min | GPU utilization sampler |
| Every 30 min | Watchdog live integrity |

## Your Role
You are the conversational interface to all this data.
When AK messages you, he likely wants to discuss:
- What the monitoring found today
- A kernel patch or repo change that came through
- Career strategy or technical deep-dives
- Something he read that connects to the watchlist
- Home status (temperature, air quality, lights)

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
```

## Memory Protocol
Save conversation summaries to `memory/YYYY-MM-DD.md`.
Read today + yesterday on every context reset.
This is how you remember across sessions — your only persistent memory.
