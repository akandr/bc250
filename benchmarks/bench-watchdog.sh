#!/bin/bash
# bench-watchdog.sh — keep overnight launcher alive.
# Runs in a sleep loop. If the launcher OR bench-overnight.py is no longer
# running AND we haven't recorded a clean finish, re-launch (resumable).
# Caps restart attempts to avoid infinite crash loops.

LOG=/tmp/bench-watchdog.log
LAUNCHER=/tmp/overnight-launcher.sh
LAUNCHER_LOG=/tmp/overnight-launcher.log
RESULTS=/tmp/bench-overnight-results.json
ATTEMPTS_FILE=/tmp/bench-watchdog.attempts

ts() { date +"[%H:%M:%S]"; }
log() { echo "$(ts) $*" >>"$LOG"; }

[[ -f "$ATTEMPTS_FILE" ]] || echo 0 >"$ATTEMPTS_FILE"
MAX_RESTARTS=8

log "=== watchdog start (PID $$) ==="

while true; do
    sleep 60

    # Did the launcher record a clean finish? Then we're done forever.
    if [[ -f "$LAUNCHER_LOG" ]] && grep -q "overnight-launcher done" "$LAUNCHER_LOG"; then
        log "launcher reported clean finish, watchdog exiting"
        exit 0
    fi
    # Or did bench-overnight finish cleanly?
    if [[ -f "$LAUNCHER_LOG" ]] && grep -q "bench-overnight finished cleanly" "$LAUNCHER_LOG"; then
        log "bench reported clean finish, watchdog exiting"
        exit 0
    fi

    # Is something still running?
    if pgrep -f "overnight-launcher.sh" >/dev/null \
       || pgrep -f "bench-overnight.py" >/dev/null \
       || pgrep -f "llama-bench" >/dev/null; then
        continue
    fi

    # Nothing running. Need to restart unless we've exhausted attempts.
    n=$(cat "$ATTEMPTS_FILE" 2>/dev/null || echo 0)
    if [[ $n -ge $MAX_RESTARTS ]]; then
        log "max restarts ($MAX_RESTARTS) hit; giving up"
        exit 2
    fi
    n=$((n+1))
    echo $n >"$ATTEMPTS_FILE"
    log "no live processes detected; restart attempt #$n"

    # Brief grace period in case a process is between subprocess.run calls
    sleep 10
    if pgrep -f "overnight-launcher.sh|bench-overnight.py|llama-bench" >/dev/null; then
        log "process reappeared during grace; not restarting"
        continue
    fi

    # Re-launch
    nohup "$LAUNCHER" >/dev/null 2>&1 </dev/null &
    log "relaunched launcher (pid $!)"
    sleep 30
done
