#!/usr/bin/env bash
# Phase D phase-2 supplemental sweeps (Mac-side driver).
#
# Runs AFTER scripts/reboot-orchestrator.py (ceiling sweep) has finished.
# Performs:
#   T1) Step-1 perf baseline at 40 CU (10 models, runs=3)
#   T2) Multi-hop quality at 16K + 32K (4 production Ollama models)
#   T3) GGML_VK_PREFER_HOST_MEMORY=1 A/B (small perf sweep, off vs on)
#   T4) 24 CU vs 40 CU pp512 A/B (reboot between sides)
#
# All results land in benchmarks/results-phase-c/40cu-rerun/phase2/.
# Designed to be re-runnable: each test writes its own tagged JSON.
#
# Safety: assumes reboot-orchestrator is NOT running. Will refuse if it is.
# Uses bc250_reset (Home Assistant power cycle) as the hard-reset fallback.

set -euo pipefail
LOCAL_REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${LOCAL_REPO}/benchmarks/results-phase-c/40cu-rerun/phase2"
LOG="${OUT}/phase2.log"
mkdir -p "$OUT"
exec > >(tee -a "$LOG") 2>&1

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*"; }

# ---------- safety: no running orchestrator ----------
if pgrep -f reboot-orchestrator.py >/dev/null; then
  log "FATAL: reboot-orchestrator.py is running. Wait for it to finish or kill it."
  exit 1
fi

# ---------- helpers ----------
ssh_q() { ssh -o ConnectTimeout=10 -o ServerAliveInterval=15 bc250 "$@"; }

wait_for_ssh() {
  local max=${1:-1500}
  local t0=$SECONDS
  while (( SECONDS - t0 < max )); do
    if ssh -o ConnectTimeout=5 -o BatchMode=yes bc250 true 2>/dev/null; then
      return 0
    fi
    sleep 10
  done
  return 1
}

ensure_box_up() {
  log "ensuring bc250 is up ..."
  if wait_for_ssh 180; then return 0; fi
  log "  soft wait exceeded -- hard reset via bc250_reset"
  bc250_reset || true
  sleep 20
  wait_for_ssh 1500
}

reboot_and_wait() {
  log "rebooting bc250 ..."
  ssh_q 'sudo -n systemctl stop ollama 2>/dev/null; sync; sudo -n /sbin/reboot' || true
  sleep 30
  ensure_box_up
  log "  preflight: 40 CU check"
  ssh_q 'bash ~/bc250-40cu-unlock/scripts/cu_map.sh 2>&1 | grep -E "CUs active"' || true
  ssh_q 'curl -fsS -m 5 http://127.0.0.1:11434/api/tags >/dev/null && echo OLLAMA-OK'
}

push_bench() {
  log "pushing bench scripts"
  scp -q "$LOCAL_REPO/benchmarks/bench-phase-c.py" bc250:/tmp/
  ssh_q 'mkdir -p ~/phase-c-out/scratch ~/phase-c-out/logs ~/phase-c-out/results ~/phase-c-out/longctx-quality'
  scp -q "$LOCAL_REPO/benchmarks/results-longctx/bench-longctx-quality.py" bc250:~/phase-c-out/bench-longctx-quality.py
}

pull_results() {
  local tag="$1"
  scp -q bc250:'~/phase-c-out/results/step-1-perf.json' "$OUT/${tag}-step-1-perf.json" 2>/dev/null || true
  scp -q bc250:'~/phase-c-out/longctx-quality/longctx-quality-*.json' "$OUT/" 2>/dev/null || true
}

# ============================================================
# T1) Step-1 perf baseline at 40 CU
# ============================================================
run_t1_perf_40cu() {
  log "===== T1: Step-1 perf at 40 CU (10 models, runs=3) ====="
  reboot_and_wait
  push_bench
  log "  launching --step perf"
  ssh_q 'cd /tmp && python3 /tmp/bench-phase-c.py --step perf --runs 3 > ~/phase-c-out/logs/t1-perf-40cu.log 2>&1; echo RC=$?'
  pull_results "t1-perf-40cu"
  log "  T1 DONE -> $OUT/t1-perf-40cu-step-1-perf.json"
}

# ============================================================
# T2) Multi-hop quality at 16K + 32K
# ============================================================
run_t2_multihop() {
  log "===== T2: Multi-hop quality 16K+32K (4 Ollama models) ====="
  reboot_and_wait
  push_bench
  log "  launching bench-longctx-quality"
  ssh_q 'python3 ~/phase-c-out/bench-longctx-quality.py > ~/phase-c-out/logs/t2-multihop.log 2>&1; echo RC=$?'
  pull_results "t2-multihop"
  scp -q bc250:'~/phase-c-out/logs/t2-multihop.log' "$OUT/t2-multihop.log" || true
  log "  T2 DONE"
}

