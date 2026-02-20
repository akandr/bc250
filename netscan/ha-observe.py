#!/usr/bin/env python3
"""ha-observe.py — Home Assistant read-only observer for ClawdBot.

Reads HA state & history via REST API, computes statistical summaries,
and returns condensed text for LLM interpretation. NEVER writes to HA.

Subcommands:
  snapshot        Current state of all entities (grouped, condensed)
  rooms           Room-by-room summary (lights, temp, air quality, covers)
  lights          Which lights/switches are on right now
  climate         Temperature + humidity + air quality across zones
  weather         Current weather + forecast
  history ENTITY  Last 24h of a specific sensor (with stats)
  anomalies       Statistical anomaly scan across all numeric sensors
  appliances      Washer, dryer, fridge, etc. — status
  covers          Blinds/shades state
  entity ENTITY   Single entity full detail (state + attributes)
  entities REGEX  List entities matching pattern

Environment:
  HASS_TOKEN — long-lived access token
  HASS_URL   — base URL (default http://homeassistant:8123)

Location: /opt/netscan/ha-observe.py
"""

import json, os, sys, re, urllib.request, statistics
from datetime import datetime, timedelta, timezone
from collections import defaultdict

HASS_URL   = os.environ.get("HASS_URL", "http://homeassistant:8123")
HASS_TOKEN = os.environ.get("HASS_TOKEN", "")

NOW = datetime.now()

# ═══════════════════════════════════════════════════════════════
# API helpers
# ═══════════════════════════════════════════════════════════════

def api_get(path):
    """GET from HA REST API."""
    url = f"{HASS_URL}{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {HASS_TOKEN}",
        "Content-Type": "application/json",
    })
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read())
    except Exception as ex:
        print(f"ERROR: {url} — {ex}", file=sys.stderr)
        return None

def get_all_states():
    return api_get("/api/states") or []

def get_history(entity_id, hours=24):
    """Get history for a single entity over the last N hours."""
    start = (datetime.now(timezone.utc) - timedelta(hours=hours))
    start_str = start.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    data = api_get(f"/api/history/period/{start_str}"
                   f"?filter_entity_id={entity_id}&minimal_response&no_attributes")
    if data and data[0]:
        return data[0]
    return []

# ═══════════════════════════════════════════════════════════════
# Entity classification
# ═══════════════════════════════════════════════════════════════

# Friendly room name from entity ID or friendly_name
ROOM_PATTERNS = {
    "salon": "Salon (Living room)",
    "sypialnia": "Sypialnia (Bedroom)",
    "kuchni|kuchen": "Kuchnia (Kitchen)",
    "jadalni": "Jadalnia (Dining room)",
    "łazienk|lazienk": "Łazienka (Bathroom)",
    "piętro|pietro": "Piętro (Upstairs)",
    "parter": "Parter (Ground floor)",
    "piwnic": "Piwnica (Basement)",
    "garaż|garaz": "Garaż (Garage)",
    "pokój komputerowy|komputerowy": "Pokój komputerowy (Office)",
    "pokój dziecięcy|chłopaki|chlopaki": "Pokój chłopców (Boys' room)",
    "wiatrołap|wiatolap": "Wiatrołap (Vestibule)",
    "spiżarni|spizarni": "Spiżarnia (Pantry)",
    "warsztat": "Warsztat (Workshop)",
    "przedpokoj|przedpokój": "Przedpokój (Hallway)",
    "ogród|ogrod": "Ogród (Garden)",
    "podjazd": "Podjazd (Driveway)",
    "dachowe|górna belka|gorna belka": "Dach/Strych (Roof/Attic)",
    "drzew": "Zewnętrzne (Outdoor)",
    "schod": "Schody (Stairs)",
}

def guess_room(entity):
    """Try to infer room from entity_id or friendly_name."""
    eid = entity.get("entity_id", "").lower()
    fname = entity.get("attributes", {}).get("friendly_name", "").lower()
    text = f"{eid} {fname}"
    for pattern, room in ROOM_PATTERNS.items():
        if re.search(pattern, text, re.IGNORECASE):
            return room
    return "Other"

