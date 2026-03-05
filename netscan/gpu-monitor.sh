#!/bin/bash
# gpu-monitor.sh — Sample Ollama GPU utilization every minute
# Logs to a TSV file with 8 columns:
#   timestamp  status  model  script  vram_mb  gpu_mhz  temp_c  throttle
#
# throttle: integer bitmask from gpu_metrics (0=none, see THROTTLE_BITS in gpu-monitor.py)
#
# Status values (3-state):
#   generating — model loaded AND GPU clock boosted (actually computing)
#   loaded     — model in VRAM but GPU at base clock (idle keep-alive)
#   idle       — no model loaded
#
# GPU clock (pp_dpm_sclk) is the ground truth for real utilization:
#   1000 MHz = idle/base,  1500+ MHz = GPU actively computing
#
# Designed to run from cron: * * * * * /opt/netscan/gpu-monitor.sh
# The TSV is read by generate-html.py to render the LOAD dashboard page.
# Auto-rotates: keeps last 14 days of samples (~20k lines).
#
# Location on bc250: /opt/netscan/gpu-monitor.sh

LOG_DIR="/opt/netscan/data"
LOG_FILE="$LOG_DIR/gpu-load.tsv"
TS=$(date '+%Y-%m-%d %H:%M')

# ─── Read hardware GPU metrics ───
# GPU clock: parse the active frequency from pp_dpm_sclk (line ending with *)
GPU_MHZ=$(grep '\*' /sys/class/drm/card1/device/pp_dpm_sclk 2>/dev/null \
          | grep -oP '\d+(?=Mhz)' || echo "0")
# Temperature: millidegrees → degrees
TEMP_MC=$(cat /sys/class/drm/card1/device/hwmon/hwmon2/temp1_input 2>/dev/null || echo "0")
TEMP_C=$(( TEMP_MC / 1000 ))

# Throttle status from gpu_metrics binary (v2.2, offset 108, uint32 LE)
THROTTLE="0"
if [ -r /sys/class/drm/card1/device/gpu_metrics ]; then
    THROTTLE=$(python3 -c "
import struct
d = open('/sys/class/drm/card1/device/gpu_metrics','rb').read()
if len(d) >= 112 and d[2] == 2:
    print(struct.unpack_from('<I', d, 108)[0])
else:
    print(0)
" 2>/dev/null || echo "0")
fi

# ─── Fetch Ollama process list ───
PS_JSON=$(curl -s --max-time 5 http://localhost:11434/api/ps 2>/dev/null || echo '{"models":[]}')
MODEL_COUNT=$(echo "$PS_JSON" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(len(d.get('models', [])))
except:
    print(0)
" 2>/dev/null || echo "0")

if [ "$MODEL_COUNT" -gt 0 ]; then
    # Extract model name and VRAM usage
    read MODEL VRAM_MB <<< $(echo "$PS_JSON" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    m = d['models'][0]
    name = m.get('name', '?')
    vram = m.get('size_vram', 0) // (1024*1024)
    print(name, vram)
except:
    print('? 0')
" 2>/dev/null || echo "? 0")

    # Identify which script is driving the model
    SCRIPT=""
    pgrep -f "lore-digest.sh" >/dev/null 2>&1 && SCRIPT="lore-digest"
    pgrep -f "repo-watch.sh" >/dev/null 2>&1 && SCRIPT="repo-watch"
    pgrep -f "idle-think.sh" >/dev/null 2>&1 && SCRIPT="idle-think"
    pgrep -f "report.sh" >/dev/null 2>&1 && SCRIPT="report"
    # New nightly batch scripts (added 2026-02)
    [ -z "$SCRIPT" ] && pgrep -f "career-scan.py" >/dev/null 2>&1 && SCRIPT="career-scan"
    [ -z "$SCRIPT" ] && pgrep -f "salary-tracker.py" >/dev/null 2>&1 && SCRIPT="salary-tracker"
    [ -z "$SCRIPT" ] && pgrep -f "company-intel.py" >/dev/null 2>&1 && SCRIPT="company-intel"
    [ -z "$SCRIPT" ] && pgrep -f "patent-watch.py" >/dev/null 2>&1 && SCRIPT="patent-watch"
    [ -z "$SCRIPT" ] && pgrep -f "event-scout.py" >/dev/null 2>&1 && SCRIPT="event-scout"
    [ -z "$SCRIPT" ] && pgrep -f "ha-journal.py" >/dev/null 2>&1 && SCRIPT="ha-journal"
    [ -z "$SCRIPT" ] && pgrep -f "leak-monitor.py" >/dev/null 2>&1 && SCRIPT="leak-monitor"
    # Gateway / Signal chat — openclaw or litellm proxy serving interactive queries
    [ -z "$SCRIPT" ] && pgrep -f "openclaw\|litellm" >/dev/null 2>&1 && SCRIPT="gateway"
    [ -z "$SCRIPT" ] && SCRIPT="unknown"

    # 3-state: generating (clock boosted) vs loaded (model in VRAM, idle)
    if [ "$GPU_MHZ" -gt 1200 ]; then
        STATUS="generating"
    else
        STATUS="loaded"
    fi

    echo -e "${TS}\t${STATUS}\t${MODEL}\t${SCRIPT}\t${VRAM_MB}\t${GPU_MHZ}\t${TEMP_C}\t${THROTTLE}" >> "$LOG_FILE"
else
    echo -e "${TS}\tidle\t\t\t0\t${GPU_MHZ}\t${TEMP_C}\t${THROTTLE}" >> "$LOG_FILE"
fi

# ─── Rotate: keep last 14 days ───
if [ -f "$LOG_FILE" ]; then
    CUTOFF=$(date -d "14 days ago" '+%Y-%m-%d' 2>/dev/null || date -v-14d '+%Y-%m-%d' 2>/dev/null)
    if [ -n "$CUTOFF" ] && [ $(wc -l < "$LOG_FILE") -gt 15000 ]; then
        TMP=$(mktemp)
        awk -v c="$CUTOFF" '$1 >= c' "$LOG_FILE" > "$TMP" && mv "$TMP" "$LOG_FILE"
    fi
fi
