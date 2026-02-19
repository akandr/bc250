#!/usr/bin/env python3
"""
generate-html.py — Phrack/BBS-style network dashboard generator
Reads scan JSON from /opt/netscan/data/, outputs static HTML to /opt/netscan/web/
Location on bc250: /opt/netscan/generate-html.py
"""
import json, os, glob, re
from datetime import datetime, timedelta
from html import escape

DATA_DIR = "/opt/netscan/data"
WEB_DIR = "/opt/netscan/web"
os.makedirs(WEB_DIR, exist_ok=True)

# ─── ASCII art / branding ───

BANNER = r"""
 ███╗   ██╗███████╗████████╗███████╗ ██████╗ █████╗ ███╗   ██╗
 ████╗  ██║██╔════╝╚══██╔══╝██╔════╝██╔════╝██╔══██╗████╗  ██║
 ██╔██╗ ██║█████╗     ██║   ███████╗██║     ███████║██╔██╗ ██║
 ██║╚██╗██║██╔══╝     ██║   ╚════██║██║     ██╔══██╗██║╚██╗██║
 ██║ ╚████║███████╗   ██║   ███████║╚██████╗██║  ██║██║ ╚████║
 ╚═╝  ╚═══╝╚══════╝   ╚═╝   ╚══════╝ ╚═════╝╚═╝  ╚═╝╚═╝  ╚═══╝"""

SKULL = r"""
      ______
   .-"      "-.
  /            \
 |              |
 |,  .-.  .-.  ,|
 | )(_o/  \o_)( |
 |/     /\     \|
 (_     ^^     _)
  \__|IIIIII|__/
   | \IIIIII/ |
   \          /
    `--------`"""

# ─── CSS: Dark BBS aesthetic, responsive ───