def is_numeric(state):
    try:
        float(state)
        return True
    except (ValueError, TypeError):
        return False

# ═══════════════════════════════════════════════════════════════
# Subcommands
# ═══════════════════════════════════════════════════════════════

def cmd_snapshot():
    """Full state snapshot, grouped by domain."""
    states = get_all_states()
    domains = defaultdict(list)
    for e in states:
        d = e["entity_id"].split(".")[0]
        domains[d].append(e)

    skip = {"event", "update", "button", "image", "tts", "conversation",
            "select", "number", "siren"}
    for d in sorted(domains):
        if d in skip:
            continue
        items = domains[d]
        print(f"\n═══ {d.upper()} ({len(items)}) ═══")
        for e in sorted(items, key=lambda x: x["entity_id"]):
            s = e["state"]
            if s in ("unknown", "unavailable"):
                continue
            fname = e.get("attributes", {}).get("friendly_name", "")
            unit = e.get("attributes", {}).get("unit_of_measurement", "")
            print(f"  {fname or e['entity_id']}: {s}{' ' + unit if unit else ''}")


def cmd_rooms():
    """Room-by-room summary with temp, humidity, air, lights, covers."""
    states = get_all_states()
    rooms = defaultdict(lambda: {"temp": [], "humidity": [], "co2": [],
                                  "pm25": [], "voc": [], "lights_on": [],
                                  "lights_off": 0, "covers": [], "other": []})

    for e in states:
        eid = e["entity_id"]
        s = e["state"]
        fname = e.get("attributes", {}).get("friendly_name", "")
        unit = e.get("attributes", {}).get("unit_of_measurement", "")
        room = guess_room(e)

        if s in ("unknown", "unavailable"):
            continue

        d = eid.split(".")[0]

        if d == "sensor" and is_numeric(s):
            v = float(s)
            # Skip derived thermal comfort sensors
            if "parametry" in eid or "thermal_comfort" in eid:
                continue
            if "°C" in unit and ("temperatur" in eid.lower() or eid.endswith("_t")):
                rooms[room]["temp"].append((fname, v))
            elif unit == "%"  and ("humid" in eid.lower() or "wilgotn" in eid.lower()):
                rooms[room]["humidity"].append((fname, v))
            elif "co2" in eid.lower() or "dwutlenek" in eid.lower():
                rooms[room]["co2"].append((fname, v))
            elif "pm2" in eid.lower():
                rooms[room]["pm25"].append((fname, v))
            elif "voc" in eid.lower() or "lotne" in eid.lower():
                rooms[room]["voc"].append((fname, v))

        elif d == "switch":
            if s == "on":
                rooms[room]["lights_on"].append(fname or eid)
            else:
                rooms[room]["lights_off"] += 1

        elif d == "light":
            if s == "on":
                rooms[room]["lights_on"].append(fname or eid)

        elif d == "cover":
            rooms[room]["covers"].append((fname, s))

    for room in sorted(rooms):
        r = rooms[room]
        # Skip rooms with nothing interesting
        if not any([r["temp"], r["lights_on"], r["covers"], r["co2"]]):
            continue
        print(f"\n─── {room} ───")
        for label, items, unit in [("Temp", r["temp"], "°C"),
                                    ("Humidity", r["humidity"], "%"),
                                    ("CO₂", r["co2"], "ppm"),
                                    ("PM2.5", r["pm25"], "µg/m³"),
                                    ("VOC", r["voc"], "mg/m³")]:
            for name, val in items:
                print(f"  {label}: {val} {unit}")
        if r["lights_on"]:
            print(f"  💡 ON: {', '.join(r['lights_on'])}")
        if r["covers"]:
            for name, state in r["covers"]:
                icon = "🔽" if state == "closed" else "🔼"
                print(f"  {icon} {name}: {state}")


