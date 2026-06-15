#!/usr/bin/env bash
# Phase W2 driver — gemma probe + multi-needle + filled-context paired CU.
# Uses sentinel-file polling so SSH channel lifetime is decoupled from
# bench duration (W1's hard-learnt lesson).
#
# ETA: A=0.25h, B=2h, C=4h → ~6.5h total.

set -uo pipefail

OUT_LOCAL="$(cd "$(dirname "$0")/.." && pwd)/benchmarks/results-phase-c-r2"
LOG_DIR="$(cd "$(dirname "$0")/.." && pwd)/logs"
mkdir -p "$OUT_LOCAL/gemma-probe" \
         "$OUT_LOCAL/multi-needle" \
         "$OUT_LOCAL/cu-paired-filled/16k/24cu" "$OUT_LOCAL/cu-paired-filled/16k/40cu" \
         "$OUT_LOCAL/cu-paired-filled/32k/24cu" "$OUT_LOCAL/cu-paired-filled/32k/40cu" \
         "$LOG_DIR"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*"; }
ssh_q() { ssh -o ConnectTimeout=10 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 bc250 "$@"; }

wait_for_ssh() {
  local max="${1:-1500}" t=0
  while (( t < max )); do
    ssh -o ConnectTimeout=5 -o BatchMode=yes bc250 true 2>/dev/null && return 0
    sleep 5; t=$((t+5))
  done
  return 1
}

ensure_box_up() {
  if ! wait_for_ssh 240; then
    log "  soft wait failed, invoking bc250_reset"
    bc250_reset || true
    sleep 25
    wait_for_ssh 1500 || { log "FATAL: box did not come up"; return 1; }
  fi
  ssh_q 'bash ~/bc250-40cu-unlock/scripts/cu_map.sh 2>&1 | grep -E "CUs|active|harvested" | head -3' || true
}

reboot_and_wait() {
  log "rebooting bc250 ..."
  ssh_q 'sudo systemctl reboot' || true
  sleep 30
  ensure_box_up
}

push_assets() {
  log "pushing harness + scripts"
  scp -q "$OUT_LOCAL/../tmp/bench-phase-c.py" bc250:phase-c-out/bench-phase-c.py
  scp -q "$OUT_LOCAL/../tmp/gemma-probe.py"   bc250:phase-c-out/gemma-probe.py
  scp -q "$OUT_LOCAL/../../scripts/toggle-cu-mode.sh" bc250:/tmp/
  ssh_q 'chmod +x /tmp/toggle-cu-mode.sh ~/phase-c-out/bench-phase-c.py ~/phase-c-out/gemma-probe.py 2>/dev/null; true'
}

heartbeat() {
  ssh_q "echo '{\"phase\":\"$1\",\"ts\":\"$(ts)\"}' > ~/phase-c-out/HEARTBEAT-W2.json" || true
}

# Launch a remote bench detached from SSH; poll for sentinel.
# $1=tag $2=cmdline-string  $3 (optional) max-seconds (default 21600 = 6h)
run_remote_async() {
  local tag="$1" cmd="$2" max="${3:-21600}"
  local sentinel="/tmp/w2-${tag}.done"
  local logf="logs/w2-${tag}.log"
  log "  launching tag=${tag} (sentinel=${sentinel})"
  ssh_q "rm -f ${sentinel}; cd ~/phase-c-out && (nohup bash -c '${cmd}' > ${logf} 2>&1; echo \$? > ${sentinel}) >/dev/null 2>&1 < /dev/null & disown; echo launched-pid=\$!"
  local elapsed=0
  while (( elapsed < max )); do
    if ssh_q "test -f ${sentinel}" 2>/dev/null; then
      local rc
      rc="$(ssh_q "cat ${sentinel}" 2>/dev/null | tr -d '[:space:]')"
      log "  tag=${tag} done rc=${rc} after ${elapsed}s"
      if [[ "$rc" != "0" ]]; then
        log "  !! tag=${tag} non-zero rc — log tail:"
        ssh_q "tail -25 ${logf} 2>&1" || true
      fi
      return 0
    fi
    sleep 60; elapsed=$((elapsed+60))
    if (( elapsed % 600 == 0 )); then
      log "    ...still running (${elapsed}s) tag=${tag}"
      ssh_q "tail -3 ${logf} 2>/dev/null" || true
    fi
  done
  log "  TIMEOUT tag=${tag} after ${max}s — leaving bench running on box"
  return 1
}

