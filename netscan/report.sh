#!/bin/bash
# report.sh — Morning Signal report: short network diff + link to dashboard
# Runs at 8 AM via cron. Location on bc250: /opt/netscan/report.sh
set -euo pipefail

DATA_DIR="/opt/netscan/data"
TODAY=$(date +%Y%m%d)
YESTERDAY=$(date -d "yesterday" +%Y%m%d)
SCAN_TODAY="$DATA_DIR/scan-${TODAY}.json"
SCAN_YEST="$DATA_DIR/scan-${YESTERDAY}.json"
HEALTH="$DATA_DIR/health-${TODAY}.json"
DASHBOARD_URL="http://192.168.3.151:8888/"
SIGNAL_SEND="/opt/openclaw/scripts/signal-send.sh"

if [[ ! -f "$SCAN_TODAY" ]]; then
    echo "No scan for today ($TODAY). Aborting."
    exit 1
fi

MESSAGE=$(python3 - "$SCAN_TODAY" "$SCAN_YEST" "$HEALTH" "$DASHBOARD_URL" << 'PYEOF'
import json, sys, os
from datetime import datetime

scan_today, scan_yest, health_file, url = sys.argv[1:5]

today = json.load(open(scan_today))
hosts_today = set(today["hosts"].keys())

parts = []
parts.append(f"📡 NETSCAN {datetime.now().strftime('%d %b %Y')}")
parts.append(f"Hosts: {today['host_count']}")

total_ports = sum(len(h["ports"]) for h in today["hosts"].values())
if total_ports: parts.append(f"Open ports: {total_ports}")

# Device type summary
types = {}
for h in today["hosts"].values():
    dt = h.get("device_type","unknown")
    types[dt] = types.get(dt,0)+1
type_str = ", ".join(f"{v}×{k}" for k,v in sorted(types.items(), key=lambda x:-x[1]) if v>0)
if type_str: parts.append(f"Types: {type_str}")

# Diff with yesterday
if os.path.exists(scan_yest):
    yest = json.load(open(scan_yest))
    hosts_yest = set(yest["hosts"].keys())
    new_hosts = hosts_today - hosts_yest
    gone_hosts = hosts_yest - hosts_today
    if new_hosts:
        new_list = []
        for ip in sorted(new_hosts):
            h = today["hosts"][ip]
            tag = h.get("device_type","?")
            new_list.append(f"  {ip} ({tag})")
        parts.append("🟢 NEW:\n" + "\n".join(new_list[:8]))
        if len(new_list)>8: parts.append(f"  ...+{len(new_list)-8} more")
    if gone_hosts:
        gone_list = []
        for ip in sorted(gone_hosts):
            h = yest["hosts"].get(ip,{})
            tag = h.get("device_type","?")
            gone_list.append(f"  {ip} ({tag})")
        parts.append("🔴 GONE:\n" + "\n".join(gone_list[:8]))
        if len(gone_list)>8: parts.append(f"  ...+{len(gone_list)-8} more")
    if not new_hosts and not gone_hosts:
        parts.append("No changes since yesterday")
else:
    parts.append("(first scan, no diff)")

# Health summary
if os.path.exists(health_file):
    hl = json.load(open(health_file))
    temps = []
    if hl.get("cpu_temp"): temps.append(f"CPU:{hl['cpu_temp']}°")
    if hl.get("gpu_temp"): temps.append(f"GPU:{hl['gpu_temp']}°")
    if temps: parts.append("🌡 " + " ".join(temps))
    if hl.get("mem_available_mb") and hl.get("mem_total_mb"):
        pct = round((1 - hl["mem_available_mb"]/hl["mem_total_mb"])*100)
        parts.append(f"💾 RAM:{pct}% | Disk:{hl.get('disk_pct','?')}")
    oom = hl.get("oom_kills_24h",0)
    if oom > 0: parts.append(f"⚠️ OOM kills: {oom}")
    dead = [k.replace("svc_","") for k,v in hl.items() if k.startswith("svc_") and v not in ("active","running")]
    if dead: parts.append(f"⚠️ Down: {', '.join(dead)}")

parts.append(f"\n🔗 {url}")

print("\n".join(parts))
PYEOF
)

echo "--- Signal message ---"
echo "$MESSAGE"
echo "---"

# Send via signal-cli JSON-RPC (daemon mode — direct CLI conflicts with running daemon)
RECIPIENT="+48503326388"
BOT_NUMBER="+48532825716"
SIGNAL_RPC="http://127.0.0.1:8080/api/v1/rpc"

PAYLOAD=$(python3 -c "
import json, sys
msg = sys.stdin.read()
print(json.dumps({
    'jsonrpc': '2.0',
    'method': 'send',
    'params': {
        'account': '$BOT_NUMBER',
        'recipient': ['$RECIPIENT'],
        'message': msg
    },
    'id': 'netscan-report'
}))
" <<< "$MESSAGE")

RESPONSE=$(curl -sf -X POST "$SIGNAL_RPC" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" 2>&1) || { echo "Signal send FAILED: $RESPONSE"; exit 1; }

if echo "$RESPONSE" | grep -q '"error"'; then
    echo "Signal RPC error: $RESPONSE"
    exit 1
fi
echo "Signal sent OK"