def cmd_lights():
    """Which lights/switches are on or off."""
    states = get_all_states()
    on, off = [], []
    for e in states:
        eid = e["entity_id"]
        d = eid.split(".")[0]
        if d not in ("switch", "light"):
            continue
        s = e["state"]
        fname = e.get("attributes", {}).get("friendly_name", eid)
        if s == "unavailable":
            continue
        if s == "on":
            on.append(fname)
        else:
            off.append(fname)

    print(f"💡 ON ({len(on)}):")
    for name in sorted(on):
        print(f"  • {name}")
    print(f"\n⚫ OFF ({len(off)}):")
    for name in sorted(off):
        print(f"  • {name}")


def cmd_climate():
    """Temperature, humidity, air quality across all zones."""
    states = get_all_states()
    print(f"Climate snapshot — {NOW.strftime('%Y-%m-%d %H:%M')}\n")

    # Weather
    for e in states:
        if e["entity_id"].startswith("weather."):
            a = e["attributes"]
            print(f"🌤 Outside: {a.get('temperature', '?')}°C, "
                  f"humidity {a.get('humidity', '?')}%, "
                  f"wind {a.get('wind_speed', '?')} km/h, "
                  f"{e['state']}")
            print(f"   pressure {a.get('pressure', '?')} hPa, "
                  f"clouds {a.get('cloud_coverage', '?')}%, "
                  f"dew point {a.get('dew_point', '?')}°C")
            print()

    # Indoor sensors grouped by zone
    zones = defaultdict(dict)
    for e in states:
        eid = e["entity_id"]
        s = e["state"]
        if not is_numeric(s) or s in ("unknown", "unavailable"):
            continue
        fname = e.get("attributes", {}).get("friendly_name", "")
        unit = e.get("attributes", {}).get("unit_of_measurement", "")
        room = guess_room(e)
        v = float(s)

        # Skip derived/computed thermal comfort sensors
        if "parametry" in eid or "thermal_comfort" in eid:
            continue

        if "°C" in unit and ("temperatur" in eid.lower() or "_t" in eid):
            zones[room]["temp"] = v
            zones[room]["temp_name"] = fname
        elif unit == "%" and ("humid" in eid.lower() or "wilgotn" in eid.lower() or "_h" in eid):
            zones[room]["humidity"] = v
        elif "ppm" in unit:
            zones[room]["co2"] = v
        elif "µg/m³" in unit or "μg/m³" in unit:
            zones[room]["pm25"] = v
        elif "mg/m³" in unit and ("voc" in eid.lower() or "lotne" in eid.lower()):
            zones[room]["voc"] = v
        elif "mg/m3" in unit and "formaldehyd" in eid.lower():
            zones[room]["hcho"] = v

    for zone in sorted(zones):
        z = zones[zone]
        if "temp" not in z:
            continue
        parts = [f"{z['temp']:.1f}°C"]
        if "humidity" in z: parts.append(f"{z['humidity']:.0f}%RH")
        if "co2" in z: parts.append(f"CO₂ {z['co2']:.0f}ppm")
        if "pm25" in z: parts.append(f"PM2.5 {z['pm25']:.0f}µg/m³")
        if "voc" in z: parts.append(f"VOC {z['voc']:.3f}mg/m³")
        if "hcho" in z: parts.append(f"HCHO {z['hcho']:.3f}mg/m³")

        # Air quality assessment
        warnings = []
        if z.get("co2", 0) > 1000: warnings.append("⚠️ CO₂ HIGH")
        if z.get("pm25", 0) > 25: warnings.append("⚠️ PM2.5 HIGH")
        if z.get("voc", 0) > 0.5: warnings.append("⚠️ VOC HIGH")
        if z.get("hcho", 0) > 0.08: warnings.append("⚠️ HCHO HIGH")
        warn_str = f"  {'  '.join(warnings)}" if warnings else ""

        print(f"  {zone:35s} {' │ '.join(parts)}{warn_str}")


