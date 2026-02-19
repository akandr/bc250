#!/bin/bash
# scan.sh — Nightly network scan + system health snapshot
# v3: mDNS discovery, persistent inventory, security scoring,
#     port change detection, instant new-device alerts.
# Runs at 2 AM via cron. Location on bc250: /opt/netscan/scan.sh
set -euo pipefail

DATA_DIR="/opt/netscan/data"
HOSTS_DB="$DATA_DIR/hosts-db.json"
mkdir -p "$DATA_DIR"

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
DATE=$(date +%Y%m%d)
SCAN_FILE="$DATA_DIR/scan-${DATE}.json"
HEALTH_FILE="$DATA_DIR/health-${DATE}.json"
LOG_FILE="$DATA_DIR/scanlog-${DATE}.txt"

exec > >(tee -a "$LOG_FILE") 2>&1
echo "[$(date)] ═══ NETSCAN v3 START ═══"

# ── Phase 1: Ping sweep ──
echo "[$(date)] Phase 1: Ping sweep..."
NMAP_PING="/tmp/netscan-ping-${DATE}.txt"
sudo nmap -sn --max-retries 2 --host-timeout 5s \
  192.168.3.0/24 > "$NMAP_PING" 2>/dev/null
HOST_COUNT=$(grep -c "Host is up" "$NMAP_PING" || echo 0)
echo "[$(date)] Found $HOST_COUNT hosts"

# ── Phase 1.5: mDNS discovery ──
echo "[$(date)] Phase 1.5: mDNS discovery..."
MDNS_FILE="/tmp/netscan-mdns-${DATE}.txt"
if command -v avahi-browse &>/dev/null; then
    timeout 30 avahi-browse -aprt --parsable 2>/dev/null > "$MDNS_FILE" || true
    MDNS_COUNT=$(grep -c "^=" "$MDNS_FILE" 2>/dev/null || echo 0)
    echo "[$(date)] mDNS: $MDNS_COUNT resolved services"
else
    touch "$MDNS_FILE"
    echo "[$(date)] mDNS: avahi-browse not found, skipping"
fi

# ── Phase 2: Port scan discovered hosts (top 100 ports) ──
echo "[$(date)] Phase 2: Port scanning $HOST_COUNT hosts..."
NMAP_PORTS="/tmp/netscan-ports-${DATE}.txt"
grep "Nmap scan report" "$NMAP_PING" | grep -oP '\d+\.\d+\.\d+\.\d+' > /tmp/netscan-targets.txt
sudo nmap -sS --top-ports 100 --open --max-retries 1 --host-timeout 15s \
  -iL /tmp/netscan-targets.txt > "$NMAP_PORTS" 2>/dev/null || true
echo "[$(date)] Port scan done"

# ── Phase 3: Build JSON + mDNS + inventory + security + alerts ──
echo "[$(date)] Phase 3: Database + analysis..."
python3 - "$NMAP_PING" "$NMAP_PORTS" "$MDNS_FILE" "$SCAN_FILE" "$TIMESTAMP" "$HOSTS_DB" "$DATA_DIR" << 'PYEOF'
import json, sys, re, os, glob, urllib.request
from datetime import datetime

ping_file, port_file, mdns_file, out_file, timestamp, hosts_db_file, data_dir = sys.argv[1:8]
date_str = timestamp[:8]
first_run = not os.path.exists(hosts_db_file)

# ─── Parse ping sweep ───
hosts = {}
for block in re.split(r'(?=Nmap scan report for )', open(ping_file).read()):
    m = re.search(r'Nmap scan report for (?:(\S+) \()?(\d+\.\d+\.\d+\.\d+)\)?', block)
    if not m or "Host is up" not in block:
        continue
    hostname, ip = m.group(1) or "", m.group(2)
    mac_m = re.search(r'MAC Address: ([0-9A-F:]+)\s*\(([^)]*)\)', block)
    lat_m = re.search(r'\(([0-9.]+)s latency\)', block)
    hosts[ip] = {
        "mac": mac_m.group(1) if mac_m else "",
        "vendor_nmap": mac_m.group(2) if mac_m else "",
        "hostname": hostname,
        "latency_ms": round(float(lat_m.group(1))*1000, 1) if lat_m else 0,
        "ports": [],
        "mdns_name": "",
        "mdns_services": [],
    }

