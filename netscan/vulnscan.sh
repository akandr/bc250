#!/bin/bash
# vulnscan.sh — Non-destructive vulnerability assessment for LAN hosts
# Probes: nmap vulners (CVE lookup), ssl-enum-ciphers, ssh2-enum-algos,
#         smb-security-mode, http-security-headers, custom risk scoring.
# All checks are DETECTION ONLY — no exploitation, no brute force, no DoS.
# Runs after enumerate.sh, enriches host data for dashboard.
# Location on bc250: /opt/netscan/vulnscan.sh
set -uo pipefail

DATA_DIR="/opt/netscan/data"
VULN_DIR="$DATA_DIR/vuln"
SCAN_DIR="$DATA_DIR"
ENUM_DIR="$DATA_DIR/enum"
DATE=$(date +%Y%m%d)
VULN_FILE="$VULN_DIR/vuln-${DATE}.json"
LOG_FILE="$DATA_DIR/scanlog-${DATE}.txt"

mkdir -p "$VULN_DIR"

log() { echo "[$(date)] $*" | tee -a "$LOG_FILE"; }

# Find latest scan + enum
SCAN_FILE=$(ls -t "$SCAN_DIR"/scan-*.json 2>/dev/null | head -1)
ENUM_FILE=$(ls -t "$ENUM_DIR"/enum-*.json 2>/dev/null | head -1)

if [[ -z "$SCAN_FILE" ]]; then
    log "VULNSCAN: No scan data found, run scan.sh first"
    exit 1
fi

log "═══ VULNSCAN START — $(basename "$SCAN_FILE") ═══"

python3 - "$SCAN_FILE" "$ENUM_FILE" "$VULN_FILE" "$VULN_DIR" << 'PYEOF'
import json, sys, os, subprocess, re, ssl, socket
import urllib.request, urllib.error
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import xml.etree.ElementTree as ET

scan_file = sys.argv[1]
enum_file = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else None
vuln_file = sys.argv[3]
vuln_dir = sys.argv[4]

scan = json.load(open(scan_file))
hosts = scan.get("hosts", {})

enum_data = {}
if enum_file and os.path.exists(enum_file):
    try:
        enum_data = json.load(open(enum_file))
    except:
        pass
enum_hosts = enum_data.get("hosts", {})

print(f"  Loaded {len(hosts)} hosts from {os.path.basename(scan_file)}")
if enum_hosts:
    print(f"  Loaded enum data for {len(enum_hosts)} hosts")

# ─── Load previous vuln data for trending ───
prev_vuln = {}
vuln_files = sorted(f for f in os.listdir(vuln_dir) if f.startswith("vuln-") and f.endswith(".json"))
for vf in vuln_files:
    if vf != os.path.basename(vuln_file):
        try:
            prev_vuln = json.load(open(os.path.join(vuln_dir, vf)))
        except:
            pass

# ─── Helpers ───