CSS = """
:root {
  --bg: #0a0a0a;
  --bg2: #111111;
  --bg3: #1a1a1a;
  --fg: #b0b0b0;
  --fg-dim: #555555;
  --green: #33ff33;
  --green2: #22aa22;
  --amber: #ffaa00;
  --red: #ff3333;
  --cyan: #00cccc;
  --magenta: #cc44cc;
  --blue: #4488ff;
  --border: #2a2a2a;
  --glow: 0 0 8px rgba(51,255,51,0.15);
}
*, *::before, *::after { box-sizing: border-box; }
html { font-size: 14px; }
body {
  margin: 0; padding: 0;
  background: var(--bg);
  color: var(--fg);
  font-family: 'IBM Plex Mono', 'Fira Code', 'Cascadia Code', 'Consolas', 'SF Mono', monospace;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}
a { color: var(--cyan); text-decoration: none; }
a:hover { color: var(--green); text-decoration: underline; }

/* scanline effect */
body::after {
  content: '';
  position: fixed; top: 0; left: 0;
  width: 100%; height: 100%;
  background: repeating-linear-gradient(
    0deg,
    transparent, transparent 2px,
    rgba(0,0,0,0.06) 2px, rgba(0,0,0,0.06) 4px
  );
  pointer-events: none;
  z-index: 9999;
}

.container {
  max-width: 1200px; margin: 0 auto; padding: 12px;
}

/* Banner / header */
.banner {
  color: var(--green);
  font-size: 0.5rem;
  line-height: 1.1;
  white-space: pre;
  text-align: center;
  text-shadow: var(--glow);
  overflow-x: auto;
  padding: 12px 0;
}
@media (max-width: 720px) {
  .banner { font-size: 0.32rem; }
}
.header-bar {
  border-top: 1px solid var(--green2);
  border-bottom: 1px solid var(--green2);
  padding: 6px 0;
  margin: 4px 0 16px 0;
  color: var(--green);
  text-align: center;
  font-size: 0.85rem;
}
.header-bar .node { color: var(--amber); }

/* Navigation */
nav {
  display: flex; flex-wrap: wrap; gap: 4px;
  justify-content: center;
  margin-bottom: 16px;
  padding: 8px;
  border: 1px solid var(--border);
  background: var(--bg2);
}
nav a {
  color: var(--green);
  padding: 4px 12px;
  border: 1px solid var(--border);
  font-size: 0.85rem;
  transition: all 0.15s;
}
nav a:hover, nav a.active {
  background: var(--green2);
  color: var(--bg);
  text-decoration: none;
  border-color: var(--green);
}
nav a::before { content: '[ '; color: var(--fg-dim); }
nav a::after { content: ' ]'; color: var(--fg-dim); }

/* Section boxes */
.section {
  border: 1px solid var(--border);
  margin-bottom: 16px;
  background: var(--bg2);
}
.section-title {
  background: var(--bg3);
  border-bottom: 1px solid var(--border);
  padding: 8px 12px;
  color: var(--green);
  font-weight: bold;
  font-size: 0.9rem;
}
.section-title::before { content: '┤ '; color: var(--fg-dim); }
.section-title::after { content: ' ├'; color: var(--fg-dim); }
.section-body { padding: 12px; }

/* Stats grid */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 8px;
}
.stat-box {
  border: 1px solid var(--border);
  padding: 10px;
  background: var(--bg);
  text-align: center;
}
.stat-val {
  font-size: 1.8rem;
  font-weight: bold;
  color: var(--green);
  text-shadow: var(--glow);
  line-height: 1.2;
}
.stat-label {
  font-size: 0.75rem;
  color: var(--fg-dim);
  text-transform: uppercase;
  letter-spacing: 1px;
}

/* Host table */
.host-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.82rem;
}
.host-table th {
  background: var(--bg3);
  color: var(--green);
  border: 1px solid var(--border);
  padding: 6px 8px;
  text-align: left;
  white-space: nowrap;
  position: sticky; top: 0;
  cursor: pointer;
  user-select: none;
}
.host-table th:hover { background: var(--green2); color: var(--bg); }
.host-table th::after { content: ' ↕'; color: var(--fg-dim); font-size: 0.7rem; }
.host-table td {
  border: 1px solid var(--border);
  padding: 4px 8px;
  vertical-align: top;
}
.host-table tr:nth-child(even) { background: var(--bg); }
.host-table tr:nth-child(odd) { background: var(--bg2); }
.host-table tr:hover { background: var(--bg3); }

/* Device type badges */
.badge {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 2px;
  font-size: 0.75rem;
  font-weight: bold;
  letter-spacing: 0.5px;
}
.badge-iot { background: #1a3a1a; color: #66ff66; border: 1px solid #33aa33; }
.badge-iot-web { background: #1a3a2a; color: #44ffaa; border: 1px solid #22aa66; }
.badge-pc { background: #1a2a3a; color: #4488ff; border: 1px solid #3366cc; }
.badge-server { background: #2a1a3a; color: #aa66ff; border: 1px solid #7744cc; }
.badge-phone { background: #3a2a1a; color: #ffaa44; border: 1px solid #cc8833; }
.badge-console { background: #3a1a1a; color: #ff6644; border: 1px solid #cc4422; }
.badge-sbc { background: #2a3a1a; color: #aaff44; border: 1px solid #88cc22; }
.badge-network { background: #1a3a3a; color: #44ffff; border: 1px solid #22cccc; }
.badge-appliance { background: #3a3a1a; color: #ffff44; border: 1px solid #cccc22; }
.badge-smart-speaker { background: #2a2a2a; color: #ff88ff; border: 1px solid #cc66cc; }
.badge-camera { background: #3a2a2a; color: #ff8888; border: 1px solid #cc5555; }
.badge-unknown, .badge-unknown-web { background: #222; color: #888; border: 1px solid #444; }

/* Port chips */
.port-chip {
  display: inline-block;
  padding: 0 4px;
  margin: 1px;
  background: var(--bg);
  border: 1px solid var(--border);
  font-size: 0.72rem;
  color: var(--cyan);
}
.port-chip.common { border-color: var(--green2); color: var(--green); }

/* Health bars */
.health-row {
  display: flex; align-items: center; gap: 8px;
  margin-bottom: 6px; font-size: 0.85rem;
}
.health-label { width: 80px; color: var(--fg-dim); text-align: right; }
.health-bar-bg {
  flex: 1; height: 16px;
  background: var(--bg);
  border: 1px solid var(--border);
  position: relative;
}
.health-bar-fill {
  height: 100%;
  transition: width 0.3s;
}
.health-bar-fill.ok { background: var(--green2); }
.health-bar-fill.warn { background: var(--amber); }
.health-bar-fill.crit { background: var(--red); }
.health-val { width: 50px; text-align: right; }

/* Diff section */
.diff-new { color: var(--green); }
.diff-new::before { content: '+ '; }
.diff-gone { color: var(--red); }
.diff-gone::before { content: '- '; }

/* Services */
.svc-up { color: var(--green); }
.svc-up::before { content: '● '; }
.svc-down { color: var(--red); }
.svc-down::before { content: '○ '; }

/* Log viewer */
.log-view {
  background: var(--bg);
  border: 1px solid var(--border);
  padding: 10px;
  max-height: 400px;
  overflow: auto;
  font-size: 0.8rem;
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--fg-dim);
}
.log-ts { color: var(--green2); }

/* History chart (simple ASCII) */
.ascii-chart {
  font-size: 0.8rem;
  line-height: 1.1;
  color: var(--green);
  overflow-x: auto;
  white-space: pre;
}

/* Footer */
.footer {
  text-align: center;
  color: var(--fg-dim);
  font-size: 0.75rem;
  padding: 20px 0;
  border-top: 1px solid var(--border);
  margin-top: 16px;
}

/* Responsive */
@media (max-width: 720px) {
  html { font-size: 13px; }
  .container { padding: 6px; }
  .host-table { display: block; overflow-x: auto; }
  .stats-grid { grid-template-columns: repeat(2, 1fr); }
  nav { gap: 2px; }
  nav a { padding: 4px 8px; font-size: 0.8rem; }
}

/* type icons */
.type-icon { margin-right: 4px; }
"""

