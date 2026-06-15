#!/usr/bin/env bash
# bench-status.sh — produce ~/phase-c-out/STATUS.md from current results JSONs.
# Idempotent; safe to invoke at any time. Designed for periodic check-ins:
#   ssh bc250 'bash ~/phase-c-out/bench-status.sh && cat ~/phase-c-out/STATUS.md'
set -u
OUT=${PHASE_C_OUT:-$HOME/phase-c-out}
RESULTS="$OUT/results"
STATUS="$OUT/STATUS.md"
HB="$OUT/HEARTBEAT.json"

mkdir -p "$OUT" "$RESULTS"

now=$(date -Iseconds)

{
echo "# Phase-C R-phase status"
echo "_generated $now on $(hostname)_"
echo
echo "## Heartbeat"
if [[ -s "$HB" ]]; then
    cat "$HB"
    echo
    age=$(($(date +%s) - $(stat -c %Y "$HB" 2>/dev/null || echo 0)))
    echo "heartbeat age: ${age}s"
else
    echo "no heartbeat file"
fi
echo
echo "## Process check"
if pgrep -af "bench-phase-c\.py" >/dev/null; then
    pgrep -af "bench-phase-c\.py"
else
    echo "no bench-phase-c.py running"
fi
echo
echo "## Disk / memory"
df -h "$OUT" /opt 2>/dev/null | head -5
echo
grep -E "MemTotal|MemAvailable" /proc/meminfo
echo
echo "## Results inventory"
for j in env-snapshot.json mxfp4-probe.json step-1-perf.json step-ctx-quality.json step-heap-ab.json step-kv-quant.json step-cold.json step-qual-32k.json step-qual-long.json; do
    f="$RESULTS/$j"
    if [[ -s "$f" ]]; then
        n=$(python3 -c "import json; d=json.load(open('$f')); r=d.get('results',{}); print(len(r) if isinstance(r,dict) else len(r))" 2>/dev/null)
        sz=$(du -h "$f" | cut -f1)
        mt=$(date -r "$f" "+%Y-%m-%d %H:%M:%S")
        echo "- **$j**  rows=${n:-?}  size=$sz  mtime=$mt"
    else
        echo "- $j  (not yet)"
    fi
done
echo
echo "## Skipped / failed cells (last 40)"
python3 - <<'PY' 2>/dev/null
import os, json, glob
results_dir = os.path.expanduser(os.environ.get("PHASE_C_OUT", "~/phase-c-out")) + "/results"
bad = []
for f in sorted(glob.glob(f"{results_dir}/*.json")):
    try: d = json.load(open(f))
    except: continue
    def walk(o, path=""):
        if isinstance(o, dict):
            if "status" in o and o.get("status") in ("skip","fail","timeout","parse_fail") or o.get("status","").startswith("exception"):
                bad.append((os.path.basename(f), o.get("model","?"), o.get("label",path), o.get("status"), o.get("skip_reason") or o.get("error") or ""))
            for k,v in o.items(): walk(v, f"{path}/{k}")
        elif isinstance(o, list):
            for i,v in enumerate(o): walk(v, f"{path}[{i}]")
    walk(d)
for row in bad[-40:]:
    print(f"- `{row[0]}` model={row[1]} label={row[2]} status={row[3]} reason={row[4][:120]}")
if not bad: print("none.")
PY
echo
echo "## Latest log tail (last 30 lines)"
LATEST=$(ls -t "$OUT"/*.nohup.out "$OUT"/*.log 2>/dev/null | head -1)
if [[ -n "$LATEST" ]]; then
    echo "_from $(basename "$LATEST")_"
    echo
    echo '```'
    tail -30 "$LATEST"
    echo '```'
else
    echo "no log file"
fi
echo
echo "## GPU snapshot"
echo '```'
cat /sys/class/drm/card*/device/pp_dpm_sclk 2>/dev/null | head -10
echo "---"
sensors 2>/dev/null | grep -iE "edge|junction|tdie|tctl|power" | head -10
echo '```'
} > "$STATUS"

echo "wrote $STATUS"