def cmd_weather():
    """Current weather + detailed forecast."""
    states = get_all_states()
    for e in states:
        if e["entity_id"].startswith("weather."):
            a = e["attributes"]
            print(f"Weather: {e['state']}")
            print(f"  Temperature: {a.get('temperature', '?')}°C")
            print(f"  Humidity: {a.get('humidity', '?')}%")
            print(f"  Pressure: {a.get('pressure', '?')} hPa")
            print(f"  Wind: {a.get('wind_speed', '?')} km/h, bearing {a.get('wind_bearing', '?')}°")
            print(f"  Cloud cover: {a.get('cloud_coverage', '?')}%")
            print(f"  Dew point: {a.get('dew_point', '?')}°C")
            print(f"  UV index: {a.get('uv_index', '?')}")
            print(f"  Source: {a.get('attribution', '?')}")

    # Sun data
    for e in states:
        if e["entity_id"] == "sun.sun":
            print(f"\nSun: {e['state']}")
    for e in states:
        if e["entity_id"].startswith("sensor.sun_"):
            fname = e.get("attributes", {}).get("friendly_name", "")
            s = e["state"]
            if "T" in s:
                try:
                    dt = datetime.fromisoformat(s)
                    s = dt.strftime("%H:%M")
                except Exception:
                    pass
            print(f"  {fname}: {s}")


