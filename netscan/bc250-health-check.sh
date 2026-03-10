#!/usr/bin/env bash
# bc250-health-check.sh — Periodic health check & auto-recovery for bc250 services
# Deployed to /opt/netscan/bc250-health-check.sh
# Runs via systemd timer every 15 minutes
#
# Checks:
#   1. Ollama is responding to /api/tags
#   2. OpenClaw gateway is running and responsive
#   3. Queue-runner is alive and not stuck
#   4. Ollama model isn't stuck (loaded but not generating for too long)
#   5. Network connectivity
#
# Recovery actions:
#   - Restart ollama if unresponsive
#   - Restart gateway if unresponsive
#   - Restart queue-runner if stuck
#   - Clear stuck ollama models

set -euo pipefail

LOG_TAG="bc250-health"
STATE_DIR="/tmp/bc250-health"
OLLAMA_URL="http://localhost:11434"
GATEWAY_PORT=18789

mkdir -p "$STATE_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    logger -t "$LOG_TAG" "$*" 2>/dev/null || true
}

# ─── Check 1: Network connectivity ──────────────────────────────────────
check_network() {
    local gw
    gw=$(ip route show default 2>/dev/null | awk '/default/ {print $3; exit}')
    if [[ -z "$gw" ]]; then
        log "WARN: No default gateway found"
        return 1
    fi
    if ! ping -c 1 -W 3 "$gw" &>/dev/null; then
        log "WARN: Default gateway $gw unreachable"
        return 1
    fi
    return 0
}

# ─── Check 2: Ollama health ─────────────────────────────────────────────
check_ollama() {
    if ! curl -sf --max-time 10 "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
        local fail_count
        fail_count=$(cat "$STATE_DIR/ollama-fails" 2>/dev/null || echo 0)
        fail_count=$((fail_count + 1))
        echo "$fail_count" > "$STATE_DIR/ollama-fails"

        if [[ $fail_count -ge 2 ]]; then
            log "CRITICAL: Ollama unresponsive ($fail_count checks). Restarting..."
            sudo systemctl restart ollama.service
            echo 0 > "$STATE_DIR/ollama-fails"
            sleep 10
        else
            log "WARN: Ollama unresponsive (attempt $fail_count/2)"
        fi
        return 1
    fi
    echo 0 > "$STATE_DIR/ollama-fails"
    return 0
}

