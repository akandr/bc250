#!/usr/bin/env bash
# ollama-watchdog.sh — detect hung Ollama (GPU wedge / inference stall) and auto-restart
# Runs every 5 minutes via systemd timer.
#
# Detection logic:
#   1. Check Ollama is responding to /api/tags (basic health)
#   2. Look at last 15 minutes of journal for /api/chat requests
#   3. If ALL recent chat requests returned 500 (none 200), GPU is wedged
#   4. If there are stuck "aborting completion" messages, confirm the hang
#   5. Auto-restart Ollama to reset Vulkan/GPU state
#
# Also kills zombie queue-runner --list processes (known leak).

set -euo pipefail

LOG_TAG="ollama-watchdog"
LOOKBACK="15min"
MIN_FAILURES=3          # Need at least this many 500s to trigger
ZOMBIE_MAX_AGE=3600     # Kill --list zombies older than 1 hour

log() { logger -t "$LOG_TAG" "$*"; echo "[$(date '+%H:%M:%S')] $*"; }

# --- 1. Basic health: is Ollama responding at all? ---
if ! curl -sf --max-time 5 http://localhost:11434/api/tags >/dev/null 2>&1; then
    log "CRITICAL: Ollama not responding to /api/tags — restarting"
    systemctl restart ollama
    sleep 10
    if curl -sf --max-time 5 http://localhost:11434/api/tags >/dev/null 2>&1; then
        log "OK: Ollama recovered after restart"
    else
        log "FATAL: Ollama still not responding after restart"
    fi
    exit 0
fi

# --- 2. Check recent inference requests in journal ---
RECENT_CHAT=$(journalctl -u ollama --no-pager --since "-${LOOKBACK}" --output=short-iso 2>/dev/null \
    | grep 'POST.*"/api/chat"' || true)

if [ -z "$RECENT_CHAT" ]; then
    # No chat requests at all — Ollama is idle, that's fine
    log "OK: No inference requests in last ${LOOKBACK} — idle"
    # Still check for zombies
else
    COUNT_200=$(echo "$RECENT_CHAT" | grep -c " 200 " || true)
    COUNT_500=$(echo "$RECENT_CHAT" | grep -c " 500 " || true)

    if [ "$COUNT_500" -ge "$MIN_FAILURES" ] && [ "$COUNT_200" -eq 0 ]; then
        # All recent requests failed — GPU is likely wedged
        ABORT_COUNT=$(journalctl -u ollama --no-pager --since "-${LOOKBACK}" --output=short-iso 2>/dev/null \
            | grep -c "aborting completion request" || true)

        log "ALERT: GPU wedge detected — ${COUNT_500} failures, 0 successes, ${ABORT_COUNT} aborts in last ${LOOKBACK}"
        log "Restarting Ollama to reset Vulkan/GPU state..."

        systemctl restart ollama
        sleep 15

        # Verify recovery with a quick inference test
        TEST_RESULT=$(curl -sf --max-time 60 http://localhost:11434/api/chat \
            -d '{"model":"qwen3:14b","messages":[{"role":"user","content":"Say OK"}],"stream":false}' 2>&1 || echo "FAIL")

        if echo "$TEST_RESULT" | grep -q '"content"'; then
            log "OK: Ollama recovered — inference working after restart"
            # Also restart queue-runner so it starts fresh
            systemctl restart queue-runner
            log "OK: Queue-runner restarted for fresh cycle"
        else
            log "FATAL: Ollama inference still failing after restart — manual intervention needed"
        fi
        exit 0
    else
        log "OK: Inference healthy — ${COUNT_200} successes, ${COUNT_500} failures in last ${LOOKBACK}"
    fi
fi

# --- 3. Kill zombie queue-runner --list processes ---
ZOMBIES=$(ps -eo pid,etimes,args 2>/dev/null \
    | grep "queue-runner.py --list" \
    | grep -v grep \
    | awk -v max="$ZOMBIE_MAX_AGE" '$2 > max {print $1}' || true)

if [ -n "$ZOMBIES" ]; then
    ZOMBIE_COUNT=$(echo "$ZOMBIES" | wc -w)
    log "Killing ${ZOMBIE_COUNT} zombie queue-runner --list process(es): ${ZOMBIES}"
    echo "$ZOMBIES" | xargs kill 2>/dev/null || true
fi

exit 0
