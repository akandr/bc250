#!/usr/bin/env bash
# Phase W1 chained driver — completes the rebench dataset.
# Budget: ~14h.
#
# Phases:
#   W1.0  push patched harness, snapshot existing JSONs.
#   W1.1  side A = 24 CU. step perf with VM_CEILING=15.0 and slow-model
#         timeouts. Re-fills cells skipped/timed-out at default ceiling.
#         --merge into cu-paired/24cu/step-1-perf.json.
#   W1.2  side B = 40 CU. step perf with VM_CEILING=15.0. --merge into
#         cu-paired/40cu/step-1-perf.json.
#   W1.3  step kv-quant @ ceiling 15.0 — fills 32K q8_0 + 32K f16 cells
#         for the 20B+ class via the expanded MODEL_KV_QUANT_EXTRA.
#   W1.4  step cold expansion — adds granite, qwen3.5-9b-ollama, etc.
#   W1.5  step ctx-easy / qual-32k refill at ceiling=15.0 to refresh
#         cells that previously failed.
#
# Heartbeat: ~/phase-c-out/HEARTBEAT-W1.json
# Pulls into benchmarks/results-phase-c-r2/cu-paired/{24cu,40cu}/ and
# extended-kv/, plus refreshing the canonical step-cold.json and
# step-2-ctx-quality.json snapshots.

set -uo pipefail

OUT_LOCAL="$(cd "$(dirname "$0")/.." && pwd)/benchmarks/results-phase-c-r2"
LOG_DIR="$(cd "$(dirname "$0")/.." && pwd)/logs"
mkdir -p "$OUT_LOCAL/cu-paired/24cu" "$OUT_LOCAL/cu-paired/40cu" "$OUT_LOCAL/extended-kv" "$LOG_DIR"

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

# Models that previously failed at 24-CU side: ceiling-blocked or timed-out.
# Plus the 4 models already-ok in the clean paired set are RE-included so
# every model gets matching reboot-paired runs (consistency).
PAIRED_MODELS_24="deepseek-r1-14b,qwen3.5-9b-ollama,gemma4-latest,granite-4.0-h-tiny,gpt-oss-20b-mxfp4,qwen3-coder-30b-iq2m,qwen3.5-35b-iq2m,qwen3.6-35b-iq2m,gpt-oss-20b-ollama,gemma4-26b-q3"
# 40-CU side: same set so we have proper paired data for all 10.
PAIRED_MODELS_40="$PAIRED_MODELS_24"
PAIRED_RUNS="${PAIRED_RUNS:-3}"

# Models for ext-kv-quant 32K f16/q8_0 fill.
KVQ_MODELS="deepseek-r1-14b,gpt-oss-20b-mxfp4,qwen3-coder-30b-iq2m,qwen3.5-35b-iq2m,qwen3.6-35b-iq2m"

# Cold-start expanded set: includes the new ollama additions.
COLD_MODELS="qwen3.5-9b-q4km,deepseek-r1-14b,gpt-oss-20b-mxfp4,qwen3-coder-30b-iq2m,qwen3.5-35b-iq2m,granite-4.0-h-tiny,gemma4-latest,qwen3.5-9b-ollama,gpt-oss-20b-ollama"

ensure_box_up
log "===== PHASE W1 START ====="
heartbeat "w1-init"

