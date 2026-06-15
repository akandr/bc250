#!/usr/bin/env bash
# run-phase-d-sweep.sh — Phase D (40 CU unlock) thorough sweep orchestrator.
#
# Strategy:
#   * Outer loop: ctx tiers small -> large (4K, 8K, 16K, 24K, 32K, 49K, 64K).
#   * Inner loop: all benchmarked models.
#   * One bench-phase-c.py invocation per (tier, model) so an OOM only kills
#     that one cell, not the whole sweep.
#   * Between models: unload Ollama-resident models, drop pagecache, log
#     sensors snapshot.
#   * If a (model, tier) fails (rc != 0 or wall < 10 s or empty output),
#     the model is marked dead for all larger tiers (since memory pressure
#     only grows monotonically).
#   * Results are merged into a single per-tier JSON so a crash mid-tier
#     does not lose earlier cells.
#
# Run on the BC-250 host:
#   nohup bash /tmp/run-phase-d-sweep.sh > /tmp/phase-c/phase-d-sweep.log 2>&1 < /dev/null &

set -uo pipefail

HARNESS="${HARNESS:-/tmp/bench-phase-c.py}"
OUTDIR="${OUTDIR:-/tmp/phase-c}"
RUNS="${RUNS:-2}"
N_GEN="${N_GEN:-200}"

# Worth-testing tier ladder. 8K and 24K added between the original 4-16-32
# steps so we get a finer-grained scaling curve for the article.
TIERS=(4096 8192 16384 24576 32768 49152 65536)

# Order matters: smaller / safer first so we get coverage before risk.
MODELS=(
  granite-4.0-h-tiny
  qwen3.5-9b-ollama
  gemma4-latest
  deepseek-r1-14b
  gpt-oss-20b-mxfp4
  gemma4-26b-q3
  qwen3-coder-30b-iq2m
  qwen3.5-35b-iq2m
  qwen3.6-35b-iq2m
)
# qwen3.5-9b-q4km is intentionally omitted — its GGUF has
# rope.dimension_sections length 3 but llama.cpp b9265 expects 4, so it
# fails to load regardless of CU count.

# Cell wall-clock guard. ctx-quality tier 64K with n_gen=200 needs ~12 min
# on the slowest models; 25 min should never be hit on a healthy run.
PER_CELL_TIMEOUT=1500

# Models that died once -> do not retry at any larger tier.
declare -A DEAD

mkdir -p "$OUTDIR"

log() { printf '[%(%F %T)T] %s\n' -1 "$*"; }

snapshot_sensors() {
  if command -v sensors >/dev/null 2>&1; then
    sensors 2>/dev/null | awk '
      /^amdgpu-pci/ {in_g=1; next}
      in_g && /^$/  {in_g=0}
      in_g && /vddgfx|edge|PPT/ {print}
    ' | tr '\n' ' '
    echo
  fi
}

