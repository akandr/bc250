#!/usr/bin/env python3
"""
generate-html.py — Phrack/BBS-style network dashboard generator
v3: security page, per-host detail pages, mDNS names, port change display,
    persistent inventory stats, security scoring display.
Reads scan JSON from /opt/netscan/data/, outputs static HTML to /opt/netscan/web/
Location on bc250: /opt/netscan/generate-html.py
"""
import json, os, glob, re, base64, urllib.parse
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

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
    nav_items.append(("/issues.html", "ISSUES", "issues"))
    nav_items.append(("/home.html", "HOME", "home"))
    nav_items.append(("/notes.html", "NOTES", "notes"))
    nav_items.append(("/academic.html", "ACADEMIC", "academic"))
    nav_items.append(("/radio.html", "RADIO", "radio"))
    nav_items.append(("/events.html", "EVENTS", "events"))
    nav_items.append(("/career.html", "CAREERS", "career"))
    nav_items.append(("/car.html", "CAR", "car"))
    nav_items.append(("/advisor.html", "ADVISOR", "advisor"))
    nav_items.append(("/load.html", "LOAD", "load"))
    nav_items.append(("/leaks.html", "LEAKS", "leaks"))
    nav_items.append(("/weather.html", "WEATHER", "weather"))
    nav_items.append(("/news.html", "NEWS", "news"))
    nav_items.append(("/health.html", "HEALTH", "health"))
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

def format_dual_timestamps(meta):
    """Format scrape+analyze timestamps for dashboard display.
    Falls back to single timestamp if dual timestamps not available."""
    scrape_ts = meta.get("scrape_timestamp", "")[:16] if meta.get("scrape_timestamp") else ""
    analyze_ts = meta.get("analyze_timestamp", meta.get("timestamp", ""))[:16] if meta.get("analyze_timestamp") or meta.get("timestamp") else ""
    if scrape_ts and analyze_ts and scrape_ts != analyze_ts:
        return f'🔍 {e(scrape_ts.replace("T"," "))} · 🧠 {e(analyze_ts.replace("T"," "))}'
    ts = analyze_ts or scrape_ts
    return f'{e(ts.replace("T"," "))}' if ts else "?"

def timestamp_health_color(meta):
    """Return CSS color based on data freshness.
    Green: scraped AND analyzed within 36h
    Yellow: scraped within 36h, analysis older
    Red: scraping older than 48h"""
    now = datetime.now()
    scrape_ts = meta.get("scrape_timestamp", "") or meta.get("timestamp", "")
    analyze_ts = meta.get("analyze_timestamp", "") or meta.get("timestamp", "")
    try:
        scrape_dt = datetime.fromisoformat(scrape_ts[:19]) if scrape_ts else None
    except (ValueError, TypeError):
        scrape_dt = None
    try:
        analyze_dt = datetime.fromisoformat(analyze_ts[:19]) if analyze_ts else None
    except (ValueError, TypeError):
        analyze_dt = None
    if not scrape_dt:
        return "var(--fg-dim)"  # unknown
    scrape_age_h = (now - scrape_dt).total_seconds() / 3600
    if scrape_age_h > 48:
        return "#f44"  # red
    if analyze_dt:
        analyze_age_h = (now - analyze_dt).total_seconds() / 3600
        if analyze_age_h > 36:
            return "#fa0"  # yellow
    if scrape_age_h <= 36:
        return "#4f4"  # green
    return "#fa0"  # yellow


# ─── Page: Home (home.html) ──────────────────────────────────────────────

def gen_home():
    """Generate the Home Assistant + Neighborhood dashboard page."""
    import glob as _glob

    # ── Load HA correlate data ────────────────────────────────────────────
    correlate_path = os.path.join(DATA_DIR, "correlate", "latest-correlate.json")
    correlate = load_json(correlate_path) or {}

    # ── Load HA journal notes (type=home) ─────────────────────────────────
    think_dir = os.path.join(DATA_DIR, "think")
    home_notes = []
    if os.path.isdir(think_dir):
        for fp in sorted(_glob.glob(os.path.join(think_dir, "note-home-*.json")))[-5:]:
            if "insights" in os.path.basename(fp):
                continue  # skip insight notes — loaded separately below
            try:
                with open(fp) as f:
                    note = json.load(f)
                if isinstance(note, dict) and note.get("content"):
                    home_notes.append(note)
            except:
                pass

    # ── Load HA correlate insights notes ──────────────────────────────────
    insight_notes = []
    if os.path.isdir(think_dir):
        for fp in sorted(_glob.glob(os.path.join(think_dir, "note-home-insights-*.json")))[-3:]:
            try:
                with open(fp) as f:
                    note = json.load(f)
                if isinstance(note, dict) and note.get("content", note.get("text", "")):
                    insight_notes.append(note)
            except:
                pass

    # ── Load car tracker data ─────────────────────────────────────────────
    car_path = os.path.join(DATA_DIR, "car-tracker", "latest-car-tracker.json")
    car_data = load_json(car_path) or {}

    # ── Load city-watch data ──────────────────────────────────────────────
    city_path = os.path.join(DATA_DIR, "city", "latest-city.json")
    city_data = load_json(city_path) or {}

    # ── Load city-watch think notes ───────────────────────────────────────
    city_notes = []
    if os.path.isdir(think_dir):
        for fp in sorted(_glob.glob(os.path.join(think_dir, "note-city-watch-*.json")))[-3:]:
            try:
                with open(fp) as f:
                    note = json.load(f)
                if isinstance(note, dict) and note.get("content"):
                    city_notes.append(note)
            except:
                pass

    # ════════════════════════════════════════════════════════════════════════
    # Section 1: Climate Overview (from correlate sensor_stats)
    # ════════════════════════════════════════════════════════════════════════
    sensor_stats = correlate.get("sensor_stats", {})
    sparse_sensors = correlate.get("sparse_sensors", {})
    all_sensors = {**sensor_stats, **sparse_sensors}

    temps = {eid: s for eid, s in all_sensors.items() if s.get("group") == "temperature"}
    humidity = {eid: s for eid, s in all_sensors.items() if s.get("group") == "humidity"}
    co2_sensors = {eid: s for eid, s in all_sensors.items() if s.get("group") == "co2"}
    voc_sensors = {eid: s for eid, s in all_sensors.items() if s.get("group") == "voc"}
    pm25_sensors = {eid: s for eid, s in all_sensors.items() if s.get("group") == "pm25"}

    corr_ts = correlate.get("generated", "")[:16]

    # Temperature cards
    temp_cards = ""
    for eid, s in sorted(temps.items(), key=lambda x: x[1].get("room", "")):
        room = e(s.get("room", "?"))
        curr = s.get("current", 0)
        mean = s.get("mean", 0)
        trend = s.get("trend", 0)
        trend_arrow = "↗" if trend > 0.5 else ("↘" if trend < -0.5 else "→")
        # Color: cold < 18, ok 18-24, warm > 24
        tc = "var(--cyan)" if curr < 18 else ("var(--green)" if curr <= 24 else "var(--red)")
        temp_cards += f"""<div style="background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:10px;min-width:120px;text-align:center">
          <div style="color:var(--fg-dim);font-size:0.8rem">{room}</div>
          <div style="color:{tc};font-size:1.6rem;font-weight:bold">{curr:.1f}°C</div>
          <div style="color:var(--fg-dim);font-size:0.75rem">{trend_arrow} {trend:+.1f}°C · avg {mean:.1f}°C</div>
        </div>"""

    # Air quality cards
    aq_cards = ""
    for eid, s in sorted(list(co2_sensors.items()) + list(voc_sensors.items()) + list(pm25_sensors.items()),
                          key=lambda x: x[1].get("room", "")):
        room = e(s.get("room", "?"))
        group = s.get("group", "?")
        curr = s.get("current", 0)
        unit = s.get("unit", "")
        # Concern thresholds
        if group == "co2":
            color = "var(--green)" if curr < 800 else ("var(--amber)" if curr < 1200 else "var(--red)")
            icon = "💨"
            label = "CO₂"
        elif group == "voc":
            try:
                val = float(curr)
            except (ValueError, TypeError):
                val = 0
            color = "var(--green)" if val < 0.3 else ("var(--amber)" if val < 0.5 else "var(--red)")
            icon = "🧪"
            label = "VOC"
        elif group == "pm25":
            try:
                val = float(curr)
            except (ValueError, TypeError):
                val = 0
            color = "var(--green)" if val < 15 else ("var(--amber)" if val < 25 else "var(--red)")
            icon = "🌫️"
            label = "PM2.5"
        else:
            color = "var(--fg-dim)"
            icon = "📊"
            label = group.upper()

        aq_cards += f"""<div style="background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:10px;min-width:120px;text-align:center">
          <div style="color:var(--fg-dim);font-size:0.8rem">{icon} {room}</div>
          <div style="color:{color};font-size:1.4rem;font-weight:bold">{curr} {e(unit)}</div>
          <div style="color:var(--fg-dim);font-size:0.75rem">{label}</div>
        </div>"""

    climate_section = f"""
<div class="section">
  <div class="section-title">🌡️ CLIMATE & AIR QUALITY <span style="color:var(--fg-dim);font-size:0.8rem">// {e(corr_ts)}</span></div>
  <div class="section-body">
    <div style="margin-bottom:12px;color:var(--fg-dim);font-size:0.85rem">Temperatures (24h)</div>
    <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px">{temp_cards}</div>
    <div style="margin-bottom:12px;color:var(--fg-dim);font-size:0.85rem">Air Quality</div>
    <div style="display:flex;gap:12px;flex-wrap:wrap">{aq_cards}</div>
  </div>
</div>"""

    # ════════════════════════════════════════════════════════════════════════
    # Section 2: Room Usage (from correlate room_usage)
    # ════════════════════════════════════════════════════════════════════════
    room_usage = correlate.get("room_usage", {})
    usage_rows = ""
    for room, u in sorted(room_usage.items(), key=lambda x: -x[1].get("lit_hours", 0)):
        lit = u.get("lit_hours", 0)
        switches = u.get("switch_on_count", 0)
        peak = u.get("peak_hour", "")
        first_act = u.get("first_activity", "")
        last_act = u.get("last_activity", "")
        bar_pct = min(100, lit / 16 * 100) if lit else 0
        bar_color = "var(--cyan)" if lit < 4 else ("var(--amber)" if lit < 8 else "var(--green)")
        usage_rows += f"""<tr>
          <td style="color:var(--cyan)">{e(room)}</td>
          <td style="text-align:right">{lit:.1f}h</td>
          <td><div style="background:var(--bg2);border-radius:3px;height:12px;width:100px">
            <div style="background:{bar_color};border-radius:3px;height:12px;width:{bar_pct:.0f}px"></div>
          </div></td>
          <td style="text-align:center;color:var(--fg-dim)">{switches}</td>
          <td style="color:var(--fg-dim)">{first_act}–{last_act}</td>
          <td style="color:var(--fg-dim)">{peak}:00</td>
        </tr>"""

    usage_section = ""
    if usage_rows:
        usage_section = f"""
<div class="section">
  <div class="section-title">🏠 ROOM USAGE (24h)</div>
  <div class="section-body">
    <table class="host-table">
      <thead><tr><th>Room</th><th>Lit</th><th></th><th>Switches</th><th>Active</th><th>Peak</th></tr></thead>
      <tbody>{usage_rows}</tbody>
    </table>
  </div>
</div>"""

    # ════════════════════════════════════════════════════════════════════════
    # Section 3: Garage & Vehicle (from correlate garage_events)
    # ════════════════════════════════════════════════════════════════════════
    garage_events = correlate.get("garage_events", [])
    garage_html = ""
    if garage_events:
        ge_cards = ""
        for ge in garage_events:
            emoji = "🚗" if ge.get("type") == "car_returned" else "🚙💨"
            ge_type = ge.get("type", "?").replace("_", " ").title()
            ge_cards += f"""<div style="background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:10px;margin-bottom:6px">
              <span style="font-size:1.2rem">{emoji}</span>
              <span style="color:var(--cyan);font-weight:bold">{e(ge.get('time_local', '?'))}</span>
              <span style="color:var(--amber);margin-left:8px">{e(ge_type)}</span>
              <span style="color:var(--fg-dim);margin-left:8px;font-size:0.85rem">{e(ge.get('detail', ''))}</span>
            </div>"""
        garage_html = f"""
<div class="section">
  <div class="section-title">🚗 GARAGE & VEHICLE</div>
  <div class="section-body">{ge_cards}</div>
</div>"""

    # ════════════════════════════════════════════════════════════════════════
    # Section 3b: Car Tracker (compact summary → links to /car.html)
    # ════════════════════════════════════════════════════════════════════════
    car_html = ""
    car_status = car_data.get("current_status", {})
    car_trips = car_data.get("trips", [])
    car_mileage = car_data.get("mileage", {})
    car_meta = car_data.get("meta", {})
    car_generated = car_data.get("generated", "")[:16]

    if car_status or car_trips:
        # Compact status line
        status_line = ""
        if car_status:
            is_moving = car_status.get("is_moving", False)
            status_emoji = "🏃" if is_moving else "🅿️"
            status_text = f"{car_status.get('speed_kmh', 0)} km/h" if is_moving else "Parked"
            status_color = "var(--amber)" if is_moving else "var(--green)"
            parked_info = ""
            if car_status.get("parked_duration_h"):
                parked_info = f' · {car_status["parked_duration_h"]:.1f}h'
            location = e(car_status.get("location", "?"))
            odo = car_status.get("total_mileage_km", 0)
            status_line = f"""<div style="display:flex;gap:12px;align-items:center;margin-bottom:12px">
              <span style="font-size:1.6rem">{status_emoji}</span>
              <span style="color:{status_color};font-weight:bold">{status_text}{parked_info}</span>
              <span style="color:var(--cyan)">{location}</span>
              <span style="color:var(--fg-dim);font-size:0.8rem">ODO: {odo:,.0f} km</span>
            </div>"""

        # Quick stats
        avg_km = car_mileage.get("avg_km", 0) if car_mileage else 0
        zero_days = car_mileage.get("zero_days", 0) if car_mileage else 0
        stats_line = f"""<div style="display:flex;gap:24px;font-size:0.9rem;margin-bottom:8px">
          <span><span style="color:var(--green);font-weight:bold">{len(car_trips)}</span> <span style="color:var(--fg-dim)">trips ({car_meta.get('track_days', 0)}d)</span></span>
          <span><span style="color:var(--cyan);font-weight:bold">{avg_km:.1f}</span> <span style="color:var(--fg-dim)">km/day avg</span></span>
          <span><span style="color:var(--fg-dim)">{zero_days} idle days</span></span>
        </div>"""

        # Last 3 trips inline
        last_trips = ""
        for t in car_trips[-3:][::-1]:
            from_loc = e(t.get("start_location", "?"))[:30]
            to_loc = e(t.get("end_location", "?"))[:30]
            dist = t.get("distance_km", 0)
            ts = t.get("start_ts", "?")[5:]
            last_trips += f'<div style="font-size:0.82rem;color:var(--fg-dim)">{ts} <span style="color:var(--cyan)">{from_loc}</span> → <span style="color:var(--cyan)">{to_loc}</span> {dist:.1f} km</div>'

        track_pts = car_meta.get("track_points", 0)
        car_html = f"""
<div class="section">
  <div class="section-title">🚗 CAR TRACKER — <a href="/car.html" style="color:var(--amber)">full dashboard →</a> <span style="color:var(--fg-dim);font-size:0.8rem">// {e(car_generated)} · {track_pts} pts</span></div>
  <div class="section-body">
    {status_line}
    {stats_line}
    {last_trips}
  </div>
</div>"""

    # ════════════════════════════════════════════════════════════════════════
    # Section 4: AI Analysis (latest LLM insights from correlate + journal)
    # ════════════════════════════════════════════════════════════════════════
    llm_analysis = correlate.get("llm_analysis", "")
    analysis_html = ""
    if llm_analysis:
        # Convert markdown to basic HTML
        import re as _re
        formatted = e(llm_analysis)
        formatted = _re.sub(r'^## (.+)$', r'<h3 style="color:var(--amber);margin:16px 0 8px">\1</h3>', formatted, flags=_re.MULTILINE)
        formatted = _re.sub(r'^- (.+)$', r'<div style="padding-left:16px;margin:2px 0">• \1</div>', formatted, flags=_re.MULTILINE)
        formatted = _re.sub(r'\*\*([^*]+)\*\*', r'<strong style="color:var(--cyan)">\1</strong>', formatted)
        formatted = formatted.replace("\n\n", "<br><br>").replace("\n", "<br>")
        analysis_html = f"""
<div class="section">
  <div class="section-title">🤖 AI HOME ANALYSIS</div>
  <div class="section-body" style="font-size:0.88rem;line-height:1.5">{formatted}</div>
</div>"""

    # ════════════════════════════════════════════════════════════════════════
    # Section 5: Latest Home Journal Notes
    # ════════════════════════════════════════════════════════════════════════
    journal_html = ""
    latest_notes = (home_notes + insight_notes)
    latest_notes.sort(key=lambda n: n.get("generated", ""), reverse=True)
    if latest_notes:
        import re as _re
        note_cards = ""
        for note in latest_notes[:3]:
            title = e(note.get("title", "Home Journal"))
            ts = e(note.get("generated", "")[:16])
            content = note.get("content", note.get("text", ""))
            # Simple markdown rendering
            formatted = e(content[:1500])
            formatted = _re.sub(r'^#### (.+)$', r'<div style="color:var(--amber);font-weight:bold;margin:12px 0 4px">\1</div>', formatted, flags=_re.MULTILINE)
            formatted = _re.sub(r'^### (.+)$', r'<div style="color:var(--amber);font-weight:bold;font-size:1.05rem;margin:12px 0 4px">\1</div>', formatted, flags=_re.MULTILINE)
            formatted = _re.sub(r'^- (.+)$', r'<div style="padding-left:16px;margin:2px 0">• \1</div>', formatted, flags=_re.MULTILINE)
            formatted = _re.sub(r'\*\*([^*]+)\*\*', r'<strong style="color:var(--cyan)">\1</strong>', formatted)
            formatted = formatted.replace("\n\n", "<br><br>").replace("\n", "<br>")

            note_cards += f"""<div style="background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:14px;margin-bottom:10px">
              <div style="display:flex;justify-content:space-between;margin-bottom:8px">
                <span style="color:var(--amber);font-weight:bold">{title}</span>
                <span style="color:var(--fg-dim);font-size:0.8rem">{ts}</span>
              </div>
              <div style="font-size:0.85rem;line-height:1.5;max-height:400px;overflow-y:auto">{formatted}</div>
            </div>"""

        journal_html = f"""
<div class="section">
  <div class="section-title">📝 HOME JOURNAL (latest observations)</div>
  <div class="section-body">{note_cards}</div>
</div>"""

    # ════════════════════════════════════════════════════════════════════════
    # Section 6: Neighborhood Watch (SkyscraperCity city-watch data)
    # ════════════════════════════════════════════════════════════════════════
    city_html = ""
    city_threads = city_data.get("threads", [])
    city_meta = city_data.get("meta", {})
    city_analysis = city_data.get("llm_analysis", "")

    if city_threads or city_analysis:
        # City analysis briefing
        city_briefing = ""
        if city_analysis:
            import re as _re
            formatted = e(city_analysis)
            formatted = _re.sub(r'^## (.+)$', r'<h3 style="color:var(--amber);margin:16px 0 8px">\1</h3>', formatted, flags=_re.MULTILINE)
            formatted = _re.sub(r'^- (.+)$', r'<div style="padding-left:16px;margin:2px 0">• \1</div>', formatted, flags=_re.MULTILINE)
            formatted = _re.sub(r'\*\*([^*]+)\*\*', r'<strong style="color:var(--cyan)">\1</strong>', formatted)
            formatted = formatted.replace("\n\n", "<br><br>").replace("\n", "<br>")
            city_briefing = f"""<div style="background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:14px;margin-bottom:16px;font-size:0.88rem;line-height:1.5">{formatted}</div>"""
        elif city_notes:
            # Use latest city-watch think note as backup
            latest_cn = city_notes[-1]
            formatted = e(latest_cn.get("content", "")[:1500])
            formatted = formatted.replace("\n\n", "<br><br>").replace("\n", "<br>")
            city_briefing = f"""<div style="background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:14px;margin-bottom:16px;font-size:0.88rem;line-height:1.5">{formatted}</div>"""

        # Thread listing
        thread_rows = ""
        for t in city_threads[:20]:
            title = e(t.get("title", "?"))[:65]
            url = t.get("url", "")
            score = t.get("total_score", 0)
            kw = e(", ".join(t.get("matched_keywords", [])[:4]))
            posts = t.get("recent_posts", 0)
            sc = "var(--green)" if score >= 5 else ("var(--amber)" if score >= 2 else "var(--fg-dim)")
            watch = " 👁️" if t.get("always_watch") else ""
            title_link = f'<a href="{e(url)}" target="_blank" style="color:var(--cyan);text-decoration:none">{title}</a>' if url else title

            thread_rows += f"""<tr>
              <td style="color:{sc};font-weight:bold;text-align:center">{score:.0f}</td>
              <td>{title_link}{watch}</td>
              <td style="color:var(--purple);font-size:0.8rem">{kw}</td>
              <td style="color:var(--fg-dim);text-align:center">{posts}</td>
            </tr>"""

        city_ts = format_dual_timestamps(city_meta)
        city_relevant = city_meta.get("relevant", 0)
        city_total = city_meta.get("total_threads_scanned", 0)

        city_html = f"""
<div class="section">
  <div class="section-title">🏗️ NEIGHBORHOOD WATCH — Do Folwarku, Widzew <span style="color:var(--fg-dim);font-size:0.8rem">// {city_ts} · {city_relevant}/{city_total} threads</span></div>
  <div class="section-body">
    {city_briefing}
    <table class="host-table">
      <thead><tr><th>Score</th><th>Thread</th><th>Keywords</th><th>Posts</th></tr></thead>
      <tbody>{thread_rows}</tbody>
    </table>
  </div>
</div>"""

    # ════════════════════════════════════════════════════════════════════════
    # Assemble page
    # ════════════════════════════════════════════════════════════════════════
    body = climate_section
    if usage_section:
        body += usage_section
    if garage_html:
        body += garage_html
    if car_html:
        body += car_html
    if analysis_html:
        body += analysis_html
    if journal_html:
        body += journal_html
    if city_html:
        body += city_html

    # Empty state
    if not sensor_stats and not city_threads:
        body = """
<div class="section">
  <div class="section-title">🏠 HOME</div>
  <div class="section-body" style="text-align:center;padding:40px;color:var(--fg-dim)">
    <div style="font-size:3rem;margin-bottom:16px">🏠</div>
    <div style="font-size:1.1rem;color:var(--amber)">No home data yet</div>
    <div style="margin-top:12px;font-size:0.9rem">
      ha-correlate.py runs 4× daily analyzing Home Assistant sensors.<br>
      ha-journal.py creates journal entries with climate, rooms, and recommendations.<br>
      city-watch.py monitors SkyscraperCity for neighborhood updates.
    </div>
  </div>
</div>"""

    return page_wrap("HOME", body, "home")


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

    # Network diff (last 2 scans) — split into truly-new vs known churn
    diff_html = ""
    if len(dates) >= 2:
        prev = load_json(f"{DATA_DIR}/scan-{dates[-2]}.json")
        if prev:
            prev_ips = set(prev["hosts"].keys())
            curr_ips = set(hosts.keys())
            new_ips = curr_ips - prev_ips
            gone_ips = prev_ips - curr_ips

            # Load persistent device inventory to distinguish new vs known
            hosts_db = {}
            db_path = f"{DATA_DIR}/hosts-db.json"
            if os.path.exists(db_path):
                hosts_db = load_json(db_path) or {}
            known_macs = set(hosts_db.keys())

            truly_new_lines = []
            truly_gone_lines = []
            known_appeared = []
            known_disappeared = []

            for ip in sorted(new_ips):
                h = hosts[ip]
                mac = h.get("mac", "")
                key = mac if mac else f"nomac-{ip}"
                name = best_name(h)
                name_str = f" — {e(name)}" if name else ""
                entry = f'{ip_link(ip)}{name_str} {badge(h.get("device_type",""))}'
                if key in known_macs:
                    known_appeared.append(f'<div class="diff-new" style="opacity:0.6">{entry}</div>')
                else:
                    truly_new_lines.append(f'<div class="diff-new">{entry}</div>')

            for ip in sorted(gone_ips):
                h = prev["hosts"].get(ip, {})
                mac = h.get("mac", "")
                key = mac if mac else f"nomac-{ip}"
                name = best_name(h)
                name_str = f" — {e(name)}" if name else ""
                entry = f'{e(ip)}{name_str}'
                if key in known_macs:
                    known_disappeared.append(f'<div class="diff-gone" style="opacity:0.6">{entry}</div>')
                else:
                    truly_gone_lines.append(f'<div class="diff-gone">{entry}</div>')

            diff_lines = truly_new_lines + truly_gone_lines
            if not diff_lines:
                diff_lines.append('<div style="color:var(--fg-dim)">No new or unknown device changes</div>')

            # Known device churn in a collapsible section
            known_section = ""
            if known_appeared or known_disappeared:
                known_items = "".join(known_appeared + known_disappeared)
                known_section = f"""
<details style="margin-top:8px">
  <summary style="color:var(--fg-dim);cursor:pointer;font-size:0.85em">{len(known_appeared)} known appeared, {len(known_disappeared)} known disappeared</summary>
  <div style="margin-top:4px">{known_items}</div>
</details>"""

            diff_html = f"""
<div class="section">
  <div class="section-title">NETWORK CHANGES (vs {e(dates[-2])})</div>
  <div class="section-body">{"".join(diff_lines)}{known_section}</div>
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

    # ── Executive Summary (LLM-generated daily briefing) ──
    summary_html = ""
    summary_data = load_json(f"{DATA_DIR}/summary/latest-summary.json")
    if summary_data and summary_data.get("summary"):
        sum_text = summary_data["summary"]
        sum_date = e(summary_data.get("date", ""))
        sum_gen = summary_data.get("generated", "")[:16]
        src_count = summary_data.get("source_count", 0)
        # Convert newlines to HTML, preserve emoji headers
        sum_lines = []
        for line in sum_text.split("\n"):
            stripped = line.strip()
            if not stripped:
                sum_lines.append("<br>")
            elif stripped.startswith(("🔑", "📊", "🔬", "🏠", "📡", "⚡")):
                sum_lines.append(f'<div style="color:var(--cyan);font-weight:bold;margin-top:10px">{e(stripped)}</div>')
            elif stripped.lower().startswith("bottom line:"):
                sum_lines.append(f'<div style="color:var(--amber);font-weight:bold;margin-top:10px;border-top:1px solid var(--border);padding-top:8px">{e(stripped)}</div>')
            elif stripped.startswith(("- ", "• ", "* ")):
                sum_lines.append(f'<div style="margin:2px 0 2px 16px;color:var(--fg)">{e(stripped)}</div>')
            else:
                sum_lines.append(f'<div style="color:var(--fg)">{e(stripped)}</div>')
        summary_html = f"""
