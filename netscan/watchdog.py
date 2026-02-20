#!/usr/bin/env python3
"""watchdog.py — Network integrity monitor & Signal alerter for bc250.

10 automated checks, tiered Signal alerts, JSON reports for dashboard.
Integrates with existing netscan pipeline (scan/enum/vuln data).

Checks (LIVE — safe to run frequently):
  1. ARP integrity       — detect MAC/IP mapping changes (ARP spoofing)
  2. DNS integrity       — verify public DNS resolution isn't poisoned
  3. Gateway/Internet    — WAN connectivity check
  4. Critical devices    — ping servers, network gear, NAS

Checks (FULL — run daily after scan pipeline):
  5. Vulnerability deltas — new/resolved vulns from weekly vuln scan
  6. Service changes      — version changes from enum comparison
  7. TLS certificate expiry — active probe of HTTPS endpoints
  8. Rogue DHCP           — nmap broadcast DHCP discover
  9. New risky ports      — dangerous services newly opened
 10. Security score trend — network-wide risk movement

Alert tiers:
  🔴 CRITICAL — rogue DHCP, ARP spoof (unknown MAC), DNS poisoned, cert expired
  🟠 HIGH     — new high/crit vuln, cert <7d, device offline, risky port opened
  🟡 MEDIUM   — service change, cert <30d, new medium vulns, risk score increase

Usage:
  watchdog.py              # full run (data deltas + live checks)
  watchdog.py --live-only  # ARP/DNS/ping only (for frequent cron)
  watchdog.py --dry-run    # print alerts, don't send Signal

Cron:
  0 5 * * *    /opt/netscan/watchdog.py                           # daily full
  */30 * * * * /opt/netscan/watchdog.py --live-only               # frequent live

Location on bc250: /opt/netscan/watchdog.py
"""

import json, os, sys, glob, subprocess, re, socket, hashlib, urllib.request, warnings
from datetime import datetime, timedelta, timezone
from collections import defaultdict

# ═══════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════

DATA_DIR    = "/opt/netscan/data"
REPORT_DIR  = f"{DATA_DIR}/watchdog"
STATE_FILE  = f"{DATA_DIR}/watchdog-state.json"

# Signal JSON-RPC
SIGNAL_RPC  = "http://127.0.0.1:8080/api/v1/rpc"
BOT_NUMBER  = "+48532825716"
RECIPIENT   = "+48503326388"

DASHBOARD   = "http://192.168.3.151:8888/"
SUBNET      = "192.168."          # /22 spans 192.168.0.0–192.168.3.255
GATEWAY_IP  = "192.168.1.254"

# Device types whose absence triggers HIGH alert
CRITICAL_DEVICE_TYPES = {"server", "network"}

# Only these IPs should serve DHCP
KNOWN_DHCP_SERVERS = {"192.168.1.254"}

# DNS targets — resolve and sanity-check (public domain → should NOT resolve to private)
DNS_TARGETS = ["google.com", "cloudflare.com", "one.one.one.one"]

# TLS cert expiry thresholds (days)
CERT_WARN_DAYS = 30
CERT_CRIT_DAYS = 7

# Suppress identical alerts within this window (hours)
ALERT_COOLDOWN_HOURS = 24

# Ports considered dangerous when newly opened
RISKY_PORTS = {
    21: "FTP", 23: "Telnet", 25: "SMTP-relay", 445: "SMB",
    1433: "MSSQL", 1521: "Oracle", 2049: "NFS", 3306: "MySQL",
    3389: "RDP", 5432: "PostgreSQL", 5900: "VNC", 6379: "Redis",
    9200: "Elasticsearch", 11211: "Memcached", 27017: "MongoDB",
}

RETENTION_DAYS = 30

NOW     = datetime.now()
TODAY   = NOW.strftime("%Y%m%d")
NOW_ISO = NOW.isoformat(timespec="seconds")


# ═══════════════════════════════════════════════════════════════════
# Utilities
# ═══════════════════════════════════════════════════════════════════

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)

def get_latest_two(pattern):
    """Return (current, previous) JSON objects from date-sorted files."""
    files = sorted(glob.glob(pattern))
    curr = load_json(files[-1]) if files else None
    prev = load_json(files[-2]) if len(files) >= 2 else None
    return curr, prev