COMMON_PORTS = {22,53,80,443,8080,8443,3389,445,139,21,25,110,143,993,995,
                5353,1883,8883,62078,9100,631,515,548,5000,8008}

# ─── Data loading ───

def load_json(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None

def get_scan_dates():
    files = sorted(glob.glob(f"{DATA_DIR}/scan-*.json"))
    return [os.path.basename(f).replace("scan-","").replace(".json","") for f in files]

def get_latest_scan():
    dates = get_scan_dates()
    if dates:
        return load_json(f"{DATA_DIR}/scan-{dates[-1]}.json")
    return None

def get_latest_health():
    files = sorted(glob.glob(f"{DATA_DIR}/health-*.json"))
    if files:
        return load_json(files[-1])
    return None

def get_log(date):
    path = f"{DATA_DIR}/scanlog-{date}.txt"
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    return ""

# ─── Device icons ───

DEVICE_ICONS = {
    "iot": "⚡", "iot-web": "🌐", "pc": "🖥", "server": "⚙",
    "phone": "📱", "console": "🎮", "sbc": "🍓", "network": "📡",
    "appliance": "🏠", "smart-speaker": "🔊", "camera": "📷",
    "unknown": "❓", "unknown-web": "❔",
}

# ─── HTML generation helpers ───

def e(s):
    return escape(str(s)) if s else ""

def page_wrap(title, body, active_page="index"):
    nav_items = [
        ("index.html", "DASHBOARD", "index"),
        ("hosts.html", "HOST INVENTORY", "hosts"),
        ("history.html", "HISTORY", "history"),
        ("log.html", "SCAN LOG", "log"),
    ]
    nav_html = "\n".join(
        f'<a href="{href}" class="{"active" if page==active_page else ""}">{label}</a>'
        for href, label, page in nav_items
    )
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NETSCAN // {e(title)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="container">
<pre class="banner">{BANNER}</pre>
<div class="header-bar">
  ── <span class="node">bc250</span> ── 192.168.3.0/24 ── AMD Zen2 + Cyan Skillfish ──
</div>
<nav>{nav_html}</nav>
{body}
<div class="footer">
<pre style="font-size:0.55rem;color:var(--fg-dim);line-height:1">{SKULL}</pre>
NETSCAN v2.0 // bc250 // generated {ts}<br>
"The Net treats censorship as damage and routes around it."
</div>
</div>
<script>{JS_SORT}</script>
</body>
</html>"""


JS_SORT = """
document.querySelectorAll('.host-table th').forEach((th, i) => {
  th.addEventListener('click', () => {
    const table = th.closest('table');
    const tbody = table.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));
    const dir = th.dataset.dir === 'asc' ? 'desc' : 'asc';
    th.dataset.dir = dir;
    rows.sort((a, b) => {
      let av = a.children[i]?.textContent.trim() || '';
      let bv = b.children[i]?.textContent.trim() || '';
      // Try numeric sort for IPs and numbers
      const an = av.split('.').map(x=>x.padStart(3,'0')).join('.');
      const bn = bv.split('.').map(x=>x.padStart(3,'0')).join('.');
      if (/^\\d/.test(av) && /^\\d/.test(bv)) { av = an; bv = bn; }
      return dir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
    });
    rows.forEach(r => tbody.appendChild(r));
  });
});
"""

def badge(device_type):
    dt = device_type or "unknown"
    icon = DEVICE_ICONS.get(dt, "❓")
    css = dt.replace("-","")
    return f'<span class="badge badge-{e(dt)}"><span class="type-icon">{icon}</span>{e(dt)}</span>'

def port_chips(ports):
    if not ports:
        return '<span style="color:var(--fg-dim)">—</span>'
    chips = []
    for p in sorted(ports, key=lambda x: x["port"]):
        cls = "port-chip common" if p["port"] in COMMON_PORTS else "port-chip"
        chips.append(f'<span class="{cls}" title="{e(p["service"])}">{p["port"]}/{e(p["proto"])}</span>')
    return " ".join(chips)

def health_bar(label, value_pct, value_text):
    try:
        pct = float(str(value_pct).rstrip('%'))
    except:
        pct = 0
    cls = "ok" if pct < 70 else ("warn" if pct < 90 else "crit")
    return f"""<div class="health-row">
  <span class="health-label">{e(label)}</span>
  <div class="health-bar-bg"><div class="health-bar-fill {cls}" style="width:{min(pct,100)}%"></div></div>
  <span class="health-val">{e(value_text)}</span>
