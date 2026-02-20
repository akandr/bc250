#!/bin/bash
# enumerate.sh — Deep service enumeration for discovered hosts
# Probes: nmap -sV, HTTP fingerprint, TLS certs, UPnP/SSDP,
#         NetBIOS, mDNS TXT, TCP banner grab
# Runs after scan.sh, enriches host data for dashboard.
# Location on bc250: /opt/netscan/enumerate.sh
set -uo pipefail

DATA_DIR="/opt/netscan/data"
ENUM_DIR="$DATA_DIR/enum"
SCAN_DIR="$DATA_DIR"
DATE=$(date +%Y%m%d)
ENUM_FILE="$ENUM_DIR/enum-${DATE}.json"
LOG_FILE="$DATA_DIR/scanlog-${DATE}.txt"

mkdir -p "$ENUM_DIR"

log() { echo "[$(date)] $*" | tee -a "$LOG_FILE"; }

# Find latest scan
SCAN_FILE=$(ls -t "$SCAN_DIR"/scan-*.json 2>/dev/null | head -1)
if [[ -z "$SCAN_FILE" ]]; then
    log "ENUMERATE: No scan data found, run scan.sh first"
    exit 1
fi

log "═══ ENUMERATE START — $(basename "$SCAN_FILE") ═══"

python3 - "$SCAN_FILE" "$ENUM_FILE" "$ENUM_DIR" "$DATA_DIR" << 'PYEOF'
import json, sys, os, subprocess, re, ssl, socket, struct
import urllib.request, urllib.error
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
import hashlib

scan_file, enum_file, enum_dir, data_dir = sys.argv[1:5]
date_str = datetime.now().strftime("%Y%m%d")

scan = json.load(open(scan_file))
hosts = scan.get("hosts", {})

print(f"  Loaded {len(hosts)} hosts from {os.path.basename(scan_file)}")

# ─── Load previous enum for delta comparison ───
prev_enum = {}
enum_files = sorted(f for f in os.listdir(enum_dir) if f.startswith("enum-") and f.endswith(".json"))
for ef in enum_files:
    if ef != os.path.basename(enum_file):
        try:
            prev_enum = json.load(open(os.path.join(enum_dir, ef)))
        except:
            pass

# ─── Helpers ───

def run(cmd, timeout=10):
    """Run shell command, return stdout or empty string."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except:
        return ""

def tcp_banner(ip, port, timeout=3):
    """Grab raw TCP banner."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((ip, port))
        # Some services send banner immediately, others need a nudge
        try:
            s.sendall(b"\r\n")
        except:
            pass
        data = s.recv(1024)
        s.close()
        text = data.decode("utf-8", errors="replace").strip()
        # Truncate
        return text[:500] if text else ""
    except:
        return ""

class TitleParser(HTMLParser):
    """Extract <title> from HTML."""
    def __init__(self):
        super().__init__()
        self._in_title = False
        self.title = ""
    def handle_starttag(self, tag, attrs):
        if tag.lower() == "title":
            self._in_title = True
    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self._in_title = False
    def handle_data(self, data):
        if self._in_title:
            self.title += data