# ─── Check 3: Stuck model detection ─────────────────────────────────────
check_ollama_stuck() {
    local ps_data
    ps_data=$(curl -sf --max-time 5 "$OLLAMA_URL/api/ps" 2>/dev/null) || return 0

    local model_count
    model_count=$(echo "$ps_data" | python3 -c "
import sys, json
data = json.load(sys.stdin)
models = data.get('models', [])
print(len(models))
" 2>/dev/null || echo 0)

    if [[ "$model_count" == "0" ]]; then
        rm -f "$STATE_DIR/model-stuck-since"
        return 0
    fi

    # Check if model has been loaded continuously for too long (>45 min)
    # This catches the case where gateway cron keeps retrying and holding model
    local expires
    expires=$(echo "$ps_data" | python3 -c "
import sys, json
from datetime import datetime, timezone
data = json.load(sys.stdin)
models = data.get('models', [])
if models:
    exp = models[0].get('expires_at', '')
    try:
        dt = datetime.fromisoformat(exp)
        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        remaining = (dt - now).total_seconds()
        print(f'{remaining:.0f}')
    except:
        print('unknown')
else:
    print('none')
" 2>/dev/null || echo "unknown")

    # If model consistently has >25 min remaining (keep_alive=30m), something keeps refreshing it
    if [[ "$expires" =~ ^[0-9]+$ ]] && [[ "$expires" -gt 1500 ]]; then
        if [[ -f "$STATE_DIR/model-stuck-since" ]]; then
            local stuck_since
            stuck_since=$(cat "$STATE_DIR/model-stuck-since")
            local now_epoch
            now_epoch=$(date +%s)
            local stuck_duration=$(( now_epoch - stuck_since ))

            if [[ $stuck_duration -gt 2700 ]]; then  # 45 minutes
                log "WARN: Model appears stuck (held for ${stuck_duration}s). Checking gateway..."
                # Check if gateway cron is in a retry loop
                local user_uid
                user_uid=$(id -u akandr 2>/dev/null || echo 1000)
                local jctl_cmd="journalctl --user"
                local sys_cmd="systemctl --user"
                if [[ "$(id -u)" != "$user_uid" ]]; then
                    jctl_cmd="journalctl --machine=akandr@.host --user"
                    sys_cmd="systemctl --machine=akandr@.host --user"
                fi
                local gw_errors
                gw_errors=$($jctl_cmd -u openclaw-gateway.service --since "30 min ago" --no-pager 2>/dev/null | grep -c "fetch failed" || true)
                if [[ "$gw_errors" -gt 3 ]]; then
                    log "CRITICAL: Gateway in fetch-failed loop ($gw_errors errors in 30min). Restarting gateway..."
                    $sys_cmd restart openclaw-gateway.service
                    rm -f "$STATE_DIR/model-stuck-since"
                    sleep 5
                fi
            fi
        else
            date +%s > "$STATE_DIR/model-stuck-since"
        fi
    else
        rm -f "$STATE_DIR/model-stuck-since"
    fi
    return 0
}

# ─── Check 4: Gateway health ────────────────────────────────────────────
check_gateway() {
    local user_uid
    user_uid=$(id -u akandr 2>/dev/null || echo 1000)
    local sys_cmd="systemctl --user"
    local jctl_cmd="journalctl --user"

    # If running as root or different user, target akandr's user session
    if [[ "$(id -u)" != "$user_uid" ]]; then
        sys_cmd="systemctl --machine=akandr@.host --user"
        jctl_cmd="journalctl --machine=akandr@.host --user"
    fi

    if ! $sys_cmd is-active openclaw-gateway.service &>/dev/null; then
        log "WARN: Gateway not active. Starting..."
        $sys_cmd start openclaw-gateway.service
        return 1
    fi

    # Check for persistent fetch-failed errors (deadlock pattern)
    local recent_errors
    recent_errors=$($jctl_cmd -u openclaw-gateway.service --since "30 min ago" --no-pager 2>/dev/null | grep -c "fetch failed" || true)
    if [[ "$recent_errors" -gt 6 ]]; then
        local fail_count
        fail_count=$(cat "$STATE_DIR/gateway-fails" 2>/dev/null || echo 0)
        fail_count=$((fail_count + 1))
        echo "$fail_count" > "$STATE_DIR/gateway-fails"

        if [[ $fail_count -ge 2 ]]; then
            log "CRITICAL: Gateway stuck in fetch-failed loop ($recent_errors errors). Restarting..."
            $sys_cmd restart openclaw-gateway.service
            echo 0 > "$STATE_DIR/gateway-fails"
        else
            log "WARN: Gateway has $recent_errors fetch errors (monitoring)"
        fi
        return 1
    fi
    echo 0 > "$STATE_DIR/gateway-fails"
    return 0
}

# ─── Check 5: Queue runner health ───────────────────────────────────────
check_queue_runner() {
    if ! systemctl is-active queue-runner.service &>/dev/null; then
        log "WARN: Queue runner not active. Starting..."
        sudo systemctl start queue-runner.service
        return 1
    fi

    # Check if queue-runner is stuck in a busy-wait loop for too long
    local busy_count
    busy_count=$(journalctl -u queue-runner --since "60 min ago" --no-pager 2>/dev/null | grep -c "GPU busy" || true)
    if [[ "$busy_count" -gt 10 ]]; then
        # GPU busy for 10+ checks (50+ min) — likely a deadlock with gateway
        local fail_count
        fail_count=$(cat "$STATE_DIR/qrunner-fails" 2>/dev/null || echo 0)
        fail_count=$((fail_count + 1))
        echo "$fail_count" > "$STATE_DIR/qrunner-fails"

        if [[ $fail_count -ge 3 ]]; then
            log "CRITICAL: Queue runner stuck in GPU-busy loop (${busy_count} retries in 60min). Restarting both services..."
            # Restart gateway first to clear any stuck cron runs
            systemctl --user restart openclaw-gateway.service
            sleep 5
            sudo systemctl restart queue-runner.service
            echo 0 > "$STATE_DIR/qrunner-fails"
        else
            log "WARN: Queue runner GPU-busy loop ($busy_count retries, check $fail_count/3)"
        fi
        return 1
    fi
    echo 0 > "$STATE_DIR/qrunner-fails"
    return 0
}

# ─── Main ────────────────────────────────────────────────────────────────
main() {
    local issues=0

    if ! check_network; then
        log "Network issue detected — skipping service checks"
        exit 0
    fi

    check_ollama || issues=$((issues + 1))
    check_ollama_stuck || issues=$((issues + 1))
    check_gateway || issues=$((issues + 1))
    check_queue_runner || issues=$((issues + 1))

    if [[ $issues -eq 0 ]]; then
        log "OK: All services healthy"
    else
        log "ISSUES: $issues check(s) flagged"
    fi

    # Refresh extended health data for dashboard (at most once per hour)
    local ext_stamp="$STATE_DIR/extended-health-last"
    local now
    now=$(date +%s)
    local last=0
    [[ -f "$ext_stamp" ]] && last=$(cat "$ext_stamp")
    if (( now - last > 3600 )); then
        python3 /opt/netscan/bc250-extended-health.py --quick >/dev/null 2>&1 || true
        echo "$now" > "$ext_stamp"
    fi
}

main "$@"
