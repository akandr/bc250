#!/bin/bash
# presence.sh — Phone presence tracker for home arrival/departure detection
# Runs every 5 minutes via cron. Tracks phone WiFi MACs on the network.
# 30-minute threshold filters out brief WiFi drops (sleep, range issues).
# Sends Signal alerts when someone arrives home or leaves.
#
# Phone list: /opt/netscan/data/phones.json
#   Auto-created from inventory on first run. Edit to add/name devices:
#   {"AA:BB:CC:DD:EE:FF": {"name": "My Phone", "track": true}}
#
# State:  /opt/netscan/data/presence-state.json
# Log:    /opt/netscan/data/presence-log.json + presence-log.txt
#
# Cron:  */5 * * * * /opt/netscan/presence.sh >> /var/log/netscan-presence.log 2>&1
# Location on bc250: /opt/netscan/presence.sh
set -euo pipefail

DATA_DIR="/opt/netscan/data"
mkdir -p "$DATA_DIR"

# Quick ping sweep — host discovery only, no port scan (~8 sec)
SCAN_OUT="/tmp/presence-scan-$$.txt"
sudo nmap -sn --max-retries 1 --host-timeout 3s \
  192.168.3.0/24 > "$SCAN_OUT" 2>/dev/null

# Also grab current ARP table (catches devices that responded recently)
ARP_OUT="/tmp/presence-arp-$$.txt"
DEFAULT_IF=$(ip route show default 2>/dev/null | awk '{print $5}' | head -1)
ip neigh show dev "${DEFAULT_IF:-eth0}" 2>/dev/null > "$ARP_OUT" || true

python3 - "$SCAN_OUT" "$ARP_OUT" "$DATA_DIR" << 'PYEOF'
import json, sys, os, re, urllib.request
from datetime import datetime, timedelta

scan_file, arp_file, data_dir = sys.argv[1:4]

PHONES_FILE = os.path.join(data_dir, "phones.json")
STATE_FILE = os.path.join(data_dir, "presence-state.json")
LOG_FILE = os.path.join(data_dir, "presence-log.json")
LOG_TXT = os.path.join(data_dir, "presence-log.txt")
HOSTS_DB = os.path.join(data_dir, "hosts-db.json")

THRESHOLD = timedelta(minutes=30)
now = datetime.now()
now_iso = now.isoformat(timespec="seconds")

# ─── Helper functions ───

def format_duration(td):
    """Format timedelta as human-readable string."""
    total_min = int(td.total_seconds() / 60)
    if total_min < 60:
        return f"{total_min}m"
    hours = total_min // 60
    mins = total_min % 60
    if hours < 24:
        return f"{hours}h {mins}m" if mins else f"{hours}h"
    days = hours // 24
    hours = hours % 24
    return f"{days}d {hours}h" if hours else f"{days}d"

def append_log(path, line):
    """Append a line to the text log, keep last 2000 lines."""
    lines = []
    if os.path.exists(path):
        with open(path) as f:
            lines = f.readlines()
    lines.append(line + "\n")
    if len(lines) > 2000:
        lines = lines[-2000:]
    with open(path, "w") as f:
        f.writelines(lines)

