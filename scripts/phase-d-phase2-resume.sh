#!/usr/bin/env bash
# Resume phase-d-phase2 from T3 side B (GGML on) + T4 (CU AB).
# Fix vs. original: push toggle scripts AFTER each reboot (they live in /tmp
# which gets wiped on reboot).
set -uo pipefail   # NOTE: no -e: don't exit on a single ssh hiccup
LOCAL_REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${LOCAL_REPO}/benchmarks/results-phase-c/40cu-rerun/phase2"
LOG="${OUT}/phase2-resume.log"
mkdir -p "$OUT"
exec > >(tee -a "$LOG") 2>&1

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*"; }
ssh_q() { ssh -o ConnectTimeout=10 -o ServerAliveInterval=30 bc250 "$@"; }

wait_for_ssh() {
  local max="${1:-1500}" t=0
  while (( t < max )); do
    if ssh -o ConnectTimeout=5 -o BatchMode=yes bc250 true 2>/dev/null; then
      return 0
    fi
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
  ssh_q 'bash ~/bc250-40cu-unlock/scripts/cu_map.sh 2>&1 | grep -E "CUs|active" | head -1'
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
  scp -q "$LOCAL_REPO/benchmarks/bench-phase-c.py" bc250:/tmp/ || true
  scp -q "$LOCAL_REPO/benchmarks/results-longctx/bench-longctx-quality.py" bc250:~/phase-c-out/ || true
  scp -q "$LOCAL_REPO/scripts/toggle-ggml-hostmem.sh" bc250:/tmp/ || true
  scp -q "$LOCAL_REPO/scripts/toggle-cu-mode.sh" bc250:/tmp/ || true
  ssh_q 'chmod +x /tmp/toggle-ggml-hostmem.sh /tmp/toggle-cu-mode.sh 2>/dev/null; true'
}

# ============================================================
# T3 side B: GGML_VK_PREFER_HOST_MEMORY=on
# ============================================================
run_t3_side_b() {
  log "===== T3 side B: GGML_VK_PREFER_HOST_MEMORY=on ====="
  local AB_MODELS="qwen3.5-9b-ollama,gemma4-26b-q3,gemma4-latest"
  reboot_and_wait
  push_bench
  ssh_q '/tmp/toggle-ggml-hostmem.sh on'
  # verify
  ssh_q 'systemctl cat ollama | grep -i prefer_host_memory || echo "  NO env in unit"'
  ssh_q 'sleep 4; curl -s --max-time 5 http://localhost:11434/api/tags >/dev/null && echo OLLAMA-OK || echo OLLAMA-DOWN'
  ssh_q "cd /tmp && python3 /tmp/bench-phase-c.py --step perf --only $AB_MODELS --runs 3 > ~/phase-c-out/logs/t3-ggml-on.log 2>&1; echo RC=\$?"
  scp -q bc250:'~/phase-c-out/results/step-1-perf.json' "$OUT/t3-ggml-on-step-1-perf.json" || true
  scp -q bc250:'~/phase-c-out/logs/t3-ggml-on.log'      "$OUT/t3-ggml-on.log"            || true
  log "T3 side B DONE"
}

# ============================================================
# T4: 24 CU vs 40 CU pp512 A/B
# ============================================================
run_t4_cu_ab() {
  log "===== T4: 24 CU vs 40 CU pp512 A/B ====="

  # --- side A: 24 CU (mode=0) ---
  log "  side A: switching to bc250_cc_write_mode=0 then rebooting"
  push_bench
  ssh_q '/tmp/toggle-cu-mode.sh 0'
  reboot_and_wait
  push_bench   # /tmp wiped by reboot — re-push
  log "  verify CU count after side A reboot"
  ssh_q 'bash ~/bc250-40cu-unlock/scripts/cu_map.sh 2>&1 | grep -E "CUs|active"' || true
  ssh_q 'cd /tmp && python3 /tmp/bench-phase-c.py --step perf --runs 3 > ~/phase-c-out/logs/t4-24cu.log 2>&1; echo RC=$?'
  scp -q bc250:'~/phase-c-out/results/step-1-perf.json' "$OUT/t4-24cu-step-1-perf.json" || true
  scp -q bc250:'~/phase-c-out/logs/t4-24cu.log'         "$OUT/t4-24cu.log"             || true

  # --- side B: 40 CU (mode=3) ---
  log "  side B: switching to bc250_cc_write_mode=3 then rebooting"
  ssh_q '/tmp/toggle-cu-mode.sh 3'
  reboot_and_wait
  push_bench   # /tmp wiped
  log "  verify CU count after side B reboot"
  ssh_q 'bash ~/bc250-40cu-unlock/scripts/cu_map.sh 2>&1 | grep -E "CUs|active"' || true
  ssh_q 'cd /tmp && python3 /tmp/bench-phase-c.py --step perf --runs 3 > ~/phase-c-out/logs/t4-40cu.log 2>&1; echo RC=$?'
  scp -q bc250:'~/phase-c-out/results/step-1-perf.json' "$OUT/t4-40cu-step-1-perf.json" || true
  scp -q bc250:'~/phase-c-out/logs/t4-40cu.log'         "$OUT/t4-40cu.log"             || true

  log "T4 DONE -- box is back in 40 CU mode"
}

# ---------- main ----------
ONLY="${1:-all}"
log "phase-d phase-2 RESUME  only=$ONLY  out=$OUT"
case "$ONLY" in
  t3b) run_t3_side_b ;;
  t4)  run_t4_cu_ab ;;
  all) run_t3_side_b; run_t4_cu_ab ;;
  *) log "usage: $0 [t3b|t4|all]"; exit 2 ;;
esac
log "phase-d phase-2 RESUME DONE"