def http_fingerprint(ip, port, use_https=False):
    """HTTP fingerprint: server header, title, redirects, powered-by."""
    scheme = "https" if use_https else "http"
    url = f"{scheme}://{ip}:{port}/"
    result = {
        "server": "",
        "title": "",
        "powered_by": "",
        "content_type": "",
        "status_code": 0,
        "redirect": "",
        "generator": "",
        "headers_raw": {},
        "favicon_hash": "",
    }
    try:
        import ssl as _ssl
        ctx = _ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE

        req = urllib.request.Request(url, headers={
            "User-Agent": "netscan/3.0 enumerate",
            "Accept": "text/html,*/*",
        })
        handler = urllib.request.HTTPSHandler(context=ctx) if use_https else urllib.request.HTTPHandler()
        opener = urllib.request.build_opener(handler)
        resp = opener.open(req, timeout=5)

        result["status_code"] = resp.status
        hdrs = dict(resp.headers)
        result["headers_raw"] = {k: v for k, v in hdrs.items()
                                  if k.lower() in ("server", "x-powered-by", "x-generator",
                                                    "content-type", "www-authenticate",
                                                    "x-frame-options", "x-content-type-options",
                                                    "strict-transport-security", "access-control-allow-origin")}
        result["server"] = hdrs.get("Server", "")
        result["powered_by"] = hdrs.get("X-Powered-By", "")
        result["content_type"] = hdrs.get("Content-Type", "")

        if resp.url != url:
            result["redirect"] = resp.url

        body = resp.read(32768).decode("utf-8", errors="replace")

        # Extract title
        tp = TitleParser()
        try:
            tp.feed(body)
            result["title"] = tp.title.strip()[:200]
        except:
            pass

        # Extract meta generator
        gm = re.search(r'<meta[^>]*name=["\']generator["\'][^>]*content=["\'](.*?)["\']', body, re.I)
        if gm:
            result["generator"] = gm.group(1).strip()[:100]

    except urllib.error.HTTPError as ex:
        result["status_code"] = ex.code
        result["server"] = ex.headers.get("Server", "") if hasattr(ex, "headers") else ""
        result["powered_by"] = ex.headers.get("X-Powered-By", "") if hasattr(ex, "headers") else ""
    except Exception:
        pass

    # Try favicon hash (useful for fingerprinting)
    if result["status_code"] > 0:
        try:
            fav_url = f"{scheme}://{ip}:{port}/favicon.ico"
            ctx2 = ssl.create_default_context()
            ctx2.check_hostname = False
            ctx2.verify_mode = ssl.CERT_NONE
            req2 = urllib.request.Request(fav_url, headers={"User-Agent": "netscan/3.0"})
            handler2 = urllib.request.HTTPSHandler(context=ctx2) if use_https else urllib.request.HTTPHandler()
            opener2 = urllib.request.build_opener(handler2)
            fav_resp = opener2.open(req2, timeout=3)
            fav_data = fav_resp.read(65536)
            if len(fav_data) > 16:
                result["favicon_hash"] = hashlib.md5(fav_data).hexdigest()
        except:
            pass

    # Strip empties
    return {k: v for k, v in result.items() if v}

def tls_cert_info(ip, port):
    """Extract TLS certificate information."""
    result = {}
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection((ip, port), timeout=5) as sock:
            with ctx.wrap_socket(sock, server_hostname=ip) as ssock:
                cert = ssock.getpeercert(binary_form=False)
                if not cert:
                    # Get binary cert and parse minimally
                    der = ssock.getpeercert(binary_form=True)
                    if der:
                        result["der_size"] = len(der)
                        result["fingerprint_sha256"] = hashlib.sha256(der).hexdigest()
                    return result

                # Subject
                subj = dict(x[0] for x in cert.get("subject", ()))
                result["cn"] = subj.get("commonName", "")
                result["org"] = subj.get("organizationName", "")

                # Issuer
                issuer = dict(x[0] for x in cert.get("issuer", ()))
                result["issuer_cn"] = issuer.get("commonName", "")
                result["issuer_org"] = issuer.get("organizationName", "")

                # SAN
                san = []
                for typ, val in cert.get("subjectAltName", ()):
                    if typ in ("DNS", "IP Address"):
                        san.append(val)
                if san:
                    result["san"] = san[:10]

                # Dates
                result["not_before"] = cert.get("notBefore", "")
                result["not_after"] = cert.get("notAfter", "")

                # Serial
                result["serial"] = cert.get("serialNumber", "")

                # Protocol version
                result["protocol"] = ssock.version()
                result["cipher"] = ssock.cipher()[0] if ssock.cipher() else ""
    except Exception:
        pass
    return {k: v for k, v in result.items() if v}