def send_signal(msg):
    """Send message via signal-cli JSON-RPC."""
    try:
        payload = json.dumps({
            "jsonrpc": "2.0",
            "method": "send",
            "params": {
                "account": "+48532825716",
                "recipient": ["+48503326388"],
                "message": msg
            },
            "id": "presence-alert"
        })
        req = urllib.request.Request(
            "http://127.0.0.1:8080/api/v1/rpc",
            data=payload.encode(),
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as ex:
        print(f"  Signal FAILED: {ex}")
        return False

def parse_dt(s):
    """Parse ISO datetime string, return None on failure."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except:
        return None

# ─── Parse nmap ping sweep for online MACs ───
online = {}  # mac -> ip
scan_text = open(scan_file).read() if os.path.exists(scan_file) else ""
for block in re.split(r'(?=Nmap scan report for )', scan_text):
    m = re.search(r'Nmap scan report for (?:\S+ \()?(\d+\.\d+\.\d+\.\d+)\)?', block)
    if not m or "Host is up" not in block:
        continue
    ip = m.group(1)
    mac_m = re.search(r'MAC Address: ([0-9A-F:]+)', block)
    if mac_m:
        online[mac_m.group(1).upper()] = ip

# Also check ARP table for REACHABLE/STALE entries
if os.path.exists(arp_file):
    for line in open(arp_file):
        parts = line.strip().split()
        if len(parts) >= 4:
            ip = parts[0]
            mac_raw = parts[2].upper().replace("-", ":")
            if re.match(r'[0-9A-F]{2}(:[0-9A-F]{2}){5}', mac_raw):
                if parts[-1] in ("REACHABLE", "STALE", "DELAY", "PROBE"):
                    if mac_raw not in online:
                        online[mac_raw] = ip

# ─── Load or auto-create phones.json ───
phones = {}
first_run = False

if os.path.exists(PHONES_FILE):
    try:
        phones = json.load(open(PHONES_FILE))
    except:
        phones = {}
else:
    first_run = True
    # Auto-discover phone-type devices from inventory
    if os.path.exists(HOSTS_DB):
        db = json.load(open(HOSTS_DB))
        for mac, entry in db.items():
            if mac.startswith("nomac-"):
                continue
            dtype = entry.get("device_type", "")
            if dtype == "phone":
                name = entry.get("mdns_name") or entry.get("vendor_oui") or "Phone"
                phones[mac] = {"name": name, "track": True}

    if phones:
        print(f"[presence] Auto-discovered {len(phones)} phone(s) from inventory")
    else:
        print("[presence] No phones found in inventory yet.")
        print("[presence] To track phones, add their WiFi MAC to /opt/netscan/data/phones.json")
        print('[presence] Format: {"AA:BB:CC:DD:EE:FF": {"name": "My Phone", "track": true}}')

    with open(PHONES_FILE, "w") as f:
        json.dump(phones, f, indent=2)

# Auto-add any new phone-type MACs from inventory
if os.path.exists(HOSTS_DB) and not first_run:
    db = json.load(open(HOSTS_DB))
    added = 0
    for mac, entry in db.items():
        if mac.startswith("nomac-") or mac in phones:
            continue
        if entry.get("device_type") == "phone":
            name = entry.get("mdns_name") or entry.get("vendor_oui") or "Phone"
            phones[mac] = {"name": name, "track": True}
            added += 1
    if added:
        with open(PHONES_FILE, "w") as f:
            json.dump(phones, f, indent=2)
        print(f"[presence] Auto-added {added} new phone(s)")

# Filter to actively tracked phones
tracked = {mac.upper(): info for mac, info in phones.items()
           if isinstance(info, dict) and info.get("track", True) and not mac.startswith("__")}

if not tracked:
    sys.exit(0)

# ─── Load presence state ───
state = {}
if os.path.exists(STATE_FILE):
    try:
        state = json.load(open(STATE_FILE))
    except:
        state = {}

# ─── Load event log ───
events = []
if os.path.exists(LOG_FILE):
    try:
        events = json.load(open(LOG_FILE))
    except:
        events = []

# ─── Process each tracked phone ───
alerts = []

for mac, phone_info in tracked.items():
    name = phone_info.get("name", mac)
    is_online = mac in online
    current_ip = online.get(mac, "")

    s = state.get(mac, {})
    prev_status = s.get("status", "unknown")
    last_seen = parse_dt(s.get("last_seen"))
    last_change = parse_dt(s.get("last_change"))

    if is_online:
        # ── Phone is responding ──
        s["last_seen"] = now_iso
        s["last_ip"] = current_ip

        if prev_status == "away":
            # Was declared away — they're back!
            away_dur = (now - last_change) if last_change else timedelta(0)
            s["status"] = "home"
            s["last_change"] = now_iso

            dur_str = format_duration(away_dur)
            alert_msg = f"🏠 {name} arrived home\nIP: {current_ip}\nWas away: {dur_str}"
            alerts.append(alert_msg)

            events.insert(0, {
                "ts": now_iso, "mac": mac, "name": name,
                "event": "arrived", "ip": current_ip,
                "away_min": int(away_dur.total_seconds() / 60)
            })
            append_log(LOG_TXT,
                f"{now.strftime('%Y-%m-%d %H:%M')} 🏠 ARRIVED  {name} ({current_ip}) — away {dur_str}")
            print(f"  🏠 {name} ARRIVED (was away {dur_str})")

        elif prev_status == "unknown":
            # First time — baseline as home, no alert
            s["status"] = "home"
            s["last_change"] = now_iso
            events.insert(0, {
                "ts": now_iso, "mac": mac, "name": name,
                "event": "baseline_home", "ip": current_ip
            })
            append_log(LOG_TXT,
                f"{now.strftime('%Y-%m-%d %H:%M')} 📱 BASELINE {name} — home ({current_ip})")
            print(f"  📱 {name} baselined HOME ({current_ip})")
        # else: already home, just updated last_seen — silent

    else:
        # ── Phone not responding ──
        if prev_status == "home" and last_seen:
            absence = now - last_seen
            if absence >= THRESHOLD:
                # Gone for 30+ minutes — declare left
                home_dur = (last_seen - last_change) if last_change else timedelta(0)
                s["status"] = "away"
                s["last_change"] = now_iso

                dur_str = format_duration(home_dur)
                last_ip = s.get("last_ip", "?")
                alert_msg = f"👋 {name} left home\nLast seen: {last_seen.strftime('%H:%M')}\nWas home: {dur_str}"
                alerts.append(alert_msg)

                events.insert(0, {
                    "ts": now_iso, "mac": mac, "name": name,
                    "event": "left", "ip": last_ip,
                    "last_seen": last_seen.isoformat(timespec="seconds"),
                    "home_min": int(home_dur.total_seconds() / 60)
                })
                append_log(LOG_TXT,
                    f"{now.strftime('%Y-%m-%d %H:%M')} 👋 LEFT     {name} (last {last_seen.strftime('%H:%M')}) — was home {dur_str}")
                print(f"  👋 {name} LEFT (absent {int(absence.total_seconds()/60)}m)")
            # else: not gone long enough, might just be wifi sleep — wait

        elif prev_status == "unknown":
            # First run, phone offline — baseline as away, no alert
            s["status"] = "away"
            s["last_change"] = now_iso
            events.insert(0, {
                "ts": now_iso, "mac": mac, "name": name,
                "event": "baseline_away"
            })
            append_log(LOG_TXT,
                f"{now.strftime('%Y-%m-%d %H:%M')} 📱 BASELINE {name} — away")
            print(f"  📱 {name} baselined AWAY")
        # else: already away, still away — silent

    state[mac] = s

# ─── Save state ───
with open(STATE_FILE, "w") as f:
    json.dump(state, f, indent=2)

# Trim event log to last 500 entries
if len(events) > 500:
    events = events[:500]
with open(LOG_FILE, "w") as f:
    json.dump(events, f, indent=2)

# ─── Send Signal alerts if any ───
if alerts:
    parts = ["📱 PRESENCE UPDATE\n"]
    parts.extend(alerts)

    # Summary: who's home now
    home_list = [tracked[m].get("name", m) for m in tracked
                 if state.get(m, {}).get("status") == "home"]
    away_list = [tracked[m].get("name", m) for m in tracked
                 if state.get(m, {}).get("status") == "away"]
    parts.append("")
    if home_list:
        parts.append(f"🏠 Home: {', '.join(home_list)}")
    if away_list:
        parts.append(f"👋 Away: {', '.join(away_list)}")

    msg = "\n".join(parts)
    if send_signal(msg):
        print(f"  Signal alert sent ({len(alerts)} event(s))")

# ─── Regenerate web dashboard (presence page) ───
# Only regenerate if there were events or every 30 min (on the :00 and :30)
minute = now.minute
should_regen = bool(alerts) or minute in (0, 5, 30, 35)
if should_regen and os.path.exists("/opt/netscan/generate-html.py"):
    import subprocess
    try:
        subprocess.run(["python3", "/opt/netscan/generate-html.py"],
                       timeout=30, capture_output=True)
    except:
        pass

PYEOF

rm -f "$SCAN_OUT" "$ARP_OUT"