# Snapshot existing JSONs before mutation.
log "snapshotting existing canonical JSONs on box"
ssh_q '
  d=~/phase-c-out/results
  ts="$(date +%s)"
  for f in step-1-perf.json step-kv-quant.json step-cold.json step-2-ctx-quality.json step-qual-32k.json; do
    [ -f "$d/$f" ] && cp "$d/$f" "$d/${f%.json}.preW1-${ts}.json"
  done
  ls -la "$d"/*.preW1-*.json 2>/dev/null | tail -10
'

# ----------------- W1.1: 24 CU side at ceiling=15.0 -----------------
log "===== W1.1: side A = 24 CU (bc250_cc_write_mode=0) @ ceiling=15.0 ====="
heartbeat "w1.1-24cu-toggle"
push_bench
ssh_q '/tmp/toggle-cu-mode.sh 0'
reboot_and_wait
push_bench
ssh_q 'cat /sys/module/amdgpu/parameters/bc250_cc_write_mode 2>/dev/null' \
  | tee "$OUT_LOCAL/cu-paired/24cu/cc_write_mode.txt"
ssh_q 'bash ~/bc250-40cu-unlock/scripts/cu_map.sh 2>&1 | grep -E "CUs|active|harvested"' \
  | tee "$OUT_LOCAL/cu-paired/24cu/cu_map.txt"

# Restore the prior 24cu step-1-perf.json so --merge keeps the 4 already-ok
# entries and re-runs the ones we name in --only.
ssh_q 'cp ~/phase-c-out/results/step-1-perf.json ~/phase-c-out/results/step-1-perf.40cu-canonical-W1.json 2>/dev/null'
scp -q "$OUT_LOCAL/cu-paired/24cu/step-1-perf.json" bc250:phase-c-out/results/step-1-perf.json 2>/dev/null || true

heartbeat "w1.1-24cu-perf"
log "  step perf @ 24 CU ceiling=15.0 runs=$PAIRED_RUNS only=$PAIRED_MODELS_24"
ssh_q "cd ~/phase-c-out && BC250_VM_CEILING_GIB=15.0 BC250_MEM_AVAIL_MIN_GIB=1.0 \
       python3 bench-phase-c.py --step perf --only $PAIRED_MODELS_24 --merge --runs $PAIRED_RUNS \
       > ~/phase-c-out/logs/w1-24cu.log 2>&1; echo RC=\$?" \
  | tee -a "$OUT_LOCAL/cu-paired/24cu/run-rc.txt"
scp -q "bc250:phase-c-out/results/step-1-perf.json" "$OUT_LOCAL/cu-paired/24cu/step-1-perf.json" || true
scp -q "bc250:phase-c-out/logs/w1-24cu.log"          "$OUT_LOCAL/cu-paired/24cu/run.log"          || true

# ----------------- W1.2: 40 CU side at ceiling=15.0 -----------------
log "===== W1.2: side B = 40 CU (bc250_cc_write_mode=3) @ ceiling=15.0 ====="
heartbeat "w1.2-40cu-toggle"
push_bench
ssh_q '/tmp/toggle-cu-mode.sh 3'
reboot_and_wait
push_bench
ssh_q 'cat /sys/module/amdgpu/parameters/bc250_cc_write_mode 2>/dev/null' \
  | tee "$OUT_LOCAL/cu-paired/40cu/cc_write_mode.txt"
ssh_q 'bash ~/bc250-40cu-unlock/scripts/cu_map.sh 2>&1 | grep -E "CUs|active|harvested"' \
  | tee "$OUT_LOCAL/cu-paired/40cu/cu_map.txt"

# Restore canonical step-1-perf so 40cu side starts clean (drops 24cu rows).
ssh_q 'cp ~/phase-c-out/results/step-1-perf.40cu-canonical-W1.json ~/phase-c-out/results/step-1-perf.json 2>/dev/null'
scp -q "$OUT_LOCAL/cu-paired/40cu/step-1-perf.json" bc250:phase-c-out/results/step-1-perf.40cu-baseline.json 2>/dev/null || true

heartbeat "w1.2-40cu-perf"
log "  step perf @ 40 CU ceiling=15.0 runs=$PAIRED_RUNS only=$PAIRED_MODELS_40"
ssh_q "cd ~/phase-c-out && BC250_VM_CEILING_GIB=15.0 BC250_MEM_AVAIL_MIN_GIB=1.0 \
       python3 bench-phase-c.py --step perf --only $PAIRED_MODELS_40 --merge --runs $PAIRED_RUNS \
       > ~/phase-c-out/logs/w1-40cu.log 2>&1; echo RC=\$?" \
  | tee -a "$OUT_LOCAL/cu-paired/40cu/run-rc.txt"
scp -q "bc250:phase-c-out/results/step-1-perf.json" "$OUT_LOCAL/cu-paired/40cu/step-1-perf.json" || true
scp -q "bc250:phase-c-out/logs/w1-40cu.log"          "$OUT_LOCAL/cu-paired/40cu/run.log"          || true

# ----------------- W1.3: extended kv-quant 32K q8_0/f16 fill -----------------
log "===== W1.3: extended kv-quant @ ceiling=15.0 (32K q8_0/f16 fill) ====="
heartbeat "w1.3-ext-kv"
# Use the existing kv-quant.json on box as the merge target — already has
# 4K/16K cells; this run adds 32K q8_0/f16 cells via expanded MODEL_KV_QUANT_EXTRA.
ssh_q "cd ~/phase-c-out && BC250_VM_CEILING_GIB=15.0 BC250_MEM_AVAIL_MIN_GIB=1.0 \
       python3 bench-phase-c.py --step kv-quant --only $KVQ_MODELS --merge --runs 2 \
       > ~/phase-c-out/logs/w1-ext-kv.log 2>&1; echo RC=\$?"
scp -q "bc250:phase-c-out/results/step-kv-quant.json" "$OUT_LOCAL/extended-kv/step-kv-quant.json" || true
scp -q "bc250:phase-c-out/logs/w1-ext-kv.log"          "$OUT_LOCAL/extended-kv/run.log"           || true

# ----------------- W1.4: cold-start matrix expansion -----------------
log "===== W1.4: cold-start expansion ====="
heartbeat "w1.4-cold"
ssh_q "cd ~/phase-c-out && BC250_VM_CEILING_GIB=15.0 BC250_MEM_AVAIL_MIN_GIB=1.0 \
       python3 bench-phase-c.py --step cold --only $COLD_MODELS --merge --runs 3 \
       > ~/phase-c-out/logs/w1-cold.log 2>&1; echo RC=\$?"
scp -q "bc250:phase-c-out/results/step-cold.json" "$OUT_LOCAL/step-cold.json" || true
scp -q "bc250:phase-c-out/logs/w1-cold.log"        "$OUT_LOCAL/step-cold.w1.log" || true

# ----------------- W1.5: ctx-easy / qual-32k refill at ceiling=15.0 ---------
log "===== W1.5: ctx-easy + qual-32k refill ====="
heartbeat "w1.5-ctx-refill"
ssh_q "cd ~/phase-c-out && BC250_VM_CEILING_GIB=15.0 BC250_MEM_AVAIL_MIN_GIB=1.0 \
       python3 bench-phase-c.py --step ctx-easy --merge --runs 2 \
       > ~/phase-c-out/logs/w1-ctx-easy.log 2>&1; echo RC=\$?"
scp -q "bc250:phase-c-out/results/step-2-ctx-quality.json" "$OUT_LOCAL/step-2-ctx-quality.json" || true
scp -q "bc250:phase-c-out/logs/w1-ctx-easy.log"            "$OUT_LOCAL/step-2-ctx-quality.w1.log" || true

ssh_q "cd ~/phase-c-out && BC250_VM_CEILING_GIB=15.0 BC250_MEM_AVAIL_MIN_GIB=1.0 \
       python3 bench-phase-c.py --step qual-32k --merge --runs 2 \
       > ~/phase-c-out/logs/w1-qual32k.log 2>&1; echo RC=\$?"
scp -q "bc250:phase-c-out/results/step-qual-32k.json" "$OUT_LOCAL/step-qual-32k.json" || true
scp -q "bc250:phase-c-out/logs/w1-qual32k.log"          "$OUT_LOCAL/step-qual-32k.w1.log" || true

heartbeat "w1-all-done"
log "===== PHASE W1 COMPLETE ====="
log "  outputs:"
log "    $OUT_LOCAL/cu-paired/24cu/step-1-perf.json"
log "    $OUT_LOCAL/cu-paired/40cu/step-1-perf.json"
log "    $OUT_LOCAL/extended-kv/step-kv-quant.json"
log "    $OUT_LOCAL/step-cold.json"
log "    $OUT_LOCAL/step-2-ctx-quality.json"
log "    $OUT_LOCAL/step-qual-32k.json"
