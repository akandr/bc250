#!/usr/bin/env bash
# Re-run JUST T4 (24 vs 40 CU A/B) after fixing toggle-cu-mode.sh to
# rebuild the initramfs (amdgpu is loaded from initramfs on this box).
# Overwrites t4-24cu-* and t4-40cu-* JSONs from the broken earlier run.
set -uo pipefail
LOCAL_REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${LOCAL_REPO}/benchmarks/results-phase-c/40cu-rerun/phase2"
LOG="${OUT}/phase2-t4-rerun.log"
mkdir -p "$OUT"
exec > >(tee -a "$LOG") 2>&1

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
  scp -q "$LOCAL_REPO/scripts/toggle-cu-mode.sh"   bc250:/tmp/ || true
  ssh_q 'chmod +x /tmp/toggle-cu-mode.sh 2>/dev/null; true'
}

# Subset chosen to be reliable at 24 CU (full 10-model run wedges the box).
T4_MODELS="qwen3.5-9b-ollama,granite-4.0-h-tiny,gemma4-26b-q3,gemma4-latest"
log "===== T4 RERUN: 24 vs 40 CU pp512 A/B (with dracut fix) ====="
log "       subset = $T4_MODELS"

# Pre-check we're starting from a clean state (whatever mode is fine).
ensure_box_up

# --- side A: 24 CU (mode=0) ---
log "  side A: switching to bc250_cc_write_mode=0 (rebuilds initramfs)"
push_bench
ssh_q '/tmp/toggle-cu-mode.sh 0'
ssh_q 'cat /etc/modprobe.d/bc250-40cu.conf; ls -la /boot/initramfs-$(uname -r).img'
reboot_and_wait
push_bench   # /tmp wiped by reboot
log "  verify CU count after side A reboot (EXPECT ~24)"
ssh_q 'cat /sys/module/amdgpu/parameters/bc250_cc_write_mode 2>/dev/null || echo "no parameter"'
ssh_q 'bash ~/bc250-40cu-unlock/scripts/cu_map.sh 2>&1 | grep -E "CUs|active|harvested"'
  ssh_q "cd /tmp && python3 /tmp/bench-phase-c.py --step perf --only $T4_MODELS --runs 3 > ~/phase-c-out/logs/t4-24cu.log 2>&1; echo RC=\$?"
scp -q bc250:'~/phase-c-out/results/step-1-perf.json' "$OUT/t4-24cu-step-1-perf.json" || true
scp -q bc250:'~/phase-c-out/logs/t4-24cu.log'         "$OUT/t4-24cu.log"             || true

# --- side B: 40 CU (mode=3) ---
log "  side B: switching to bc250_cc_write_mode=3 (rebuilds initramfs)"
push_bench
ssh_q '/tmp/toggle-cu-mode.sh 3'
reboot_and_wait
push_bench
log "  verify CU count after side B reboot (EXPECT 40)"
ssh_q 'cat /sys/module/amdgpu/parameters/bc250_cc_write_mode 2>/dev/null || echo "no parameter"'
ssh_q 'bash ~/bc250-40cu-unlock/scripts/cu_map.sh 2>&1 | grep -E "CUs|active|harvested"'
  ssh_q "cd /tmp && python3 /tmp/bench-phase-c.py --step perf --only $T4_MODELS --runs 3 > ~/phase-c-out/logs/t4-40cu.log 2>&1; echo RC=\$?"
scp -q bc250:'~/phase-c-out/results/step-1-perf.json' "$OUT/t4-40cu-step-1-perf.json" || true
scp -q bc250:'~/phase-c-out/logs/t4-40cu.log'         "$OUT/t4-40cu.log"             || true

log "T4 RERUN DONE -- box is back in 40 CU mode"
