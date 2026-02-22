#!/bin/bash
# syslog.sh — System activity logger for bc250
# Captures system health, service status, and OpenClaw activity.
# Run from cron every 5 minutes. All logs go to /opt/netscan/data/syslog/
#
# Log files (daily rotation, kept 30 days):
#   health-YYYY-MM-DD.tsv  — system metrics time series (TSV)
#   gateway-YYYY-MM-DD.log — OpenClaw gateway journal entries
#   ollama-YYYY-MM-DD.log  — Ollama service journal entries
#   events-YYYY-MM-DD.log  — notable events (errors, OOM, restarts, timeouts)
#
# Usage:
#   /opt/netscan/syslog.sh           # normal 5-min snapshot
#   /opt/netscan/syslog.sh --rotate  # daily rotation + cleanup (run at 00:05)

set -euo pipefail

LOGDIR="/opt/netscan/data/syslog"
TODAY=$(date +%Y-%m-%d)
NOW=$(date '+%Y-%m-%d %H:%M:%S')
HEALTH="$LOGDIR/health-${TODAY}.tsv"
EVENTS="$LOGDIR/events-${TODAY}.log"
GATEWAY_LOG="$LOGDIR/gateway-${TODAY}.log"
OLLAMA_LOG="$LOGDIR/ollama-${TODAY}.log"
RETAIN_DAYS=30

mkdir -p "$LOGDIR"

# ── Rotation mode ──────────────────────────────────────────────
if [[ "${1:-}" == "--rotate" ]]; then
    # Archive OpenClaw's /tmp log before it's lost
    TMPLOG="/tmp/openclaw/openclaw-$(date -d yesterday +%Y-%m-%d).log"
    if [[ -f "$TMPLOG" ]]; then
        cp "$TMPLOG" "$LOGDIR/openclaw-raw-$(date -d yesterday +%Y-%m-%d).log"
    fi

    # Purge old logs
    find "$LOGDIR" -name "*.tsv" -mtime +${RETAIN_DAYS} -delete 2>/dev/null || true
    find "$LOGDIR" -name "*.log" -mtime +${RETAIN_DAYS} -delete 2>/dev/null || true

    echo "[$NOW] Rotated. Kept last ${RETAIN_DAYS} days." >> "$EVENTS"
    exit 0
fi

# ── Health snapshot ────────────────────────────────────────────

# Write TSV header if new file
if [[ ! -f "$HEALTH" ]]; then
    printf "timestamp\tcpu_load_1m\tcpu_load_5m\tmem_total_mb\tmem_used_mb\tmem_avail_mb\tswap_used_mb\tgtt_used_mb\tgtt_total_mb\tcpu_temp_c\tgpu_temp_c\tollama_model\tollama_vram_mb\tollama_ctx\tgateway_status\tgateway_mem_mb\tuptime_sec\n" > "$HEALTH"
fi

# CPU load
read load1 load5 _ < /proc/loadavg

# Memory (in MB)
mem_total=$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo)
mem_avail=$(awk '/MemAvailable/ {printf "%d", $2/1024}' /proc/meminfo)
mem_used=$((mem_total - mem_avail))
swap_used=$(awk '/SwapTotal/ {t=$2} /SwapFree/ {f=$2} END {printf "%d", (t-f)/1024}' /proc/meminfo)

# GTT (GPU system memory)
gtt_total=$(awk '{printf "%d", $1/1048576}' /sys/class/drm/card1/device/mem_info_gtt_total 2>/dev/null || echo 0)
gtt_used=$(awk '{printf "%d", $1/1048576}' /sys/class/drm/card1/device/mem_info_gtt_used 2>/dev/null || echo 0)

# Temperatures
cpu_temp=$(sensors 2>/dev/null | awk '/Tctl/ {gsub(/[+°C]/,"",$2); print $2; exit}' || echo "?")
gpu_temp=$(sensors 2>/dev/null | awk '/edge/ {gsub(/[+°C]/,"",$2); print $2; exit}' || echo "?")

