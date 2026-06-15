#!/usr/bin/env bash
# bench-watchdog-phase-ab.sh — poll every 60s; if launcher-phase-ab is gone
# AND the result files have not grown in N minutes, restart launcher.
# Max 5 restarts then exit.
set -u
LOG=/tmp/bench-watchdog-phase-ab.log
COUNTER=/tmp/bench-watchdog-phase-ab.attempts
MAX_RESTARTS=5
STALE_MIN=10

[[ -f "$COUNTER" ]] || echo 0 > "$COUNTER"
exec >> "$LOG" 2>&1

echo "[$(date +%H:%M:%S)] watchdog start pid=$$ max=$MAX_RESTARTS stale_min=$STALE_MIN"

mtime_min_ago() {
  local f="$1"
  [[ -f "$f" ]] || { echo 999; return; }
  local now mt
  now=$(date +%s); mt=$(stat -c %Y "$f" 2>/dev/null || echo 0)
  echo $(( (now - mt) / 60 ))
}

while true; do
  if pgrep -f launcher-phase-ab.sh >/dev/null; then
    sleep 60; continue
  fi
  # launcher not running — was it ever?
  if [[ ! -f /tmp/launcher-phase-ab.log ]]; then
    echo "[$(date +%H:%M:%S)] launcher.log missing; exiting"
    exit 0
  fi
  # if launcher finished cleanly, the log will have the "finished" line
  if tail -n 20 /tmp/launcher-phase-ab.log 2>/dev/null | grep -q "launcher-phase-ab finished"; then
    echo "[$(date +%H:%M:%S)] launcher finished cleanly; watchdog exiting"
    exit 0
  fi
  # check staleness on result jsons
  CSTALE=$(mtime_min_ago /tmp/bench-ceiling-llamacpp-results.json)
  BSTALE=$(mtime_min_ago /tmp/bench-build-bisect-results.json)
  MIN_STALE=$CSTALE; (( BSTALE < MIN_STALE )) && MIN_STALE=$BSTALE
  if (( MIN_STALE < STALE_MIN )); then
    echo "[$(date +%H:%M:%S)] launcher gone but recent file activity (${MIN_STALE} min) — wait"
    sleep 60; continue
  fi
  N=$(<"$COUNTER")
  if (( N >= MAX_RESTARTS )); then
    echo "[$(date +%H:%M:%S)] hit MAX_RESTARTS=$MAX_RESTARTS; giving up"
    exit 1
  fi
  N=$((N+1)); echo $N > "$COUNTER"
  echo "[$(date +%H:%M:%S)] restart attempt #$N (ceil-stale=${CSTALE}m bisect-stale=${BSTALE}m)"
  nohup /tmp/launcher-phase-ab.sh > /tmp/launcher-phase-ab.nohup 2>&1 < /dev/null &
  sleep 30
done