def ssdp_discover(timeout=4):
    """Send SSDP M-SEARCH and collect device responses."""
    msg = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        "MAN: \"ssdp:discover\"\r\n"
        "MX: 3\r\n"
        "ST: ssdp:all\r\n"
        "\r\n"
    )
    devices = {}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.settimeout(timeout)
        s.sendto(msg.encode(), ("239.255.255.250", 1900))

        while True:
            try:
                data, addr = s.recvfrom(4096)
                ip = addr[0]
                text = data.decode("utf-8", errors="replace")
                hdrs = {}
                for line in text.split("\r\n"):
                    if ":" in line:
                        k, v = line.split(":", 1)
                        hdrs[k.strip().upper()] = v.strip()
                if ip not in devices:
                    devices[ip] = {
                        "server": hdrs.get("SERVER", ""),
                        "location": hdrs.get("LOCATION", ""),
                        "st": hdrs.get("ST", ""),
                        "usn": hdrs.get("USN", ""),
                    }
            except socket.timeout:
                break
        s.close()
    except:
        pass
    return devices

def fetch_upnp_description(url, timeout=5):
    """Fetch and parse UPnP device description XML."""
    result = {}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "netscan/3.0"})
        resp = urllib.request.urlopen(req, timeout=timeout)
        xml = resp.read(16384).decode("utf-8", errors="replace")

        def extract(tag):
            m = re.search(f"<{tag}>(.*?)</{tag}>", xml, re.I | re.DOTALL)
            return m.group(1).strip() if m else ""

        result["friendly_name"] = extract("friendlyName")
        result["manufacturer"] = extract("manufacturer")
        result["model_name"] = extract("modelName")
        result["model_number"] = extract("modelNumber")
        result["model_description"] = extract("modelDescription")
        result["serial_number"] = extract("serialNumber")
        result["udn"] = extract("UDN")
        result["device_type"] = extract("deviceType")
        result["presentation_url"] = extract("presentationURL")
    except:
        pass
    return {k: v for k, v in result.items() if v}


# ═══════════════════════════════════════════════
# Phase 1: SSDP/UPnP discovery (broadcast, fast)
# ═══════════════════════════════════════════════
print("  Phase 1: SSDP/UPnP discovery...")
ssdp_devices = ssdp_discover(timeout=4)
print(f"    SSDP: {len(ssdp_devices)} devices responded")

# Fetch UPnP descriptions for devices with LOCATION
upnp_info = {}
for ip, ssdp in ssdp_devices.items():
    loc = ssdp.get("location", "")
    if loc and loc.startswith("http"):
        desc = fetch_upnp_description(loc)
        if desc:
            upnp_info[ip] = desc
print(f"    UPnP: {len(upnp_info)} device descriptions fetched")


# ═══════════════════════════════════════════════
# Phase 2: nmap service version detection (-sV)
# ═══════════════════════════════════════════════
print("  Phase 2: nmap service version scan...")
# Build target list: only hosts with open ports
targets_with_ports = {}
for ip, h in hosts.items():
    ports = h.get("ports", [])
    if ports:
        targets_with_ports[ip] = [p["port"] for p in ports]

# Write targets file
targets_file = "/tmp/netscan-enum-targets.txt"
with open(targets_file, "w") as f:
    for ip in targets_with_ports:
        f.write(ip + "\n")

# Collect all unique ports
all_ports = set()
for ports in targets_with_ports.values():
    all_ports.update(ports)
port_str = ",".join(str(p) for p in sorted(all_ports))