def cmd_history(entity_id, hours=24):
    """History of a sensor with statistics."""
    points = get_history(entity_id, hours)
    if not points:
        print(f"No history for {entity_id} (last {hours}h)")
        return

    # Extract numeric values
    values = []
    times = []
    for p in points:
        s = p.get("state", "")
        ts = p.get("last_changed", "")
        if is_numeric(s):
            values.append(float(s))
            times.append(ts)

    if not values:
        print(f"{entity_id}: {len(points)} points but none numeric")
        return

    # Basic stats
    mean = statistics.mean(values)
    stdev = statistics.stdev(values) if len(values) > 1 else 0
    median = statistics.median(values)
    vmin, vmax = min(values), max(values)

    print(f"History: {entity_id} — last {hours}h ({len(values)} samples)")
    print(f"  Current:  {values[-1]}")
    print(f"  Mean:     {mean:.2f}")
    print(f"  Median:   {median:.2f}")
    print(f"  Stdev:    {stdev:.2f}")
    print(f"  Min:      {vmin} ({times[values.index(vmin)][:16]})")
    print(f"  Max:      {vmax} ({times[values.index(vmax)][:16]})")
    print(f"  Range:    {vmax - vmin:.2f}")

    # IQR outlier detection
    if len(values) >= 10:
        sorted_v = sorted(values)
        n = len(sorted_v)
        q1 = sorted_v[n // 4]
        q3 = sorted_v[3 * n // 4]
        iqr = q3 - q1
        low_fence = q1 - 2.5 * iqr
        high_fence = q3 + 2.5 * iqr
        outliers = [(times[i], v) for i, v in enumerate(values)
                    if v < low_fence or v > high_fence]
        if outliers:
            print(f"\n  ⚠️ OUTLIERS ({len(outliers)}) [IQR fences: {low_fence:.1f}–{high_fence:.1f}]:")
            for ts, v in outliers[:5]:
                print(f"    {ts[:16]} → {v}")

    # Z-score of current value
    if stdev > 0:
        z = (values[-1] - mean) / stdev
        if abs(z) > 2:
            print(f"\n  ⚠️ Current z-score: {z:+.2f} (>{'+' if z>0 else ''}{2 if z>0 else -2} threshold)")

    # Trend (simple: compare first/last quarter means)
    if len(values) >= 8:
        q_len = len(values) // 4
        early = statistics.mean(values[:q_len])
        late = statistics.mean(values[-q_len:])
        delta = late - early
        if abs(delta) > stdev * 0.5 and stdev > 0:
            direction = "↗ rising" if delta > 0 else "↘ falling"
            print(f"  Trend: {direction} ({early:.1f} → {late:.1f}, Δ{delta:+.2f})")

    # Recent values (last 10)
    print(f"\n  Recent ({min(10, len(values))} pts):")
    for ts, v in zip(times[-10:], values[-10:]):
        print(f"    {ts[:16]}  {v}")


def cmd_anomalies():
    """Scan all numeric sensors for statistical anomalies."""
    states = get_all_states()
    print(f"Anomaly scan — {NOW.strftime('%Y-%m-%d %H:%M')}\n")

    targets = []
    for e in states:
        eid = e["entity_id"]
        d = eid.split(".")[0]
        s = e["state"]
        if d != "sensor" or not is_numeric(s):
            continue
        # Skip irrelevant sensors
        if any(x in eid for x in ["battery", "bateria", "akandr_", "sun_",
                                   "backup_", "app_version", "urmet_",
                                   "parametry", "thermal_comfort"]):
            continue
        unit = e.get("attributes", {}).get("unit_of_measurement", "")
        if not unit:
            continue
        targets.append((eid, float(s), unit,
                        e.get("attributes", {}).get("friendly_name", "")))

    anomalies_found = 0
    for eid, current, unit, fname in targets:
        points = get_history(eid, hours=48)
        values = []
        for p in points:
            s = p.get("state", "")
            if is_numeric(s):
                values.append(float(s))

        if len(values) < 10:
            continue

        mean = statistics.mean(values)
        stdev = statistics.stdev(values) if len(values) > 1 else 0

        # Z-score check
        if stdev > 0:
            z = (current - mean) / stdev
            if abs(z) > 2.5:
                anomalies_found += 1
                direction = "above" if z > 0 else "below"
                print(f"⚠️  {fname or eid}")
                print(f"    Current: {current} {unit} ({direction} normal)")
                print(f"    Mean(48h): {mean:.2f}, Stdev: {stdev:.2f}, Z: {z:+.2f}")
                print()

        # IQR check
        sorted_v = sorted(values)
        n = len(sorted_v)
        q1 = sorted_v[n // 4]
        q3 = sorted_v[3 * n // 4]
        iqr = q3 - q1
        if iqr > 0:
            high_fence = q3 + 2.5 * iqr
            low_fence = q1 - 2.5 * iqr
            if current > high_fence or current < low_fence:
                if stdev == 0 or abs((current - mean) / stdev) <= 2.5:
                    anomalies_found += 1
                    print(f"⚠️  {fname or eid}")
                    print(f"    Current: {current} {unit} (outside IQR fences)")
                    print(f"    Q1: {q1:.2f}, Q3: {q3:.2f}, IQR: {iqr:.2f}")
                    print(f"    Fences: [{low_fence:.1f}, {high_fence:.1f}]")
                    print()

    if anomalies_found == 0:
        print("✅ No anomalies detected across all numeric sensors.")
    else:
        print(f"\n{anomalies_found} anomal{'y' if anomalies_found == 1 else 'ies'} found.")


def cmd_appliances():
    """Status of appliances (washer, dryer, fridge, etc.)."""
    states = get_all_states()
    appliance_keys = ["pralka", "suszarka", "lodowka", "zmywarka",
                      "piekarnik", "odkurzacz"]

    for key in appliance_keys:
        related = [e for e in states if key in e["entity_id"].lower()]
        if not related:
            continue
        label = {"pralka": "🫧 Pralka (Washer)", "suszarka": "🌀 Suszarka (Dryer)",
                 "lodowka": "🧊 Lodówka (Fridge)", "zmywarka": "🍽 Zmywarka (Dishwasher)",
                 "piekarnik": "🔥 Piekarnik (Oven)", "odkurzacz": "🧹 Odkurzacz (Vacuum)",
                 }.get(key, key)
        print(f"\n{label}:")
        for e in related:
            s = e["state"]
            if s in ("unknown", "unavailable"):
                continue
            fname = e.get("attributes", {}).get("friendly_name", "")
            print(f"  {fname}: {s}")


def cmd_covers():
    """Blinds/shades state."""
    states = get_all_states()
    print("Covers/blinds:\n")
    for e in sorted(states, key=lambda x: x["entity_id"]):
        if e["entity_id"].split(".")[0] != "cover":
            continue
        s = e["state"]
        fname = e.get("attributes", {}).get("friendly_name", e["entity_id"])
        pos = e.get("attributes", {}).get("current_position", "")
        icon = "🔽" if s == "closed" else ("🔼" if s == "open" else "❓")
        pos_str = f" ({pos}%)" if pos != "" else ""
        print(f"  {icon} {fname}: {s}{pos_str}")


def cmd_entity(entity_id):
    """Full detail of a single entity."""
    data = api_get(f"/api/states/{entity_id}")
    if not data:
        print(f"Entity not found: {entity_id}")
        return
    print(f"Entity: {data['entity_id']}")
    print(f"State: {data['state']}")
    print(f"Last changed: {data.get('last_changed', '?')}")
    print(f"Last updated: {data.get('last_updated', '?')}")
    print("Attributes:")
    for k, v in sorted(data.get("attributes", {}).items()):
        if isinstance(v, (list, dict)):
            print(f"  {k}: {json.dumps(v, ensure_ascii=False)[:200]}")
        else:
            print(f"  {k}: {v}")


def cmd_entities(pattern):
    """List entities matching a regex pattern."""
    states = get_all_states()
    matches = [e for e in states
               if re.search(pattern, e["entity_id"], re.IGNORECASE)
               or re.search(pattern,
                           e.get("attributes", {}).get("friendly_name", ""),
                           re.IGNORECASE)]
    if not matches:
        print(f"No entities matching '{pattern}'")
        return
    for e in sorted(matches, key=lambda x: x["entity_id"]):
        s = e["state"]
        fname = e.get("attributes", {}).get("friendly_name", "")
        unit = e.get("attributes", {}).get("unit_of_measurement", "")
        print(f"  {e['entity_id']:55s} {s:>15s} {unit:>6s}  {fname}")


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

USAGE = """ha-observe.py — Home Assistant read-only observer

Usage:
  ha-observe.py snapshot         All entity states (grouped by domain)
  ha-observe.py rooms            Room-by-room summary (temp, lights, air)
  ha-observe.py lights           Which lights/switches are ON
  ha-observe.py climate          Temperature + humidity + air quality
  ha-observe.py weather          Current weather + sun times
  ha-observe.py history ENTITY [HOURS]  Sensor history with statistics
  ha-observe.py anomalies        Statistical anomaly scan (48h window)
  ha-observe.py appliances       Washer, dryer, fridge status
  ha-observe.py covers           Blinds/shades state
  ha-observe.py entity ENTITY    Single entity full detail
  ha-observe.py entities REGEX   List entities matching pattern"""

def main():
    if not HASS_TOKEN:
        print("ERROR: HASS_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    if len(sys.argv) < 2:
        print(USAGE)
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == "snapshot":
        cmd_snapshot()
    elif cmd == "rooms":
        cmd_rooms()
    elif cmd == "lights":
        cmd_lights()
    elif cmd == "climate":
        cmd_climate()
    elif cmd == "weather":
        cmd_weather()
    elif cmd == "history":
        if len(sys.argv) < 3:
            print("Usage: ha-observe.py history ENTITY_ID [HOURS]")
            sys.exit(1)
        hours = int(sys.argv[3]) if len(sys.argv) > 3 else 24
        cmd_history(sys.argv[2], hours)
    elif cmd == "anomalies":
        cmd_anomalies()
    elif cmd == "appliances":
        cmd_appliances()
    elif cmd == "covers":
        cmd_covers()
    elif cmd == "entity":
        if len(sys.argv) < 3:
            print("Usage: ha-observe.py entity ENTITY_ID")
            sys.exit(1)
        cmd_entity(sys.argv[2])
    elif cmd == "entities":
        if len(sys.argv) < 3:
            print("Usage: ha-observe.py entities REGEX")
            sys.exit(1)
        cmd_entities(sys.argv[2])
    else:
        print(f"Unknown command: {cmd}")
        print(USAGE)
        sys.exit(1)


if __name__ == "__main__":
    main()
