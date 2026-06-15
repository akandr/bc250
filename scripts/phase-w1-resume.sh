#!/usr/bin/env bash
# Phase W1 resume driver — picks up after W1.1 stalled SSH.
# Runs W1.2, W1.3, W1.4, W1.5 with sentinel-file polling instead of
# holding the SSH channel open for the entire bench duration.

set -uo pipefail

OUT_LOCAL="$(cd "$(dirname "$0")/.." && pwd)/benchmarks/results-phase-c-r2"
LOG_DIR="$(cd "$(dirname "$0")/.." && pwd)/logs"
mkdir -p "$OUT_LOCAL/cu-paired/40cu" "$OUT_LOCAL/extended-kv" "$LOG_DIR"

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

push_bench() {
  log "pushing bench harness + toggles"
  scp -q "$OUT_LOCAL/../tmp/bench-phase-c.py" bc250:phase-c-out/bench-phase-c.py
  scp -q "$OUT_LOCAL/../../scripts/toggle-cu-mode.sh" bc250:/tmp/
  ssh_q 'chmod +x /tmp/toggle-cu-mode.sh ~/phase-c-out/bench-phase-c.py 2>/dev/null; true'
}

heartbeat() {
  local body="$1"
  ssh_q "echo '{\"phase\":\"$body\",\"ts\":\"$(ts)\"}' > ~/phase-c-out/HEARTBEAT-W1.json" || true
}

# Launch a bench command on the box detached from the SSH channel.
# Polls for a sentinel file. Args: $1=tag (label), $2=cmdline (string).
# Captures rc into ~/phase-c-out/.w1-<tag>.done and log into
# ~/phase-c-out/logs/w1-<tag>.log
run_remote_async() {
  local tag="$1" cmd="$2"
  local sentinel="/tmp/w1-${tag}.done"
  local logf="logs/w1-${tag}.log"   # relative to ~/phase-c-out (we cd there)
  log "  launching tag=${tag} (sentinel=${sentinel})"
  ssh_q "rm -f ${sentinel}; cd ~/phase-c-out && (nohup bash -c '${cmd}' > ${logf} 2>&1; echo \$? > ${sentinel}) >/dev/null 2>&1 < /dev/null & disown; echo launched-pid=\$!"
  # Poll
  local elapsed=0 max="${MAX_BENCH_S:-21600}"  # 6h cap
  while (( elapsed < max )); do
    if ssh_q "test -f ${sentinel}" 2>/dev/null; then
      local rc
      rc="$(ssh_q "cat ${sentinel}" 2>/dev/null | tr -d '[:space:]')"
      log "  tag=${tag} done rc=${rc} after ${elapsed}s"
      if [[ "$rc" != "0" ]]; then
        log "  !! tag=${tag} non-zero rc — dumping log tail:"
        ssh_q "tail -25 ${logf} 2>&1; echo --- ; ls -la ${logf} 2>&1" || true
      fi
      return 0
    fi
    sleep 60
    elapsed=$((elapsed+60))
    # Periodic progress line
    if (( elapsed % 600 == 0 )); then
      log "    ...still running (${elapsed}s) tag=${tag}"
      ssh_q "tail -3 ${logf} 2>/dev/null" || true
    fi
  done
  log "  TIMEOUT tag=${tag} after ${max}s — leaving bench running on box"
  return 1
}

PAIRED_MODELS_40="deepseek-r1-14b,qwen3.5-9b-ollama,gemma4-latest,granite-4.0-h-tiny,gpt-oss-20b-mxfp4,qwen3-coder-30b-iq2m,qwen3.5-35b-iq2m,qwen3.6-35b-iq2m,gpt-oss-20b-ollama,gemma4-26b-q3"
PAIRED_RUNS="${PAIRED_RUNS:-3}"
KVQ_MODELS="deepseek-r1-14b,gpt-oss-20b-mxfp4,qwen3-coder-30b-iq2m,qwen3.5-35b-iq2m,qwen3.6-35b-iq2m"
COLD_MODELS="qwen3.5-9b-q4km,deepseek-r1-14b,gpt-oss-20b-mxfp4,qwen3-coder-30b-iq2m,qwen3.5-35b-iq2m,granite-4.0-h-tiny,gemma4-latest,qwen3.5-9b-ollama,gpt-oss-20b-ollama"

ensure_box_up
log "===== PHASE W1 RESUME (W1.2-W1.5) ====="

# ----------------- W1.2: 40 CU side -----------------
log "===== W1.2: side B = 40 CU (bc250_cc_write_mode=3) @ ceiling=15.0 ====="
heartbeat "w1.2-40cu-toggle"
if [[ "${SKIP_W12_TOGGLE:-0}" != "1" ]]; then
  push_bench
  ssh_q '/tmp/toggle-cu-mode.sh 3'
  reboot_and_wait
  push_bench
