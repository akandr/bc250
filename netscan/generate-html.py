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
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
os.makedirs(WEB_DIR, exist_ok=True)
os.makedirs(os.path.join(WEB_DIR, "host"), exist_ok=True)

# ─── Digest feeds config ───

DIGEST_FEEDS = {}
_feeds_path = os.path.join(SCRIPT_DIR, "digest-feeds.json")
if os.path.exists(_feeds_path):
    try:
        with open(_feeds_path) as _f:
            DIGEST_FEEDS = json.load(_f)
    except Exception:
        pass

# ─── Repo feeds config ───

REPO_FEEDS = {}
_repo_feeds_path = os.path.join(SCRIPT_DIR, "repo-feeds.json")
if os.path.exists(_repo_feeds_path):
    try:
        with open(_repo_feeds_path) as _f:
            _raw = json.load(_f)
            REPO_FEEDS = {k: v for k, v in _raw.items() if isinstance(v, dict)}
    except Exception:
        pass

# ─── ASCII art / branding ───

BANNER_LINES = [
    (' ███╗   ██╗███████╗████████╗███████╗ ██████╗ █████╗ ███╗   ██╗', '#ff44ff'),
    (' ████╗  ██║██╔════╝╚══██╔══╝██╔════╝██╔════╝██╔══██╗████╗  ██║', '#dd55ff'),
    (' ██╔██╗ ██║█████╗     ██║   ███████╗██║     ███████║██╔██╗ ██║', '#aa66ff'),
    (' ██║╚██╗██║██╔══╝     ██║   ╚════██║██║     ██╔══██╗██║╚██╗██║', '#7777ff'),
    (' ██║ ╚████║███████╗   ██║   ███████║╚██████╗██║  ██║██║ ╚████║', '#4488ff'),
    (' ╚═╝  ╚═══╝╚══════╝   ╚═╝   ╚══════╝ ╚═════╝╚═╝  ╚═╝╚═╝  ╚═══╝', '#22ccff'),
]

def render_banner():
    lines = []
    for text, color in BANNER_LINES:
        lines.append(f'<span style="color:{color};text-shadow:0 0 10px {color}44">{text}</span>')
    return "\n".join(lines)

SKULL = r"""
     ╔══════════════════════════════╗
     ║  ░▒▓█ NETSCAN v3.0 █▓▒░    ║
     ║  ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄   ║
     ║  █ bc250 // zen2+skillfish █ ║
     ║  ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀   ║
     ╚══════════════════════════════╝"""

# ─── CSS: Demoscene BBS aesthetic, responsive ───

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;600;700&display=swap');

:root {
  --bg: #08080f;
  --bg2: #0e0e1a;
  --bg3: #161625;
  --bg4: #1e1e30;
  --fg: #b8b8cc;
  --fg-dim: #555566;
  --fg-muted: #3a3a4a;
  --green: #33ff88;
  --green2: #22cc66;
  --green-dim: #115533;
  --amber: #ffbb33;
  --red: #ff4455;
  --cyan: #22ddff;
  --magenta: #dd55ff;
  --purple: #9966ff;
  --blue: #4488ff;
  --pink: #ff66aa;
  --border: #252540;
  --border-bright: #3a3a60;
  --glow-green: 0 0 12px rgba(51,255,136,0.2);
  --glow-cyan: 0 0 12px rgba(34,221,255,0.2);
  --glow-magenta: 0 0 12px rgba(221,85,255,0.15);
  --glow-purple: 0 0 12px rgba(153,102,255,0.15);
  --gradient-1: linear-gradient(135deg, #ff44ff22, #4488ff22, #22ddff22);
  --gradient-text: linear-gradient(90deg, #ff44ff, #9966ff, #4488ff, #22ddff);
}
*, *::before, *::after { box-sizing: border-box; }
html { font-size: 14px; }
body {
  margin: 0; padding: 0;
  background: var(--bg);
  color: var(--fg);
  font-family: 'Fira Code', 'IBM Plex Mono', 'Cascadia Code', 'Consolas', 'SF Mono', monospace;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}
a { color: var(--cyan); text-decoration: none; transition: all 0.2s; }
a:hover { color: var(--magenta); text-shadow: var(--glow-magenta); }

/* CRT scanline effect — subtle */
body::after {
  content: '';
  position: fixed; top: 0; left: 0;
  width: 100%; height: 100%;
  background: repeating-linear-gradient(
    0deg,
    transparent, transparent 3px,
    rgba(0,0,0,0.04) 3px, rgba(0,0,0,0.04) 4px
  );
  pointer-events: none;
  z-index: 9999;
}

/* Subtle animated gradient background */
body::before {
  content: '';
  position: fixed; top: 0; left: 0;
  width: 100%; height: 100%;
  background: radial-gradient(ellipse at 20% 50%, rgba(153,102,255,0.03) 0%, transparent 50%),
              radial-gradient(ellipse at 80% 20%, rgba(34,221,255,0.03) 0%, transparent 50%),
              radial-gradient(ellipse at 50% 80%, rgba(255,68,255,0.02) 0%, transparent 50%);
  pointer-events: none;
  z-index: -1;
}

.container {
  max-width: 1200px; margin: 0 auto; padding: 16px;
}

/* Banner / header */
.banner {
  font-size: 0.52rem;
  line-height: 1.15;
  white-space: pre;
  text-align: center;
  overflow-x: auto;
  padding: 16px 0 8px 0;
  letter-spacing: 0.5px;
}
@media (max-width: 720px) {
  .banner { font-size: 0.3rem; }
}
.header-bar {
  border-top: 1px solid var(--border-bright);
  border-bottom: 1px solid var(--border-bright);
  padding: 8px 0;
  margin: 6px 0 16px 0;
  text-align: center;
  font-size: 0.82rem;
  color: var(--fg-dim);
  background: linear-gradient(90deg, transparent, var(--bg3), transparent);
  letter-spacing: 2px;
}
.header-bar .node {
  color: var(--cyan);
  text-shadow: var(--glow-cyan);
  font-weight: 600;
}
.header-bar .sep {
  color: var(--fg-muted);
}

/* Navigation — pill-style with glow */
nav {
  display: flex; flex-wrap: wrap; gap: 4px;
  justify-content: center;
  margin-bottom: 20px;
  padding: 10px;
  border: 1px solid var(--border);
  background: var(--bg2);
  border-radius: 8px;
  position: relative;
}
nav::before {
  content: '';
  position: absolute; top: -1px; left: 10%; right: 10%; height: 1px;
  background: linear-gradient(90deg, transparent, var(--purple), var(--cyan), transparent);
}
nav a {
  color: var(--fg-dim);
  padding: 5px 14px;
  border: 1px solid transparent;
  border-radius: 4px;
  font-size: 0.8rem;
  font-weight: 600;
  letter-spacing: 0.5px;
  transition: all 0.2s;
  text-transform: uppercase;
}
nav a:hover {
  color: var(--cyan);
  background: var(--bg3);
  border-color: var(--border-bright);
  text-decoration: none;
  text-shadow: var(--glow-cyan);
}
nav a.active {
  color: var(--bg);
  background: linear-gradient(135deg, var(--purple), var(--cyan));
  border-color: transparent;
  text-decoration: none;
  text-shadow: none;
}

/* Section boxes — glass morphism */
.section {
  border: 1px solid var(--border);
  margin-bottom: 16px;
  background: var(--bg2);
  border-radius: 6px;
  overflow: hidden;
  position: relative;
}
.section::before {
  content: '';
  position: absolute; top: 0; left: 0; right: 0; height: 1px;
  background: linear-gradient(90deg, transparent, var(--border-bright), transparent);
}
.section-title {
  background: var(--bg3);
  border-bottom: 1px solid var(--border);
  padding: 10px 14px;
  color: var(--cyan);
  font-weight: 600;
  font-size: 0.88rem;
  letter-spacing: 0.5px;
}
.section-title::before { content: '▸ '; color: var(--magenta); }
.section-body { padding: 14px; }

/* Stats grid */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 10px;
}
.stat-box {
  border: 1px solid var(--border);
  padding: 12px;
  background: var(--bg);
  text-align: center;
  border-radius: 6px;
  transition: border-color 0.2s;
}
.stat-box:hover { border-color: var(--border-bright); }
.stat-val {
  font-size: 1.8rem;
  font-weight: 700;
  color: var(--green);
  text-shadow: var(--glow-green);
  line-height: 1.2;
}
.stat-val.amber { color: var(--amber); text-shadow: 0 0 12px rgba(255,187,51,0.2); }
.stat-val.red { color: var(--red); text-shadow: 0 0 12px rgba(255,68,85,0.2); }
.stat-val.cyan { color: var(--cyan); text-shadow: var(--glow-cyan); }
.stat-label {
  font-size: 0.72rem;
  color: var(--fg-dim);
  text-transform: uppercase;
  letter-spacing: 1.5px;
  margin-top: 4px;
}

/* Host table */
.host-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.82rem;
}
.host-table th {
  background: var(--bg3);
  color: var(--purple);
  border: 1px solid var(--border);
  padding: 8px 10px;
  text-align: left;
  white-space: nowrap;
  position: sticky; top: 0;
  cursor: pointer;
  user-select: none;
  font-weight: 600;
  letter-spacing: 0.5px;
  font-size: 0.78rem;
  text-transform: uppercase;
}
.host-table th:hover { background: var(--bg4); color: var(--cyan); }
.host-table th::after { content: ' ↕'; color: var(--fg-muted); font-size: 0.65rem; }
.host-table td {
  border: 1px solid var(--border);
  padding: 5px 10px;
  vertical-align: top;
}
.host-table tr:nth-child(even) { background: var(--bg); }
.host-table tr:nth-child(odd) { background: var(--bg2); }
.host-table tr:hover { background: var(--bg3); }