</div>"""


# ─── Page: Dashboard (index.html) ───

def gen_dashboard():
    scan = get_latest_scan()
    health = get_latest_health()
    dates = get_scan_dates()

    if not scan:
        return page_wrap("DASHBOARD", '<div class="section"><div class="section-body">NO SCAN DATA YET</div></div>')

    hosts = scan["hosts"]
    total_ports = sum(len(h["ports"]) for h in hosts.values())
    types = {}
    for h in hosts.values():
        dt = h.get("device_type", "unknown")
        types[dt] = types.get(dt, 0) + 1

    # Stats boxes
    stats = f"""
<div class="section">
  <div class="section-title">NETWORK OVERVIEW — {e(scan.get('date',''))}</div>
  <div class="section-body">
    <div class="stats-grid">
      <div class="stat-box"><div class="stat-val">{scan['host_count']}</div><div class="stat-label">Hosts</div></div>
      <div class="stat-box"><div class="stat-val">{total_ports}</div><div class="stat-label">Open Ports</div></div>
      <div class="stat-box"><div class="stat-val">{len(types)}</div><div class="stat-label">Device Types</div></div>
      <div class="stat-box"><div class="stat-val">{len(dates)}</div><div class="stat-label">Scan Days</div></div>
    </div>
  </div>
</div>"""

    # Device type breakdown
    type_rows = ""
    for dt, cnt in sorted(types.items(), key=lambda x: -x[1]):
        bar_w = int(cnt / max(types.values()) * 100) if types else 0
        type_rows += f"""<div style="display:flex;align-items:center;gap:8px;margin:3px 0">
  {badge(dt)}
  <div style="flex:1;height:12px;background:var(--bg);border:1px solid var(--border)">
    <div style="height:100%;width:{bar_w}%;background:var(--green2)"></div>
  </div>
  <span style="width:30px;text-align:right">{cnt}</span>
</div>"""

    types_section = f"""
<div class="section">
  <div class="section-title">DEVICE TYPES</div>
  <div class="section-body">{type_rows}</div>