def run_cmd(cmd, timeout=30):
    """Run shell command, return stdout or empty string on any failure."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""

def send_signal(msg):
    """Send message via signal-cli JSON-RPC daemon."""
    try:
        payload = json.dumps({
            "jsonrpc": "2.0",
            "method": "send",
            "params": {
                "account": BOT_NUMBER,
                "recipient": [RECIPIENT],
                "message": msg
            },
            "id": f"watchdog-{int(NOW.timestamp())}"
        })
        req = urllib.request.Request(
            SIGNAL_RPC,
            data=payload.encode(),
            headers={"Content-Type": "application/json"}
        )
        resp = urllib.request.urlopen(req, timeout=15)
        body = json.loads(resp.read())
        if "error" in body:
            log(f"  Signal RPC error: {body['error']}")
            return False
        return True
    except Exception as ex:
        log(f"  Signal send FAILED: {ex}")
        return False

def alert_hash(alert):
    """Stable hash for cooldown deduplication."""
    key = f"{alert['category']}|{alert.get('host', '')}|{alert['title']}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


# ═══════════════════════════════════════════════════════════════════
# Check #1 — ARP Integrity
# ═══════════════════════════════════════════════════════════════════

def check_arp_integrity():
    """Compare live ARP table with known scan MAC→IP mappings."""
    alerts = []

    # Build known IP→MAC from latest scan
    scan, _ = get_latest_two(f"{DATA_DIR}/scan-*.json")
    known = {}
    known_macs = set()
    if scan:
        for ip, h in scan.get("hosts", {}).items():
            mac = h.get("mac", "").upper()
            if mac:
                known[ip] = mac
                known_macs.add(mac)

    # Parse live ARP table
    arp_raw = run_cmd("ip neigh show")
    live = {}
    for line in arp_raw.splitlines():
        m = re.match(
            r'(\d+\.\d+\.\d+\.\d+)\s+dev\s+\S+\s+lladdr\s+([0-9a-fA-F:]+)\s+(\S+)',
            line
        )
        if m:
            ip, mac, state = m.group(1), m.group(2).upper(), m.group(3)
            if state not in ("FAILED", "INCOMPLETE") and ip.startswith(SUBNET):
                live[ip] = mac

    anomalies = []
    for ip, curr_mac in live.items():
        expected = known.get(ip)
        if expected and curr_mac != expected:
            is_known = curr_mac in known_macs
            tier = "high" if is_known else "critical"
            label = "(known device — possible DHCP reassignment)" if is_known \
                    else "(unknown MAC — possible ARP spoof)"
            alerts.append({
                "tier": tier, "category": "arp_anomaly",
                "title": f"ARP change: {ip} MAC {expected[:8]}…→{curr_mac[:8]}…",
                "detail": f"Expected {expected}, got {curr_mac} {label}",
                "host": ip,
            })
            anomalies.append(ip)

    # Duplicate MAC detection (same MAC on multiple IPs)
    mac_ips = defaultdict(list)
    for ip, mac in live.items():
        mac_ips[mac].append(ip)
    for mac, ips in mac_ips.items():
        if len(ips) > 3:  # routers often have 2-3; flag >3
            alerts.append({
                "tier": "medium", "category": "arp_dup_mac",
                "title": f"MAC {mac[:8]}… claimed by {len(ips)} IPs",
                "detail": ", ".join(sorted(ips)[:6]),
                "host": ips[0],
            })

    stats = {
        "checked": len(live),
        "anomalies": len(anomalies),
        "status": "critical" if any(a["tier"] == "critical" for a in alerts)
                  else "warning" if anomalies else "ok",
    }
    return alerts, stats


# ═══════════════════════════════════════════════════════════════════
# Check #2 — DNS Integrity
# ═══════════════════════════════════════════════════════════════════

def check_dns_integrity():
    """Resolve well-known public domains and verify results are sane."""
    alerts = []
    issues = 0

    for domain in DNS_TARGETS:
        try:
            addrs = socket.getaddrinfo(domain, None, socket.AF_INET)
            ips = list({a[4][0] for a in addrs})
            # Public domain resolving to private IP = almost certainly poisoned
            for ip in ips:
                if re.match(r'^(10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.)', ip):
                    alerts.append({
                        "tier": "critical", "category": "dns_poisoned",
                        "title": f"DNS poisoning: {domain} → {ip}",
                        "detail": "Public domain resolving to private IP — likely hijack",
                        "host": "",
                    })
                    issues += 1
        except socket.gaierror:
            alerts.append({
                "tier": "high", "category": "dns_failure",
                "title": f"DNS lookup failed: {domain}",
                "detail": "Could not resolve — DNS outage or misconfiguration",
                "host": "",
            })
            issues += 1

    stats = {"checked": len(DNS_TARGETS), "issues": issues,
             "status": "critical" if issues else "ok"}
    return alerts, stats


# ═══════════════════════════════════════════════════════════════════
# Check #3 — Gateway / Internet Reachability
# ═══════════════════════════════════════════════════════════════════

def check_gateway():
    """Verify gateway and Internet are reachable."""
    alerts = []
    gw_ok = "1 received" in run_cmd(f"ping -c 1 -W 2 {GATEWAY_IP}", timeout=5) \
             or "1 packets received" in run_cmd(f"ping -c 1 -W 2 {GATEWAY_IP}", timeout=5)
    if not gw_ok:
        alerts.append({
            "tier": "critical", "category": "gateway_down",
            "title": f"Gateway unreachable: {GATEWAY_IP}",
            "detail": "Default gateway not responding to ping",
            "host": GATEWAY_IP,
        })
        return alerts, {"gateway": False, "internet": False, "status": "critical"}

    inet_ok = "1 received" in run_cmd("ping -c 1 -W 3 1.1.1.1", timeout=6) \
               or "1 packets received" in run_cmd("ping -c 1 -W 3 1.1.1.1", timeout=6)
    if not inet_ok:
        inet_ok = "1 received" in run_cmd("ping -c 1 -W 3 8.8.8.8", timeout=6) \
                   or "1 packets received" in run_cmd("ping -c 1 -W 3 8.8.8.8", timeout=6)
    if not inet_ok:
        alerts.append({
            "tier": "high", "category": "internet_down",
            "title": "Internet unreachable",
            "detail": "Gateway is up but 1.1.1.1 and 8.8.8.8 not responding",
            "host": "",
        })

    stats = {"gateway": gw_ok, "internet": inet_ok,
             "status": "ok" if inet_ok else "critical" if not gw_ok else "high"}
    return alerts, stats


# ═══════════════════════════════════════════════════════════════════
# Check #4 — Critical Device Availability
# ═══════════════════════════════════════════════════════════════════

def check_device_availability():
    """Ping critical infrastructure devices (servers, network gear)."""
    alerts = []
    scan, _ = get_latest_two(f"{DATA_DIR}/scan-*.json")
    if not scan:
        return alerts, {}

    # Auto-identify critical devices from device_type
    critical = {}
    for ip, h in scan.get("hosts", {}).items():
        dtype = h.get("device_type", "")
        if dtype in CRITICAL_DEVICE_TYPES:
            name = h.get("mdns_name") or h.get("hostname") or \
                   h.get("vendor_oui") or ip
            critical[ip] = {"name": name, "type": dtype}

    # Always include gateway
    if GATEWAY_IP not in critical:
        critical[GATEWAY_IP] = {"name": "Gateway", "type": "network"}

    missing = []
    for ip, info in critical.items():
        result = run_cmd(f"ping -c 2 -W 2 {ip}", timeout=8)
        if not re.search(r'[12] (packets )?received', result):
            missing.append(ip)
            alerts.append({
                "tier": "high", "category": "device_offline",
                "title": f"Offline: {info['name']} ({ip})",
                "detail": f"{info['type']} device not responding to ping",
                "host": ip,
            })

    stats = {"critical_total": len(critical), "offline": len(missing),
             "status": "critical" if missing else "ok"}
    return alerts, stats


# ═══════════════════════════════════════════════════════════════════
# Check #5 — Vulnerability Deltas
# ═══════════════════════════════════════════════════════════════════

def check_vuln_deltas():
    """Compare latest two vuln scans — report new/resolved findings."""
    alerts = []
    curr, prev = get_latest_two(f"{DATA_DIR}/vuln/vuln-*.json")
    if not curr:
        return alerts, {}
    if not prev:
        return alerts, {"note": "first vuln scan, no comparison", "status": "ok"}

    curr_hosts = curr.get("hosts", {})
    prev_hosts = prev.get("hosts", {})
    curr_stats = curr.get("stats", {})
    prev_stats = prev.get("stats", {})

    def fkey(f):
        return f"{f.get('type', '')}|{f.get('port', '')}|{f.get('cve', '')}"

    new_findings, resolved = [], []
    for ip, h in curr_hosts.items():
        curr_keys = {fkey(f) for f in h.get("findings", [])}
        ph = prev_hosts.get(ip, {})
        prev_keys = {fkey(f) for f in ph.get("findings", [])}

        for f in h.get("findings", []):
            if fkey(f) not in prev_keys:
                new_findings.append((ip, h.get("name", ip), f))
        for f in ph.get("findings", []):
            if fkey(f) not in curr_keys:
                resolved.append((ip, ph.get("name", ip), f))

    # Hosts that fell out of vuln scan entirely
    for ip in prev_hosts:
        if ip not in curr_hosts and prev_hosts[ip].get("finding_count", 0) > 0:
            fc = prev_hosts[ip]["finding_count"]
            resolved.append((ip, prev_hosts[ip].get("name", ip),
                             {"type": "all_resolved", "severity": "info",
                              "detail": f"{fc} findings resolved (host clean)"}))

    # Categorize new findings by severity
    by_sev = defaultdict(list)
    for ip, name, f in new_findings:
        sev = f.get("severity", "?")
        if sev in ("critical", "high", "medium"):
            by_sev[sev].append((ip, name, f))

    for ip, name, f in by_sev.get("critical", []):
        alerts.append({
            "tier": "critical", "category": "vuln_new",
            "title": f"Critical vuln: {f.get('type', '?')} on {name}",
            "detail": f"{ip} — {f.get('detail', '')[:100]}",
            "host": ip,
        })
    for ip, name, f in by_sev.get("high", []):
        alerts.append({
            "tier": "high", "category": "vuln_new",
            "title": f"New high vuln: {f.get('type', '?')} on {name}",
            "detail": f"{ip} — {f.get('detail', '')[:100]}",
            "host": ip,
        })
    med_list = by_sev.get("medium", [])
    if med_list:
        hosts_hit = len({ip for ip, _, _ in med_list})
        alerts.append({
            "tier": "medium", "category": "vuln_new",
            "title": f"{len(med_list)} new medium vuln(s) on {hosts_hit} host(s)",
            "detail": "; ".join(f"{ip}:{f.get('type','?')}" for ip, _, f in med_list[:5]),
            "host": "",
        })

    # Risk score movement
    curr_risk = curr_stats.get("avg_risk_score", 0)
    prev_risk = prev_stats.get("avg_risk_score", 0)
    delta = curr_risk - prev_risk
    if delta >= 10:
        alerts.append({
            "tier": "high", "category": "risk_spike",
            "title": f"Risk spike: {prev_risk}→{curr_risk} (▲{delta})",
            "detail": "Network-wide average risk jumped significantly",
            "host": "",
        })
    elif delta >= 5:
        alerts.append({
            "tier": "medium", "category": "risk_increase",
            "title": f"Risk up: {prev_risk}→{curr_risk} (▲{delta})",
            "detail": "Network average risk increased",
            "host": "",
        })

    stats = {
        "new_total": len(new_findings),
        "new_critical": len(by_sev.get("critical", [])),
        "new_high": len(by_sev.get("high", [])),
        "new_medium": len(by_sev.get("medium", [])),
        "resolved": len(resolved),
        "curr_total": curr_stats.get("total_findings", 0),
        "prev_total": prev_stats.get("total_findings", 0),
        "risk_delta": delta,
        "status": "critical" if by_sev.get("critical") else
                  "warning" if by_sev.get("high") else "ok",
    }
    return alerts, stats


# ═══════════════════════════════════════════════════════════════════
# Check #6 — Service Version Changes
# ═══════════════════════════════════════════════════════════════════

def check_service_changes():
    """Compare enum data to detect fingerprint / HTTP header changes."""
    alerts = []
    curr, prev = get_latest_two(f"{DATA_DIR}/enum/enum-*.json")
    if not curr or not prev:
        return alerts, {"status": "ok", "note": "need 2+ enum runs"}

    changes = []
    for ip in curr.get("hosts", {}):
        ch = curr["hosts"][ip]
        ph = prev.get("hosts", {}).get(ip, {})

        # Fingerprint changed
        cfp, pfp = ch.get("fingerprint", ""), ph.get("fingerprint", "")
        if cfp and pfp and cfp != pfp:
            changes.append((ip, pfp, cfp))

        # HTTP Server header changed
        for ch_http in ch.get("http", []):
            for ph_http in ph.get("http", []):
                if ch_http.get("port") == ph_http.get("port"):
                    cs = ch_http.get("server", "")
                    ps = ph_http.get("server", "")
                    if cs and ps and cs != ps:
                        changes.append((ip, f"HTTP/{ch_http['port']}: {ps}",
                                             f"HTTP/{ch_http['port']}: {cs}"))

    for ip, old, new in changes:
        alerts.append({
            "tier": "medium", "category": "service_change",
            "title": f"Service change: {ip}",
            "detail": f"{old} → {new}",
            "host": ip,
        })

    stats = {"changes": len(changes), "status": "warning" if changes else "ok"}
    return alerts, stats


# ═══════════════════════════════════════════════════════════════════
# Check #7 — TLS Certificate Expiry
# ═══════════════════════════════════════════════════════════════════

def check_cert_expiry():
    """Probe HTTPS endpoints for certificate expiration dates."""
    alerts = []
    targets = set()

    # Gather TLS targets from scan (https ports) and enum (tls probes)
    scan, _ = get_latest_two(f"{DATA_DIR}/scan-*.json")
    if scan:
        TLS_SERVICES = {"https", "ssl", "https-alt", "imaps", "pop3s", "smtps"}
        TLS_PORTS = {443, 8443, 993, 995, 465, 636}
        for ip, h in scan.get("hosts", {}).items():
            for p in h.get("ports", []):
                if p.get("service") in TLS_SERVICES or p.get("port") in TLS_PORTS:
                    targets.add((ip, p["port"]))

    enum, _ = get_latest_two(f"{DATA_DIR}/enum/enum-*.json")
    if enum:
        for ip, h in enum.get("hosts", {}).items():
            for t in h.get("tls", []):
                targets.add((ip, t.get("port", 443)))

    expiring = []
    for host, port in targets:
        cmd = (f"echo | timeout 5 openssl s_client -connect {host}:{port} "
               f"-servername {host} 2>/dev/null "
               f"| openssl x509 -noout -enddate 2>/dev/null")
        output = run_cmd(cmd, timeout=10)
        if "notAfter=" not in output:
            continue
        date_str = output.split("=", 1)[1].strip()
        try:
            expiry = datetime.strptime(date_str, "%b %d %H:%M:%S %Y %Z")
        except ValueError:
            continue
        days_left = (expiry - datetime.now(timezone.utc).replace(tzinfo=None)).days

        if days_left <= 0:
            tier, cat = "critical", "cert_expired"
            title = f"CERT EXPIRED: {host}:{port} ({abs(days_left)}d ago)"
        elif days_left <= CERT_CRIT_DAYS:
            tier, cat = "high", "cert_expiring"
            title = f"Cert expires in {days_left}d: {host}:{port}"
        elif days_left <= CERT_WARN_DAYS:
            tier, cat = "medium", "cert_expiring"
            title = f"Cert expires in {days_left}d: {host}:{port}"
        else:
            continue  # healthy — don't alert

        alerts.append({
            "tier": tier, "category": cat,
            "title": title,
            "detail": f"Expires {expiry.strftime('%Y-%m-%d')}",
            "host": host,
        })
        expiring.append((host, port, days_left))

    stats = {
        "checked": len(targets), "expiring": len(expiring),
        "status": "critical" if any(d <= 0 for _, _, d in expiring)
                  else "warning" if expiring else "ok",
    }
    return alerts, stats


# ═══════════════════════════════════════════════════════════════════
# Check #8 — Rogue DHCP Detection
# ═══════════════════════════════════════════════════════════════════

def check_rogue_dhcp():
    """Use nmap broadcast-dhcp-discover to find unauthorized DHCP servers."""
    alerts = []

    # Determine default interface
    iface = run_cmd("ip route show default | awk '{print $5}' | head -1")
    if not iface:
        return alerts, {"status": "skip", "note": "no default interface"}

    output = run_cmd(
        f"sudo nmap --script broadcast-dhcp-discover -e {iface} 2>/dev/null",
        timeout=25
    )

    servers = set()
    for line in output.splitlines():
        m = re.search(r'Server Identifier:\s*(\d+\.\d+\.\d+\.\d+)', line)
        if m:
            servers.add(m.group(1))

    rogue = servers - KNOWN_DHCP_SERVERS
    for ip in rogue:
        alerts.append({
            "tier": "critical", "category": "rogue_dhcp",
            "title": f"Rogue DHCP server: {ip}",
            "detail": f"Unauthorized DHCP server on LAN! "
                       f"Known: {', '.join(KNOWN_DHCP_SERVERS)}",
            "host": ip,
        })

    stats = {
        "servers": list(servers), "rogue": list(rogue),
        "status": "critical" if rogue else "ok",
    }
    return alerts, stats


# ═══════════════════════════════════════════════════════════════════
# Check #9 — New Risky Ports
# ═══════════════════════════════════════════════════════════════════

def check_risky_ports():
    """Cross-reference new open ports with known dangerous services."""
    alerts = []
    curr, prev = get_latest_two(f"{DATA_DIR}/scan-*.json")
    if not curr or not prev:
        return alerts, {"status": "ok"}

    risky_found = []
    for ip, h in curr.get("hosts", {}).items():
        curr_ports = {p["port"] for p in h.get("ports", [])}
        ph = prev.get("hosts", {}).get(ip, {})
        prev_ports = {p["port"] for p in ph.get("ports", [])}

        for port in (curr_ports - prev_ports):
            if port in RISKY_PORTS:
                name = h.get("mdns_name") or h.get("hostname") or \
                       h.get("vendor_oui") or ip
                risky_found.append((ip, port))
                alerts.append({
                    "tier": "high", "category": "risky_port",
                    "title": f"New {RISKY_PORTS[port]} ({port}/tcp) on {name}",
                    "detail": f"{ip} — dangerous service newly opened",
                    "host": ip,
                })

    stats = {"risky_new": len(risky_found), "status": "warning" if risky_found else "ok"}
    return alerts, stats


# ═══════════════════════════════════════════════════════════════════
# Check #10 — Security Score Trend
# ═══════════════════════════════════════════════════════════════════

def check_score_trend():
    """Compare network-wide security score between consecutive scans."""
    alerts = []
    curr, prev = get_latest_two(f"{DATA_DIR}/scan-*.json")
    if not curr or not prev:
        return alerts, {"status": "ok"}

    curr_avg = curr.get("security", {}).get("avg_score", 0)
    prev_avg = prev.get("security", {}).get("avg_score", 0)
    delta = prev_avg - curr_avg  # positive = score dropped (worse)

    curr_crit = curr.get("security", {}).get("critical", 0)
    prev_crit = prev.get("security", {}).get("critical", 0)
    new_crit = curr_crit - prev_crit

    if delta >= 10 or new_crit >= 3:
        alerts.append({
            "tier": "high", "category": "score_drop",
            "title": f"Security score drop: {prev_avg}→{curr_avg} (▼{delta})",
            "detail": f"{curr_crit} critical hosts (+{new_crit})",
            "host": "",
        })
    elif delta >= 5 or new_crit >= 1:
        alerts.append({
            "tier": "medium", "category": "score_drop",
            "title": f"Score dipped: {prev_avg}→{curr_avg} (▼{delta})",
            "detail": f"{curr_crit} critical hosts",
            "host": "",
        })

    # Per-host: flag any device whose score dropped by 20+
    for ip, h in curr.get("hosts", {}).items():
        ph = prev.get("hosts", {}).get(ip, {})
        cs = h.get("security_score", 100)
        ps = ph.get("security_score", 100)
        if ps - cs >= 20:
            name = h.get("mdns_name") or h.get("hostname") or ip
            alerts.append({
                "tier": "medium", "category": "host_score_drop",
                "title": f"Score drop on {name}: {ps}→{cs}",
                "detail": f"{ip} security score fell by {ps-cs} points",
                "host": ip,
            })

    stats = {
        "curr_avg": curr_avg, "prev_avg": prev_avg, "delta": delta,
        "new_critical_hosts": new_crit,
        "status": "warning" if delta >= 5 else "ok",
    }
    return alerts, stats


# ═══════════════════════════════════════════════════════════════════
# Alert Management & Signal Formatting
# ═══════════════════════════════════════════════════════════════════

def load_state():
    return load_json(STATE_FILE) or {"sent_alerts": {}, "history": []}

def save_state(state):
    save_json(STATE_FILE, state)

def filter_cooldown(alerts, state):
    """Suppress alerts that were already sent within the cooldown window."""
    cutoff = (NOW - timedelta(hours=ALERT_COOLDOWN_HOURS)).isoformat()
    sent = state.get("sent_alerts", {})
    return [a for a in alerts if sent.get(alert_hash(a), "") < cutoff]

def build_signal_message(alerts, check_stats, mode):
    """Format actionable alerts into a readable Signal message."""
    if not alerts:
        return None

    ts = NOW.strftime("%d %b %H:%M")
    header = f"🛡️ WATCHDOG — {ts}" if mode == "full" \
             else f"⚠️ WATCHDOG LIVE — {ts}"
    parts = [header, ""]

    # Group by tier
    buckets = {"critical": [], "high": [], "medium": []}
    for a in alerts:
        t = a.get("tier", "medium")
        if t in buckets:
            buckets[t].append(a)

    icons = {"critical": "🔴 CRITICAL", "high": "🟠 HIGH", "medium": "🟡 CHANGES"}
    for tier, icon in icons.items():
        items = buckets[tier]
        if not items:
            continue
        parts.append(icon)
        for a in items[:6]:
            parts.append(f"• {a['title']}")
            if a.get("detail") and tier in ("critical", "high"):
                parts.append(f"  {a['detail'][:90]}")
        if len(items) > 6:
            parts.append(f"  …+{len(items) - 6} more")
        parts.append("")

    # Integrity summary
    integrity = []
    for key, label in [("arp_integrity", "ARP"), ("dns_integrity", "DNS"),
                        ("gateway", "GW"), ("dhcp_integrity", "DHCP")]:
        st = check_stats.get(key, {}).get("status", "—")
        integrity.append(f"{label}:{'✅' if st == 'ok' else '⚠️'}")
    parts.append(" · ".join(integrity))

    # Vuln summary if available
    vs = check_stats.get("vuln_delta", {})
    if vs and "curr_total" in vs:
        parts.append(
            f"📊 Vulns: {vs['curr_total']} "
            f"(+{vs.get('new_total', 0)}/−{vs.get('resolved', 0)})"
        )

    parts.append(f"\n🔗 {DASHBOARD}")

    msg = "\n".join(parts)
    if len(msg) > 1500:
        msg = msg[:1450] + "\n…(truncated)"
    return msg


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

def main():
    dry_run   = "--dry-run"   in sys.argv
    live_only = "--live-only" in sys.argv
    mode      = "live" if live_only else "full"

    os.makedirs(REPORT_DIR, exist_ok=True)
    state = load_state()

    all_alerts   = []
    check_stats  = {}

    log(f"═══ WATCHDOG {mode.upper()} START ═══")

    # ── Live checks (always) ──────────────────────────────────

    checks_live = [
        ("ARP integrity",        check_arp_integrity),
        ("DNS integrity",        check_dns_integrity),
        ("Gateway / Internet",   check_gateway),
        ("Critical device ping", check_device_availability),
    ]

    for label, fn in checks_live:
        log(f"Check: {label}…")
        try:
            alerts, stats = fn()
            all_alerts.extend(alerts)
            check_stats[fn.__name__.replace("check_", "")] = stats
            status_icon = "✓" if stats.get("status") == "ok" else "⚠"
            # one-line summary
            detail_parts = [f"{k}={v}" for k, v in stats.items()
                            if k != "status" and not isinstance(v, (dict, list))]
            log(f"  {status_icon} {', '.join(detail_parts[:4])}")
        except Exception as ex:
            log(f"  ✗ {label} failed: {ex}")

    # ── Full checks (daily after scan pipeline) ──────────────

    if not live_only:
        checks_full = [
            ("Rogue DHCP",          check_rogue_dhcp),
            ("Vulnerability deltas", check_vuln_deltas),
            ("Service changes",      check_service_changes),
            ("TLS cert expiry",      check_cert_expiry),
            ("New risky ports",      check_risky_ports),
            ("Security score trend", check_score_trend),
        ]

        for label, fn in checks_full:
            log(f"Check: {label}…")
            try:
                alerts, stats = fn()
                all_alerts.extend(alerts)
                check_stats[fn.__name__.replace("check_", "")] = stats
                status_icon = "✓" if stats.get("status") == "ok" else "⚠"
                detail_parts = [f"{k}={v}" for k, v in stats.items()
                                if k != "status" and not isinstance(v, (dict, list))]
                log(f"  {status_icon} {', '.join(detail_parts[:4])}")
            except Exception as ex:
                log(f"  ✗ {label} failed: {ex}")

    # ── Deduplicate via cooldown ─────────────────────────────

    actionable = filter_cooldown(all_alerts, state)

    tier_counts = defaultdict(int)
    for a in actionable:
        tier_counts[a.get("tier", "info")] += 1

    log("─── Summary ───")
    log(f"Alerts: {len(all_alerts)} total, {len(actionable)} actionable")
    log(f"  🔴 {tier_counts['critical']} critical │ "
        f"🟠 {tier_counts['high']} high │ "
        f"🟡 {tier_counts['medium']} medium")

    # ── Send Signal ──────────────────────────────────────────

    should_send = (
        tier_counts["critical"] > 0
        or tier_counts["high"] > 0
        or (not live_only and tier_counts["medium"] > 0)
    )

    signal_sent = False
    if should_send and actionable:
        msg = build_signal_message(actionable, check_stats, mode)
        if msg:
            if dry_run:
                log("DRY RUN — would send:")
                print("─" * 50)
                print(msg)
                print("─" * 50)
            else:
                log("Sending Signal alert…")
                signal_sent = send_signal(msg)
                if signal_sent:
                    for a in actionable:
                        state.setdefault("sent_alerts", {})[alert_hash(a)] = NOW_ISO
                    log("Signal sent ✓")
    else:
        log("All clear — no alerts to send ✓")

    # ── Save report JSON ─────────────────────────────────────

    report = {
        "date": TODAY,
        "run_at": NOW_ISO,
        "mode": mode,
        "alerts": all_alerts,
        "alert_counts": dict(tier_counts),
        "checks": check_stats,
        "signal_sent": signal_sent,
        "actionable": len(actionable),
    }
    report_file = f"{REPORT_DIR}/watchdog-{TODAY}-{NOW.strftime('%H%M')}.json"
    save_json(report_file, report)
    log(f"Report: {report_file}")

    # ── Update state ─────────────────────────────────────────

    state["last_run"] = NOW_ISO
    state["last_mode"] = mode
    state.setdefault("history", []).insert(0, {
        "ts": NOW_ISO, "mode": mode,
        "counts": dict(tier_counts), "signal": signal_sent,
    })
    state["history"] = state["history"][:200]

    # Expire old cooldown entries
    cutoff = (NOW - timedelta(hours=ALERT_COOLDOWN_HOURS * 3)).isoformat()
    state["sent_alerts"] = {
        k: v for k, v in state.get("sent_alerts", {}).items() if v > cutoff
    }
    save_state(state)

    # ── Retention cleanup ────────────────────────────────────

    cutoff_date = (NOW - timedelta(days=RETENTION_DAYS)).strftime("%Y%m%d")
    for f in glob.glob(f"{REPORT_DIR}/watchdog-*.json"):
        fname = os.path.basename(f)
        parts = fname.replace("watchdog-", "").split("-")
        if parts and parts[0] < cutoff_date:
            os.remove(f)

    # ── Regenerate dashboard if there were alerts ────────────

    if all_alerts and not dry_run:
        if os.path.exists("/opt/netscan/generate-html.py"):
            log("Regenerating dashboard…")
            try:
                subprocess.run(["python3", "/opt/netscan/generate-html.py"],
                               timeout=60, capture_output=True)
                log("Dashboard updated ✓")
            except Exception:
                pass

    log(f"═══ WATCHDOG {mode.upper()} DONE ═══")


if __name__ == "__main__":
    main()
