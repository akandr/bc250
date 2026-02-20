#!/bin/bash
# gpu-monitor.sh — Sample Ollama GPU utilization every minute
# Logs to a TSV file: timestamp, status (busy/idle), model, script, vram_mb
# Designed to run from cron: * * * * * /opt/netscan/gpu-monitor.sh
#
# The TSV is read by generate-html.py to render the LOAD dashboard page.
# Auto-rotates: keeps last 14 days of samples (~20k lines).
#
# Location on bc250: /opt/netscan/gpu-monitor.sh

LOG_DIR="/opt/netscan/data"
LOG_FILE="$LOG_DIR/gpu-load.tsv"
TS=$(date '+%Y-%m-%d %H:%M')

# Fetch Ollama process list
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
    [ -z "$SCRIPT" ] && SCRIPT="manual"

    echo -e "${TS}\tbusy\t${MODEL}\t${SCRIPT}\t${VRAM_MB}" >> "$LOG_FILE"
else
    echo -e "${TS}\tidle\t\t\t0" >> "$LOG_FILE"
fi

# ─── Rotate: keep last 14 days ───
if [ -f "$LOG_FILE" ]; then
    CUTOFF=$(date -d "14 days ago" '+%Y-%m-%d' 2>/dev/null || date -v-14d '+%Y-%m-%d' 2>/dev/null)
    if [ -n "$CUTOFF" ] && [ $(wc -l < "$LOG_FILE") -gt 15000 ]; then
        TMP=$(mktemp)
        awk -v c="$CUTOFF" '$1 >= c' "$LOG_FILE" > "$TMP" && mv "$TMP" "$LOG_FILE"
    fi
fi