<div class="section" style="border-color:var(--cyan)">
  <div class="section-title">📋 DAILY BRIEFING — {sum_date} <span style="color:var(--fg-dim);font-size:0.75rem">generated {e(sum_gen)} from {src_count} sources</span></div>
  <div class="section-body" style="font-size:0.88rem;line-height:1.5">
    {"".join(sum_lines)}
  </div>
</div>"""

    body = summary_html + stats + types_section + presence_html + diff_html + port_change_html + security_html + vuln_summary_html + wd_html + health_html + top_html
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


# ─── Page: Leaks / CTI (leaks.html) ───

def gen_leaks():
    """Generate the Leak Monitor / Cyber Threat Intelligence page."""
    leaks_file = os.path.join(DATA_DIR, "leaks", "leak-intel.json")
    if not os.path.exists(leaks_file):
        body = '<div class="section"><div class="section-title">🔒 LEAK MONITOR</div><div class="section-body"><p style="color:var(--fg-dim)">No leak intelligence data yet. Run <code>leak-monitor.py scan</code> to collect.</p></div></div>'
        return page_wrap("LEAKS", body, "leaks")
    try:
        db = json.loads(open(leaks_file).read())
    except Exception:
        body = '<div class="section"><div class="section-title">🔒 LEAK MONITOR</div><div class="section-body"><p style="color:var(--red)">Error reading leak DB.</p></div></div>'
        return page_wrap("LEAKS", body, "leaks")

    findings = db.get("findings", [])
    runs = db.get("runs", [])
    stats = db.get("stats", {})
    last_analysis = stats.get("last_analysis", "")
    last_run = stats.get("last_run", "never")

    # ── Severity counts ──
    sev = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for f in findings:
        s = f.get("severity", "info")
        sev[s] = sev.get(s, 0) + 1

    # ── Source counts ──
    src_counts = {}
    for f in findings:
        s = f.get("source", "?")
        src_counts[s] = src_counts.get(s, 0) + 1

    # ── Category counts ──
    cat_counts = {}
    for f in findings:
        c = f.get("category", "?")
        cat_counts[c] = cat_counts.get(c, 0) + 1

    # ── Recent findings (last 7 days) ──
    cutoff_7d = (datetime.now() - timedelta(days=7)).isoformat()
    recent = [f for f in findings if f.get("first_seen", "") > cutoff_7d]
    recent.sort(key=lambda x: x.get("first_seen", ""), reverse=True)

    # ── 24h findings ──
    cutoff_24h = (datetime.now() - timedelta(hours=24)).isoformat()
    last_24h = [f for f in findings if f.get("first_seen", "") > cutoff_24h]

    # ── Stat boxes ──
    crit_class = "red" if sev["critical"] > 0 else "green"
    high_class = "amber" if sev["high"] > 0 else "green"
    stats_html = f"""
    <div class="section">
      <div class="section-title">▸ 🔒 LEAK MONITOR — Cyber Threat Intelligence</div>
      <div class="section-body">
        <div class="stats-grid">
          <div class="stat-box"><div class="stat-val">{len(findings)}</div>
            <div class="stat-label">total findings</div></div>
          <div class="stat-box"><div class="stat-val">{len(last_24h)}</div>
            <div class="stat-label">last 24h</div></div>
          <div class="stat-box"><div class="stat-val {crit_class}">{sev['critical']}</div>
            <div class="stat-label">critical</div></div>
          <div class="stat-box"><div class="stat-val {high_class}">{sev['high']}</div>
            <div class="stat-label">high</div></div>
          <div class="stat-box"><div class="stat-val">{sev['medium']}</div>
            <div class="stat-label">medium</div></div>
          <div class="stat-box"><div class="stat-val">{len(runs)}</div>
            <div class="stat-label">scans run</div></div>
          <div class="stat-box"><div class="stat-val">{len(src_counts)}</div>
            <div class="stat-label">active sources</div></div>
          <div class="stat-box"><div class="stat-val" style="font-size:0.7em">{last_run[:16] if last_run != 'never' else 'never'}</div>
            <div class="stat-label">last scan</div></div>
        </div>
      </div>
    </div>"""

    # ── Source breakdown ──
    src_rows = ""
    for src, cnt in sorted(src_counts.items(), key=lambda x: -x[1]):
        src_sev = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
        for f in findings:
            if f.get("source") == src:
                src_sev[f.get("severity", "info")] += 1
        src_rows += f"""<tr>
          <td style="color:var(--cyan)">{src}</td>
          <td>{cnt}</td>
          <td style="color:var(--red)">{src_sev['critical']}</td>
          <td style="color:var(--amber)">{src_sev['high']}</td>
          <td>{src_sev['medium']}</td>
          <td style="color:var(--fg-dim)">{src_sev['low'] + src_sev['info']}</td>
        </tr>"""

    source_html = f"""
    <div class="section">
      <div class="section-title">▸ 📡 INTELLIGENCE SOURCES</div>
      <div class="section-body">
        <table class="host-table">
          <thead><tr><th>Source</th><th>Findings</th><th>Critical</th><th>High</th><th>Medium</th><th>Low/Info</th></tr></thead>
          <tbody>{src_rows}</tbody>
        </table>
      </div>
    </div>"""

    # ── Category breakdown as badges ──
    cat_badges = ""
    cat_icons = {"ransomware_victim": "🔒", "exploited_vuln": "🛡", "exposed_secret": "🔑",
                 "channel_mention": "📱", "c2_infrastructure": "🦠", "c2_stats": "📊",
                 "osint_alert": "🐦", "darkweb_mention": "🧅", "breach_intel": "🕵",
                 "pl_database": "🇵🇱", "source_code_leak": "💻", "target_breach": "🎯",
                 "major_breach": "📢", "infostealer_exposure": "🔓"}
    for cat, cnt in sorted(cat_counts.items(), key=lambda x: -x[1]):
        icon = cat_icons.get(cat, "📌")
        cat_badges += f'<span class="badge" style="margin:2px 4px;padding:4px 10px">{icon} {cat.replace("_"," ")}: {cnt}</span>'

    cat_html = f"""
    <div class="section">
      <div class="section-title">▸ 📂 FINDING CATEGORIES</div>
      <div class="section-body"><div style="padding:6px 0">{cat_badges}</div></div>
    </div>"""

    # ── LLM Analysis ──
    analysis_html = ""
    if last_analysis:
        analysis_date = stats.get("last_analysis_date", "")[:16]
        analysis_html = f"""
    <div class="section">
      <div class="section-title">▸ 🤖 LLM THREAT ANALYSIS ({analysis_date})</div>
      <div class="section-body">
        <div class="log-view" style="max-height:400px;overflow-y:auto;white-space:pre-wrap;font-size:0.82em;line-height:1.5">{e(last_analysis)}</div>
      </div>
    </div>"""

    # ── Critical & High findings table ──
    crit_high = [f for f in findings if f.get("severity") in ("critical", "high")]
    crit_high.sort(key=lambda x: x.get("first_seen", ""), reverse=True)
    ch_rows = ""
    for f in crit_high[:50]:
        ts = f.get("first_seen", "")[:16]
        sev_color = "var(--red)" if f["severity"] == "critical" else "var(--amber)"
        sev_label = f["severity"].upper()[:4]
        url = f.get("url", "")
        title_safe = e(f.get("title", "?")[:120])
        if url:
            title_safe = f'<a href="{e(url)}" target="_blank" rel="noopener" style="color:var(--cyan)">{title_safe}</a>'
        ch_rows += f"""<tr>
          <td style="color:var(--fg-dim);white-space:nowrap">{ts}</td>
          <td style="color:{sev_color};font-weight:bold">{sev_label}</td>
          <td>{e(f.get('source','?'))}</td>
          <td>{title_safe}</td>
        </tr>"""

    crit_html = ""
    if ch_rows:
        crit_html = f"""
    <div class="section">
      <div class="section-title">▸ 🚨 CRITICAL &amp; HIGH FINDINGS</div>
      <div class="section-body">
        <table class="host-table">
          <thead><tr><th>Time</th><th>Sev</th><th>Source</th><th>Finding</th></tr></thead>
          <tbody>{ch_rows}</tbody>
        </table>
      </div>
    </div>"""

    # ── Recent findings (all severities, last 7 days) ──
    recent_rows = ""
    for f in recent[:100]:
        ts = f.get("first_seen", "")[:16]
        sev_map = {"critical": ("var(--red)", "CRIT"), "high": ("var(--amber)", "HIGH"),
                   "medium": ("var(--fg)", "MED"), "low": ("var(--fg-dim)", "LOW"),
                   "info": ("var(--fg-dim)", "INFO")}
        sev_color, sev_label = sev_map.get(f.get("severity", "info"), ("var(--fg-dim)", "?"))
        title_safe = e(f.get("title", "?")[:140])
        url = f.get("url", "")
        if url:
            title_safe = f'<a href="{e(url)}" target="_blank" rel="noopener" style="color:var(--cyan)">{title_safe}</a>'
        summary = e(f.get("summary", "")[:200])
        recent_rows += f"""<tr>
          <td style="color:var(--fg-dim);white-space:nowrap">{ts}</td>
          <td style="color:{sev_color}">{sev_label}</td>
          <td>{f.get('source','?')}</td>
          <td>{title_safe}<br><small style="color:var(--fg-dim)">{summary}</small></td>
        </tr>"""

    recent_html = f"""
    <div class="section">
      <div class="section-title">▸ 📋 ALL FINDINGS — LAST 7 DAYS ({len(recent)} total)</div>
      <div class="section-body">
        <table class="host-table">
          <thead><tr><th>Time</th><th>Sev</th><th>Source</th><th>Finding</th></tr></thead>
          <tbody>{recent_rows if recent_rows else '<tr><td colspan="4" style="color:var(--fg-dim)">No findings in the last 7 days</td></tr>'}</tbody>
        </table>
      </div>
    </div>"""

    # ── Scan history ──
    run_rows = ""
    for r in reversed(runs[-14:]):
        ts = r.get("timestamp", "")[:16]
        new_c = r.get("new_findings", 0)
        total_c = r.get("total_findings", 0)
        new_style = "color:var(--green)" if new_c > 0 else "color:var(--fg-dim)"
        run_rows += f"""<tr>
          <td style="color:var(--fg-dim)">{ts}</td>
          <td style="{new_style}">{new_c}</td>
          <td>{total_c}</td>
          <td>{r.get('sources_ok', '?')}</td>
        </tr>"""

    run_html = f"""
    <div class="section">
      <div class="section-title">▸ 📆 SCAN HISTORY (last 14 runs)</div>
      <div class="section-body">
        <table class="host-table">
          <thead><tr><th>Timestamp</th><th>New</th><th>Total</th><th>Sources</th></tr></thead>
          <tbody>{run_rows if run_rows else '<tr><td colspan="4" style="color:var(--fg-dim)">No scan runs recorded</td></tr>'}</tbody>
        </table>
      </div>
    </div>"""

    body = stats_html + source_html + cat_html + analysis_html + crit_html + recent_html + run_html
    return page_wrap("LEAKS", body, "leaks")


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
                        exp_date = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
                        if exp_date < datetime.now():
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
    """Generate the repository issues + CSI sensor monitoring page."""
    repo_section = ""
    csi_section = ""

    # ── Repo feeds section ──
    if REPO_FEEDS:
        all_cards = ""
        total_interesting = 0
        total_repos_checked = 0

        for rid, rcfg in REPO_FEEDS.items():
            repo_dir = os.path.join(DATA_DIR, rcfg.get("data_dir", f"repos/{rid}"))
            latest_path = os.path.join(repo_dir, "latest.json")
            repo_name = rcfg.get("name", rid)
            repo_emoji = rcfg.get("emoji", "\U0001f4e6")
            web_url = rcfg.get("web_url", "")

            data = load_json(latest_path) if os.path.exists(latest_path) else None

            if not data:
                all_cards += f"""
<div class="section">
  <div class="section-title">{repo_emoji} {e(repo_name.upper())}</div>
  <div class="section-body" style="color:var(--fg-dim);padding:16px">
    \u23f3 Not checked yet \u2014 waiting for first repo-watch run
  </div>
</div>"""
                continue

            total_repos_checked += 1
            interesting = data.get("interesting", [])
            total_interesting += len(interesting)
            checked = data.get("checked", "?")
            total_items = data.get("total_items", 0)
            other_count = data.get("other_count", 0)

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

                type_icon = "\U0001f500" if "merge" in itype or "pull" in itype else ("\U0001f4e9" if "patch" in itype else ("\U0001f4e6" if "series" in itype else "\U0001f41b"))
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
    <span style="color:var(--fg-dim);font-size:0.8rem;white-space:nowrap">\U0001f4ac{comments} \U0001f44d{reactions}</span>
  </div>
  <div style="margin-top:4px;font-size:0.8rem;color:var(--fg-dim)">\U0001f464 {author}</div>
  <div style="margin-top:4px">{kw_chips} {label_chips}</div>
  {"<div style='margin-top:6px;font-size:0.82rem;color:var(--fg-dim);border-left:2px solid var(--border);padding-left:8px'>" + body_preview + "</div>" if body_preview else ""}
</div>"""

            web_link = f' &nbsp;\u2502&nbsp; <a href="{e(web_url)}">{e(web_url.replace("https://", ""))}</a>' if web_url else ""

            all_cards += f"""
<div class="section">
  <div class="section-title">{repo_emoji} {e(repo_name.upper())} \u2014 {len(interesting)} interesting of {total_items} checked</div>
  <div class="section-body">
    <div style="margin-bottom:10px;font-size:0.82rem;color:var(--fg-dim)">
      Last check: {e(checked)} &nbsp;\u2502&nbsp; Other: {other_count} low-relevance items{web_link}
    </div>
    {item_rows if item_rows else '<div style="color:var(--fg-dim);padding:12px">No interesting items found</div>'}
  </div>
</div>"""

        repo_section = f"""
<div class="section">
  <div class="section-title">\U0001f41b REPOSITORY ISSUE TRACKER</div>
  <div class="section-body">
    <div style="display:flex;gap:24px;flex-wrap:wrap;font-size:0.9rem">
      <div><span style="color:var(--green);font-size:1.5rem;font-weight:bold">{total_interesting}</span><br><span style="color:var(--fg-dim)">interesting items</span></div>
      <div><span style="color:var(--cyan);font-size:1.5rem;font-weight:bold">{len(REPO_FEEDS)}</span><br><span style="color:var(--fg-dim)">repos monitored</span></div>
      <div><span style="color:var(--amber);font-size:1.5rem;font-weight:bold">{total_repos_checked}</span><br><span style="color:var(--fg-dim)">checked today</span></div>
    </div>
    <div style="margin-top:12px;font-size:0.82rem;color:var(--fg-dim)">
      Monitoring: {', '.join(rcfg.get('name', rid) for rid, rcfg in REPO_FEEDS.items())} &nbsp;\u2502&nbsp;
      Scored by keyword relevance + user interest profile
    </div>
  </div>
</div>""" + all_cards

    # ── CSI Camera Sensor section ──
    csi_path = os.path.join(DATA_DIR, "csi-sensors", "latest-csi.json")
    csi_data = load_json(csi_path) if os.path.exists(csi_path) else None

    if csi_data and csi_data.get("sensors"):
        stats = csi_data.get("stats", {})
        checked = csi_data.get("checked", "?")
        sensors = csi_data.get("sensors", {})
        candidates = csi_data.get("candidates", [])
        llm = csi_data.get("llm_analysis", "")

        # Stats header
        csi_section += f"""
<div class="section">
  <div class="section-title">\U0001f4f7 CSI CAMERA SENSOR TRACKER</div>
  <div class="section-body">
    <div style="display:flex;gap:24px;flex-wrap:wrap;font-size:0.9rem">
      <div><span style="color:var(--cyan);font-size:1.5rem;font-weight:bold">{stats.get('total_sensors', 0)}</span><br><span style="color:var(--fg-dim)">sensors tracked</span></div>
      <div><span style="color:var(--green);font-size:1.5rem;font-weight:bold">{stats.get('with_mainline_driver', 0)}</span><br><span style="color:var(--fg-dim)">mainline drivers</span></div>
      <div><span style="color:var(--red);font-size:1.5rem;font-weight:bold">{stats.get('needs_driver', 0)}</span><br><span style="color:var(--fg-dim)">need driver</span></div>
      <div><span style="color:var(--amber);font-size:1.5rem;font-weight:bold">{stats.get('active_issues', 0)}</span><br><span style="color:var(--fg-dim)">active issues</span></div>
      <div><span style="color:var(--magenta);font-size:1.5rem;font-weight:bold">{stats.get('new_candidates', 0)}</span><br><span style="color:var(--fg-dim)">newly discovered</span></div>
    </div>
    <div style="margin-top:12px;font-size:0.82rem;color:var(--fg-dim)">
      Last scan: {e(str(checked))} &nbsp;\u2502&nbsp;
      Platforms: Jetson Orin Nano, RPi 2, RPi 4, RPi 5 &nbsp;\u2502&nbsp;
      Self-expanding watchlist with auto-discovery
    </div>
  </div>
</div>"""

        # Driver Status Grid — grouped by status
        needs_driver = []
        in_progress = []
        has_driver = []
        for sid, s in sorted(sensors.items(), key=lambda x: -x[1].get("interest", 0)):
            ds = s.get("driver_status", "unknown")
            if ds == "mainline":
                has_driver.append((sid, s))
            elif ds == "in-progress":
                in_progress.append((sid, s))
            else:
                needs_driver.append((sid, s))

        def _sensor_row(sid, s, highlight=False):
            model = e(s.get("model", sid.upper()))
            vendor = e(s.get("vendor", "?"))
            res = e(s.get("resolution", "?"))
            intf = e(s.get("interface", "CSI-2"))
            ds = s.get("driver_status", "unknown")
            ds_icon = "\u2705" if ds == "mainline" else ("\U0001f527" if ds == "in-progress" else "\u274c")
            ds_color = "var(--green)" if ds == "mainline" else ("var(--amber)" if ds == "in-progress" else "var(--red)")
            plats = s.get("platforms", [])
            plat_chips = " ".join(f'<span class="port-chip">{p}</span>' for p in plats)
            datasheet = s.get("datasheet", "?")
            ds_badge_color = "var(--green)" if datasheet == "available" else ("var(--amber)" if datasheet == "restricted" else "var(--fg-dim)")
            purchase = s.get("purchase", "?")
            buy_color = "var(--green)" if purchase == "easy" else ("var(--amber)" if purchase == "moderate" else "var(--red)")
            n_issues = s.get("issue_count", 0)
            note = e(s.get("notes", ""))
            interest = s.get("interest", 3)
            interest_bar = "\u2588" * interest + "\u2591" * (5 - interest)
            driver_path = s.get("driver_path", "")
            drv_info = f' <span style="color:var(--fg-dim);font-size:0.75rem">{e(driver_path)}</span>' if driver_path else ""
            bg = "var(--bg3)" if highlight else "var(--bg2)"

            row = f"""<div style="border:1px solid var(--border);background:{bg};padding:8px;margin-bottom:4px">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:4px">
    <div>
      <span style="color:var(--cyan);font-weight:bold">{model}</span>
      <span style="color:var(--fg-dim);font-size:0.82rem">({vendor}, {res}, {intf})</span>
      <span style="color:{ds_color}">{ds_icon} {ds}</span>{drv_info}
    </div>
    <div style="font-size:0.8rem">
      <span style="color:{ds_badge_color}" title="Datasheet">\U0001f4c4{datasheet}</span>
      &nbsp;<span style="color:{buy_color}" title="Purchase">\U0001f6d2{purchase}</span>
      &nbsp;<span style="color:var(--fg-dim)" title="Interest">{interest_bar}</span>"""
            if n_issues:
                row += f'\n      &nbsp;<span style="color:var(--amber)" title="Issues">\u26a0\ufe0f{n_issues}</span>'
            row += f"""
    </div>
  </div>
  <div style="margin-top:2px;font-size:0.8rem">{plat_chips}</div>"""
            if note:
                row += f'\n  <div style="margin-top:2px;font-size:0.78rem;color:var(--fg-dim)">{note}</div>'
            row += "\n</div>"
            return row

        if needs_driver:
            grid = "".join(_sensor_row(sid, s, True) for sid, s in needs_driver)
            csi_section += f"""
<div class="section">
  <div class="section-title">\u274c SENSORS NEEDING DRIVER ({len(needs_driver)})</div>
  <div class="section-body">{grid}</div>
</div>"""

        if in_progress:
            grid = "".join(_sensor_row(sid, s, True) for sid, s in in_progress)
            csi_section += f"""
<div class="section">
  <div class="section-title">\U0001f527 DRIVER IN PROGRESS ({len(in_progress)})</div>
  <div class="section-body">{grid}</div>
</div>"""

        # Active issues for sensors
        all_issues = []
        for sid, s in sensors.items():
            for iss in s.get("issues", []):
                iss["_sensor"] = s.get("model", sid.upper())
                all_issues.append(iss)
        all_issues.sort(key=lambda x: x.get("date", ""), reverse=True)

        if all_issues:
            issue_rows = ""
            for iss in all_issues[:25]:
                sensor = e(iss.get("_sensor", "?"))
                title = e(iss.get("title", "?"))[:120]
                date = e(iss.get("date", "?"))
                src = e(iss.get("source", "?"))
                url = iss.get("url", "")
                author = e(iss.get("author", iss.get("submitter", "?")))
                state = e(iss.get("state", "?"))
                state_color = "var(--green)" if state in ("accepted", "merged") else ("var(--amber)" if state in ("new", "open", "opened") else "var(--fg-dim)")
                link = f'<a href="{e(url)}" style="color:var(--cyan)">{title}</a>' if url else title
                issue_rows += f"""<div style="border:1px solid var(--border);background:var(--bg2);padding:8px;margin-bottom:4px">
  <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:4px">
    <div><span style="color:var(--magenta);font-weight:bold">[{sensor}]</span> {link}</div>
    <span style="color:var(--fg-dim);font-size:0.8rem;white-space:nowrap">{date}</span>
  </div>
  <div style="font-size:0.78rem;color:var(--fg-dim);margin-top:2px">
    <span style="color:{state_color}">{state}</span> &nbsp;\u2502&nbsp; {src} &nbsp;\u2502&nbsp; {author}
  </div>
</div>"""

            csi_section += f"""
<div class="section">
  <div class="section-title">\U0001f4e1 RECENT SENSOR PATCHES & ISSUES ({len(all_issues)})</div>
  <div class="section-body">{issue_rows}</div>
</div>"""

        # LLM analysis
        if llm:
            llm_html = e(llm).replace("\n", "<br>")
            csi_section += f"""
<div class="section">
  <div class="section-title">\U0001f9e0 AI SENSOR ANALYSIS</div>
  <div class="section-body">
    <div style="font-family:monospace;white-space:pre-wrap;line-height:1.6;font-size:0.88rem;color:var(--fg)">{llm_html}</div>
  </div>
</div>"""

        # Sensors with mainline drivers (collapsed)
        if has_driver:
            grid = "".join(_sensor_row(sid, s) for sid, s in has_driver)
            csi_section += f"""
<div class="section">
  <div class="section-title">\u2705 MAINLINE DRIVER ({len(has_driver)})</div>
  <div class="section-body">{grid}</div>
</div>"""

        # New candidates discovered
        if candidates:
            cand_items = "".join(
                f'<span class="port-chip" style="margin:2px">{e(c.get("model", "?"))} <span style="color:var(--fg-dim);font-size:0.7rem">({e(c.get("discovered", "?"))})</span></span>'
                for c in candidates
            )
            total_cand = csi_data.get("all_candidates_count", len(candidates))
            csi_section += f"""
<div class="section">
  <div class="section-title">\U0001f50d AUTO-DISCOVERED CANDIDATES</div>
  <div class="section-body">
    <div style="margin-bottom:8px;font-size:0.82rem;color:var(--fg-dim)">
      Sensor models found by crawling that aren't in watchlist yet ({total_cand} total)
    </div>
    <div>{cand_items}</div>
  </div>
</div>"""

    body = repo_section + csi_section
    if not body.strip():
        body = """
<div class="section">
  <div class="section-title">\U0001f41b ISSUES & SENSOR TRACKER</div>
  <div class="section-body" style="text-align:center;padding:40px;color:var(--fg-dim)">
    No data yet \u2014 waiting for first repo-watch and csi-sensor-watch runs
  </div>
</div>"""
    return page_wrap("ISSUES", body, "issues")