# ─── Parse port scan ───
if os.path.exists(port_file):
    cur_ip = None
    for line in open(port_file):
        m = re.search(r'Nmap scan report for (?:\S+ \()?(\d+\.\d+\.\d+\.\d+)\)?', line)
        if m:
            cur_ip = m.group(1)
            continue
        pm = re.match(r'\s*(\d+)/(\w+)\s+open\s+(\S+)', line)
        if pm and cur_ip and cur_ip in hosts:
            hosts[cur_ip]["ports"].append({
                "port": int(pm.group(1)), "proto": pm.group(2), "service": pm.group(3)
            })

# ─── Parse mDNS (avahi-browse resolved entries) ───
# Format: =;interface;proto;name;service;domain;hostname;address;port;txt
def avahi_unescape(s):
    """Decode avahi-browse \\NNN decimal escapes to characters."""
    return re.sub(r'\\(\d{3})', lambda m: chr(int(m.group(1))), s)

def is_uuid(s):
    """Check if string looks like a UUID."""
    return bool(re.match(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-', s))

mdns_by_ip = {}
if os.path.exists(mdns_file):
    for line in open(mdns_file):
        line = line.strip()
        if not line.startswith("="):
            continue
        parts = line.split(";")
        if len(parts) < 8:
            continue
        name = avahi_unescape(parts[3])
        service, ip = parts[4], parts[7]
        if not re.match(r'\d+\.\d+\.\d+\.\d+', ip):
            continue
        if ip not in mdns_by_ip:
            mdns_by_ip[ip] = {"names": set(), "services": set()}
        mdns_by_ip[ip]["names"].add(name)
        mdns_by_ip[ip]["services"].add(service)

for ip, mdns in mdns_by_ip.items():
    if ip in hosts:
        # Prefer human-readable names over UUIDs/hashes
        names = sorted(mdns["names"], key=lambda n: (is_uuid(n), -len(n), n))
        hosts[ip]["mdns_name"] = names[0] if names else ""
        hosts[ip]["mdns_services"] = sorted(mdns["services"])
print(f"  mDNS: {len(mdns_by_ip)} IPs with names")

# ─── MAC OUI lookup ───
oui_db = {}
if os.path.exists("/opt/netscan/oui.txt"):
    for line in open("/opt/netscan/oui.txt"):
        if "(hex)" in line:
            parts = line.strip().split("(hex)")
            if len(parts) == 2:
                oui_db[parts[0].strip().replace("-",":").upper()] = parts[1].strip()

for h in hosts.values():
    mac = h.get("mac","")
    h["vendor_oui"] = oui_db.get(mac[:8].upper(), "") if len(mac) >= 8 else ""

# ─── Device classification (mDNS-enhanced) ───
def classify(h):
    v = (h.get("vendor_oui","")+" "+h.get("vendor_nmap","")).lower()
    hn = h.get("hostname","").lower()
    mdns = h.get("mdns_name","").lower()
    mdns_svcs = " ".join(h.get("mdns_services",[])).lower()
    ports = {p["port"] for p in h.get("ports",[])}

    # mDNS-informed (highest priority — real device identity)
    # Specific device names first (override service-type guesses)
    if "macbook" in mdns or "imac" in mdns: return "pc"
    if "iphone" in mdns or "ipad" in mdns: return "phone"
    if "_home-assistant" in mdns_svcs or "homeassistant" in mdns: return "server"
    if "hikvision" in mdns or "ipc-" in mdns: return "camera"
    if "_openclaw" in mdns_svcs or "openclaw" in mdns: return "server"
    if "roborock" in mdns or "vacuum" in mdns or "_miio" in mdns_svcs: return "appliance"
    if "soundbar" in mdns: return "appliance"
    if "_raop" in mdns_svcs and not any(x in mdns for x in ["macbook","imac","iphone","ipad"]): return "appliance"
    if "_airplay" in mdns_svcs and "tv" in mdns: return "appliance"
    if "funbox" in mdns: return "network"

    # Vendor-based
    if "espressif" in v: return "iot-web" if ports & {80,443,8080,8081} else "iot"
    if any(x in v for x in ["tuya","xiaomi","imilab","shenzhen"]): return "iot"
    if "guoguang" in v or "a113d" in hn or "a113d" in mdns: return "smart-speaker"
    if "google" in v and ports & {8008,8009,8443}: return "smart-speaker"
    if "raspberry" in v or "retropie" in hn: return "sbc"
    if "microsoft" in v or "xbox" in hn: return "console"
    if "sony" in v or "playstation" in hn: return "console"
    if "azurewave" in v or "roomba" in hn: return "appliance"
    if 554 in ports: return "camera"
    if "rivet" in v or "liteon" in v or "nss" in v: return "pc"
    if any(x in hn for x in ["linux","bc250"]): return "server"
    if "apple" in v or "apple" in hn: return "phone"
    if "samsung" in v and not ports: return "phone"
    if ports & {22, 3389, 445}: return "pc"
    if ports & {80,443,8080}: return "unknown-web"
    return "unknown"

for h in hosts.values():
    h["device_type"] = classify(h)

# ─── Port change detection ───
scan_files = sorted(glob.glob(f"{data_dir}/scan-*.json"))
prev_scan = None
for sf in reversed(scan_files):
    d = os.path.basename(sf).replace("scan-","").replace(".json","")
    if d < date_str:
        try:
            with open(sf) as f:
                prev_scan = json.load(f)
        except:
            pass
        break

port_changes_summary = {"new_ports": 0, "gone_ports": 0, "hosts_changed": 0}
if prev_scan:
    prev_hosts = prev_scan.get("hosts", {})
    for ip, h in hosts.items():
        curr_ports = {(p["port"], p["proto"]) for p in h.get("ports",[])}
        prev_h = prev_hosts.get(ip, {})
        prev_ports = {(p["port"], p["proto"]) for p in prev_h.get("ports",[])}
        new_p = curr_ports - prev_ports
        gone_p = prev_ports - curr_ports
        if new_p or gone_p:
            h["port_changes"] = {
                "new": [{"port": p, "proto": pr} for p, pr in sorted(new_p)],
                "gone": [{"port": p, "proto": pr} for p, pr in sorted(gone_p)]
            }
            port_changes_summary["new_ports"] += len(new_p)
            port_changes_summary["gone_ports"] += len(gone_p)
            port_changes_summary["hosts_changed"] += 1
print(f"  Port changes: +{port_changes_summary['new_ports']} -{port_changes_summary['gone_ports']} on {port_changes_summary['hosts_changed']} hosts")

# ─── Security scoring ───
def security_score(h):
    score = 100
    flags = []
    ports = {p["port"] for p in h.get("ports",[])}
    dtype = h.get("device_type","")

    if 23 in ports:
        score -= 40; flags.append("Telnet exposed (port 23)")
    if 21 in ports:
        score -= 20; flags.append("FTP exposed (port 21)")
    if dtype == "camera" and 80 in ports and 443 not in ports:
        score -= 30; flags.append("Camera: unencrypted HTTP feed")
    if 554 in ports:
        score -= 10; flags.append("RTSP stream exposed (port 554)")
    if 445 in ports and dtype in ("iot","iot-web","camera","appliance","smart-speaker"):
        score -= 20; flags.append("SMB on IoT/embedded device")
    if 3389 in ports:
        score -= 10; flags.append("RDP exposed (port 3389)")
    if 1900 in ports or 5000 in ports:
        score -= 5; flags.append("UPnP/SSDP exposed")
    if 80 in ports and 443 not in ports and dtype not in ("iot","iot-web","unknown","sbc"):
        score -= 5; flags.append("HTTP without HTTPS")
    if dtype in ("unknown","unknown-web") and len(ports) >= 3:
        score -= 15; flags.append("Unknown device with multiple services")
    if 22 in ports and dtype in ("iot","iot-web","camera","appliance","smart-speaker"):
        score -= 10; flags.append("SSH on IoT/embedded device")
    if ports & {3306, 5432}:
        score -= 25; flags.append("Database port exposed")
    if ports & {6379, 11211}:
        score -= 30; flags.append("Cache/KV store exposed")
    return max(0, score), flags

for h in hosts.values():
    h["security_score"], h["security_flags"] = security_score(h)

# ─── Persistent inventory (hosts-db.json) ───
hosts_db = {}
if os.path.exists(hosts_db_file):
    try:
        with open(hosts_db_file) as f:
            hosts_db = json.load(f)
    except:
        hosts_db = {}

new_macs = []
for ip, h in hosts.items():
    mac = h.get("mac","")
    key = mac if mac else f"nomac-{ip}"
    if key in hosts_db:
        entry = hosts_db[key]
        entry["last_seen"] = date_str
        entry["last_ip"] = ip
        if ip not in entry.get("ips_seen",[]):
            entry.setdefault("ips_seen",[]).append(ip)
        if h.get("mdns_name"):
            entry["mdns_name"] = h["mdns_name"]
        entry["device_type"] = h.get("device_type","")
        entry["vendor_oui"] = h.get("vendor_oui","") or entry.get("vendor_oui","")
    else:
        hosts_db[key] = {
            "first_seen": date_str,
            "last_seen": date_str,
            "last_ip": ip,
            "ips_seen": [ip],
            "vendor_oui": h.get("vendor_oui",""),
            "mdns_name": h.get("mdns_name",""),
            "device_type": h.get("device_type",""),
        }
        if mac:
            new_macs.append({
                "mac": mac, "ip": ip,
                "vendor": h.get("vendor_oui","") or h.get("vendor_nmap",""),
                "mdns_name": h.get("mdns_name",""),
                "device_type": h.get("device_type",""),
                "security_score": h.get("security_score",100),
                "security_flags": h.get("security_flags",[]),
            })

    # Enrich host record with inventory data
    entry = hosts_db[key]
    h["first_seen"] = entry["first_seen"]
    h["last_seen"] = entry["last_seen"]
    try:
        fs = datetime.strptime(entry["first_seen"], "%Y%m%d")
        ls = datetime.strptime(entry["last_seen"], "%Y%m%d")
        h["days_tracked"] = (ls - fs).days + 1
    except:
        h["days_tracked"] = 1

with open(hosts_db_file, "w") as f:
    json.dump(hosts_db, f, indent=2)
print(f"  Inventory: {len(hosts_db)} MACs tracked, {len(new_macs)} new")

# ─── Build output JSON ───
sorted_hosts = dict(sorted(hosts.items(), key=lambda x: [int(o) for o in x[0].split(".")]))

all_scores = [h["security_score"] for h in sorted_hosts.values()]
sec_summary = {
    "avg_score": round(sum(all_scores)/len(all_scores)) if all_scores else 100,
    "critical": sum(1 for s in all_scores if s < 50),
    "warning": sum(1 for s in all_scores if 50 <= s < 80),
    "ok": sum(1 for s in all_scores if s >= 80),
}

data = {
    "timestamp": timestamp,
    "date": date_str,
    "host_count": len(sorted_hosts),
    "hosts": sorted_hosts,
    "port_changes": port_changes_summary,
    "security": sec_summary,
    "mdns_devices": sum(1 for h in sorted_hosts.values() if h.get("mdns_name")),
    "inventory_total": len(hosts_db),
    "new_devices": len(new_macs),
}

with open(out_file, "w") as f:
    json.dump(data, f, indent=2)

total_ports = sum(len(h["ports"]) for h in sorted_hosts.values())
print(f"  Output: {len(sorted_hosts)} hosts, {total_ports} ports")
print(f"  Security: avg={sec_summary['avg_score']}, crit={sec_summary['critical']}, warn={sec_summary['warning']}, ok={sec_summary['ok']}")

# ─── Instant alert for new devices ───
if new_macs and not first_run:
    alert_lines = []
    label = "NEW DEVICES" if len(new_macs) > 1 else "NEW DEVICE"
    alert_lines.append(f"\U0001f195 {label} DETECTED!\n")
    for nd in new_macs[:10]:
        name = nd["mdns_name"] or nd["vendor"] or "Unknown"
        alert_lines.append(f"  {nd['ip']} \u2014 {name}")
        alert_lines.append(f"  MAC: {nd['mac']} [{nd['device_type']}]")
        if nd["security_score"] < 80:
            alert_lines.append(f"  \u26a0\ufe0f Security: {nd['security_score']}/100")
            for fl in nd["security_flags"][:3]:
                alert_lines.append(f"    \u2022 {fl}")
        alert_lines.append("")
    if len(new_macs) > 10:
        alert_lines.append(f"  ...+{len(new_macs)-10} more")
    alert_lines.append(f"\U0001f517 http://192.168.3.151:8888/")
    alert_msg = "\n".join(alert_lines)
    try:
        payload = json.dumps({
            "jsonrpc": "2.0",
            "method": "send",
            "params": {
                "account": "+48532825716",
                "recipient": ["+48503326388"],
                "message": alert_msg
            },
            "id": "netscan-alert"
        })
        req = urllib.request.Request(
            "http://127.0.0.1:8080/api/v1/rpc",
            data=payload.encode(),
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=10)
        print(f"  Signal alert: {len(new_macs)} new device(s)")
    except Exception as ex:
        print(f"  Signal alert FAILED: {ex}")
elif new_macs and first_run:
    print(f"  First run: {len(new_macs)} devices baselined (no alert)")
PYEOF

rm -f "$NMAP_PING" "$NMAP_PORTS" /tmp/netscan-targets.txt "$MDNS_FILE"

# ── Phase 4: System health ──
echo "[$(date)] Phase 4: Health snapshot..."
python3 - "$HEALTH_FILE" << 'PYEOF'
import json, subprocess, sys

def cmd(c):
    try: return subprocess.check_output(c, shell=True, stderr=subprocess.DEVNULL, timeout=10).decode().strip()
    except: return ""
def sysfs(p):
    try:
        with open(p) as f: return f.read().strip()
    except: return ""

h = {}
h["uptime"] = cmd("uptime -p")
h["load_avg"] = cmd("cat /proc/loadavg").split()[:3]

mi = {}
for line in open("/proc/meminfo"):
    p = line.split()
    if p[0].rstrip(":") in ("MemTotal","MemAvailable","SwapTotal","SwapFree"):
        mi[p[0].rstrip(":")] = int(p[1])
h["mem_total_mb"] = mi.get("MemTotal",0)//1024
h["mem_available_mb"] = mi.get("MemAvailable",0)//1024
h["swap_used_mb"] = (mi.get("SwapTotal",0)-mi.get("SwapFree",0))//1024

df = cmd("df -h / --output=size,used,avail,pcent | tail -1").split()
if len(df)>=4: h["disk_total"],h["disk_used"],h["disk_avail"],h["disk_pct"] = df

h["gpu_vram_used_mb"] = int(sysfs("/sys/class/drm/card1/device/mem_info_vram_used") or 0)//1048576
h["gpu_gtt_used_mb"] = int(sysfs("/sys/class/drm/card1/device/mem_info_gtt_used") or 0)//1048576

sensors = cmd("sensors")
for line in sensors.split("\n"):
    l = line.strip()
    if l.startswith("edge:") and "+" in l: h["gpu_temp"] = l.split("+")[1].split("°")[0]
    elif l.startswith("Tctl:") and "+" in l: h["cpu_temp"] = l.split("+")[1].split("°")[0]
    elif l.startswith("Composite:") and "+" in l: h["nvme_temp"] = l.split("+")[1].split("°")[0]
    elif l.startswith("PPT:"): h["gpu_power_w"] = l.split(":")[1].strip().split()[0]

smart = cmd("sudo smartctl -a /dev/nvme0n1 2>/dev/null")
for line in smart.split("\n"):
    if "Percentage Used:" in line: h["nvme_wear_pct"] = line.split(":")[1].strip()
    elif "Power On Hours:" in line: h["nvme_power_hours"] = line.split(":")[1].strip().replace(",","")

for svc in ["ollama"]:
    h[f"svc_{svc}"] = cmd(f"systemctl is-active {svc}")
h["svc_openclaw"] = cmd("systemctl --user is-active openclaw-gateway")
h["svc_signal-cli"] = "active" if cmd("pgrep -x signal-cli") else "dead"
h["svc_nginx"] = cmd("systemctl is-active nginx")

models = cmd("curl -sf http://127.0.0.1:11434/api/ps 2>/dev/null")
if models:
    try: h["ollama_models"] = [m["name"] for m in json.loads(models).get("models",[])]
    except: h["ollama_models"] = []

oom = cmd("sudo dmesg --since '24 hours ago' 2>/dev/null | grep -c 'Out of memory' || echo 0")
h["oom_kills_24h"] = int(oom) if oom.isdigit() else 0
h["failed_units"] = cmd("systemctl --failed --no-legend --no-pager 2>/dev/null | wc -l").strip()

with open(sys.argv[1], "w") as f:
    json.dump(h, f, indent=2)
print("  Health saved")
PYEOF

# ── Phase 5: Generate web dashboard ──
echo "[$(date)] Phase 5: Web dashboard..."
python3 /opt/netscan/generate-html.py
echo "[$(date)] ═══ NETSCAN v3 COMPLETE ═══"
