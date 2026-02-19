#!/usr/bin/env python3
"""
generate-html.py — Phrack/BBS-style network dashboard generator
v3: security page, per-host detail pages, mDNS names, port change display,
    persistent inventory stats, security scoring display.
Reads scan JSON from /opt/netscan/data/, outputs static HTML to /opt/netscan/web/
Location on bc250: /opt/netscan/generate-html.py
"""
import json, os, glob, re
from datetime import datetime, timedelta
from html import escape

DATA_DIR = "/opt/netscan/data"
WEB_DIR = "/opt/netscan/web"
os.makedirs(WEB_DIR, exist_ok=True)
os.makedirs(os.path.join(WEB_DIR, "host"), exist_ok=True)

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
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
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
.stat-val.amber { color: var(--amber); }
.stat-val.red { color: var(--red); }
.stat-val.cyan { color: var(--cyan); }
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
.port-chip.port-new { border-color: var(--green); color: var(--green); font-weight: bold; }
.port-chip.port-gone { border-color: var(--red); color: var(--red); text-decoration: line-through; opacity: 0.7; }

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

/* History chart */
.ascii-chart {
  font-size: 0.8rem;
  line-height: 1.1;
  color: var(--green);
  overflow-x: auto;
  white-space: pre;
}

/* Security score badges */
.score {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 2px;
  font-weight: bold;
  font-size: 0.8rem;
  min-width: 36px;
  text-align: center;
}
.score-ok { background: #1a3a1a; color: #33ff33; border: 1px solid #22aa22; }
.score-warn { background: #3a3a1a; color: #ffaa00; border: 1px solid #aa7700; }
.score-crit { background: #3a1a1a; color: #ff3333; border: 1px solid #cc2222; }

/* Security flags */
.flag-item {
  padding: 4px 8px;
  margin: 2px 0;
  font-size: 0.82rem;
  border-left: 3px solid var(--border);
  background: var(--bg);
}
.flag-crit { border-left-color: var(--red); }
.flag-warn { border-left-color: var(--amber); }

/* mDNS name */
.mdns-name { color: var(--cyan); font-weight: bold; }
.mdns-sub { color: var(--fg-dim); font-size: 0.75rem; }

/* Host detail page */
.detail-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
@media (max-width: 720px) { .detail-grid { grid-template-columns: 1fr; } }
.detail-kv {
  display: flex;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px solid var(--border);
  font-size: 0.85rem;
}
.detail-key { color: var(--fg-dim); min-width: 110px; flex-shrink: 0; }
.detail-val { color: var(--fg); word-break: break-all; }

/* Score meter (large) */
.score-meter {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 8px 0;
}
.score-meter .score-num {
  font-size: 2.5rem;
  font-weight: bold;
  text-shadow: var(--glow);
  line-height: 1;
}
.score-meter .score-num.ok { color: var(--green); }
.score-meter .score-num.warn { color: var(--amber); }
.score-meter .score-num.crit { color: var(--red); }
.score-meter .score-label { color: var(--fg-dim); font-size: 0.85rem; }

/* Timeline */
.timeline-item {
  padding: 6px 0 6px 16px;
  border-left: 2px solid var(--border);
  margin-left: 8px;
  font-size: 0.82rem;
}
.timeline-item.online { border-left-color: var(--green2); }
.timeline-item.offline { border-left-color: var(--red); opacity: 0.5; }
.timeline-date { color: var(--green2); font-weight: bold; }

/* mDNS service chips */
.svc-chip {
  display: inline-block;
  padding: 1px 6px;
  margin: 1px;
  background: var(--bg);
  border: 1px solid var(--border);
  font-size: 0.72rem;
  color: var(--magenta);
}

/* IP link */
.ip-link { color: var(--cyan); }
.ip-link:hover { color: var(--green); text-decoration: underline; }

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
        try:
            with open(path) as f:
                return json.load(f)
        except:
            pass
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

def load_all_scans(max_days=30):
    """Load recent scans for history tracking."""
    dates = get_scan_dates()
    scans = {}
    for d in dates[-max_days:]:
        s = load_json(f"{DATA_DIR}/scan-{d}.json")
        if s:
            scans[d] = s
    return scans

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
        ("/index.html", "DASHBOARD", "index"),
        ("/hosts.html", "HOSTS", "hosts"),
        ("/presence.html", "PRESENCE", "presence"),
        ("/lkml.html", "LKML", "lkml"),
        ("/security.html", "SECURITY", "security"),
        ("/history.html", "HISTORY", "history"),
        ("/log.html", "LOG", "log"),
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
NETSCAN v3.0 // bc250 // generated {ts}<br>
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
    return f'<span class="badge badge-{e(dt)}"><span class="type-icon">{icon}</span>{e(dt)}</span>'

def score_badge(score):
    s = int(score) if score is not None else 100
    if s >= 80:
        cls = "score-ok"
    elif s >= 50:
        cls = "score-warn"
    else:
        cls = "score-crit"
    return f'<span class="score {cls}">{s}</span>'

def best_name(h):
    """Best display name for a host."""
    return h.get("mdns_name") or h.get("hostname") or h.get("vendor_oui") or h.get("vendor_nmap") or ""

def port_chips(ports, port_changes=None):
    if not ports and not port_changes:
        return '<span style="color:var(--fg-dim)">—</span>'
    chips = []
    # Current ports
    new_ports = set()
    if port_changes:
        new_ports = {(p["port"], p["proto"]) for p in port_changes.get("new", [])}
    for p in sorted(ports or [], key=lambda x: x["port"]):
        if (p["port"], p["proto"]) in new_ports:
            chips.append(f'<span class="port-chip port-new" title="{e(p["service"])} (NEW)">+{p["port"]}/{e(p["proto"])}</span>')
        else:
            cls = "port-chip common" if p["port"] in COMMON_PORTS else "port-chip"
            chips.append(f'<span class="{cls}" title="{e(p["service"])}">{p["port"]}/{e(p["proto"])}</span>')
    # Gone ports
    if port_changes:
        for p in port_changes.get("gone", []):
            chips.append(f'<span class="port-chip port-gone" title="CLOSED">-{p["port"]}/{e(p["proto"])}</span>')
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

def ip_link(ip):
    """Clickable IP that links to host detail page."""
    safe = ip.replace(".", "-")
    return f'<a href="/host/{safe}.html" class="ip-link">{e(ip)}</a>'

def format_date(d):
    """Format YYYYMMDD to more readable form."""
    try:
        return datetime.strptime(str(d), "%Y%m%d").strftime("%d %b %Y")
    except:
        return str(d)

def short_date(d):
    """Format YYYYMMDD to short form."""
    try:
        return datetime.strptime(str(d), "%Y%m%d").strftime("%d %b")
    except:
        return str(d)


# ─── Page: Dashboard (index.html) ───

def gen_dashboard(all_scans):
    scan = get_latest_scan()
    health = get_latest_health()
    dates = get_scan_dates()

    if not scan:
        return page_wrap("DASHBOARD", '<div class="section"><div class="section-body">NO SCAN DATA YET</div></div>')

    hosts = scan["hosts"]
    total_ports = sum(len(h.get("ports",[])) for h in hosts.values())
    types = {}
    for h in hosts.values():
        dt = h.get("device_type", "unknown")
        types[dt] = types.get(dt, 0) + 1

    sec = scan.get("security", {})
    mdns_count = scan.get("mdns_devices", 0)
    inv_total = scan.get("inventory_total", 0)

    # Stats boxes
    sec_cls = "red" if sec.get("critical", 0) > 0 else ("amber" if sec.get("warning", 0) > 0 else "")
    stats = f"""
<div class="section">
  <div class="section-title">NETWORK OVERVIEW — {e(scan.get('date',''))}</div>
  <div class="section-body">
    <div class="stats-grid">
      <div class="stat-box"><div class="stat-val">{scan['host_count']}</div><div class="stat-label">Hosts</div></div>
      <div class="stat-box"><div class="stat-val">{total_ports}</div><div class="stat-label">Open Ports</div></div>
      <div class="stat-box"><div class="stat-val">{len(types)}</div><div class="stat-label">Device Types</div></div>
      <div class="stat-box"><div class="stat-val cyan">{mdns_count}</div><div class="stat-label">mDNS Named</div></div>
      <div class="stat-box"><div class="stat-val {sec_cls}">{sec.get('avg_score', '?')}</div><div class="stat-label">Security Avg</div></div>
      <div class="stat-box"><div class="stat-val">{inv_total}</div><div class="stat-label">Inventory Total</div></div>
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
                    name = best_name(h)
                    name_str = f" — {e(name)}" if name else ""
                    diff_lines.append(f'<div class="diff-new">{ip_link(ip)}{name_str} {badge(h.get("device_type",""))}</div>')
            if gone_ips:
                for ip in sorted(gone_ips):
                    h = prev["hosts"].get(ip, {})
                    name = best_name(h)
                    name_str = f" — {e(name)}" if name else ""
                    diff_lines.append(f'<div class="diff-gone">{e(ip)}{name_str}</div>')
            if not new_ips and not gone_ips:
                diff_lines.append('<div style="color:var(--fg-dim)">No host changes from previous scan</div>')
            diff_html = f"""
<div class="section">
  <div class="section-title">NETWORK CHANGES (vs {e(dates[-2])})</div>
  <div class="section-body">{"".join(diff_lines)}</div>
</div>"""

    # Port changes
    pc = scan.get("port_changes", {})
    port_change_html = ""
    if pc.get("hosts_changed", 0) > 0:
        pc_lines = []
        pc_lines.append(f'<div style="margin-bottom:8px;color:var(--fg)">+{pc["new_ports"]} new ports, -{pc["gone_ports"]} closed — {pc["hosts_changed"]} hosts changed</div>')
        # Show individual host port changes
        for ip, h in sorted(hosts.items()):
            pch = h.get("port_changes")
            if not pch:
                continue
            name = best_name(h)
            new_str = ", ".join(f'+{p["port"]}/{p["proto"]}' for p in pch.get("new",[]))
            gone_str = ", ".join(f'-{p["port"]}/{p["proto"]}' for p in pch.get("gone",[]))
            changes = []
            if new_str: changes.append(f'<span style="color:var(--green)">{new_str}</span>')
            if gone_str: changes.append(f'<span style="color:var(--red)">{gone_str}</span>')
            name_str = f' <span style="color:var(--fg-dim)">({e(name)})</span>' if name else ""
            pc_lines.append(f'<div style="margin:3px 0">{ip_link(ip)}{name_str}: {" ".join(changes)}</div>')
        port_change_html = f"""
<div class="section">
  <div class="section-title">PORT CHANGES</div>
  <div class="section-body">{"".join(pc_lines)}</div>
</div>"""

    # Security summary
    security_html = ""
    if sec:
        issues = []
        for ip, h in sorted(hosts.items(), key=lambda x: x[1].get("security_score", 100)):
            flags = h.get("security_flags", [])
            if not flags:
                continue
            score = h.get("security_score", 100)
            if score >= 80:
                continue
            name = best_name(h)
            name_str = f" — {e(name)}" if name else ""
            flag_html = "".join(
                f'<div class="flag-item {"flag-crit" if score < 50 else "flag-warn"}">{e(f)}</div>'
                for f in flags[:3]
            )
            extra = f' <span style="color:var(--fg-dim)">+{len(flags)-3} more</span>' if len(flags) > 3 else ""
            issues.append(f'<div style="margin:6px 0">{ip_link(ip)}{name_str} {badge(h.get("device_type",""))} {score_badge(score)}{flag_html}{extra}</div>')

        if issues:
            security_html = f"""
<div class="section">
  <div class="section-title">SECURITY ALERTS — <a href="/security.html" style="color:var(--amber)">view full report →</a></div>
  <div class="section-body">
    <div style="margin-bottom:8px">
      🔴 Critical: {sec.get('critical',0)} &nbsp;│&nbsp;
      🟡 Warning: {sec.get('warning',0)} &nbsp;│&nbsp;
      🟢 OK: {sec.get('ok',0)} &nbsp;│&nbsp;
      Average: {score_badge(sec.get('avg_score',100))}
    </div>
    {"".join(issues[:8])}
    {'<div style="color:var(--fg-dim);margin-top:6px">...more on <a href="/security.html">security page</a></div>' if len(issues) > 8 else ""}
  </div>
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

    # Top talkers
    top_hosts = sorted(hosts.items(), key=lambda x: len(x[1].get("ports",[])), reverse=True)[:10]
    top_html = ""
    if any(len(h.get("ports",[]))>0 for _,h in top_hosts):
        rows = ""
        for ip, h in top_hosts:
            if not h.get("ports"): continue
            name = best_name(h)
            rows += f'<tr><td>{ip_link(ip)}</td><td>{e(name) if name else "—"}</td><td>{badge(h.get("device_type",""))}</td><td>{score_badge(h.get("security_score",100))}</td><td>{port_chips(h["ports"], h.get("port_changes"))}</td></tr>'
        if rows:
            top_html = f"""
<div class="section">
  <div class="section-title">TOP HOSTS BY OPEN PORTS</div>
  <div class="section-body">
    <table class="host-table"><thead><tr><th>IP</th><th>Name</th><th>Type</th><th>Score</th><th>Open Ports</th></tr></thead><tbody>{rows}</tbody></table>
  </div>
</div>"""

    # Presence widget (who's home)
    presence_html = ""
    pstate = load_json(f"{DATA_DIR}/presence-state.json") or {}
    pphones = load_json(f"{DATA_DIR}/phones.json") or {}
    ptracked = {mac: info for mac, info in pphones.items()
                if isinstance(info, dict) and info.get("track", True) and not mac.startswith("__")}
    if ptracked:
        plines = []
        for mac, info in sorted(ptracked.items(), key=lambda x: x[1].get("name", "")):
            name = e(info.get("name", mac[:8]))
            s = pstate.get(mac, {})
            status = s.get("status", "unknown")
            if status == "home":
                plines.append(f'<span style="color:var(--green)">🏠 {name}</span>')
            elif status == "away":
                plines.append(f'<span style="color:var(--fg-dim)">👋 {name}</span>')
            else:
                plines.append(f'<span style="color:var(--fg-dim)">❓ {name}</span>')
        presence_html = f"""
<div class="section">
  <div class="section-title">WHO'S HOME — <a href="/presence.html" style="color:var(--cyan)">presence tracker →</a></div>
  <div class="section-body">
    <div style="display:flex;flex-wrap:wrap;gap:16px;font-size:1.05rem">{"".join(plines)}</div>
  </div>
</div>"""

    body = stats + types_section + presence_html + diff_html + port_change_html + security_html + health_html + top_html
    return page_wrap("DASHBOARD", body, "index")


# ─── Page: Host inventory (hosts.html) ───

def gen_hosts(scan):
    if not scan:
        return page_wrap("HOSTS", '<div class="section"><div class="section-body">NO DATA</div></div>', "hosts")

    hosts = scan["hosts"]
    rows = ""
    for ip, h in hosts.items():
        name = best_name(h)
        mac = h.get("mac","") or "—"
        latency = f'{h.get("latency_ms",0)}ms' if h.get("latency_ms") else "—"
        first_seen = short_date(h.get("first_seen","")) if h.get("first_seen") else "—"
        rows += f"""<tr>
  <td style="white-space:nowrap">{ip_link(ip)}</td>
  <td class="mdns-name">{e(name) if name else '<span style="color:var(--fg-dim)">—</span>'}</td>
  <td style="font-size:0.75rem;white-space:nowrap">{e(mac)}</td>
  <td>{badge(h.get("device_type",""))}</td>
  <td style="text-align:center">{score_badge(h.get("security_score",100))}</td>
  <td>{port_chips(h.get("ports",[]), h.get("port_changes"))}</td>
  <td style="font-size:0.78rem;white-space:nowrap">{first_seen}</td>
  <td style="text-align:right">{latency}</td>
</tr>"""

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

    # Security filter buttons
    sec_counts = {"crit": 0, "warn": 0, "ok": 0}
    for h in hosts.values():
        s = h.get("security_score", 100)
        if s < 50: sec_counts["crit"] += 1
        elif s < 80: sec_counts["warn"] += 1
        else: sec_counts["ok"] += 1

    body = f"""
<div class="section">
  <div class="section-title">HOST INVENTORY — {total} hosts, {total_ports} open ports — scan {e(scan.get('date',''))}</div>
  <div class="section-body">
    <div style="margin-bottom:6px">
      <button class="badge badge-unknown" onclick="filterType('all')" style="cursor:pointer;margin:2px">ALL ({total})</button>
      {filter_buttons}
    </div>
    <div style="margin-bottom:10px">
      <button class="score score-crit" onclick="filterScore(0,49)" style="cursor:pointer;margin:2px">🔴 Critical ({sec_counts["crit"]})</button>
      <button class="score score-warn" onclick="filterScore(50,79)" style="cursor:pointer;margin:2px">🟡 Warning ({sec_counts["warn"]})</button>
      <button class="score score-ok" onclick="filterScore(80,100)" style="cursor:pointer;margin:2px">🟢 OK ({sec_counts["ok"]})</button>
      <button class="badge badge-unknown" onclick="filterType('all')" style="cursor:pointer;margin:2px">Reset</button>
    </div>
    <div style="overflow-x:auto">
    <table class="host-table" id="hostTable">
      <thead><tr>
        <th>IP Address</th><th>Name</th><th>MAC</th><th>Type</th><th>Score</th><th>Open Ports</th><th>Since</th><th>Latency</th>
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
function filterScore(min, max) {{
  document.querySelectorAll('#hostTable tbody tr').forEach(tr => {{
    const scoreEl = tr.querySelector('.score');
    const s = scoreEl ? parseInt(scoreEl.textContent) : 100;
    tr.style.display = (s >= min && s <= max) ? '' : 'none';
  }});
}}
</script>"""
    return page_wrap("HOST INVENTORY", body, "hosts")


# ─── Page: Security (security.html) ───

def gen_security(scan):
    if not scan:
        return page_wrap("SECURITY", '<div class="section"><div class="section-body">NO DATA</div></div>', "security")

    hosts = scan["hosts"]
    sec = scan.get("security", {})

    # Overview
    avg = sec.get("avg_score", 100)
    avg_cls = "ok" if avg >= 80 else ("warn" if avg >= 50 else "crit")

    overview = f"""
<div class="section">
  <div class="section-title">SECURITY OVERVIEW</div>
  <div class="section-body">
    <div class="score-meter">
      <div class="score-num {avg_cls}">{avg}</div>
      <div class="score-label">/ 100<br>Average Network Score</div>
    </div>
    <div class="stats-grid" style="margin-top:12px">
      <div class="stat-box"><div class="stat-val red">{sec.get('critical',0)}</div><div class="stat-label">Critical (&lt;50)</div></div>
      <div class="stat-box"><div class="stat-val amber">{sec.get('warning',0)}</div><div class="stat-label">Warning (50-79)</div></div>
      <div class="stat-box"><div class="stat-val">{sec.get('ok',0)}</div><div class="stat-label">OK (80+)</div></div>
      <div class="stat-box"><div class="stat-val">{scan['host_count']}</div><div class="stat-label">Total Hosts</div></div>
    </div>
  </div>
</div>"""

    # Hosts sorted by score (worst first) — only show those with flags
    flagged = [(ip, h) for ip, h in hosts.items() if h.get("security_flags")]
    flagged.sort(key=lambda x: x[1].get("security_score", 100))

    critical_html = ""
    warning_html = ""
    crit_rows = []
    warn_rows = []

    for ip, h in flagged:
        score = h.get("security_score", 100)
        name = best_name(h)
        name_str = f" — {e(name)}" if name else ""
        flags_html = ""
        for f in h.get("security_flags", []):
            cls = "flag-crit" if score < 50 else "flag-warn"
            flags_html += f'<div class="flag-item {cls}">{e(f)}</div>'
        block = f"""<div style="margin:10px 0;padding:8px;border:1px solid var(--border);background:var(--bg)">
  <div style="margin-bottom:6px">{ip_link(ip)}{name_str} {badge(h.get("device_type",""))} {score_badge(score)}</div>
  {flags_html}
</div>"""
        if score < 50:
            crit_rows.append(block)
        elif score < 80:
            warn_rows.append(block)

    if crit_rows:
        critical_html = f"""
<div class="section">
  <div class="section-title">🔴 CRITICAL ISSUES (score &lt; 50)</div>
  <div class="section-body">{"".join(crit_rows)}</div>
</div>"""

    if warn_rows:
        warning_html = f"""
<div class="section">
  <div class="section-title">🟡 WARNINGS (score 50-79)</div>
  <div class="section-body">{"".join(warn_rows)}</div>
</div>"""

    # Full host table sorted by score
    rows = ""
    all_sorted = sorted(hosts.items(), key=lambda x: x[1].get("security_score", 100))
    for ip, h in all_sorted:
        name = best_name(h)
        score = h.get("security_score", 100)
        flags = h.get("security_flags", [])
        flag_str = "; ".join(flags[:3]) if flags else "—"
        if len(flags) > 3:
            flag_str += f" +{len(flags)-3}"
        rows += f"""<tr>
  <td>{ip_link(ip)}</td>
  <td>{e(name) if name else "—"}</td>
  <td>{badge(h.get("device_type",""))}</td>
  <td style="text-align:center">{score_badge(score)}</td>
  <td style="font-size:0.78rem">{e(flag_str)}</td>
  <td>{port_chips(h.get("ports",[]))}</td>
</tr>"""

    table_html = f"""
<div class="section">
  <div class="section-title">ALL HOSTS BY SECURITY SCORE</div>
  <div class="section-body" style="overflow-x:auto">
    <table class="host-table" id="secTable">
      <thead><tr><th>IP</th><th>Name</th><th>Type</th><th>Score</th><th>Issues</th><th>Ports</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</div>"""

    # Recommendations
    recs = []
    cam_http = sum(1 for h in hosts.values() if h.get("device_type") == "camera" and any(p["port"]==80 for p in h.get("ports",[])))
    if cam_http:
        recs.append(f"📷 {cam_http} camera(s) with unencrypted HTTP — consider HTTPS-only or VLAN isolation")
    telnet_count = sum(1 for h in hosts.values() if any(p["port"]==23 for p in h.get("ports",[])))
    if telnet_count:
        recs.append(f"⚠️ {telnet_count} host(s) with Telnet — disable and use SSH instead")
    rdp_count = sum(1 for h in hosts.values() if any(p["port"]==3389 for p in h.get("ports",[])))
    if rdp_count:
        recs.append(f"🖥 {rdp_count} host(s) with RDP exposed — restrict to VPN/internal only")
    unknown_svc = sum(1 for h in hosts.values() if h.get("device_type") in ("unknown","unknown-web") and len(h.get("ports",[])) >= 3)
    if unknown_svc:
        recs.append(f"❓ {unknown_svc} unknown device(s) with multiple services — identify and classify")
    if not recs:
        recs.append("✅ No critical recommendations — network looks good!")

    recs_html = f"""
<div class="section">
  <div class="section-title">RECOMMENDATIONS</div>
  <div class="section-body">
    {"".join(f'<div style="padding:4px 0">{r}</div>' for r in recs)}
  </div>
</div>"""

    body = overview + critical_html + warning_html + table_html + recs_html
    return page_wrap("SECURITY REPORT", body, "security")


# ─── Page: Host detail (host/192-168-3-X.html) ───

def gen_host_detail(ip, h, all_scans):
    safe_ip = ip.replace(".", "-")
    name = best_name(h)
    title_name = f" — {name}" if name else ""

    # Info section
    kv_items = [
        ("IP Address", ip),
        ("MAC Address", h.get("mac","") or "—"),
        ("Vendor (OUI)", h.get("vendor_oui","") or "—"),
        ("Vendor (nmap)", h.get("vendor_nmap","") or "—"),
        ("Hostname", h.get("hostname","") or "—"),
        ("mDNS Name", f'<span class="mdns-name">{e(h.get("mdns_name",""))}</span>' if h.get("mdns_name") else "—"),
        ("Device Type", badge(h.get("device_type",""))),
        ("Latency", f'{h.get("latency_ms",0)}ms' if h.get("latency_ms") else "—"),
        ("First Seen", format_date(h.get("first_seen","")) if h.get("first_seen") else "—"),
        ("Last Seen", format_date(h.get("last_seen","")) if h.get("last_seen") else "—"),
        ("Days Tracked", str(h.get("days_tracked","1"))),
    ]
    info_html = ""
    for key, val in kv_items:
        info_html += f'<div class="detail-kv"><span class="detail-key">{key}</span><span class="detail-val">{val}</span></div>'

    # Security section
    score = h.get("security_score", 100)
    score_cls = "ok" if score >= 80 else ("warn" if score >= 50 else "crit")
    flags = h.get("security_flags", [])
    flags_html = ""
    if flags:
        for f in flags:
            cls = "flag-crit" if score < 50 else "flag-warn"
            flags_html += f'<div class="flag-item {cls}">{e(f)}</div>'
    else:
        flags_html = '<div style="color:var(--green)">✅ No security issues detected</div>'

    security_sec = f"""
<div class="section">
  <div class="section-title">SECURITY</div>
  <div class="section-body">
    <div class="score-meter">
      <div class="score-num {score_cls}">{score}</div>
      <div class="score-label">/ 100</div>
    </div>
    {flags_html}
  </div>
</div>"""

    # mDNS services
    mdns_html = ""
    mdns_svcs = h.get("mdns_services", [])
    if mdns_svcs:
        chips = " ".join(f'<span class="svc-chip">{e(s)}</span>' for s in mdns_svcs)
        mdns_html = f"""
<div class="section">
  <div class="section-title">mDNS SERVICES</div>
  <div class="section-body">{chips}</div>
</div>"""

    # Open ports
    ports = h.get("ports", [])
    port_rows = ""
    new_ports = set()
    pch = h.get("port_changes")
    if pch:
        new_ports = {(p["port"], p["proto"]) for p in pch.get("new", [])}
    for p in sorted(ports, key=lambda x: x["port"]):
        is_new = (p["port"], p["proto"]) in new_ports
        new_tag = ' <span style="color:var(--green);font-weight:bold">NEW</span>' if is_new else ""
        cls = "port-chip common" if p["port"] in COMMON_PORTS else "port-chip"
        port_rows += f'<tr><td><span class="{cls}">{p["port"]}</span></td><td>{e(p["proto"])}</td><td>{e(p["service"])}</td><td>{new_tag}</td></tr>'
    # Add gone ports
    if pch:
        for p in pch.get("gone", []):
            port_rows += f'<tr style="opacity:0.5"><td><span class="port-chip port-gone">{p["port"]}</span></td><td>{e(p["proto"])}</td><td>—</td><td><span style="color:var(--red)">CLOSED</span></td></tr>'

    ports_html = ""
    if port_rows:
        ports_html = f"""
<div class="section">
  <div class="section-title">OPEN PORTS ({len(ports)})</div>
  <div class="section-body">
    <table class="host-table"><thead><tr><th>Port</th><th>Proto</th><th>Service</th><th>Status</th></tr></thead>
    <tbody>{port_rows}</tbody></table>
  </div>
</div>"""
    else:
        ports_html = f"""
<div class="section">
  <div class="section-title">OPEN PORTS</div>
  <div class="section-body" style="color:var(--fg-dim)">No open ports detected</div>
</div>"""

    # History timeline
    timeline_items = []
    sorted_dates = sorted(all_scans.keys())
    for d in reversed(sorted_dates):
        s = all_scans[d]
        if ip in s.get("hosts", {}):
            sh = s["hosts"][ip]
            port_count = len(sh.get("ports", []))
            port_list = ", ".join(str(p["port"]) for p in sorted(sh.get("ports",[]), key=lambda x: x["port"])[:8])
            if len(sh.get("ports",[])) > 8:
                port_list += "..."
            sc = sh.get("security_score", "?")
            timeline_items.append(f'<div class="timeline-item online"><span class="timeline-date">{e(d)}</span> — ● online — {port_count} ports [{port_list}] — score {sc}</div>')
        else:
            timeline_items.append(f'<div class="timeline-item offline"><span class="timeline-date">{e(d)}</span> — ○ offline</div>')

    history_html = ""
    if timeline_items:
        history_html = f"""
<div class="section">
  <div class="section-title">SCAN HISTORY (last {len(sorted_dates)} scans)</div>
  <div class="section-body">{"".join(timeline_items)}</div>
</div>"""

    # Header
    header = f"""
<div class="section">
  <div class="section-title">HOST DETAIL: {e(ip)}{e(title_name)}</div>
  <div class="section-body">
    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
      <span style="font-size:1.4rem;color:var(--cyan);font-weight:bold">{e(ip)}</span>
      {badge(h.get("device_type",""))}
      {score_badge(score)}
      {f'<span class="mdns-name" style="font-size:1.1rem">{e(name)}</span>' if name else ""}
    </div>
  </div>
</div>"""

    body = header + f'<div class="detail-grid"><div>{info_html}</div><div>{security_sec}</div></div>' + mdns_html + ports_html + history_html

    # Wrap in info section
    body = header + f"""
<div class="detail-grid">
  <div class="section">
    <div class="section-title">DEVICE INFO</div>
    <div class="section-body">{info_html}</div>
  </div>
  {security_sec}
</div>
""" + mdns_html + ports_html + history_html

    return page_wrap(f"HOST {ip}", body, "hosts")


# ─── Page: Presence tracker (presence.html) ───

def gen_presence():
    """Generate presence tracking page showing who's home/away + event log."""
    phones = load_json(f"{DATA_DIR}/phones.json") or {}
    state = load_json(f"{DATA_DIR}/presence-state.json") or {}
    events = load_json(f"{DATA_DIR}/presence-log.json") or []

    tracked = {mac: info for mac, info in phones.items()
               if isinstance(info, dict) and info.get("track", True) and not mac.startswith("__")}

    now = datetime.now()

    if not tracked:
        body = """
<div class="section">
  <div class="section-title">PHONE PRESENCE TRACKER</div>
  <div class="section-body">
    <div style="color:var(--fg-dim);text-align:center;padding:40px 0">
      <div style="font-size:3rem;margin-bottom:16px">📱</div>
      <div style="font-size:1.1rem;color:var(--amber)">No phones configured yet</div>
      <div style="margin-top:12px;max-width:500px;margin-left:auto;margin-right:auto;text-align:left">
        <p style="color:var(--fg)">Phones are auto-detected from network scans, or add manually:</p>
        <pre style="background:var(--bg);padding:12px;border:1px solid var(--border);margin-top:8px;color:var(--green);font-size:0.85rem">
# Edit /opt/netscan/data/phones.json
{
  "AA:BB:CC:DD:EE:FF": {
    "name": "My Phone",
    "track": true
  }
}</pre>
        <p style="color:var(--fg-dim);margin-top:8px;font-size:0.85rem">
          💡 Find your phone's WiFi MAC in Settings → Wi-Fi → (i)
          <br>Modern phones randomize MACs — use the "private address" for your home network.
        </p>
      </div>
    </div>
  </div>
</div>"""
        return page_wrap("PRESENCE", body, "presence")

    # ── Status cards ──
    home_cards = []
    away_cards = []
    for mac, info in sorted(tracked.items(), key=lambda x: x[1].get("name", "")):
        name = e(info.get("name", mac))
        s = state.get(mac, {})
        status = s.get("status", "unknown")
        last_seen_str = s.get("last_seen", "")
        last_change_str = s.get("last_change", "")
        last_ip = s.get("last_ip", "—")

        try:
            last_seen_dt = datetime.fromisoformat(last_seen_str) if last_seen_str else None
        except:
            last_seen_dt = None
        try:
            last_change_dt = datetime.fromisoformat(last_change_str) if last_change_str else None
        except:
            last_change_dt = None

        if status == "home":
            icon = "🏠"
            status_text = "HOME"
            status_color = "var(--green)"
            border_color = "var(--green2)"
            duration = ""
            if last_change_dt:
                td = now - last_change_dt
                total_min = int(td.total_seconds() / 60)
                if total_min < 60:
                    duration = f"{total_min}m"
                elif total_min < 1440:
                    duration = f"{total_min // 60}h {total_min % 60}m"
                else:
                    days = total_min // 1440
                    hours = (total_min % 1440) // 60
                    duration = f"{days}d {hours}h"
            seen_str = last_seen_dt.strftime("%H:%M") if last_seen_dt else "—"
            card = f"""<div style="border:1px solid {border_color};background:var(--bg3);padding:16px;min-width:200px;flex:1;max-width:300px">
  <div style="font-size:2rem;margin-bottom:6px">{icon}</div>
  <div style="font-size:1.1rem;color:{status_color};font-weight:bold">{name}</div>
  <div style="font-size:0.9rem;color:{status_color};margin:4px 0">{status_text}{' — ' + duration if duration else ''}</div>
  <div style="font-size:0.8rem;color:var(--fg-dim)">IP: {e(last_ip)}</div>
  <div style="font-size:0.8rem;color:var(--fg-dim)">Last seen: {seen_str}</div>
  <div style="font-size:0.75rem;color:var(--fg-dim);margin-top:4px">{e(mac)}</div>
</div>"""
            home_cards.append(card)
        else:
            icon = "👋"
            status_text = "AWAY"
            status_color = "var(--fg-dim)"
            border_color = "var(--border)"
            duration = ""
            if last_change_dt:
                td = now - last_change_dt
                total_min = int(td.total_seconds() / 60)
                if total_min < 60:
                    duration = f"{total_min}m"
                elif total_min < 1440:
                    duration = f"{total_min // 60}h {total_min % 60}m"
                else:
                    days = total_min // 1440
                    hours = (total_min % 1440) // 60
                    duration = f"{days}d {hours}h"
            seen_str = last_seen_dt.strftime("%H:%M") if last_seen_dt else "never"
            card = f"""<div style="border:1px solid {border_color};background:var(--bg2);padding:16px;min-width:200px;flex:1;max-width:300px;opacity:0.6">
  <div style="font-size:2rem;margin-bottom:6px">{icon}</div>
  <div style="font-size:1.1rem;color:{status_color}">{name}</div>
  <div style="font-size:0.9rem;color:{status_color};margin:4px 0">{status_text}{' — ' + duration if duration else ''}</div>
  <div style="font-size:0.8rem;color:var(--fg-dim)">Last IP: {e(last_ip)}</div>
  <div style="font-size:0.8rem;color:var(--fg-dim)">Last seen: {seen_str}</div>
  <div style="font-size:0.75rem;color:var(--fg-dim);margin-top:4px">{e(mac)}</div>
</div>"""
            away_cards.append(card)

    all_cards = home_cards + away_cards
    cards_html = f"""
<div class="section">
  <div class="section-title">WHO'S HOME — {len(home_cards)} home, {len(away_cards)} away</div>
  <div class="section-body">
    <div style="display:flex;flex-wrap:wrap;gap:12px">
      {"".join(all_cards)}
    </div>
  </div>
</div>"""

    # ── Event log ──
    event_rows = ""
    shown_events = [ev for ev in events if ev.get("event") not in ("baseline_home", "baseline_away")][:100]
    if shown_events:
        for ev in shown_events:
            ts = ev.get("ts", "")
            try:
                ts_dt = datetime.fromisoformat(ts)
                ts_fmt = ts_dt.strftime("%d %b %H:%M")
            except:
                ts_fmt = ts[:16] if ts else "—"
            ev_name = e(ev.get("name", ev.get("mac", "?")))
            ev_type = ev.get("event", "?")
            ev_ip = ev.get("ip", "—")

            if ev_type == "arrived":
                icon = "🏠"
                label = "ARRIVED"
                color = "var(--green)"
                away_min = ev.get("away_min", 0)
                extra = f"away {away_min // 60}h {away_min % 60}m" if away_min >= 60 else f"away {away_min}m"
            elif ev_type == "left":
                icon = "👋"
                label = "LEFT"
                color = "var(--red)"
                home_min = ev.get("home_min", 0)
                extra = f"was home {home_min // 60}h {home_min % 60}m" if home_min >= 60 else f"was home {home_min}m"
            else:
                icon = "📱"
                label = ev_type.upper()
                color = "var(--fg-dim)"
                extra = ""

            event_rows += f"""<tr>
  <td style="white-space:nowrap;color:var(--fg-dim)">{ts_fmt}</td>
  <td style="color:{color}">{icon} {label}</td>
  <td>{ev_name}</td>
  <td style="color:var(--fg-dim)">{e(ev_ip)}</td>
  <td style="color:var(--fg-dim);font-size:0.85rem">{extra}</td>
</tr>"""
    else:
        event_rows = '<tr><td colspan="5" style="text-align:center;color:var(--fg-dim);padding:20px">No events recorded yet — waiting for arrivals and departures</td></tr>'

    events_html = f"""
<div class="section">
  <div class="section-title">EVENT LOG — last {len(shown_events)} events</div>
  <div class="section-body">
    <table class="host-table">
      <thead><tr><th>Time</th><th>Event</th><th>Phone</th><th>IP</th><th>Details</th></tr></thead>
      <tbody>{event_rows}</tbody>
    </table>
  </div>
</div>"""

    # ── Config info ──
    config_html = f"""
<div class="section">
  <div class="section-title">TRACKING CONFIG</div>
  <div class="section-body" style="font-size:0.85rem;color:var(--fg-dim)">
    📱 Tracked phones: {len(tracked)} &nbsp;│&nbsp;
    ⏱ Scan interval: 5 min &nbsp;│&nbsp;
    🎚 Threshold: 30 min &nbsp;│&nbsp;
    📄 Config: /opt/netscan/data/phones.json
  </div>
</div>"""

    body = cards_html + events_html + config_html
    return page_wrap("PRESENCE", body, "presence")


# ─── Page: LKML Digest (lkml.html) ───

def gen_lkml():
    """Generate linux-media mailing list digest page."""
    lkml_dir = os.path.join(DATA_DIR, "lkml")

    # Load all available digests (newest first)
    digests = []
    if os.path.isdir(lkml_dir):
        for fn in sorted(os.listdir(lkml_dir), reverse=True):
            if fn.startswith("digest-") and fn.endswith(".json"):
                d = load_json(os.path.join(lkml_dir, fn))
                if d:
                    digests.append(d)

    if not digests:
        body = """
<div class="section">
  <div class="section-title">LINUX-MEDIA MAILING LIST DIGEST</div>
  <div class="section-body">
    <div style="color:var(--fg-dim);text-align:center;padding:40px 0">
      <div style="font-size:3rem;margin-bottom:16px">📡</div>
      <div style="font-size:1.1rem;color:var(--amber)">No digests generated yet</div>
      <div style="margin-top:12px;color:var(--fg-dim);font-size:0.9rem">
        Daily digest runs at 4:00 AM — summarizing linux-media mailing list<br>
        camera drivers, V4L2, ISP, MIPI sensors, libcamera, UVC<br>
        Powered by local LLM via Ollama
      </div>
    </div>
  </div>
</div>"""
        return page_wrap("LKML DIGEST", body, "lkml")

    # Latest digest — show full bulletin
    latest = digests[0]
    bulletin_raw = latest.get("bulletin", latest.get("bulletin_sent", ""))
    # Convert plain text to HTML (preserve newlines, escape HTML)
    bulletin_html = e(bulletin_raw).replace("\n", "<br>")

    # Stats
    stats_parts = []
    stats_parts.append(f'{latest.get("total_messages", "?")} messages')
    stats_parts.append(f'{latest.get("total_threads", "?")} threads')
    stats_parts.append(f'{latest.get("camera_threads", "?")} camera-relevant')
    if latest.get("ollama_model"):
        stats_parts.append(f'Model: {e(latest["ollama_model"])}')
    if latest.get("ollama_time_s"):
        stats_parts.append(f'LLM: {latest["ollama_time_s"]}s')
    stats_line = " &nbsp;│&nbsp; ".join(stats_parts)

    latest_html = f"""
<div class="section">
  <div class="section-title">📡 LATEST DIGEST — {e(latest.get('date', '?'))}</div>
  <div class="section-body">
    <div style="font-family:monospace;white-space:pre-wrap;line-height:1.6;font-size:0.9rem;color:var(--fg)">{bulletin_html}</div>
    <div style="margin-top:16px;padding-top:10px;border-top:1px solid var(--border);font-size:0.8rem;color:var(--fg-dim)">
      {stats_line}<br>
      Generated: {e(latest.get('generated', '?'))}
    </div>
  </div>
</div>"""

    # Top threads: detailed analysis cards
    top_threads = latest.get("top_threads", [])
    detail_cards = ""
    if top_threads:
        for i, t in enumerate(top_threads[:15]):
            subj = e(t.get("subject", "?"))
            score = t.get("score", 0)
            n_msg = t.get("messages", 0)
            authors = e(", ".join(t.get("authors", [])))
            kws = t.get("keywords", [])
            kw_chips = " ".join(f'<span class="port-chip">{e(k)}</span>' for k in kws[:6])
            patch_tag = ' 📦' if t.get("is_patch") else ""
            ver = f' {e(t["patch_version"])}' if t.get("patch_version") else ""
            score_cls = "green" if score >= 8 else ("amber" if score >= 4 else "fg-dim")

            # Link to lore
            link_html = ""
            links = t.get("links", [])
            if links:
                link_html = f' <a href="{e(links[0])}" style="font-size:0.8rem;color:var(--cyan)">[lore]</a>'

            # Per-thread LLM analysis
            analysis = t.get("llm_analysis", "")
            analysis_html = ""
            if analysis:
                # Parse structured fields for nicer display
                analysis_lines = analysis.strip().split("\n")
                formatted = []
                for ln in analysis_lines:
                    ln_s = ln.strip()
                    if not ln_s:
                        formatted.append("")
                        continue
                    # Highlight field labels
                    for field in ("SUBJECT:", "TYPE:", "SUBSYSTEM:", "IMPORTANCE:",
                                  "SUMMARY:", "KEY PEOPLE:", "STATUS:", "IMPACT:"):
                        if ln_s.startswith(field):
                            value = ln_s[len(field):].strip()
                            # Color-code importance
                            if field == "IMPORTANCE:":
                                imp_color = "var(--green)" if "low" in value.lower() else (
                                    "var(--amber)" if "medium" in value.lower() else "var(--red)")
                                ln_s = f'<span style="color:var(--cyan)">{field}</span> <span style="color:{imp_color}">{e(value)}</span>'
                            elif field == "STATUS:":
                                st_color = "var(--green)" if "accepted" in value.lower() else (
                                    "var(--amber)" if "revision" in value.lower() else "var(--fg)")
                                ln_s = f'<span style="color:var(--cyan)">{field}</span> <span style="color:{st_color}">{e(value)}</span>'
                            elif field in ("SUMMARY:", "IMPACT:"):
                                ln_s = f'<span style="color:var(--cyan)">{field}</span> {e(value)}'
                            else:
                                ln_s = f'<span style="color:var(--cyan)">{field}</span> {e(value)}'
                            break
                    else:
                        ln_s = e(ln_s)
                    formatted.append(ln_s)
                analysis_html = f'<div style="margin-top:10px;padding:10px;background:var(--bg);border-left:2px solid var(--green2);font-size:0.85rem;line-height:1.7;white-space:pre-wrap">{"<br>".join(formatted)}</div>'

            detail_cards += f"""<div style="border:1px solid var(--border);background:var(--bg2);padding:14px;margin-bottom:12px">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:6px">
    <div>
      <span style="color:var(--{score_cls});font-weight:bold;margin-right:8px">[{score:.0f}]</span>
      <span style="color:var(--fg);font-size:1rem">{subj}{patch_tag}{ver}</span>{link_html}
    </div>
    <span style="color:var(--fg-dim);font-size:0.85rem;white-space:nowrap">{n_msg} msgs</span>
  </div>
  <div style="margin-top:6px;font-size:0.85rem;color:var(--fg-dim)">👤 {authors}</div>
  <div style="margin-top:4px">{kw_chips}</div>
  {analysis_html}
</div>"""

    threads_html = ""
    if detail_cards:
        threads_html = f"""
<div class="section">
  <div class="section-title">CAMERA-RELEVANT THREADS — detailed analysis</div>
  <div class="section-body">{detail_cards}</div>
</div>"""

    # Archive: previous digests
    archive_rows = ""
    for d in digests[1:30]:  # last 30, skip latest
        dt = e(d.get("date", "?"))
        msgs = d.get("total_messages", "?")
        cam = d.get("camera_threads", "?")
        model_t = d.get("total_llm_time_s", d.get("ollama_time_s", "?"))
        # First line of bulletin as preview
        preview_lines = d.get("bulletin", "").strip().split("\n")
        # Find first non-empty, non-header line
        preview = ""
        for ln in preview_lines[2:6]:
            ln = ln.strip()
            if ln and not ln.startswith("📡") and not ln.startswith("==="):
                preview = ln[:100]
                break
        archive_rows += f'<tr><td style="white-space:nowrap">{dt}</td><td>{msgs}</td><td>{cam}</td><td>{model_t}s</td><td style="color:var(--fg-dim);font-size:0.85rem">{e(preview)}</td></tr>'

    archive_html = ""
    if archive_rows:
        archive_html = f"""
<div class="section">
  <div class="section-title">DIGEST ARCHIVE</div>
  <div class="section-body">
    <table class="host-table">
      <thead><tr><th>Date</th><th>Msgs</th><th>Camera</th><th>LLM</th><th>Preview</th></tr></thead>
      <tbody>{archive_rows}</tbody>
    </table>
  </div>
</div>"""

    # Info section
    info_html = """
<div class="section">
  <div class="section-title">ABOUT</div>
  <div class="section-body" style="font-size:0.85rem;color:var(--fg-dim)">
    📡 Source: <a href="https://lore.kernel.org/linux-media/">lore.kernel.org/linux-media</a> &nbsp;│&nbsp;
    🕐 Daily at 4:00 AM &nbsp;│&nbsp;
    🤖 Local LLM summarization via Ollama &nbsp;│&nbsp;
    📱 Signal bulletin delivery<br>
    Focus: camera drivers, V4L2, ISP, MIPI CSI, sensors, libcamera, UVC, videobuf2
  </div>
</div>"""

    body = latest_html + threads_html + archive_html + info_html
    return page_wrap("LKML DIGEST", body, "lkml")


# ─── Page: History (history.html) ───

def gen_history(all_scans):
    dates = get_scan_dates()
    if not dates:
        return page_wrap("HISTORY", '<div class="section"><div class="section-body">NO DATA</div></div>', "history")

    # Load data for chart
    history_data = []
    for d in dates[-30:]:
        s = all_scans.get(d)
        if s:
            total_ports = sum(len(h.get("ports",[])) for h in s["hosts"].values())
            mdns = s.get("mdns_devices", sum(1 for h in s["hosts"].values() if h.get("mdns_name")))
            sec_avg = s.get("security", {}).get("avg_score", "?")
            history_data.append({"date": d, "hosts": s["host_count"], "ports": total_ports, "mdns": mdns, "sec": sec_avg})

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

    # Day-by-day changes table (enhanced with port changes)
    diff_rows = ""
    for i in range(len(dates)-1, 0, -1):
        curr = all_scans.get(dates[i])
        prev = all_scans.get(dates[i-1])
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

        # Port changes
        pc = curr.get("port_changes", {})
        pc_str = ""
        if pc.get("hosts_changed", 0) > 0:
            pc_str = f'+{pc["new_ports"]}/-{pc["gone_ports"]} ({pc["hosts_changed"]}h)'

        # Security
        sec_avg = curr.get("security", {}).get("avg_score", "?")
        mdns = curr.get("mdns_devices", 0)

        diff_rows += f"""<tr>
  <td>{e(dates[i])}</td>
  <td>{curr['host_count']}</td>
  <td style="color:var(--green)">{f'+{len(new_ips)}' if new_ips else '—'}</td>
  <td style="color:var(--red)">{f'-{len(gone_ips)}' if gone_ips else '—'}</td>
  <td style="font-size:0.75rem">{e(pc_str) if pc_str else '—'}</td>
  <td style="text-align:center">{score_badge(sec_avg) if sec_avg != '?' else '—'}</td>
  <td style="text-align:center">{mdns}</td>
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
      <thead><tr><th>Date</th><th>Total</th><th>New</th><th>Gone</th><th>Port Δ</th><th>SecAvg</th><th>mDNS</th><th>New IPs</th><th>Gone IPs</th></tr></thead>
      <tbody>{diff_rows if diff_rows else '<tr><td colspan="9" style="color:var(--fg-dim)">Need at least 2 scans for history</td></tr>'}</tbody>
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
        highlighted = re.sub(
            r'\[([^\]]+)\]',
            r'<span class="log-ts">[\1]</span>',
            escape(log)
        )
        tabs += f'<a href="#log-{d}" onclick="showLog(\'{d}\')" style="margin-right:8px">{d}</a>'
        content += f'<div id="log-{d}" class="log-view" style="display:none">{highlighted}</div>'

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
    scan = get_latest_scan()
    all_scans = load_all_scans(30)

    # Main pages
    pages = {
        "index.html": lambda: gen_dashboard(all_scans),
        "hosts.html": lambda: gen_hosts(scan),
        "presence.html": gen_presence,
        "lkml.html": gen_lkml,
        "security.html": lambda: gen_security(scan),
        "history.html": lambda: gen_history(all_scans),
        "log.html": gen_log,
    }
    for fname, gen_fn in pages.items():
        html = gen_fn()
        path = os.path.join(WEB_DIR, fname)
        with open(path, "w") as f:
            f.write(html)
        size = len(html)
        print(f"  [{fname}] {size:,} bytes")

    # Per-host detail pages
    host_count = 0
    if scan:
        for ip, h in scan["hosts"].items():
            safe_ip = ip.replace(".", "-")
            html = gen_host_detail(ip, h, all_scans)
            path = os.path.join(WEB_DIR, "host", f"{safe_ip}.html")
            with open(path, "w") as f:
                f.write(html)
            host_count += 1

    total_pages = len(pages) + host_count
    print(f"Dashboard generated: {total_pages} pages ({len(pages)} main + {host_count} host details) in {WEB_DIR}/")

if __name__ == "__main__":
    main()
