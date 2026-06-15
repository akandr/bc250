#!/usr/bin/env bash
# bc250-thermal-watchdog.sh -- board-side thermal safety net for the
# 40-CU filled-context sweep.  Runs concurrently with run-phase-d-sweep.sh.
#
# The primary thermal protection on this board is the oberon-governor
# (purpose-built Cyan Skillfish governor): it caps sclk at 1500 MHz and
# self-throttles below that when the GPU overheats.  This watchdog is only a
# LAST-RESORT net for a genuine runaway oberon cannot contain -- it sits well
# above oberon's throttle point so it does not prematurely kill a sweep that
# oberon is already pacing safely.  On breach it kills the running sweep + any
# in-flight llama/ollama inference (stopping load is the fastest, most
# reliable way to shed heat -- far more effective than poking the locked-down
# clock sysfs, which rejects manual writes on this APU) and writes a flag file
# so the orchestrator stops cleanly.
#
# NOTE: this APU's force_performance_level accepts only "auto" and the OD
# interface is driven exclusively by oberon, so there is no reliable userspace
# "drop the clock" lever; we rely on killing the load instead.
#
# Usage (launched by the orchestrator under nohup):
#   ABORT_C=100 POLL=10 nohup bash /tmp/bc250-thermal-watchdog.sh \
#       > /tmp/phase-d-stage2/watchdog.log 2>&1 < /dev/null &
set -uo pipefail

ABORT_C="${ABORT_C:-100}"         # edge-temp hard ceiling (deg C); above oberon throttle
WARN_C="${WARN_C:-93}"            # log a warning above this (oberon throttle band)
POLL="${POLL:-10}"               # seconds between samples
FLAG_ABORT="${FLAG_ABORT:-/tmp/phase-d-stage2/THERMAL_ABORT}"
FLAG_DONE="${FLAG_DONE:-/tmp/phase-d-stage2/SWEEP_DONE}"

log() { printf '[%(%F %T)T] watchdog: %s\n' -1 "$*"; }

read_edge_c() {
  # Parse the amdgpu 'edge' temperature from lm-sensors; integer deg C.
  sensors 2>/dev/null | awk '
    /edge:/ { gsub(/[^0-9.]/,"",$2); printf "%d", $2+0; found=1 }
    END { if (!found) print -1 }'
}

drop_clock() {
  # This APU rejects manual force_performance_level / pp_dpm_sclk writes, so
  # there is no direct clock lever here.  Best-effort: bounce oberon so it
  # re-asserts its 1500 MHz cap from a clean state.  The real heat-shed is
  # kill_sweep() removing the GPU load.
  sudo -n systemctl restart oberon-governor >/dev/null 2>&1 || true
}

kill_sweep() {
  pkill -f run-phase-d-sweep.sh   2>/dev/null || true
  pkill -f bench-phase-c.py       2>/dev/null || true
  pkill -f llama-completion       2>/dev/null || true
  pkill -f llama-cli              2>/dev/null || true
  # Unload any resident Ollama model so memory + GPU go idle.
  curl -s http://127.0.0.1:11434/api/ps 2>/dev/null \
    | python3 -c 'import sys,json
try:
    for m in json.load(sys.stdin).get("models",[]): print(m["model"])
except Exception: pass' 2>/dev/null \
    | while read -r m; do
        [[ -n "$m" ]] && curl -s -X POST http://127.0.0.1:11434/api/generate \
          -d "{\"model\":\"$m\",\"keep_alive\":0,\"prompt\":\"\"}" >/dev/null 2>&1 || true
      done
}

log "start ABORT_C=${ABORT_C} WARN_C=${WARN_C} POLL=${POLL}s"
while :; do
  [[ -f "$FLAG_DONE" ]] && { log "sweep done flag seen; exiting"; exit 0; }
  edge=$(read_edge_c)
  if [[ "$edge" -ge "$ABORT_C" ]] 2>/dev/null; then
    log "THERMAL ABORT: edge=${edge}C >= ${ABORT_C}C -- dropping clock + killing sweep"
    drop_clock
    kill_sweep
    mkdir -p "$(dirname "$FLAG_ABORT")"
    echo "edge=${edge}C at $(date '+%F %T')" > "$FLAG_ABORT"
    exit 1
  elif [[ "$edge" -ge "$WARN_C" ]] 2>/dev/null; then
    log "WARN: edge=${edge}C (>= ${WARN_C}C)"
  fi
  sleep "$POLL"
done
