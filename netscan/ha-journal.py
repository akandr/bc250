#!/usr/bin/env python3
"""ha-journal.py — ClawdBot Home Assistant journal entry generator.

Collects current HA sensor data via ha-observe.py, feeds it to the local
Ollama LLM for analytical commentary, and saves the result as a "home"
note in the idle-think notes system (DATA_DIR/think/).

This gives ClawdBot a persistent, visible record of home observations
that appears on the dashboard Notes page alongside research/trend notes.

Usage:
    ha-journal.py                 (full analysis — climate + anomalies + rooms)
    ha-journal.py --quick         (climate-only snapshot, shorter prompt)

Schedule (cron):
    30 1,7,13,19 * * *  /usr/bin/python3 /opt/netscan/ha-journal.py

Location on bc250: /opt/netscan/ha-journal.py
"""

import argparse
import json
import re
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from llm_sanitize import sanitize_llm_output

# ─── Config ───

DATA_DIR = "/opt/netscan/data"
THINK_DIR = os.path.join(DATA_DIR, "think")
HA_OBSERVE = "/opt/netscan/ha-observe.py"

OLLAMA_URL = "http://localhost:11434"
OLLAMA_CHAT = f"{OLLAMA_URL}/api/chat"
OLLAMA_MODEL = "qwen3:14b"  # consolidated model for all batch scripts

QUIET_START = 0   # 00:00
QUIET_END   = 6   # 06:00 — no chat, GPU free for batch jobs

def is_quiet_hours():
    return QUIET_START <= datetime.now().hour < QUIET_END

HA_JOURNAL_DIR = os.path.join(DATA_DIR, "ha-journal")
RAW_HA_FILE = os.path.join(HA_JOURNAL_DIR, "raw-ha-data.json")

os.makedirs(THINK_DIR, exist_ok=True)
os.makedirs(HA_JOURNAL_DIR, exist_ok=True)

# Load HA credentials from openclaw .env
ENV_FILE = os.path.expanduser("~/.openclaw/.env")
if os.path.exists(ENV_FILE):
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

HASS_URL = os.environ.get("HASS_URL", "http://homeassistant:8123")
HASS_TOKEN = os.environ.get("HASS_TOKEN", "")

# Room pattern → room name
ROOM_PATTERNS = {
    "salon": "Salon", "sypialnia": "Sypialnia", "kuchni|kuchen": "Kuchnia",
    "jadalni": "Jadalnia", "łazienk|lazienk": "Łazienka", "piętro|pietro": "Piętro",
    "parter": "Parter", "piwnic": "Piwnica", "garaż|garaz": "Garaż",
    "komputerow": "Biuro", "chłopaki|chlopaki|dziecięc": "Chłopcy",
    "wiatrołap|wiatolap": "Wiatrołap", "spiżarni|spizarni": "Spiżarnia",
    "warsztat": "Warsztat", "przedpokoj|przedpokój": "Przedpokój",
    "ogród|ogrod": "Ogród",
}

# Switches to skip for room occupancy (not human-activated)
SKIP_SWITCHES = ["brama", "garaz", "gate", "wentylator", "fan",
                 "pompa", "auto", "pralka", "suszarka", "drzewo",
                 "termometr", "gniazdko"]


def guess_room(entity_id, friendly_name=""):
    text = f"{entity_id} {friendly_name}".lower()
    for pattern, room in ROOM_PATTERNS.items():
        if re.search(pattern, text, re.IGNORECASE):
            return room
    return "Other"


def ha_api_get(path):
    """GET from HA REST API."""
    url = f"{HASS_URL}{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {HASS_TOKEN}",
        "Content-Type": "application/json",
    })
    try:
        resp = urllib.request.urlopen(req, timeout=20)
        return json.loads(resp.read())
    except Exception as ex:
        print(f"  HA API error: {ex}")
        return None