# ─── Page: Academic Literature (academic.html) ───

def gen_academic():
    """Generate the academic literature page — publications, dissertations, patents.
    Only shows results the LLM scored as interesting (relevance >= 8)."""
    academic_dir = os.path.join(DATA_DIR, "academic")

    TOPICS = [
        ("kernel-drivers", "Linux Kernel Drivers", "🐧"),
        ("camera-drivers", "Camera Driver Architecture", "📷"),
        ("embedded-inference", "Hardware for Embedded Inference", "🧠"),
        ("adas-cameras", "In-Cabin ADAS Cameras", "🚗"),
    ]
    CONTENT_TYPES = [
        ("publication", "📄", "var(--cyan)"),
        ("dissertation", "🎓", "var(--magenta)"),
        ("patent", "📜", "var(--amber)"),
    ]
    MIN_SCORE = 8  # Only show results scored >= this

    # Collect all data
    all_data = {}  # {(topic_id, content_type): {meta, results}}
    total_interesting = 0
    total_scanned = 0
    topics_with_data = 0

    for topic_id, topic_label, topic_icon in TOPICS:
        for ctype, cicon, ccolor in CONTENT_TYPES:
            latest = os.path.join(academic_dir, f"latest-{topic_id}-{ctype}.json")
            if not os.path.exists(latest):
                continue
            data = load_json(latest)
            if not data:
                continue
            results = data.get("top_results", [])
            interesting = [r for r in results if r.get("score", 0) >= MIN_SCORE]
            meta = data.get("meta", {})
            total_scanned += meta.get("total_found", len(results))
            total_interesting += len(interesting)
            if interesting:
                topics_with_data += 1
            all_data[(topic_id, ctype)] = {
                "meta": meta,
                "results": interesting,
                "all_count": len(results),
            }

    # Empty state
    if not all_data:
        body = """
<div class="section">
  <div class="section-title">🎓 ACADEMIC LITERATURE</div>
  <div class="section-body" style="text-align:center;padding:40px;color:var(--fg-dim)">
    <div style="font-size:3rem;margin-bottom:16px">🎓</div>
    <div style="font-size:1.1rem;color:var(--amber)">No academic data yet</div>
    <div style="margin-top:12px;color:var(--fg-dim);font-size:0.9rem">
      ClawdBot monitors scientific publications, MSc/PhD dissertations, and patents<br>
      across 4 topic areas. Results will appear here after the first academic-watch runs.<br>
      Only items scored as interesting (relevance ≥ {min_score}) are shown.
    </div>
  </div>
</div>""".replace("{min_score}", str(MIN_SCORE))
        return page_wrap("ACADEMIC", body, "academic")

    # Summary header
    last_updated = ""
    last_meta = {}
    for key, val in all_data.items():
        ts = val["meta"].get("timestamp", "")
        if ts > last_updated:
            last_updated = ts
            last_meta = val["meta"]
    last_dt = format_dual_timestamps(last_meta) if last_meta else "?"

    summary = f"""
<div class="section">
  <div class="section-title">🎓 ACADEMIC LITERATURE — curated by ClawdBot</div>
  <div class="section-body">
    <div style="display:flex;gap:32px;flex-wrap:wrap;font-size:0.9rem">
      <div><span style="color:var(--green);font-size:1.5rem;font-weight:bold">{total_interesting}</span><br><span style="color:var(--fg-dim)">interesting finds</span></div>
      <div><span style="color:var(--fg-dim);font-size:1.5rem;font-weight:bold">{total_scanned}</span><br><span style="color:var(--fg-dim)">total scanned</span></div>
      <div><span style="color:var(--cyan);font-size:1.5rem;font-weight:bold">{topics_with_data}</span><br><span style="color:var(--fg-dim)">active feeds</span></div>
    </div>
    <div style="margin-top:8px;font-size:0.82rem;color:var(--fg-dim)">
      Showing results with relevance score ≥ {MIN_SCORE} &nbsp;│&nbsp; Last scan: {last_dt}
      &nbsp;│&nbsp; Sources: arXiv, Semantic Scholar, Google Scholar, Google Patents
    </div>
  </div>
</div>"""

    # Build sections per topic
    topic_sections = ""
    for topic_id, topic_label, topic_icon in TOPICS:
        # Collect all content types for this topic
        topic_results = []
        topic_meta_parts = []
        for ctype, cicon, ccolor in CONTENT_TYPES:
            key = (topic_id, ctype)
            if key not in all_data:
                continue
            entry = all_data[key]
            for r in entry["results"]:
                r["_ctype"] = ctype
                r["_cicon"] = cicon
                r["_ccolor"] = ccolor
            topic_results.extend(entry["results"])
            meta = entry["meta"]
            topic_meta_parts.append(f'{cicon} {ctype}: {len(entry["results"])}/{entry["all_count"]}')

        if not topic_results:
            continue

        # Sort by score descending
        topic_results.sort(key=lambda x: x.get("score", 0), reverse=True)

        meta_str = " &nbsp;│&nbsp; ".join(topic_meta_parts)

        cards = ""
        for r in topic_results:
            title = e(r.get("title", "Untitled"))
            abstract = e(r.get("abstract", "")[:300])
            url = r.get("url", "")
            source = r.get("source", "?")
            score = r.get("score", 0)
            year = r.get("year") or ""
            citations = r.get("citations", "")
            authors = r.get("authors", [])
            cicon = r.get("_cicon", "📝")
            ccolor = r.get("_ccolor", "var(--fg)")
            ctype = r.get("_ctype", "?")
            patent_id = r.get("patent_id", "")
            assignee = r.get("assignee", "")
            doi = r.get("doi", "")

            # Author line
            author_str = ""
            if authors:
                author_str = e(", ".join(authors[:4]))
                if len(authors) > 4:
                    author_str += f" +{len(authors)-4} more"

            # Meta line pieces
            meta_pieces = []
            if year:
                meta_pieces.append(f"<span style='color:var(--green)'>{e(str(year))}</span>")
            if citations:
                meta_pieces.append(f"<span style='color:var(--amber)'>{citations} cit.</span>")
            if assignee:
                meta_pieces.append(f"<span style='color:var(--cyan)'>{e(assignee)}</span>")
            if patent_id:
                meta_pieces.append(f"<span style='color:var(--fg-dim)'>{e(patent_id)}</span>")
            meta_pieces.append(f"<span style='color:var(--fg-dim)'>via {e(source)}</span>")
            meta_pieces.append(f"<span style='color:{'var(--green)' if score >= 12 else 'var(--amber)' if score >= 8 else 'var(--fg-dim)'}'>score:{score}</span>")

            meta_line = " &nbsp;│&nbsp; ".join(meta_pieces)

            # URL link
            link_html = ""
            if url:
                link_html = f' <a href="{e(url)}" target="_blank" rel="noopener" style="color:var(--cyan);text-decoration:none;font-size:0.8rem">↗ open</a>'

            cards += f"""
<div style="margin:10px 0;padding:10px 14px;border-left:3px solid {ccolor};background:rgba(255,255,255,0.02)">
  <div style="font-size:0.9rem;font-weight:bold;color:var(--fg)">
    {cicon} {title}{link_html}
  </div>
  <div style="font-size:0.8rem;color:var(--fg-dim);margin:3px 0">{meta_line}</div>"""

            if author_str:
                cards += f'  <div style="font-size:0.8rem;color:var(--fg-dim);margin:2px 0">👤 {author_str}</div>\n'

            if abstract:
                cards += f'  <div style="font-size:0.82rem;color:var(--fg);margin-top:6px;line-height:1.5;opacity:0.85">{abstract}</div>\n'

            cards += '</div>\n'

        topic_sections += f"""
<div class="section">
  <div class="section-title">{topic_icon} {e(topic_label)}</div>
  <div class="section-body">
    <div style="font-size:0.82rem;color:var(--fg-dim);margin-bottom:8px">
      {meta_str} &nbsp;│&nbsp; interesting / total scanned
    </div>
    {cards}
  </div>
</div>"""

    # LLM analysis section (from think notes)
    think_dir = os.path.join(DATA_DIR, "think")
    analysis_html = ""
    if os.path.isdir(think_dir):
        analysis_types = {"publication", "dissertation", "patent"}
        analysis_notes = []
        for fp in sorted(glob.glob(os.path.join(think_dir, "note-publication-*.json")) +
                         glob.glob(os.path.join(think_dir, "note-dissertation-*.json")) +
                         glob.glob(os.path.join(think_dir, "note-patent-*.json")), reverse=True)[:6]:
            note = load_json(fp)
            if note:
                analysis_notes.append(note)

        if analysis_notes:
            note_cards = ""
            for note in analysis_notes:
                ntype = note.get("type", "?")
                ntitle = e(note.get("title", ""))
                ncontent = note.get("content", "")
                ngen = e(note.get("generated", "?"))
                nchars = len(ncontent)
                type_icon = {"publication": "📄", "dissertation": "🎓", "patent": "📜"}.get(ntype, "📝")
                type_color = {"publication": "var(--cyan)", "dissertation": "var(--magenta)", "patent": "var(--amber)"}.get(ntype, "var(--fg)")
                content_html = e(ncontent).replace("\\n", "<br>")

                note_cards += f"""
<details style="margin:6px 0">
  <summary style="cursor:pointer;font-size:0.88rem;color:var(--fg)">
    {type_icon} <span style="color:{type_color}">[{ntype.upper()}]</span> {ntitle}
    <span style="float:right;font-size:0.78rem;color:var(--fg-dim)">{ngen} │ {nchars} chars</span>
  </summary>
  <div style="font-family:monospace;white-space:pre-wrap;line-height:1.5;font-size:0.82rem;color:var(--fg);padding:8px 12px;margin-top:4px;background:rgba(0,0,0,0.2);border-radius:4px">{content_html}</div>
</details>"""

            analysis_html = f"""
<div class="section">
  <div class="section-title">🔬 LLM ANALYSIS — recent academic assessments</div>
  <div class="section-body">{note_cards}
  </div>
</div>"""

    body = summary + topic_sections + analysis_html
    return page_wrap("ACADEMIC", body, "academic")

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
        "home": "🏠", "career-scan": "💼",
        "city-watch": "🏙️", "market": "📊",
        "publication": "📄", "dissertation": "🎓",
        "patent": "📜",
    }
    type_colors = {
        "weekly": "var(--green)", "trends": "var(--amber)",
        "crossfeed": "var(--cyan)", "research": "var(--magenta)",
        "career": "var(--amber)", "crawl": "var(--blue)",
        "learn": "var(--green)", "signal": "var(--red)",
        "home": "var(--cyan)", "career-scan": "var(--purple)",
        "city-watch": "var(--blue)", "market": "var(--amber)",
        "publication": "var(--cyan)", "dissertation": "var(--magenta)",
        "patent": "var(--amber)",
    }

    for entry in index[:30]:
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


# ─── Page: Events & Meetups (events.html) ───

def gen_events():
    """Generate the events / meetups / conferences page."""
    events_path = os.path.join(DATA_DIR, "events", "latest-events.json")

    if not os.path.exists(events_path):
        body = """
<div class="section">
  <div class="section-title">🎤 EVENTS & MEETUPS</div>
  <div class="section-body" style="text-align:center;padding:40px;color:var(--fg-dim)">
    <div style="font-size:3rem;margin-bottom:16px">🎤</div>
    <div style="font-size:1.1rem;color:var(--amber)">No event data yet</div>
    <div style="margin-top:12px;color:var(--fg-dim);font-size:0.9rem">
      event-scout.py scans Meetup, Crossweb, Konfeo, Eventbrite, and conference sites.<br>
      Focus: embedded Linux, kernel, camera/imaging, automotive ADAS, edge AI.<br>
      Geographic priority: Łódź → Warsaw → Poland → Europe.
    </div>
  </div>
</div>"""
        return page_wrap("EVENTS", body, "events")

    data = load_json(events_path) or {}
    meta = data.get("meta", {})
    events = data.get("events", [])

    scan_ts = format_dual_timestamps(meta)
    duration = meta.get("duration_seconds", 0)
    total_found = meta.get("total_found", 0)
    relevant = meta.get("relevant", len(events))
    sources_list = meta.get("sources", [])

    # Location tier styling
    tier_colors = {
        "Łódź": "var(--green)", "Warsaw": "var(--cyan)",
        "Poland": "var(--blue)", "Europe": "var(--purple)",
        "unknown": "var(--fg-dim)",
    }
    tier_emoji = {
        "Łódź": "🏠", "Warsaw": "🚆", "Poland": "🇵🇱",
        "Europe": "🌍", "unknown": "📍",
    }

    # Overview
    local_count = sum(1 for ev in events if ev.get("location_tier") in ("Łódź", "Warsaw"))
    local_color = "var(--green)" if local_count >= 3 else "var(--amber)" if local_count >= 1 else "var(--fg-dim)"
    overview = f"""
<div class="section">
  <div class="section-title">🎤 EVENTS & MEETUPS &nbsp;<span style="color:var(--fg-dim);font-size:0.8rem">// {e(scan_ts)} ({duration}s)</span></div>
  <div class="section-body">
    <div style="display:flex;gap:32px;flex-wrap:wrap;font-size:0.9rem">
      <div><span style="color:var(--cyan);font-size:1.8rem;font-weight:bold">{relevant}</span><br><span style="color:var(--fg-dim)">relevant events</span></div>
      <div><span style="color:{local_color};font-size:1.8rem;font-weight:bold">{local_count}</span><br><span style="color:var(--fg-dim)">local (Łódź/Warsaw)</span></div>
      <div><span style="color:var(--purple);font-size:1.8rem;font-weight:bold">{total_found}</span><br><span style="color:var(--fg-dim)">total scanned</span></div>
      <div><span style="color:var(--blue);font-size:1.8rem;font-weight:bold">{len(sources_list)}</span><br><span style="color:var(--fg-dim)">sources</span></div>
    </div>
    <div style="margin-top:12px;font-size:0.82rem;color:var(--fg-dim)">
      Sources: {e(", ".join(s.split(":")[0] if ":" in s else s for s in sources_list[:6]))} &nbsp;│&nbsp;
      Updated daily at 02:30
    </div>
  </div>
</div>"""

    # Upcoming events sorted by score (high-priority first)
    events_sorted = sorted(events, key=lambda x: -x.get("combined_score", 0))

    # Top picks: events with score >= 2 and a real location
    top_picks = [ev for ev in events_sorted if ev.get("combined_score", 0) >= 2
                 and ev.get("location_tier") != "unknown"]
    top_html = ""
    if top_picks:
        cards = ""
        for ev in top_picks[:10]:
            name = e(ev.get("name", "?"))[:80]
            date_str = e(ev.get("date", ""))[:30] or "TBA"
            tier = ev.get("location_tier", "unknown")
            loc = e(ev.get("location", ev.get("city", "?")))
            score = ev.get("combined_score", 0)
            url = ev.get("url", "")
            source = e(ev.get("source", "?").split(":")[0] if ":" in ev.get("source", "") else ev.get("source", "?"))
            desc = e(ev.get("description", ""))[:150]
            keywords = ev.get("matched_keywords", [])
            kw_str = e(", ".join(keywords[:4]))

            tc = tier_colors.get(tier, "var(--fg-dim)")
            te = tier_emoji.get(tier, "📍")
            sc = "var(--green)" if score >= 5 else "var(--amber)" if score >= 2 else "var(--fg-dim)"

            # Clean up DuckDuckGo redirect URLs
            if url.startswith("//duckduckgo.com/l/"):
                parsed = urllib.parse.parse_qs(urllib.parse.urlparse("https:" + url).query)
                url = parsed.get("uddg", [url])[0]

            title_link = f'<a href="{e(url)}" target="_blank" style="color:var(--cyan);font-weight:bold;text-decoration:none">{name} ↗</a>' if url else f'<span style="color:var(--cyan);font-weight:bold">{name}</span>'

            desc_div = f'<div style="margin-top:4px;color:var(--fg-dim);font-size:0.82rem;max-height:40px;overflow:hidden">{desc}</div>' if desc else ""
            kw_div = f'<div style="margin-top:3px;font-size:0.78rem;color:var(--purple)">🏷️ {kw_str}</div>' if kw_str else ""

            cards += f"""<div style="background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:12px;margin-bottom:8px">
      <div style="display:flex;justify-content:space-between;align-items:flex-start">
        <div style="flex:1">
          <span style="color:{sc};font-weight:bold;font-size:1.2rem">{score:.0f}</span>
          <span style="margin-left:8px">{title_link}</span>
          <div style="margin-top:4px;font-size:0.82rem">
            <span style="color:{tc}">{te} {loc}</span>
            <span style="color:var(--fg-dim);margin-left:12px">📅 {date_str}</span>
            <span style="color:var(--fg-dim);margin-left:12px;font-size:0.78rem">[{source}]</span>
          </div>
          {kw_div}
          {desc_div}
        </div>
      </div>
    </div>"""

        top_html = f"""
<div class="section">
  <div class="section-title">▸ ⭐ TOP PICKS — conferences & local meetups</div>
  <div class="section-body">{cards}</div>
</div>"""

    # All events table
    event_rows = ""
    for ev in events_sorted:
        name = e(ev.get("name", "?"))[:65]
        date_str = e(ev.get("date", ""))[:25] or "TBA"
        tier = ev.get("location_tier", "unknown")
        loc = e(ev.get("location", ev.get("city", "?"))[:25])
        score = ev.get("combined_score", 0)
        url = ev.get("url", "")
        source = ev.get("source", "?")
        if ":" in source:
            source = source.split(":")[0]

        # Clean DuckDuckGo URLs
        if url.startswith("//duckduckgo.com/l/"):
            parsed = urllib.parse.parse_qs(urllib.parse.urlparse("https:" + url).query)
            url = parsed.get("uddg", [url])[0]

        tc = tier_colors.get(tier, "var(--fg-dim)")
        te = tier_emoji.get(tier, "📍")
        sc = "var(--green)" if score >= 5 else "var(--amber)" if score >= 2 else "var(--fg-dim)"
        title_html = f'<a href="{e(url)}" target="_blank" style="color:var(--cyan);text-decoration:none">{name}</a>' if url else name

        event_rows += f"""<tr>
          <td style="color:{sc};font-weight:bold;text-align:center">{score:.0f}</td>
          <td style="color:{tc}">{te}</td>
          <td>{title_html}</td>
          <td style="color:var(--fg-dim)">{loc}</td>
          <td style="color:var(--fg-dim)">{e(date_str)}</td>
          <td style="color:var(--fg-dim);font-size:0.8rem">{e(source)}</td>
        </tr>"""

    all_events_html = f"""
<div class="section">
  <div class="section-title">▸ 📋 ALL DISCOVERED EVENTS ({len(events)})</div>
  <div class="section-body">
    <table class="host-table">
      <thead><tr><th>Score</th><th></th><th>Event</th><th>Location</th><th>Date</th><th>Source</th></tr></thead>
      <tbody>{event_rows}</tbody>
    </table>
  </div>
</div>"""

    body = overview + top_html + all_events_html
    return page_wrap("EVENTS", body, "events")


# ─── Page: Radio Scanner (radio.html) ───

def gen_radio():
    """Generate the radio scanner / radioscanner.pl page."""
    radio_dir = os.path.join(DATA_DIR, "radio")
    scan_path = os.path.join(radio_dir, "radio-latest.json")

    if not os.path.exists(scan_path):
        body = """
<div class="section">
  <div class="section-title">📻 RADIO SCANNER</div>
  <div class="section-body" style="text-align:center;padding:40px;color:var(--fg-dim)">
    <div style="font-size:3rem;margin-bottom:16px">📻</div>
    <div style="font-size:1.1rem;color:var(--amber)">No radio scan data yet</div>
    <div style="margin-top:12px;color:var(--fg-dim);font-size:0.9rem">
      radio-scan.py scrapes radioscanner.pl for radio activity updates.<br>
      Monitoring: Łódź local, satellite comms, shortwave DX, ham radio.<br>
      Scheduled every 6 hours. First scan results will appear here.
    </div>
  </div>
</div>"""
        return page_wrap("RADIO", body, "radio")

    scan = load_json(scan_path) or {}
    meta = scan.get("meta", {})
    threads = scan.get("threads", [])
    highlights = scan.get("highlights", [])
    section_stats = scan.get("section_stats", {})

    scan_ts = format_dual_timestamps(meta)
    duration = meta.get("duration_seconds", 0)

    # Category emoji map
    cat_emoji = {
        "monitoring": "📡", "frequencies": "📻", "satellite": "🛰️",
        "shortwave": "🌊", "ham": "📟", "broadcast": "📺",
        "comms": "🔊", "airband": "✈️",
    }

    # ─ Overview section ─
    n_highlights = len(highlights)
    hot_color = "var(--red)" if n_highlights >= 5 else "var(--amber)" if n_highlights >= 2 else "var(--green)"
    overview = f"""
<div class="section">
  <div class="section-title">📻 RADIO SCANNER &nbsp;<span style="color:var(--fg-dim);font-size:0.8rem">// {e(scan_ts)} ({duration}s)</span></div>
  <div class="section-body">
    <div style="display:flex;gap:32px;flex-wrap:wrap;font-size:0.9rem">
      <div><span style="color:{hot_color};font-size:1.8rem;font-weight:bold">{n_highlights}</span><br><span style="color:var(--fg-dim)">highlights</span></div>
      <div><span style="color:var(--cyan);font-size:1.8rem;font-weight:bold">{len(threads)}</span><br><span style="color:var(--fg-dim)">threads found</span></div>
      <div><span style="color:var(--purple);font-size:1.8rem;font-weight:bold">{meta.get('sections_scanned', 0)}</span><br><span style="color:var(--fg-dim)">sections</span></div>
      <div><span style="color:var(--blue);font-size:1.8rem;font-weight:bold">{meta.get('previews_fetched', 0)}</span><br><span style="color:var(--fg-dim)">previews</span></div>
    </div>
    <div style="margin-top:12px;font-size:0.82rem;color:var(--fg-dim)">
      Source: <a href="https://radioscanner.pl" target="_blank" style="color:var(--cyan)">radioscanner.pl</a> &nbsp;│&nbsp;
      Window: last {meta.get('thread_age_cutoff_days', 7)} days &nbsp;│&nbsp;
      Next scan: every 6h
    </div>
  </div>
</div>"""

    # ─ AI Briefing section ─
    briefing = scan.get("briefing", "")
    briefing_ts = meta.get("analyze_timestamp", meta.get("briefing_generated", ""))[:16]
    briefing_html = ""
    if briefing:
        # Render plain text briefing with line breaks preserved
        briefing_lines = ""
        for line in briefing.strip().split("\n"):
            line_esc = e(line)
            # Highlight section headers (lines that are ALL CAPS or start with common header patterns)
            if line.strip() and (line.strip().isupper() or line.strip().endswith(":")):
                briefing_lines += f'<div style="color:var(--amber);font-weight:bold;margin-top:10px">{line_esc}</div>\n'
            elif line.strip().startswith("- ") or line.strip().startswith("• "):
                briefing_lines += f'<div style="padding-left:16px;color:var(--fg)">{line_esc}</div>\n'
            else:
                briefing_lines += f'<div style="color:var(--fg-dim)">{line_esc}</div>\n'

        briefing_html = f"""
<div class="section">
  <div class="section-title">▸ 🤖 AI BRIEFING &nbsp;<span style="color:var(--fg-dim);font-size:0.8rem">// clawdbot analysis {e(briefing_ts)}</span></div>
  <div class="section-body" style="font-size:0.88rem;line-height:1.6">
    {briefing_lines}
  </div>
</div>"""

    # ─ Section breakdown ─
    sec_rows = ""
    for name, st in sorted(section_stats.items(), key=lambda x: -x[1].get("recent", 0)):
        em = st.get("emoji", "📻")
        total = st.get("total", 0)
        recent = st.get("recent", 0)
        cat = st.get("category", "?")
        bar_len = min(recent * 2, 30)
        bar = "█" * bar_len + "░" * max(0, 10 - bar_len)
        sec_rows += f"""<tr>
          <td>{em}</td>
          <td style="color:var(--cyan)">{e(name)}</td>
          <td style="color:var(--fg-dim)">{cat}</td>
          <td>{total}</td>
          <td style="color:var(--green);font-weight:bold">{recent}</td>
          <td style="font-family:monospace;color:var(--green);font-size:0.75rem">{bar}</td>
        </tr>"""

    sections_html = f"""
<div class="section">
  <div class="section-title">▸ 📡 FORUM SECTIONS</div>
  <div class="section-body">
    <table class="host-table">
      <thead><tr><th></th><th>Section</th><th>Category</th><th>Total</th><th>Recent</th><th>Activity</th></tr></thead>
      <tbody>{sec_rows}</tbody>
    </table>
  </div>
</div>"""

    # ─ Highlights section (score >= 25) ─
    highlights_html = ""
    if highlights:
        cards = ""
        for h in highlights[:15]:
            score = h.get("score", 0)
            title = e(h.get("title", "?"))
            section = e(h.get("section", "?"))
            sec_emoji = h.get("section_emoji", "📻")
            url = h.get("url", "")
            reasons = ", ".join(h.get("reasons", [])[:5])
            preview = e(h.get("preview", "")[:200])
            date_str = h.get("date", "")[:10] if h.get("date") else "?"

            score_color = "var(--green)" if score >= 50 else "var(--amber)" if score >= 30 else "var(--fg-dim)"
            title_link = f'<a href="{e(url)}" target="_blank" style="color:var(--cyan);font-weight:bold;text-decoration:none">{title} ↗</a>' if url else f'<span style="color:var(--cyan);font-weight:bold">{title}</span>'

            preview_div = f'<div style="margin-top:6px;color:var(--fg-dim);font-size:0.82rem;max-height:60px;overflow:hidden">{preview}</div>' if preview else ""

            cards += f"""
    <div style="background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:12px;margin-bottom:8px">
      <div style="display:flex;justify-content:space-between;align-items:flex-start">
        <div style="flex:1">
          <span style="color:{score_color};font-weight:bold;font-size:1.2rem">{score}</span>
          <span style="margin-left:8px">{title_link}</span>
          <div style="margin-top:4px;font-size:0.82rem">
            <span style="color:var(--fg-dim)">{sec_emoji} {section}</span>
            <span style="color:var(--fg-dim);margin-left:12px">📅 {date_str}</span>
          </div>
          <div style="margin-top:4px;font-size:0.78rem;color:var(--purple)">🏷️ {e(reasons)}</div>
          {preview_div}
        </div>
      </div>
    </div>"""

        highlights_html = f"""
<div class="section">
  <div class="section-title">▸ 🎯 HIGHLIGHTS — scored ≥25</div>
  <div class="section-body">{cards}</div>
</div>"""

    # ─ All threads table ─
    thread_rows = ""
    for t in threads[:50]:
        score = t.get("score", 0)
        title = e(t.get("title", "?"))[:80]
        section = e(t.get("section", "?"))
        sec_emoji = t.get("section_emoji", "📻")
        url = t.get("url", "")
        age = t.get("age_days")
        cat = t.get("category", "?")

        score_color = "var(--green)" if score >= 50 else "var(--amber)" if score >= 25 else "var(--fg-dim)"
        age_str = f"{age:.0f}d" if age is not None else "?"
        age_color = "var(--green)" if age is not None and age <= 1 else "var(--amber)" if age is not None and age <= 3 else "var(--fg-dim)"
        title_html = f'<a href="{e(url)}" target="_blank" style="color:var(--cyan);text-decoration:none">{title}</a>' if url else title

        thread_rows += f"""<tr>
          <td style="color:{score_color};font-weight:bold;text-align:center">{score}</td>
          <td>{sec_emoji}</td>
          <td>{title_html}</td>
          <td style="color:var(--fg-dim)">{section}</td>
          <td style="color:{age_color};text-align:center">{age_str}</td>
        </tr>"""

    threads_html = f"""
<div class="section">
  <div class="section-title">▸ 📋 ALL RECENT THREADS</div>
  <div class="section-body">
    <table class="host-table">
      <thead><tr><th>Score</th><th></th><th>Thread</th><th>Section</th><th>Age</th></tr></thead>
      <tbody>{thread_rows}</tbody>
    </table>
  </div>
</div>"""

    body = overview + briefing_html + sections_html + highlights_html + threads_html
    return page_wrap("RADIO", body, "radio")