# Ollama
ollama_info=$(curl -s --connect-timeout 3 http://localhost:11434/api/ps 2>/dev/null || echo '{"models":[]}')
ollama_model=$(echo "$ollama_info" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    models = d.get('models', [])
    if models:
        m = models[0]
        print(m['name'])
    else:
        print('none')
except: print('error')
" 2>/dev/null || echo "error")

ollama_vram=$(echo "$ollama_info" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    models = d.get('models', [])
    if models:
        print(int(models[0].get('size_vram', 0) / 1048576))
    else:
        print(0)
except: print(0)
" 2>/dev/null || echo "0")

ollama_ctx=$(echo "$ollama_info" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    models = d.get('models', [])
    if models:
        print(models[0].get('context_length', 0))
    else:
        print(0)
except: print(0)
" 2>/dev/null || echo "0")

# OpenClaw gateway status
gw_status="unknown"
gw_mem="0"
if systemctl --user is-active openclaw-gateway &>/dev/null; then
    gw_status="running"
    gw_pid=$(systemctl --user show openclaw-gateway --property=MainPID --value 2>/dev/null || echo 0)
    if [[ "$gw_pid" -gt 0 ]]; then
        gw_mem=$(awk '/VmRSS/ {printf "%d", $2/1024}' /proc/$gw_pid/status 2>/dev/null || echo 0)
    fi
else
    gw_status="stopped"
fi

# Uptime in seconds
uptime_sec=$(awk '{printf "%d", $1}' /proc/uptime)

# Write health row
printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
    "$NOW" "$load1" "$load5" "$mem_total" "$mem_used" "$mem_avail" "$swap_used" \
    "$gtt_used" "$gtt_total" "$cpu_temp" "$gpu_temp" \
    "$ollama_model" "$ollama_vram" "$ollama_ctx" \
    "$gw_status" "$gw_mem" "$uptime_sec" >> "$HEALTH" 2>/dev/null || true

# ── Capture journal entries since last run ─────────────────────

LASTRUN="$LOGDIR/.last-journal-ts"
if [[ -f "$LASTRUN" ]]; then
    SINCE=$(cat "$LASTRUN")
else
    SINCE="5 minutes ago"
fi

# Gateway journal → persistent log
journalctl --user -u openclaw-gateway --since "$SINCE" --no-pager -o short-iso 2>/dev/null \
    >> "$GATEWAY_LOG" || true

# Ollama journal → persistent log
journalctl -u ollama --since "$SINCE" --no-pager -o short-iso 2>/dev/null \
    >> "$OLLAMA_LOG" || true

# ── Event detection ────────────────────────────────────────────

# Check gateway journal for notable events
gw_recent=$(journalctl --user -u openclaw-gateway --since "$SINCE" --no-pager 2>/dev/null || true)

# Timeouts
timeout_count=$(echo "$gw_recent" | grep -c "timeout" || true)
if [[ "$timeout_count" -gt 0 ]]; then
    echo "[$NOW] ⚠ Gateway: $timeout_count timeout(s) in last 5 min" >> "$EVENTS"
fi

# Failovers
failover_count=$(echo "$gw_recent" | grep -c "FailoverError\|fallback" || true)
if [[ "$failover_count" -gt 0 ]]; then
    echo "[$NOW] ⚠ Gateway: $failover_count failover(s)" >> "$EVENTS"
fi

# Signal errors
signal_errors=$(echo "$gw_recent" | grep -ci "signal.*error\|signal.*fail" || true)
if [[ "$signal_errors" -gt 0 ]]; then
    echo "[$NOW] ⚠ Signal: $signal_errors error(s)" >> "$EVENTS"
fi

# OOM / memory pressure
if journalctl --since "$SINCE" --no-pager 2>/dev/null | grep -qi "oom\|out of memory\|killed process"; then
    echo "[$NOW] 🔴 OOM killer activated!" >> "$EVENTS"
fi

# Service restarts
for svc in ollama openclaw-gateway; do
    if journalctl --since "$SINCE" --no-pager 2>/dev/null | grep -q "${svc}.*Started\|${svc}.*Stopped"; then
        echo "[$NOW] ℹ Service ${svc} restarted" >> "$EVENTS"
    fi
done

# Low memory warning (available < 500 MB)
if [[ "$mem_avail" -lt 500 ]]; then
    echo "[$NOW] ⚠ Low memory: ${mem_avail}MB available" >> "$EVENTS"
fi

# High swap usage (> 4 GB)
if [[ "$swap_used" -gt 4096 ]]; then
    echo "[$NOW] ⚠ High swap: ${swap_used}MB used" >> "$EVENTS"
fi

# High CPU temp (> 85°C)
if [[ "$cpu_temp" != "?" ]] && (( $(echo "$cpu_temp > 85" | bc -l 2>/dev/null || echo 0) )); then
    echo "[$NOW] ⚠ High CPU temp: ${cpu_temp}°C" >> "$EVENTS"
fi

# Update last-run timestamp
date '+%Y-%m-%d %H:%M:%S' > "$LASTRUN"