# Run a perf step at filled context, redirecting bench-phase-c output to
# isolated file so we don't merge into step-2-ctx-quality.json.
# $1=tag $2=tier_int $3=models  $4=runs
run_filled_perf() {
  local tag="$1" tier="$2" models="$3" runs="$4"
  local out_remote="~/phase-c-out/results/step-filled-${tag}.json"
  log "  filled perf ${tag} tier=${tier} runs=${runs} models=${models}"
  # Use ctx-easy with tier override; redirect output via stash trick on box.
  local cmd="bash -c '
    SRC=~/phase-c-out/results/step-2-ctx-quality.json
    STASH=\${SRC}.stash-${tag}
    [ -f \$SRC ] && mv \$SRC \$STASH || true
    BC250_VM_CEILING_GIB=15.0 BC250_MEM_AVAIL_MIN_GIB=1.0 python3 bench-phase-c.py --step ctx-easy --only ${models} --tiers ${tier} --runs ${runs} --n-gen 100
    rc=\$?
    [ -f \$SRC ] && mv \$SRC ~/phase-c-out/results/step-filled-${tag}.json || true
    [ -f \$STASH ] && mv \$STASH \$SRC || true
    exit \$rc
  '"
  run_remote_async "filled-${tag}" "$cmd"
}

PAIRED_FILLED="deepseek-r1-14b,qwen3.5-9b-ollama,granite-4.0-h-tiny,gpt-oss-20b-mxfp4"
MULTI_NEEDLE_MODELS="gemma4-latest,granite-4.0-h-tiny,qwen3.5-9b-ollama,deepseek-r1-14b"

ensure_box_up
log "===== PHASE W2 START ====="
push_assets

# ----------- W2.0 snapshot -----------
heartbeat "w2.0-snapshot"
ssh_q 'cat /sys/module/amdgpu/parameters/bc250_cc_write_mode; bash ~/bc250-40cu-unlock/scripts/cu_map.sh 2>&1 | grep -E "CUs|active|harvested" | head -3'

# ----------- W2.A gemma probe (no reboot, fast) -----------
log "===== W2.A: gemma4-latest retrieval root-cause probe ====="
heartbeat "w2.a-gemma-probe"
run_remote_async "gemma-probe" "python3 gemma-probe.py" 3600
mkdir -p "$OUT_LOCAL/gemma-probe"
scp -q 'bc250:phase-c-out/results/gemma-probe/*.json' "$OUT_LOCAL/gemma-probe/" || true
scp -q 'bc250:phase-c-out/logs/w2-gemma-probe.log'    "$OUT_LOCAL/gemma-probe/run.log" || true

# ----------- W2.B multi-needle 32K/64K/128K -----------
log "===== W2.B: multi-needle (5 needles × {32K,64K,128K}) ====="
heartbeat "w2.b-multi-needle"
run_remote_async "multi-needle" \
  "BC250_VM_CEILING_GIB=15.0 BC250_MEM_AVAIL_MIN_GIB=1.0 python3 bench-phase-c.py --step multi-needle --only $MULTI_NEEDLE_MODELS --tiers 32768,65536,131072 --runs 2 --n-gen 300 --merge"
scp -q "bc250:phase-c-out/results/step-multi-needle.json" "$OUT_LOCAL/multi-needle/step-multi-needle.json" || true
scp -q "bc250:phase-c-out/logs/w2-multi-needle.log"        "$OUT_LOCAL/multi-needle/run.log" || true

