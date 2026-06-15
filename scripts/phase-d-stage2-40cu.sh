#!/usr/bin/env bash
# phase-d-stage2-40cu.sh -- Mac-side overnight orchestrator that fills the
# long-context (Step-2 filled-context) gap on the 40-CU configuration.
#
# Context: the original 40-CU Step-2 attempt OOM-killed the host because a
# single llama worker reached ~12 GiB on the ~14 GiB UMA budget.  Since then
# bench-phase-c.py grew a pre-launch VM guard (BC250_VM_CEILING_GIB=11) that
# REFUSES any cell whose projected weights+KV exceeds the ceiling instead of
# letting Linux OOM-kill init.  This orchestrator adds more safety layers:
#   1. relies on oberon-governor for the clock cap (1500 MHz) + auto-throttle;
#      this APU rejects manual force_performance_level / OD writes, so oberon
#      is the only working clock lever.  We verify it is active, not pin.
#   2. cooldown-gates every cell (run-phase-d-sweep.sh waits for edge<=72 C),
#   3. runs bc250-thermal-watchdog.sh as a last-resort cut-off ABOVE oberon's
#      throttle band (ABORT_C=100), and
#   4. escalates a stuck reboot to a bc250_reset smart-plug power cycle.
#
# It drives the board entirely over SSH (alias: bc250), launches the sweep
# under nohup on the board so it survives disconnects, polls partial results
# back to the repo, and reverts the board to 24-CU on completion.
#
# Run (detached) from the repo root on the Mac:
#   nohup bash scripts/phase-d-stage2-40cu.sh > /tmp/phase-d-stage2-orch.log 2>&1 < /dev/null &
#   tail -f /tmp/phase-d-stage2-orch.log
set -uo pipefail

# ----- config (override via env) -----
SSH_HOST="${SSH_HOST:-bc250}"
LOCAL_REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${OUT:-$LOCAL_REPO/benchmarks/results-phase-d-stage2}"
REMOTE_OUTDIR="${REMOTE_OUTDIR:-/tmp/phase-d-stage2}"
REMOTE_RESULTS="${REMOTE_RESULTS:-\$HOME/phase-c-out/results/step-2-ctx-quality.json}"
RUNS="${RUNS:-3}"
N_GEN="${N_GEN:-200}"
VM_CEILING_GIB="${VM_CEILING_GIB:-11.0}"
ABORT_C="${ABORT_C:-100}"              # last-resort net ABOVE oberon's ~1500MHz throttle band
POLL_MIN="${POLL_MIN:-15}"             # minutes between partial fetches
MAX_HOURS="${MAX_HOURS:-12}"           # safety cap on total poll time
REVERT_24CU="${REVERT_24CU:-1}"        # revert board to 24-CU when done

mkdir -p "$OUT"
LOG="$OUT/orchestrator.log"
exec > >(tee -a "$LOG") 2>&1