nmap_sv = {}
if targets_with_ports:
    nmap_xml = "/tmp/netscan-enum-sv.xml"
    # Split into batches of 15 hosts to avoid timeouts and memory spikes
    ip_list = list(targets_with_ports.keys())
    batch_size = 15
    for batch_start in range(0, len(ip_list), batch_size):
        batch = ip_list[batch_start:batch_start+batch_size]
        batch_file = "/tmp/netscan-enum-batch.txt"
        with open(batch_file, "w") as bf:
            bf.write("\n".join(batch) + "\n")
        # Get ports for this batch
        batch_ports = set()
        for bip in batch:
            batch_ports.update(targets_with_ports[bip])
        bp_str = ",".join(str(p) for p in sorted(batch_ports))

        cmd = f"sudo nmap -sV --version-intensity 5 --open -p {bp_str} -iL {batch_file} -oX {nmap_xml} --host-timeout 45s --max-retries 1 --max-parallelism 3 --min-rate 50 --max-rate 200 2>/dev/null"
        run(cmd, timeout=180)

        # Parse nmap XML output — handle truncated XML gracefully
        if os.path.exists(nmap_xml):
            try:
                import xml.etree.ElementTree as ET
                # Try strict parse first
                try:
                    tree = ET.parse(nmap_xml)
                    root = tree.getroot()
                except ET.ParseError:
                    # Truncated XML — try to salvage by closing tags
                    with open(nmap_xml, "r") as xf:
                        xml_text = xf.read()
                    # Close any open host/nmaprun tags
                    if "</nmaprun>" not in xml_text:
                        xml_text = xml_text.rstrip() + "</host></nmaprun>"
                    try:
                        root = ET.fromstring(xml_text)
                    except ET.ParseError:
                        # Last resort: extract what we can with regex
                        root = None

                if root is not None:
                    for host_el in root.findall(".//host"):
                        addr_el = host_el.find("address[@addrtype='ipv4']")
                        if addr_el is None:
                            continue
                        ip = addr_el.get("addr", "")
                        if not ip:
                            continue
                        services = []
                        for port_el in host_el.findall(".//port"):
                            state_el = port_el.find("state")
                            if state_el is None or state_el.get("state") != "open":
                                continue
                            svc_el = port_el.find("service")
                            svc_info = {
                                "port": int(port_el.get("portid", 0)),
                                "proto": port_el.get("protocol", "tcp"),
                                "service": svc_el.get("name", "") if svc_el is not None else "",
                                "product": svc_el.get("product", "") if svc_el is not None else "",
                                "version": svc_el.get("version", "") if svc_el is not None else "",
                                "extrainfo": svc_el.get("extrainfo", "") if svc_el is not None else "",
                                "ostype": svc_el.get("ostype", "") if svc_el is not None else "",
                                "cpe": "",
                            }
                            if svc_el is not None:
                                cpe_el = svc_el.find("cpe")
                                if cpe_el is not None and cpe_el.text:
                                    svc_info["cpe"] = cpe_el.text
                            services.append({k: v for k, v in svc_info.items() if v})
                        if services:
                            nmap_sv[ip] = services
            except Exception as ex:
                print(f"    nmap XML parse error (batch {batch_start//batch_size+1}): {ex}")
            try:
                os.remove(nmap_xml)
            except:
                pass
        try:
            os.remove(batch_file)
        except:
            pass
        print(f"    batch {batch_start//batch_size+1}/{(len(ip_list)+batch_size-1)//batch_size}: {len(nmap_sv)} hosts so far")

print(f"    nmap -sV: {len(nmap_sv)} hosts with service info, {sum(len(v) for v in nmap_sv.values())} services total")


# ═══════════════════════════════════════════════
# Phase 3: Per-host deep probing (threaded)
# ═══════════════════════════════════════════════
print("  Phase 3: Per-host deep probes (HTTP, TLS, banners)...")