</div>"""

    # Network diff (last 2 scans)
    diff_html = ""
    if len(dates) >= 2:
        prev = load_json(f"{DATA_DIR}/scan-{dates[-2]}.json")
        if prev:
            prev_ips = set(prev["hosts"].keys())
            curr_ips = set(hosts.keys())
            new_ips = curr_ips - prev_ips
            gone_ips = prev_ips - curr_ips
            diff_lines = []
            if new_ips:
                for ip in sorted(new_ips):
                    h = hosts[ip]
                    diff_lines.append(f'<div class="diff-new">{e(ip)} — {badge(h.get("device_type",""))} {e(h.get("vendor_oui","") or h.get("vendor_nmap",""))}</div>')
            if gone_ips:
                for ip in sorted(gone_ips):
                    h = prev["hosts"].get(ip, {})
                    diff_lines.append(f'<div class="diff-gone">{e(ip)} — {e(h.get("vendor_oui","") or h.get("vendor_nmap",""))}</div>')
            if not new_ips and not gone_ips:
                diff_lines.append('<div style="color:var(--fg-dim)">No changes from previous scan</div>')
            diff_html = f"""
<div class="section">
  <div class="section-title">NETWORK CHANGES (vs {e(dates[-2])})</div>
  <div class="section-body">{"".join(diff_lines)}</div>
</div>"""

    # Health
    health_html = ""
    if health:
        bars = ""
        if health.get("mem_total_mb") and health.get("mem_available_mb"):
            used = health["mem_total_mb"] - health["mem_available_mb"]
            pct = round(used / health["mem_total_mb"] * 100)
            bars += health_bar("RAM", pct, f"{used}/{health['mem_total_mb']}M")
        if health.get("disk_pct"):
            bars += health_bar("Disk", health["disk_pct"], health["disk_pct"])
        if health.get("swap_used_mb") is not None:
            bars += health_bar("Swap", min(health["swap_used_mb"]/100, 100) if health["swap_used_mb"] else 0, f"{health['swap_used_mb']}M")

        temps = []
        for k, label in [("cpu_temp","CPU"),("gpu_temp","GPU"),("nvme_temp","NVMe")]:
            if health.get(k):
                temps.append(f"{label}: {health[k]}°C")
        temp_html = " &nbsp;│&nbsp; ".join(temps) if temps else ""

        svcs = ""
        for k, v in sorted(health.items()):
            if k.startswith("svc_"):
                name = k[4:]
                up = v in ("active", "running")
                svcs += f'<span class="{"svc-up" if up else "svc-down"}">{e(name)}</span>&nbsp; '

        extras = []
        if health.get("uptime"): extras.append(f"Uptime: {health['uptime']}")
        if health.get("load_avg"): extras.append(f"Load: {' '.join(health['load_avg'])}")
        if health.get("gpu_power_w"): extras.append(f"GPU Power: {health['gpu_power_w']}W")
        if health.get("nvme_wear_pct"): extras.append(f"NVMe Wear: {health['nvme_wear_pct']}")
        if health.get("oom_kills_24h",0) > 0: extras.append(f'<span style="color:var(--red)">OOM Kills (24h): {health["oom_kills_24h"]}</span>')

        health_html = f"""
<div class="section">
  <div class="section-title">SYSTEM HEALTH</div>
  <div class="section-body">
    {bars}
    <div style="margin-top:10px;font-size:0.85rem">🌡 {temp_html}</div>
    <div style="margin-top:8px;font-size:0.85rem">Services: {svcs}</div>
    <div style="margin-top:6px;font-size:0.8rem;color:var(--fg-dim)">{"  │  ".join(extras)}</div>
  </div>
</div>"""

    # Top talkers (hosts with most open ports)
    top_hosts = sorted(hosts.items(), key=lambda x: len(x[1].get("ports",[])), reverse=True)[:10]
    top_html = ""
    if any(len(h["ports"])>0 for _,h in top_hosts):
        rows = ""
        for ip, h in top_hosts:
            if not h["ports"]: continue
            rows += f'<tr><td>{e(ip)}</td><td>{badge(h.get("device_type",""))}</td><td>{port_chips(h["ports"])}</td></tr>'
        if rows:
            top_html = f"""
<div class="section">
  <div class="section-title">TOP HOSTS BY OPEN PORTS</div>
  <div class="section-body">
    <table class="host-table"><thead><tr><th>IP</th><th>Type</th><th>Open Ports</th></tr></thead><tbody>{rows}</tbody></table>
  </div>