# ─── Page: Car Tracker (car.html) ───

def gen_car_tracker():
    """Generate detailed car tracker dashboard with log, statistics, and AI summary."""
    import re as _re
    import glob as _glob

    car_dir = os.path.join(DATA_DIR, "car-tracker")
    latest_path = os.path.join(car_dir, "latest-car-tracker.json")

    if not os.path.exists(latest_path):
        body = """
<div class="section">
  <div class="section-title">🚗 CAR TRACKER</div>
  <div class="section-body" style="text-align:center;padding:40px;color:var(--fg-dim)">
    <div style="font-size:3rem;margin-bottom:16px">🚗</div>
    <div style="font-size:1.1rem;color:var(--amber)">No car tracker data yet</div>
    <div style="margin-top:12px;color:var(--fg-dim);font-size:0.9rem">
      car-tracker.py fetches GPS data from SinoTrack and analyzes movement patterns.<br>
      First scan results will appear here.
    </div>
  </div>
</div>"""
        return page_wrap("CAR TRACKER", body, "car")

    data = load_json(latest_path) or {}
    status = data.get("current_status", {})
    trips = data.get("trips", [])
    stops = data.get("stops", [])
    mileage = data.get("mileage", {})
    clusters = data.get("location_clusters", [])
    daily = data.get("daily_summary", {})
    llm_text = data.get("llm_analysis", "")
    alarms = data.get("alarms", [])
    meta = data.get("meta", {})
    generated = data.get("generated", "")[:16]

    # ── Load historical files for log section ──
    historical = []
    if os.path.isdir(car_dir):
        for fp in sorted(_glob.glob(os.path.join(car_dir, "car-tracker-*.json")))[-30:]:
            try:
                with open(fp) as f:
                    h = json.load(f)
                historical.append({
                    "file": os.path.basename(fp),
                    "generated": h.get("generated", "")[:16],
                    "trips": len(h.get("trips", [])),
                    "stops": len(h.get("stops", [])),
                    "track_points": h.get("meta", {}).get("track_points", 0),
                    "elapsed_s": h.get("meta", {}).get("elapsed_s", 0),
                })
            except:
                pass

    # ════════════════════════════════════════════════════════════════════════
    # Section 1: Current Status
    # ════════════════════════════════════════════════════════════════════════
    status_html = ""
    if status:
        is_moving = status.get("is_moving", False)
        status_emoji = "🏃" if is_moving else "🅿️"
        status_text = f"{status.get('speed_kmh', 0)} km/h {status.get('direction_compass', '')}" if is_moving else "Parked"
        status_color = "var(--amber)" if is_moving else "var(--green)"
        parked_info = ""
        if status.get("parked_duration_h"):
            parked_info = f' · parked {status["parked_duration_h"]:.1f}h'
        if status.get("parked_since"):
            parked_info += f' (since {status["parked_since"][-8:]})'
        odo = status.get("total_mileage_km", 0)
        location = e(status.get("location", "?"))
        age_min = status.get("data_age_min", -1)
        age_text = f"{age_min:.0f}m ago" if age_min >= 0 else "?"
        last_update = e(status.get("last_update", "?"))
        lat = status.get("lat", 0)
        lon = status.get("lon", 0)

        status_html = f"""
<div class="section">
  <div class="section-title">📍 CURRENT STATUS <span style="color:var(--fg-dim);font-size:0.8rem">// updated {age_text}</span></div>
  <div class="section-body">
    <div style="display:flex;gap:20px;align-items:center;flex-wrap:wrap">
      <div style="font-size:2.6rem">{status_emoji}</div>
      <div>
        <div style="font-size:1.5rem;font-weight:bold;color:{status_color}">{status_text}{parked_info}</div>
        <div style="color:var(--cyan);font-size:1.1rem">{location}</div>
        <div style="color:var(--fg-dim);font-size:0.85rem">
          {lat:.5f}, {lon:.5f} · Odometer: {odo:,.0f} km · Last update: {last_update}
        </div>
      </div>
    </div>
  </div>
</div>"""

    # ════════════════════════════════════════════════════════════════════════
    # Section 2: Statistics Overview
    # ════════════════════════════════════════════════════════════════════════
    total_trip_km = sum(t.get("distance_km", 0) for t in trips)
    total_trip_min = sum(t.get("duration_min", 0) for t in trips)
    max_speed_all = max((t.get("max_speed_kmh", 0) for t in trips), default=0)
    avg_trip_km = total_trip_km / len(trips) if trips else 0
    longest_trip = max(trips, key=lambda t: t.get("distance_km", 0)) if trips else None
    fastest_trip = max(trips, key=lambda t: t.get("max_speed_kmh", 0)) if trips else None

    avg_daily = mileage.get("avg_km", 0) if mileage else 0
    total_mileage_km = mileage.get("total_km", 0) if mileage else 0
    zero_days = mileage.get("zero_days", 0) if mileage else 0
    days_tracked = mileage.get("days_tracked", 0) if mileage else 0

    stats_html = f"""
<div class="section">
  <div class="section-title">📊 STATISTICS <span style="color:var(--fg-dim);font-size:0.8rem">// {meta.get('track_days', 0)}-day track · {days_tracked}-day mileage</span></div>
  <div class="section-body">
    <div style="display:flex;gap:24px;flex-wrap:wrap;margin-bottom:20px">
      <div style="background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:14px;min-width:120px;text-align:center">
        <div style="color:var(--green);font-size:2rem;font-weight:bold">{len(trips)}</div>
        <div style="color:var(--fg-dim);font-size:0.8rem">trips detected</div>
      </div>
      <div style="background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:14px;min-width:120px;text-align:center">
        <div style="color:var(--cyan);font-size:2rem;font-weight:bold">{total_trip_km:.1f}</div>
        <div style="color:var(--fg-dim);font-size:0.8rem">km driven ({meta.get('track_days', 0)}d)</div>
      </div>
      <div style="background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:14px;min-width:120px;text-align:center">
        <div style="color:var(--amber);font-size:2rem;font-weight:bold">{avg_daily:.1f}</div>
        <div style="color:var(--fg-dim);font-size:0.8rem">km/day avg ({days_tracked}d)</div>
      </div>
      <div style="background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:14px;min-width:120px;text-align:center">
        <div style="color:{'var(--red)' if max_speed_all > 120 else 'var(--fg)'};font-size:2rem;font-weight:bold">{max_speed_all}</div>
        <div style="color:var(--fg-dim);font-size:0.8rem">max km/h</div>
      </div>
      <div style="background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:14px;min-width:120px;text-align:center">
        <div style="color:var(--fg);font-size:2rem;font-weight:bold">{avg_trip_km:.1f}</div>
        <div style="color:var(--fg-dim);font-size:0.8rem">avg km/trip</div>
      </div>
      <div style="background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:14px;min-width:120px;text-align:center">
        <div style="color:var(--fg-dim);font-size:2rem;font-weight:bold">{zero_days}</div>
        <div style="color:var(--fg-dim);font-size:0.8rem">idle days</div>
      </div>
    </div>"""

    # Notable trips
    notable = ""
    if longest_trip:
        notable += f'<div style="font-size:0.85rem;color:var(--fg-dim);margin-bottom:4px">🏆 Longest: <span style="color:var(--cyan)">{longest_trip["distance_km"]} km</span> — {e(longest_trip["start_location"])} → {e(longest_trip["end_location"])} ({longest_trip["start_ts"][5:]})</div>'
    if fastest_trip and fastest_trip != longest_trip:
        spd_c = "var(--red)" if fastest_trip["max_speed_kmh"] > 120 else "var(--amber)"
        notable += f'<div style="font-size:0.85rem;color:var(--fg-dim)">⚡ Fastest: <span style="color:{spd_c}">{fastest_trip["max_speed_kmh"]} km/h</span> — {e(fastest_trip["start_location"])} → {e(fastest_trip["end_location"])} ({fastest_trip["start_ts"][5:]})</div>'

    if notable:
        stats_html += f'<div style="margin-top:8px">{notable}</div>'

    stats_html += """
  </div>
</div>"""

    # ════════════════════════════════════════════════════════════════════════
    # Section 3: Daily Mileage Chart
    # ════════════════════════════════════════════════════════════════════════
    mileage_html = ""
    daily_entries = mileage.get("daily", []) if mileage else []
    if daily_entries:
        max_km = max(d.get("km", 0) for d in daily_entries) or 1
        bars = ""
        for d in daily_entries[-14:]:
            km = d.get("km", 0)
            day_label = d.get("date", "?")[-5:]
            pct = min(100, km / max_km * 100)
            bar_color = "var(--green)" if km > 5 else ("var(--amber)" if km > 0.5 else "var(--fg-dim)")
            bars += f"""<div style="text-align:center;min-width:38px">
              <div style="height:80px;display:flex;align-items:flex-end;justify-content:center">
                <div style="width:24px;background:{bar_color};border-radius:3px 3px 0 0;height:{max(2, pct * 0.8):.0f}px"></div>
              </div>
              <div style="font-size:0.7rem;color:var(--fg-dim);margin-top:2px">{day_label}</div>
              <div style="font-size:0.7rem;color:var(--cyan)">{km:.1f}</div>
            </div>"""
        mileage_html = f"""
<div class="section">
  <div class="section-title">📈 DAILY MILEAGE <span style="color:var(--fg-dim);font-size:0.8rem">// {days_tracked} days · total {total_mileage_km:.0f} km</span></div>
  <div class="section-body">
    <div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:center">{bars}</div>
  </div>
</div>"""

    # ════════════════════════════════════════════════════════════════════════
    # Section 4: AI Analysis (full LLM output)
    # ════════════════════════════════════════════════════════════════════════
    ai_html = ""
    if llm_text:
        formatted = e(llm_text)
        formatted = _re.sub(r'^## (.+)$', r'<h3 style="color:var(--amber);margin:16px 0 8px">\1</h3>', formatted, flags=_re.MULTILINE)
        formatted = _re.sub(r'^### (.+)$', r'<div style="color:var(--amber);font-weight:bold;margin:12px 0 4px">\1</div>', formatted, flags=_re.MULTILINE)
        formatted = _re.sub(r'^- (.+)$', r'<div style="padding-left:16px;margin:2px 0">• \1</div>', formatted, flags=_re.MULTILINE)
        formatted = _re.sub(r'\*\*([^*]+)\*\*', r'<strong style="color:var(--cyan)">\1</strong>', formatted)
        formatted = formatted.replace("\n\n", "<br><br>").replace("\n", "<br>")
        ai_html = f"""
<div class="section">
  <div class="section-title">🤖 AI MOVEMENT ANALYSIS</div>
  <div class="section-body">
    <div style="font-size:0.88rem;line-height:1.6">{formatted}</div>
  </div>
</div>"""

    # ════════════════════════════════════════════════════════════════════════
    # Section 5: Trip Log (full table of all trips)
    # ════════════════════════════════════════════════════════════════════════
    trips_html = ""
    if trips:
        trip_rows = ""
        for t in reversed(trips):
            from_loc_full = e(t.get("start_location", "?"))
            to_loc_full = e(t.get("end_location", "?"))
            dist = t.get("distance_km", 0)
            dur = t.get("duration_min", 0)
            max_spd = t.get("max_speed_kmh", 0)
            avg_spd = t.get("avg_speed_kmh", 0)
            ts_start = t.get("start_ts", "?")
            ts_end = t.get("end_ts", "?")[-5:]  # just HH:MM
            pts = t.get("points", 0)
            spd_color = "var(--red)" if max_spd > 120 else ("var(--amber)" if max_spd > 90 else "var(--fg-dim)")
            dist_color = "var(--green)" if dist > 10 else ("var(--cyan)" if dist > 3 else "var(--fg-dim)")
            trip_rows += f"""<tr>
              <td style="color:var(--fg-dim);white-space:nowrap">{ts_start}</td>
              <td style="color:var(--fg-dim);white-space:nowrap">{ts_end}</td>
              <td style="color:var(--cyan);max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{from_loc_full}">{from_loc_full}</td>
              <td style="color:var(--fg-dim)">→</td>
              <td style="color:var(--cyan);max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{to_loc_full}">{to_loc_full}</td>
              <td style="text-align:right;color:{dist_color}">{dist:.1f}</td>
              <td style="text-align:right;color:var(--fg-dim)">{dur:.0f}m</td>
              <td style="text-align:right;color:{spd_color}">{max_spd}</td>
              <td style="text-align:right;color:var(--fg-dim)">{avg_spd:.0f}</td>
              <td style="text-align:right;color:var(--fg-dim)">{pts}</td>
            </tr>"""
        trips_html = f"""
<div class="section">
  <div class="section-title">🗺️ TRIP LOG <span style="color:var(--fg-dim);font-size:0.8rem">// {len(trips)} trips · {total_trip_km:.1f} km total · {total_trip_min:.0f} min driving</span></div>
  <div class="section-body">
    <table class="host-table">
      <thead><tr>
        <th>Start</th><th>End</th><th>From</th><th></th><th>To</th>
        <th style="text-align:right">km</th><th style="text-align:right">Dur</th>
        <th style="text-align:right">Max</th><th style="text-align:right">Avg</th>
        <th style="text-align:right">Pts</th>
      </tr></thead>
      <tbody>{trip_rows}</tbody>
    </table>
  </div>
</div>"""

    # ════════════════════════════════════════════════════════════════════════
    # Section 6: Frequent Locations
    # ════════════════════════════════════════════════════════════════════════
    locations_html = ""
    if clusters:
        loc_rows = ""
        for c in clusters:
            loc = e(c.get("location", "?"))
            visits = c.get("visits", 0)
            hours = c.get("total_hours", 0)
            lat = c.get("lat", 0)
            lon = c.get("lon", 0)
            # Bar proportional to hours
            max_h = max(cl.get("total_hours", 0) for cl in clusters) or 1
            bar_pct = min(100, hours / max_h * 100)
            loc_rows += f"""<tr>
              <td style="color:var(--cyan);font-weight:bold">{loc}</td>
              <td style="text-align:right">{visits}</td>
              <td style="text-align:right;color:var(--fg-dim)">{hours:.1f}h</td>
              <td style="width:40%">
                <div style="background:var(--bg2);border-radius:3px;height:14px;overflow:hidden">
                  <div style="background:var(--cyan);height:100%;width:{bar_pct:.0f}%;border-radius:3px"></div>
                </div>
              </td>
              <td style="color:var(--fg-dim);font-size:0.75rem">{lat:.4f},{lon:.4f}</td>
            </tr>"""
        locations_html = f"""
<div class="section">
  <div class="section-title">📌 FREQUENT LOCATIONS <span style="color:var(--fg-dim);font-size:0.8rem">// {len(clusters)} distinct</span></div>
  <div class="section-body">
    <table class="host-table">
      <thead><tr><th>Location</th><th style="text-align:right">Visits</th><th style="text-align:right">Time</th><th>Distribution</th><th>Coordinates</th></tr></thead>
      <tbody>{loc_rows}</tbody>
    </table>
  </div>
</div>"""

    # ════════════════════════════════════════════════════════════════════════
    # Section 7: Stop Log (significant parking/stops)
    # ════════════════════════════════════════════════════════════════════════
    stops_html = ""
    if stops:
        stop_rows = ""
        for s in sorted(stops, key=lambda x: x.get("start_time", 0), reverse=True)[:50]:
            loc_full = e(s.get("location", "?"))
            dur = s.get("duration_min", 0)
            start_ts = s.get("start_ts", "?")
            end_ts = s.get("end_ts", "?")[-5:] if s.get("end_ts") else "?"
            dur_color = "var(--green)" if dur > 120 else ("var(--fg)" if dur > 30 else "var(--fg-dim)")
            stop_rows += f"""<tr>
              <td style="color:var(--fg-dim);white-space:nowrap">{start_ts}</td>
              <td style="color:var(--fg-dim)">{end_ts}</td>
              <td style="color:var(--cyan);max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{loc_full}">{loc_full}</td>
              <td style="text-align:right;color:{dur_color}">{dur:.0f}m</td>
            </tr>"""
        stops_html = f"""
<div class="section">
  <div class="section-title">🅿️ STOP LOG <span style="color:var(--fg-dim);font-size:0.8rem">// {len(stops)} significant stops (≥10 min)</span></div>
  <div class="section-body">
    <table class="host-table">
      <thead><tr><th>Start</th><th>End</th><th>Location</th><th style="text-align:right">Duration</th></tr></thead>
      <tbody>{stop_rows}</tbody>
    </table>
  </div>
</div>"""

    # ════════════════════════════════════════════════════════════════════════
    # Section 8: Daily Breakdown
    # ════════════════════════════════════════════════════════════════════════
    daily_html = ""
    if daily:
        day_rows = ""
        for day_key in sorted(daily.keys(), reverse=True):
            d = daily[day_key]
            tc = d.get("trip_count", 0)
            km = d.get("total_km", 0)
            drv_min = d.get("total_driving_min", 0)
            park_min = d.get("total_parked_min", 0)
            max_spd = d.get("max_speed_kmh", 0)
            spd_color = "var(--red)" if max_spd > 120 else ("var(--amber)" if max_spd > 90 else "var(--fg-dim)")
            day_rows += f"""<tr>
              <td style="color:var(--cyan);font-weight:bold">{day_key}</td>
              <td style="text-align:right">{tc}</td>
              <td style="text-align:right;color:var(--green)">{km:.1f}</td>
              <td style="text-align:right;color:var(--fg-dim)">{drv_min:.0f}m</td>
              <td style="text-align:right;color:var(--fg-dim)">{park_min:.0f}m</td>
              <td style="text-align:right;color:{spd_color}">{max_spd}</td>
            </tr>"""
        daily_html = f"""
<div class="section">
  <div class="section-title">📅 DAILY BREAKDOWN</div>
  <div class="section-body">
    <table class="host-table">
      <thead><tr><th>Date</th><th style="text-align:right">Trips</th><th style="text-align:right">km</th><th style="text-align:right">Driving</th><th style="text-align:right">Parked</th><th style="text-align:right">Max km/h</th></tr></thead>
      <tbody>{day_rows}</tbody>
    </table>
  </div>
</div>"""

    # ════════════════════════════════════════════════════════════════════════
    # Section 9: Run History / Log
    # ════════════════════════════════════════════════════════════════════════
    log_html = ""
    if historical:
        log_rows = ""
        for h in reversed(historical):
            log_rows += f"""<tr>
              <td style="color:var(--fg-dim)">{h['generated']}</td>
              <td style="color:var(--green)">{h['trips']}</td>
              <td style="color:var(--fg-dim)">{h['stops']}</td>
              <td style="color:var(--fg-dim)">{h['track_points']}</td>
              <td style="color:var(--fg-dim)">{h['elapsed_s']:.0f}s</td>
              <td style="color:var(--fg-dim);font-size:0.75rem">{h['file']}</td>
            </tr>"""
        log_html = f"""
<div class="section">
  <div class="section-title">📋 RUN LOG <span style="color:var(--fg-dim);font-size:0.8rem">// {len(historical)} scans</span></div>
  <div class="section-body">
    <table class="host-table">
      <thead><tr><th>Timestamp</th><th>Trips</th><th>Stops</th><th>Track pts</th><th>Runtime</th><th>File</th></tr></thead>
      <tbody>{log_rows}</tbody>
    </table>
  </div>
</div>"""

    # ════════════════════════════════════════════════════════════════════════
    # Section 10: Meta / Technical
    # ════════════════════════════════════════════════════════════════════════
    meta_html = f"""
<div class="section">
  <div class="section-title" style="color:var(--fg-dim)">⚙️ TECHNICAL</div>
  <div class="section-body" style="font-size:0.82rem;color:var(--fg-dim)">
    IMEI: {e(data.get('imei', '?'))} · Track points: {meta.get('track_points', 0)} ·
    Track window: {meta.get('track_days', 0)} days · Stops detected: {meta.get('stop_count', 0)} ·
    Runtime: {meta.get('elapsed_s', 0):.0f}s · Generated: {e(generated)}
  </div>
</div>"""

    body = status_html + stats_html + mileage_html + ai_html + trips_html + locations_html + stops_html + daily_html + log_html + meta_html
    return page_wrap("CAR TRACKER", body, "car")


# ─── Page: Life Advisor (advisor.html) ───