unload_ollama() {
  # Drop all currently-loaded ollama models (keep_alive=0 on each entry).
  local names
  names=$(curl -s http://127.0.0.1:11434/api/ps \
            | python3 -c 'import sys,json
try:
    d=json.load(sys.stdin)
    print(" ".join(m["model"] for m in d.get("models",[])))
except Exception:
    pass' 2>/dev/null)
  for m in $names; do
    curl -s -X POST http://127.0.0.1:11434/api/generate \
      -d "{\"model\":\"$m\",\"keep_alive\":0,\"prompt\":\"\"}" >/dev/null 2>&1 || true
  done
}

cleanup_between_cells() {
  unload_ollama
  # Drop pagecache (do NOT drop dentries/inodes; that hurts perf without
  # freeing significant memory in our scenario).
  sync
  echo 1 | sudo -n tee /proc/sys/vm/drop_caches >/dev/null 2>&1 || true
  sleep 2
}

# Cooldown gate: 40 CU long-context prefill drives the passively-cooled board
# into oberon's throttle band (~90-93 C).  Starting each cell hot means oberon
# throttles mid-cell and contaminates the throughput number.  Wait (with the
# GPU idle, after unload) until edge drops to COOLDOWN_C, or COOLDOWN_MAX_S.
COOLDOWN_C="${COOLDOWN_C:-72}"
COOLDOWN_MAX_S="${COOLDOWN_MAX_S:-240}"
edge_c() {
  sensors 2>/dev/null | awk '/edge:/ { gsub(/[^0-9.]/,"",$2); printf "%d", $2+0; f=1 } END{ if(!f) print -1 }'
}
wait_for_cool() {
  local t=0 e
  e=$(edge_c)
  while (( e >= COOLDOWN_C && t < COOLDOWN_MAX_S )); do
    sleep 10; t=$((t+10)); e=$(edge_c)
  done
  log "  cooldown: edge=${e}C after ${t}s (target<=${COOLDOWN_C}C)"
}

restart_ollama_hard() {
  # Heavier reset between large-context cells to be safe.
  log "  hard-restart ollama"
  sudo -n systemctl restart ollama 2>/dev/null || true
  # Wait until the API is ready again.
  for _ in $(seq 1 30); do
    if curl -s --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null; then
      break
    fi
    sleep 1
  done
}

run_cell() {
  local tier=$1 model=$2
  local out_json="$OUTDIR/phase-d-ctx-quality.json"
  local cell_log="$OUTDIR/cell.${model}.${tier}.log"
  local t0 t1 rc wall

  log "=== tier=$tier model=$model ==="
  snapshot_sensors | sed 's/^/  sensors: /'

  cleanup_between_cells
  wait_for_cool

  t0=$(date +%s)
  timeout --kill-after=60 "$PER_CELL_TIMEOUT" \
    python3 "$HARNESS" \
      --step ctx-quality \
      --only "$model" \
      --tiers "$tier" \
      --runs "$RUNS" \
      --n-gen "$N_GEN" \
      --merge \
    > "$cell_log" 2>&1
  rc=$?
  t1=$(date +%s)
  wall=$((t1 - t0))

  log "  rc=$rc wall=${wall}s"
  tail -3 "$cell_log" | sed 's/^/  /'

  # Heuristics for "this model is effectively broken at this size":
  #   * rc nonzero (segfault, timeout, OOM-kill of the python script itself)
  #   * wall < 10 s (model failed to load every run)
  #   * grep shows "status=fail" in every run line for this cell
  if [[ $rc -ne 0 || $wall -lt 10 ]]; then
    log "  -> marking $model dead (rc=$rc wall=${wall}s)"
    DEAD[$model]=1
    restart_ollama_hard
    return
  fi
  if grep -q "status=ok" "$cell_log"; then
    :  # at least one run ok, keep the model alive
  else
    if grep -q "status=fail" "$cell_log"; then
      log "  -> all runs failed for $model at $tier; marking dead"
      DEAD[$model]=1
      restart_ollama_hard
    fi
  fi
}

log "Phase D sweep start. tiers=${TIERS[*]} models=${MODELS[*]}"
log "host=$(uname -n) kernel=$(uname -r) cu_param=$(cat /sys/module/amdgpu/parameters/bc250_cc_write_mode 2>/dev/null || echo ?)"
log "sclk_states=$(cat /sys/class/drm/card1/device/pp_dpm_sclk 2>/dev/null | tr -d '\n')"
log "oberon=$(systemctl is-active oberon-governor 2>/dev/null) cap=$(awk '/max:/{print $3; exit}' /etc/oberon-config.yaml 2>/dev/null)MHz cooldown<=${COOLDOWN_C}C(max${COOLDOWN_MAX_S}s)"

for tier in "${TIERS[@]}"; do
  log "###### TIER $tier ######"
  for model in "${MODELS[@]}"; do
    if [[ -n "${DEAD[$model]:-}" ]]; then
      log "skip $model (dead at smaller tier)"
      continue
    fi
    run_cell "$tier" "$model"
  done
  log "###### TIER $tier complete ######"
  snapshot_sensors | sed 's/^/end-of-tier sensors: /'
done

log "ALL DONE"
log "dead models: ${!DEAD[*]}"
