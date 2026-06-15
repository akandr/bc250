#!/bin/bash
# overnight-launcher.sh — wait for downloads, validate models, run overnight bench.
# Designed to be nohup'd: tail /tmp/overnight-launcher.log to monitor.
set -u
exec >>/tmp/overnight-launcher.log 2>&1

ts() { date +"[%H:%M:%S]"; }
log() { echo "$(ts) $*"; }

EXPECTED_MODELS=(
    /opt/models/qwen3.6-35b-a3b-iq2m.gguf
    /opt/models/gpt-oss-20b-mxfp4.gguf
    /opt/models/granite-4.0-h-tiny-q4km.gguf
)

log "=== overnight-launcher start ==="

# 1) Wait for all downloads to finish (no .partial files left)
log "waiting for downloads to finish (max 4h)…"
deadline=$(( $(date +%s) + 14400 ))
while true; do
    pending=$(ls /opt/models/*.partial 2>/dev/null | wc -l)
    if [[ $pending -eq 0 ]]; then
        log "all downloads done"
        break
    fi
    if [[ $(date +%s) -gt $deadline ]]; then
        log "DEADLINE hit, $pending download(s) still pending — aborting"
        ls -la /opt/models/*.partial 2>/dev/null
        exit 2
    fi
    sleep 60
done

# 2) Validate every expected model file exists and is non-trivial size
for f in "${EXPECTED_MODELS[@]}"; do
    if [[ ! -f "$f" ]]; then
        log "MISSING after download: $f"
    else
        log "OK $(ls -la "$f")"
    fi
done

# 3) Stop services again in case anything was re-enabled
sudo systemctl stop ollama-watchdog.timer bc250-health.timer queue-runner.service signal-cli.service ollama.service 2>/dev/null
sudo pkill -f idle-think.sh 2>/dev/null
sleep 3
log "services state:"
systemctl is-active ollama queue-runner signal-cli ollama-watchdog.timer bc250-health.timer

# 4) Quick load test on each new model with both builds — failures get logged
#    but don't abort. The orchestrator will skip whatever broke.
log "=== quick load probes ==="
for build_path in /opt/llama.cpp/build/bin/llama-bench /opt/llama.cpp-b9165/build/bin/llama-bench; do
    for f in "${EXPECTED_MODELS[@]}"; do
        [[ -f "$f" ]] || continue
        log "probe $build_path  ←  $f"
        timeout 120 "$build_path" -m "$f" -ngl 99 -fa 1 -p 16 -n 4 -r 1 -o json 2>&1 \
            | tail -3 \
            | sed "s|^|    |"
        sleep 5
    done
done

# 5) Run the full overnight benchmark
log "=== launching bench-overnight.py ==="
rm -f /tmp/bench-overnight-results.json /tmp/bench-overnight.log /tmp/bench-overnight-state.json
python3 -u /tmp/bench-overnight.py
rc=$?
log "bench-overnight.py exited rc=$rc"

log "=== overnight-launcher done ==="
