#!/bin/bash
# scan.sh — Nightly network scan + system health snapshot
# Runs at 2 AM via cron. Stores JSON in /opt/netscan/data/, generates web pages.
# Location on bc250: /opt/netscan/scan.sh
set -euo pipefail

DATA_DIR="/opt/netscan/data"
mkdir -p "$DATA_DIR"

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
DATE=$(date +%Y%m%d)
SCAN_FILE="$DATA_DIR/scan-${DATE}.json"
HEALTH_FILE="$DATA_DIR/health-${DATE}.json"
LOG_FILE="$DATA_DIR/scanlog-${DATE}.txt"

exec > >(tee -a "$LOG_FILE") 2>&1
echo "[$(date)] ═══ NETSCAN START ═══"

# ── Phase 1: Ping sweep ──
echo "[$(date)] Phase 1: Ping sweep..."
NMAP_PING="/tmp/netscan-ping-${DATE}.txt"
sudo nmap -sn --max-retries 2 --host-timeout 5s \
  192.168.3.0/24 > "$NMAP_PING" 2>/dev/null
HOST_COUNT=$(grep -c "Host is up" "$NMAP_PING" || echo 0)
echo "[$(date)] Found $HOST_COUNT hosts"

# ── Phase 2: Port scan discovered hosts (top 100 ports) ──
echo "[$(date)] Phase 2: Port scanning $HOST_COUNT hosts..."
NMAP_PORTS="/tmp/netscan-ports-${DATE}.txt"
grep "Nmap scan report" "$NMAP_PING" | grep -oP '\d+\.\d+\.\d+\.\d+' > /tmp/netscan-targets.txt
sudo nmap -sS --top-ports 100 --open --max-retries 1 --host-timeout 15s \
  -iL /tmp/netscan-targets.txt > "$NMAP_PORTS" 2>/dev/null || true
echo "[$(date)] Port scan done"

# ── Phase 3: Build JSON database ──
echo "[$(date)] Phase 3: Building database..."
python3 - "$NMAP_PING" "$NMAP_PORTS" "$SCAN_FILE" "$TIMESTAMP" << 'PYEOF'
import json, sys, re, os

ping_file, port_file, out_file, timestamp = sys.argv[1:5]

# Parse ping sweep
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
    }

# Parse port scan
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

# MAC OUI lookup
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

# Device classification
def classify(h):
    v = (h.get("vendor_oui","")+" "+h.get("vendor_nmap","")).lower()
    hn = h.get("hostname","").lower()
    ports = {p["port"] for p in h.get("ports",[])}
    svcs = {p.get("service","") for p in h.get("ports",[])}
    if "espressif" in v: return "iot-web" if ports & {80,443,8080,8081} else "iot"
    if any(x in v for x in ["tuya","xiaomi","imilab","shenzhen"]): return "iot"
    if "guoguang" in v or "a113d" in hn: return "smart-speaker"
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

sorted_hosts = dict(sorted(hosts.items(), key=lambda x: [int(o) for o in x[0].split(".")]))
data = {"timestamp": timestamp, "date": timestamp[:8], "host_count": len(sorted_hosts), "hosts": sorted_hosts}
with open(out_file, "w") as f:
    json.dump(data, f, indent=2)
total_ports = sum(len(h["ports"]) for h in sorted_hosts.values())
print(f"Database: {len(sorted_hosts)} hosts, {total_ports} open ports")
PYEOF

rm -f "$NMAP_PING" "$NMAP_PORTS" /tmp/netscan-targets.txt

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
print("Health saved")
PYEOF

# ── Phase 5: Generate web dashboard ──
echo "[$(date)] Phase 5: Web dashboard..."
python3 /opt/netscan/generate-html.py
echo "[$(date)] ═══ NETSCAN COMPLETE ═══"