def probe_host(ip, h):
    """Run all probes for a single host. Returns (ip, result_dict)."""
    result = {"ip": ip, "probed_at": datetime.now().strftime("%Y-%m-%d %H:%M")}
    ports = h.get("ports", [])
    port_nums = {p["port"] for p in ports}

    # HTTP fingerprint
    http_results = []
    for p in ports:
        pn = p["port"]
        if pn in (80, 8080, 8081, 8008, 3000, 5000, 8888, 9090, 10000, 49152):
            fp = http_fingerprint(ip, pn, use_https=False)
            if fp:
                fp["port"] = pn
                http_results.append(fp)
        elif pn in (443, 8443):
            fp = http_fingerprint(ip, pn, use_https=True)
            if fp:
                fp["port"] = pn
                http_results.append(fp)
    if http_results:
        result["http"] = http_results

    # TLS certificates
    tls_results = []
    for pn in sorted(port_nums & {443, 8443, 8883, 993, 995, 636}):
        cert = tls_cert_info(ip, pn)
        if cert:
            cert["port"] = pn
            tls_results.append(cert)
    if tls_results:
        result["tls"] = tls_results

    # TCP banner grab (non-HTTP ports)
    banner_results = []
    non_http_ports = port_nums - {80, 443, 8080, 8081, 8008, 8443, 3000, 5000, 8888, 9090, 10000, 49152}
    for pn in sorted(non_http_ports):
        if pn in (22, 554, 139, 445, 111, 53):  # known protocols, skip raw banner
            continue
        banner = tcp_banner(ip, pn, timeout=3)
        if banner and len(banner) > 2:
            banner_results.append({"port": pn, "banner": banner[:300]})
    if banner_results:
        result["banners"] = banner_results

    # SSDP/UPnP info (already collected)
    if ip in ssdp_devices:
        result["ssdp"] = ssdp_devices[ip]
    if ip in upnp_info:
        result["upnp"] = upnp_info[ip]

    # nmap service versions (already collected)
    if ip in nmap_sv:
        result["services"] = nmap_sv[ip]

    return ip, result

# Run probes in parallel (8 threads)
results = {}
probe_targets = [(ip, h) for ip, h in hosts.items() if h.get("ports")]
# Also probe portless hosts that responded to SSDP
for ip in ssdp_devices:
    if ip in hosts and ip not in [t[0] for t in probe_targets]:
        probe_targets.append((ip, hosts[ip]))

print(f"    Probing {len(probe_targets)} hosts...")
with ThreadPoolExecutor(max_workers=4) as pool:
    futures = {pool.submit(probe_host, ip, h): ip for ip, h in probe_targets}
    done = 0
    for f in as_completed(futures):
        done += 1
        try:
            ip, result = f.result()
            # Only store if we got something beyond ip/timestamp
            useful_keys = set(result.keys()) - {"ip", "probed_at"}
            if useful_keys:
                results[ip] = result
        except Exception as ex:
            pass
        if done % 10 == 0:
            print(f"    ... {done}/{len(probe_targets)}")

print(f"    Deep probes: {len(results)} hosts with data")


# ═══════════════════════════════════════════════
# Phase 4: mDNS TXT records (bonus metadata)
# ═══════════════════════════════════════════════
print("  Phase 4: mDNS TXT enrichment...")
mdns_txt_raw = run("timeout 10 avahi-browse -aprt --parsable 2>/dev/null", timeout=15)
mdns_txt = {}
if mdns_txt_raw:
    for line in mdns_txt_raw.split("\n"):
        if not line.startswith("="):
            continue
        parts = line.split(";")
        if len(parts) < 10:
            continue
        ip = parts[7]
        txt = parts[9] if len(parts) > 9 else ""
        svc_type = parts[4]
        if not re.match(r'\d+\.\d+\.\d+\.\d+', ip):
            continue
        if ip not in mdns_txt:
            mdns_txt[ip] = []
        if txt and txt != '""':
            mdns_txt[ip].append({"service": svc_type, "txt": txt[:500]})

