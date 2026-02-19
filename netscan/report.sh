#!/bin/bash
# report.sh — Morning network diff + health report, sent via Signal
# Runs at 8 AM via cron
# Location on bc250: /opt/netscan/report.sh

set -euo pipefail

DATA_DIR="/opt/netscan/data"
SIGNAL_RPC="http://127.0.0.1:8080/api/v1/rpc"
RECIPIENT="+48503326388"
ACCOUNT="+48532825716"

TODAY=$(date +%Y%m%d)
SCAN_TODAY="$DATA_DIR/scan-${TODAY}.json"
HEALTH_TODAY="$DATA_DIR/health-${TODAY}.json"

# Find yesterday's scan (or most recent previous)
PREV_SCAN=$(ls -t "$DATA_DIR"/scan-*.json 2>/dev/null | grep -v "$TODAY" | head -1 || true)

if [ ! -f "$SCAN_TODAY" ]; then
    echo "No scan data for today. Run scan.sh first."
    exit 1
fi

# ── Generate report ──
REPORT=$(python3 - "$SCAN_TODAY" "$PREV_SCAN" "$HEALTH_TODAY" << 'PYEOF'
import json, sys
from datetime import datetime

scan_file = sys.argv[1]
prev_file = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else ""
health_file = sys.argv[3] if len(sys.argv) > 3 else ""

with open(scan_file) as f:
    today = json.load(f)

today_hosts = today["hosts"]

lines = []
lines.append(f"🌅 Morning Report — {datetime.now().strftime('%A, %b %d')}")
lines.append("")

# ── Network changes ──
if prev_file:
    try:
        with open(prev_file) as f:
            prev = json.load(f)
        prev_hosts = prev["hosts"]

        new_ips = set(today_hosts.keys()) - set(prev_hosts.keys())
        gone_ips = set(prev_hosts.keys()) - set(today_hosts.keys())

        # Check for MAC changes (same IP, different device)
        changed = []
        for ip in set(today_hosts.keys()) & set(prev_hosts.keys()):
            if today_hosts[ip]["mac"] != prev_hosts[ip]["mac"] and today_hosts[ip]["mac"] and prev_hosts[ip]["mac"]:
                changed.append(ip)

        has_changes = new_ips or gone_ips or changed

        if has_changes:
            lines.append("📡 Network Changes:")
            if new_ips:
                for ip in sorted(new_ips):
                    h = today_hosts[ip]
                    name = h.get("hostname") or h.get("vendor") or "unknown"
                    mac = h.get("mac", "")
                    lines.append(f"  🟢 NEW: {ip} — {name} ({mac})")
            if gone_ips:
                for ip in sorted(gone_ips):
                    h = prev_hosts[ip]
                    name = h.get("hostname") or h.get("vendor") or "unknown"
                    lines.append(f"  🔴 GONE: {ip} — {name}")
            if changed:
                for ip in sorted(changed):
                    old_mac = prev_hosts[ip]["mac"]
                    new_mac = today_hosts[ip]["mac"]
                    lines.append(f"  ⚠️ CHANGED MAC: {ip} — {old_mac} → {new_mac}")
            lines.append("")
        else:
            lines.append(f"📡 Network: No changes ({len(today_hosts)} hosts)")
            lines.append("")

        # Count by vendor
        vendors = {}
        for h in today_hosts.values():
            v = h.get("vendor") or "Unknown"
            vendors[v] = vendors.get(v, 0) + 1
        top = sorted(vendors.items(), key=lambda x: -x[1])[:5]
        vendor_str = ", ".join(f"{v}:{n}" for v, n in top)
        lines.append(f"  Devices: {len(today_hosts)} total ({vendor_str})")
        lines.append("")

    except Exception as e:
        lines.append(f"📡 Network: {len(today_hosts)} hosts (no previous scan to compare)")
        lines.append("")
else:
    lines.append(f"📡 Network: {len(today_hosts)} hosts (first scan, no baseline yet)")
    lines.append("")

# ── System health ──
if health_file:
    try:
        with open(health_file) as f:
            h = json.load(f)

        lines.append("🖥️ System Health:")
        lines.append(f"  {h.get('uptime', '?')}")

        mem_avail = h.get("mem_available_mb", 0)
        mem_total = h.get("mem_total_mb", 1)
        mem_pct = round((1 - mem_avail / mem_total) * 100)
        swap = h.get("swap_used_mb", 0)
        lines.append(f"  RAM: {mem_pct}% used ({mem_avail}MB free), swap: {swap}MB")

        if h.get("disk_pct"):
            lines.append(f"  Disk: {h['disk_pct']} used ({h.get('disk_avail', '?')} free)")

        temps = []
        if h.get("cpu_temp"): temps.append(f"CPU {h['cpu_temp']}°C")
        if h.get("gpu_temp"): temps.append(f"GPU {h['gpu_temp']}°C")
        if h.get("nvme_temp"): temps.append(f"NVMe {h['nvme_temp']}°C")
        if temps:
            lines.append(f"  Temps: {', '.join(temps)}")
        if h.get("gpu_power_w"):
            lines.append(f"  GPU power: {h['gpu_power_w']}")

        if h.get("nvme_wear_pct"):
            lines.append(f"  NVMe wear: {h['nvme_wear_pct']}")

        # Alerts
        alerts = []
        if h.get("oom_kills_24h", 0) > 0:
            alerts.append(f"⚠️ {h['oom_kills_24h']} OOM kills in last 24h!")
        if int(h.get("failed_units", "0")) > 0:
            alerts.append(f"⚠️ {h['failed_units']} failed systemd units")
        if mem_pct > 90:
            alerts.append(f"⚠️ RAM critical: {mem_pct}% used")
        if h.get("disk_pct", "0%").rstrip("%").isdigit() and int(h["disk_pct"].rstrip("%")) > 85:
            alerts.append(f"⚠️ Disk getting full: {h['disk_pct']}")
        cpu_temp_val = float(h.get("cpu_temp", "0") or "0")
        if cpu_temp_val > 85:
            alerts.append(f"⚠️ CPU hot: {h['cpu_temp']}°C")

        # Services
        svcs = []
        for key in ("svc_ollama", "svc_openclaw", "svc_signal-cli"):
            name = key.replace("svc_", "")
            status = h.get(key, "unknown")
            if status != "active":
                alerts.append(f"🔴 {name}: {status}")
            else:
                svcs.append(name)

        if svcs:
            lines.append(f"  Services OK: {', '.join(svcs)}")

        if alerts:
            lines.append("")
            lines.append("🚨 Alerts:")
            for a in alerts:
                lines.append(f"  {a}")
        else:
            lines.append("  ✅ All clear — no alerts")

        lines.append("")
    except Exception as e:
        lines.append(f"  (health data error: {e})")
        lines.append("")

# Footer
lines.append("— Clawd 🦞 (automated scan)")

print("\n".join(lines))
PYEOF
)

if [ -z "$REPORT" ]; then
    echo "Empty report, skipping send."
    exit 1
fi

echo "$REPORT"
echo "---"

# ── Send via Signal ──
# Escape the report for JSON
REPORT_JSON=$(python3 -c "import json,sys; print(json.dumps(sys.stdin.read()))" <<< "$REPORT")

PAYLOAD=$(cat <<EOF
{
  "jsonrpc": "2.0",
  "method": "send",
  "params": {
    "account": "$ACCOUNT",
    "recipient": ["$RECIPIENT"],
    "message": $REPORT_JSON
  },
  "id": "netscan-$(date +%s)"
}
EOF
)

RESPONSE=$(curl -sf -X POST "$SIGNAL_RPC" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" 2>&1) || { echo "ERROR: Signal send failed: $RESPONSE"; exit 1; }

echo "Signal: $RESPONSE"

if echo "$RESPONSE" | grep -q '"error"'; then
    echo "ERROR: Signal RPC error"
    exit 1
fi

echo "[$(date)] Report sent!"

# ── Cleanup old data (keep 30 days) ──
find "$DATA_DIR" -name "*.json" -mtime +30 -delete 2>/dev/null || true