</div>"""

    body = stats + types_section + diff_html + health_html + top_html
    return page_wrap("DASHBOARD", body, "index")


# ─── Page: Host inventory (hosts.html) ───

def gen_hosts():
    scan = get_latest_scan()
    if not scan:
        return page_wrap("HOSTS", '<div class="section"><div class="section-body">NO DATA</div></div>', "hosts")

    hosts = scan["hosts"]
    rows = ""
    for ip, h in hosts.items():
        vendor = h.get("vendor_oui","") or h.get("vendor_nmap","") or "—"
        hostname = h.get("hostname","") or "—"
        mac = h.get("mac","") or "—"
        latency = f'{h.get("latency_ms",0)}ms' if h.get("latency_ms") else "—"
        rows += f"""<tr>
  <td style="white-space:nowrap">{e(ip)}</td>
  <td style="font-size:0.75rem;white-space:nowrap">{e(mac)}</td>
  <td style="font-size:0.78rem">{e(vendor)}</td>
  <td>{e(hostname)}</td>
  <td>{badge(h.get("device_type",""))}</td>
  <td>{port_chips(h.get("ports",[]))}</td>
  <td style="text-align:right">{latency}</td>
</tr>"""

    # Summary line
    total = len(hosts)
    total_ports = sum(len(h.get("ports",[])) for h in hosts.values())
    types = {}
    for h in hosts.values():
        dt = h.get("device_type","unknown")
        types[dt] = types.get(dt,0)+1

    filter_buttons = ""
    for dt, cnt in sorted(types.items(), key=lambda x: -x[1]):
        icon = DEVICE_ICONS.get(dt, "❓")
        filter_buttons += f'<button class="badge badge-{e(dt)}" onclick="filterType(\'{e(dt)}\')" style="cursor:pointer;margin:2px">{icon} {e(dt)} ({cnt})</button> '

    body = f"""
<div class="section">
  <div class="section-title">HOST INVENTORY — {total} hosts, {total_ports} open ports — scan {e(scan.get('date',''))}</div>
  <div class="section-body">
    <div style="margin-bottom:10px">
      <button class="badge badge-unknown" onclick="filterType('all')" style="cursor:pointer;margin:2px">ALL ({total})</button>
      {filter_buttons}
    </div>
    <div style="overflow-x:auto">
    <table class="host-table" id="hostTable">
      <thead><tr>
        <th>IP Address</th><th>MAC</th><th>Vendor</th><th>Hostname</th><th>Type</th><th>Open Ports</th><th>Latency</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
    </div>
  </div>
</div>
<script>
function filterType(type) {{
  document.querySelectorAll('#hostTable tbody tr').forEach(tr => {{
    if (type === 'all') {{ tr.style.display = ''; return; }}
    const badge = tr.querySelector('.badge');
    const t = badge ? badge.textContent.trim() : '';
    tr.style.display = t.includes(type) ? '' : 'none';
  }});
}}
</script>"""
    return page_wrap("HOST INVENTORY", body, "hosts")


# ─── Page: History (history.html) ───

def gen_history():
    dates = get_scan_dates()
    if not dates:
        return page_wrap("HISTORY", '<div class="section"><div class="section-body">NO DATA</div></div>', "history")

    # Load all scans for chart
    history_data = []
    for d in dates[-30:]:  # last 30 days
        s = load_json(f"{DATA_DIR}/scan-{d}.json")
        if s:
            total_ports = sum(len(h.get("ports",[])) for h in s["hosts"].values())
            history_data.append({"date": d, "hosts": s["host_count"], "ports": total_ports})

    # ASCII bar chart
    if history_data:
        max_h = max(d["hosts"] for d in history_data) or 1
        chart_lines = []
        chart_lines.append(f"  Hosts over time (last {len(history_data)} scans)")
        chart_lines.append(f"  {'─'*60}")
        for d in history_data:
            bar_w = int(d["hosts"] / max_h * 50)
            bar = "█" * bar_w + "░" * (50 - bar_w)
            chart_lines.append(f"  {d['date'][-4:]} │{bar}│ {d['hosts']}")
        chart_lines.append(f"  {'─'*60}")
        chart_html = "\n".join(chart_lines)
    else:
        chart_html = "  No data"

    # Day-by-day changes table
    diff_rows = ""
    for i in range(len(dates)-1, 0, -1):
        curr = load_json(f"{DATA_DIR}/scan-{dates[i]}.json")
        prev = load_json(f"{DATA_DIR}/scan-{dates[i-1]}.json")
        if not curr or not prev:
            continue
        curr_ips = set(curr["hosts"].keys())
        prev_ips = set(prev["hosts"].keys())
        new_ips = curr_ips - prev_ips
        gone_ips = prev_ips - curr_ips
        new_str = ", ".join(sorted(new_ips)[:5])
        if len(new_ips)>5: new_str += f" +{len(new_ips)-5}"
        gone_str = ", ".join(sorted(gone_ips)[:5])
        if len(gone_ips)>5: gone_str += f" +{len(gone_ips)-5}"

        diff_rows += f"""<tr>
  <td>{e(dates[i])}</td>
  <td>{curr['host_count']}</td>
  <td style="color:var(--green)">{f'+{len(new_ips)}' if new_ips else '—'}</td>
  <td style="color:var(--red)">{f'-{len(gone_ips)}' if gone_ips else '—'}</td>
  <td style="font-size:0.75rem;color:var(--green)">{e(new_str) if new_str else ''}</td>
  <td style="font-size:0.75rem;color:var(--red)">{e(gone_str) if gone_str else ''}</td>