ts()  { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*"; }
ssh_q() { ssh -o ConnectTimeout=10 -o ServerAliveInterval=30 "$SSH_HOST" "$@"; }

wait_for_ssh() {
  local max="${1:-1500}" t=0
  while (( t < max )); do
    ssh -o ConnectTimeout=5 -o BatchMode=yes "$SSH_HOST" true 2>/dev/null && return 0
    sleep 5; t=$((t+5))
  done
  return 1
}

ensure_box_up() {
  wait_for_ssh 180 || { log "FATAL: $SSH_HOST unreachable"; return 1; }
  ssh_q 'curl -s --max-time 5 http://localhost:11434/api/tags >/dev/null \
           && echo OLLAMA-OK || echo OLLAMA-DOWN'
}

reboot_and_wait() {
  log "rebooting $SSH_HOST ..."
  ssh_q 'sudo -n systemctl reboot' || true
  sleep 30
  if wait_for_ssh 240; then sleep 5; return 0; fi
  # Soft reboot did not come back in time (this is exactly how the board hung
  # on last night's revert).  Escalate to a Home Assistant smart-plug power
  # cycle, which is remotely recoverable.
  log "soft reboot did not return in 240s -- escalating to bc250_reset power-cycle"
  if command -v bc250_reset >/dev/null 2>&1; then
    bc250_reset || log "WARN: bc250_reset returned nonzero"
  else
    log "WARN: bc250_reset not found on PATH; cannot hard power-cycle"
  fi
  sleep 30
  wait_for_ssh 1500 || { log "FATAL: box did not come back even after power-cycle"; return 1; }
  sleep 5
}

cu_count() {
  ssh_q 'bash ~/bc250-40cu-unlock/scripts/cu_map.sh 2>&1 | grep -iE "active|CUs|harvested" | head -2'
}

verify_governor() {
  # Clock control on this Cyan Skillfish APU is owned entirely by
  # oberon-governor (force_performance_level accepts only "auto"; the OD
  # interface is driven by oberon).  oberon caps sclk at 1500 MHz and
  # self-throttles when hot.  We do not pin clocks ourselves -- we just
  # confirm oberon is alive and capped, and (re)start it if not.
  log "verifying oberon-governor (1500 MHz cap)"
  ssh_q 'systemctl is-active oberon-governor >/dev/null 2>&1 || sudo -n systemctl restart oberon-governor
         sleep 2
         echo "  oberon=$(systemctl is-active oberon-governor) cap=$(awk "/max:/{print \$3; exit}" /etc/oberon-config.yaml 2>/dev/null)MHz"
         echo "  sclk: $(cat /sys/class/drm/card1/device/pp_dpm_sclk 2>/dev/null | tr "\n" " ")"'
}

push_scripts() {
  log "pushing harness + sweep + watchdog to $SSH_HOST"
  ssh_q "mkdir -p $REMOTE_OUTDIR"
  scp -q "$LOCAL_REPO/benchmarks/bench-phase-c.py"        "$SSH_HOST:/tmp/bench-phase-c.py"
  scp -q "$LOCAL_REPO/benchmarks/run-phase-d-sweep.sh"    "$SSH_HOST:/tmp/run-phase-d-sweep.sh"
  scp -q "$LOCAL_REPO/scripts/bc250-thermal-watchdog.sh"  "$SSH_HOST:/tmp/bc250-thermal-watchdog.sh"
  # The harness reads the GPU telemetry sampler from /tmp/gpu-overlay-sampler.sh;
  # if it is missing every cell silently records samples=0 (the 40-CU
  # operating-point telemetry gap seen in the first stage-2 attempt).
  scp -q "$LOCAL_REPO/benchmarks/gpu-overlay-sampler.sh"   "$SSH_HOST:/tmp/gpu-overlay-sampler.sh"
  ssh_q "chmod +x /tmp/run-phase-d-sweep.sh /tmp/bc250-thermal-watchdog.sh /tmp/bench-phase-c.py /tmp/gpu-overlay-sampler.sh"
}

start_watchdog() {
  log "starting thermal watchdog (ABORT_C=$ABORT_C)"
  ssh_q "rm -f $REMOTE_OUTDIR/THERMAL_ABORT $REMOTE_OUTDIR/SWEEP_DONE
         ABORT_C=$ABORT_C POLL=10 \
           FLAG_ABORT=$REMOTE_OUTDIR/THERMAL_ABORT \
           FLAG_DONE=$REMOTE_OUTDIR/SWEEP_DONE \
           nohup bash /tmp/bc250-thermal-watchdog.sh \
             > $REMOTE_OUTDIR/watchdog.log 2>&1 < /dev/null &
         echo watchdog-pid=\$!"
}

start_sweep() {
  log "launching filled-context sweep under nohup (RUNS=$RUNS, VM_CEILING=$VM_CEILING_GIB)"
  ssh_q "HARNESS=/tmp/bench-phase-c.py OUTDIR=$REMOTE_OUTDIR \
           RUNS=$RUNS N_GEN=$N_GEN \
           BC250_VM_CEILING_GIB=$VM_CEILING_GIB \
           nohup bash -c '
             bash /tmp/run-phase-d-sweep.sh;
             rc=\$?;
             echo \"sweep exit rc=\$rc\";
             touch $REMOTE_OUTDIR/SWEEP_DONE
           ' > $REMOTE_OUTDIR/sweep.log 2>&1 < /dev/null &
         echo sweep-pid=\$!"
}

fetch_partials() {
  scp -q "$SSH_HOST:$REMOTE_OUTDIR/sweep.log"     "$OUT/sweep.log"      2>/dev/null || true
  scp -q "$SSH_HOST:$REMOTE_OUTDIR/watchdog.log"  "$OUT/watchdog.log"   2>/dev/null || true
  ssh_q "cp -f $REMOTE_RESULTS $REMOTE_OUTDIR/step-2-ctx-quality.partial.json 2>/dev/null" || true
  scp -q "$SSH_HOST:$REMOTE_OUTDIR/step-2-ctx-quality.partial.json" \
         "$OUT/step-2-ctx-quality.40cu.json" 2>/dev/null || true
}

revert_24cu() {
  [[ "$REVERT_24CU" == "1" ]] || { log "leaving board in 40-CU (REVERT_24CU=0)"; return 0; }
  log "reverting board to 24-CU baseline mode"
  push_scripts >/dev/null 2>&1 || true
  scp -q "$LOCAL_REPO/scripts/toggle-cu-mode.sh" "$SSH_HOST:/tmp/toggle-cu-mode.sh" 2>/dev/null || true
  ssh_q "chmod +x /tmp/toggle-cu-mode.sh; /tmp/toggle-cu-mode.sh 0" || true
  reboot_and_wait || true
  log "post-revert CU state:"; cu_count || true
}

# ============================ main ============================
log "===== Phase-D Stage-2 (40-CU filled-context gap-fill) ====="
log "host=$SSH_HOST out=$OUT runs=$RUNS abort_c=$ABORT_C vm_ceiling=$VM_CEILING_GIB (clocks: oberon 1500MHz cap)"

ensure_box_up || exit 1

log "switching board to 40-CU mode (bc250_cc_write_mode=3)"
scp -q "$LOCAL_REPO/scripts/toggle-cu-mode.sh" "$SSH_HOST:/tmp/toggle-cu-mode.sh"
ssh_q "chmod +x /tmp/toggle-cu-mode.sh; /tmp/toggle-cu-mode.sh 3"
reboot_and_wait || exit 1

log "verifying 40 CU active:"
cu_count
mode=$(ssh_q 'cat /sys/module/amdgpu/parameters/bc250_cc_write_mode 2>/dev/null || echo ?')
log "bc250_cc_write_mode=$mode (expect 3)"
[[ "$mode" == "3" ]] || { log "FATAL: CU unlock did not take effect (mode=$mode)"; exit 1; }

verify_governor
push_scripts
start_watchdog
start_sweep

log "sweep running. polling every ${POLL_MIN} min (max ${MAX_HOURS}h)."
deadline=$(( $(date +%s) + MAX_HOURS*3600 ))
while :; do
  sleep $(( POLL_MIN*60 ))
  fetch_partials
  if ssh_q "test -f $REMOTE_OUTDIR/THERMAL_ABORT" 2>/dev/null; then
    log "!!! THERMAL ABORT detected -- sweep stopped by watchdog. Fetching final partials."
    fetch_partials
    break
  fi
  if ssh_q "test -f $REMOTE_OUTDIR/SWEEP_DONE" 2>/dev/null; then
    log "sweep completed. fetching final results."
    fetch_partials
    break
  fi
  if (( $(date +%s) > deadline )); then
    log "MAX_HOURS reached; stopping sweep."
    ssh_q "pkill -f run-phase-d-sweep.sh; pkill -f bench-phase-c.py" || true
    fetch_partials
    break
  fi
  cells=$(ssh_q "grep -c 'rc=' $REMOTE_OUTDIR/sweep.log 2>/dev/null" || echo 0)
  edge=$(ssh_q "sensors 2>/dev/null | awk '/edge:/{gsub(/[^0-9.]/,\"\",\$2);print int(\$2)}'" || echo '?')
  log "progress: cells_done=$cells edge=${edge}C"
done

# stop watchdog (sweep done flag also makes it self-exit)
ssh_q "pkill -f bc250-thermal-watchdog.sh" 2>/dev/null || true
fetch_partials
revert_24cu

log "===== Stage-2 done. Results: $OUT/step-2-ctx-quality.40cu.json ====="
log "sweep log: $OUT/sweep.log ; watchdog log: $OUT/watchdog.log"
