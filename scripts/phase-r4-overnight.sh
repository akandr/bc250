#!/usr/bin/env bash
# Phase R4 overnight chain (~7-8h budget):
#   1. extended kv-quant rerun: pulls in MODEL_KV_QUANT_EXTRA cells (32K-q4_0
#      for the vm_guard-skipped class). Merges into existing step-kv-quant.json.
#   2. 24-CU side: toggle bc250_cc_write_mode=0, reboot, run step-1 perf with
#      --merge --runs 3, pull JSON.
#   3. 40-CU side: toggle bc250_cc_write_mode=3, reboot, re-run perf as a
#      sanity cross-check (should match the canonical step-1-perf.json).
#
# Heartbeat at ~/phase-c-out/HEARTBEAT-OVERNIGHT.json. Result deltas pulled
# locally on completion via the trailing scp block (run by hand if the chain
# survives unattended).

set -uo pipefail

OUT_LOCAL="$(cd "$(dirname "$0")/.." && pwd)/benchmarks/results-phase-c-r2"
mkdir -p "$OUT_LOCAL/cu-paired/24cu" "$OUT_LOCAL/cu-paired/40cu" "$OUT_LOCAL/extended-kv"

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

# 24-CU paired models -- LC + ollama subset that we actually have on the box.
# Excludes broken/missing: qwen3.5-9b-q4km (broken GGUF), gemma4-26b-a4b-iq3m
# (file missing), qwen3.6-27b/glm-5.1/llama-3.4-8b (files missing), gemma4-26b-a4b-ollama (tag missing).
PAIRED_MODELS="qwen3.5-9b-ollama,deepseek-r1-14b,gpt-oss-20b-mxfp4,gpt-oss-20b-ollama,qwen3-coder-30b-iq2m,qwen3.5-35b-iq2m,qwen3.6-35b-iq2m,gemma4-latest,gemma4-26b-q3,granite-4.0-h-tiny"
PAIRED_RUNS="${PAIRED_RUNS:-3}"

ensure_box_up

# ---------- Phase 1: extended kv-quant at relaxed ceiling (no reboot) ----------
# vm_guard at 13.5 GiB rejected 32K-q4_0 cells for gpt-oss-20b/coder-30b/35b
# class. We bump the ceiling to 15.0 GiB to test whether the conservative
# safety margin was leaving real headroom on the table, or whether OOM is
# imminent. Cells either run cleanly (= ceiling was too conservative) or
# crash/skip with OOM (= 13.5 was correct). Either outcome is article data.
log "===== Phase 1: extended kv-quant @ ceiling=15.0 GiB ====="
push_bench
ssh_q "echo '{\"phase\":\"ext-kv-quant\",\"ceiling_gib\":15.0,\"started\":\"$(ts)\"}' > ~/phase-c-out/HEARTBEAT-OVERNIGHT.json"
ssh_q 'cp ~/phase-c-out/results/step-kv-quant.json ~/phase-c-out/results/step-kv-quant.canonical-13_5.json 2>/dev/null'
ssh_q 'cd ~/phase-c-out && setsid nohup env BC250_VM_CEILING_GIB=15.0 BC250_MEM_AVAIL_MIN_GIB=1.0 \
       python3 bench-phase-c.py --step kv-quant --merge --runs 2 \
       > ~/phase-c-out/logs/r4-ext-kv.log 2>&1 < /dev/null & disown; echo "ext-kv pid=$!"'
log "waiting up to 3h for ext-kv-quant to finish (polled every 60s)"
deadline=$(( $(date +%s) + 10800 ))
while (( $(date +%s) < deadline )); do
  if ! ssh_q 'pgrep -af "bench-phase-c.py --step kv-quant" | grep -v grep' >/dev/null; then
    log "ext-kv-quant done"
    break
  fi
  sleep 60
done
scp -q "bc250:phase-c-out/results/step-kv-quant.json" "$OUT_LOCAL/extended-kv/step-kv-quant.json" || true
scp -q "bc250:phase-c-out/logs/r4-ext-kv.log"          "$OUT_LOCAL/extended-kv/run.log"          || true