def gen_advisor():
    """Generate the ClawdBot life advisor / cross-domain intelligence page."""
    think_dir = os.path.join(DATA_DIR, "think")

    # Load cross-domain synthesis
    cross_path = os.path.join(think_dir, "latest-life-cross.json")
    cross = load_json(cross_path) if os.path.exists(cross_path) else None

    # Load life advisor
    advisor_path = os.path.join(think_dir, "latest-life-advisor.json")
    advisor = load_json(advisor_path) if os.path.exists(advisor_path) else None

    # Load system-think data
    gpu_think = os.path.exists(os.path.join(think_dir, "latest-system-gpu.json"))
    netsec_think = os.path.exists(os.path.join(think_dir, "latest-system-netsec.json"))
    health_think = os.path.exists(os.path.join(think_dir, "latest-system-health.json"))

    if not cross and not advisor and not gpu_think and not netsec_think and not health_think:
        body = """
<div class="section">
  <div class="section-title">🧭 LIFE ADVISOR</div>
  <div class="section-body" style="text-align:center;padding:40px;color:var(--fg-dim)">
    <div style="font-size:3rem;margin-bottom:16px">🧭</div>
    <div style="font-size:1.1rem;color:var(--amber)">No advisor data yet</div>
    <div style="margin-top:12px;font-size:0.9rem">
      life-think.py runs during nightly batch:<br>
      <b>Cross synthesis</b> — combines career + company intelligence<br>
      <b>Life advisor</b> — reads ALL data sources, generates actionable advice<br>
      Data will appear after the first nightly batch completes.
    </div>
  </div>
</div>"""
        return page_wrap("ADVISOR", body, "advisor")

    body = ""

    # ── Cross-domain synthesis section ──
    if cross:
        cross_date = e(cross.get("generated", "?")[:16])
        cross_analysis = cross.get("analysis", "")
        cross_sources = cross.get("sources", {})
        hot_jobs = cross_sources.get("hot_jobs", 0)
        career_cos = len(cross_sources.get("career_companies", []))
        company_cos = len(cross_sources.get("company_companies", []))
        cross_meta = cross.get("meta", {})
        dur = cross_meta.get("duration_s", 0)

        # Format analysis: convert markdown-ish sections to HTML
        formatted = _format_think_text(cross_analysis)

        body += f"""
<div class="section">
  <div class="section-title">🔀 CROSS-DOMAIN INTELLIGENCE <span style="color:var(--fg-dim);font-size:0.8rem">// {cross_date} · {career_cos} career + {company_cos} company analyses · {hot_jobs} hot jobs · {dur:.0f}s GPU</span></div>
  <div class="section-body">
    <div style="white-space:pre-wrap;line-height:1.6;font-size:0.92rem">{formatted}</div>
  </div>
</div>"""

    # ── Life advisor section ──
    if advisor:
        adv_date = e(advisor.get("generated", "?")[:16])
        adv_analysis = advisor.get("analysis", "")
        adv_sources = advisor.get("sources_used", {})
        adv_meta = advisor.get("meta", {})
        dur = adv_meta.get("duration_s", 0)
        web_count = adv_sources.get("web_searches", 0)
        notes_count = adv_sources.get("think_notes", 0)
        followup_count = adv_meta.get("followup_count", 0)

        # Count active data sources
        active_sources = sum(1 for v in adv_sources.values() if v and v not in (0, False))

        formatted = _format_think_text(adv_analysis)

        # Web research section
        web_html = ""
        web_research = advisor.get("web_research", {})
        if web_research:
            web_rows = ""
            for query, results in web_research.items():
                for r in results[:2]:
                    title = e(r.get("title", "?")[:60])
                    url = e(r.get("url", "#"))
                    snippet = e(r.get("snippet", "")[:100])
                    web_rows += f"""<tr>
                      <td style="color:var(--cyan);font-size:0.85rem">{e(query[:40])}</td>
                      <td><a href="{url}" target="_blank" style="color:var(--green);text-decoration:none">{title}</a></td>
                      <td style="color:var(--fg-dim);font-size:0.8rem">{snippet}</td>
                    </tr>"""
            if web_rows:
                web_html = f"""
<div style="margin-top:24px;border-top:1px solid var(--border);padding-top:16px">
  <div style="color:var(--amber);font-weight:bold;margin-bottom:8px">🌐 WEB RESEARCH ({len(web_research)} queries)</div>
  <table class="host-table">
    <thead><tr><th>Query</th><th>Result</th><th>Snippet</th></tr></thead>
    <tbody>{web_rows}</tbody>
  </table>
</div>"""

        # Followup research
        followup_html = ""
        followups = advisor.get("followup_research", [])
        if followups:
            fu_items = ""
            for fu in followups[:5]:
                ft = fu.get("type", "?")
                if ft == "url_fetch":
                    fu_items += f'<div style="margin:4px 0;font-size:0.85rem">📄 <a href="{e(fu.get("url","#"))}" target="_blank" style="color:var(--cyan)">{e(fu.get("url","")[:60])}</a> — {e(fu.get("excerpt","")[:100])}</div>'
                elif ft == "search":
                    fu_items += f'<div style="margin:4px 0;font-size:0.85rem">🔍 {e(fu.get("query",""))}: {len(fu.get("results",[]))} results</div>'
            if fu_items:
                followup_html = f"""
<div style="margin-top:16px;border-top:1px solid var(--border);padding-top:12px">
  <div style="color:var(--purple);font-weight:bold;margin-bottom:8px">🔭 FOLLOW-UP RESEARCH ({len(followups)} items)</div>
  {fu_items}
</div>"""

        # System health
        sys_html = ""
        sys_health = advisor.get("system_health", {})
        if sys_health:
            disk = e(sys_health.get("disk", ""))
            uptime = e(sys_health.get("uptime", ""))
            cron_jobs = e(str(sys_health.get("cron_jobs", "")))
            sys_html = f"""
<div style="margin-top:16px;border-top:1px solid var(--border);padding-top:12px">
  <div style="color:var(--fg-dim);font-weight:bold;margin-bottom:8px">🖥️ SYSTEM HEALTH</div>
  <div style="font-size:0.85rem;color:var(--fg-dim)">
    <div>⏱️ {uptime}</div>
    <div>💾 {disk}</div>
    <div>📋 {cron_jobs} active cron jobs</div>
  </div>
</div>"""

        body += f"""
<div class="section">
  <div class="section-title">🧭 LIFE ADVISOR <span style="color:var(--fg-dim);font-size:0.8rem">// {adv_date} · {active_sources} data sources · {web_count} web searches · {followup_count} follow-ups · {dur:.0f}s GPU</span></div>
  <div class="section-body">
    <div style="white-space:pre-wrap;line-height:1.6;font-size:0.92rem">{formatted}</div>
    {web_html}
    {followup_html}
    {sys_html}
  </div>
</div>"""

    # ── GPU Intelligence section ──
    gpu_data = load_json(os.path.join(think_dir, "latest-system-gpu.json")) if os.path.exists(os.path.join(think_dir, "latest-system-gpu.json")) else None
    if gpu_data:
        gpu_date = e(gpu_data.get("generated", "?")[:16])
        gpu_analysis = gpu_data.get("analysis", "")
        gpu_meta = gpu_data.get("meta", {})
        gpu_days = gpu_meta.get("days_analyzed", 0)
        gpu_dur = gpu_meta.get("duration_s", 0)

        # Quick stats from day summaries
        day_sums = gpu_data.get("day_summaries", [])
        gpu_stats = ""
        if day_sums:
            latest = day_sums[-1] if day_sums else {}
            gpu_stats = (f" · {latest.get('temp_avg', 0):.0f}°C avg · "
                         f"{latest.get('throttle_pct', 0):.0f}% throttled · "
                         f"{sum(d.get('cost_pln', 0) for d in day_sums):.1f} PLN/week")

        utilization = gpu_data.get("utilization", {})
        util_html = ""
        if utilization:
            gen_pct = utilization.get("generating_pct", 0)
            loaded_pct = utilization.get("loaded_pct", 0)
            idle_pct = utilization.get("idle_pct", 0)
            bar_gen = f'<div style="display:inline-block;height:12px;width:{gen_pct}%;background:var(--green);border-radius:2px" title="Generating {gen_pct}%"></div>'
            bar_load = f'<div style="display:inline-block;height:12px;width:{loaded_pct}%;background:var(--amber);border-radius:2px" title="Loaded {loaded_pct}%"></div>'
            bar_idle = f'<div style="display:inline-block;height:12px;width:{idle_pct}%;background:var(--fg-dim);border-radius:2px;opacity:0.3" title="Idle {idle_pct}%"></div>'
            util_html = f"""
<div style="margin-bottom:12px">
  <div style="font-size:0.85rem;color:var(--fg-dim);margin-bottom:4px">GPU Utilization (7-day)</div>
  <div style="background:var(--bg2);border-radius:4px;overflow:hidden;display:flex">{bar_gen}{bar_load}{bar_idle}</div>
  <div style="font-size:0.8rem;margin-top:4px;color:var(--fg-dim)">
    <span style="color:var(--green)">■</span> Generating {gen_pct}%
    <span style="color:var(--amber);margin-left:12px">■</span> Loaded {loaded_pct}%
    <span style="margin-left:12px">■</span> Idle {idle_pct}%
  </div>
</div>"""

        formatted_gpu = _format_think_text(gpu_analysis)
        body += f"""
<div class="section">
  <div class="section-title">🔥 GPU INTELLIGENCE <span style="color:var(--fg-dim);font-size:0.8rem">// {gpu_date} · {gpu_days} days analyzed{gpu_stats} · {gpu_dur:.0f}s GPU</span></div>
  <div class="section-body">
    {util_html}
    <div style="white-space:pre-wrap;line-height:1.6;font-size:0.92rem">{formatted_gpu}</div>
  </div>
</div>"""

    # ── Network Security section ──
    netsec_data = load_json(os.path.join(think_dir, "latest-system-netsec.json")) if os.path.exists(os.path.join(think_dir, "latest-system-netsec.json")) else None
    if netsec_data:
        netsec_date = e(netsec_data.get("generated", "?")[:16])
        netsec_analysis = netsec_data.get("analysis", "")
        netsec_meta = netsec_data.get("meta", {})
        netsec_dur = netsec_meta.get("duration_s", 0)

        scan_sum = netsec_data.get("scan_summary", {})
        host_count = scan_sum.get("host_count", 0)
        avg_score = scan_sum.get("avg_security_score", 0)
        low_hosts = scan_sum.get("low_score_hosts", 0)

        vuln_sum = netsec_data.get("vuln_summary", {})
        vuln_total = vuln_sum.get("total_findings", 0) if vuln_sum else 0
        vuln_crit = vuln_sum.get("critical", 0) if vuln_sum else 0
        vuln_high = vuln_sum.get("high", 0) if vuln_sum else 0

        score_color = "var(--green)" if avg_score >= 80 else ("var(--amber)" if avg_score >= 60 else "var(--red)")

        formatted_netsec = _format_think_text(netsec_analysis)
        body += f"""
<div class="section">
  <div class="section-title">🛡️ NETWORK SECURITY <span style="color:var(--fg-dim);font-size:0.8rem">// {netsec_date} · {host_count} hosts · score <span style="color:{score_color}">{avg_score:.0f}</span>/100 · {vuln_total} vulns ({vuln_crit} crit, {vuln_high} high) · {netsec_dur:.0f}s GPU</span></div>
  <div class="section-body">
    <div style="white-space:pre-wrap;line-height:1.6;font-size:0.92rem">{formatted_netsec}</div>
  </div>
</div>"""

    # ── Health Watchdog section ──
    health_data = load_json(os.path.join(think_dir, "latest-system-health.json")) if os.path.exists(os.path.join(think_dir, "latest-system-health.json")) else None
    if health_data:
        health_date = e(health_data.get("generated", "?")[:16])
        health_analysis = health_data.get("analysis", "")
        health_meta = health_data.get("meta", {})
        health_dur = health_meta.get("duration_s", 0)

        acounts = health_data.get("alert_counts", {})
        hc = acounts.get("critical", 0)
        hh = acounts.get("high", 0)
        hm = acounts.get("medium", 0)
        total_alerts = hc + hh + hm
        signal_sent = health_data.get("signal_sent", False)

        if hc:
            verdict_color = "var(--red)"
            verdict_icon = "🔴"
        elif hh:
            verdict_color = "var(--amber)"
            verdict_icon = "🟠"
        elif hm:
            verdict_color = "#ffff00"
            verdict_icon = "🟡"
        else:
            verdict_color = "var(--green)"
            verdict_icon = "✅"

        # Alert badges
        alert_badges = ""
        if total_alerts:
            alert_badges = f'<span style="margin-left:8px">'
            if hc: alert_badges += f'<span style="background:var(--red);color:#000;padding:1px 6px;border-radius:3px;font-size:0.75rem;margin-right:4px">{hc} CRIT</span>'
            if hh: alert_badges += f'<span style="background:var(--amber);color:#000;padding:1px 6px;border-radius:3px;font-size:0.75rem;margin-right:4px">{hh} HIGH</span>'
            if hm: alert_badges += f'<span style="background:#ffff00;color:#000;padding:1px 6px;border-radius:3px;font-size:0.75rem;margin-right:4px">{hm} MED</span>'
            alert_badges += '</span>'

        signal_badge = f' · <span style="color:var(--green)">📱 Signal sent</span>' if signal_sent else ""

        # Show individual alerts
        alerts_html = ""
        raw_alerts = health_data.get("alerts", [])
        if raw_alerts:
            alert_items = ""
            for a in raw_alerts[:10]:
                sev = a.get("severity", "?")
                cat = e(a.get("category", "?"))
                detail = e(a.get("detail", "")[:200])
                sev_color = "var(--red)" if sev == "CRITICAL" else ("var(--amber)" if sev == "HIGH" else "#ffff00")
                alert_items += f"""<div style="margin:6px 0;padding:8px;background:var(--bg2);border-left:3px solid {sev_color};border-radius:0 4px 4px 0">
                  <span style="color:{sev_color};font-weight:bold;font-size:0.85rem">[{sev}]</span> <span style="color:var(--cyan);font-size:0.85rem">{cat}</span>
                  <div style="font-size:0.82rem;color:var(--fg-dim);margin-top:4px;white-space:pre-wrap">{detail}</div>
                </div>"""
            alerts_html = f"""
<div style="margin-bottom:16px">
  <div style="color:var(--amber);font-weight:bold;margin-bottom:8px">⚠️ ALERTS ({total_alerts})</div>
  {alert_items}
</div>"""

        formatted_health = _format_think_text(health_analysis)
        body += f"""
<div class="section">
  <div class="section-title">{verdict_icon} HEALTH WATCHDOG <span style="color:var(--fg-dim);font-size:0.8rem">// {health_date} · <span style="color:{verdict_color}">{total_alerts} alerts</span>{alert_badges}{signal_badge} · {health_dur:.0f}s GPU</span></div>
  <div class="section-body">
    {alerts_html}
    <div style="white-space:pre-wrap;line-height:1.6;font-size:0.92rem">{formatted_health}</div>
  </div>
</div>"""

    return page_wrap("ADVISOR", body, "advisor")


def _format_think_text(text):
    """Format LLM analysis text for HTML display — handles markdown-ish formatting."""
    import re as _re
    text = e(text)
    # Bold: **text** → <b>text</b>
    text = _re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    # Headers: ## text → colored header
    text = _re.sub(r'^(#{1,3})\s+(.+)$', lambda m: f'<div style="color:var(--green);font-weight:bold;margin-top:16px;font-size:1.05rem">{"━" * len(m.group(1))} {m.group(2)}</div>', text, flags=_re.MULTILINE)
    # Numbered lists: 1. text → styled
    text = _re.sub(r'^(\d+)\.\s+', r'<span style="color:var(--amber);font-weight:bold">\1.</span> ', text, flags=_re.MULTILINE)
    # Bullet points: - text → styled
    text = _re.sub(r'^[-•]\s+', '<span style="color:var(--cyan)">▸</span> ', text, flags=_re.MULTILINE)
    # Emojis in section headers — keep them
    return text


# ─── Page: Career Intelligence (career.html) ───