def get_switch_activity(hours=6):
    """Get recent switch activity and compute room usage summary.

    Returns (room_summary_text, timeline_text) for the LLM prompt.
    """
    if not HASS_TOKEN:
        return "", ""

    # Get all current states to discover switches
    states = ha_api_get("/api/states")
    if not states:
        return "", ""

    switch_eids = []
    switch_info = {}  # eid → {fname, room}
    for e in states:
        eid = e["entity_id"]
        s = e["state"]
        if not eid.startswith(("switch.", "light.")):
            continue
        if s not in ("on", "off"):
            continue
        fname = e.get("attributes", {}).get("friendly_name", "")
        if any(p in eid.lower() or p in fname.lower() for p in SKIP_SWITCHES):
            continue
        switch_eids.append(eid)
        switch_info[eid] = {"fname": fname, "room": guess_room(eid, fname)}

    if not switch_eids:
        return "", ""

    # Fetch history in batches
    start = datetime.now(timezone.utc) - timedelta(hours=hours)
    start_str = start.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    all_history = {}
    BATCH = 30
    for i in range(0, len(switch_eids), BATCH):
        batch = switch_eids[i:i + BATCH]
        ids_str = ",".join(batch)
        data = ha_api_get(
            f"/api/history/period/{start_str}"
            f"?filter_entity_id={ids_str}&minimal_response&no_attributes"
        )
        if data:
            for entity_hist in data:
                if entity_hist:
                    eid = entity_hist[0].get("entity_id", "")
                    all_history[eid] = entity_hist
        time.sleep(0.3)

    # Compute room usage
    room_lit_min = defaultdict(float)   # room → total minutes lit
    room_events = defaultdict(list)     # room → [(time_str, switch, ON/OFF)]
    now = datetime.now(timezone.utc)

    for eid, hist in all_history.items():
        info = switch_info.get(eid)
        if not info:
            continue
        room = info["room"]
        fname = info["fname"]
        prev_ts = None
        prev_on = False

        for p in hist:
            s = p.get("state", "").lower()
            ts_str = p.get("last_changed", "")
            if s not in ("on", "off"):
                continue
            is_on = s == "on"
            try:
                ts = datetime.fromisoformat(ts_str.replace("+00:00", "").replace("Z", ""))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except Exception:
                continue

            if ts < start:
                prev_ts = ts
                prev_on = is_on
                continue

            # Accumulate on-duration
            if prev_ts and prev_on:
                delta = max(0, (ts - max(prev_ts, start)).total_seconds() / 60)
                room_lit_min[room] += delta

            if is_on:
                room_events[room].append((ts.strftime("%H:%M"), fname, "ON"))
            else:
                room_events[room].append((ts.strftime("%H:%M"), fname, "OFF"))

            prev_ts = ts
            prev_on = is_on

        # Still on at end
        if prev_on and prev_ts:
            delta = max(0, (now - max(prev_ts, start)).total_seconds() / 60)
            room_lit_min[room] += delta

    # Build room summary
    room_lines = []
    for room in sorted(room_lit_min, key=room_lit_min.get, reverse=True):
        mins = room_lit_min[room]
        hrs = mins / 60
        events = room_events.get(room, [])
        n_on = sum(1 for _, _, e in events if e == "ON")
        if mins < 1 and n_on == 0:
            continue
        room_lines.append(f"  {room}: {hrs:.1f}h lit ({mins:.0f}min), {n_on} switch-on events")
        # Show which switches
        switches_used = sorted(set(s for _, s, _ in events))
        if switches_used:
            room_lines.append(f"    switches: {', '.join(switches_used)}")

    room_summary = "\n".join(room_lines) if room_lines else "No room activity detected"

    # Build timeline (last 20 events)
    all_events = []
    for room, evts in room_events.items():
        for t, sw, ev in evts:
            all_events.append((t, room, sw, ev))
    all_events.sort(key=lambda x: x[0])

    timeline_lines = []
    for t, room, sw, ev in all_events[-20:]:
        icon = "🟢" if ev == "ON" else "⚫"
        timeline_lines.append(f"  {icon} {t} {room}: {sw} {ev}")

    timeline = "\n".join(timeline_lines) if timeline_lines else "No switch events"

    return room_summary, timeline


# ─── Helpers ───

def run_ha(command, *args):
    """Run ha-observe.py with a subcommand and return stdout."""
    cmd = ["python3", HA_OBSERVE, command] + list(args)
    env = os.environ.copy()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, env=env
        )
        return result.stdout.strip()
    except Exception as ex:
        return f"[error: {ex}]"


