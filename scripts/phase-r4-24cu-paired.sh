#!/usr/bin/env bash
# Phase R4: paired 24-CU vs 40-CU step-1 perf for the rebench model set.
#
# Mirrors scripts/phase-d-phase2-t4-rerun.sh but parameterised on MODELS and
# writes into results-phase-c-r2/cu-paired/{24cu,40cu}/. Each side reboots
# into the requested bc250_cc_write_mode (0 = harvested 24 CU, 3 = full
# 40 CU), runs `bench-phase-c.py --step perf --merge --runs 3`, and pulls
# step-1-perf.json plus the cu_map.sh evidence.
#
# Tuning knobs (env vars):
#   MODELS="a,b,c"     subset to run (default: tier-1 rebench list)
#   RUNS=3             runs per cell
#   SKIP_24CU=1        skip side A
#   SKIP_40CU=1        skip side B
#   FINAL_MODE=3       CU mode to leave the box in afterwards (default 3)
#
# Pre-reqs: bench-phase-c.py + toggle-cu-mode.sh already in repo; box has
# bc250-40cu-unlock kernel module installed; HASS reset hook (bc250_reset)
# available locally for hard recovery.
set -uo pipefail

LOCAL_REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${LOCAL_REPO}/benchmarks/results-phase-c-r2/cu-paired"
LOG="${OUT}/cu-paired.log"
mkdir -p "$OUT/24cu" "$OUT/40cu"
exec > >(tee -a "$LOG") 2>&1

# Tier-1 subset: avoid the full 10-model run (wedges the box at 24 CU).
# Excludes qwen3.5-9b-q4km (broken GGUF on current llama.cpp build).
DEFAULT_MODELS="qwen3.5-9b-ollama,deepseek-r1-14b,gpt-oss-20b-mxfp4,gpt-oss-20b-ollama,qwen3-coder-30b-iq2m,qwen3.5-35b-iq2m,gemma4-latest,gemma4-26b-q3"
MODELS="${MODELS:-$DEFAULT_MODELS}"
RUNS="${RUNS:-3}"
FINAL_MODE="${FINAL_MODE:-3}"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*"; }
ssh_q() { ssh -o ConnectTimeout=10 -o ServerAliveInterval=30 bc250 "$@"; }

wait_for_ssh() {
  local max="${1:-1500}" t=0
  while (( t < max )); do
    ssh -o ConnectTimeout=5 -o BatchMode=yes bc250 true 2>/dev/null && return 0
    sleep 5; t=$((t+5))
  done
  return 1
}

ensure_box_up() {
  if ! wait_for_ssh 180; then
    log "  soft wait failed, invoking bc250_reset"
    bc250_reset || true
    sleep 25
    wait_for_ssh 1500 || { log "FATAL: box did not come up"; return 1; }
  fi
  ssh_q 'bash ~/bc250-40cu-unlock/scripts/cu_map.sh 2>&1 | grep -E "CUs|active|harvested" | head -3' || true
  ssh_q 'curl -s --max-time 5 http://localhost:11434/api/tags >/dev/null && echo OLLAMA-OK || echo OLLAMA-DOWN'
}

reboot_and_wait() {
  log "rebooting bc250 ..."
  ssh_q 'sudo systemctl reboot' || true
  sleep 30
  ensure_box_up
}

push_bench() {
  log "pushing bench scripts + toggles"
  scp -q "$LOCAL_REPO/benchmarks/tmp/bench-phase-c.py" bc250:phase-c-out/bench-phase-c.py
  scp -q "$LOCAL_REPO/scripts/toggle-cu-mode.sh"       bc250:/tmp/
  ssh_q 'chmod +x /tmp/toggle-cu-mode.sh ~/phase-c-out/bench-phase-c.py 2>/dev/null; true'
}

run_side() {
  local mode="$1" tag="$2" outdir="$3"
  log "===== side $tag: bc250_cc_write_mode=$mode ====="
  push_bench
  ssh_q "/tmp/toggle-cu-mode.sh $mode"
  reboot_and_wait
  push_bench   # /tmp wiped on reboot
  log "  verify CU map after $tag reboot"
  ssh_q 'cat /sys/module/amdgpu/parameters/bc250_cc_write_mode 2>/dev/null || echo "no parameter"' \
    | tee "$outdir/cc_write_mode.txt"
  ssh_q 'bash ~/bc250-40cu-unlock/scripts/cu_map.sh 2>&1 | grep -E "CUs|active|harvested"' \
    | tee "$outdir/cu_map.txt"
  log "  starting step-1 perf (--merge, runs=$RUNS) for $MODELS"
  ssh_q "cd ~/phase-c-out && python3 bench-phase-c.py --step perf --only $MODELS --merge --runs $RUNS \
         > ~/phase-c-out/logs/cu-${tag}.log 2>&1; echo RC=\$?" | tee -a "$outdir/run-rc.txt"
  scp -q "bc250:phase-c-out/results/step-1-perf.json" "$outdir/step-1-perf.json" || true
  scp -q "bc250:phase-c-out/logs/cu-${tag}.log"        "$outdir/run.log"          || true
}

ensure_box_up

if [[ "${SKIP_24CU:-0}" != "1" ]]; then
  run_side 0 24cu "$OUT/24cu"
fi
if [[ "${SKIP_40CU:-0}" != "1" ]]; then
  run_side 3 40cu "$OUT/40cu"
fi

# Restore final mode if it differs from whatever we ended on.
end_mode=$(ssh_q 'cat /sys/module/amdgpu/parameters/bc250_cc_write_mode 2>/dev/null')
if [[ "$end_mode" != "$FINAL_MODE" ]]; then
  log "switching back to FINAL_MODE=$FINAL_MODE (ended on $end_mode)"
  push_bench
  ssh_q "/tmp/toggle-cu-mode.sh $FINAL_MODE"
  reboot_and_wait
fi

log "===== Phase R4 paired CU run complete ====="
log "  outputs: $OUT/{24cu,40cu}/step-1-perf.json"
log "  models : $MODELS"
log "  runs   : $RUNS"