/* Device type badges — neon style */
.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 3px;
  font-size: 0.72rem;
  font-weight: 600;
  letter-spacing: 0.5px;
}
.badge-iot { background: #112218; color: #55ff88; border: 1px solid #33aa5544; }
.badge-iot-web { background: #112220; color: #44ffaa; border: 1px solid #22aa6644; }
.badge-pc { background: #101828; color: #5599ff; border: 1px solid #3366cc44; }
.badge-server { background: #181028; color: #bb77ff; border: 1px solid #7744cc44; }
.badge-phone { background: #201810; color: #ffbb55; border: 1px solid #cc883344; }
.badge-console { background: #201010; color: #ff7755; border: 1px solid #cc442244; }
.badge-sbc { background: #182010; color: #aaff55; border: 1px solid #88cc2244; }
.badge-network { background: #102020; color: #55ffff; border: 1px solid #22cccc44; }
.badge-appliance { background: #202010; color: #ffff55; border: 1px solid #cccc2244; }
.badge-smart-speaker { background: #181818; color: #ff99ff; border: 1px solid #cc66cc44; }
.badge-camera { background: #201818; color: #ff9999; border: 1px solid #cc555544; }
.badge-unknown, .badge-unknown-web { background: #151520; color: #777788; border: 1px solid #44444444; }

/* Port chips */
.port-chip {
  display: inline-block;
  padding: 1px 5px;
  margin: 1px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 3px;
  font-size: 0.72rem;
  color: var(--cyan);
}
.port-chip.common { border-color: var(--green-dim); color: var(--green); }
.port-chip.port-new { border-color: var(--green); color: var(--green); font-weight: bold; }
.port-chip.port-gone { border-color: var(--red); color: var(--red); text-decoration: line-through; opacity: 0.7; }

/* Health bars */
.health-row {
  display: flex; align-items: center; gap: 8px;
  margin-bottom: 6px; font-size: 0.85rem;
}
.health-label { width: 80px; color: var(--fg-dim); text-align: right; }
.health-bar-bg {
  flex: 1; height: 18px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 3px;
  overflow: hidden;
}
.health-bar-fill {
  height: 100%;
  transition: width 0.3s;
  border-radius: 2px;
}
.health-bar-fill.ok { background: linear-gradient(90deg, var(--green-dim), var(--green2)); }
.health-bar-fill.warn { background: linear-gradient(90deg, #553300, var(--amber)); }
.health-bar-fill.crit { background: linear-gradient(90deg, #551111, var(--red)); }
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
  border-radius: 4px;
  padding: 12px;
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
  border-radius: 3px;
  font-weight: 600;
  font-size: 0.8rem;
  min-width: 36px;
  text-align: center;
}
.score-ok { background: #112218; color: #33ff88; border: 1px solid #22aa5544; }
.score-warn { background: #221800; color: #ffbb33; border: 1px solid #aa770044; }
.score-crit { background: #221010; color: #ff4455; border: 1px solid #cc222244; }

/* Security flags */
.flag-item {
  padding: 5px 10px;
  margin: 3px 0;
  font-size: 0.82rem;
  border-left: 3px solid var(--border);
  background: var(--bg);
  border-radius: 0 3px 3px 0;
}
.flag-crit { border-left-color: var(--red); }
.flag-warn { border-left-color: var(--amber); }

/* mDNS name */
.mdns-name { color: var(--cyan); font-weight: 600; }
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
  line-height: 1;
}
.score-meter .score-num.ok { color: var(--green); text-shadow: var(--glow-green); }
.score-meter .score-num.warn { color: var(--amber); text-shadow: 0 0 12px rgba(255,187,51,0.2); }
.score-meter .score-num.crit { color: var(--red); text-shadow: 0 0 12px rgba(255,68,85,0.2); }
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
  border-radius: 3px;
  font-size: 0.72rem;
  color: var(--magenta);
}

/* IP link */
.ip-link { color: var(--cyan); }
.ip-link:hover { color: var(--magenta); text-shadow: var(--glow-magenta); }

/* Footer */
.footer {
  text-align: center;
  color: var(--fg-dim);
  font-size: 0.75rem;
  padding: 24px 0;
  border-top: 1px solid var(--border);
  margin-top: 20px;
  position: relative;
}
.footer::before {
  content: '';
  position: absolute; top: -1px; left: 15%; right: 15%; height: 1px;
  background: linear-gradient(90deg, transparent, var(--purple)44, var(--cyan)44, transparent);
}
.footer-art {
  color: var(--fg-muted);
  font-size: 0.55rem;
  line-height: 1.1;
  margin-bottom: 12px;
}
.footer-text {
  color: var(--fg-dim);
  font-size: 0.78rem;
}
.footer-quote {
  color: var(--fg-muted);
  font-style: italic;
  margin-top: 6px;
  font-size: 0.75rem;
}

/* Responsive */
@media (max-width: 720px) {
  html { font-size: 13px; }
  .container { padding: 8px; }
  .host-table { display: block; overflow-x: auto; }
  .stats-grid { grid-template-columns: repeat(2, 1fr); }
  nav { gap: 3px; padding: 8px; border-radius: 6px; }
  nav a { padding: 5px 8px; font-size: 0.75rem; }
  .section { border-radius: 4px; }
  .section-title { padding: 8px 10px; font-size: 0.82rem; }
}

/* type icons */
.type-icon { margin-right: 4px; }

/* Scrollbar */
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--border-bright); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: var(--fg-muted); }

/* Enumeration / fingerprint styles */
.enum-section { margin-top: 8px; }
.enum-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
@media (max-width: 720px) { .enum-grid { grid-template-columns: 1fr; } }
.fp-box {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 10px;
}
.fp-title {
  color: var(--purple);
  font-weight: 600;
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 6px;
  border-bottom: 1px solid var(--border);
  padding-bottom: 4px;
}
.fp-title::before { content: '◆ '; color: var(--magenta); }
.fp-row {
  display: flex; gap: 8px;
  padding: 3px 0;
  font-size: 0.82rem;
}
.fp-label { color: var(--fg-dim); min-width: 90px; flex-shrink: 0; }
.fp-value { color: var(--fg); word-break: break-all; }
.svc-version-chip {
  display: inline-block;
  padding: 2px 7px;
  margin: 2px;
  background: var(--bg);
  border: 1px solid var(--purple);
  border-radius: 3px;
  font-size: 0.72rem;
  color: var(--purple);
}
.svc-version-chip .port-num { color: var(--cyan); margin-right: 4px; }
.http-card {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 8px 10px;
  margin-bottom: 6px;
}
.http-card .http-url {
  color: var(--cyan);
  font-size: 0.78rem;
  font-weight: 600;
  margin-bottom: 4px;
}
.http-card .http-server { color: var(--green); font-size: 0.78rem; }
.http-card .http-title { color: var(--amber); font-size: 0.78rem; }
.tls-card {
  background: var(--bg);
  border: 1px solid var(--border);
  border-left: 3px solid var(--green);
  border-radius: 0 4px 4px 0;
  padding: 8px 10px;
  margin-bottom: 6px;
}
.tls-card.expired { border-left-color: var(--red); }
.tls-cn { color: var(--cyan); font-weight: 600; font-size: 0.82rem; }
.tls-detail { color: var(--fg-dim); font-size: 0.78rem; }
.upnp-card {
  background: linear-gradient(135deg, var(--bg), var(--bg3));
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 10px;
}
.upnp-name { color: var(--cyan); font-size: 1rem; font-weight: 600; }
.upnp-mfr { color: var(--magenta); font-size: 0.82rem; }
.upnp-model { color: var(--fg); font-size: 0.85rem; }
.banner-box {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 8px 10px;
  margin-bottom: 4px;
  font-size: 0.78rem;
}
.banner-port { color: var(--cyan); font-weight: 600; }
.banner-text { color: var(--fg-dim); white-space: pre-wrap; word-break: break-all; font-family: inherit; }
.phone-hint {
  padding: 4px 8px;
  margin: 2px;
  display: inline-block;
  background: #201810;
  border: 1px solid var(--amber);
  border-radius: 3px;
  font-size: 0.75rem;
  color: var(--amber);
}
.fp-summary {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
  padding: 8px 0;
}
.fp-os {
  color: var(--green);
  font-size: 0.88rem;
  font-weight: 600;
  padding: 3px 10px;
  background: #112218;
  border: 1px solid var(--green-dim);
  border-radius: 4px;
}
.fp-device {
  color: var(--magenta);
  font-size: 0.88rem;
  font-weight: 600;
  padding: 3px 10px;
  background: #201020;
  border: 1px solid var(--magenta);
  border-radius: 4px;
}
.fp-identifier {
  display: block;
  padding: 3px 0;
  font-size: 0.8rem;
  color: var(--fg-dim);
}
.fp-identifier::before { content: '› '; color: var(--cyan); }
.enum-badge {
  display: inline-block;
  padding: 1px 6px;
  margin-left: 4px;
  background: #181030;
  border: 1px solid var(--purple);
  border-radius: 3px;
  font-size: 0.62rem;
  color: var(--purple);
  vertical-align: middle;
  letter-spacing: 0.5px;
}

/* Vulnerability scan styles */
.vuln-card {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 8px 10px;
  margin-bottom: 6px;
  border-left: 3px solid var(--border);
  transition: border-color 0.2s;
}
.vuln-card:hover { border-left-color: var(--cyan); }
.vuln-card.vuln-critical { border-left-color: #ff2244; background: #1a0808; }
.vuln-card.vuln-high { border-left-color: #ff6622; background: #1a0e08; }
.vuln-card.vuln-medium { border-left-color: #ffbb33; background: #1a1508; }
.vuln-card.vuln-low { border-left-color: #667788; }
.vuln-header {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 4px;
}
.vuln-sev {
  display: inline-block;
  padding: 1px 7px;
  border-radius: 3px;
  font-size: 0.7rem;
  font-weight: 700;
  letter-spacing: 0.5px;
  text-transform: uppercase;
}
.sev-critical { background: #ff2244; color: #fff; }
.sev-high { background: #ff6622; color: #fff; }
.sev-medium { background: #ffbb33; color: #111; }
.sev-low { background: #334455; color: #aabbcc; }
.vuln-cve {
  color: var(--cyan);
  font-weight: 600;
  font-size: 0.82rem;
}
.vuln-cve a { color: var(--cyan); }
.vuln-cve a:hover { color: var(--magenta); }
.vuln-detail { color: var(--fg); font-size: 0.82rem; }
.vuln-meta { color: var(--fg-dim); font-size: 0.75rem; margin-top: 2px; }
.vuln-fix {
  color: var(--green);
  font-size: 0.75rem;
  margin-top: 2px;
}
.vuln-fix::before { content: '💡 '; }
.vuln-cvss {
  font-weight: 700;
  font-size: 0.78rem;
  padding: 1px 6px;
  border-radius: 3px;
  background: var(--bg3);
}
.cvss-crit { color: #ff2244; border: 1px solid #ff2244; }
.cvss-high { color: #ff6622; border: 1px solid #ff6622; }
.cvss-med { color: #ffbb33; border: 1px solid #ffbb33; }
.cvss-low { color: #667788; border: 1px solid #667788; }
.risk-meter {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 8px 0;
}
.risk-num {
  font-size: 2.2rem;
  font-weight: bold;
  line-height: 1;
}
.risk-num.risk-ok { color: var(--green); text-shadow: var(--glow-green); }
.risk-num.risk-warn { color: var(--amber); }
.risk-num.risk-crit { color: var(--red); }
.risk-label { color: var(--fg-dim); font-size: 0.82rem; }
.vuln-summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(100px, 1fr));
  gap: 8px;
  margin: 8px 0;
}
.vuln-count-box {
  text-align: center;
  padding: 8px;
  border-radius: 4px;
  background: var(--bg);
  border: 1px solid var(--border);
}
.vuln-count-num { font-size: 1.5rem; font-weight: 700; line-height: 1.2; }
.vuln-count-label { font-size: 0.68rem; color: var(--fg-dim); text-transform: uppercase; letter-spacing: 1px; }

/* Watchdog */
.wd-checks { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
.wd-chip { padding: 3px 10px; border-radius: 3px; font-size: 0.75rem; font-family: var(--mono); border: 1px solid var(--border); }
.wd-ok { color: var(--green); border-color: var(--green); }
.wd-warn { color: var(--amber); border-color: var(--amber); background: rgba(255,187,51,0.08); }
.wd-crit { color: var(--red); border-color: var(--red); background: rgba(255,34,68,0.08); }
.wd-alert { padding: 4px 0; font-size: 0.82rem; border-bottom: 1px solid var(--border); }
.wd-alert:last-child { border-bottom: none; }
.wd-ts { font-size: 0.72rem; color: var(--fg-dim); }
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

def get_latest_enum():
    """Load latest enum JSON for service/fingerprint enrichment."""
    files = sorted(glob.glob(f"{DATA_DIR}/enum/enum-*.json"))
    if files:
        return load_json(files[-1])
    return None

def get_latest_vuln():
    """Load latest vulnerability scan results."""
    files = sorted(glob.glob(f"{DATA_DIR}/vuln/vuln-*.json"))
    if files:
        return load_json(files[-1])
    return None

def get_latest_watchdog():
    """Load latest watchdog report for integrity/alert display."""
    files = sorted(glob.glob(f"{DATA_DIR}/watchdog/watchdog-*.json"))
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
    ]
    # Dynamic feed pages from digest-feeds.json
    for fid, fcfg in DIGEST_FEEDS.items():
        slug = fcfg.get("page_slug", fid)
        label = fcfg.get("nav_label", fid.upper())
        nav_items.append((f"/{slug}.html", label, slug))
    # Issues and Notes pages (only if configs exist)
    if REPO_FEEDS:
        nav_items.append(("/issues.html", "ISSUES", "issues"))
    nav_items.append(("/notes.html", "NOTES", "notes"))
    nav_items.append(("/load.html", "LOAD", "load"))
    nav_items += [
        ("/security.html", "SECURITY", "security"),
        ("/history.html", "HISTORY", "history"),
        ("/log.html", "LOG", "log"),
    ]
    nav_html = "\n".join(
        f'<a href="{href}" class="{"active" if page==active_page else ""}">{label}</a>'
        for href, label, page in nav_items
    )
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    banner_html = render_banner()
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#33ff88">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="manifest" href="/manifest.json">
<title>NETSCAN // {e(title)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="container">
<pre class="banner">{banner_html}</pre>
<div class="header-bar">
  <span class="sep">░▒▓</span> <span class="node">bc250</span>
  <span class="sep">─</span> 192.168.3.0/24
  <span class="sep">─</span> zen2 + cyan skillfish
  <span class="sep">▓▒░</span>
</div>
<nav>{nav_html}</nav>
{body}
<div class="footer">
<pre class="footer-art">{SKULL}</pre>
<div class="footer-text">NETSCAN v3.0 // bc250 // generated {ts}</div>
<div class="footer-quote">"The Net treats censorship as damage and routes around it."</div>
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

    # Enum stats
    enum_data = get_latest_enum()
    enum_hosts_count = 0
    enum_svc_count = 0
    if enum_data:
        enum_hosts_dict = enum_data.get("hosts", {})
        enum_hosts_count = len(enum_hosts_dict)
        enum_svc_count = sum(len(v.get("services", [])) for v in enum_hosts_dict.values())

    # Vuln stats
    vuln_data = get_latest_vuln()
    vuln_total = 0
    vuln_crit = 0
    vuln_high = 0
    if vuln_data:
        vs = vuln_data.get("stats", {})
        vuln_total = vs.get("total_findings", 0)
        vuln_crit = vs.get("critical", 0)
        vuln_high = vs.get("high", 0)

    # Stats boxes
    sec_cls = "red" if sec.get("critical", 0) > 0 else ("amber" if sec.get("warning", 0) > 0 else "")
    vuln_cls = "red" if vuln_crit > 0 else ("amber" if vuln_high > 0 else "")
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
      <div class="stat-box"><div class="stat-val cyan">{enum_hosts_count}</div><div class="stat-label">Fingerprinted</div></div>
      <div class="stat-box"><div class="stat-val {vuln_cls}">{vuln_total}</div><div class="stat-label">Vulns Found</div></div>
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

    # Vulnerability summary widget
    vuln_summary_html = ""
    if vuln_data and vuln_data.get("stats", {}).get("total_findings", 0) > 0:
        vs = vuln_data["stats"]
        vuln_hosts = vuln_data.get("hosts", {})
        # Top vulnerable hosts
        vuln_rows = ""
        sorted_vuln = sorted(vuln_hosts.items(), key=lambda x: x[1].get("risk_score", 100))
        for vip, vdata in sorted_vuln[:8]:
            vname = vdata.get("name", "")
            vr = vdata.get("risk_score", 100)
            vc = vdata.get("severity_counts", {})
            rc = "risk-crit" if vr < 50 else ("risk-warn" if vr < 80 else "risk-ok")
            sev_pills = ""
            if vc.get("critical", 0):
                sev_pills += f'<span class="vuln-sev sev-critical" style="font-size:0.62rem">{vc["critical"]}C</span> '
            if vc.get("high", 0):
                sev_pills += f'<span class="vuln-sev sev-high" style="font-size:0.62rem">{vc["high"]}H</span> '
            if vc.get("medium", 0):
                sev_pills += f'<span class="vuln-sev sev-medium" style="font-size:0.62rem">{vc["medium"]}M</span> '
            vuln_rows += f'<tr><td>{ip_link(vip)}</td><td>{e(vname) if vname else "—"}</td><td><span class="{rc}" style="font-weight:bold">{vr}</span></td><td>{sev_pills}</td><td>{vdata.get("finding_count",0)}</td></tr>'
        delta = ""
        if vs.get("new_findings") or vs.get("resolved_findings"):
            delta = f' — <span style="color:var(--green)">+{vs.get("new_findings",0)} new</span> <span style="color:var(--fg-dim)">-{vs.get("resolved_findings",0)} resolved</span>'
        vuln_summary_html = f"""
<div class="section">
  <div class="section-title">VULNERABILITY SCAN — <a href="/security.html" style="color:var(--red)">full report →</a>{delta}</div>
  <div class="section-body">
    <div class="vuln-summary-grid">
      <div class="vuln-count-box"><div class="vuln-count-num" style="color:#ff2244">{vs.get('critical',0)}</div><div class="vuln-count-label">Critical</div></div>
      <div class="vuln-count-box"><div class="vuln-count-num" style="color:#ff6622">{vs.get('high',0)}</div><div class="vuln-count-label">High</div></div>
      <div class="vuln-count-box"><div class="vuln-count-num" style="color:#ffbb33">{vs.get('medium',0)}</div><div class="vuln-count-label">Medium</div></div>
      <div class="vuln-count-box"><div class="vuln-count-num" style="color:#667788">{vs.get('low',0)}</div><div class="vuln-count-label">Low</div></div>
      <div class="vuln-count-box"><div class="vuln-count-num">{vs.get('hosts_with_findings',0)}</div><div class="vuln-count-label">Hosts</div></div>
    </div>
    {f'<table class="host-table" style="margin-top:8px"><thead><tr><th>IP</th><th>Name</th><th>Risk</th><th>Severity</th><th>Count</th></tr></thead><tbody>{vuln_rows}</tbody></table>' if vuln_rows else ''}
  </div>
</div>"""

    # Watchdog integrity widget
    wd_html = ""
    wd_data = get_latest_watchdog()
    if wd_data:
        wd_ts = wd_data.get("run_at", "")[:16].replace("T", " ")
        wd_mode = wd_data.get("mode", "full")
        checks = wd_data.get("checks", {})
        # Build check chips
        check_labels = [
            ("arp_integrity", "ARP"), ("dns_integrity", "DNS"),
            ("gateway", "Gateway"), ("device_availability", "Devices"),
            ("dhcp_integrity", "DHCP"), ("vuln_delta", "Vulns"),
            ("cert_expiry", "Certs"), ("service_changes", "Services"),
            ("risky_ports", "Ports"), ("score_trend", "Scores"),
        ]
        chips = ""
        for key, label in check_labels:
            st = checks.get(key, {}).get("status", "")
            if not st:
                continue
            cls = "wd-ok" if st == "ok" else ("wd-crit" if st == "critical" else "wd-warn")
            icon = "✓" if st == "ok" else "⚠"
            chips += f'<span class="wd-chip {cls}">{label} {icon}</span>'
        # Recent alerts
        wd_alerts = wd_data.get("alerts", [])
        alert_lines = ""
        shown = 0
        for a in wd_alerts:
            tier = a.get("tier", "info")
            if tier not in ("critical", "high", "medium"):
                continue
            icon = {"critical": "🔴", "high": "🟠", "medium": "🟡"}.get(tier, "")
            alert_lines += f'<div class="wd-alert">{icon} {e(a.get("title", ""))}</div>'
            shown += 1
            if shown >= 6:
                break
        remaining = len([a for a in wd_alerts if a.get("tier") in ("critical","high","medium")]) - shown
        if remaining > 0:
            alert_lines += f'<div class="wd-alert" style="color:var(--fg-dim)">…+{remaining} more</div>'
        counts = wd_data.get("alert_counts", {})
        count_str = f'🔴{counts.get("critical",0)} 🟠{counts.get("high",0)} 🟡{counts.get("medium",0)}'
        no_alerts = not alert_lines
        wd_html = f"""
<div class="section">
  <div class="section-title">🛡️ WATCHDOG — {e(wd_ts)} ({e(wd_mode)}) — {count_str if not no_alerts else '✅ all clear'}</div>
  <div class="section-body">
    <div class="wd-checks">{chips}</div>
    {alert_lines if alert_lines else '<div style="color:var(--green);font-size:0.85rem">No integrity issues detected</div>'}
  </div>
</div>"""

    body = stats + types_section + presence_html + diff_html + port_change_html + security_html + vuln_summary_html + wd_html + health_html + top_html
    return page_wrap("DASHBOARD", body, "index")


# ─── Page: Host inventory (hosts.html) ───

def gen_hosts(scan):
    if not scan:
        return page_wrap("HOSTS", '<div class="section"><div class="section-body">NO DATA</div></div>', "hosts")

    hosts = scan["hosts"]
    enum_data = get_latest_enum()
    enum_hosts = enum_data.get("hosts", {}) if enum_data else {}

    rows = ""
    for ip, h in hosts.items():
        name = best_name(h)
        mac = h.get("mac","") or "—"
        latency = f'{h.get("latency_ms",0)}ms' if h.get("latency_ms") else "—"
        first_seen = short_date(h.get("first_seen","")) if h.get("first_seen") else "—"
        # Fingerprint summary from enum
        fp_html = ""
        eh = enum_hosts.get(ip, {})
        fp = eh.get("fingerprint", {})
        if fp:
            parts = []
            if fp.get("device_guess"):
                parts.append(f'<span style="color:var(--magenta);font-size:0.72rem">{e(fp["device_guess"])}</span>')
            elif fp.get("os_guess"):
                parts.append(f'<span style="color:var(--green);font-size:0.72rem">{e(fp["os_guess"])}</span>')
            sw = fp.get("software", [])
            if sw:
                label = sw[0].get("label","")
                if label:
                    parts.append(f'<span style="color:var(--fg-dim);font-size:0.7rem">{e(label)}</span>')
            fp_html = "<br>".join(parts) if parts else ""
        if eh.get("phone_hints"):
            fp_html = (fp_html + "<br>" if fp_html else "") + '<span style="color:var(--amber);font-size:0.7rem">📱 mobile</span>'
        if not fp_html:
            fp_html = '<span style="color:var(--fg-muted)">—</span>'
        rows += f"""<tr>
  <td style="white-space:nowrap">{ip_link(ip)}</td>
  <td class="mdns-name">{e(name) if name else '<span style="color:var(--fg-dim)">—</span>'}</td>
  <td style="font-size:0.75rem;white-space:nowrap">{e(mac)}</td>
  <td>{badge(h.get("device_type",""))}</td>
  <td style="text-align:center">{score_badge(h.get("security_score",100))}</td>
  <td>{port_chips(h.get("ports",[]), h.get("port_changes"))}</td>
  <td>{fp_html}</td>
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
        <th>IP Address</th><th>Name</th><th>MAC</th><th>Type</th><th>Score</th><th>Open Ports</th><th>Fingerprint</th><th>Since</th><th>Latency</th>
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

def gen_host_detail(ip, h, all_scans, enum_data=None, vuln_data=None, watchdog_data=None):
    safe_ip = ip.replace(".", "-")
    name = best_name(h)
    title_name = f" — {name}" if name else ""

    # Get enum info for this host
    enum_host = {}
    if enum_data and "hosts" in enum_data:
        enum_host = enum_data["hosts"].get(ip, {})

    # Get vuln info for this host
    vuln_host = {}
    if vuln_data and "hosts" in vuln_data:
        vuln_host = vuln_data["hosts"].get(ip, {})

    # Get watchdog alerts for this host
    wd_host_alerts = []
    if watchdog_data:
        for a in watchdog_data.get("alerts", []):
            if a.get("host") == ip:
                wd_host_alerts.append(a)

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

    # ─── Enumeration / fingerprint sections ───
    enum_html = ""
    if enum_host:
        enum_sections = []

        # Fingerprint summary (top-level overview)
        fp = enum_host.get("fingerprint", {})
        if fp:
            fp_parts = []
            if fp.get("os_guess"):
                fp_parts.append(f'<span class="fp-os">{e(fp["os_guess"])}</span>')
            if fp.get("device_guess"):
                fp_parts.append(f'<span class="fp-device">{e(fp["device_guess"])}</span>')
            fp_ids = ""
            for ident in fp.get("identifiers", []):
                fp_ids += f'<span class="fp-identifier">{e(ident)}</span>'
            sw_chips = ""
            for sw in fp.get("software", []):
                port_num = sw.get("port", "")
                label = sw.get("label", "")
                if port_num:
                    sw_chips += f'<span class="svc-version-chip"><span class="port-num">:{port_num}</span> {e(label)}</span>'
                else:
                    sw_chips += f'<span class="svc-version-chip">{e(label)}</span>'
            fp_body = '<div class="fp-summary">' + "".join(fp_parts) + '</div>'
            if sw_chips:
                fp_body += f'<div style="margin:6px 0">{sw_chips}</div>'
            if fp_ids:
                fp_body += f'<div style="margin-top:6px">{fp_ids}</div>'
            enum_sections.append(f"""
<div class="section">
  <div class="section-title">FINGERPRINT</div>
  <div class="section-body">{fp_body}</div>
</div>""")

        # Service versions (nmap -sV)
        services = enum_host.get("services", [])
        if services:
            svc_rows = ""
            for svc in sorted(services, key=lambda x: x.get("port", 0)):
                product = svc.get("product", "")
                version = svc.get("version", "")
                svc_name = svc.get("service", "")
                extra = svc.get("extrainfo", "")
                cpe = svc.get("cpe", "")
                label = product
                if version:
                    label += f" {version}"
                if not label:
                    label = svc_name
                cpe_html = f'<span style="color:var(--fg-muted);font-size:0.7rem;margin-left:8px">{e(cpe)}</span>' if cpe else ""
                extra_html = f'<span style="color:var(--fg-dim);font-size:0.78rem"> ({e(extra)})</span>' if extra else ""
                svc_rows += f'<tr><td><span class="port-chip">{svc.get("port","")}</span></td><td>{e(svc_name)}</td><td style="color:var(--green)">{e(label)}{extra_html}</td><td>{cpe_html}</td></tr>'
            enum_sections.append(f"""
<div class="section">
  <div class="section-title">SERVICE VERSIONS ({len(services)} detected)</div>
  <div class="section-body">
    <table class="host-table"><thead><tr><th>Port</th><th>Service</th><th>Product / Version</th><th>CPE</th></tr></thead>
    <tbody>{svc_rows}</tbody></table>
  </div>
</div>""")

        # HTTP fingerprints
        http_list = enum_host.get("http", [])
        if http_list:
            http_cards = ""
            for hf in http_list:
                port = hf.get("port", "")
                status = hf.get("status_code", 0)
                server = hf.get("server", "")
                title = hf.get("title", "")
                powered = hf.get("powered_by", "")
                gen = hf.get("generator", "")
                redirect = hf.get("redirect", "")
                fav = hf.get("favicon_hash", "")
                status_color = "var(--green)" if 200 <= status < 300 else ("var(--amber)" if 300 <= status < 400 else "var(--red)")
                lines = []
                lines.append(f'<div class="http-url">:{port} → <span style="color:{status_color}">{status}</span></div>')
                if server:
                    lines.append(f'<div class="http-server">Server: {e(server)}</div>')
                if title:
                    lines.append(f'<div class="http-title">Title: {e(title)}</div>')
                if powered:
                    lines.append(f'<div style="color:var(--fg-dim);font-size:0.78rem">X-Powered-By: {e(powered)}</div>')
                if gen:
                    lines.append(f'<div style="color:var(--fg-dim);font-size:0.78rem">Generator: {e(gen)}</div>')
                if redirect:
                    lines.append(f'<div style="color:var(--fg-dim);font-size:0.78rem">→ {e(redirect)}</div>')
                if fav:
                    lines.append(f'<div style="color:var(--fg-muted);font-size:0.7rem">Favicon: {e(fav[:16])}…</div>')
                http_cards += f'<div class="http-card">{"".join(lines)}</div>'
            enum_sections.append(f"""
<div class="section">
  <div class="section-title">HTTP FINGERPRINTS ({len(http_list)})</div>
  <div class="section-body">{http_cards}</div>
</div>""")

        # TLS certificates
        tls_list = enum_host.get("tls", [])
        if tls_list:
            tls_cards = ""
            for t in tls_list:
                port = t.get("port", "")
                cn = t.get("cn", "")
                org = t.get("org", "")
                issuer_cn = t.get("issuer_cn", "")
                issuer_org = t.get("issuer_org", "")
                not_after = t.get("not_after", "")
                proto = t.get("protocol", "")
                cipher = t.get("cipher", "")
                san = t.get("san", [])
                fp_sha = t.get("fingerprint_sha256", "")
                # Check if expired
                expired_cls = ""
                if not_after:
                    try:
                        from datetime import datetime as _dt
                        exp_date = _dt.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
                        if exp_date < _dt.now():
                            expired_cls = " expired"
                    except:
                        pass
                lines = []
                if cn:
                    lines.append(f'<div class="tls-cn">:{port} — {e(cn)}</div>')
                elif fp_sha:
                    lines.append(f'<div class="tls-cn">:{port} — [binary cert]</div>')
                if org:
                    lines.append(f'<div class="tls-detail">Org: {e(org)}</div>')
                if issuer_cn or issuer_org:
                    issuer = issuer_cn or issuer_org
                    lines.append(f'<div class="tls-detail">Issuer: {e(issuer)}</div>')
                if san:
                    san_str = ", ".join(san[:5])
                    if len(san) > 5:
                        san_str += f" +{len(san)-5} more"
                    lines.append(f'<div class="tls-detail">SAN: {e(san_str)}</div>')
                if not_after:
                    lines.append(f'<div class="tls-detail">Expires: {e(not_after)}</div>')
                if proto and cipher:
                    lines.append(f'<div class="tls-detail" style="color:var(--fg-muted)">{e(proto)} / {e(cipher)}</div>')
                if fp_sha:
                    lines.append(f'<div class="tls-detail" style="color:var(--fg-muted);font-size:0.7rem">SHA256: {e(fp_sha[:32])}…</div>')
                tls_cards += f'<div class="tls-card{expired_cls}">{"".join(lines)}</div>'
            enum_sections.append(f"""
<div class="section">
  <div class="section-title">TLS CERTIFICATES ({len(tls_list)})</div>
  <div class="section-body">{tls_cards}</div>
</div>""")

        # UPnP device info
        upnp = enum_host.get("upnp", {})
        if upnp:
            upnp_lines = []
            if upnp.get("friendly_name"):
                upnp_lines.append(f'<div class="upnp-name">{e(upnp["friendly_name"])}</div>')
            if upnp.get("manufacturer"):
                upnp_lines.append(f'<div class="upnp-mfr">{e(upnp["manufacturer"])}</div>')
            if upnp.get("model_name"):
                model = upnp["model_name"]
                if upnp.get("model_number"):
                    model += f' ({upnp["model_number"]})'
                upnp_lines.append(f'<div class="upnp-model">{e(model)}</div>')
            if upnp.get("model_description"):
                upnp_lines.append(f'<div style="color:var(--fg-dim);font-size:0.82rem">{e(upnp["model_description"])}</div>')
            if upnp.get("device_type"):
                upnp_lines.append(f'<div style="color:var(--fg-muted);font-size:0.78rem">{e(upnp["device_type"])}</div>')
            if upnp.get("serial_number"):
                upnp_lines.append(f'<div style="color:var(--fg-muted);font-size:0.72rem">S/N: {e(upnp["serial_number"])}</div>')
            ssdp = enum_host.get("ssdp", {})
            if ssdp.get("server"):
                upnp_lines.append(f'<div style="color:var(--fg-muted);font-size:0.72rem;margin-top:4px">SSDP: {e(ssdp["server"])}</div>')
            enum_sections.append(f"""
<div class="section">
  <div class="section-title">UPnP DEVICE</div>
  <div class="section-body"><div class="upnp-card">{"".join(upnp_lines)}</div></div>
</div>""")

        # TCP banners
        banners = enum_host.get("banners", [])
        if banners:
            banner_html = ""
            for b in banners:
                banner_html += f'<div class="banner-box"><span class="banner-port">:{b.get("port","")}</span> <span class="banner-text">{e(b.get("banner",""))}</span></div>'
            enum_sections.append(f"""
<div class="section">
  <div class="section-title">TCP BANNERS ({len(banners)})</div>
  <div class="section-body">{banner_html}</div>
</div>""")

        # mDNS TXT records (from enum, more detailed than base scan)
        mdns_txts = enum_host.get("mdns_txt", [])
        if mdns_txts:
            txt_rows = ""
            for mt in mdns_txts:
                txt_rows += f'<div class="banner-box"><span class="banner-port">{e(mt.get("service",""))}</span> <span class="banner-text">{e(mt.get("txt",""))}</span></div>'
            enum_sections.append(f"""
<div class="section">
  <div class="section-title">mDNS TXT RECORDS ({len(mdns_txts)})</div>
  <div class="section-body">{txt_rows}</div>
</div>""")

        # Phone / mobile hints
        phone_hints = enum_host.get("phone_hints", [])
        if phone_hints:
            chips = " ".join(f'<span class="phone-hint">📱 {e(hint)}</span>' for hint in phone_hints)
            enum_sections.append(f"""
<div class="section">
  <div class="section-title">PHONE / MOBILE INDICATORS</div>
  <div class="section-body">{chips}</div>
</div>""")

        # Probed timestamp
        probed_at = enum_host.get("probed_at", "")
        if enum_sections:
            enum_html = f"""
<div class="section">
  <div class="section-title">ENUMERATION DATA{' — probed ' + e(probed_at) if probed_at else ''}</div>
  <div class="section-body">
    {"".join(enum_sections)}
  </div>
</div>"""

    # ─── Vulnerability findings ───
    vuln_html = ""
    if vuln_host and vuln_host.get("findings"):
        findings = vuln_host["findings"]
        risk_score = vuln_host.get("risk_score", 100)
        sev_counts = vuln_host.get("severity_counts", {})
        risk_cls = "risk-ok" if risk_score >= 80 else ("risk-warn" if risk_score >= 50 else "risk-crit")

        # Summary bar
        summary = f"""<div class="risk-meter">
  <div class="risk-num {risk_cls}">{risk_score}</div>
  <div class="risk-label">/ 100 risk score</div>
</div>
<div class="vuln-summary-grid">
  <div class="vuln-count-box"><div class="vuln-count-num" style="color:#ff2244">{sev_counts.get('critical',0)}</div><div class="vuln-count-label">Critical</div></div>
  <div class="vuln-count-box"><div class="vuln-count-num" style="color:#ff6622">{sev_counts.get('high',0)}</div><div class="vuln-count-label">High</div></div>
  <div class="vuln-count-box"><div class="vuln-count-num" style="color:#ffbb33">{sev_counts.get('medium',0)}</div><div class="vuln-count-label">Medium</div></div>
  <div class="vuln-count-box"><div class="vuln-count-num" style="color:#667788">{sev_counts.get('low',0)}</div><div class="vuln-count-label">Low</div></div>
</div>"""

        # Individual findings
        finding_cards = ""
        for f in findings[:25]:  # Cap display at 25
            sev = f.get("severity", "low")
            cve = f.get("cve", "")
            cvss = f.get("cvss", 0)
            detail = f.get("detail", "")
            port = f.get("port", "")
            rec = f.get("recommendation", "")
            url = f.get("url", "")
            product = f.get("product", "")
            version = f.get("version", "")

            header_parts = [f'<span class="vuln-sev sev-{sev}">{sev}</span>']
            if cve:
                cve_link = f'<a href="{e(url)}" target="_blank" rel="noopener">{e(cve)}</a>' if url else e(cve)
                header_parts.append(f'<span class="vuln-cve">{cve_link}</span>')
            if cvss > 0:
                cvss_cls = "cvss-crit" if cvss >= 9 else ("cvss-high" if cvss >= 7 else ("cvss-med" if cvss >= 4 else "cvss-low"))
                header_parts.append(f'<span class="vuln-cvss {cvss_cls}">CVSS {cvss}</span>')
            if port:
                header_parts.append(f'<span class="port-chip">:{port}</span>')

            card = f'<div class="vuln-card vuln-{sev}"><div class="vuln-header">{"".join(header_parts)}</div>'
            card += f'<div class="vuln-detail">{e(detail)}</div>'
            if product and version:
                card += f'<div class="vuln-meta">{e(product)} {e(version)}</div>'
            if rec:
                card += f'<div class="vuln-fix">{e(rec)}</div>'
            card += '</div>'
            finding_cards += card

        if len(findings) > 25:
            finding_cards += f'<div style="color:var(--fg-dim);text-align:center;padding:8px">...and {len(findings)-25} more findings</div>'

        scanned_at = vuln_host.get("scanned_at", "")
        vuln_html = f"""
<div class="section">
  <div class="section-title">VULNERABILITY ASSESSMENT — {len(findings)} findings{' — scanned ' + e(scanned_at) if scanned_at else ''}</div>
  <div class="section-body">
    {summary}
    {finding_cards}
  </div>
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
""" + mdns_html + ports_html + enum_html + vuln_html

    # Watchdog alerts for this host
    wd_detail_html = ""
    if wd_host_alerts:
        wd_items = ""
        for a in wd_host_alerts:
            tier = a.get("tier", "info")
            icon = {"critical": "🔴", "high": "🟠", "medium": "🟡"}.get(tier, "🔵")
            cls = {"critical": "wd-crit", "high": "wd-warn", "medium": ""}.get(tier, "")
            wd_items += f'<div class="wd-alert {cls}">{icon} <b>{e(a.get("title",""))}</b>'
            if a.get("detail"):
                wd_items += f'<br><span style="color:var(--fg-dim);font-size:0.8rem">{e(a["detail"][:120])}</span>'
            wd_items += '</div>'
        wd_detail_html = f"""
<div class="section">
  <div class="section-title">🛡️ WATCHDOG ALERTS</div>
  <div class="section-body">{wd_items}</div>
</div>"""

    body += wd_detail_html + history_html

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

def gen_feed_page(feed_id, feed_cfg):
    """Generate a mailing list digest page for any configured feed."""
    feed_dir = os.path.join(DATA_DIR, feed_cfg.get("data_dir", feed_id))
    feed_name = feed_cfg.get("name", feed_id)
    feed_emoji = feed_cfg.get("emoji", "📰")
    lore_list = feed_cfg.get("lore_list", feed_id)
    page_slug = feed_cfg.get("page_slug", feed_id)
    about_url = feed_cfg.get("about_url", f"https://lore.kernel.org/{lore_list}/")
    about_text = feed_cfg.get("about_text", feed_cfg.get("description", ""))
    scored_label = feed_cfg.get("nav_label", feed_id.upper())  # for display

    # Load all available digests (newest first)
    digests = []
    if os.path.isdir(feed_dir):
        for fn in sorted(os.listdir(feed_dir), reverse=True):
            if fn.startswith("digest-") and fn.endswith(".json"):
                d = load_json(os.path.join(feed_dir, fn))
                if d:
                    digests.append(d)

    if not digests:
        body = f"""
<div class="section">
  <div class="section-title">{feed_emoji} {e(feed_name.upper())} MAILING LIST DIGEST</div>
  <div class="section-body">
    <div style="color:var(--fg-dim);text-align:center;padding:40px 0">
      <div style="font-size:3rem;margin-bottom:16px">{feed_emoji}</div>
      <div style="font-size:1.1rem;color:var(--amber)">No digests generated yet</div>
      <div style="margin-top:12px;color:var(--fg-dim);font-size:0.9rem">
        Daily digest — summarizing {e(lore_list)} mailing list<br>
        {e(about_text)}<br>
        Powered by local LLM via Ollama
      </div>
    </div>
  </div>
</div>"""
        return page_wrap(f"{scored_label} DIGEST", body, page_slug)

    # Latest digest — show full bulletin
    latest = digests[0]
    bulletin_raw = latest.get("bulletin", latest.get("bulletin_sent", ""))
    bulletin_html = e(bulletin_raw).replace("\n", "<br>")

    # Stats — support both old (camera_threads) and new (scored_threads) field names
    n_scored = latest.get("scored_threads", latest.get("camera_threads", "?"))
    stats_parts = []
    stats_parts.append(f'{latest.get("total_messages", "?")} messages')
    stats_parts.append(f'{latest.get("total_threads", "?")} threads')
    stats_parts.append(f'{n_scored} relevant')
    if latest.get("ollama_model"):
        stats_parts.append(f'Model: {e(latest["ollama_model"])}')
    llm_time = latest.get("total_llm_time_s", latest.get("ollama_time_s"))
    if llm_time:
        stats_parts.append(f'LLM: {llm_time}s')
    stats_line = " &nbsp;│&nbsp; ".join(stats_parts)

    latest_html = f"""
<div class="section">
  <div class="section-title">{feed_emoji} LATEST DIGEST — {e(latest.get('date', '?'))}</div>
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
  <div class="section-title">RELEVANT THREADS — detailed analysis</div>
  <div class="section-body">{detail_cards}</div>
</div>"""

    # Archive: previous digests
    archive_rows = ""
    for d in digests[1:30]:  # last 30, skip latest
        dt = e(d.get("date", "?"))
        msgs = d.get("total_messages", "?")
        cam = d.get("scored_threads", d.get("camera_threads", "?"))
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
      <thead><tr><th>Date</th><th>Msgs</th><th>Relevant</th><th>LLM</th><th>Preview</th></tr></thead>
      <tbody>{archive_rows}</tbody>
    </table>
  </div>
</div>"""

    # Info section
    info_html = f"""
<div class="section">
  <div class="section-title">ABOUT</div>
  <div class="section-body" style="font-size:0.85rem;color:var(--fg-dim)">
    {feed_emoji} Source: <a href="{e(about_url)}">{e(about_url.replace('https://', ''))}</a> &nbsp;│&nbsp;
    🕐 Daily digest &nbsp;│&nbsp;
    🤖 Local LLM summarization via Ollama &nbsp;│&nbsp;
    📱 Signal bulletin delivery<br>
    {e(about_text)}
  </div>
</div>"""

    body = latest_html + threads_html + archive_html + info_html
    return page_wrap(f"{scored_label} DIGEST", body, page_slug)


# ─── Page: Issues (issues.html) — GitHub/GitLab repo issue tracker ───

def gen_issues():
    """Generate the repository issues monitoring page."""
    if not REPO_FEEDS:
        body = """
<div class="section">
  <div class="section-title">🐛 REPOSITORY ISSUE TRACKER</div>
  <div class="section-body" style="text-align:center;padding:40px;color:var(--fg-dim)">
    No repository feeds configured (repo-feeds.json)
  </div>
</div>"""
        return page_wrap("ISSUES", body, "issues")

    all_cards = ""
    total_interesting = 0
    total_repos_checked = 0

    for rid, rcfg in REPO_FEEDS.items():
        repo_dir = os.path.join(DATA_DIR, rcfg.get("data_dir", f"repos/{rid}"))
        latest_path = os.path.join(repo_dir, "latest.json")
        repo_name = rcfg.get("name", rid)
        repo_emoji = rcfg.get("emoji", "📦")
        web_url = rcfg.get("web_url", "")

        data = load_json(latest_path) if os.path.exists(latest_path) else None

        if not data:
            all_cards += f"""
<div class="section">
  <div class="section-title">{repo_emoji} {e(repo_name.upper())}</div>
  <div class="section-body" style="color:var(--fg-dim);padding:16px">
    ⏳ Not checked yet — waiting for first repo-watch run
  </div>
</div>"""
            continue

        total_repos_checked += 1
        interesting = data.get("interesting", [])
        total_interesting += len(interesting)
        checked = data.get("checked", "?")
        total_items = data.get("total_items", 0)
        other_count = data.get("other_count", 0)

        # Build issue/MR cards
        item_rows = ""
        for item in interesting[:20]:
            iid = item.get("id", "?")
            itype = item.get("type", "issue")
            title = e(item.get("title", "?"))
            score = item.get("score", 0)
            author = e(item.get("author", "?"))
            url = item.get("url", "")
            labels = item.get("labels", [])
            comments = item.get("comments", 0)
            reactions = item.get("reactions", 0)
            keywords = item.get("keywords", [])
            is_new = item.get("is_new", False)
            body_preview = e(item.get("body_preview", "")[:200])

            type_icon = "🔀" if "merge" in itype or "pull" in itype else ("📩" if "patch" in itype else ("📦" if "series" in itype else "🐛"))
            new_badge = ' <span style="background:var(--green);color:#000;padding:1px 6px;font-size:0.75rem;font-weight:bold">NEW</span>' if is_new else ""
            score_cls = "green" if score >= 8 else ("amber" if score >= 4 else "fg-dim")
            kw_chips = " ".join(f'<span class="port-chip">{e(k)}</span>' for k in keywords[:5])
            label_chips = " ".join(f'<span style="background:var(--bg3);color:var(--magenta);padding:1px 4px;font-size:0.75rem;border:1px solid var(--border)">{e(l)}</span>' for l in labels[:4])

            link_html = f'<a href="{e(url)}" style="color:var(--cyan)">#{iid}</a>' if url else f"#{iid}"

            item_rows += f"""<div style="border:1px solid var(--border);background:var(--bg2);padding:10px;margin-bottom:8px">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:4px">
    <div>
      {type_icon} <span style="color:var(--{score_cls});font-weight:bold">[{score:.0f}]</span>
      {link_html} {title}{new_badge}
    </div>
    <span style="color:var(--fg-dim);font-size:0.8rem;white-space:nowrap">💬{comments} 👍{reactions}</span>
  </div>
  <div style="margin-top:4px;font-size:0.8rem;color:var(--fg-dim)">👤 {author}</div>
  <div style="margin-top:4px">{kw_chips} {label_chips}</div>
  {"<div style='margin-top:6px;font-size:0.82rem;color:var(--fg-dim);border-left:2px solid var(--border);padding-left:8px'>" + body_preview + "</div>" if body_preview else ""}
</div>"""

        web_link = f' &nbsp;│&nbsp; <a href="{e(web_url)}">{e(web_url.replace("https://", ""))}</a>' if web_url else ""

        all_cards += f"""
<div class="section">
  <div class="section-title">{repo_emoji} {e(repo_name.upper())} — {len(interesting)} interesting of {total_items} checked</div>
  <div class="section-body">
    <div style="margin-bottom:10px;font-size:0.82rem;color:var(--fg-dim)">
      Last check: {e(checked)} &nbsp;│&nbsp; Other: {other_count} low-relevance items{web_link}
    </div>
    {item_rows if item_rows else '<div style="color:var(--fg-dim);padding:12px">No interesting items found</div>'}
  </div>
</div>"""

    # Summary header
    summary_html = f"""
<div class="section">
  <div class="section-title">🐛 REPOSITORY ISSUE TRACKER</div>
  <div class="section-body">
    <div style="display:flex;gap:24px;flex-wrap:wrap;font-size:0.9rem">
      <div><span style="color:var(--green);font-size:1.5rem;font-weight:bold">{total_interesting}</span><br><span style="color:var(--fg-dim)">interesting items</span></div>
      <div><span style="color:var(--cyan);font-size:1.5rem;font-weight:bold">{len(REPO_FEEDS)}</span><br><span style="color:var(--fg-dim)">repos monitored</span></div>
      <div><span style="color:var(--amber);font-size:1.5rem;font-weight:bold">{total_repos_checked}</span><br><span style="color:var(--fg-dim)">checked today</span></div>
    </div>
    <div style="margin-top:12px;font-size:0.82rem;color:var(--fg-dim)">
      Monitoring: {', '.join(rcfg.get('name', rid) for rid, rcfg in REPO_FEEDS.items())} &nbsp;│&nbsp;
      Scored by keyword relevance + user interest profile
    </div>
  </div>
</div>"""

    body = summary_html + all_cards
    return page_wrap("ISSUES", body, "issues")


# ─── Page: Notes (notes.html) — LLM thinking/research notes ───

def gen_notes():
    """Generate the thinking notes / research insights page."""
    think_dir = os.path.join(DATA_DIR, "think")

    # Load notes index
    index_path = os.path.join(think_dir, "notes-index.json")
    index = []
    if os.path.exists(index_path):
        index = load_json(index_path) or []

    if not index:
        body = """
<div class="section">
  <div class="section-title">🧠 RESEARCH NOTES</div>
  <div class="section-body" style="text-align:center;padding:40px;color:var(--fg-dim)">
    <div style="font-size:3rem;margin-bottom:16px">🧠</div>
    <div style="font-size:1.1rem;color:var(--amber)">No thinking notes yet</div>
    <div style="margin-top:12px;color:var(--fg-dim);font-size:0.9rem">
      ClawdBot generates research notes during idle time:<br>
      weekly briefings, trend analysis, cross-feed insights, deep dives.<br>
      Notes will appear here after the first idle-think run.
    </div>
  </div>
</div>"""
        return page_wrap("NOTES", body, "notes")

    # Load actual note contents
    note_cards = ""
    type_icons = {
        "weekly": "📋", "trends": "📈",
        "crossfeed": "🔗", "research": "🔬",
        "career": "🎯", "crawl": "🌐",
        "learn": "🧠", "signal": "📡",
        "home": "🏠",
    }
    type_colors = {
        "weekly": "var(--green)", "trends": "var(--amber)",
        "crossfeed": "var(--cyan)", "research": "var(--magenta)",
        "career": "var(--amber)", "crawl": "var(--blue)",
        "learn": "var(--green)", "signal": "var(--red)",
        "home": "var(--cyan)",
    }

    for entry in index[:20]:
        note_path = os.path.join(think_dir, entry.get("file", ""))
        note = load_json(note_path) if os.path.exists(note_path) else None
        if not note:
            continue

        ntype = note.get("type", "note")
        title = e(note.get("title", "Untitled"))
        content = note.get("content", "")
        generated = e(note.get("generated", "?"))
        chars = len(content)
        icon = type_icons.get(ntype, "📝")
        color = type_colors.get(ntype, "var(--fg)")
        type_label = ntype.upper()

        # Format content: preserve whitespace, escape HTML
        content_html = e(content).replace("\n", "<br>")

        note_cards += f"""
<div class="section">
  <div class="section-title">
    {icon} <span style="color:{color}">[{type_label}]</span> {title}
    <span style="float:right;font-size:0.8rem;color:var(--fg-dim)">{generated} &nbsp;│&nbsp; {chars} chars</span>
  </div>
  <div class="section-body">
    <div style="font-family:monospace;white-space:pre-wrap;line-height:1.6;font-size:0.88rem;color:var(--fg)">{content_html}</div>
  </div>
</div>"""

    # Type distribution
    type_counts = {}
    for entry in index:
        t = entry.get("type", "other")
        type_counts[t] = type_counts.get(t, 0) + 1

    dist_html = " &nbsp;│&nbsp; ".join(
        f'{type_icons.get(t, "📝")} {t}: {c}' for t, c in sorted(type_counts.items())
    )

    summary = f"""
<div class="section">
  <div class="section-title">🧠 RESEARCH NOTES — ClawdBot thinking log</div>
  <div class="section-body">
    <div style="display:flex;gap:24px;flex-wrap:wrap;font-size:0.9rem">
      <div><span style="color:var(--green);font-size:1.5rem;font-weight:bold">{len(index)}</span><br><span style="color:var(--fg-dim)">total notes</span></div>
    </div>
    <div style="margin-top:8px;font-size:0.82rem;color:var(--fg-dim)">
      {dist_html}<br>
      Generated during idle time by local LLM &nbsp;│&nbsp; Auto-rotates: weekly → trends → crossfeed → research
    </div>
  </div>
</div>"""

    body = summary + note_cards
    return page_wrap("NOTES", body, "notes")


# ─── Page: GPU Load (load.html) ───

def load_gpu_samples(days=14):
    """Load GPU load TSV samples. Returns list of (datetime, status, model, script, vram_mb)."""
    tsv_path = os.path.join(DATA_DIR, "gpu-load.tsv")
    if not os.path.exists(tsv_path):
        return []
    samples = []
    cutoff = datetime.now() - timedelta(days=days)
    with open(tsv_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            try:
                ts = datetime.strptime(parts[0], "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            if ts < cutoff:
                continue
            status = parts[1] if len(parts) > 1 else "idle"
            model = parts[2] if len(parts) > 2 else ""
            script = parts[3] if len(parts) > 3 else ""
            vram = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
            samples.append((ts, status, model, script, vram))
    return samples


def gen_load():
    """Generate the GPU / system load statistics page."""
    samples = load_gpu_samples(14)

    if not samples:
        body = """
<div class="section">
  <div class="section-title">📊 GPU LOAD — Model Utilization</div>
  <div class="section-body" style="text-align:center;padding:40px;color:var(--fg-dim)">
    <div style="font-size:3rem;margin-bottom:16px">📊</div>
    <div style="font-size:1.1rem;color:var(--amber)">No load data yet</div>
    <div style="margin-top:12px;color:var(--fg-dim);font-size:0.9rem">
      gpu-monitor.sh samples Ollama utilization every minute.<br>
      Data will appear here after the first cron cycle.
    </div>
  </div>
</div>"""
        return page_wrap("LOAD", body, "load")

    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    # ─── Compute stats ───
    # Today
    today_samples = [(s, st, m, sc, v) for s, st, m, sc, v in samples if s.strftime("%Y-%m-%d") == today_str]
    today_busy = sum(1 for _, st, _, _, _ in today_samples if st == "busy")
    today_total = len(today_samples)
    today_pct = (today_busy / today_total * 100) if today_total > 0 else 0
    today_busy_min = today_busy  # 1 sample ≈ 1 minute

    # Last 7 days
    week_cutoff = now - timedelta(days=7)
    week_samples = [(s, st, m, sc, v) for s, st, m, sc, v in samples if s >= week_cutoff]
    week_busy = sum(1 for _, st, _, _, _ in week_samples if st == "busy")
    week_total = len(week_samples)
    week_pct = (week_busy / week_total * 100) if week_total > 0 else 0

    # Available capacity estimate: 1440 min/day, show how many more 2-min jobs could fit
    daily_capacity = 1440
    today_idle_min = today_total - today_busy if today_total > 0 else daily_capacity
    avg_job_min = 2  # typical Ollama call ≈ 1-2 minutes
    headroom_jobs = today_idle_min // avg_job_min

    # Per-script breakdown (last 7 days)
    script_minutes = {}
    for _, st, _, sc, _ in week_samples:
        if st == "busy" and sc:
            script_minutes[sc] = script_minutes.get(sc, 0) + 1

    # ─── Daily utilization for last 14 days ───
    daily_stats = {}
    for s, st, _, _, _ in samples:
        day = s.strftime("%Y-%m-%d")
        if day not in daily_stats:
            daily_stats[day] = {"busy": 0, "total": 0}
        daily_stats[day]["total"] += 1
        if st == "busy":
            daily_stats[day]["busy"] += 1

    # ─── Hourly heatmap (last 7 days, 24 hours × 7 days) ───
    # Build a grid: rows=hours (0-23), cols=days
    heatmap_days = []
    for i in range(6, -1, -1):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        heatmap_days.append(d)

    heatmap = {}  # (day, hour) → (busy, total)
    for s, st, _, _, _ in week_samples:
        day = s.strftime("%Y-%m-%d")
        hour = s.hour
        key = (day, hour)
        if key not in heatmap:
            heatmap[key] = [0, 0]
        heatmap[key][1] += 1
        if st == "busy":
            heatmap[key][0] += 1

    # ─── Build HTML ───

    # Utilization gauge color
    def pct_color(p):
        if p < 25:
            return "var(--green)"
        elif p < 60:
            return "var(--amber)"
        else:
            return "var(--red)"

    def pct_class(p):
        if p < 25:
            return ""
        elif p < 60:
            return "amber"
        else:
            return "red"

    # Stats boxes
    stats_html = f"""
<div class="section">
  <div class="section-title">📊 GPU LOAD — Ollama Model Utilization on bc250</div>
  <div class="section-body">
    <div class="stats-grid">
      <div class="stat-box">
        <div class="stat-val {pct_class(today_pct)}">{today_pct:.0f}%</div>
        <div class="stat-label">today util</div>
      </div>
      <div class="stat-box">
        <div class="stat-val {pct_class(week_pct)}">{week_pct:.0f}%</div>
        <div class="stat-label">7-day util</div>
      </div>
      <div class="stat-box">
        <div class="stat-val cyan">{today_busy_min}</div>
        <div class="stat-label">busy min today</div>
      </div>
      <div class="stat-box">
        <div class="stat-val">{today_total}</div>
        <div class="stat-label">samples today</div>
      </div>
      <div class="stat-box">
        <div class="stat-val" style="color:var(--green)">{headroom_jobs}</div>
        <div class="stat-label">est. free slots</div>
      </div>
      <div class="stat-box">
        <div class="stat-val">{len(samples)}</div>
        <div class="stat-label">total samples</div>
      </div>
    </div>
    <div style="margin-top:10px;font-size:0.82rem;color:var(--fg-dim)">
      1 sample = 1 minute &nbsp;│&nbsp; free slots = idle minutes ÷ ~{avg_job_min} min/job &nbsp;│&nbsp;
      Sampling started {samples[0][0].strftime('%Y-%m-%d %H:%M')}
    </div>
  </div>
</div>"""

    # ─── Heatmap ───
    heatmap_html = '<div class="section"><div class="section-title">🗓 HOURLY HEATMAP — last 7 days</div><div class="section-body">'
    heatmap_html += '<div style="overflow-x:auto"><table style="border-collapse:collapse;font-size:0.75rem;width:100%">'
    # Header row: day labels
    heatmap_html += '<tr><td style="padding:2px 6px;color:var(--fg-dim)">Hour</td>'
    day_labels = [(datetime.strptime(d, "%Y-%m-%d")).strftime("%a %d") for d in heatmap_days]
    for dl in day_labels:
        heatmap_html += f'<td style="padding:2px 4px;color:var(--fg-dim);text-align:center;min-width:60px">{dl}</td>'
    heatmap_html += '</tr>'
    # Rows: one per hour
    for h in range(24):
        heatmap_html += f'<tr><td style="padding:2px 6px;color:var(--fg-dim);text-align:right">{h:02d}:00</td>'
        for d in heatmap_days:
            key = (d, h)
            busy, total = heatmap[key] if key in heatmap else (0, 0)
            if total == 0:
                # No data (future or not yet sampled)
                bg = "var(--bg)"
                label = "·"
                fg = "var(--fg-dim)"
            else:
                pct = busy / total * 100
                if pct == 0:
                    bg = "#0d1a0d"
                    label = "░"
                    fg = "#224422"
                elif pct < 25:
                    bg = "#1a2a1a"
                    label = f"{busy}m"
                    fg = "var(--green)"
                elif pct < 50:
                    bg = "#2a3a1a"
                    label = f"{busy}m"
                    fg = "var(--green)"
                elif pct < 75:
                    bg = "#3a3a1a"
                    label = f"{busy}m"
                    fg = "var(--amber)"
                else:
                    bg = "#3a2a1a"
                    label = f"{busy}m"
                    fg = "var(--amber)"
            heatmap_html += f'<td style="padding:2px 4px;text-align:center;background:{bg};color:{fg};border:1px solid var(--border)">{label}</td>'
        heatmap_html += '</tr>'
    heatmap_html += '</table></div>'
    heatmap_html += '<div style="margin-top:8px;font-size:0.78rem;color:var(--fg-dim)">░ = idle &nbsp;│&nbsp; Nm = N minutes busy in that hour &nbsp;│&nbsp; · = no data</div>'
    heatmap_html += '</div></div>'

    # ─── Daily bar chart ───
    sorted_days = sorted(daily_stats.keys())[-14:]
    bar_html = '<div class="section"><div class="section-title">📈 DAILY UTILIZATION — last 14 days</div><div class="section-body">'
    for day in sorted_days:
        ds = daily_stats[day]
        pct = (ds["busy"] / ds["total"] * 100) if ds["total"] > 0 else 0
        busy_h = ds["busy"] / 60  # convert min to hours
        total_h = ds["total"] / 60
        is_today = day == today_str
        day_label = datetime.strptime(day, "%Y-%m-%d").strftime("%a %d")
        # Bar width proportional to utilization
        bar_w = max(pct, 1)
        bar_color = pct_color(pct)
        today_marker = " ◀" if is_today else ""
        bar_html += f'''<div style="display:flex;align-items:center;gap:8px;margin:3px 0;font-size:0.82rem">
  <span style="width:52px;text-align:right;color:{'var(--green)' if is_today else 'var(--fg-dim)'};flex-shrink:0">{day_label}</span>
  <div style="flex:1;height:16px;background:var(--bg);border:1px solid var(--border);position:relative">
    <div style="height:100%;width:{bar_w}%;background:{bar_color};opacity:0.7"></div>
  </div>
  <span style="width:90px;text-align:right;color:var(--fg-dim);flex-shrink:0">{pct:.0f}% ({ds['busy']}m){today_marker}</span>
</div>'''
    bar_html += '</div></div>'

    # ─── Per-script breakdown ───
    script_html = '<div class="section"><div class="section-title">⚙ PER-SCRIPT BREAKDOWN — last 7 days</div><div class="section-body">'
    if script_minutes:
        total_busy_week = sum(script_minutes.values())
        script_icons = {
            "lore-digest": "📨", "repo-watch": "👁", "idle-think": "🧠",
            "report": "📊", "manual": "🖐",
        }
        # Sort by minutes descending
        sorted_scripts = sorted(script_minutes.items(), key=lambda x: -x[1])
        for sc, mins in sorted_scripts:
            sc_pct = (mins / total_busy_week * 100) if total_busy_week > 0 else 0
            icon = script_icons.get(sc, "🔧")
            hours = mins / 60
            bar_w = max(sc_pct, 2)
            script_html += f'''<div style="display:flex;align-items:center;gap:8px;margin:4px 0;font-size:0.85rem">
  <span style="width:120px;color:var(--cyan);flex-shrink:0">{icon} {e(sc)}</span>
  <div style="flex:1;height:14px;background:var(--bg);border:1px solid var(--border)">
    <div style="height:100%;width:{bar_w}%;background:var(--cyan);opacity:0.6"></div>
  </div>
  <span style="width:110px;text-align:right;color:var(--fg-dim);flex-shrink:0">{mins}m ({hours:.1f}h) {sc_pct:.0f}%</span>
</div>'''
        script_html += f'<div style="margin-top:8px;font-size:0.82rem;color:var(--fg-dim)">Total busy: {total_busy_week} min ({total_busy_week/60:.1f}h) over 7 days</div>'
    else:
        script_html += '<div style="color:var(--fg-dim)">No per-script data yet (need busy samples with script identification)</div>'
    script_html += '</div></div>'

    # ─── Capacity planning ───
    cron_jobs = [
        ("lore-digest", "04:00", "~8–15 min", "Daily digest of mailing list feeds"),
        ("repo-watch ×3", "00:00, 06:00, 12:00", "~5–10 min", "Silent repo monitoring"),
        ("repo-watch +notify", "18:00", "~5–10 min", "Daily repo digest + Signal alert"),
        ("idle-think ×2", "10:00, 15:00", "~1–3 min", "Research / career / crawl / learn"),
        ("ha-journal ×4", "01:30, 07:30, 13:30, 19:30", "~1–2 min", "Home Assistant observation journal"),
        ("report", "08:00", "~1 min", "Morning health report"),
    ]
    cap_html = '<div class="section"><div class="section-title">🔧 SCHEDULED JOBS &amp; CAPACITY</div><div class="section-body">'
    cap_html += '<table class="host-table"><thead><tr>'
    cap_html += '<th>Job</th><th>Schedule</th><th>Est. Duration</th><th>Purpose</th>'
    cap_html += '</tr></thead><tbody>'
    for name, sched, dur, purpose in cron_jobs:
        cap_html += f'<tr><td style="color:var(--cyan)">{e(name)}</td><td>{e(sched)}</td><td>{e(dur)}</td><td style="color:var(--fg-dim)">{e(purpose)}</td></tr>'
    cap_html += '</tbody></table>'
    # Estimated total
    cap_html += f'''<div style="margin-top:12px;font-size:0.85rem">
  <span style="color:var(--fg-dim)">Estimated daily GPU time:</span>
  <span style="color:var(--amber)">~30–55 min</span>
  <span style="color:var(--fg-dim)">out of 1440 min (</span><span style="color:var(--green)">~2–4%</span><span style="color:var(--fg-dim)">)</span>
  <br>
  <span style="color:var(--fg-dim)">Headroom for additional jobs:</span>
  <span style="color:var(--green)">very high</span>
  <span style="color:var(--fg-dim)">— could comfortably add 10–20× more tasks</span>
</div>'''
    cap_html += '</div></div>'

    # ─── Recent activity log ───
    recent = samples[-60:]  # last 60 samples
    recent.reverse()
    log_html = '<div class="section"><div class="section-title">📋 RECENT SAMPLES — last 60 minutes</div><div class="section-body">'
    log_html += '<div class="log-view">'
    for ts, st, model, script, vram in recent:
        ts_str = ts.strftime("%H:%M")
        if st == "busy":
            vram_str = f" [{vram}MB]" if vram else ""
            script_str = f" ← {script}" if script else ""
            log_html += f'<span class="log-ts">{ts_str}</span> <span style="color:var(--amber)">■ BUSY</span> {e(model)}{vram_str}{script_str}\n'
        else:
            log_html += f'<span class="log-ts">{ts_str}</span> <span style="color:#224422">□ idle</span>\n'
    log_html += '</div></div></div>'

    body = stats_html + heatmap_html + bar_html + script_html + cap_html + log_html
    return page_wrap("LOAD", body, "load")


# Keep backward compat alias
def gen_lkml():
    if "linux-media" in DIGEST_FEEDS:
        return gen_feed_page("linux-media", DIGEST_FEEDS["linux-media"])
    return page_wrap("LKML DIGEST", '<div class="section"><div class="section-body">No feed config</div></div>', "lkml")


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
    enum_data = get_latest_enum()
    vuln_data = get_latest_vuln()
    watchdog_data = get_latest_watchdog()

    # Main pages
    pages = {
        "index.html": lambda: gen_dashboard(all_scans),
        "hosts.html": lambda: gen_hosts(scan),
        "presence.html": gen_presence,
        "security.html": lambda: gen_security(scan),
        "history.html": lambda: gen_history(all_scans),
        "log.html": gen_log,
        "notes.html": gen_notes,
        "load.html": gen_load,
    }
    # Dynamic feed pages from digest-feeds.json
    for fid, fcfg in DIGEST_FEEDS.items():
        slug = fcfg.get("page_slug", fid)
        pages[f"{slug}.html"] = (lambda _fid=fid, _fcfg=fcfg: gen_feed_page(_fid, _fcfg))
    # Issues page (only if repo feeds configured)
    if REPO_FEEDS:
        pages["issues.html"] = gen_issues
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
            html = gen_host_detail(ip, h, all_scans, enum_data, vuln_data, watchdog_data)
            path = os.path.join(WEB_DIR, "host", f"{safe_ip}.html")
            with open(path, "w") as f:
                f.write(html)
            host_count += 1

    total_pages = len(pages) + host_count
    print(f"Dashboard generated: {total_pages} pages ({len(pages)} main + {host_count} host details) in {WEB_DIR}/")

if __name__ == "__main__":
    main()