def call_ollama(system_prompt, user_prompt, temperature=0.4, max_tokens=2000):
    """Call local Ollama for analysis."""
    # Health check
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        models = [m["name"] for m in data.get("models", [])]
        if OLLAMA_MODEL not in models:
            print(f"  Model {OLLAMA_MODEL} not found in Ollama")
            return None
    except Exception as ex:
        print(f"  Ollama not reachable: {ex}")
        return None

    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "/nothink\n" + user_prompt},
        ],
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": 24576},
        "keep_alive": "5m",
    })

    try:
        req = urllib.request.Request(
            OLLAMA_CHAT,
            data=payload.encode(),
            headers={"Content-Type": "application/json"},
        )
        t0 = time.time()
        resp = urllib.request.urlopen(req, timeout=600)
        result = json.loads(resp.read())
        elapsed = time.time() - t0
        content = result.get("message", {}).get("content", "")
        tokens = result.get("eval_count", 0)
        tps = tokens / elapsed if elapsed > 0 else 0
        print(f"  Ollama OK: {elapsed:.0f}s, {tokens} tok ({tps:.1f} t/s)")
        return sanitize_llm_output(content)
    except Exception as ex:
        print(f"  Ollama failed: {ex}")
        return None


def save_note(title, content, context=None):
    """Save a home-type note in the think system."""
    dt = datetime.now()
    note = {
        "type": "home",
        "title": title,
        "content": content,
        "generated": dt.isoformat(timespec="seconds"),
        "model": OLLAMA_MODEL,
        "context": context or {},
    }
    fname = f"note-home-{dt.strftime('%Y%m%d-%H%M')}.json"
    path = os.path.join(THINK_DIR, fname)
    with open(path, "w") as f:
        json.dump(note, f, indent=2)
    print(f"  Saved: {path}")

    # Update notes index
    index_path = os.path.join(THINK_DIR, "notes-index.json")
    index = []
    if os.path.exists(index_path):
        try:
            with open(index_path) as f:
                index = json.load(f)
        except Exception:
            pass

    index.insert(0, {
        "file": fname,
        "type": "home",
        "title": title,
        "generated": note["generated"],
        "chars": len(content),
    })
    index = index[:50]  # keep last 50 notes
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)


def load_previous_home_note():
    """Load the most recent 'home' note content for comparison."""
    index_path = os.path.join(THINK_DIR, "notes-index.json")
    if not os.path.exists(index_path):
        return None
    try:
        with open(index_path) as f:
            index = json.load(f)
        for entry in index:
            if entry.get("type") == "home":
                path = os.path.join(THINK_DIR, entry["file"])
                if os.path.exists(path):
                    with open(path) as f:
                        note = json.load(f)
                    return {
                        "generated": entry.get("generated", "?"),
                        "content": note.get("content", ""),
                    }
    except Exception:
        pass
    return None


def load_latest_insights():
    """Load latest home-insights (from ha-correlate) for cross-reference."""
    index_path = os.path.join(THINK_DIR, "notes-index.json")
    if not os.path.exists(index_path):
        return None
    try:
        with open(index_path) as f:
            index = json.load(f)
        for entry in index:
            if entry.get("type") == "home-insights":
                path = os.path.join(THINK_DIR, entry["file"])
                if os.path.exists(path):
                    with open(path) as f:
                        note = json.load(f)
                    return {
                        "generated": entry.get("generated", "?"),
                        "content": note.get("content", "")[:600],
                    }
    except Exception:
        pass
    return None


# ─── Main ───