# ----------- W2.C filled-context paired CU sweep -----------
# We test 16K and 32K filled context at 24-CU and 40-CU.
# Current state on box should be 40-CU (left from W1). Run 40-CU side first
# to avoid an unnecessary reboot, then toggle to 24-CU for paired side.

CURRENT_CU="$(ssh_q 'cat /sys/module/amdgpu/parameters/bc250_cc_write_mode' 2>/dev/null | tr -d '[:space:]')"
log "===== W2.C: filled-context paired CU (current cc_write_mode=$CURRENT_CU) ====="

if [[ "$CURRENT_CU" == "3" ]]; then
  SIDE_FIRST="40cu"; SIDE_SECOND="24cu"; TOGGLE_TO_SECOND="0"
else
  SIDE_FIRST="24cu"; SIDE_SECOND="40cu"; TOGGLE_TO_SECOND="3"
fi

# --- side 1 (no reboot) ---
heartbeat "w2.c-${SIDE_FIRST}-16k"
run_filled_perf "${SIDE_FIRST}-16k" 16384 "$PAIRED_FILLED" 3
scp -q "bc250:phase-c-out/results/step-filled-${SIDE_FIRST}-16k.json" \
    "$OUT_LOCAL/cu-paired-filled/16k/${SIDE_FIRST}/step-1-perf.json" || true
scp -q "bc250:phase-c-out/logs/w2-filled-${SIDE_FIRST}-16k.log" \
    "$OUT_LOCAL/cu-paired-filled/16k/${SIDE_FIRST}/run.log" || true

heartbeat "w2.c-${SIDE_FIRST}-32k"
run_filled_perf "${SIDE_FIRST}-32k" 32768 "$PAIRED_FILLED" 3
scp -q "bc250:phase-c-out/results/step-filled-${SIDE_FIRST}-32k.json" \
    "$OUT_LOCAL/cu-paired-filled/32k/${SIDE_FIRST}/step-1-perf.json" || true
scp -q "bc250:phase-c-out/logs/w2-filled-${SIDE_FIRST}-32k.log" \
    "$OUT_LOCAL/cu-paired-filled/32k/${SIDE_FIRST}/run.log" || true

# --- toggle + side 2 ---
log "===== W2.C: toggling to ${SIDE_SECOND} (cc_write_mode=${TOGGLE_TO_SECOND}) ====="
heartbeat "w2.c-toggle-${SIDE_SECOND}"
ssh_q "/tmp/toggle-cu-mode.sh ${TOGGLE_TO_SECOND}"
reboot_and_wait
push_assets
ssh_q 'cat /sys/module/amdgpu/parameters/bc250_cc_write_mode'

heartbeat "w2.c-${SIDE_SECOND}-16k"
run_filled_perf "${SIDE_SECOND}-16k" 16384 "$PAIRED_FILLED" 3
scp -q "bc250:phase-c-out/results/step-filled-${SIDE_SECOND}-16k.json" \
    "$OUT_LOCAL/cu-paired-filled/16k/${SIDE_SECOND}/step-1-perf.json" || true
scp -q "bc250:phase-c-out/logs/w2-filled-${SIDE_SECOND}-16k.log" \
    "$OUT_LOCAL/cu-paired-filled/16k/${SIDE_SECOND}/run.log" || true

heartbeat "w2.c-${SIDE_SECOND}-32k"
run_filled_perf "${SIDE_SECOND}-32k" 32768 "$PAIRED_FILLED" 3
scp -q "bc250:phase-c-out/results/step-filled-${SIDE_SECOND}-32k.json" \
    "$OUT_LOCAL/cu-paired-filled/32k/${SIDE_SECOND}/step-1-perf.json" || true
scp -q "bc250:phase-c-out/logs/w2-filled-${SIDE_SECOND}-32k.log" \
    "$OUT_LOCAL/cu-paired-filled/32k/${SIDE_SECOND}/run.log" || true

heartbeat "w2-done"
log "===== PHASE W2 COMPLETE ====="
