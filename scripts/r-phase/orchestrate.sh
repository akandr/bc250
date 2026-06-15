#!/usr/bin/env bash
# orchestrate.sh — serial driver for the full R-phase re-benchmark.
# Designed to run under nohup on the BC-250 box. Each step writes its own
# JSON; --merge makes them resumable. Heartbeat written after every step;
# bench-status.sh refreshed after every step.
#
# Usage:
#   nohup bash ~/phase-c-out/orchestrate.sh > ~/phase-c-out/orchestrate.nohup.out 2>&1 &
#
# Skip steps via env:  SKIP_STEPS="cold,kv-quant" bash orchestrate.sh
# Run subset via env:  ONLY_STEPS="env-snapshot,mxfp4-probe,heap-ab" bash orchestrate.sh
set -u
OUT=${PHASE_C_OUT:-$HOME/phase-c-out}
PY=${BENCH_PY:-$OUT/bench-phase-c.py}
RESULTS="$OUT/results"
HB="$OUT/HEARTBEAT.json"
LOG="$OUT/orchestrate.log"
STATUS_SH="$OUT/bench-status.sh"

# VM/memory tuning. Box has 16 GB UMA; weights up to ~11.5 GiB load fine with
# KV-q4 at 4K ctx, so default ceiling 13.5 GiB.
export BC250_VM_CEILING_GIB="${BC250_VM_CEILING_GIB:-13.5}"
export BC250_MEM_AVAIL_MIN_GIB="${BC250_MEM_AVAIL_MIN_GIB:-1.5}"

mkdir -p "$OUT" "$RESULTS"

# /tmp lives on tmpfs and gets wiped at every reboot, so reinstall the sampler
# from a persistent copy in $OUT if it's missing.
if [[ ! -x /tmp/gpu-overlay-sampler.sh && -f "$OUT/gpu-overlay-sampler.sh" ]]; then
    cp "$OUT/gpu-overlay-sampler.sh" /tmp/gpu-overlay-sampler.sh
    chmod +x /tmp/gpu-overlay-sampler.sh
fi
exec >> "$LOG" 2>&1
echo "=== orchestrate started $(date -Iseconds) pid=$$ ==="

heartbeat() {
    cat > "$HB" <<EOF
{"step":"$1","detail":"$2","pid":$$,"updated":"$(date -Iseconds)"}
EOF
    [[ -x "$STATUS_SH" ]] && bash "$STATUS_SH" >/dev/null 2>&1 || true
}

run_step() {
    local name="$1" ; shift
    if [[ -n "${SKIP_STEPS:-}" && ",${SKIP_STEPS}," == *",${name},"* ]]; then
        echo "[skip-step] $name (in SKIP_STEPS)"; return
    fi
    if [[ -n "${ONLY_STEPS:-}" && ",${ONLY_STEPS}," != *",${name},"* ]]; then
        echo "[skip-step] $name (not in ONLY_STEPS)"; return
    fi
    echo
    echo "=== step:$name $(date -Iseconds) ==="
    heartbeat "$name" "starting"
    python3 "$PY" --step "$name" "$@" || echo "[step-fail] $name rc=$?"
    heartbeat "$name" "done"
    sleep 30
}

# 1. Snapshot environment first so every later JSON can be tied to a stack.
run_step env-snapshot

# 2. MXFP4 probe (fast; defines whether gpt-oss-20b is usable in the perf run).
run_step mxfp4-probe

# 3. Full perf table across the refreshed model list (merge to keep partials).
run_step perf --merge

# 4. Filled-context quality sweep — defaults to all tiers up to 32K.
run_step ctx-quality --merge --runs 2 --n-gen 200

# 5. Heap A/B controlled experiment (6 models × 3 ctx × 2 variants × 2 runs).
run_step heap-ab --merge --runs 2

# 6. Cold start (only ports already implemented).
run_step cold || true

# 7. Quality at filled 32K and long-context tiers.
run_step qual-32k || true
run_step qual-long || true

# 8. KV-quant trade-off rerun (will rerun on MXFP4 model if available).
run_step kv-quant || true

heartbeat "all-done" "orchestrate finished cleanly"
echo "=== orchestrate FINISHED $(date -Iseconds) ==="