def run(cmd, timeout=60):
    """Run shell command, return stdout."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except:
        return ""

def parse_nmap_xml(xml_path):
    """Parse nmap XML output, return dict of ip -> script results."""
    results = {}
    if not os.path.exists(xml_path):
        return results
    try:
        # Handle truncated XML
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
        except ET.ParseError:
            with open(xml_path, "r") as f:
                xml_text = f.read()
            if "</nmaprun>" not in xml_text:
                xml_text = xml_text.rstrip() + "</host></nmaprun>"
            try:
                root = ET.fromstring(xml_text)
            except:
                return results

        for host_el in root.findall(".//host"):
            addr = host_el.find("address[@addrtype='ipv4']")
            if addr is None:
                continue
            ip = addr.get("addr", "")
            if not ip:
                continue
            results[ip] = {"ports": {}}
            for port_el in host_el.findall(".//port"):
                port_id = port_el.get("portid", "")
                port_data = {"scripts": {}}
                # Service info
                svc = port_el.find("service")
                if svc is not None:
                    port_data["service"] = svc.get("name", "")
                    port_data["product"] = svc.get("product", "")
                    port_data["version"] = svc.get("version", "")
                # Script outputs
                for script_el in port_el.findall("script"):
                    sid = script_el.get("id", "")
                    output = script_el.get("output", "")
                    # Also get structured tables
                    tables = []
                    for table in script_el.findall(".//table"):
                        row = {}
                        for elem in table.findall("elem"):
                            key = elem.get("key", "")
                            if key:
                                row[key] = elem.text or ""
                        if row:
                            tables.append(row)
                    port_data["scripts"][sid] = {
                        "output": output,
                        "tables": tables,
                    }
                if port_data["scripts"] or port_data.get("service"):
                    results[ip]["ports"][port_id] = port_data

            # Host-level scripts
            for script_el in host_el.findall("hostscript/script"):
                sid = script_el.get("id", "")
                output = script_el.get("output", "")
                if "host_scripts" not in results[ip]:
                    results[ip]["host_scripts"] = {}
                results[ip]["host_scripts"][sid] = output
    except Exception as ex:
        print(f"    XML parse error: {ex}")
    return results


# ─── Known weak patterns ───

WEAK_SSH_ALGOS = {
    "kex": {"diffie-hellman-group1-sha1", "diffie-hellman-group14-sha1",
            "diffie-hellman-group-exchange-sha1"},
    "encryption": {"3des-cbc", "arcfour", "arcfour128", "arcfour256",
                   "blowfish-cbc", "cast128-cbc", "aes128-cbc", "aes192-cbc", "aes256-cbc"},
    "mac": {"hmac-md5", "hmac-md5-96", "hmac-sha1-96", "hmac-ripemd160",
            "umac-64@openssh.com"},
}

WEAK_TLS_CIPHERS = {"RC4", "DES", "3DES", "NULL", "EXPORT", "anon", "MD5"}
WEAK_TLS_VERSIONS = {"SSLv2", "SSLv3", "TLSv1.0", "TLSv1.1"}

MISSING_HEADERS_CRITICAL = {
    "Strict-Transport-Security": "Missing HSTS header — allows protocol downgrade attacks",
    "X-Content-Type-Options": "Missing X-Content-Type-Options — allows MIME-sniffing attacks",
    "X-Frame-Options": "Missing X-Frame-Options — allows clickjacking",
}
MISSING_HEADERS_WARN = {
    "Content-Security-Policy": "Missing CSP — no protection against XSS/injection",
    "X-XSS-Protection": "Missing X-XSS-Protection header",
    "Referrer-Policy": "Missing Referrer-Policy header",
    "Permissions-Policy": "Missing Permissions-Policy header",
}

# Known vulnerable product/version patterns (major CVEs for common home devices)
KNOWN_VULNS = [
    # (product_regex, version_check_fn, cve, severity, description)
    (r"OpenSSH", lambda v: v and v < "7.0", "CVE-2016-0777", "high",
     "OpenSSH <7.0: roaming buffer overflow, private key leak"),
    (r"OpenSSH", lambda v: v and v < "8.5", "CVE-2021-28041", "medium",
     "OpenSSH <8.5: double-free in ssh-agent"),
    (r"OpenSSH", lambda v: v and v < "9.3p2", "CVE-2023-38408", "critical",
     "OpenSSH <9.3p2: remote code execution via ssh-agent forwarding"),
    (r"OpenSSH", lambda v: v and v < "9.8", "CVE-2024-6387", "critical",
     "OpenSSH <9.8 (regreSSHion): unauthenticated RCE via race condition"),
    (r"Apache httpd", lambda v: v and v < "2.4.52", "CVE-2021-44790", "critical",
     "Apache httpd <2.4.52: buffer overflow in mod_lua"),
    (r"Apache httpd", lambda v: v and v < "2.4.56", "CVE-2023-25690", "critical",
     "Apache httpd <2.4.56: HTTP request smuggling"),
    (r"nginx", lambda v: v and v < "1.25.3", "CVE-2023-44487", "high",
     "nginx <1.25.3: HTTP/2 rapid reset DDoS vulnerability"),
    (r"MiniUPnP", lambda v: v and v < "2.1", "CVE-2019-12109", "high",
     "MiniUPnPd <2.1: information disclosure"),
    (r"Samba", lambda v: v and v < "4.15.2", "CVE-2021-44142", "critical",
     "Samba <4.15.2: heap overflow in vfs_fruit"),
    (r"dnsmasq", lambda v: v and v < "2.86", "CVE-2021-3448", "medium",
     "dnsmasq <2.86: DNS cache poisoning"),
    (r"lighttpd", lambda v: v and v < "1.4.76", "CVE-2024-42064", "medium",
     "lighttpd <1.4.76: request smuggling"),
    (r"Portable SDK for UPnP", lambda v: True, "CVE-2012-5958", "high",
     "libupnp: multiple buffer overflow vulnerabilities"),
    (r"ISC BIND", lambda v: v and v < "9.18.24", "CVE-2023-50387", "high",
     "BIND <9.18.24: KeyTrap DNS DoS vulnerability"),
    (r"Dropbear", lambda v: v and v < "2024.84", "CVE-2023-48795", "medium",
     "Dropbear: Terrapin SSH prefix truncation attack"),
    (r"Node\.js", lambda v: v and v < "18.19.1", "CVE-2024-22019", "high",
     "Node.js <18.19.1: HTTP request smuggling via chunk extension"),
]


# ═══════════════════════════════════════════════
# Phase 1: nmap vulners — CVE lookup from service versions
# ═══════════════════════════════════════════════
print("  Phase 1: nmap vulners CVE scan...")

# Build targets: hosts with open ports
targets_with_ports = {}
for ip, h in hosts.items():
    ports = h.get("ports", [])
    if ports:
        targets_with_ports[ip] = [p["port"] for p in ports]

vulners_results = {}
if targets_with_ports:
    targets_file = "/tmp/netscan-vuln-targets.txt"
    nmap_xml = "/tmp/netscan-vuln-vulners.xml"
    all_ports = set()
    for ports in targets_with_ports.values():
        all_ports.update(ports)
    port_str = ",".join(str(p) for p in sorted(all_ports))

    # Batch by 15 hosts (same as enumerate.sh)
    ip_list = list(targets_with_ports.keys())
    batch_size = 15
    for batch_start in range(0, len(ip_list), batch_size):
        batch = ip_list[batch_start:batch_start+batch_size]
        with open(targets_file, "w") as f:
            f.write("\n".join(batch) + "\n")
        batch_ports = set()
        for bip in batch:
            batch_ports.update(targets_with_ports[bip])
        bp_str = ",".join(str(p) for p in sorted(batch_ports))

        cmd = (f"sudo nmap -sV --version-intensity 3 --open -p {bp_str} "
               f"--script vulners --host-timeout 45s --max-retries 1 "
               f"--max-parallelism 3 -iL {targets_file} -oX {nmap_xml} 2>/dev/null")
        run(cmd, timeout=180)

        parsed = parse_nmap_xml(nmap_xml)
        for ip, data in parsed.items():
            if ip not in vulners_results:
                vulners_results[ip] = data
            else:
                vulners_results[ip]["ports"].update(data.get("ports", {}))
        try:
            os.remove(nmap_xml)
        except:
            pass
        print(f"    vulners batch {batch_start//batch_size+1}/{(len(ip_list)+batch_size-1)//batch_size}: "
              f"{len(vulners_results)} hosts scanned")
    try:
        os.remove(targets_file)
    except:
        pass

# Extract CVEs from vulners output
cve_findings = {}
for ip, data in vulners_results.items():
    ip_cves = []
    for port_id, port_data in data.get("ports", {}).items():
        vulners_script = port_data.get("scripts", {}).get("vulners", {})
        output = vulners_script.get("output", "")
        # Parse CVE lines from vulners output
        for line in output.split("\n"):
            line = line.strip()
            # Match: CVE-YYYY-NNNNN  SCORE  URL
            m = re.match(r'(CVE-\d{4}-\d+)\s+(\d+\.?\d*)\s+(https?://\S+)?', line)
            if m:
                cve_id, score, url = m.groups()
                ip_cves.append({
                    "cve": cve_id,
                    "cvss": float(score),
                    "port": int(port_id),
                    "service": port_data.get("service", ""),
                    "product": port_data.get("product", ""),
                    "version": port_data.get("version", ""),
                    "url": url or f"https://nvd.nist.gov/vuln/detail/{cve_id}",
                })
        # Also check tables
        for table_row in vulners_script.get("tables", []):
            cve_id = table_row.get("id", "")
            if cve_id.startswith("CVE-"):
                try:
                    score = float(table_row.get("cvss", "0"))
                except:
                    score = 0.0
                # Avoid duplicates
                if not any(c["cve"] == cve_id and c["port"] == int(port_id) for c in ip_cves):
                    ip_cves.append({
                        "cve": cve_id,
                        "cvss": score,
                        "port": int(port_id),
                        "service": port_data.get("service", ""),
                        "product": port_data.get("product", ""),
                        "version": port_data.get("version", ""),
                        "url": f"https://nvd.nist.gov/vuln/detail/{cve_id}",
                    })
    if ip_cves:
        # Sort by CVSS descending, deduplicate
        seen = set()
        unique = []
        for c in sorted(ip_cves, key=lambda x: -x["cvss"]):
            key = (c["cve"], c["port"])
            if key not in seen:
                seen.add(key)
                unique.append(c)
        cve_findings[ip] = unique

print(f"    vulners: {len(cve_findings)} hosts with CVEs, "
      f"{sum(len(v) for v in cve_findings.values())} total CVEs")


# ═══════════════════════════════════════════════
# Phase 2: TLS weakness detection
# ═══════════════════════════════════════════════
print("  Phase 2: TLS/SSL weakness scan...")

# Find all TLS-capable ports
tls_targets = {}
for ip, h in hosts.items():
    tls_ports = []
    for p in h.get("ports", []):
        pn = p["port"]
        svc = p.get("service", "").lower()
        if pn in (443, 8443, 993, 995, 636, 8883) or "ssl" in svc or "https" in svc:
            tls_ports.append(pn)
    if tls_ports:
        tls_targets[ip] = tls_ports

tls_findings = {}
if tls_targets:
    nmap_xml = "/tmp/netscan-vuln-tls.xml"
    targets_file = "/tmp/netscan-vuln-tls-targets.txt"

    for ip, ports in tls_targets.items():
        port_str = ",".join(str(p) for p in ports)
        with open(targets_file, "w") as f:
            f.write(ip + "\n")
        cmd = (f"sudo nmap --script ssl-enum-ciphers -p {port_str} "
               f"--host-timeout 30s {ip} -oX {nmap_xml} 2>/dev/null")
        run(cmd, timeout=60)

        parsed = parse_nmap_xml(nmap_xml)
        if ip in parsed:
            issues = []
            for port_id, port_data in parsed[ip].get("ports", {}).items():
                ssl_script = port_data.get("scripts", {}).get("ssl-enum-ciphers", {})
                output = ssl_script.get("output", "")

                # Check for weak TLS versions
                for weak_ver in WEAK_TLS_VERSIONS:
                    if weak_ver in output:
                        issues.append({
                            "type": "weak_tls_version",
                            "port": int(port_id),
                            "detail": f"{weak_ver} supported",
                            "severity": "high" if "SSLv" in weak_ver else "medium",
                            "recommendation": f"Disable {weak_ver}, use TLSv1.2+ only",
                        })

                # Check for weak ciphers
                for weak in WEAK_TLS_CIPHERS:
                    if weak.lower() in output.lower():
                        issues.append({
                            "type": "weak_cipher",
                            "port": int(port_id),
                            "detail": f"Weak cipher suite: {weak}",
                            "severity": "high" if weak in ("NULL", "EXPORT", "RC4") else "medium",
                            "recommendation": f"Remove {weak}-based cipher suites",
                        })

                # Check cipher strength grades from nmap output
                if "grade" in output.lower():
                    for grade_match in re.finditer(r'least strength:\s*(\w)', output):
                        grade = grade_match.group(1)
                        if grade in ('D', 'E', 'F'):
                            issues.append({
                                "type": "weak_cipher_grade",
                                "port": int(port_id),
                                "detail": f"Cipher strength grade: {grade}",
                                "severity": "high",
                                "recommendation": "Upgrade cipher configuration",
                            })

            if issues:
                # Deduplicate
                seen = set()
                unique = []
                for issue in issues:
                    key = (issue["type"], issue["port"], issue["detail"])
                    if key not in seen:
                        seen.add(key)
                        unique.append(issue)
                tls_findings[ip] = unique

        try:
            os.remove(nmap_xml)
        except:
            pass

    try:
        os.remove(targets_file)
    except:
        pass

print(f"    TLS: {len(tls_findings)} hosts with weak TLS, "
      f"{sum(len(v) for v in tls_findings.values())} issues")


# ═══════════════════════════════════════════════
# Phase 3: SSH algorithm weakness scan
# ═══════════════════════════════════════════════
print("  Phase 3: SSH algorithm scan...")

ssh_hosts = [ip for ip, h in hosts.items()
             if any(p["port"] == 22 for p in h.get("ports", []))]

ssh_findings = {}
if ssh_hosts:
    nmap_xml = "/tmp/netscan-vuln-ssh.xml"
    targets_file = "/tmp/netscan-vuln-ssh-targets.txt"
    with open(targets_file, "w") as f:
        f.write("\n".join(ssh_hosts) + "\n")

    cmd = (f"sudo nmap --script ssh2-enum-algos,ssh-auth-methods -p 22 "
           f"--host-timeout 20s -iL {targets_file} -oX {nmap_xml} 2>/dev/null")
    run(cmd, timeout=120)

    parsed = parse_nmap_xml(nmap_xml)
    for ip, data in parsed.items():
        port_data = data.get("ports", {}).get("22", {})
        scripts = port_data.get("scripts", {})
        issues = []

        # ssh2-enum-algos
        algo_output = scripts.get("ssh2-enum-algos", {}).get("output", "")
        if algo_output:
            # Check KEX algorithms
            for weak in WEAK_SSH_ALGOS["kex"]:
                if weak in algo_output:
                    issues.append({
                        "type": "weak_ssh_kex",
                        "detail": f"Weak key exchange: {weak}",
                        "severity": "medium",
                        "recommendation": f"Disable {weak} in sshd_config KexAlgorithms",
                    })
            # Check encryption algorithms
            for weak in WEAK_SSH_ALGOS["encryption"]:
                if weak in algo_output:
                    issues.append({
                        "type": "weak_ssh_cipher",
                        "detail": f"Weak cipher: {weak}",
                        "severity": "medium" if "cbc" in weak else "high",
                        "recommendation": f"Disable {weak} in sshd_config Ciphers",
                    })
            # Check MAC algorithms
            for weak in WEAK_SSH_ALGOS["mac"]:
                if weak in algo_output:
                    issues.append({
                        "type": "weak_ssh_mac",
                        "detail": f"Weak MAC: {weak}",
                        "severity": "low",
                        "recommendation": f"Disable {weak} in sshd_config MACs",
                    })

        # ssh-auth-methods
        auth_output = scripts.get("ssh-auth-methods", {}).get("output", "")
        if auth_output:
            if "password" in auth_output.lower():
                issues.append({
                    "type": "ssh_password_auth",
                    "detail": "Password authentication enabled",
                    "severity": "medium",
                    "recommendation": "Disable PasswordAuthentication, use key-based auth only",
                })
            if "none_auth" in auth_output.lower() or "none" == auth_output.strip().lower():
                issues.append({
                    "type": "ssh_no_auth",
                    "detail": "SSH allows authentication with no credentials",
                    "severity": "critical",
                    "recommendation": "Immediately require authentication for SSH",
                })

        if issues:
            ssh_findings[ip] = issues

    try:
        os.remove(nmap_xml)
        os.remove(targets_file)
    except:
        pass

print(f"    SSH: {len(ssh_findings)} hosts with weak SSH config, "
      f"{sum(len(v) for v in ssh_findings.values())} issues")


# ═══════════════════════════════════════════════
# Phase 4: SMB security assessment
# ═══════════════════════════════════════════════
print("  Phase 4: SMB security scan...")

smb_hosts = [ip for ip, h in hosts.items()
             if any(p["port"] in (139, 445) for p in h.get("ports", []))]

smb_findings = {}
if smb_hosts:
    nmap_xml = "/tmp/netscan-vuln-smb.xml"
    for ip in smb_hosts:
        # Safe SMB scripts only — no exploit attempts
        cmd = (f"sudo nmap --script smb-security-mode,smb-vuln-ms17-010 "
               f"-p 445 --host-timeout 30s {ip} -oX {nmap_xml} 2>/dev/null")
        run(cmd, timeout=45)

        parsed = parse_nmap_xml(nmap_xml)
        if ip in parsed:
            issues = []
            # Check host-level scripts
            host_scripts = parsed[ip].get("host_scripts", {})

            # SMB security mode
            smb_sec = host_scripts.get("smb-security-mode", "")
            if smb_sec:
                if "message_signing: disabled" in smb_sec.lower():
                    issues.append({
                        "type": "smb_signing_disabled",
                        "detail": "SMB message signing disabled",
                        "severity": "high",
                        "recommendation": "Enable SMB signing to prevent relay attacks",
                    })
                if "guest" in smb_sec.lower():
                    issues.append({
                        "type": "smb_guest_access",
                        "detail": "SMB guest access may be allowed",
                        "severity": "medium",
                        "recommendation": "Disable guest access to SMB shares",
                    })

            # EternalBlue (MS17-010) — detection only
            ms17 = host_scripts.get("smb-vuln-ms17-010", "")
            if ms17 and "VULNERABLE" in ms17.upper():
                issues.append({
                    "type": "smb_ms17_010",
                    "detail": "VULNERABLE to EternalBlue (MS17-010)",
                    "severity": "critical",
                    "cve": "CVE-2017-0144",
                    "recommendation": "Apply MS17-010 patch immediately — wormable RCE",
                })

            # Port-level scripts
            for port_id, port_data in parsed[ip].get("ports", {}).items():
                for sid, sdata in port_data.get("scripts", {}).items():
                    output = sdata.get("output", "")
                    if "VULNERABLE" in output.upper() and "smb-vuln" in sid:
                        issues.append({
                            "type": f"smb_{sid}",
                            "detail": f"{sid}: VULNERABLE",
                            "severity": "critical",
                            "recommendation": f"Apply patches for {sid}",
                        })

            if issues:
                smb_findings[ip] = issues

        try:
            os.remove(nmap_xml)
        except:
            pass

print(f"    SMB: {len(smb_findings)} hosts with SMB issues, "
      f"{sum(len(v) for v in smb_findings.values())} issues")


# ═══════════════════════════════════════════════
# Phase 5: HTTP security headers analysis
# ═══════════════════════════════════════════════
print("  Phase 5: HTTP security headers...")

http_findings = {}

# Use enum data for HTTP endpoints we already fingerprinted
for ip, eh in enum_hosts.items():
    for hf in eh.get("http", []):
        port = hf.get("port", 0)
        status = hf.get("status_code", 0)
        if status <= 0:
            continue
        headers_raw = hf.get("headers_raw", {})
        issues = []

        # For HTTPS endpoints, check security headers
        is_https = port in (443, 8443)

        # Check critical missing headers
        for hdr, desc in MISSING_HEADERS_CRITICAL.items():
            # HSTS only matters for HTTPS
            if hdr == "Strict-Transport-Security" and not is_https:
                continue
            if hdr not in headers_raw:
                issues.append({
                    "type": "missing_header",
                    "port": port,
                    "detail": desc,
                    "severity": "medium",
                    "header": hdr,
                })

        # Check warning-level missing headers
        for hdr, desc in MISSING_HEADERS_WARN.items():
            if hdr not in headers_raw:
                issues.append({
                    "type": "missing_header",
                    "port": port,
                    "detail": desc,
                    "severity": "low",
                    "header": hdr,
                })

        # Check for server version disclosure
        server = hf.get("server", "")
        if server and re.search(r'\d+\.\d+', server):
            issues.append({
                "type": "server_version_disclosure",
                "port": port,
                "detail": f"Server header reveals version: {server}",
                "severity": "low",
                "recommendation": "Configure server to suppress version in headers",
            })

        # X-Powered-By disclosure
        powered = hf.get("powered_by", "")
        if powered:
            issues.append({
                "type": "powered_by_disclosure",
                "port": port,
                "detail": f"X-Powered-By reveals: {powered}",
                "severity": "low",
                "recommendation": "Remove X-Powered-By header",
            })

        if issues:
            if ip not in http_findings:
                http_findings[ip] = []
            http_findings[ip].extend(issues)

print(f"    HTTP: {len(http_findings)} hosts with header issues, "
      f"{sum(len(v) for v in http_findings.values())} issues")


# ═══════════════════════════════════════════════
# Phase 6: Known version-based vulnerabilities
# ═══════════════════════════════════════════════
print("  Phase 6: Version-based CVE matching...")

version_findings = {}

def extract_version(version_str):
    """Extract comparable version string."""
    if not version_str:
        return ""
    m = re.search(r'(\d+(?:\.\d+)+(?:p\d+)?)', str(version_str))
    return m.group(1) if m else ""

# Check against known vulnerable patterns using enum service data
for ip, eh in enum_hosts.items():
    issues = []
    for svc in eh.get("services", []):
        product = svc.get("product", "")
        version = extract_version(svc.get("version", ""))
        port = svc.get("port", 0)

        for pattern, version_check, cve_id, severity, desc in KNOWN_VULNS:
            if re.search(pattern, product, re.I):
                try:
                    if version_check(version):
                        issues.append({
                            "type": "known_cve",
                            "port": port,
                            "cve": cve_id,
                            "severity": severity,
                            "detail": desc,
                            "product": product,
                            "version": version,
                            "recommendation": f"Upgrade {product} (detected: {version})",
                        })
                except:
                    pass

    # Also check fingerprint software from enum
    fp = eh.get("fingerprint", {})
    for sw in fp.get("software", []):
        label = sw.get("label", "")
        port = sw.get("port", 0)
        for pattern, version_check, cve_id, severity, desc in KNOWN_VULNS:
            if re.search(pattern, label, re.I):
                version = extract_version(label)
                try:
                    if version_check(version):
                        # Avoid duplicate with services
                        if not any(i.get("cve") == cve_id and i.get("port") == port for i in issues):
                            issues.append({
                                "type": "known_cve",
                                "port": port,
                                "cve": cve_id,
                                "severity": severity,
                                "detail": desc,
                                "product": label,
                                "version": version,
                                "recommendation": f"Upgrade {label.split()[0] if label else 'software'}",
                            })
                except:
                    pass

    # Check SSDP server strings (often reveal old kernel/UPnP library)
    ssdp = eh.get("ssdp", {})
    ssdp_server = ssdp.get("server", "")
    if ssdp_server:
        for pattern, version_check, cve_id, severity, desc in KNOWN_VULNS:
            if re.search(pattern, ssdp_server, re.I):
                version = extract_version(ssdp_server)
                try:
                    if version_check(version):
                        if not any(i.get("cve") == cve_id for i in issues):
                            issues.append({
                                "type": "known_cve",
                                "cve": cve_id,
                                "severity": severity,
                                "detail": f"{desc} (SSDP: {ssdp_server[:80]})",
                                "recommendation": "Update device firmware",
                            })
                except:
                    pass

    if issues:
        version_findings[ip] = issues

print(f"    Version CVEs: {len(version_findings)} hosts, "
      f"{sum(len(v) for v in version_findings.values())} known vulnerabilities")


# ═══════════════════════════════════════════════
# Phase 7: Exposure / misconfiguration checks
# ═══════════════════════════════════════════════
print("  Phase 7: Exposure checks...")

exposure_findings = {}
for ip, h in hosts.items():
    issues = []
    ports = h.get("ports", [])
    port_nums = {p["port"] for p in ports}

    # RDP exposed
    if 3389 in port_nums:
        issues.append({
            "type": "rdp_exposed",
            "port": 3389,
            "severity": "high",
            "detail": "RDP (3389) exposed — frequent brute-force target",
            "recommendation": "Restrict RDP access via firewall or VPN",
        })

    # Telnet
    if 23 in port_nums:
        issues.append({
            "type": "telnet_exposed",
            "port": 23,
            "severity": "critical",
            "detail": "Telnet (23) exposed — plaintext credentials",
            "recommendation": "Disable Telnet, use SSH instead",
        })

    # RPC/NFS
    if 111 in port_nums:
        issues.append({
            "type": "rpc_exposed",
            "port": 111,
            "severity": "medium",
            "detail": "RPCBind (111) exposed — information disclosure",
            "recommendation": "Restrict RPCBind to localhost or trusted networks",
        })

    # FTP
    if 21 in port_nums:
        issues.append({
            "type": "ftp_exposed",
            "port": 21,
            "severity": "medium",
            "detail": "FTP (21) exposed — plaintext protocol",
            "recommendation": "Use SFTP instead of FTP",
        })

    # DNS open resolver
    if 53 in port_nums:
        issues.append({
            "type": "dns_exposed",
            "port": 53,
            "severity": "low",
            "detail": "DNS (53) open — potential open resolver",
            "recommendation": "Ensure DNS only serves local network queries",
        })

    # RTSP (cameras)
    if 554 in port_nums:
        issues.append({
            "type": "rtsp_exposed",
            "port": 554,
            "severity": "medium",
            "detail": "RTSP (554) exposed — camera stream accessible on LAN",
            "recommendation": "Verify RTSP authentication is enabled",
        })

    # UPnP
    if 5000 in port_nums or ip in enum_hosts and enum_hosts.get(ip, {}).get("upnp"):
        issues.append({
            "type": "upnp_enabled",
            "port": 5000,
            "severity": "medium",
            "detail": "UPnP enabled — allows automatic port forwarding",
            "recommendation": "Disable UPnP on router if not needed",
        })

    # Unencrypted web admin panels
    eh = enum_hosts.get(ip, {})
    for hf in eh.get("http", []):
        port = hf.get("port", 0)
        title = (hf.get("title", "") or "").lower()
        if port in (80, 8080, 8081) and any(kw in title for kw in
                ["admin", "router", "login", "management", "configuration",
                 "dashboard", "settings", "panel", "webui"]):
            issues.append({
                "type": "unencrypted_admin",
                "port": port,
                "severity": "high",
                "detail": f"Admin panel over HTTP (unencrypted): {hf.get('title','')}",
                "recommendation": "Access admin interfaces only via HTTPS",
            })

    if issues:
        exposure_findings[ip] = issues

print(f"    Exposure: {len(exposure_findings)} hosts, "
      f"{sum(len(v) for v in exposure_findings.values())} issues")


# ═══════════════════════════════════════════════
# Consolidate + score
# ═══════════════════════════════════════════════
print("  Consolidating findings and scoring...")

SEVERITY_SCORE = {"critical": 10, "high": 7, "medium": 4, "low": 1}

all_results = {}
for ip in set(list(cve_findings) + list(tls_findings) + list(ssh_findings) +
              list(smb_findings) + list(http_findings) + list(version_findings) +
              list(exposure_findings)):
    findings = []
    findings.extend(cve_findings.get(ip, []))
    findings.extend(tls_findings.get(ip, []))
    findings.extend(ssh_findings.get(ip, []))
    findings.extend(smb_findings.get(ip, []))
    findings.extend(http_findings.get(ip, []))
    findings.extend(version_findings.get(ip, []))
    findings.extend(exposure_findings.get(ip, []))

    # Deduplicate by (type, port, cve/detail)
    seen = set()
    unique = []
    for f in findings:
        key = (f.get("type",""), f.get("port",0), f.get("cve",""), f.get("detail","")[:50])
        if key not in seen:
            seen.add(key)
            unique.append(f)

    # Sort: critical first, then by CVSS/severity
    def sort_key(f):
        sev = SEVERITY_SCORE.get(f.get("severity","low"), 0)
        cvss = f.get("cvss", 0)
        return (-sev, -cvss)
    unique.sort(key=sort_key)

    # Risk score: weighted sum capped at 0
    risk_points = sum(SEVERITY_SCORE.get(f.get("severity","low"), 0) for f in unique)
    risk_score = max(0, 100 - risk_points)

    # Count by severity
    sev_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for f in unique:
        sev = f.get("severity", "low")
        sev_counts[sev] = sev_counts.get(sev, 0) + 1

    # Name from scan
    h = hosts.get(ip, {})
    name = h.get("mdns_name") or h.get("hostname") or h.get("vendor_oui") or ""

    all_results[ip] = {
        "ip": ip,
        "name": name,
        "scanned_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "risk_score": risk_score,
        "severity_counts": sev_counts,
        "finding_count": len(unique),
        "findings": unique,
    }

# ─── Global stats ───
total_findings = sum(r["finding_count"] for r in all_results.values())
total_crit = sum(r["severity_counts"]["critical"] for r in all_results.values())
total_high = sum(r["severity_counts"]["high"] for r in all_results.values())
total_med = sum(r["severity_counts"]["medium"] for r in all_results.values())
total_low = sum(r["severity_counts"]["low"] for r in all_results.values())
avg_risk = round(sum(r["risk_score"] for r in all_results.values()) / max(len(all_results),1))

# ─── Delta from previous scan ───
new_findings = 0
resolved_findings = 0
if prev_vuln and "hosts" in prev_vuln:
    prev_hosts = prev_vuln["hosts"]
    for ip, r in all_results.items():
        prev_r = prev_hosts.get(ip, {})
        prev_set = {(f.get("type",""), f.get("port",0), f.get("cve","")) for f in prev_r.get("findings",[])}
        curr_set = {(f.get("type",""), f.get("port",0), f.get("cve","")) for f in r["findings"]}
        new_findings += len(curr_set - prev_set)
    for ip, prev_r in prev_hosts.items():
        curr_r = all_results.get(ip, {})
        prev_set = {(f.get("type",""), f.get("port",0), f.get("cve","")) for f in prev_r.get("findings",[])}
        curr_set = {(f.get("type",""), f.get("port",0), f.get("cve","")) for f in curr_r.get("findings",[])}
        resolved_findings += len(prev_set - curr_set)


# ═══════════════════════════════════════════════
# Save
# ═══════════════════════════════════════════════
output = {
    "date": datetime.now().strftime("%Y%m%d"),
    "scanned_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    "scan_source": os.path.basename(scan_file),
    "stats": {
        "hosts_scanned": len(targets_with_ports),
        "hosts_with_findings": len(all_results),
        "total_findings": total_findings,
        "critical": total_crit,
        "high": total_high,
        "medium": total_med,
        "low": total_low,
        "avg_risk_score": avg_risk,
        "new_findings": new_findings,
        "resolved_findings": resolved_findings,
    },
    "hosts": all_results,
}

with open(vuln_file, "w") as f:
    json.dump(output, f, indent=2)

# ─── Cleanup old files (keep 30 days for trend) ───
vuln_files = sorted(f for f in os.listdir(vuln_dir) if f.startswith("vuln-") and f.endswith(".json"))
if len(vuln_files) > 30:
    for old in vuln_files[:-30]:
        try:
            os.remove(os.path.join(vuln_dir, old))
        except:
            pass

print(f"\n  ═══ VULNSCAN COMPLETE ═══")
print(f"  Output: {vuln_file}")
print(f"  {len(all_results)} hosts assessed, {total_findings} findings")
print(f"  🔴 Critical: {total_crit}  🟠 High: {total_high}  🟡 Medium: {total_med}  ⚪ Low: {total_low}")
if new_findings or resolved_findings:
    print(f"  Delta: +{new_findings} new, -{resolved_findings} resolved")
print(f"  Average risk score: {avg_risk}/100")
PYEOF

log "═══ VULNSCAN COMPLETE ═══"