# ---------- Phase 2: 24-CU side ----------
log "===== Phase 2: side A = 24 CU (bc250_cc_write_mode=0) ====="
ssh_q "echo '{\"phase\":\"24cu-toggle\",\"started\":\"$(ts)\"}' > ~/phase-c-out/HEARTBEAT-OVERNIGHT.json"
push_bench
ssh_q '/tmp/toggle-cu-mode.sh 0'
reboot_and_wait
push_bench
ssh_q 'cat /sys/module/amdgpu/parameters/bc250_cc_write_mode 2>/dev/null' \
  | tee "$OUT_LOCAL/cu-paired/24cu/cc_write_mode.txt"
ssh_q 'bash ~/bc250-40cu-unlock/scripts/cu_map.sh 2>&1 | grep -E "CUs|active|harvested"' \
  | tee "$OUT_LOCAL/cu-paired/24cu/cu_map.txt"
ssh_q "echo '{\"phase\":\"24cu-perf\",\"started\":\"$(ts)\"}' > ~/phase-c-out/HEARTBEAT-OVERNIGHT.json"
log "  starting step-1 perf @ 24 CU (--merge runs=$PAIRED_RUNS) for $PAIRED_MODELS"
# Capture step-1-perf.json BEFORE we run, so we can compare later if --merge mutates it.
ssh_q 'cp ~/phase-c-out/results/step-1-perf.json ~/phase-c-out/results/step-1-perf.40cu-canonical.json 2>/dev/null'
ssh_q "cd ~/phase-c-out && python3 bench-phase-c.py --step perf --only $PAIRED_MODELS --merge --runs $PAIRED_RUNS \
       > ~/phase-c-out/logs/r4-24cu.log 2>&1; echo RC=\$?" | tee -a "$OUT_LOCAL/cu-paired/24cu/run-rc.txt"
scp -q "bc250:phase-c-out/results/step-1-perf.json" "$OUT_LOCAL/cu-paired/24cu/step-1-perf.json" || true
scp -q "bc250:phase-c-out/logs/r4-24cu.log"          "$OUT_LOCAL/cu-paired/24cu/run.log"         || true

# ---------- Phase 3: restore 40 CU ----------
log "===== Phase 3: restore side B = 40 CU (bc250_cc_write_mode=3) ====="
ssh_q "echo '{\"phase\":\"40cu-toggle\",\"started\":\"$(ts)\"}' > ~/phase-c-out/HEARTBEAT-OVERNIGHT.json"
push_bench
ssh_q '/tmp/toggle-cu-mode.sh 3'
reboot_and_wait
push_bench
ssh_q 'cat /sys/module/amdgpu/parameters/bc250_cc_write_mode 2>/dev/null' \
  | tee "$OUT_LOCAL/cu-paired/40cu/cc_write_mode.txt"
ssh_q 'bash ~/bc250-40cu-unlock/scripts/cu_map.sh 2>&1 | grep -E "CUs|active|harvested"' \
  | tee "$OUT_LOCAL/cu-paired/40cu/cu_map.txt"
ssh_q "echo '{\"phase\":\"40cu-perf\",\"started\":\"$(ts)\"}' > ~/phase-c-out/HEARTBEAT-OVERNIGHT.json"
log "  cross-check step-1 perf @ 40 CU"
# Restore the canonical step-1-perf.json before this side so the cross-check
# overwrites cleanly without merging 24-CU rows in.
ssh_q 'cp ~/phase-c-out/results/step-1-perf.40cu-canonical.json ~/phase-c-out/results/step-1-perf.json 2>/dev/null'
ssh_q "cd ~/phase-c-out && python3 bench-phase-c.py --step perf --only $PAIRED_MODELS --merge --runs $PAIRED_RUNS \
       > ~/phase-c-out/logs/r4-40cu.log 2>&1; echo RC=\$?" | tee -a "$OUT_LOCAL/cu-paired/40cu/run-rc.txt"
scp -q "bc250:phase-c-out/results/step-1-perf.json" "$OUT_LOCAL/cu-paired/40cu/step-1-perf.json" || true
scp -q "bc250:phase-c-out/logs/r4-40cu.log"          "$OUT_LOCAL/cu-paired/40cu/run.log"         || true

ssh_q "echo '{\"phase\":\"all-done\",\"finished\":\"$(ts)\"}' > ~/phase-c-out/HEARTBEAT-OVERNIGHT.json"
log "===== R4 OVERNIGHT CHAIN COMPLETE ====="
log "  outputs:"
log "    $OUT_LOCAL/extended-kv/step-kv-quant.json"
log "    $OUT_LOCAL/cu-paired/24cu/step-1-perf.json"
log "    $OUT_LOCAL/cu-paired/40cu/step-1-perf.json"
