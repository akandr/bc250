# Career Intelligence Scanner

ClawdBot can monitor job opportunities and company intelligence.

## always: true

## Tool: exec

### Trigger career scan

```bash
python3 /opt/netscan/career-scan.py
```

Full scan — scrapes company career pages, job boards, and intel sites.
Takes 5-15 minutes depending on page load times and LLM analysis.

### Quick scan (career pages only)

```bash
python3 /opt/netscan/career-scan.py --quick
```

Skips intel sources (gowork, layoffs.fyi, levels.fyi). Faster.

### Check latest scan results

```bash
cat /opt/netscan/data/career/latest-scan.json | python3 -m json.tool | head -80
```

### List scan archives

```bash
ls -la /opt/netscan/data/career/scan-*.json
```

## Schedule

- **Mon/Thu 11:00** — Full scan via cron
- Signal alerts sent automatically for hot matches (score ≥ 80%) and urgent intel (layoffs)

## Target Companies

| Company | Industry | Why |
|---------|----------|-----|
| Nvidia | Silicon | Tegra, Jetson, kernel drivers |
| Google | Silicon | ChromeOS, Pixel, Android camera |
| AMD | Silicon | GPU drivers, ROCm, RDNA |
| Intel | Silicon | IPU, camera, kernel team |
| Samsung | Silicon | Exynos, camera ISP |
| Amazon | Tech | Ring, Alexa, embedded Linux |
| TCL Research | Consumer | TCL Research Europe (Łódź) |
| Qualcomm | Silicon | Snapdragon camera, MIPI CSI |
| Arm | Silicon | Mali GPU, kernel |
| HARMAN | Automotive | Current employer monitoring |

## Profile Match Criteria

- **Must match**: kernel, drivers, embedded, V4L2, camera, MIPI, ISP, BSP, SoC
- **Location**: Remote-from-Poland OR hybrid Łódź/Warsaw only
- **Scoring**: 0-100, hot ≥ 70, good ≥ 40
- **Signal alert**: score ≥ 80 + remote-compatible

## Dashboard

Results visible at `http://bc250:8888/career.html`

## When user asks about jobs

If AK asks about job opportunities, career market, or specific companies:
1. Check latest scan: `cat /opt/netscan/data/career/latest-scan.json`
2. Summarize hot matches and intel alerts
3. Offer to run a fresh scan if data is > 3 days old