SYSTEM_PROMPT = """\
You are ClawdBot, an AI home observer for a family house in Poland.
Your job: analyze Home Assistant sensor data AND room activity patterns
to write an insightful journal entry about the state of the home.

Your analysis MUST cover:
1. **Room Activity**: Which rooms are/were used, for how long. What does the switch
   timeline tell about household routine? Who's home, who's out?
2. **Climate**: Temperature across rooms, outdoor conditions, heating effectiveness
3. **Air Quality**: CO₂, PM2.5, VOC — flag anything above thresholds
4. **Environmental Change**: What changed since previous observation?
5. **Correlations**: Connect the dots — e.g. "kitchen lights on for 2h + CO₂ rising = cooking"

Rules:
- Be analytical and specific. No filler, no greetings.
- Use emoji for quick scanning: 🌡️ temp, 💨 air, 💡 lights, 🔴 warning, ✅ normal
- Flag anything unusual: high CO₂, open windows when cold, lights left on, temp anomalies
- Estimate room occupancy from light activity (lights on = room in use)
- Compare with previous observation — note what changed and WHY
- Include time context (night vs. day, season-appropriate behavior)
- Air quality thresholds: CO₂ >1000=concerning >1500=bad, PM2.5 >25=moderate >50=poor, VOC >0.5=elevated
- Write in English, translate Polish sensor names
- End with 2-3 specific, actionable recommendations
- Keep it under 500 words

Device context (do NOT flag these as anomalies):
- Garaż termometr (garage heater): turns on automatically for dehumidification
- Kuchnia ledy góra (kitchen upper LEDs): always on — physical wall buttons
- Łazienka piwnica-Wentylator: automated safety ventilation for gas heater
- Łazienka piętro-Wentylator: always on — humidity-triggered
"""


def run_scrape(quick=False):
    """Collect HA sensor data via ha-observe.py. Save raw JSON (no LLM)."""
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

    dt = datetime.now()
    mode_str = 'quick' if quick else 'full'
    print(f"[{dt:%Y-%m-%d %H:%M:%S}] ha-journal scrape starting ({mode_str})")

    t0 = time.time()

    # Collect HA data
    print("  Collecting HA data...")
    climate_data = run_ha("climate")
    rooms_data = run_ha("rooms") if not quick else ""
    anomalies_data = run_ha("anomalies") if not quick else ""
    lights_data = run_ha("lights")

    # Get switch activity and room usage (last 6h window)
    print("  Fetching switch activity...")
    room_activity, switch_timeline = get_switch_activity(hours=6)

    if not climate_data or climate_data.startswith("[error"):
        print(f"  Failed to get HA data: {climate_data}")
        return

    scrape_duration = int(time.time() - t0)
    raw_data = {
        "scrape_timestamp": dt.isoformat(timespec="seconds"),
        "scrape_duration_seconds": scrape_duration,
        "scrape_version": 1,
        "data": {
            "quick": quick,
            "climate_data": climate_data,
            "rooms_data": rooms_data,
            "anomalies_data": anomalies_data,
            "lights_data": lights_data,
            "room_activity": room_activity,
            "switch_timeline": switch_timeline,
        },
        "scrape_errors": [],
    }

    tmp_path = RAW_HA_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(raw_data, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, RAW_HA_FILE)

    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] ha-journal scrape done ({scrape_duration}s)")