def gen_careers():
    """Generate the career intelligence / job scanner page."""
    career_dir = os.path.join(DATA_DIR, "career")
    scan_path = os.path.join(career_dir, "latest-scan.json")

    if not os.path.exists(scan_path):
        body = """
<div class="section">
  <div class="section-title">🎯 CAREER INTELLIGENCE</div>
  <div class="section-body" style="text-align:center;padding:40px;color:var(--fg-dim)">
    <div style="font-size:3rem;margin-bottom:16px">🎯</div>
    <div style="font-size:1.1rem;color:var(--amber)">No career scan data yet</div>
    <div style="margin-top:12px;color:var(--fg-dim);font-size:0.9rem">
      career-scan.py scans company career pages, job boards, and intel sites.<br>
      Scheduled Mon/Thu at 11:00. First scan results will appear here.
    </div>
  </div>
</div>"""
        return page_wrap("CAREERS", body, "career")

    scan = load_json(scan_path) or {}
    meta = scan.get("meta", {})
    jobs = scan.get("jobs", [])
    intel = scan.get("intel", {})
    summary_text = scan.get("summary", "")
    sources = scan.get("source_results", {})

    hot_jobs = [j for j in jobs if j.get("match_score", 0) >= 70]
    good_jobs = [j for j in jobs if 40 <= j.get("match_score", 0) < 70]
    remote_jobs = [j for j in jobs if j.get("remote_compatible", False)]

    scan_ts = format_dual_timestamps(meta)
    duration = meta.get("duration_seconds", 0)
    mode_label = "FULL" if meta.get("mode") == "full" else "QUICK"

    # ─ Scan overview section ─
    overview = f"""
<div class="section">
  <div class="section-title">🎯 CAREER INTELLIGENCE &nbsp;<span style="color:var(--fg-dim);font-size:0.8rem">// {scan_ts} ({mode_label}, {duration}s)</span></div>
  <div class="section-body">
    <div style="display:flex;gap:32px;flex-wrap:wrap;font-size:0.9rem">
      <div><span style="color:var(--green);font-size:1.8rem;font-weight:bold">{len(jobs)}</span><br><span style="color:var(--fg-dim)">matches found</span></div>
      <div><span style="color:var(--red);font-size:1.8rem;font-weight:bold">{len(hot_jobs)}</span><br><span style="color:var(--fg-dim)">hot (≥70%)</span></div>
      <div><span style="color:var(--amber);font-size:1.8rem;font-weight:bold">{len(good_jobs)}</span><br><span style="color:var(--fg-dim)">good (40-69%)</span></div>
      <div><span style="color:var(--cyan);font-size:1.8rem;font-weight:bold">{len(remote_jobs)}</span><br><span style="color:var(--fg-dim)">remote-ok</span></div>
      <div><span style="color:var(--purple);font-size:1.8rem;font-weight:bold">{meta.get('companies_scanned', 0)}</span><br><span style="color:var(--fg-dim)">companies</span></div>
      <div><span style="color:var(--blue);font-size:1.8rem;font-weight:bold">{meta.get('pages_fetched', 0)}</span><br><span style="color:var(--fg-dim)">pages fetched</span></div>
    </div>
  </div>
</div>"""

    # ─ LLM Summary section ─
    summary_html = ""
    if summary_text:
        summary_content = e(summary_text).replace("\n", "<br>")
        summary_html = f"""
<div class="section">
  <div class="section-title">🤖 AI BRIEFING</div>
  <div class="section-body">
    <div style="font-family:monospace;white-space:pre-wrap;line-height:1.6;font-size:0.88rem;color:var(--fg)">{summary_content}</div>
  </div>
</div>"""

    # ─ Hot matches section ─
    hot_html = ""
    if hot_jobs:
        hot_cards = ""
        for j in sorted(hot_jobs, key=lambda x: -x.get("match_score", 0)):
            score = j.get("match_score", 0)
            title = e(j.get("title", "Unknown"))
            company = e(j.get("company", "?"))
            location = e(j.get("remote_details") or j.get("location", "?"))
            remote = "✅ Remote OK" if j.get("remote_compatible") else "❌ On-site"
            # Salary: prefer new structured fields, fall back to salary_hint
            b2b = j.get("salary_b2b_net_pln")
            uop = j.get("salary_uop_gross_pln")
            sal_src = j.get("salary_source", "")
            sal_note = j.get("salary_note", "")
            if b2b or uop:
                sal_parts = []
                if b2b:
                    sal_parts.append(f"B2B net: {b2b}")
                if uop:
                    sal_parts.append(f"UoP gross: {uop}")
                zus = j.get("salary_has_zus_akup")
                if zus is True:
                    sal_parts.append("+ ZUS/akup")
                elif zus is False:
                    sal_parts.append("no akup")
                src_label = {"from_offer": "📋 from offer", "estimated": "📊 estimated", "not_possible": "❓"}.get(sal_src, "")
                salary = e(" | ".join(sal_parts))
                salary_badge = f' <span style="font-size:0.75rem;color:var(--purple)">[{e(src_label)}]</span>' if src_label else ""
            else:
                salary = e(j.get("salary_hint", "—"))
                salary_badge = ""
            sw_badge = ' <span style="color:var(--purple);font-size:0.75rem">[SW House]</span>' if j.get("via_software_house") else ""
            reasons = ", ".join(j.get("match_reasons", [])[:4])
            reqs = ", ".join(j.get("key_requirements", [])[:5])
            flags = ", ".join(j.get("red_flags", []))
            job_url = j.get("job_url", "")

            score_color = "var(--green)" if score >= 85 else "var(--amber)" if score >= 70 else "var(--fg)"
            flag_html = f'<div style="color:var(--red);margin-top:4px">⚠ {e(flags)}</div>' if flags else ""
            remote_feas = j.get("remote_feasibility", "")
            feas_html = f'<div style="color:var(--amber);margin-top:4px;font-size:0.8rem">🌍 {e(remote_feas)}</div>' if remote_feas else ""
            title_html = f'<a href="{e(job_url)}" target="_blank" style="color:var(--cyan);font-weight:bold;margin-left:12px;font-size:1rem;text-decoration:none" title="Open job posting">{title} ↗</a>' if job_url else f'<span style="color:var(--cyan);font-weight:bold;margin-left:12px;font-size:1rem">{title}</span>'

            hot_cards += f"""
    <div style="background:var(--bg3);border:1px solid var(--border-bright);border-radius:6px;padding:12px;margin-bottom:10px">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div>
          <span style="color:{score_color};font-weight:bold;font-size:1.3rem">{score}%</span>
          {title_html}
          <span style="color:var(--fg-dim);margin-left:8px">@ {company}{sw_badge}</span>
        </div>
        <div style="font-size:0.82rem;text-align:right">
          <div style="color:var(--fg-dim)">📍 {location}</div>
          <div>{remote}</div>
        </div>
      </div>
      <div style="margin-top:8px;font-size:0.82rem;color:var(--fg-dim)">
        <div>✅ <span style="color:var(--green)">{e(reasons)}</span></div>
        <div style="margin-top:2px">📋 {e(reqs)}</div>
        <div style="margin-top:2px">💰 {salary}{salary_badge}</div>
        {flag_html}
        {feas_html}
      </div>
    </div>"""

        hot_html = f"""
<div class="section">
  <div class="section-title">🔥 HOT MATCHES <span style="color:var(--red)">({len(hot_jobs)} jobs, score ≥ 70%)</span></div>
  <div class="section-body">{hot_cards}</div>
</div>"""

    # ─ Good matches table ─
    good_html = ""
    if good_jobs:
        rows = ""
        for j in sorted(good_jobs, key=lambda x: -x.get("match_score", 0))[:20]:
            score = j.get("match_score", 0)
            sc = "var(--amber)" if score >= 55 else "var(--fg-dim)"
            remote_icon = "✅" if j.get("remote_compatible") else "❌"
            reasons_short = ", ".join(j.get("match_reasons", [])[:2])
            b2b_g = j.get("salary_b2b_net_pln", "")
            sal_src_g = j.get("salary_source", "")
            sal_short = e(b2b_g) if b2b_g else e(j.get("salary_hint", "—"))
            sal_src_icon = {"from_offer": "📋", "estimated": "📊", "not_possible": "❓"}.get(sal_src_g, "")
            sw_icon = "🏢" if j.get("via_software_house") else ""
            job_url_g = j.get("job_url", "")
            title_g = e(j.get('title', '?'))
            title_cell = f'<a href="{e(job_url_g)}" target="_blank" style="color:var(--cyan);text-decoration:none">{title_g} ↗</a>' if job_url_g else f'<span style="color:var(--cyan)">{title_g}</span>'
            rows += f"""<tr>
  <td style="color:{sc};font-weight:bold">{score}%</td>
  <td>{title_cell}</td>
  <td>{e(j.get('company', '?'))} {sw_icon}</td>
  <td>{e(j.get('remote_details') or j.get('location', '?'))}</td>
  <td>{remote_icon}</td>
  <td style="color:var(--green);font-size:0.8rem">{sal_short} {sal_src_icon}</td>
  <td style="color:var(--fg-dim);font-size:0.8rem">{e(reasons_short)}</td>
</tr>"""

        good_html = f"""
<div class="section">
  <div class="section-title">📊 GOOD MATCHES <span style="color:var(--amber)">({len(good_jobs)} jobs, score 40-69%)</span></div>
  <div class="section-body">
    <table class="host-table"><thead><tr>
      <th>Score</th><th>Title</th><th>Company</th><th>Location</th><th>Remote</th><th>Salary (B2B net)</th><th>Match</th>
    </tr></thead><tbody>{rows}</tbody></table>
  </div>
</div>"""

    # ─ Company intel section ─
    intel_html = ""
    if intel and isinstance(intel, dict):
        alerts = intel.get("alerts", [])
        benchmarks = intel.get("salary_benchmarks", [])
        mood = intel.get("market_mood", "")

        alert_cards = ""
        for a in alerts:
            sev = a.get("severity", "info")
            sev_color = {"urgent": "var(--red)", "notable": "var(--amber)"}.get(sev, "var(--fg-dim)")
            sev_icon = {"urgent": "🚨", "notable": "📢"}.get(sev, "ℹ️")
            alert_cards += f"""
    <div style="border-left:3px solid {sev_color};padding:6px 12px;margin-bottom:8px;background:var(--bg3)">
      <div>{sev_icon} <span style="color:{sev_color};font-weight:bold">{e(a.get('company', '?'))}</span>
        — <span style="color:var(--fg)">{e(a.get('summary', ''))}</span>
        <span style="color:var(--fg-dim);font-size:0.8rem;margin-left:8px">[{e(a.get('type', '?'))}]</span>
      </div>
      <div style="font-size:0.82rem;color:var(--fg-dim);margin-top:2px">{e(a.get('details', ''))}</div>
    </div>"""

        bench_rows = ""
        for b in benchmarks[:10]:
            bench_rows += f"<tr><td>{e(b.get('role', '?'))}</td><td style='color:var(--green)'>{e(b.get('range', '?'))}</td><td style='color:var(--fg-dim)'>{e(b.get('source', '?'))}</td></tr>"

        bench_html = ""
        if bench_rows:
            bench_html = f"""
    <div style="margin-top:16px">
      <div style="color:var(--amber);font-weight:bold;margin-bottom:6px">💰 SALARY BENCHMARKS</div>
      <table class="host-table"><thead><tr><th>Role</th><th>Range</th><th>Source</th></tr></thead>
      <tbody>{bench_rows}</tbody></table>
    </div>"""

        mood_html = f'<div style="margin-top:12px;font-size:0.88rem;color:var(--fg);padding:8px;background:var(--bg2);border-radius:4px">{e(mood)}</div>' if mood else ""

        intel_html = f"""
<div class="section">
  <div class="section-title">🕵️ COMPANY INTELLIGENCE</div>
  <div class="section-body">
    {alert_cards}
    {bench_html}
    {mood_html}
  </div>
</div>"""

    # ─ Deep company intel section (from company-intel.py) ─
    deep_intel_path = os.path.join(DATA_DIR, "intel", "latest-intel.json")
    deep_intel = load_json(deep_intel_path) if os.path.exists(deep_intel_path) else None
    deep_intel_html = ""
    if deep_intel and isinstance(deep_intel, dict):
        companies_data = deep_intel.get("companies", [])
        di_ts = format_dual_timestamps(deep_intel.get("meta", {}))
        company_cards = ""
        for comp in companies_data:
            if comp.get("error"):
                continue
            cname = comp.get("name", "?")
            analysis = comp.get("analysis", {})
            sentiment = analysis.get("sentiment", "?")
            score = analysis.get("sentiment_score", "?")
            adas_rel = analysis.get("adas_relevance", "?")
            recommendation = analysis.get("recommendation", "")
            community = analysis.get("community_pulse", "")

            sent_color = {
                "positive": "var(--green)", "negative": "var(--red)",
                "mixed": "var(--amber)", "neutral": "var(--fg-dim)",
            }.get(sentiment, "var(--fg-dim)")

            # Collect source links across all data types
            source_links = ""
            for item in comp.get("news", [])[:3]:
                url = item.get("url", "")
                title = item.get("title", "news")[:60]
                if url:
                    source_links += f' <a href="{e(url)}" target="_blank" style="color:var(--cyan);font-size:0.75rem;text-decoration:none" title="{e(title)}">📰</a>'
            for item in comp.get("reddit", [])[:2]:
                url = item.get("url", "")
                sub = item.get("subreddit", "reddit")
                if url:
                    source_links += f' <a href="{e(url)}" target="_blank" style="color:var(--amber);font-size:0.75rem;text-decoration:none" title="{e(sub)}: {e(item.get("title","")[:60])}">💬</a>'
            for item in comp.get("hackernews", [])[:2]:
                url = item.get("url", "")
                if url:
                    source_links += f' <a href="{e(url)}" target="_blank" style="color:var(--green);font-size:0.75rem;text-decoration:none" title="HN: {e(item.get("title","")[:60])}">🟠</a>'
            for item in comp.get("4programmers", [])[:2]:
                url = item.get("url", "")
                if url:
                    source_links += f' <a href="{e(url)}" target="_blank" style="color:var(--purple);font-size:0.75rem;text-decoration:none" title="4p: {e(item.get("title","")[:60])}">🇵🇱</a>'
            for item in comp.get("semiwiki", [])[:1]:
                url = item.get("url", "")
                if url:
                    source_links += f' <a href="{e(url)}" target="_blank" style="color:var(--blue);font-size:0.75rem;text-decoration:none" title="SemiWiki: {e(item.get("title","")[:60])}">🔬</a>'

            # Red flags and growth signals
            flags = analysis.get("red_flags", [])
            signals = analysis.get("growth_signals", [])
            flag_html = f'<span style="color:var(--red);font-size:0.78rem"> ⚠ {e(", ".join(flags[:2]))}</span>' if flags else ""
            signal_html = f'<span style="color:var(--green);font-size:0.78rem"> 📈 {e(", ".join(signals[:2]))}</span>' if signals else ""

            # Careers found
            careers = comp.get("careers_openings", [])
            careers_html = ""
            if careers:
                career_items = ""
                for cj in careers[:5]:
                    cj_url = cj.get("url", "")
                    cj_title = e(cj.get("title", "?")[:80])
                    cj_loc = e(cj.get("location", ""))
                    if cj_url:
                        career_items += f'<div style="margin-left:12px;font-size:0.78rem"><a href="{e(cj_url)}" target="_blank" style="color:var(--cyan);text-decoration:none">↗ {cj_title}</a> <span style="color:var(--fg-dim)">{cj_loc}</span></div>'
                    else:
                        career_items += f'<div style="margin-left:12px;font-size:0.78rem;color:var(--fg-dim)">{cj_title} — {cj_loc}</div>'
                careers_html = f'<div style="margin-top:4px">{career_items}</div>'

            company_cards += f"""
    <div style="border-left:3px solid {sent_color};padding:6px 12px;margin-bottom:10px;background:var(--bg3)">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div>
          <span style="color:var(--cyan);font-weight:bold">{e(cname)}</span>
          <span style="color:{sent_color};margin-left:8px;font-size:0.82rem">{e(str(sentiment))} ({score})</span>
          <span style="color:var(--purple);margin-left:8px;font-size:0.78rem">ADAS:{adas_rel}/10</span>
          {source_links}
        </div>
        <div style="font-size:0.75rem;color:var(--fg-dim)">{len(careers)} openings</div>
      </div>
      <div style="font-size:0.8rem;margin-top:4px">
        {flag_html}{signal_html}
      </div>
      <div style="font-size:0.8rem;color:var(--fg-dim);margin-top:2px">{e(recommendation)}</div>
      <div style="font-size:0.78rem;color:var(--fg-dim);margin-top:2px;font-style:italic">{e(community)}</div>
      {careers_html}
    </div>"""

        # HN Who is Hiring section
        hn_hiring = deep_intel.get("hn_who_is_hiring", {})
        hn_jobs = hn_hiring.get("matching_jobs", [])
        hn_url = hn_hiring.get("thread_url", "")
        hn_html = ""
        if hn_jobs:
            hn_items = ""
            for hj in hn_jobs[:8]:
                hj_url = hj.get("url", "")
                hj_company = e(hj.get("company", "?")[:60])
                hj_remote = "🌍" if hj.get("is_remote") else ""
                hj_eu = "🇪🇺" if hj.get("is_europe") else ""
                hj_rel = hj.get("relevance_score", 0)
                hj_text = e(hj.get("text", "")[:120])
                link_html = f'<a href="{e(hj_url)}" target="_blank" style="color:var(--cyan);text-decoration:none">{hj_company} ↗</a>' if hj_url else hj_company
                hn_items += f'<div style="font-size:0.8rem;margin-bottom:4px">{link_html} {hj_remote}{hj_eu} <span style="color:var(--purple)">rel:{hj_rel}</span> <span style="color:var(--fg-dim)">— {hj_text}</span></div>'

            thread_link = f'<a href="{e(hn_url)}" target="_blank" style="color:var(--amber);text-decoration:none;font-size:0.8rem">full thread ↗</a>' if hn_url else ""
            hn_html = f"""
    <div style="margin-top:16px;border-top:1px solid var(--border);padding-top:12px">
      <div style="color:var(--amber);font-weight:bold;margin-bottom:6px">🟠 HN "Who is Hiring" — {len(hn_jobs)} relevant matches {thread_link}</div>
      {hn_items}
    </div>"""

        if company_cards:
            deep_intel_html = f"""
<div class="section">
  <div class="section-title">🏢 DEEP COMPANY INTELLIGENCE <span style="color:var(--fg-dim);font-size:0.8rem">// {e(di_ts)}</span></div>
  <div class="section-body">
    {company_cards}
    {hn_html}
  </div>
</div>"""

    # ─ Source status section ─
    source_rows = ""
    for label, results in [("Career Pages", sources.get("career_pages", {})),
                           ("Job Boards", sources.get("job_boards", {})),
                           ("Intel Sources", sources.get("intel_sources", {}))]:
        for sid, res in results.items():
            status = res.get("status", "?")
            st_color = {"ok": "var(--green)", "error": "var(--red)", "no_keywords": "var(--fg-dim)", "insufficient": "var(--amber)"}.get(status, "var(--fg)")
            source_rows += f"""<tr>
  <td style="color:var(--cyan)">{e(label)}</td>
  <td>{e(sid)}</td>
  <td style="color:{st_color}">{e(status)}</td>
  <td style="color:var(--fg-dim)">{res.get('chars', '—')}</td>
  <td>{res.get('jobs_found', '—')}</td>
</tr>"""

    source_html = f"""
<div class="section">
  <div class="section-title">📡 SCAN SOURCES</div>
  <div class="section-body">
    <table class="host-table"><thead><tr>
      <th>Category</th><th>Source</th><th>Status</th><th>Chars</th><th>Jobs</th>
    </tr></thead><tbody>{source_rows}</tbody></table>
  </div>
</div>"""

    # ─ Scan history ─
    archives = sorted(
        [f for f in os.listdir(career_dir) if f.startswith("scan-") and f.endswith(".json")],
        reverse=True,
    ) if os.path.isdir(career_dir) else []

    history_rows = ""
    for af in archives[:10]:
        a = load_json(os.path.join(career_dir, af))
        if not a:
            continue
        am = a.get("meta", {})
        history_rows += f"""<tr>
  <td style="color:var(--cyan)">{e(am.get('timestamp', '?')[:16])}</td>
  <td>{e(am.get('mode', '?'))}</td>
  <td style="color:var(--green)">{am.get('total_jobs_found', 0)}</td>
  <td style="color:var(--red)">{am.get('hot_matches', 0)}</td>
  <td>{am.get('pages_fetched', 0)}</td>
  <td style="color:var(--fg-dim)">{am.get('duration_seconds', 0)}s</td>
</tr>"""

    history_html = ""
    if history_rows:
        history_html = f"""
<div class="section">
  <div class="section-title">📜 SCAN HISTORY</div>
  <div class="section-body">
    <table class="host-table"><thead><tr>
      <th>Timestamp</th><th>Mode</th><th>Jobs</th><th>Hot</th><th>Pages</th><th>Duration</th>
    </tr></thead><tbody>{history_rows}</tbody></table>
  </div>
</div>"""

    # ─ Career page health check section ─
    url_health = scan.get("url_health", {})
    health_html = ""
    if url_health:
        health_rows = ""
        # Sort: errors first, then by company name
        sorted_urls = sorted(url_health.items(),
                             key=lambda x: (x[1].get("http_code", 0) == 200,
                                            x[1].get("company", "")))
        for url, h in sorted_urls:
            http_code = h.get("http_code", 0)
            resp_time = h.get("response_time", 0)
            company = h.get("company", "?")
            company_id = h.get("company_id", "?")
            is_sw_house = h.get("software_house", False)
            status = h.get("status", "unknown")

            # Color coding for HTTP status
            if http_code == 200:
                code_color = "var(--green)"
                code_icon = "✅"
            elif http_code == 0:
                code_color = "var(--red)"
                code_icon = "❌"
            elif 300 <= http_code < 400:
                code_color = "var(--amber)"
                code_icon = "↩️"
            else:
                code_color = "var(--red)"
                code_icon = "⚠️"

            # Response time color
            if resp_time > 0 and resp_time < 3:
                time_color = "var(--green)"
            elif resp_time < 8:
                time_color = "var(--amber)"
            else:
                time_color = "var(--red)"

            tier_badge = '<span style="color:var(--purple);font-size:0.75rem;margin-left:4px">[SW House]</span>' if is_sw_house else ""
            short_url = url[:80] + ("..." if len(url) > 80 else "")

            health_rows += f"""<tr>
  <td style="color:var(--cyan)">{e(company)}{tier_badge}</td>
  <td>{code_icon} <span style="color:{code_color};font-weight:bold">{http_code or 'FAIL'}</span></td>
  <td style="color:{time_color}">{f'{resp_time:.1f}s' if resp_time > 0 else '—'}</td>
  <td style="color:var(--fg-dim);font-size:0.78rem" title="{e(url)}">{e(short_url)}</td>
</tr>"""

        # Count stats
        total = len(url_health)
        ok_count = sum(1 for h in url_health.values() if h.get("http_code") == 200)
        fail_count = total - ok_count
        health_color = "var(--green)" if fail_count == 0 else "var(--red)" if fail_count > 2 else "var(--amber)"

        health_html = f"""
<div class="section">
  <div class="section-title">🏥 CAREER PAGE HEALTH CHECK &nbsp;<span style="color:{health_color};font-size:0.85rem">({ok_count}/{total} OK)</span></div>
  <div class="section-body">
    <table class="host-table"><thead><tr>
      <th>Company</th><th>HTTP</th><th>Response</th><th>URL</th>
    </tr></thead><tbody>{health_rows}</tbody></table>
  </div>
</div>"""

    body = overview + summary_html + hot_html + good_html + intel_html + deep_intel_html + health_html + source_html + history_html
    return page_wrap("CAREERS", body, "career")


# ─── Page: GPU Load (load.html) ───

# ── GPU hardware sensor data (from gpu-monitor.py CSVs) ──

GPU_DATA_DIR = os.path.join(DATA_DIR, "gpu")
SYSTEM_OVERHEAD_W = 9
PSU_EFFICIENCY = 0.87
G11_PLN_PER_KWH = 1.30  # PGE Łódź 2026 gross


def _estimate_wall_w(ppt_w):
    return (ppt_w + SYSTEM_OVERHEAD_W) / PSU_EFFICIENCY


def load_gpu_hw_csv(date_str):
    """Load one day's GPU hardware CSV. Returns list of dicts."""
    import csv as csv_mod
    fpath = os.path.join(GPU_DATA_DIR, f"gpu-{date_str}.csv")
    if not os.path.exists(fpath):
        return []
    rows = []
    with open(fpath) as f:
        reader = csv_mod.DictReader(f)
        for r in reader:
            try:
                rows.append({
                    "time": datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S"),
                    "power_w": float(r["power_w"]) if r.get("power_w") else None,
                    "temp_c": float(r["temp_c"]) if r.get("temp_c") else None,
                    "freq_mhz": float(r["freq_mhz"]) if r.get("freq_mhz") else None,
                    "vram_mb": float(r["vram_mb"]) if r.get("vram_mb") else None,
                    "gtt_mb": float(r["gtt_mb"]) if r.get("gtt_mb") else None,
                })
            except (ValueError, KeyError):
                continue
    return rows


def gpu_day_summary(date_str):
    """Calculate daily GPU hardware summary + cost. Returns dict or None."""
    rows = load_gpu_hw_csv(date_str)
    if not rows:
        return None
    powers = [r["power_w"] for r in rows if r["power_w"] is not None]
    temps = [r["temp_c"] for r in rows if r["temp_c"] is not None]
    freqs = [r["freq_mhz"] for r in rows if r["freq_mhz"] is not None]
    if not powers:
        return None
    wall_powers = [_estimate_wall_w(p) for p in powers]
    minutes = len(powers)
    hours = minutes / 60.0
    avg_wall = sum(wall_powers) / len(wall_powers)
    daily_kwh = avg_wall * 24 / 1000
    daily_pln = daily_kwh * G11_PLN_PER_KWH
    return {
        "date": date_str,
        "samples": minutes,
        "hours": hours,
        "ppt_avg": sum(powers) / len(powers),
        "ppt_max": max(powers),
        "ppt_min": min(powers),
        "wall_avg": avg_wall,
        "wall_max": max(wall_powers),
        "temp_avg": sum(temps) / len(temps) if temps else 0,
        "temp_max": max(temps) if temps else 0,
        "freq_avg": sum(freqs) / len(freqs) if freqs else 0,
        "freq_max": max(freqs) if freqs else 0,
        "daily_kwh": daily_kwh,
        "daily_pln": daily_pln,
    }


def gen_power_cost_section():
    """Generate the GPU hardware power & electricity cost HTML section."""
    now = datetime.now()
    today_str = now.strftime("%Y%m%d")

    # Collect up to 14 days of summaries
    day_summaries = []
    for i in range(14):
        ds = (now - timedelta(days=i)).strftime("%Y%m%d")
        s = gpu_day_summary(ds)
        if s:
            day_summaries.append(s)

    if not day_summaries:
        return ""  # no hardware data yet

    today = day_summaries[0] if day_summaries[0]["date"] == today_str else None

    # Averages across all days with data
    avg_daily_kwh = sum(d["daily_kwh"] for d in day_summaries) / len(day_summaries)
    avg_daily_pln = sum(d["daily_pln"] for d in day_summaries) / len(day_summaries)
    avg_wall = sum(d["wall_avg"] for d in day_summaries) / len(day_summaries)
    avg_ppt = sum(d["ppt_avg"] for d in day_summaries) / len(day_summaries)
    max_ppt = max(d["ppt_max"] for d in day_summaries)

    # Current readings from today
    curr_ppt = f"{today['ppt_avg']:.0f}" if today else "—"
    curr_wall = f"{today['wall_avg']:.0f}" if today else "—"
    curr_temp = f"{today['temp_avg']:.0f}" if today else "—"
    curr_freq = f"{today['freq_avg']:.0f}" if today else "—"

    # Stats boxes
    html = f"""
<div class="section">
  <div class="section-title">⚡ POWER &amp; ELECTRICITY COST — G11 tariff (PGE Łódź)</div>
  <div class="section-body">
    <div class="stats-grid">
      <div class="stat-box">
        <div class="stat-val" style="color:var(--amber)">{curr_ppt}</div>
        <div class="stat-label">PPT avg (W)</div>
      </div>
      <div class="stat-box">
        <div class="stat-val" style="color:#ff6b35">{curr_wall}</div>
        <div class="stat-label">est. wall (W)</div>
      </div>
      <div class="stat-box">
        <div class="stat-val" style="color:var(--cyan)">{curr_temp}</div>
        <div class="stat-label">temp avg (°C)</div>
      </div>
      <div class="stat-box">
        <div class="stat-val" style="color:var(--purple)">{curr_freq}</div>
        <div class="stat-label">clock avg (MHz)</div>
      </div>
      <div class="stat-box">
        <div class="stat-val" style="color:var(--green)">{avg_daily_pln:.2f}</div>
        <div class="stat-label">PLN / day</div>
      </div>
      <div class="stat-box">
        <div class="stat-val" style="color:var(--green)">{avg_daily_pln*30:.0f}</div>
        <div class="stat-label">PLN / month</div>
      </div>
    </div>
    <div style="margin-top:10px;font-size:0.82rem;color:var(--fg-dim)">
      G11: {G11_PLN_PER_KWH:.2f} PLN/kWh gross &nbsp;│&nbsp;
      Wall = (PPT + {SYSTEM_OVERHEAD_W}W) / {PSU_EFFICIENCY*100:.0f}% PSU &nbsp;│&nbsp;
      ~{avg_daily_kwh:.2f} kWh/day &nbsp;│&nbsp;
      {avg_daily_pln*365:.0f} PLN/year
    </div>
  </div>
</div>"""

    # Daily cost table (last 14 days)
    html += """
<div class="section">
  <div class="section-title">💰 DAILY ELECTRICITY COST — last 14 days</div>
  <div class="section-body">
    <table class="host-table">
      <thead><tr>
        <th>Date</th><th>Samples</th><th>PPT avg</th><th>PPT max</th>
        <th>Wall est.</th><th>Temp avg</th><th>kWh/day</th><th>PLN/day</th>
      </tr></thead>
      <tbody>"""

    for d in sorted(day_summaries, key=lambda x: x["date"], reverse=True):
        ds_fmt = f"{d['date'][:4]}-{d['date'][4:6]}-{d['date'][6:]}"
        is_today = d["date"] == today_str
        row_style = ' style="background:var(--bg3)"' if is_today else ''
        today_tag = ' ◀' if is_today else ''
        # Color code the PLN
        pln_color = "var(--green)" if d["daily_pln"] < 3 else "var(--amber)" if d["daily_pln"] < 5 else "var(--red)"
        html += f"""<tr{row_style}>
          <td style="color:{'var(--green)' if is_today else 'var(--fg-dim)'}">{ds_fmt}{today_tag}</td>
          <td>{d['samples']} ({d['hours']:.1f}h)</td>
          <td style="color:var(--amber)">{d['ppt_avg']:.1f}W</td>
          <td>{d['ppt_max']:.1f}W</td>
          <td style="color:#ff6b35">{d['wall_avg']:.0f}W</td>
          <td style="color:var(--cyan)">{d['temp_avg']:.1f}°C</td>
          <td>{d['daily_kwh']:.2f}</td>
          <td style="color:{pln_color};font-weight:bold">{d['daily_pln']:.2f} zł</td>
        </tr>"""

    html += """</tbody></table>"""

    # Summary row
    total_pln = sum(d["daily_pln"] for d in day_summaries)
    html += f"""
    <div style="margin-top:12px;display:flex;gap:24px;flex-wrap:wrap;font-size:0.88rem">
      <div><span style="color:var(--fg-dim)">Avg daily:</span>
           <span style="color:var(--green);font-weight:bold">{avg_daily_pln:.2f} PLN</span></div>
      <div><span style="color:var(--fg-dim)">Monthly est:</span>
           <span style="color:var(--green);font-weight:bold">{avg_daily_pln*30:.1f} PLN</span></div>
      <div><span style="color:var(--fg-dim)">Yearly est:</span>
           <span style="color:var(--amber);font-weight:bold">{avg_daily_pln*365:.0f} PLN</span></div>
      <div><span style="color:var(--fg-dim)">Avg wall power:</span>
           <span style="color:#ff6b35">{avg_wall:.0f}W</span></div>
    </div>
  </div>
</div>"""

    # Embedded chart images (if they exist)
    chart_names = [
        ("power", "⚡ Power Consumption"),
        ("temp", "🌡️ Temperature"),
        ("dashboard", "📊 Full Dashboard"),
    ]
    chart_html = ""
    for suffix, label in chart_names:
        # Check for today's chart first, then most recent
        for di in range(14):
            ds = (now - timedelta(days=di)).strftime("%Y%m%d")
            chart_file = f"gpu-{ds}-{suffix}.png"
            chart_path = os.path.join(GPU_DATA_DIR, chart_file)
            if os.path.exists(chart_path):
                with open(chart_path, "rb") as cf:
                    b64 = base64.b64encode(cf.read()).decode("ascii")
                chart_html += f"""
<div class="section">
  <div class="section-title">{label} — {ds[:4]}-{ds[4:6]}-{ds[6:]}</div>
  <div class="section-body" style="text-align:center;padding:8px">
    <img src="data:image/png;base64,{b64}" alt="{e(label)}" style="max-width:100%;height:auto;border-radius:6px;border:1px solid var(--border)">
  </div>
</div>"""
                break  # found most recent chart for this type

    html += chart_html
    return html


