#!/usr/bin/env bash
# Single-cell runner with disk-backed output, memory sampler, and post-run summary.
# Usage: run-cell.sh STEP MODEL [TIER] [RUNS] [NGEN] [TAG]
set -u

STEP="${1:?step}"
MODEL="${2:?model}"
TIER="${3:-4096}"
RUNS="${4:-1}"
NGEN="${5:-100}"
TAG="${6:-cell}"

OUT="$HOME/phase-c-out"
mkdir -p "$OUT" /tmp/phase-c

LOG="$OUT/${TAG}.log"
MEM="$OUT/${TAG}.mem.log"
JSON_DST="$OUT/${TAG}.json"

case "$STEP" in
  ctx-quality) SRC_JSON="/tmp/phase-c/step-2-ctx-quality.json"; EXTRA="--tiers $TIER" ;;
  perf)        SRC_JSON="/tmp/phase-c/step-1-perf.json";        EXTRA="" ;;
  *)           SRC_JSON="/tmp/phase-c/step-${STEP}.json";       EXTRA="" ;;
esac
rm -f "$SRC_JSON" "$LOG" "$MEM"

# memory sampler in background
( while true; do
    printf '%s ' "$(date +%H:%M:%S)"
    awk '/^MemFree:|^MemAvailable:|^Cached:|^SwapFree:|^Committed_AS:/ {printf "%s=%s ", $1, $2}' /proc/meminfo
    echo
    sleep 2
  done ) > "$MEM" &
MSPID=$!

echo "[$(date +%H:%M:%S)] cell start step=$STEP model=$MODEL tier=$TIER runs=$RUNS ngen=$NGEN" | tee -a "$LOG"
echo "[$(date +%H:%M:%S)] thermals_pre: $(sensors 2>/dev/null | grep -E 'vddgfx|edge|PPT' | tr '\n' ' ')" | tee -a "$LOG"

timeout --kill-after=30 1200 python3 "$OUT/bench-phase-c.py" \
  --step "$STEP" --only "$MODEL" $EXTRA --runs "$RUNS" --n-gen "$NGEN" --merge \
  >> "$LOG" 2>&1
RC=$?

kill "$MSPID" 2>/dev/null
wait "$MSPID" 2>/dev/null

echo "[$(date +%H:%M:%S)] cell end rc=$RC" | tee -a "$LOG"
echo "[$(date +%H:%M:%S)] thermals_post: $(sensors 2>/dev/null | grep -E 'vddgfx|edge|PPT' | tr '\n' ' ')" | tee -a "$LOG"

# copy result JSON to home if present
if [ -f "$SRC_JSON" ]; then
  cp -f "$SRC_JSON" "$JSON_DST"
  echo "saved $JSON_DST" | tee -a "$LOG"
fi

# print summary
MIN_AVAIL=$(awk '
  {
    for (i=2;i<=NF;i++) {
      split($i,a,"=")
      if (a[1]=="MemAvailable:") {
        v = a[2]+0
        if (min=="" || v<min) { min=v; t=$1 }
      }
    }
  }
  END { print min " kB at " t }' "$MEM")
echo "MIN_MEM_AVAILABLE=$MIN_AVAIL" | tee -a "$LOG"

echo "=== LOG TAIL ==="
tail -30 "$LOG"

exit $RC