def run_analyze():
    """Load raw HA data, run LLM analysis, save think note."""
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

    dt = datetime.now()
    quiet = is_quiet_hours()
    print(f"[{dt:%Y-%m-%d %H:%M:%S}] ha-journal analyze starting ({'QUIET HOURS' if quiet else 'daytime'})")

    # Guard: don't compete for GPU with other batch scripts
    for proc_name in ["lore-digest.sh", "repo-watch.sh", "idle-think.sh"]:
        try:
            result = subprocess.run(
                ["pgrep", "-f", proc_name],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                print(f"  {proc_name} is running — skipping")
                return
        except Exception:
            pass

    # GPU guard — during quiet hours (00-06) we own the GPU, skip guard
    if not quiet:
        try:
            req = urllib.request.Request(f"{OLLAMA_URL}/api/ps")
            resp = urllib.request.urlopen(req, timeout=5)
            ps_data = json.loads(resp.read())
            for m in ps_data.get("models", []):
                name = m.get("name", "")
                if name and name != OLLAMA_MODEL:
                    print(f"  Ollama busy with {name} (likely gateway) — skipping")
                    return
        except Exception:
            pass
    else:
        print("  Quiet hours — GPU free for batch, no chat guard needed")

    # Load raw data
    if not os.path.exists(RAW_HA_FILE):
        print(f"  ERROR: Raw data file not found: {RAW_HA_FILE}", file=sys.stderr)
        print("  Run with --scrape-only first.", file=sys.stderr)
        sys.exit(1)

    try:
        with open(RAW_HA_FILE) as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  ERROR: Failed to read raw data: {e}", file=sys.stderr)
        sys.exit(1)

    scrape_ts = raw.get("scrape_timestamp", "")
    d = raw.get("data", {})
    quick = d.get("quick", False)
    climate_data = d.get("climate_data", "")
    rooms_data = d.get("rooms_data", "")
    anomalies_data = d.get("anomalies_data", "")
    lights_data = d.get("lights_data", "")
    room_activity = d.get("room_activity", "")
    switch_timeline = d.get("switch_timeline", "")

    # Check staleness
    if scrape_ts:
        try:
            scrape_dt = datetime.fromisoformat(scrape_ts)
            age_hours = (dt - scrape_dt).total_seconds() / 3600
            if age_hours > 12:
                print(f"  WARNING: Raw data is {age_hours:.0f}h old (scraped {scrape_ts})")
        except ValueError:
            pass

    print(f"  Loaded raw data (scraped {scrape_ts}, mode={'quick' if quick else 'full'})")

    # Build the user prompt
    now = datetime.now()
    parts = [
        f"Date/time: {now.strftime('%A, %d %B %Y, %H:%M')} (Poland, CET)",
        "",
        "=== CLIMATE & AIR QUALITY ===",
        climate_data,
        "",
        "=== LIGHTS (current) ===",
        lights_data,
    ]

    if room_activity:
        parts += ["", "=== ROOM USAGE (last 6h, from switch activity) ===", room_activity]

    if switch_timeline:
        parts += ["", "=== SWITCH TIMELINE (recent events) ===", switch_timeline]

    if rooms_data and not rooms_data.startswith("[error"):
        parts += ["", "=== ROOMS OVERVIEW ===", rooms_data]

    if anomalies_data and not anomalies_data.startswith("[error"):
        parts += ["", "=== STATISTICAL ANOMALIES (48h) ===", anomalies_data]

    # Add previous observation for comparison
    prev = load_previous_home_note()
    if prev:
        parts += [
            "",
            f"=== PREVIOUS OBSERVATION ({prev['generated']}) ===",
            prev["content"][:800],
        ]

    # Add latest correlate insights if available
    insights = load_latest_insights()
    if insights:
        parts += [
            "",
            f"=== LATEST DEEP ANALYSIS ({insights['generated']}) ===",
            insights["content"],
        ]

    user_prompt = "\n".join(parts)

    print(f"  Prompt: {len(user_prompt)} chars")

    # Ask Ollama
    print("  Calling Ollama for analysis...")
    analysis = call_ollama(SYSTEM_PROMPT, user_prompt)
    if analysis:
        # Strip combining-character junk the model sometimes outputs
        analysis = re.sub(r"^[\u0300-\u036f\u0332]+", "", analysis).lstrip()
    if not analysis:
        print("  Ollama failed — saving raw data as fallback")
        analysis = (
            f"⚠️ LLM analysis unavailable — raw snapshot:\n\n"
            f"{climate_data}\n\n{lights_data}"
        )

    # Save note with dual timestamps
    title = f"Home Journal — {now.strftime('%d %b %Y, %H:%M')}"
    context = {
        "mode": "quick" if quick else "full",
        "scrape_timestamp": scrape_ts,
        "analyze_timestamp": dt.isoformat(timespec="seconds"),
        "sensors_collected": sum(1 for x in [climate_data, rooms_data, anomalies_data, lights_data] if x),
        "previous_available": prev is not None,
        "room_activity": bool(room_activity),
        "insights_available": insights is not None,
    }
    save_note(title, analysis, context)
    print("  Done.")


def main():
    parser = argparse.ArgumentParser(description="HA journal — home automation analysis")
    parser.add_argument('--scrape-only', action='store_true',
                        help='Only collect HA sensor data, save raw (no LLM)')
    parser.add_argument('--analyze-only', action='store_true',
                        help='Only run LLM analysis on previously collected raw data')
    parser.add_argument('--quick', action='store_true',
                        help='Climate-only snapshot (shorter prompt)')
    args = parser.parse_args()

    if args.scrape_only:
        run_scrape(quick=args.quick)
    elif args.analyze_only:
        run_analyze()
    else:
        # Legacy: full run (backward compatible)
        run_scrape(quick=args.quick)
        run_analyze()


if __name__ == "__main__":
    main()