else
  log "  SKIP_W12_TOGGLE=1, skipping toggle+reboot (assuming box already at 40CU)"
  push_bench
fi
ssh_q 'cat /sys/module/amdgpu/parameters/bc250_cc_write_mode 2>/dev/null' \
  | tee "$OUT_LOCAL/cu-paired/40cu/cc_write_mode.txt"
ssh_q 'bash ~/bc250-40cu-unlock/scripts/cu_map.sh 2>&1 | grep -E "CUs|active|harvested"' \
  | tee "$OUT_LOCAL/cu-paired/40cu/cu_map.txt"

# Restore canonical 40-CU step-1-perf so 40cu side runs --merge against
# pre-W1 state (NOT against the W1.1 24-CU data we just wrote).
ssh_q 'cp ~/phase-c-out/results/step-1-perf.40cu-canonical-W1.json ~/phase-c-out/results/step-1-perf.json 2>/dev/null && echo restored 40cu canonical || echo no canonical found'

heartbeat "w1.2-40cu-perf"
log "  step perf @ 40 CU ceiling=15.0 runs=$PAIRED_RUNS only=$PAIRED_MODELS_40"
run_remote_async "40cu-perf" \
  "BC250_VM_CEILING_GIB=15.0 BC250_MEM_AVAIL_MIN_GIB=1.0 python3 bench-phase-c.py --step perf --only $PAIRED_MODELS_40 --merge --runs $PAIRED_RUNS"
scp -q "bc250:phase-c-out/results/step-1-perf.json" "$OUT_LOCAL/cu-paired/40cu/step-1-perf.json" || true
scp -q "bc250:phase-c-out/logs/w1-40cu-perf.log"     "$OUT_LOCAL/cu-paired/40cu/run.log"          || true

# ----------------- W1.3: extended kv-quant -----------------
log "===== W1.3: extended kv-quant @ ceiling=15.0 (32K q8_0/f16 fill) ====="
heartbeat "w1.3-ext-kv"
run_remote_async "ext-kv" \
  "BC250_VM_CEILING_GIB=15.0 BC250_MEM_AVAIL_MIN_GIB=1.0 python3 bench-phase-c.py --step kv-quant --only $KVQ_MODELS --merge --runs 2"
scp -q "bc250:phase-c-out/results/step-kv-quant.json" "$OUT_LOCAL/extended-kv/step-kv-quant.json" || true
scp -q "bc250:phase-c-out/logs/w1-ext-kv.log"          "$OUT_LOCAL/extended-kv/run.log"           || true

# ----------------- W1.4: cold expansion -----------------
log "===== W1.4: cold-start expansion ====="
heartbeat "w1.4-cold"
run_remote_async "cold" \
  "BC250_VM_CEILING_GIB=15.0 BC250_MEM_AVAIL_MIN_GIB=1.0 python3 bench-phase-c.py --step cold --only $COLD_MODELS --merge --runs 3"
scp -q "bc250:phase-c-out/results/step-cold.json" "$OUT_LOCAL/step-cold.json" || true
scp -q "bc250:phase-c-out/logs/w1-cold.log"        "$OUT_LOCAL/step-cold.w1.log" || true

# ----------------- W1.5: ctx-easy + qual-32k refill -----------------
log "===== W1.5: ctx-easy refill ====="
heartbeat "w1.5-ctx-easy"
run_remote_async "ctx-easy" \
  "BC250_VM_CEILING_GIB=15.0 BC250_MEM_AVAIL_MIN_GIB=1.0 python3 bench-phase-c.py --step ctx-easy --merge --runs 2"
scp -q "bc250:phase-c-out/results/step-2-ctx-quality.json" "$OUT_LOCAL/step-2-ctx-quality.json" || true
scp -q "bc250:phase-c-out/logs/w1-ctx-easy.log"             "$OUT_LOCAL/step-2-ctx-quality.w1.log" || true

log "===== W1.5b: qual-32k refill ====="
heartbeat "w1.5-qual-32k"
run_remote_async "qual-32k" \
  "BC250_VM_CEILING_GIB=15.0 BC250_MEM_AVAIL_MIN_GIB=1.0 python3 bench-phase-c.py --step qual-32k --merge --runs 2"
scp -q "bc250:phase-c-out/results/step-qual-32k.json" "$OUT_LOCAL/step-qual-32k.json" || true
scp -q "bc250:phase-c-out/logs/w1-qual-32k.log"        "$OUT_LOCAL/step-qual-32k.w1.log" || true

heartbeat "w1-resume-done"
log "===== PHASE W1 RESUME COMPLETE ====="