def load_gpu_samples(days=14):
    """Load GPU load TSV samples.

    Returns list of (datetime, status, model, script, vram_mb, gpu_mhz, temp_c, throttle).
    Status: 'generating' (GPU active), 'loaded' (model in VRAM, idle), 'idle'.
    Legacy 'busy' rows are re-mapped using gpu_mhz if available.
    """
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
            gpu_mhz = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else 0
            temp_c = int(parts[6]) if len(parts) > 6 and parts[6].isdigit() else 0
            throttle = int(parts[7]) if len(parts) > 7 and parts[7].isdigit() else 0
            # Re-map legacy "busy" using gpu_mhz if available
            if status == "busy":
                if gpu_mhz > 1200:
                    status = "generating"
                else:
                    status = "loaded"
            samples.append((ts, status, model, script, vram, gpu_mhz, temp_c, throttle))
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
    # Helper: "generating" means GPU is actually computing tokens
    def is_active(st):
        return st == "generating"

    def is_model_loaded(st):
        return st in ("generating", "loaded", "busy")

    # Today
    today_samples = [s for s in samples if s[0].strftime("%Y-%m-%d") == today_str]
    today_gen = sum(1 for s in today_samples if is_active(s[1]))
    today_loaded = sum(1 for s in today_samples if is_model_loaded(s[1]))
    today_total = len(today_samples)
    today_pct = (today_gen / today_total * 100) if today_total > 0 else 0
    today_loaded_pct = (today_loaded / today_total * 100) if today_total > 0 else 0
    today_busy_min = today_gen  # 1 sample ≈ 1 minute

    # Last 7 days
    week_cutoff = now - timedelta(days=7)
    week_samples = [s for s in samples if s[0] >= week_cutoff]
    week_gen = sum(1 for s in week_samples if is_active(s[1]))
    week_loaded = sum(1 for s in week_samples if is_model_loaded(s[1]))
    week_total = len(week_samples)
    week_pct = (week_gen / week_total * 100) if week_total > 0 else 0
    week_loaded_pct = (week_loaded / week_total * 100) if week_total > 0 else 0

    # Available capacity estimate: 1440 min/day, show how many more 2-min jobs could fit
    daily_capacity = 1440
    today_idle_min = today_total - today_gen if today_total > 0 else daily_capacity
    avg_job_min = 2  # typical Ollama call ≈ 1-2 minutes
    headroom_jobs = today_idle_min // avg_job_min

    # Per-script breakdown (last 7 days) — count actual generating minutes
    script_minutes = {}
    for s in week_samples:
        if is_active(s[1]) and s[3]:
            script_minutes[s[3]] = script_minutes.get(s[3], 0) + 1

    # ─── Daily utilization for last 14 days ───
    daily_stats = {}
    for s in samples:
        day = s[0].strftime("%Y-%m-%d")
        if day not in daily_stats:
            daily_stats[day] = {"generating": 0, "loaded": 0, "total": 0}
        daily_stats[day]["total"] += 1
        if is_active(s[1]):
            daily_stats[day]["generating"] += 1
        elif is_model_loaded(s[1]):
            daily_stats[day]["loaded"] += 1

    # ─── Hourly heatmap (last 7 days, 24 hours × 7 days) ───
    # Build a grid: rows=hours (0-23), cols=days
    heatmap_days = []
    for i in range(6, -1, -1):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        heatmap_days.append(d)

    heatmap = {}  # (day, hour) → [generating, loaded, total]
    for s in week_samples:
        day = s[0].strftime("%Y-%m-%d")
        hour = s[0].hour
        key = (day, hour)
        if key not in heatmap:
            heatmap[key] = [0, 0, 0]  # generating, loaded, total
        heatmap[key][2] += 1
        if is_active(s[1]):
            heatmap[key][0] += 1
        elif is_model_loaded(s[1]):
            heatmap[key][1] += 1

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

    # ─── Throttle analysis ───
    THROTTLE_BITS = {
        0: "SPL", 1: "FPPT", 2: "SPPT", 3: "SPPT_APU",
        4: "THM_CORE", 5: "THM_GFX", 6: "THM_SOC",
        7: "TDC_VDD", 8: "TDC_SOC", 9: "TDC_GFX",
        10: "EDC_CPU", 11: "EDC_GFX", 12: "PROCHOT",
    }
    THERMAL_MASK = (1 << 4) | (1 << 5) | (1 << 6) | (1 << 12)
    POWER_MASK = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3)

    def decode_thr(val):
        return [name for bit, name in THROTTLE_BITS.items() if val & (1 << bit)]

    # Basic counts
    today_throttle_min = sum(1 for s in today_samples if s[7] > 0)
    week_throttle_min = sum(1 for s in week_samples if s[7] > 0)
    today_thermal_min = sum(1 for s in today_samples if s[7] & THERMAL_MASK)
    today_power_min = sum(1 for s in today_samples if s[7] & POWER_MASK)

    # Throttle % of generating time (the key new metric)
    today_gen_throttled = sum(1 for s in today_samples if is_active(s[1]) and s[7] > 0)
    today_throttle_gen_pct = (today_gen_throttled / today_gen * 100) if today_gen > 0 else 0
    week_gen_throttled = sum(1 for s in week_samples if is_active(s[1]) and s[7] > 0)
    week_throttle_gen_pct = (week_gen_throttled / week_gen * 100) if week_gen > 0 else 0

    # Per-reason breakdown (7 days)
    reason_minutes = {}
    for s in week_samples:
        if s[7] > 0:
            for bit, name in THROTTLE_BITS.items():
                if s[7] & (1 << bit):
                    reason_minutes[name] = reason_minutes.get(name, 0) + 1

    # Daily throttle stats for trend chart
    daily_throttle = {}
    for s in samples:
        day = s[0].strftime("%Y-%m-%d")
        if day not in daily_throttle:
            daily_throttle[day] = {"gen": 0, "gen_thr": 0, "total": 0,
                                   "throttled": 0, "thermal": 0, "power": 0}
        dt = daily_throttle[day]
        dt["total"] += 1
        if s[7] > 0:
            dt["throttled"] += 1
            if s[7] & THERMAL_MASK:
                dt["thermal"] += 1
            if s[7] & POWER_MASK:
                dt["power"] += 1
        if is_active(s[1]):
            dt["gen"] += 1
            if s[7] > 0:
                dt["gen_thr"] += 1

    # Throttle episodes (today)
    today_episodes = []
    _prev_thr = False
    _ep_start = None
    _ep_reasons = set()
    for s in today_samples:
        if s[7] > 0:
            if not _prev_thr:
                _ep_start = s[0]
                _ep_reasons = set(decode_thr(s[7]))
            else:
                _ep_reasons.update(decode_thr(s[7]))
            _prev_thr = True
        else:
            if _prev_thr and _ep_start:
                today_episodes.append((_ep_start, s[0], _ep_reasons))
            _prev_thr = False
            _ep_start = None
            _ep_reasons = set()
    if _prev_thr and _ep_start and today_samples:
        today_episodes.append((_ep_start, today_samples[-1][0], _ep_reasons))

    throttle_color = "var(--red)" if today_throttle_min > 0 else "var(--green)"
    if today_throttle_min > 0:
        throttle_val = f"{today_throttle_gen_pct:.0f}%"
    else:
        throttle_val = "✓"

    # Stats boxes
    stats_html = f"""
<div class="section">
  <div class="section-title">📊 GPU LOAD — Ollama Model Utilization on bc250</div>
  <div class="section-body">
    <div class="stats-grid">
      <div class="stat-box">
        <div class="stat-val {pct_class(today_pct)}">{today_pct:.0f}%</div>
        <div class="stat-label">today generating</div>
      </div>
      <div class="stat-box">
        <div class="stat-val {pct_class(week_pct)}">{week_pct:.0f}%</div>
        <div class="stat-label">7-day generating</div>
      </div>
      <div class="stat-box">
        <div class="stat-val cyan">{today_busy_min}</div>
        <div class="stat-label">gen. min today</div>
      </div>
      <div class="stat-box">
        <div class="stat-val" style="color:var(--fg-dim)">{today_loaded_pct:.0f}%</div>
        <div class="stat-label">model in VRAM</div>
      </div>
      <div class="stat-box">
        <div class="stat-val" style="color:var(--green)">{headroom_jobs}</div>
        <div class="stat-label">est. free slots</div>
      </div>
      <div class="stat-box">
        <div class="stat-val" style="color:{throttle_color}">{throttle_val}</div>
        <div class="stat-label">gen throttled</div>
      </div>
    </div>
    <div style="margin-top:10px;font-size:0.82rem;color:var(--fg-dim)">
      1 sample = 1 minute &nbsp;│&nbsp; generating = GPU clock &gt;1.2 GHz (actual compute) &nbsp;│&nbsp;
      loaded = model in VRAM but GPU idle (keep-alive 30m)
      {f'&nbsp;│&nbsp; <span style="color:var(--red)">⚠ {today_throttle_min}min throttled ({today_gen_throttled}m during gen, thermal: {today_thermal_min}m, power: {today_power_min}m)</span>' if today_throttle_min > 0 else ''}
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
            gen, loaded, total = heatmap[key] if key in heatmap else (0, 0, 0)
            if total == 0:
                # No data (future or not yet sampled)
                bg = "var(--bg)"
                label = "·"
                fg = "var(--fg-dim)"
            elif gen == 0 and loaded == 0:
                # All samples idle (no model)
                bg = "#0d1a0d"
                label = "░"
                fg = "#224422"
            elif gen == 0:
                # Model loaded in VRAM but not computing — dim indicator
                bg = "#141e28"
                label = "▪"
                fg = "#2a4a5a"
            else:
                # Actual generation happened — show minutes
                gen_pct = gen / total * 100
                if gen_pct < 25:
                    bg = "#1a2a1a"
                    fg = "var(--green)"
                elif gen_pct < 50:
                    bg = "#2a3a1a"
                    fg = "var(--green)"
                elif gen_pct < 75:
                    bg = "#3a3a1a"
                    fg = "var(--amber)"
                else:
                    bg = "#3a2a1a"
                    fg = "var(--amber)"
                label = f"{gen}m"
            heatmap_html += f'<td style="padding:2px 4px;text-align:center;background:{bg};color:{fg};border:1px solid var(--border)">{label}</td>'
        heatmap_html += '</tr>'
    heatmap_html += '</table></div>'
    heatmap_html += '<div style="margin-top:8px;font-size:0.78rem;color:var(--fg-dim)">░ idle &nbsp;│&nbsp; ▪ model in VRAM (not computing) &nbsp;│&nbsp; Nm = N minutes generating &nbsp;│&nbsp; · no data</div>'
    heatmap_html += '</div></div>'

    # ─── Daily bar chart ───
    sorted_days = sorted(daily_stats.keys())[-14:]
    bar_html = '<div class="section"><div class="section-title">📈 DAILY UTILIZATION — last 14 days (generating vs loaded)</div><div class="section-body">'
    for day in sorted_days:
        ds = daily_stats[day]
        gen_pct = (ds["generating"] / ds["total"] * 100) if ds["total"] > 0 else 0
        loaded_pct = (ds["loaded"] / ds["total"] * 100) if ds["total"] > 0 else 0
        is_today = day == today_str
        day_label = datetime.strptime(day, "%Y-%m-%d").strftime("%a %d")
        gen_w = max(gen_pct, 0.5) if ds["generating"] > 0 else 0
        loaded_w = max(loaded_pct, 0.5) if ds["loaded"] > 0 else 0
        gen_color = pct_color(gen_pct)
        today_marker = " ◀" if is_today else ""
        bar_html += f'''<div style="display:flex;align-items:center;gap:8px;margin:3px 0;font-size:0.82rem">
  <span style="width:52px;text-align:right;color:{'var(--green)' if is_today else 'var(--fg-dim)'};flex-shrink:0">{day_label}</span>
  <div style="flex:1;height:16px;background:var(--bg);border:1px solid var(--border);position:relative;display:flex">
    <div style="height:100%;width:{gen_w}%;background:{gen_color};opacity:0.8"></div>
    <div style="height:100%;width:{loaded_w}%;background:#1a3a5a;opacity:0.5"></div>
  </div>
  <span style="width:120px;text-align:right;color:var(--fg-dim);flex-shrink:0">{gen_pct:.0f}% gen ({ds['generating']}m) + {ds['loaded']}m loaded{today_marker}</span>
</div>'''
    bar_html += '<div style="margin-top:6px;font-size:0.78rem;color:var(--fg-dim)">■ generating (GPU active) &nbsp;│&nbsp; <span style="color:#2a5a7a">■</span> loaded (model in VRAM, idle)</div>'
    bar_html += '</div></div>'

    # ─── Per-script breakdown ───
    script_html = '<div class="section"><div class="section-title">⚙ PER-SCRIPT BREAKDOWN — last 7 days</div><div class="section-body">'
    if script_minutes:
        total_busy_week = sum(script_minutes.values())
        script_icons = {
            "lore-digest": "📨", "repo-watch": "👁", "idle-think": "🧠",
            "report": "📊", "career-scan": "💼", "salary-tracker": "💰",
            "company-intel": "🏢", "patent-watch": "📜", "event-scout": "🎤",
            "ha-journal": "🏠", "leak-monitor": "🔒", "gateway": "🌐",
            "academic-watch": "🎓", "unknown": "❓",
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
        script_html += f'<div style="margin-top:8px;font-size:0.82rem;color:var(--fg-dim)">Total generating: {total_busy_week} min ({total_busy_week/60:.1f}h) over 7 days</div>'
    else:
        script_html += '<div style="color:var(--fg-dim)">No generating data yet (need samples with GPU clock &gt;1.2 GHz)</div>'
    script_html += '</div></div>'

    # ─── Capacity planning — dynamically read from jobs.json ───
    cron_jobs_path = "/opt/netscan/data/jobs.json"
    cron_jobs_night = []  # (sort_key, name, schedule_str, timeout_str, last_status, last_dur_s)
    cron_jobs_day = []
    cron_total = 0
    cron_enabled = 0
    try:
        with open(cron_jobs_path) as _cjf:
            _cron_data = json.load(_cjf)
        for _cj in _cron_data.get("jobs", []):
            cron_total += 1
            if not _cj.get("enabled", True):
                continue
            cron_enabled += 1
            _name = _cj.get("name", "?")
            _expr = _cj.get("schedule", {}).get("expr", "* * * * *")
            _timeout = _cj.get("payload", {}).get("timeoutSeconds", 0)
            _state = _cj.get("state", {})
            _last_status = _state.get("lastStatus", "—")
            _last_dur = _state.get("lastDurationMs", 0) / 1000

            # Parse cron expression for display
            _parts = _expr.split()
            _min_s, _hour_s = _parts[0], _parts[1]
            if _hour_s == "*":
                _sched_str = f"*:{int(_min_s):02d}"
                _sort_h = 99
            else:
                _h = int(_hour_s)
                _sched_str = f"{_h:02d}:{int(_min_s):02d}"
                _sort_h = _h

            _timeout_min = _timeout // 60
            _timeout_str = f"≤{_timeout_min} min"

            _row = (_sort_h, int(_min_s), _name, _sched_str, _timeout_str, _last_status, _last_dur)
            if _sort_h >= 23 or _sort_h < 8:
                _sk = _sort_h if _sort_h >= 23 else _sort_h + 24
                cron_jobs_night.append((_sk, int(_min_s), _name, _sched_str, _timeout_str, _last_status, _last_dur))
            elif _sort_h == 99:
                cron_jobs_day.append(_row)
            else:
                cron_jobs_day.append(_row)
        cron_jobs_night.sort()
        cron_jobs_day.sort()
    except Exception:
        pass  # no cron data → empty tables

    _n_night = len(cron_jobs_night)
    _n_day = len(cron_jobs_day)

    cap_html = f'<div class="section"><div class="section-title">🔧 QUEUE-RUNNER — {cron_enabled} SCHEDULED JOBS</div><div class="section-body">'
    cap_html += '<div style="margin-bottom:12px;padding:8px;background:var(--bg2);border-left:3px solid var(--purple);border-radius:4px;font-size:0.85rem">'
    cap_html += '🤖 <span style="color:var(--purple);font-weight:bold">Sequential queue-runner</span> '
    cap_html += '<span style="color:var(--fg-dim)">executes scripts directly via subprocess. '
    cap_html += 'LLM-only tasks route through openclaw agent. '
    cap_html += 'Signal preempts background work.</span>'
    cap_html += '</div>'

    def _status_color(st):
        return {"ok": "var(--green)", "error": "var(--red)", "running": "var(--amber)"}.get(st, "var(--fg-dim)")

    def _render_cron_table(jobs, bg_style=""):
        t = '<table class="host-table"><thead><tr>'
        t += '<th>Job</th><th>Time</th><th>Timeout</th><th>Last</th><th>Duration</th>'
        t += '</tr></thead><tbody>'
        for _, _, name, sched, timeout_s, status, dur in jobs:
            dur_str = f"{dur:.0f}s" if dur > 0 else "—"
            st_icon = {"ok": "✓", "error": "✗", "running": "⟳"}.get(status, "—")
            st_color = _status_color(status)
            row_bg = f' style="background:var(--bg3)"' if bg_style else ""
            t += f'<tr{row_bg}><td style="color:var(--cyan)">{e(name)}</td>'
            t += f'<td>{e(sched)}</td><td>{e(timeout_s)}</td>'
            t += f'<td style="color:{st_color}">{st_icon} {e(status)}</td>'
            t += f'<td style="color:var(--fg-dim)">{dur_str}</td></tr>'
        t += '</tbody></table>'
        return t

    # Night table
    cap_html += f'<div style="margin-bottom:6px;color:var(--purple);font-weight:bold;font-size:0.85rem">🌙 Night batch (23:00–07:59) — {_n_night} jobs</div>'
    cap_html += _render_cron_table(cron_jobs_night, bg_style="night")
    # Day table
    cap_html += f'<div style="margin:12px 0 6px;color:var(--amber);font-weight:bold;font-size:0.85rem">☀️ Daytime (08:00–22:59) — {_n_day} jobs</div>'
    cap_html += _render_cron_table(cron_jobs_day)

    # Summary stats
    _night_max_min = sum(int(j[4].replace("≤", "").replace(" min", "")) for j in cron_jobs_night)
    _day_max_min = sum(int(j[4].replace("≤", "").replace(" min", "")) for j in cron_jobs_day)
    _total_max_min = _night_max_min + _day_max_min
    cap_html += f'''<div style="margin-top:12px;font-size:0.85rem">
  <span style="color:var(--fg-dim)">Daily jobs:</span>
  <span style="color:var(--amber)">{cron_enabled}</span>
  <span style="color:var(--fg-dim)">({_n_night} night + {_n_day} day) — max timeout budget:</span>
  <span style="color:var(--amber)">{_total_max_min} min</span>
  <span style="color:var(--fg-dim)">({round(_total_max_min/1440*100)}% of 24h)</span>
  <br>
  <span style="color:var(--fg-dim)">Night timeout budget:</span>
  <span style="color:var(--purple)">{_night_max_min} min</span>
  <span style="color:var(--fg-dim)">of 540 min window</span>
  <br>
  <span style="color:var(--fg-dim)">Orchestration:</span>
  <span style="color:var(--cyan)">queue-runner → direct subprocess / openclaw agent → Ollama</span>
  <br>
  <span style="color:var(--fg-dim)">Preemption:</span>
  <span style="color:var(--green)">Signal messages queue and process after current job</span>
</div>'''
    cap_html += '</div></div>'

    # ─── Recent activity log ───
    recent = samples[-60:]  # last 60 samples
    recent.reverse()
    log_html = '<div class="section"><div class="section-title">📋 RECENT SAMPLES — last 60 minutes</div><div class="section-body">'
    log_html += '<div class="log-view">'
    for s in recent:
        ts, st, model, script, vram = s[0], s[1], s[2], s[3], s[4]
        gpu_mhz = s[5] if len(s) > 5 else 0
        temp_c = s[6] if len(s) > 6 else 0
        throttle = s[7] if len(s) > 7 else 0
        ts_str = ts.strftime("%H:%M")
        hw_info = ""
        if gpu_mhz:
            hw_info = f" {gpu_mhz}MHz"
        if temp_c:
            hw_info += f" {temp_c}°C"
        if throttle:
            thr_reasons = decode_thr(throttle)
            thr_short = ",".join(thr_reasons[:3])
            if len(thr_reasons) > 3:
                thr_short += f"+{len(thr_reasons)-3}"
            hw_info += f' <span style="color:var(--red)" title="throttle=0x{throttle:x} [{", ".join(thr_reasons)}]">⚠{thr_short}</span>'
        if st == "generating":
            vram_str = f" [{vram}MB]" if vram else ""
            script_str = f" ← {script}" if script else ""
            log_html += f'<span class="log-ts">{ts_str}</span> <span style="color:var(--amber)">■ GEN</span> {e(model)}{vram_str}{script_str}{hw_info}\n'
        elif st in ("loaded", "busy"):
            vram_str = f" [{vram}MB]" if vram else ""
            script_str = f" ← {script}" if script and script != "unknown" else ""
            log_html += f'<span class="log-ts">{ts_str}</span> <span style="color:#2a5a7a">▪ loaded</span> {e(model)}{vram_str}{script_str}{hw_info}\n'
        else:
            log_html += f'<span class="log-ts">{ts_str}</span> <span style="color:#224422">□ idle</span>{hw_info}\n'
    log_html += '</div></div></div>'

    # ─── Throttle Analysis section ───
    thr_html = ''
    if week_throttle_min > 0:
        _reason_colors = {
            "SPL": "#f97316", "FPPT": "#fb923c", "SPPT": "#fdba74", "SPPT_APU": "#fed7aa",
            "THM_CORE": "#ef4444", "THM_GFX": "#f87171", "THM_SOC": "#fca5a5",
            "TDC_VDD": "#a78bfa", "TDC_SOC": "#c4b5fd", "TDC_GFX": "#ddd6fe",
            "EDC_CPU": "#67e8f9", "EDC_GFX": "#a5f3fc", "PROCHOT": "#dc2626",
        }
        _reason_icons = {
            "THM_CORE": "🌡️", "THM_GFX": "🌡️", "THM_SOC": "🌡️", "PROCHOT": "🌡️",
            "SPL": "⚡", "FPPT": "⚡", "SPPT": "⚡", "SPPT_APU": "⚡",
            "TDC_VDD": "🔌", "TDC_SOC": "🔌", "TDC_GFX": "🔌",
            "EDC_CPU": "📊", "EDC_GFX": "📊",
        }

        thr_html = '<div class="section"><div class="section-title">🌡️ THROTTLE ANALYSIS — GPU Thermal &amp; Power Limits</div><div class="section-body">'

        # ── Top stats grid ──
        _tg_color = lambda p: "var(--green)" if p == 0 else ("var(--amber)" if p < 20 else "var(--red)")
        thr_html += '<div class="stats-grid">'
        thr_html += f'''<div class="stat-box">
  <div class="stat-val" style="color:{_tg_color(today_throttle_gen_pct)}">{today_throttle_gen_pct:.0f}%</div>
  <div class="stat-label">gen throttled today</div>
</div>'''
        thr_html += f'''<div class="stat-box">
  <div class="stat-val" style="color:{_tg_color(week_throttle_gen_pct)}">{week_throttle_gen_pct:.0f}%</div>
  <div class="stat-label">gen throttled 7d</div>
</div>'''
        thr_html += f'''<div class="stat-box">
  <div class="stat-val" style="color:var(--red)">{today_throttle_min}</div>
  <div class="stat-label">throttle min today</div>
</div>'''
        thr_html += f'''<div class="stat-box">
  <div class="stat-val" style="color:var(--red)">{week_throttle_min}</div>
  <div class="stat-label">throttle min 7d</div>
</div>'''
        thr_html += f'''<div class="stat-box">
  <div class="stat-val" style="color:#ef4444">{today_thermal_min}m</div>
  <div class="stat-label">thermal today</div>
</div>'''
        thr_html += f'''<div class="stat-box">
  <div class="stat-val" style="color:#f97316">{today_power_min}m</div>
  <div class="stat-label">power lim today</div>
</div>'''
        thr_html += '</div>'

        thr_html += f'''<div style="margin-top:10px;font-size:0.82rem;color:var(--fg-dim)">
  "gen throttled" = % of GPU generating time spent in a throttled state &nbsp;│&nbsp;
  throttle bitmask read from <code>gpu_metrics</code> binary blob (offset 108, uint32 LE)
</div>'''

        # ── Per-reason breakdown (7 days) ──
        if reason_minutes:
            _max_rm = max(reason_minutes.values())
            thr_html += '<div style="margin-top:16px"><div style="font-weight:bold;color:var(--fg-dim);margin-bottom:8px;font-size:0.85rem">📊 PER-REASON BREAKDOWN — last 7 days</div>'
            for reason, mins in sorted(reason_minutes.items(), key=lambda x: -x[1]):
                _bar_w = (mins / _max_rm * 100) if _max_rm > 0 else 0
                _color = _reason_colors.get(reason, "var(--red)")
                _icon = _reason_icons.get(reason, "⚠")
                _pct_of_total = mins * 100 / week_total if week_total > 0 else 0
                thr_html += f'''<div style="display:flex;align-items:center;gap:8px;margin:3px 0;font-size:0.82rem">
  <span style="width:110px;color:var(--fg);flex-shrink:0">{_icon} {reason}</span>
  <div style="flex:1;height:14px;background:var(--bg);border:1px solid var(--border)">
    <div style="height:100%;width:{_bar_w}%;background:{_color};opacity:0.7"></div>
  </div>
  <span style="width:90px;text-align:right;color:var(--fg-dim);flex-shrink:0">{mins}m ({_pct_of_total:.1f}%)</span>
</div>'''
            thr_html += '<div style="margin-top:4px;font-size:0.78rem;color:var(--fg-dim)">🌡️ thermal &nbsp;│&nbsp; ⚡ power limit &nbsp;│&nbsp; 🔌 current draw (TDC) &nbsp;│&nbsp; 📊 electrical design (EDC)</div>'
            thr_html += '</div>'

        # ── Daily throttle trend (14 days) ──
        thr_html += '<div style="margin-top:16px"><div style="font-weight:bold;color:var(--fg-dim);margin-bottom:8px;font-size:0.85rem">📈 DAILY THROTTLE TREND — last 14 days</div>'
        for day in sorted(daily_stats.keys())[-14:]:
            _dt = daily_throttle.get(day, {"gen": 0, "gen_thr": 0, "total": 0,
                                           "throttled": 0, "thermal": 0, "power": 0})
            _gen_thr_pct = (_dt["gen_thr"] / _dt["gen"] * 100) if _dt["gen"] > 0 else 0
            _thr_pct = (_dt["throttled"] / _dt["total"] * 100) if _dt["total"] > 0 else 0
            _therm_w = (_dt["thermal"] / _dt["total"] * 100) if _dt["total"] > 0 else 0
            _pwr_w = (_dt["power"] / _dt["total"] * 100) if _dt["total"] > 0 else 0
            _dlabel = datetime.strptime(day, "%Y-%m-%d").strftime("%a %d")
            _is_today = day == today_str
            if _dt["throttled"] > 0:
                thr_html += f'''<div style="display:flex;align-items:center;gap:8px;margin:3px 0;font-size:0.82rem">
  <span style="width:52px;text-align:right;color:{'var(--red)' if _is_today else 'var(--fg-dim)'};flex-shrink:0">{_dlabel}</span>
  <div style="flex:1;height:16px;background:var(--bg);border:1px solid var(--border);display:flex">
    <div style="height:100%;width:{_therm_w}%;background:#ef4444;opacity:0.7" title="thermal {_dt['thermal']}m"></div>
    <div style="height:100%;width:{_pwr_w}%;background:#f97316;opacity:0.7" title="power {_dt['power']}m"></div>
  </div>
  <span style="width:160px;text-align:right;color:var(--fg-dim);flex-shrink:0">{_dt['throttled']}m ({_thr_pct:.0f}%) gen:{_gen_thr_pct:.0f}%{'  ◀' if _is_today else ''}</span>
</div>'''
            else:
                thr_html += f'''<div style="display:flex;align-items:center;gap:8px;margin:3px 0;font-size:0.82rem">
  <span style="width:52px;text-align:right;color:{'var(--green)' if _is_today else 'var(--fg-dim)'};flex-shrink:0">{_dlabel}</span>
  <div style="flex:1;height:16px;background:var(--bg);border:1px solid var(--border)"></div>
  <span style="width:160px;text-align:right;color:var(--green);flex-shrink:0">✓ clean{'  ◀' if _is_today else ''}</span>
</div>'''
        thr_html += '<div style="margin-top:6px;font-size:0.78rem;color:var(--fg-dim)"><span style="color:#ef4444">■</span> thermal &nbsp;│&nbsp; <span style="color:#f97316">■</span> power limit &nbsp;│&nbsp; gen% = % of generating time throttled</div>'
        thr_html += '</div>'

        # ── Throttle heatmap (7 days × 24 hours, showing throttle minutes per cell) ──
        thr_heatmap = {}
        for s in week_samples:
            _d = s[0].strftime("%Y-%m-%d")
            _h = s[0].hour
            _k = (_d, _h)
            if _k not in thr_heatmap:
                thr_heatmap[_k] = [0, 0, 0]  # thermal, power, total_throttled
            if s[7] > 0:
                thr_heatmap[_k][2] += 1
                if s[7] & THERMAL_MASK:
                    thr_heatmap[_k][0] += 1
                if s[7] & POWER_MASK:
                    thr_heatmap[_k][1] += 1

        _has_hm_data = any(v[2] > 0 for v in thr_heatmap.values())
        if _has_hm_data:
            thr_html += '<div style="margin-top:16px"><div style="font-weight:bold;color:var(--fg-dim);margin-bottom:8px;font-size:0.85rem">🗓 THROTTLE HEATMAP — last 7 days</div>'
            thr_html += '<div style="overflow-x:auto"><table style="border-collapse:collapse;font-size:0.75rem;width:100%">'
            thr_html += '<tr><td style="padding:2px 6px;color:var(--fg-dim)">Hour</td>'
            for dl in [(datetime.strptime(d, "%Y-%m-%d")).strftime("%a %d") for d in heatmap_days]:
                thr_html += f'<td style="padding:2px 4px;color:var(--fg-dim);text-align:center;min-width:60px">{dl}</td>'
            thr_html += '</tr>'
            for h in range(24):
                thr_html += f'<tr><td style="padding:2px 6px;color:var(--fg-dim);text-align:right">{h:02d}:00</td>'
                for d in heatmap_days:
                    _k = (d, h)
                    _therm, _pwr, _tot = thr_heatmap[_k] if _k in thr_heatmap else (0, 0, 0)
                    if _tot == 0:
                        _bg = "var(--bg)"
                        _lbl = "·"
                        _fg = "#224422"
                    elif _therm > 0 and _pwr > 0:
                        _bg = "#3a1a1a"
                        _lbl = f"{_tot}m"
                        _fg = "#ef4444"
                    elif _therm > 0:
                        _bg = "#2a1a1a"
                        _lbl = f"{_tot}m"
                        _fg = "#f87171"
                    elif _pwr > 0:
                        _bg = "#2a1a0a"
                        _lbl = f"{_tot}m"
                        _fg = "#f97316"
                    else:
                        _bg = "#1a1a1a"
                        _lbl = f"{_tot}m"
                        _fg = "var(--amber)"
                    thr_html += f'<td style="padding:2px 4px;text-align:center;background:{_bg};color:{_fg};border:1px solid var(--border)">{_lbl}</td>'
                thr_html += '</tr>'
            thr_html += '</table></div>'
            thr_html += '<div style="margin-top:6px;font-size:0.78rem;color:var(--fg-dim)">· no throttle &nbsp;│&nbsp; <span style="color:#f87171">Nm</span> thermal &nbsp;│&nbsp; <span style="color:#f97316">Nm</span> power &nbsp;│&nbsp; <span style="color:#ef4444">Nm</span> both</div>'
            thr_html += '</div>'

        # ── Throttle episodes table (today) ──
        if today_episodes:
            thr_html += '<div style="margin-top:16px"><div style="font-weight:bold;color:var(--fg-dim);margin-bottom:8px;font-size:0.85rem">⚡ THROTTLE EPISODES TODAY — ' + str(len(today_episodes)) + ' episodes</div>'
            thr_html += '<table class="host-table"><thead><tr><th>Start</th><th>End</th><th>Duration</th><th>Type</th><th>Reasons</th></tr></thead><tbody>'
            for _ep_s, _ep_e, _ep_r in today_episodes:
                _dur = (_ep_e - _ep_s).total_seconds() / 60
                _has_th = any(r in ("THM_CORE", "THM_GFX", "THM_SOC", "PROCHOT") for r in _ep_r)
                _has_pw = any(r in ("SPL", "FPPT", "SPPT", "SPPT_APU") for r in _ep_r)
                if _has_th and _has_pw:
                    _etype = '<span style="color:#ef4444">🌡️⚡ both</span>'
                elif _has_th:
                    _etype = '<span style="color:#ef4444">🌡️ thermal</span>'
                elif _has_pw:
                    _etype = '<span style="color:#f97316">⚡ power</span>'
                else:
                    _etype = '<span style="color:var(--amber)">⚠ other</span>'
                _dur_s = f"{_dur:.0f}m" if _dur >= 1 else "&lt;1m"
                _reasons_s = ", ".join(sorted(_ep_r))
                thr_html += f'<tr><td>{_ep_s.strftime("%H:%M")}</td><td>{_ep_e.strftime("%H:%M")}</td><td style="color:var(--red)">{_dur_s}</td><td>{_etype}</td><td style="font-size:0.8rem;color:var(--fg-dim)">{_reasons_s}</td></tr>'
            thr_html += '</tbody></table></div>'

        thr_html += '</div></div>'

    power_html = gen_power_cost_section()

    body = stats_html + heatmap_html + bar_html + thr_html + power_html + script_html + cap_html + log_html
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


# ── Weather page ───────────────────────────────────────────────────────────

def gen_weather():
    """Generate the weather forecast & air quality page."""
    latest = os.path.join(DATA_DIR, "latest-weather.json")
    if os.path.islink(latest):
        data = load_json(latest)
    else:
        # Try finding most recent weather file
        wfiles = sorted(Path(DATA_DIR).glob("weather-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        data = load_json(str(wfiles[0])) if wfiles else None

    if not data:
        body = """
