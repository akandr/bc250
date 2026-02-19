#!/bin/bash
# scan.sh — Nightly network scan + system health snapshot
# Runs at 2 AM via cron, stores results for morning diff
# Location on bc250: /opt/netscan/scan.sh

set -euo pipefail

DATA_DIR="/opt/netscan/data"
mkdir -p "$DATA_DIR"

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
DATE=$(date +%Y%m%d)
SCAN_FILE="$DATA_DIR/scan-${DATE}.json"
HEALTH_FILE="$DATA_DIR/health-${DATE}.json"

echo "[$(date)] Starting nightly scan..."

# ── Network scan (ping sweep + MAC/vendor detection) ──
NMAP_FULL="/tmp/netscan-${DATE}.txt"
sudo nmap -sn --max-retries 2 --host-timeout 5s \
  192.168.3.0/24 > "$NMAP_FULL" 2>/dev/null

# Parse nmap output into JSON for easy diffing
python3 - "$NMAP_FULL" "$SCAN_FILE" << 'PYEOF'
import json, sys, re

text = open(sys.argv[1]).read()
hosts = {}

# Parse blocks like:
# Nmap scan report for hostname (IP)  or  Nmap scan report for IP
# Host is up (0.1s latency).
# MAC Address: AA:BB:CC:DD:EE:FF (Vendor Name)
blocks = re.split(r'(?=Nmap scan report for )', text)
for block in blocks:
    m = re.search(r'Nmap scan report for (?:(\S+) \()?(\d+\.\d+\.\d+\.\d+)\)?', block)
    if not m:
        continue
    hostname = m.group(1) or ""
    ip = m.group(2)

    if "Host is up" not in block:
        continue

    mac_m = re.search(r'MAC Address: ([0-9A-F:]+)\s*\(([^)]*)\)', block)
    mac = mac_m.group(1) if mac_m else ""
    vendor = mac_m.group(2) if mac_m else ""

    hosts[ip] = {
        "mac": mac,
        "vendor": vendor,
        "hostname": hostname,
    }

with open(sys.argv[2], "w") as f:
    json.dump({"timestamp": "TIMESTAMP", "hosts": hosts}, f, indent=2)

print(f"Scanned {len(hosts)} hosts")
PYEOF

# Fix timestamp in JSON (python doesn't have the shell var)
sed -i "s/TIMESTAMP/$TIMESTAMP/" "$SCAN_FILE"

rm -f "$NMAP_FULL"

# ── System health snapshot ──
python3 - "$HEALTH_FILE" << 'PYEOF'
import json, subprocess, os, sys

def cmd(c):
    try:
        return subprocess.check_output(c, shell=True, stderr=subprocess.DEVNULL, timeout=10).decode().strip()
    except:
        return ""

def read_sysfs(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except:
        return ""

health = {}

# Uptime & load
health["uptime"] = cmd("uptime -p")
health["load_avg"] = cmd("cat /proc/loadavg").split()[:3]

# Memory
meminfo = {}
for line in open("/proc/meminfo"):
    parts = line.split()
    if parts[0].rstrip(":") in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
        meminfo[parts[0].rstrip(":")] = int(parts[1])
health["mem_total_mb"] = meminfo.get("MemTotal", 0) // 1024
health["mem_available_mb"] = meminfo.get("MemAvailable", 0) // 1024
health["swap_used_mb"] = (meminfo.get("SwapTotal", 0) - meminfo.get("SwapFree", 0)) // 1024

# Disk
df_out = cmd("df -h / --output=size,used,avail,pcent | tail -1").split()
if len(df_out) >= 4:
    health["disk_total"] = df_out[0]
    health["disk_used"] = df_out[1]
    health["disk_avail"] = df_out[2]
    health["disk_pct"] = df_out[3]

# GPU
health["gpu_vram_used_mb"] = int(read_sysfs("/sys/class/drm/card1/device/mem_info_vram_used") or 0) // 1048576
health["gpu_gtt_used_mb"] = int(read_sysfs("/sys/class/drm/card1/device/mem_info_gtt_used") or 0) // 1048576
health["gpu_clock"] = [l.strip() for l in read_sysfs("/sys/class/drm/card1/device/pp_dpm_sclk").split("\n") if "*" in l]

# Temperatures
sensors = cmd("sensors")
for line in sensors.split("\n"):
    line = line.strip()
    if line.startswith("edge:"):
        health["gpu_temp"] = line.split("+")[1].split("°")[0] if "+" in line else ""
    elif line.startswith("Tctl:"):
        health["cpu_temp"] = line.split("+")[1].split("°")[0] if "+" in line else ""
    elif line.startswith("Composite:"):
        health["nvme_temp"] = line.split("+")[1].split("°")[0] if "+" in line else ""
    elif line.startswith("PPT:"):
        health["gpu_power_w"] = line.split(":")[1].strip().split()[0] if ":" in line else ""

# NVMe SMART
smart = cmd("sudo smartctl -a /dev/nvme0n1 2>/dev/null")
for line in smart.split("\n"):
    if "Percentage Used:" in line:
        health["nvme_wear_pct"] = line.split(":")[1].strip()
    elif "Power On Hours:" in line:
        health["nvme_power_hours"] = line.split(":")[1].strip().replace(",", "")
    elif "Data Units Read:" in line:
        health["nvme_data_read"] = line.split(":")[1].strip()
    elif "Data Units Written:" in line:
        health["nvme_data_written"] = line.split(":")[1].strip()

# Services
for svc in ["ollama"]:
    health[f"svc_{svc}"] = cmd(f"systemctl is-active {svc}")
health["svc_openclaw"] = cmd("systemctl --user is-active openclaw-gateway")
# signal-cli runs as raw process, not systemd
health["svc_signal-cli"] = "active" if cmd("pgrep -x signal-cli") else "dead"

# Ollama models loaded
models = cmd("curl -sf http://127.0.0.1:11434/api/ps 2>/dev/null")
if models:
    try:
        d = json.loads(models)
        health["ollama_models"] = [m["name"] for m in d.get("models", [])]
    except:
        health["ollama_models"] = []

# OOM kills since last scan (check dmesg)
oom = cmd("sudo dmesg --since '24 hours ago' 2>/dev/null | grep -c 'Out of memory' || echo 0")
health["oom_kills_24h"] = int(oom) if oom.isdigit() else 0

# Failed systemd units
health["failed_units"] = cmd("systemctl --failed --no-legend --no-pager 2>/dev/null | wc -l").strip()

# Listening ports summary
ports = cmd("ss -tulnp 2>/dev/null | tail -n +2 | awk '{print $1, $5}' | sort")
health["listening_ports"] = ports.split("\n") if ports else []

with open(sys.argv[1], "w") as f:
    json.dump(health, f, indent=2)

print(f"Health snapshot saved")
PYEOF

echo "[$(date)] Scan complete: $(python3 -c "import json; d=json.load(open('$SCAN_FILE')); print(len(d['hosts']), 'hosts')")"
