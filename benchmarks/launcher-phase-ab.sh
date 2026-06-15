#!/usr/bin/env bash
# launcher-phase-ab.sh — wait for b8600 + b9265 builds, then run
# bench-ceiling-llamacpp.py followed by bench-build-bisect.py.
#
# Designed to be nohup'd on bc250. Self-contained: if either bench python
# script dies, we log it and continue to the next; we DO NOT loop forever.
set -uo pipefail
LOG=/tmp/launcher-phase-ab.log
exec > >(tee -a "$LOG") 2>&1

echo "=== launcher-phase-ab starting $(date) ==="
echo "hostname: $(hostname)  pid: $$"

# 1. Belt-and-braces: make sure nothing competes for the GPU.
for s in ollama queue-runner signal-cli ollama-watchdog.timer bc250-health.timer; do
  sudo systemctl stop "$s" 2>/dev/null || true
done
sudo pkill -9 -f idle-think.sh 2>/dev/null || true
sleep 2

# 2. Wait up to 30 min for both intermediate builds to land.
WAIT=0
while true; do
  HAVE_8600=0; HAVE_9265=0
  [[ -x /opt/llama.cpp-b8600/build/bin/llama-bench ]] && HAVE_8600=1
  [[ -x /opt/llama.cpp-b9265/build/bin/llama-bench ]] && HAVE_9265=1
  if (( HAVE_8600 && HAVE_9265 )); then
    echo "[$(date +%H:%M:%S)] both builds ready"
    break
  fi
  if (( WAIT >= 1800 )); then
    echo "[$(date +%H:%M:%S)] WARNING: timeout waiting for builds; have b8600=$HAVE_8600 b9265=$HAVE_9265 — continuing with what we have"
    break
  fi
  echo "[$(date +%H:%M:%S)] waiting on builds  b8600=$HAVE_8600 b9265=$HAVE_9265  (waited ${WAIT}s)"
  sleep 30; WAIT=$((WAIT+30))
done

# 3. Validate ceiling-finder dep (b9265). If missing, skip Phase A.
if [[ -x /opt/llama.cpp-b9265/build/bin/llama-bench ]]; then
  echo "=== PHASE A: bench-ceiling-llamacpp $(date) ==="
  /usr/bin/python3 /tmp/bench-ceiling-llamacpp.py || echo "phase A exited rc=$?"
else
  echo "=== PHASE A SKIPPED: b9265 not available ==="
fi

# 4. Phase B: bisect. Will skip per-build cells when a build is missing.
echo "=== PHASE B: bench-build-bisect $(date) ==="
/usr/bin/python3 /tmp/bench-build-bisect.py || echo "phase B exited rc=$?"

echo "=== launcher-phase-ab finished $(date) ==="