<div class="section">
  <div class="section-title">🌤️ WEATHER FORECAST</div>
  <div class="section-body" style="text-align:center;padding:40px;color:var(--fg-dim)">
    <div style="font-size:3rem;margin-bottom:16px">🌤️</div>
    <div style="font-size:1.1rem;color:var(--amber)">No weather data yet</div>
    <div style="margin-top:12px;font-size:0.9rem">
      weather-watch.py fetches OpenMeteo forecasts, air quality data,<br>
      and correlates with Home Assistant indoor sensors.<br>
      First data will appear after the scheduled scrape runs.
    </div>
  </div>
</div>"""
        return page_wrap("WEATHER", body, "weather")

    meta = data.get("meta", {})
    forecast = data.get("forecast", {})
    air_quality = data.get("air_quality", {})
    ha_sensors = data.get("ha_sensors", {})
    analysis = data.get("analysis", "")

    scan_ts = meta.get("scrape_timestamp", "")[:16]
    analyze_ts = meta.get("analyze_timestamp", "")[:16]
    lat = meta.get("latitude", "?")
    lon = meta.get("longitude", "?")

    # ─ Current conditions ─
    temp_data = forecast.get("temperature", [])
    humid_data = forecast.get("humidity", [])
    wind_data = forecast.get("wind_speed", [])
    precip_data = forecast.get("precipitation", [])

    current_temp = temp_data[0] if temp_data else "?"
    current_humid = humid_data[0] if humid_data else "?"
    current_wind = wind_data[0] if wind_data else "?"

    # Min/max from forecast
    temp_min = min(temp_data) if temp_data else "?"
    temp_max = max(temp_data) if temp_data else "?"

    overview = f"""
<div class="section">
  <div class="section-title">🌤️ WEATHER FORECAST — Łódź &nbsp;
    <span style="color:var(--fg-dim);font-size:0.8rem">// {e(scan_ts)} &nbsp; {lat}°N {lon}°E</span>
  </div>
  <div class="section-body">
    <div style="display:flex;gap:32px;flex-wrap:wrap;font-size:0.9rem">
      <div><span style="color:var(--cyan);font-size:1.8rem;font-weight:bold">{current_temp}°C</span><br><span style="color:var(--fg-dim)">now</span></div>
      <div><span style="color:var(--blue);font-size:1.8rem;font-weight:bold">{temp_min}°</span> / <span style="color:var(--red);font-size:1.8rem;font-weight:bold">{temp_max}°</span><br><span style="color:var(--fg-dim)">min / max</span></div>
      <div><span style="color:var(--green);font-size:1.8rem;font-weight:bold">{current_humid}%</span><br><span style="color:var(--fg-dim)">humidity</span></div>
      <div><span style="color:var(--amber);font-size:1.8rem;font-weight:bold">{current_wind}</span><br><span style="color:var(--fg-dim)">wind km/h</span></div>
    </div>
  </div>
</div>"""

    # ─ Air quality ─
    aqi = air_quality.get("european_aqi", [])
    pm25 = air_quality.get("pm2_5", [])
    pm10 = air_quality.get("pm10", [])
    current_aqi = aqi[0] if aqi else "?"
    current_pm25 = pm25[0] if pm25 else "?"
    current_pm10 = pm10[0] if pm10 else "?"

    aqi_color = "var(--green)" if isinstance(current_aqi, (int, float)) and current_aqi <= 25 else "var(--amber)" if isinstance(current_aqi, (int, float)) and current_aqi <= 50 else "var(--red)"

    aq_html = f"""
<div class="section">
  <div class="section-title">▸ 🌬️ AIR QUALITY</div>
  <div class="section-body">
    <div style="display:flex;gap:32px;flex-wrap:wrap;font-size:0.9rem">
      <div><span style="color:{aqi_color};font-size:1.8rem;font-weight:bold">{current_aqi}</span><br><span style="color:var(--fg-dim)">EU AQI</span></div>
      <div><span style="color:var(--amber);font-size:1.8rem;font-weight:bold">{current_pm25}</span><br><span style="color:var(--fg-dim)">PM2.5 µg/m³</span></div>
      <div><span style="color:var(--amber);font-size:1.8rem;font-weight:bold">{current_pm10}</span><br><span style="color:var(--fg-dim)">PM10 µg/m³</span></div>
    </div>
  </div>
</div>"""

    # ─ HA indoor sensors ─
    ha_html = ""
    if ha_sensors:
        rows = ""
        for sensor_name, readings in ha_sensors.items():
            if isinstance(readings, list) and readings:
                latest_val = readings[-1] if readings else "?"
                avg_val = sum(r for r in readings if isinstance(r, (int, float))) / max(len([r for r in readings if isinstance(r, (int, float))]), 1)
                rows += f"""<tr>
                  <td style="color:var(--cyan)">{e(sensor_name)}</td>
                  <td style="font-weight:bold">{latest_val}</td>
                  <td style="color:var(--fg-dim)">{avg_val:.1f}</td>
                  <td style="color:var(--fg-dim)">{len(readings)} pts</td>
                </tr>"""
            elif isinstance(readings, dict):
                val = readings.get("value", readings.get("state", "?"))
                rows += f"""<tr>
                  <td style="color:var(--cyan)">{e(sensor_name)}</td>
                  <td style="font-weight:bold">{val}</td>
                  <td colspan="2" style="color:var(--fg-dim)">—</td>
                </tr>"""

        if rows:
            ha_html = f"""
<div class="section">
  <div class="section-title">▸ 🏠 INDOOR SENSORS (Home Assistant)</div>
  <div class="section-body">
    <table class="host-table">
      <thead><tr><th>Sensor</th><th>Latest</th><th>Avg</th><th>Points</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</div>"""

    # ─ AI Analysis ─
    analysis_html = ""
    if analysis:
        lines = ""
        for line in analysis.strip().split("\n"):
            line_esc = e(line)
            if line.strip() and (line.strip().startswith("#") or line.strip().endswith(":")):
                lines += f'<div style="color:var(--amber);font-weight:bold;margin-top:10px">{line_esc}</div>\n'
            elif line.strip().startswith("- ") or line.strip().startswith("• "):
                lines += f'<div style="padding-left:16px;color:var(--fg)">{line_esc}</div>\n'
            else:
                lines += f'<div style="color:var(--fg-dim)">{line_esc}</div>\n'
        analysis_html = f"""
<div class="section">
  <div class="section-title">▸ 🤖 AI WEATHER ANALYSIS &nbsp;<span style="color:var(--fg-dim);font-size:0.8rem">// {e(analyze_ts)}</span></div>
  <div class="section-body" style="font-size:0.88rem;line-height:1.6">{lines}</div>
</div>"""

    body = overview + aq_html + ha_html + analysis_html
    return page_wrap("WEATHER", body, "weather")


# ── News page ──────────────────────────────────────────────────────────────

def gen_news():
    """Generate the news digest page."""
    latest = os.path.join(DATA_DIR, "latest-news.json")
    if os.path.islink(latest):
        data = load_json(latest)
    else:
        nfiles = sorted(Path(DATA_DIR).glob("news-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        data = load_json(str(nfiles[0])) if nfiles else None

    if not data:
        body = """
<div class="section">
  <div class="section-title">📰 NEWS DIGEST</div>
  <div class="section-body" style="text-align:center;padding:40px;color:var(--fg-dim)">
    <div style="font-size:3rem;margin-bottom:16px">📰</div>
    <div style="font-size:1.1rem;color:var(--amber)">No news data yet</div>
    <div style="margin-top:12px;font-size:0.9rem">
      news-watch.py aggregates RSS feeds from tech news sources<br>
      with LLM-powered filtering for automotive, embedded, and Linux topics.<br>
      First digest will appear after the scheduled scrape.
    </div>
  </div>
</div>"""
        return page_wrap("NEWS", body, "news")

    meta = data.get("meta", {})
    articles = data.get("articles", [])
    digest = data.get("digest", "")
    feed_stats = data.get("feed_stats", {})

    scan_ts = meta.get("scrape_timestamp", "")[:16]
    analyze_ts = meta.get("analyze_timestamp", "")[:16]

    # ─ Overview ─
    n_articles = len(articles)
    n_feeds = len(feed_stats) if feed_stats else meta.get("feeds_scraped", 0)
    n_relevant = sum(1 for a in articles if a.get("relevance_score", 0) >= 50)

    overview = f"""
<div class="section">
  <div class="section-title">📰 NEWS DIGEST &nbsp;
    <span style="color:var(--fg-dim);font-size:0.8rem">// {e(scan_ts)}</span>
  </div>
  <div class="section-body">
    <div style="display:flex;gap:32px;flex-wrap:wrap;font-size:0.9rem">
      <div><span style="color:var(--cyan);font-size:1.8rem;font-weight:bold">{n_articles}</span><br><span style="color:var(--fg-dim)">articles</span></div>
      <div><span style="color:var(--green);font-size:1.8rem;font-weight:bold">{n_relevant}</span><br><span style="color:var(--fg-dim)">relevant</span></div>
      <div><span style="color:var(--purple);font-size:1.8rem;font-weight:bold">{n_feeds}</span><br><span style="color:var(--fg-dim)">feeds</span></div>
    </div>
  </div>
</div>"""

    # ─ AI Digest ─
    digest_html = ""
    if digest:
        lines = ""
        for line in digest.strip().split("\n"):
            line_esc = e(line)
            if line.strip() and (line.strip().startswith("#") or line.strip().endswith(":")):
                lines += f'<div style="color:var(--amber);font-weight:bold;margin-top:10px">{line_esc}</div>\n'
            elif line.strip().startswith("- ") or line.strip().startswith("• "):
                lines += f'<div style="padding-left:16px;color:var(--fg)">{line_esc}</div>\n'
            else:
                lines += f'<div style="color:var(--fg-dim)">{line_esc}</div>\n'
        digest_html = f"""
<div class="section">
  <div class="section-title">▸ 🤖 AI NEWS DIGEST &nbsp;<span style="color:var(--fg-dim);font-size:0.8rem">// {e(analyze_ts)}</span></div>
  <div class="section-body" style="font-size:0.88rem;line-height:1.6">{lines}</div>
</div>"""

    # ─ Feed stats ─
    feed_html = ""
    if feed_stats:
        rows = ""
        for feed_name, stats in sorted(feed_stats.items(), key=lambda x: -x[1].get("articles", 0)):
            count = stats.get("articles", 0)
            status = stats.get("status", "ok")
            status_icon = "✓" if status == "ok" else "⚠" if status == "partial" else "✗"
            status_color = "var(--green)" if status == "ok" else "var(--amber)" if status == "partial" else "var(--red)"
            rows += f"""<tr>
              <td style="color:{status_color}">{status_icon}</td>
              <td style="color:var(--cyan)">{e(feed_name)}</td>
              <td style="font-weight:bold">{count}</td>
              <td style="color:var(--fg-dim)">{e(status)}</td>
            </tr>"""
        feed_html = f"""
<div class="section">
  <div class="section-title">▸ 📡 FEED STATUS</div>
  <div class="section-body">
    <table class="host-table">
      <thead><tr><th></th><th>Feed</th><th>Articles</th><th>Status</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</div>"""

    # ─ Articles list ─
    article_cards = ""
    for a in articles[:30]:
        title = e(a.get("title", "Untitled"))[:120]
        source = e(a.get("source", "?"))
        url = a.get("url", "")
        score = a.get("relevance_score", 0)
        pub_date = a.get("published", "")[:10]
        summary = e(a.get("summary", "")[:200])

        score_color = "var(--green)" if score >= 70 else "var(--amber)" if score >= 40 else "var(--fg-dim)"
        title_link = f'<a href="{e(url)}" target="_blank" style="color:var(--cyan);text-decoration:none">{title} ↗</a>' if url else title
        summary_div = f'<div style="margin-top:4px;color:var(--fg-dim);font-size:0.82rem">{summary}</div>' if summary else ""

        article_cards += f"""
    <div style="background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:10px;margin-bottom:6px">
      <div style="display:flex;justify-content:space-between;align-items:flex-start">
        <div style="flex:1">
          <span style="color:{score_color};font-weight:bold">{score}</span>
          <span style="margin-left:8px">{title_link}</span>
          <div style="margin-top:3px;font-size:0.8rem;color:var(--fg-dim)">
            {source} &nbsp;│&nbsp; {pub_date}
          </div>
          {summary_div}
        </div>
      </div>
    </div>"""

    articles_html = f"""
<div class="section">
  <div class="section-title">▸ 📋 ARTICLES</div>
  <div class="section-body">{article_cards if article_cards else '<div style="color:var(--fg-dim)">No articles found</div>'}</div>
</div>"""

    body = overview + digest_html + feed_html + articles_html
    return page_wrap("NEWS", body, "news")


# ── Health page ────────────────────────────────────────────────────────────

def gen_health():
    """Generate the system health assessment page."""
    latest = os.path.join(DATA_DIR, "latest-health.json")
    if os.path.islink(latest):
        data = load_json(latest)
    else:
        hfiles = sorted(Path(DATA_DIR).glob("health-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        data = load_json(str(hfiles[0])) if hfiles else None

    if not data:
        body = """
<div class="section">
  <div class="section-title">🏥 SYSTEM HEALTH</div>
  <div class="section-body" style="text-align:center;padding:40px;color:var(--fg-dim)">
    <div style="font-size:3rem;margin-bottom:16px">🏥</div>
    <div style="font-size:1.1rem;color:var(--amber)">No health report yet</div>
    <div style="margin-top:12px;font-size:0.9rem">
      Run bc250-extended-health.py to generate a comprehensive health assessment.
    </div>
  </div>
</div>"""
        return page_wrap("HEALTH", body, "health")

    ts = data.get("timestamp", "")[:16]
    services = data.get("services", {})
    data_freshness = data.get("data_freshness", {})
    dashboard = data.get("dashboard_freshness", {})
    chinese = data.get("chinese_contamination", {})
    queue = data.get("queue_runner", {})
    assessment = data.get("llm_assessment", "")

    # ─ Services ─
    svc_rows = ""
    for svc, info in services.items():
        if svc == "ollama_loaded":
            continue
        if isinstance(info, dict):
            status = info.get("status", "?")
        else:
            status = str(info)
        icon = "✓" if status in ("OK", "active") else "⚠" if status == "NO_MODEL" else "✗"
        color = "var(--green)" if status in ("OK", "active") else "var(--amber)" if status == "NO_MODEL" else "var(--red)"
        svc_rows += f'<tr><td style="color:{color}">{icon}</td><td style="color:var(--cyan)">{e(svc)}</td><td style="color:{color}">{e(status)}</td></tr>'

    svc_html = f"""
<div class="section">
  <div class="section-title">🏥 SYSTEM HEALTH &nbsp;<span style="color:var(--fg-dim);font-size:0.8rem">// {e(ts)}</span></div>
  <div class="section-body">
    <table class="host-table">
      <thead><tr><th></th><th>Service</th><th>Status</th></tr></thead>
      <tbody>{svc_rows}</tbody>
    </table>
  </div>
</div>"""

    # ─ Data freshness ─
    ok_cnt = sum(1 for v in data_freshness.values() if v.get("status") == "OK")
    stale_cnt = sum(1 for v in data_freshness.values() if v.get("status") == "STALE")
    miss_cnt = sum(1 for v in data_freshness.values() if v.get("status") == "MISSING")

    df_rows = ""
    for name, info in sorted(data_freshness.items(), key=lambda x: x[1].get("status", "Z")):
        status = info.get("status", "?")
        age = info.get("age_hours", -1)
        mx = info.get("max_hours", 0)
        icon = "✓" if status == "OK" else "⚠" if status == "STALE" else "✗"
        color = "var(--green)" if status == "OK" else "var(--amber)" if status == "STALE" else "var(--red)"
        age_str = f"{age:.0f}h" if age >= 0 else "—"
        df_rows += f'<tr><td style="color:{color}">{icon}</td><td style="color:var(--cyan)">{e(name)}</td><td style="color:{color}">{age_str}</td><td style="color:var(--fg-dim)">{mx}h max</td><td style="color:{color}">{e(status)}</td></tr>'

    df_html = f"""
<div class="section">
  <div class="section-title">▸ 📊 DATA FRESHNESS &nbsp;<span style="color:var(--fg-dim);font-size:0.8rem">({ok_cnt} OK / {stale_cnt} stale / {miss_cnt} missing)</span></div>
  <div class="section-body">
    <table class="host-table">
      <thead><tr><th></th><th>Source</th><th>Age</th><th>Max</th><th>Status</th></tr></thead>
      <tbody>{df_rows}</tbody>
    </table>
  </div>
</div>"""

    # ─ Chinese contamination ─
    ch_clean = chinese.get("clean", 0)
    ch_dirty = chinese.get("contaminated", 0)
    ch_color = "var(--green)" if ch_dirty == 0 else "var(--red)"
    ch_html = f"""
<div class="section">
  <div class="section-title">▸ 🈲 LLM OUTPUT QUALITY</div>
  <div class="section-body">
    <div style="display:flex;gap:32px;flex-wrap:wrap;font-size:0.9rem">
      <div><span style="color:var(--green);font-size:1.8rem;font-weight:bold">{ch_clean}</span><br><span style="color:var(--fg-dim)">clean notes</span></div>
      <div><span style="color:{ch_color};font-size:1.8rem;font-weight:bold">{ch_dirty}</span><br><span style="color:var(--fg-dim)">contaminated</span></div>
    </div>
  </div>
</div>"""

    # ─ Queue runner ─
    qr_total = queue.get("total", 0)
    qr_ok = queue.get("recent_ok", 0)
    qr_stale = queue.get("stale", 0)
    qr_never = queue.get("never_run", 0)
    qr_html = f"""
<div class="section">
  <div class="section-title">▸ ⚙️ QUEUE RUNNER JOBS</div>
  <div class="section-body">
    <div style="display:flex;gap:32px;flex-wrap:wrap;font-size:0.9rem">
      <div><span style="color:var(--cyan);font-size:1.8rem;font-weight:bold">{qr_total}</span><br><span style="color:var(--fg-dim)">total</span></div>
      <div><span style="color:var(--green);font-size:1.8rem;font-weight:bold">{qr_ok}</span><br><span style="color:var(--fg-dim)">recent OK</span></div>
      <div><span style="color:var(--amber);font-size:1.8rem;font-weight:bold">{qr_stale}</span><br><span style="color:var(--fg-dim)">stale</span></div>
      <div><span style="color:var(--red);font-size:1.8rem;font-weight:bold">{qr_never}</span><br><span style="color:var(--fg-dim)">never run</span></div>
    </div>
  </div>
</div>"""

    # ─ LLM Assessment ─
    assess_html = ""
    if assessment:
        lines = ""
        for line in assessment.strip().split("\n"):
            line_esc = e(line)
            if line.strip().startswith("##"):
                lines += f'<div style="color:var(--amber);font-weight:bold;margin-top:12px;font-size:1.05rem">{line_esc}</div>\n'
            elif line.strip().startswith("#"):
                lines += f'<div style="color:var(--amber);font-weight:bold;margin-top:10px">{line_esc}</div>\n'
            elif line.strip().startswith("- ") or line.strip().startswith("• "):
                lines += f'<div style="padding-left:16px;color:var(--fg)">{line_esc}</div>\n'
            else:
                lines += f'<div style="color:var(--fg-dim)">{line_esc}</div>\n'
        assess_html = f"""
<div class="section">
  <div class="section-title">▸ 🤖 AI HEALTH ASSESSMENT</div>
  <div class="section-body" style="font-size:0.88rem;line-height:1.6">{lines}</div>
</div>"""

    body = svc_html + df_html + ch_html + qr_html + assess_html
    return page_wrap("HEALTH", body, "health")


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
        "home.html": gen_home,
        "notes.html": gen_notes,
        "academic.html": gen_academic,
        "radio.html": gen_radio,
        "events.html": gen_events,
        "career.html": gen_careers,
        "car.html": gen_car_tracker,
        "advisor.html": gen_advisor,
        "load.html": gen_load,
        "leaks.html": gen_leaks,
        "weather.html": gen_weather,
        "news.html": gen_news,
        "health.html": gen_health,
    }
    # Dynamic feed pages from digest-feeds.json
    for fid, fcfg in DIGEST_FEEDS.items():
        slug = fcfg.get("page_slug", fid)
        pages[f"{slug}.html"] = (lambda _fid=fid, _fcfg=fcfg: gen_feed_page(_fid, _fcfg))
    # Issues page (only if repo feeds configured)
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