# ============================================================
# T3) GGML_VK_PREFER_HOST_MEMORY A/B (small perf sweep)
# ============================================================
run_t3_ggml_ab() {
  log "===== T3: GGML_VK_PREFER_HOST_MEMORY A/B ====="
  # Use small subset that exercises Vulkan path: 3 Ollama models, runs=3
  local AB_MODELS="qwen3.5-9b-ollama,gemma4-26b-q3,gemma4-latest"

  # --- side A: OFF ---
  reboot_and_wait
  log "  side A: GGML_VK_PREFER_HOST_MEMORY=off"
  scp -q "$LOCAL_REPO/scripts/toggle-ggml-hostmem.sh" bc250:/tmp/
  ssh_q 'chmod +x /tmp/toggle-ggml-hostmem.sh && /tmp/toggle-ggml-hostmem.sh off'
  push_bench
  ssh_q "cd /tmp && python3 /tmp/bench-phase-c.py --step perf --only $AB_MODELS --runs 3 > ~/phase-c-out/logs/t3-ggml-off.log 2>&1; echo RC=\$?"
  scp -q bc250:'~/phase-c-out/results/step-1-perf.json' "$OUT/t3-ggml-off-step-1-perf.json" || true

  # --- side B: ON ---
  reboot_and_wait
  log "  side B: GGML_VK_PREFER_HOST_MEMORY=on"
  ssh_q '/tmp/toggle-ggml-hostmem.sh on'
  push_bench
  ssh_q "cd /tmp && python3 /tmp/bench-phase-c.py --step perf --only $AB_MODELS --runs 3 > ~/phase-c-out/logs/t3-ggml-on.log 2>&1; echo RC=\$?"
  scp -q bc250:'~/phase-c-out/results/step-1-perf.json' "$OUT/t3-ggml-on-step-1-perf.json" || true
  log "  T3 DONE"
}

# ============================================================
# T4) 24 CU vs 40 CU pp512 A/B
# ============================================================
run_t4_cu_ab() {
  log "===== T4: 24 CU vs 40 CU pp512 A/B ====="
  scp -q "$LOCAL_REPO/scripts/toggle-cu-mode.sh" bc250:/tmp/

  # --- side A: 24 CU (mode=0) ---
  log "  side A: switching to bc250_cc_write_mode=0 then rebooting"
  ssh_q 'chmod +x /tmp/toggle-cu-mode.sh && /tmp/toggle-cu-mode.sh 0'
  reboot_and_wait
  log "  verify CU count after side A reboot"
  ssh_q 'bash ~/bc250-40cu-unlock/scripts/cu_map.sh 2>&1 | grep -E "CUs|active"' || true
  push_bench
  ssh_q 'cd /tmp && python3 /tmp/bench-phase-c.py --step perf --runs 3 > ~/phase-c-out/logs/t4-24cu.log 2>&1; echo RC=$?'
  scp -q bc250:'~/phase-c-out/results/step-1-perf.json' "$OUT/t4-24cu-step-1-perf.json" || true

  # --- side B: 40 CU (mode=3) ---
  log "  side B: switching to bc250_cc_write_mode=3 then rebooting"
  ssh_q '/tmp/toggle-cu-mode.sh 3'
  reboot_and_wait
  log "  verify CU count after side B reboot"
  ssh_q 'bash ~/bc250-40cu-unlock/scripts/cu_map.sh 2>&1 | grep -E "CUs|active"' || true
  push_bench
  ssh_q 'cd /tmp && python3 /tmp/bench-phase-c.py --step perf --runs 3 > ~/phase-c-out/logs/t4-40cu.log 2>&1; echo RC=$?'
  scp -q bc250:'~/phase-c-out/results/step-1-perf.json' "$OUT/t4-40cu-step-1-perf.json" || true

  log "  T4 DONE -- box is back in 40 CU mode"
}

# ---------- main ----------
ONLY="${1:-all}"
log "phase-d phase-2 START  only=$ONLY  out=$OUT"
case "$ONLY" in
  t1)  run_t1_perf_40cu ;;
  t2)  run_t2_multihop ;;
  t3)  run_t3_ggml_ab ;;
  t4)  run_t4_cu_ab ;;
  all) run_t1_perf_40cu; run_t2_multihop; run_t3_ggml_ab; run_t4_cu_ab ;;
  *) log "usage: $0 [t1|t2|t3|t4|all]"; exit 2 ;;
esac
log "phase-d phase-2 DONE"