for ip, txts in mdns_txt.items():
    if ip in results:
        results[ip]["mdns_txt"] = txts[:10]
    elif txts:
        results[ip] = {"ip": ip, "probed_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "mdns_txt": txts[:10]}

print(f"    mDNS TXT: {sum(1 for v in results.values() if 'mdns_txt' in v)} hosts with TXT records")


# ═══════════════════════════════════════════════
# Phase 5: NetBIOS scan
# ═══════════════════════════════════════════════
print("  Phase 5: NetBIOS names...")
smb_hosts = [ip for ip, h in hosts.items()
             if any(p["port"] in (139, 445) for p in h.get("ports", []))]
for ip in smb_hosts:
    nb = run(f"nmblookup -A {ip} 2>/dev/null | head -10", timeout=5)
    if nb:
        names = []
        workgroup = ""
        for line in nb.split("\n"):
            m = re.match(r'\s+(\S+)\s+<([0-9a-fA-F]+)>\s+-\s+(\S+)', line)
            if m:
                name, code, flags = m.groups()
                if code == "00" and "GROUP" not in flags:
                    names.append(name)
                elif code == "00" and "GROUP" in flags:
                    workgroup = name
        if names or workgroup:
            if ip not in results:
                results[ip] = {"ip": ip, "probed_at": datetime.now().strftime("%Y-%m-%d %H:%M")}
            results[ip]["netbios"] = {
                "names": names[:5],
                "workgroup": workgroup,
            }

print(f"    NetBIOS: {sum(1 for v in results.values() if 'netbios' in v)} hosts with names")


# ═══════════════════════════════════════════════
# Phase 6: Fingerprint summary per host
# ═══════════════════════════════════════════════
print("  Phase 6: Building fingerprint summaries...")

def build_fingerprint(ip, r):
    """Build a human-readable fingerprint summary from all probes."""
    fp = {"os_guess": "", "device_guess": "", "software": [], "identifiers": []}

    # From nmap service versions
    for svc in r.get("services", []):
        product = svc.get("product", "")
        version = svc.get("version", "")
        ostype = svc.get("ostype", "")
        if product:
            label = product
            if version:
                label += f" {version}"
            fp["software"].append({"port": svc.get("port", 0), "label": label})
        if ostype and not fp["os_guess"]:
            fp["os_guess"] = ostype

    # From HTTP
    for h in r.get("http", []):
        server = h.get("server", "")
        title = h.get("title", "")
        powered = h.get("powered_by", "")
        if server:
            fp["software"].append({"port": h.get("port", 0), "label": f"HTTP: {server}"})
        if title:
            fp["identifiers"].append(f"Web title: {title}")
        if powered:
            fp["software"].append({"port": h.get("port", 0), "label": f"Powered: {powered}"})

    # From UPnP
    upnp = r.get("upnp", {})
    if upnp:
        name = upnp.get("friendly_name", "")
        mfr = upnp.get("manufacturer", "")
        model = upnp.get("model_name", "")
        if name:
            fp["identifiers"].append(f"UPnP: {name}")
        if mfr and model:
            fp["device_guess"] = f"{mfr} {model}"
        elif mfr:
            fp["device_guess"] = mfr

    # From TLS certs
    for t in r.get("tls", []):
        cn = t.get("cn", "")
        org = t.get("org", "")
        if cn:
            fp["identifiers"].append(f"TLS CN: {cn}")
        if org:
            fp["identifiers"].append(f"TLS Org: {org}")

    # From NetBIOS
    nb = r.get("netbios", {})
    if nb:
        names = nb.get("names", [])
        wg = nb.get("workgroup", "")
        if names:
            fp["identifiers"].append(f"NetBIOS: {names[0]}")
        if wg:
            fp["identifiers"].append(f"Workgroup: {wg}")

    # From SSDP
    ssdp = r.get("ssdp", {})
    if ssdp.get("server"):
        ssdp_srv = ssdp["server"]
        # Often contains OS info like "Linux/4.9 UPnP/1.0 ..."
        if "linux" in ssdp_srv.lower() and not fp["os_guess"]:
            fp["os_guess"] = "Linux"
        elif "windows" in ssdp_srv.lower() and not fp["os_guess"]:
            fp["os_guess"] = "Windows"
        fp["identifiers"].append(f"SSDP: {ssdp_srv}")

    # Deduplicate
    fp["software"] = fp["software"][:10]
    fp["identifiers"] = list(dict.fromkeys(fp["identifiers"]))[:10]

    return {k: v for k, v in fp.items() if v}

for ip, r in results.items():
    fp = build_fingerprint(ip, r)
    if fp:
        results[ip]["fingerprint"] = fp


# ═══════════════════════════════════════════════
# Phase 7: Phone detection heuristics
# ═══════════════════════════════════════════════
print("  Phase 7: Phone/device heuristics...")

# Phones with randomized MACs often have:
# - mDNS names like "iPhone-xxx", "Galaxy-xxx"
# - _companion-link, _rdlink, _airplay services
# - Port 62078 (iSync/lockdownd on iOS)
# - DHCP hostname patterns

phones_file = os.path.join(data_dir, "phones.json")
phones = {}
if os.path.exists(phones_file):
    try:
        phones = json.load(open(phones_file))
    except:
        pass

phone_hints = {}
for ip, h in hosts.items():
    hints = []
    mdns = h.get("mdns_name", "").lower()
    mdns_svcs = " ".join(h.get("mdns_services", [])).lower()
    port_nums = {p["port"] for p in h.get("ports", [])}

    # iOS indicators
    if "_companion-link" in mdns_svcs or "_rdlink" in mdns_svcs:
        hints.append("Apple companion-link (iOS/macOS)")
    if 62078 in port_nums:
        hints.append("Port 62078 (iOS lockdownd)")
    if "_apple-mobdev2" in mdns_svcs:
        hints.append("Apple mobile device")
    if "iphone" in mdns or "ipad" in mdns:
        hints.append(f"mDNS name: {h.get('mdns_name', '')}")

    # Android indicators
    if "_adb-tls-connect" in mdns_svcs:
        hints.append("Android ADB wireless")
    if "android" in mdns or "galaxy" in mdns or "pixel" in mdns:
        hints.append(f"mDNS name: {h.get('mdns_name', '')}")

    # UPnP description match
    r = results.get(ip, {})
    upnp = r.get("upnp", {})
    if upnp:
        model = (upnp.get("model_name", "") + " " + upnp.get("manufacturer", "")).lower()
        if any(x in model for x in ["samsung", "apple", "google", "oneplus", "xiaomi", "oppo", "huawei"]):
            hints.append(f"UPnP: {upnp.get('manufacturer', '')} {upnp.get('model_name', '')}")

    # Random MAC detection (locally administered bit set)
    mac = h.get("mac", "")
    if mac:
        try:
            first_octet = int(mac.split(":")[0], 16)
            if first_octet & 0x02:  # locally administered = randomized
                hints.append("Randomized MAC (locally administered bit set)")
        except:
            pass

    if hints:
        phone_hints[ip] = hints
        if ip in results:
            results[ip]["phone_hints"] = hints
        else:
            results[ip] = {"ip": ip, "probed_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "phone_hints": hints}

if phone_hints:
    print(f"    Phone hints: {len(phone_hints)} hosts with phone/mobile indicators")
else:
    print(f"    Phone hints: none detected")


# ═══════════════════════════════════════════════
# Save results
# ═══════════════════════════════════════════════
output = {
    "date": date_str,
    "probed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    "scan_source": os.path.basename(scan_file),
    "hosts_probed": len(probe_targets),
    "hosts_with_data": len(results),
    "ssdp_devices": len(ssdp_devices),
    "nmap_sv_hosts": len(nmap_sv),
    "hosts": results,
}

with open(enum_file, "w") as f:
    json.dump(output, f, indent=2)

# ─── Cleanup old enum files (keep 14 days) ───
enum_files = sorted(f for f in os.listdir(enum_dir) if f.startswith("enum-") and f.endswith(".json"))
if len(enum_files) > 14:
    for old in enum_files[:-14]:
        try:
            os.remove(os.path.join(enum_dir, old))
        except:
            pass

print(f"\n  ═══ ENUMERATE COMPLETE ═══")
print(f"  Output: {enum_file}")
print(f"  {len(results)} hosts enriched, {sum(len(r.get('services',[])) for r in results.values())} services identified")

# Cleanup
try:
    os.remove(targets_file)
except:
    pass
PYEOF

log "═══ ENUMERATE COMPLETE ═══"