</tr>"""

    body = f"""
<div class="section">
  <div class="section-title">HOST COUNT HISTORY</div>
  <div class="section-body">
    <pre class="ascii-chart">{e(chart_html)}</pre>
  </div>
</div>
<div class="section">
  <div class="section-title">DAILY CHANGES</div>
  <div class="section-body" style="overflow-x:auto">
    <table class="host-table">
      <thead><tr><th>Date</th><th>Total</th><th>New</th><th>Gone</th><th>New IPs</th><th>Gone IPs</th></tr></thead>
      <tbody>{diff_rows if diff_rows else '<tr><td colspan="6" style="color:var(--fg-dim)">Need at least 2 scans for history</td></tr>'}</tbody>
    </table>
  </div>
</div>"""
    return page_wrap("HISTORY", body, "history")


# ─── Page: Scan log (log.html) ───

def gen_log():
    dates = get_scan_dates()
    tabs = ""
    content = ""
    for d in reversed(dates[-7:]):
        log = get_log(d)
        if not log:
            continue
        # Highlight timestamps
        highlighted = re.sub(
            r'\[([^\]]+)\]',
            r'<span class="log-ts">[\1]</span>',
            escape(log)
        )
        tabs += f'<a href="#log-{d}" onclick="showLog(\'{d}\')" style="margin-right:8px">{d}</a>'
        content += f'<div id="log-{d}" class="log-view" style="display:none">{highlighted}</div>'

    # Show latest by default
    if dates:
        latest = dates[-1]
        content = content.replace(f'id="log-{latest}" class="log-view" style="display:none"',
                                   f'id="log-{latest}" class="log-view"')

    body = f"""
<div class="section">
  <div class="section-title">SCAN LOGS</div>
  <div class="section-body">
    <div style="margin-bottom:10px">{tabs if tabs else '<span style="color:var(--fg-dim)">No logs yet</span>'}</div>
    {content}
  </div>
</div>
<script>
function showLog(d) {{
  document.querySelectorAll('.log-view').forEach(e => e.style.display='none');
  const el = document.getElementById('log-'+d);
  if (el) el.style.display = 'block';
}}
</script>"""
    return page_wrap("SCAN LOG", body, "log")


# ─── Generate all pages ───

def main():
    pages = {
        "index.html": gen_dashboard,
        "hosts.html": gen_hosts,
        "history.html": gen_history,
        "log.html": gen_log,
    }
    for fname, gen_fn in pages.items():
        html = gen_fn()
        path = os.path.join(WEB_DIR, fname)
        with open(path, "w") as f:
            f.write(html)
        size = len(html)
        print(f"  [{fname}] {size:,} bytes")

    print(f"Dashboard generated: {len(pages)} pages in {WEB_DIR}/")

if __name__ == "__main__":
    main()
