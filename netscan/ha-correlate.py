#!/usr/bin/env python3
"""
ha-correlate.py — HA sensor time-series correlation analysis
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Analyzes 24h of Home Assistant sensor history to find:
  - Cross-correlations between sensor pairs (temp ↔ humidity, CO₂ ↔ window, etc.)
  - Statistical anomalies via rolling mean ± 2σ
  - Lag analysis (how long until a response to a stimulus)
  - Switch/actuator duty cycles and trigger patterns
  - Daily patterns and weekly trend comparisons

Uses idle GPU time for LLM synthesis of findings.

Output: /opt/netscan/data/correlate/
  - correlate-YYYYMMDD.json   (daily analysis results)
  - latest-correlate.json     (symlink to latest)

Cron: 30 7 * * * flock -w 1200 /tmp/ollama-gpu.lock python3 /opt/netscan/ha-correlate.py

Location on bc250: /opt/netscan/ha-correlate.py
"""

import argparse
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from pathlib import Path
from llm_sanitize import sanitize_llm_output

# ── Config ─────────────────────────────────────────────────────────────────
OLLAMA_URL = "http://localhost:11434"
OLLAMA_CHAT = f"{OLLAMA_URL}/api/chat"
OLLAMA_MODEL = "qwen3:14b"

HASS_URL = os.environ.get("HASS_URL", "http://homeassistant:8123")
HASS_TOKEN = os.environ.get("HASS_TOKEN", "")

SIGNAL_RPC = "http://127.0.0.1:8080/api/v1/rpc"
SIGNAL_FROM = "+<BOT_PHONE>"
SIGNAL_TO = "+<OWNER_PHONE>"

DATA_DIR = Path("/opt/netscan/data/correlate")
RAW_CORRELATE_FILE = DATA_DIR / "raw-correlate.json"
THINK_DIR = Path("/opt/netscan/data/think")
HISTORY_HOURS = 24        # look back to cover since-last-analysis window
MIN_SAMPLES = 4           # minimum data points for per-sensor stats
MIN_CORR_SAMPLES = 8      # minimum aligned points for correlations
CORR_THRESHOLD = 0.60     # minimum |r| to report a correlation
ANOMALY_Z = 2.5           # z-score threshold for anomaly
DUTY_ON_STATES = {"on", "open", "home"}

# Load HA credentials from openclaw .env
ENV_FILE = os.path.expanduser("~/.openclaw/.env")
if os.path.exists(ENV_FILE):
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
    HASS_TOKEN = os.environ.get("HASS_TOKEN", HASS_TOKEN)

# ── Garage car tracker ──────────────────────────────────────────────────────
# Detects car leaving/returning by correlating gate+door events with temp signature:
#   Gate opens → garage door opens → temp dips (cold air) → door closes
#   → If temp then climbs rapidly (+0.8°C in <30min): car RETURNED (warm engine)
#   → If temp drops suddenly (≥1°C cold air ingress): car LEFT
#   → Small temp change (<1°C drop, <0.8°C rise): just a door button press
GARAGE_TEMP_SENSOR = "sensor.1000becdc2_t"    # Garaż termometr
GARAGE_HUMIDITY    = "sensor.1000becdc2_h"    # Garaż humidity
GARAGE_DOOR_SWITCH = "switch.10014d3a8b_2"    # Bramy garazowe-Garaz
GARAGE_AUTO_SWITCH = "switch.1000ac01ad"       # Garaz auto
GATE_SWITCH        = "switch.1000aa2079"       # Brama (driveway gate)

# Detection thresholds
GARAGE_TEMP_RISE_THRESHOLD = 0.8    # °C rise within window → car returned
GARAGE_TEMP_DROP_THRESHOLD = 1.0    # °C drop required to confirm garage opened (cold air)
GARAGE_TEMP_WINDOW_MIN = 30         # minutes after door event to look for temp change
GARAGE_EVENT_MERGE_SEC = 300        # merge gate+door events within 5min as one event

# Known device context — don't flag these automation patterns
KNOWN_DEVICES = {
    "switch.1000becdc2": {
        "name": "Garaż termometr (garage heater)",
        "behavior": "auto on when humidity exceeds threshold — normal dehumidification",
    },
    "switch.1001192a6d": {
        "name": "Kuchnia ledy góra (kitchen upper LEDs)",
        "behavior": "always on — controlled by physical wall buttons",
    },
    "switch.1000670730_2": {
        "name": "Łazienka piwnica-Wentylator (basement bathroom fan)",
        "behavior": "automated periodic on/off — safety ventilation for gas heater",
    },
    "switch.10007f0781_2": {
        "name": "Łazienka piętro-Wentylator (upstairs bathroom fan)",
        "behavior": "always on — humidity-triggered, runs continuously",
    },
}

# Sensor groups for correlation analysis
SENSOR_GROUPS = {
    "temperature": {
        "unit_pattern": "°C",
        "id_pattern": r"(temperatur|_t$)",
        "exclude_pattern": r"(parametry|thermal_comfort|weather)",
    },
    "humidity": {
        "unit_pattern": "%",
        "id_pattern": r"(humid|wilgotn|_h$)",
        "exclude_pattern": r"(parametry|thermal_comfort|bateria|battery)",
    },
    "co2": {
        "unit_pattern": "ppm",
        "id_pattern": r"(dwutlenek|co2|carbon)",
        "exclude_pattern": "",
    },
    "pm25": {
        "unit_pattern": "µg/m³|μg/m³",
        "id_pattern": r"pm2",
        "exclude_pattern": "",
    },
    "voc": {
        "unit_pattern": "mg/m³|mg/m3",
        "id_pattern": r"(lotne|voc)",
        "exclude_pattern": r"formaldehyd",
    },
}

# Room pattern → room name (from ha-observe.py)
ROOM_PATTERNS = {
    "salon": "Salon",
    "sypialnia": "Sypialnia",
    "kuchni|kuchen": "Kuchnia",
    "jadalni": "Jadalnia",
    "łazienk|lazienk": "Łazienka",
    "piętro|pietro": "Piętro",
    "parter": "Parter",
    "piwnic": "Piwnica",
    "garaż|garaz": "Garaż",
    "komputerow": "Biuro",
    "chłopaki|chlopaki|dziecięc": "Chłopcy",
    "wiatrołap|wiatolap": "Wiatrołap",
    "spiżarni|spizarni": "Spiżarnia",
    "warsztat": "Warsztat",
    "przedpokoj|przedpokój": "Przedpokój",
    "ogród|ogrod": "Ogród",
    "dachowe|górna belka|gorna belka": "Dach",
}


# ── Helpers ────────────────────────────────────────────────────────────────

def log(msg):
    print(f"  {msg}", flush=True)


def guess_room(entity_id, friendly_name=""):
    """Infer room from entity ID or friendly name."""
    text = f"{entity_id} {friendly_name}".lower()
    for pattern, room in ROOM_PATTERNS.items():
        if re.search(pattern, text, re.IGNORECASE):
            return room
    return "Other"


# ── HA API ─────────────────────────────────────────────────────────────────

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
        log(f"HA API error: {url} — {ex}")
        return None


def ha_get_all_states():
    return ha_api_get("/api/states") or []


def ha_get_history(entity_id, hours=24):
    """Get history for a single entity over the last N hours."""
    start = datetime.now(timezone.utc) - timedelta(hours=hours)
    start_str = start.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    data = ha_api_get(
        f"/api/history/period/{start_str}"
        f"?filter_entity_id={entity_id}&minimal_response&no_attributes"
    )
    if data and data[0]:
        return data[0]
    return []


def ha_get_history_bulk(entity_ids, hours=24):
    """Get history for multiple entities in one API call."""
    start = datetime.now(timezone.utc) - timedelta(hours=hours)
    start_str = start.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    ids_str = ",".join(entity_ids)
    data = ha_api_get(
        f"/api/history/period/{start_str}"
        f"?filter_entity_id={ids_str}&minimal_response&no_attributes"
    )
    if not data:
        return {}
    result = {}
    for entity_history in data:
        if entity_history:
            eid = entity_history[0].get("entity_id", "")
            result[eid] = entity_history
    return result


# ── Statistics ─────────────────────────────────────────────────────────────

def extract_numeric_timeseries(history_points):
    """Extract (timestamp, value) pairs from HA history."""
    series = []
    for p in history_points:
        s = p.get("state", "")
        ts_str = p.get("last_changed", "")
        try:
            v = float(s)
            if not math.isnan(v) and not math.isinf(v):
                # Parse ISO timestamp
                ts = ts_str.replace("+00:00", "").replace("Z", "")
                series.append((ts, v))
        except (ValueError, TypeError):
            pass
    return series


def extract_onoff_timeseries(history_points):
    """Extract (timestamp, is_on_bool) pairs from HA history."""
    series = []
    for p in history_points:
        s = p.get("state", "").lower()
        ts_str = p.get("last_changed", "")
        if s in ("on", "off", "open", "closed", "home", "not_home"):
            is_on = s in DUTY_ON_STATES
            ts = ts_str.replace("+00:00", "").replace("Z", "")
            series.append((ts, is_on))
    return series


def resample_to_hourly(series, hours=24):
    """Resample a time series to hourly buckets (mean per hour).
    Returns list of (hour_label, mean_value) for the last N hours."""
    if not series:
        return []
    now = datetime.now(timezone.utc)
    buckets = defaultdict(list)

    for ts_str, val in series:
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            hour_key = ts.strftime("%Y-%m-%d %H:00")
            buckets[hour_key].append(val)
        except Exception:
            pass

    # Generate ordered hour keys
    result = []
    for h in range(hours, 0, -1):
        t = now - timedelta(hours=h)
        key = t.strftime("%Y-%m-%d %H:00")
        if key in buckets:
            result.append((key, statistics.mean(buckets[key])))
        # Skip hours with no data — don't interpolate

    return result


def pearson_correlation(xs, ys):
    """Compute Pearson correlation coefficient between two lists."""
    n = len(xs)
    if n < MIN_CORR_SAMPLES:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    num = sum(a * b for a, b in zip(dx, dy))
    den_x = math.sqrt(sum(a * a for a in dx))
    den_y = math.sqrt(sum(b * b for b in dy))
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


def compute_lag_correlation(xs, ys, max_lag_hours=4):
    """Find the lag (in hours) that maximizes correlation.
    Returns (best_lag, best_r) where lag is in hours."""
    if len(xs) < MIN_CORR_SAMPLES + max_lag_hours:
        return 0, pearson_correlation(xs, ys) if len(xs) >= MIN_CORR_SAMPLES else None

    best_lag = 0
    best_r = pearson_correlation(xs, ys)
    if best_r is None:
        best_r = 0.0

    for lag in range(1, max_lag_hours + 1):
        # ys shifted by lag (ys responds to xs with delay)
        r = pearson_correlation(xs[:-lag], ys[lag:])
        if r is not None and abs(r) > abs(best_r):
            best_r = r
            best_lag = lag

        # xs shifted by lag (xs responds to ys with delay)
        r = pearson_correlation(xs[lag:], ys[:-lag])
        if r is not None and abs(r) > abs(best_r):
            best_r = r
            best_lag = -lag

    return best_lag, best_r


def compute_duty_cycle(onoff_series, hours=24):
    """Compute duty cycle (fraction of time ON) from on/off series.
    Also returns toggle count and average on/off durations."""
    if not onoff_series:
        return None

    total_on_seconds = 0
    total_off_seconds = 0
    toggle_count = 0
    on_durations = []
    off_durations = []

    prev_ts = None
    prev_state = None

    for ts_str, is_on in onoff_series:
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            continue

        if prev_ts is not None:
            delta = (ts - prev_ts).total_seconds()
            if delta < 0:
                continue
            if prev_state:
                total_on_seconds += delta
                on_durations.append(delta)
            else:
                total_off_seconds += delta
                off_durations.append(delta)

            if is_on != prev_state:
                toggle_count += 1

        prev_ts = ts
        prev_state = is_on

    # Account for time since last change until now
    if prev_ts is not None:
        now = datetime.now(timezone.utc)
        delta = (now - prev_ts).total_seconds()
        if prev_state:
            total_on_seconds += delta
        else:
            total_off_seconds += delta

    total = total_on_seconds + total_off_seconds
    if total == 0:
        return None

    duty = total_on_seconds / total
    avg_on = statistics.mean(on_durations) if on_durations else 0
    avg_off = statistics.mean(off_durations) if off_durations else 0

    return {
        "duty_cycle": round(duty, 3),
        "on_pct": round(duty * 100, 1),
        "toggle_count": toggle_count,
        "total_on_min": round(total_on_seconds / 60, 1),
        "total_off_min": round(total_off_seconds / 60, 1),
        "avg_on_min": round(avg_on / 60, 1) if avg_on else 0,
        "avg_off_min": round(avg_off / 60, 1) if avg_off else 0,
    }


def compute_duty_heatmap(onoff_series):
    """Compute hourly duty cycle heatmap (24 bins) from on/off state series.
    Returns dict with hour (0-23) → fraction of time ON in that hour."""
    if not onoff_series:
        return None

    # Build minute-resolution on/off timeline
    hour_on = [0.0] * 24   # seconds ON per hour
    hour_total = [0.0] * 24  # seconds observed per hour

    prev_ts = None
    prev_state = None

    for ts_str, is_on in onoff_series:
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            continue

        if prev_ts is not None and prev_state is not None:
            # Walk through each hour boundary between prev_ts and ts
            cursor = prev_ts
            while cursor < ts:
                h = cursor.hour
                next_hour = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
                end = min(next_hour, ts)
                secs = (end - cursor).total_seconds()
                if secs > 0:
                    hour_total[h] += secs
                    if prev_state:
                        hour_on[h] += secs
                cursor = end

        prev_ts = ts
        prev_state = is_on

    # Account for time from last change to now
    if prev_ts is not None and prev_state is not None:
        now = datetime.now(timezone.utc)
        cursor = prev_ts
        while cursor < now:
            h = cursor.hour
            next_hour = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            end = min(next_hour, now)
            secs = (end - cursor).total_seconds()
            if secs > 0:
                hour_total[h] += secs
                if prev_state:
                    hour_on[h] += secs
            cursor = end

    heatmap = {}
    for h in range(24):
        if hour_total[h] > 0:
            heatmap[h] = round(hour_on[h] / hour_total[h] * 100, 1)
        else:
            heatmap[h] = 0.0

    # Find peak usage hours (>50% on)
    peak_hours = [h for h in range(24) if heatmap.get(h, 0) > 50]
    # Find idle hours (0% on)
    idle_hours = [h for h in range(24) if heatmap.get(h, 0) == 0 and hour_total[h] > 0]

    return {
        "hourly_pct": heatmap,
        "peak_hours": peak_hours,
        "idle_hours": idle_hours,
    }


def detect_anomalies(series, label=""):
    """Detect anomalies in a numeric time series using z-score."""
    if len(series) < MIN_SAMPLES:
        return []

    values = [v for _, v in series]
    mean = statistics.mean(values)
    stdev = statistics.stdev(values) if len(values) > 1 else 0
    if stdev == 0:
        return []

    anomalies = []
    for ts, v in series:
        z = (v - mean) / stdev
        if abs(z) > ANOMALY_Z:
            anomalies.append({
                "timestamp": ts,
                "value": v,
                "z_score": round(z, 2),
                "mean": round(mean, 2),
                "stdev": round(stdev, 2),
                "label": label,
            })
    return anomalies


def hourly_pattern(series):
    """Compute average value per hour of day (0-23)."""
    buckets = defaultdict(list)
    for ts_str, val in series:
        try:
            ts = datetime.fromisoformat(ts_str)
            buckets[ts.hour].append(val)
        except Exception:
            pass

    pattern = {}
    for h in range(24):
        if h in buckets:
            pattern[h] = round(statistics.mean(buckets[h]), 2)
    return pattern


# ── Room occupancy estimation ──────────────────────────────────────────────

def compute_room_usage(switch_ts, switch_entities, hours=24):
    """Estimate room occupancy from light/switch activity.

    Returns per-room summary:
      - total_lit_minutes: how long any light was on
      - first_activity: earliest switch-on timestamp
      - last_activity: latest switch-on timestamp
      - peak_hour: hour of day with most activity
      - activity_periods: list of (start, end) on-periods
    """
    room_data = defaultdict(lambda: {
        "lit_minutes": 0.0,
        "first_on": None,
        "last_off": None,
        "on_events": [],      # timestamps when turned on
        "hourly_on_min": defaultdict(float),  # hour → minutes lit
        "switches_active": set(),
    })

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)

    for eid, ts_series in switch_ts.items():
        info = switch_entities.get(eid)
        if not info:
            continue
        room = info["room"]
        fname = info["fname"]

        # Skip non-room entities (gates, garage doors, fans, etc.)
        skip_patterns = ["brama", "garaz", "gate", "wentylator", "fan",
                         "pompa", "auto", "pralka", "suszarka", "drzewo",
                         "termometr", "gniazdko"]
        if any(p in eid.lower() or p in fname.lower() for p in skip_patterns):
            continue

        prev_ts = None
        prev_on = False

        for ts_str, is_on in ts_series:
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except Exception:
                continue

            if ts < cutoff:
                prev_ts = ts
                prev_on = is_on
                continue

            # Track on-duration
            if prev_ts and prev_on:
                start = max(prev_ts, cutoff)
                delta_min = (ts - start).total_seconds() / 60
                if delta_min > 0:
                    room_data[room]["lit_minutes"] += delta_min
                    # Distribute across hours
                    t = start
                    while t < ts:
                        hour_end = t.replace(minute=0, second=0) + timedelta(hours=1)
                        chunk_end = min(hour_end, ts)
                        chunk_min = (chunk_end - t).total_seconds() / 60
                        room_data[room]["hourly_on_min"][t.hour] += chunk_min
                        t = chunk_end

            if is_on:
                room_data[room]["on_events"].append(ts)
                room_data[room]["switches_active"].add(fname)
                if room_data[room]["first_on"] is None or ts < room_data[room]["first_on"]:
                    room_data[room]["first_on"] = ts
            else:
                if room_data[room]["last_off"] is None or ts > room_data[room]["last_off"]:
                    room_data[room]["last_off"] = ts

            prev_ts = ts
            prev_on = is_on

        # Account for still-on at end of window
        if prev_on and prev_ts:
            start = max(prev_ts, cutoff)
            delta_min = (now - start).total_seconds() / 60
            if delta_min > 0:
                room_data[room]["lit_minutes"] += delta_min

    # Build summary
    result = {}
    for room, data in room_data.items():
        if data["lit_minutes"] < 1 and not data["on_events"]:
            continue

        # Find peak hour
        hourly = dict(data["hourly_on_min"])
        peak_hour = max(hourly, key=hourly.get) if hourly else None

        result[room] = {
            "lit_hours": round(data["lit_minutes"] / 60, 1),
            "lit_minutes": round(data["lit_minutes"], 0),
            "first_activity": data["first_on"].strftime("%H:%M") if data["first_on"] else None,
            "last_activity": data["last_off"].strftime("%H:%M") if data["last_off"] else None,
            "switch_on_count": len(data["on_events"]),
            "peak_hour": peak_hour,
            "switches_used": sorted(data["switches_active"]),
            "hourly_breakdown": {str(h): round(m, 0) for h, m in sorted(hourly.items())},
        }

    return result


def build_room_timeline(room_usage, switch_ts, switch_entities, hours=24):
    """Build a chronological room activity timeline.

    Returns list of {time, room, event, switch} ordered by time.
    """
    events = []
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)

    skip_patterns = ["brama", "garaz", "gate", "wentylator", "fan",
                     "pompa", "auto", "pralka", "suszarka", "drzewo",
                     "termometr", "gniazdko"]

    for eid, ts_series in switch_ts.items():
        info = switch_entities.get(eid)
        if not info:
            continue
        fname = info["fname"]
        room = info["room"]

        if any(p in eid.lower() or p in fname.lower() for p in skip_patterns):
            continue

        for ts_str, is_on in ts_series:
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if ts < cutoff:
                continue

            events.append({
                "time": ts.strftime("%H:%M"),
                "timestamp": ts,
                "room": room,
                "event": "ON" if is_on else "OFF",
                "switch": fname,
            })

    events.sort(key=lambda e: e["timestamp"])

    # Convert timestamp to string for serialization
    for e in events:
        e["timestamp"] = e["timestamp"].isoformat(timespec="seconds")

    return events


def load_previous_analysis():
    """Load the most recent correlate output for delta comparison.

    Returns the parsed JSON or None.
    """
    try:
        latest = DATA_DIR / "latest-correlate.json"
        if latest.exists():
            target = latest.resolve()
            with open(target) as f:
                return json.load(f)
    except Exception:
        pass
    return None


def compute_env_deltas(current_stats, previous_report):
    """Compute environmental changes since previous analysis.

    Returns dict of {room: {metric: {prev, now, delta, trend}}}.
    """
    if not previous_report:
        return {}

    prev_stats = previous_report.get("sensor_stats", {})
    prev_time = previous_report.get("generated", "?")
    deltas = {}

    for eid, curr in current_stats.items():
        if eid not in prev_stats:
            continue
        prev = prev_stats[eid]
        room = curr["room"]
        group = curr["group"]

        d_current = curr["current"]
        d_prev = prev.get("current", prev.get("mean"))
        if d_prev is None:
            continue

        delta = d_current - d_prev
        if abs(delta) < 0.01:
            continue

        if room not in deltas:
            deltas[room] = {"since": prev_time}
        deltas[room][group] = {
            "previous": round(d_prev, 1),
            "current": round(d_current, 1),
            "delta": round(delta, 1),
            "trend": "↗" if delta > 0.3 else ("↘" if delta < -0.3 else "→"),
            "unit": curr["unit"],
        }

    return deltas


# ── Ollama LLM ─────────────────────────────────────────────────────────────

def call_ollama(system_prompt, user_prompt, temperature=0.3, max_tokens=2500):
    """Call Ollama for LLM synthesis."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=10) as r:
            tags = json.loads(r.read())
            models = [m["name"] for m in tags.get("models", [])]
            if not any(OLLAMA_MODEL in m for m in models):
                log(f"Model {OLLAMA_MODEL} not found in Ollama")
                return None
    except Exception as e:
        log(f"Ollama health check failed: {e}")
        return None

    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "/nothink\n" + user_prompt},
        ],
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": 24576},
    }).encode()

    req = urllib.request.Request(OLLAMA_CHAT, data=payload, headers={
        "Content-Type": "application/json",
    })

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            result = json.loads(resp.read())
            content = result.get("message", {}).get("content", "")
            elapsed = time.time() - t0
            tokens = result.get("eval_count", len(content.split()))
            tps = tokens / elapsed if elapsed > 0 else 0
            log(f"LLM: {elapsed:.0f}s, {tokens} tok ({tps:.1f} t/s)")
            return content
    except Exception as e:
        log(f"Ollama call failed: {e}")
        return None


# ── Garage car tracker ─────────────────────────────────────────────────────

# Outdoor reference sensor — Dach (roof/attic, unheated, tracks outdoor temp)
OUTDOOR_REF_SENSORS = ["sensor.1000bec2f1_t"]  # Dach sensor (exposed)

def detect_garage_events(all_history):
    """Collect garage door activations + temperature context for LLM analysis.

    Instead of hardcoded thresholds, we collect raw data:
    - Door/gate activation times (merged)
    - Garage temp before, during, and after each event
    - Outdoor reference temperature
    - Temp trend (5-min, 15-min, 30-min after)

    The LLM analyzes the data considering season, outdoor temp, and context
    to determine car_returned / car_left / door_event classification.
    """
    events = []

    # Get door/gate activation times
    door_times = []
    for eid in [GARAGE_DOOR_SWITCH, GATE_SWITCH, GARAGE_AUTO_SWITCH]:
        if eid not in all_history:
            continue
        for p in all_history[eid]:
            state = p.get("state", "").lower()
            ts_str = p.get("last_changed", "")
            if state == "on" and ts_str:  # activation pulse
                try:
                    ts = datetime.fromisoformat(
                        ts_str.replace("+00:00", "").replace("Z", "")
                    ).replace(tzinfo=timezone.utc)
                    door_times.append(ts)
                except Exception:
                    pass

    if not door_times:
        return events

    # Merge close timestamps (gate+door fire within seconds)
    door_times.sort()
    merged = [door_times[0]]
    for t in door_times[1:]:
        if (t - merged[-1]).total_seconds() < GARAGE_EVENT_MERGE_SEC:
            continue  # skip — same physical event
        merged.append(t)

    # Get garage temp history
    if GARAGE_TEMP_SENSOR not in all_history:
        return events

    temp_points = []
    for p in all_history[GARAGE_TEMP_SENSOR]:
        try:
            v = float(p["state"])
            ts_str = p.get("last_changed", "")
            ts = datetime.fromisoformat(
                ts_str.replace("+00:00", "").replace("Z", "")
            ).replace(tzinfo=timezone.utc)
            temp_points.append((ts, v))
        except (ValueError, TypeError, KeyError):
            pass

    if not temp_points:
        return events

    temp_points.sort(key=lambda x: x[0])

    # Get outdoor reference temp history
    outdoor_points = []
    for ref_sensor in OUTDOOR_REF_SENSORS:
        if ref_sensor not in all_history:
            continue
        for p in all_history[ref_sensor]:
            try:
                v = float(p["state"])
                ts_str = p.get("last_changed", "")
                ts = datetime.fromisoformat(
                    ts_str.replace("+00:00", "").replace("Z", "")
                ).replace(tzinfo=timezone.utc)
                outdoor_points.append((ts, v))
            except (ValueError, TypeError, KeyError):
                pass
    outdoor_points.sort(key=lambda x: x[0])

    def temp_at(points, target_ts, tolerance_min=20):
        """Find closest reading within tolerance of target time."""
        best = None
        best_delta = timedelta(minutes=tolerance_min)
        for ts, v in points:
            d = abs(ts - target_ts)
            if d < best_delta:
                best = v
                best_delta = d
        return best

    def temp_series_after(points, target_ts, windows=(5, 15, 30)):
        """Get temp readings at specific minute offsets after target time."""
        result = {}
        for w in windows:
            v = temp_at(points, target_ts + timedelta(minutes=w), tolerance_min=10)
            if v is not None:
                result[f"+{w}min"] = round(v, 1)
        return result

    def temp_max_after(points, target_ts, window_min=30):
        """Find max temp within window after target time."""
        cutoff = target_ts + timedelta(minutes=window_min)
        temps_in_window = [v for ts, v in points if target_ts <= ts <= cutoff]
        return round(max(temps_in_window), 1) if temps_in_window else None

    def temp_min_after(points, target_ts, window_min=30):
        """Find min temp within window after target time."""
        cutoff = target_ts + timedelta(minutes=window_min)
        temps_in_window = [v for ts, v in points if target_ts <= ts <= cutoff]
        return round(min(temps_in_window), 1) if temps_in_window else None

    # Analyze each door event — collect raw data, no classification
    for door_ts in merged:
        temp_before = temp_at(temp_points, door_ts - timedelta(minutes=5), tolerance_min=30)
        outdoor_temp = temp_at(outdoor_points, door_ts, tolerance_min=60)

        if temp_before is None:
            continue

        event = {
            "timestamp": door_ts.isoformat(timespec="seconds"),
            "time_local": door_ts.strftime("%H:%M"),
            "temp_before": round(temp_before, 1),
            "outdoor_temp": outdoor_temp,
            "temp_after": temp_series_after(temp_points, door_ts),
            "temp_peak_30min": temp_max_after(temp_points, door_ts, 30),
            "temp_min_30min": temp_min_after(temp_points, door_ts, 30),
        }

        # Compute simple delta for backward compat + logging
        peak = event["temp_peak_30min"]
        if peak is not None:
            delta = peak - temp_before
            event["delta"] = round(delta, 1)
        else:
            event["delta"] = 0.0

        # LLM will classify — provide a preliminary label based on simple heuristic
        # but mark it as "preliminary" so LLM can override
        delta = event["delta"]
        if delta >= 1.5:
            event["type"] = "car_returned"
            event["preliminary"] = True
            event["detail"] = (
                f"Temp rose {delta:+.1f}°C ({temp_before:.1f}→{peak:.1f}°C) "
                f"within 30min (outdoor: {outdoor_temp}°C)"
            )
        elif delta <= -1.5:
            event["type"] = "car_left"
            event["preliminary"] = True
            event["detail"] = (
                f"Temp dropped {delta:+.1f}°C ({temp_before:.1f}→{peak:.1f}°C) "
                f"(outdoor: {outdoor_temp}°C)"
            )
        else:
            event["type"] = "door_event"
            event["preliminary"] = True
            peak_str = f"{peak:.1f}" if peak else "?"
            event["detail"] = (
                f"Temp change {delta:+.1f}°C ({temp_before:.1f}→"
                f"{peak_str}°C) — "
                f"outdoor: {outdoor_temp}°C — needs LLM analysis"
            )

        events.append(event)

    return events


# ── Signal ─────────────────────────────────────────────────────────────────

def signal_send(msg):
    """Send Signal notification."""
    try:
        payload = json.dumps({
            "jsonrpc": "2.0", "method": "send",
            "params": {
                "account": SIGNAL_FROM,
                "recipient": [SIGNAL_TO],
                "message": msg,
            },
            "id": "ha-correlate",
        })
        req = urllib.request.Request(
            SIGNAL_RPC, data=payload.encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=15)
        log("Signal alert sent")
        return True
    except Exception as e:
        log(f"Signal send failed: {e}")
        return False


# ── Main analysis ──────────────────────────────────────────────────────────

def run_scrape():
    """Collect HA sensor data, compute statistics and correlations. Save raw JSON (no LLM)."""
    t_start = time.time()
    now = datetime.now()
    dt_label = now.strftime("%d %b %Y, %H:%M")
    dt_file = now.strftime("%Y%m%d-%H%M")

    print(f"[{now:%Y-%m-%d %H:%M:%S}] ha-correlate scrape starting")
    print(f"  Analyzing last {HISTORY_HOURS}h of HA sensor data")

    # Load previous analysis for delta comparison
    prev_analysis = load_previous_analysis()
    if prev_analysis:
        log(f"Previous analysis: {prev_analysis.get('generated', '?')}")
    else:
        log("No previous analysis found — first run")

    if not HASS_TOKEN:
        print("ERROR: HASS_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ── Step 1: Discover sensors ──────────────────────────────────────────
    log("Discovering sensors...")
    states = ha_get_all_states()
    if not states:
        log("ERROR: Could not fetch HA states")
        sys.exit(1)

    numeric_sensors = {}    # eid → {fname, unit, room, group, current}
    switch_entities = {}    # eid → {fname, room, current}

    for e in states:
        eid = e["entity_id"]
        s = e["state"]
        attrs = e.get("attributes", {})
        fname = attrs.get("friendly_name", "")
        unit = attrs.get("unit_of_measurement", "")
        room = guess_room(eid, fname)

        # Numeric sensors
        if eid.startswith("sensor."):
            try:
                v = float(s)
            except (ValueError, TypeError):
                continue

            # Skip battery, derived, irrelevant
            if any(x in eid for x in [
                "battery", "bateria", "akandr_", "sun_",
                "backup_", "app_version", "urmet_",
                "parametry", "thermal_comfort",
            ]):
                continue
            if not unit:
                continue

            # Classify into sensor group
            group = None
            for gname, gcfg in SENSOR_GROUPS.items():
                if not re.search(gcfg["unit_pattern"], unit):
                    continue
                if gcfg["id_pattern"] and not re.search(gcfg["id_pattern"], eid.lower()):
                    continue
                if gcfg["exclude_pattern"] and re.search(gcfg["exclude_pattern"], eid.lower()):
                    continue
                group = gname
                break

            if group:
                numeric_sensors[eid] = {
                    "fname": fname, "unit": unit, "room": room,
                    "group": group, "current": v,
                }

        # Switches / fans / lights
        elif eid.startswith(("switch.", "light.")):
            if s in ("on", "off"):
                switch_entities[eid] = {
                    "fname": fname, "room": room, "current": s,
                }

    log(f"Found {len(numeric_sensors)} numeric sensors, {len(switch_entities)} switches")

    # ── Step 2: Fetch history in bulk ─────────────────────────────────────
    log("Fetching sensor histories...")
    # Always include garage tracker entities even if not in discovered lists
    garage_eids = [
        GARAGE_TEMP_SENSOR, GARAGE_HUMIDITY, GARAGE_DOOR_SWITCH,
        GARAGE_AUTO_SWITCH, GATE_SWITCH,
    ]
    all_entity_ids = list(set(
        list(numeric_sensors.keys()) + list(switch_entities.keys()) + garage_eids
    ))

    # HA API has a URL length limit, so batch entity IDs
    BATCH_SIZE = 30
    all_history = {}
    for i in range(0, len(all_entity_ids), BATCH_SIZE):
        batch = all_entity_ids[i:i + BATCH_SIZE]
        batch_history = ha_get_history_bulk(batch, HISTORY_HOURS)
        all_history.update(batch_history)
        time.sleep(0.5)  # rate limit

    log(f"Fetched history for {len(all_history)} entities")

    # ── Step 3: Build time series ─────────────────────────────────────────
    log("Building time series...")
    numeric_ts = {}   # eid → [(ts, value), ...]
    hourly_ts = {}    # eid → [(hour_label, mean_value), ...]
    switch_ts = {}    # eid → [(ts, is_on), ...]

    sparse_sensors = {}   # eid → {fname, room, group, samples, current} (too few samples for stats)

    for eid in numeric_sensors:
        if eid in all_history:
            ts = extract_numeric_timeseries(all_history[eid])
            if len(ts) >= MIN_SAMPLES:
                numeric_ts[eid] = ts
                hourly_ts[eid] = resample_to_hourly(ts, HISTORY_HOURS)
            elif len(ts) >= 1:
                # Too few samples for stats but still report
                info = numeric_sensors[eid]
                vals = [v for _, v in ts]
                sparse_sensors[eid] = {
                    "fname": info["fname"], "room": info["room"],
                    "group": info["group"], "unit": info["unit"],
                    "samples": len(ts), "current": info["current"],
                    "values": vals,
                }

    for eid in switch_entities:
        if eid in all_history:
            ts = extract_onoff_timeseries(all_history[eid])
            if ts:
                switch_ts[eid] = ts

    log(f"Valid series: {len(numeric_ts)} numeric, {len(switch_ts)} switches, "
        f"{len(sparse_sensors)} sparse")

    # ── Step 4: Per-sensor statistics ─────────────────────────────────────
    log("Computing per-sensor statistics...")
    sensor_stats = {}
    all_anomalies = []

    for eid, ts in numeric_ts.items():
        info = numeric_sensors[eid]
        values = [v for _, v in ts]
        mean = statistics.mean(values)
        stdev = statistics.stdev(values) if len(values) > 1 else 0
        vmin, vmax = min(values), max(values)

        # Trend: compare first/last quarter means
        q_len = max(1, len(values) // 4)
        early = statistics.mean(values[:q_len])
        late = statistics.mean(values[-q_len:])
        trend = late - early

        sensor_stats[eid] = {
            "fname": info["fname"],
            "room": info["room"],
            "group": info["group"],
            "unit": info["unit"],
            "current": info["current"],
            "mean": round(mean, 2),
            "stdev": round(stdev, 3),
            "min": round(vmin, 2),
            "max": round(vmax, 2),
            "range": round(vmax - vmin, 2),
            "samples": len(values),
            "trend": round(trend, 2),
            "hourly_pattern": hourly_pattern(ts),
        }

        # Anomaly detection
        anomalies = detect_anomalies(ts, info["fname"])
        if anomalies:
            all_anomalies.extend(anomalies)

    log(f"Computed stats for {len(sensor_stats)} sensors, found {len(all_anomalies)} anomalies")

    # ── Step 5: Duty cycle analysis ───────────────────────────────────────
    log("Analyzing switch duty cycles...")
    duty_cycles = {}
    duty_heatmaps = {}

    for eid, ts in switch_ts.items():
        info = switch_entities[eid]
        dc = compute_duty_cycle(ts, HISTORY_HOURS)
        if dc:
            dc["fname"] = info["fname"]
            dc["room"] = info["room"]
            dc["known_behavior"] = KNOWN_DEVICES.get(eid, {}).get("behavior", "")
            duty_cycles[eid] = dc

            # Compute hourly heatmap for interesting switches
            heatmap = compute_duty_heatmap(ts)
            if heatmap:
                heatmap["fname"] = info["fname"]
                heatmap["room"] = info["room"]
                duty_heatmaps[eid] = heatmap

    log(f"Analyzed {len(duty_cycles)} switch duty cycles, {len(duty_heatmaps)} heatmaps")

    # ── Step 5.5: Room occupancy estimation ────────────────────────────────
    log("Estimating room usage from light activity...")
    room_usage = compute_room_usage(switch_ts, switch_entities, HISTORY_HOURS)
    room_timeline = build_room_timeline(room_usage, switch_ts, switch_entities, HISTORY_HOURS)

    if room_usage:
        log(f"Room activity detected in {len(room_usage)} rooms:")
        for room, usage in sorted(room_usage.items(), key=lambda x: x[1]["lit_hours"], reverse=True):
            log(f"  {room}: {usage['lit_hours']}h lit, {usage['switch_on_count']} switch events")
    else:
        log("No room activity detected")

    # ── Step 5.6: Environmental deltas ────────────────────────────────────
    log("Computing environmental changes since last analysis...")
    env_deltas = compute_env_deltas(sensor_stats, prev_analysis)
    if env_deltas:
        log(f"Environmental changes in {len(env_deltas)} rooms")
    else:
        log("No significant environmental changes (or first run)")

    # ── Step 5.7: Garage car tracker ──────────────────────────────────────
    log("Detecting garage car events...")
    garage_events = detect_garage_events(all_history)
    if garage_events:
        log(f"Detected {len(garage_events)} garage event(s):")
        for ge in garage_events:
            emoji = "🚗" if ge["type"] == "car_returned" else ("🚙💨" if ge["type"] == "car_left" else "🚪")
            log(f"  {emoji} {ge['time_local']} — {ge['type']}: {ge['detail']}")

        # Remove garage temp spikes from anomalies — they're explained by car events
        garage_event_times = set()
        for ge in garage_events:
            try:
                et = datetime.fromisoformat(ge["timestamp"])
                # Mark a ±30min window around each garage event
                for m in range(-10, GARAGE_TEMP_WINDOW_MIN + 5):
                    key = (et + timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M")
                    garage_event_times.add(key[:13])  # match by hour
            except Exception:
                pass

        before = len(all_anomalies)
        all_anomalies = [
            a for a in all_anomalies
            if not ("Garaż" in a.get("label", "") or "Garage" in a.get("label", "")  # type: ignore
                    or "termometr" in a.get("label", "").lower())
            or a["timestamp"][:13] not in garage_event_times
        ]
        if len(all_anomalies) < before:
            log(f"  Filtered {before - len(all_anomalies)} garage anomalies (explained by car events)")
    else:
        log("No garage door events detected")

    # ── Step 6: Cross-correlations ────────────────────────────────────────
    log("Computing cross-correlations...")
    correlations = []

    # Group sensors by room for same-room correlations
    room_sensors = defaultdict(list)
    for eid in hourly_ts:
        room = numeric_sensors[eid]["room"]
        room_sensors[room].append(eid)

    # Same-room cross-correlations (most meaningful)
    for room, eids in room_sensors.items():
        if len(eids) < 2:
            continue
        for i in range(len(eids)):
            for j in range(i + 1, len(eids)):
                eid_a, eid_b = eids[i], eids[j]
                ts_a = hourly_ts[eid_a]
                ts_b = hourly_ts[eid_b]

                # Align timestamps
                keys_a = {k: v for k, v in ts_a}
                keys_b = {k: v for k, v in ts_b}
                common = sorted(set(keys_a.keys()) & set(keys_b.keys()))
                if len(common) < MIN_CORR_SAMPLES:
                    continue

                xs = [keys_a[k] for k in common]
                ys = [keys_b[k] for k in common]

                lag, r = compute_lag_correlation(xs, ys, max_lag_hours=3)
                if r is not None and abs(r) >= CORR_THRESHOLD:
                    info_a = numeric_sensors[eid_a]
                    info_b = numeric_sensors[eid_b]
                    correlations.append({
                        "room": room,
                        "sensor_a": info_a["fname"],
                        "group_a": info_a["group"],
                        "sensor_b": info_b["fname"],
                        "group_b": info_b["group"],
                        "r": round(r, 3),
                        "lag_hours": lag,
                        "n_points": len(common),
                        "interpretation": interpret_correlation(
                            info_a["group"], info_b["group"], r, lag
                        ),
                    })

    # Cross-room temperature correlations
    temp_sensors = [eid for eid, info in numeric_sensors.items()
                    if info["group"] == "temperature" and eid in hourly_ts]
    for i in range(len(temp_sensors)):
        for j in range(i + 1, len(temp_sensors)):
            eid_a, eid_b = temp_sensors[i], temp_sensors[j]
            if numeric_sensors[eid_a]["room"] == numeric_sensors[eid_b]["room"]:
                continue  # already done above

            ts_a = hourly_ts[eid_a]
            ts_b = hourly_ts[eid_b]
            keys_a = {k: v for k, v in ts_a}
            keys_b = {k: v for k, v in ts_b}
            common = sorted(set(keys_a.keys()) & set(keys_b.keys()))
            if len(common) < MIN_CORR_SAMPLES:
                continue

            xs = [keys_a[k] for k in common]
            ys = [keys_b[k] for k in common]
            r = pearson_correlation(xs, ys)
            if r is not None and abs(r) >= CORR_THRESHOLD:
                info_a = numeric_sensors[eid_a]
                info_b = numeric_sensors[eid_b]
                correlations.append({
                    "room": f"{info_a['room']} ↔ {info_b['room']}",
                    "sensor_a": info_a["fname"],
                    "group_a": info_a["group"],
                    "sensor_b": info_b["fname"],
                    "group_b": info_b["group"],
                    "r": round(r, 3),
                    "lag_hours": 0,
                    "n_points": len(common),
                    "interpretation": "cross-room temperature tracking",
                })

    # Sort correlations by absolute r value
    correlations.sort(key=lambda c: abs(c["r"]), reverse=True)
    log(f"Found {len(correlations)} significant correlations (|r| ≥ {CORR_THRESHOLD})")

    # ── Step 7: Build analysis report data ────────────────────────────────
    report = {
        "date": dt_label,
        "date_file": dt_file,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "history_hours": HISTORY_HOURS,
        "sensor_count": len(numeric_ts),
        "sparse_count": len(sparse_sensors),
        "switch_count": len(switch_ts),
        "sensor_stats": sensor_stats,
        "sparse_sensors": sparse_sensors,
        "duty_cycles": duty_cycles,
        "duty_heatmaps": duty_heatmaps,
        "correlations": correlations[:20],  # top 20
        "anomalies": all_anomalies[:30],    # max 30
        "garage_events": garage_events,
        "room_usage": room_usage,
        "room_timeline": room_timeline[:100],  # last 100 events
        "env_deltas": env_deltas,
        "prev_analysis_time": prev_analysis.get("generated") if prev_analysis else None,
    }

    # Save raw intermediate data
    scrape_duration = int(time.time() - t_start)
    raw_data = {
        "scrape_timestamp": now.isoformat(timespec="seconds"),
        "scrape_duration_seconds": scrape_duration,
        "scrape_version": 1,
        "data": report,
        "scrape_errors": [],
    }
    tmp = RAW_CORRELATE_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(raw_data, f, indent=2, default=str)
    tmp.rename(RAW_CORRELATE_FILE)

    log(f"Scrape done: {len(sensor_stats)} sensors, {len(correlations)} correlations, "
        f"{len(all_anomalies)} anomalies ({scrape_duration}s)")


def run_analyze():
    """Load raw data, run LLM synthesis, save final output. Signal on concerns."""
    t_start = time.time()
    dt = datetime.now()
    dt_label = dt.strftime("%d %b %Y, %H:%M")
    dt_file = dt.strftime("%Y%m%d-%H%M")

    print(f"[{dt:%Y-%m-%d %H:%M:%S}] ha-correlate analyze starting")

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not RAW_CORRELATE_FILE.exists():
        log(f"ERROR: Raw data file not found: {RAW_CORRELATE_FILE}")
        log("Run with --scrape-only first.")
        sys.exit(1)

    try:
        with open(RAW_CORRELATE_FILE) as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log(f"ERROR: Failed to read raw data: {e}")
        sys.exit(1)

    scrape_ts = raw.get("scrape_timestamp", "")
    report = raw.get("data", {})

    # Check staleness
    if scrape_ts:
        try:
            scrape_dt = datetime.fromisoformat(scrape_ts)
            age_hours = (dt - scrape_dt).total_seconds() / 3600
            if age_hours > 48:
                log(f"WARNING: Raw data is {age_hours:.0f}h old (scraped {scrape_ts})")
        except ValueError:
            pass

    log(f"Loaded raw data: {report.get('sensor_count', 0)} sensors (scraped {scrape_ts})")

    # Extract data from report
    sensor_stats = report.get("sensor_stats", {})
    sparse_sensors = report.get("sparse_sensors", {})
    duty_cycles = report.get("duty_cycles", {})
    duty_heatmaps = report.get("duty_heatmaps", {})
    correlations = report.get("correlations", [])
    all_anomalies = report.get("anomalies", [])
    garage_events = report.get("garage_events", [])
    room_usage = report.get("room_usage", {})
    room_timeline = report.get("room_timeline", [])
    env_deltas = report.get("env_deltas", {})

    # ── Step 8: LLM synthesis ─────────────────────────────────────────────
    log("Synthesizing insights with LLM...")

    # Build concise data summary for LLM
    summary_parts = []

    # Room temperatures
    room_temps = {}
    for eid, stats in sensor_stats.items():
        if stats["group"] == "temperature":
            room_temps[stats["room"]] = {
                "current": stats["current"],
                "mean": stats["mean"],
                "range": stats["range"],
                "trend": stats["trend"],
            }
    if room_temps:
        summary_parts.append("TEMPERATURES (24h):")
        for room, t in sorted(room_temps.items()):
            trend_arrow = "↗" if t["trend"] > 0.5 else ("↘" if t["trend"] < -0.5 else "→")
            summary_parts.append(
                f"  {room}: {t['current']:.1f}°C now, mean {t['mean']:.1f}°C, "
                f"range {t['range']:.1f}°C, {trend_arrow} {t['trend']:+.1f}°C trend"
            )

    # Air quality — from detailed stats
    aq_stats = {eid: s for eid, s in sensor_stats.items()
                if s["group"] in ("co2", "pm25", "voc")}
    if aq_stats:
        summary_parts.append(f"\nAIR QUALITY ({HISTORY_HOURS}h detailed):")
        for eid, s in sorted(aq_stats.items(), key=lambda x: x[1]["room"]):
            summary_parts.append(
                f"  {s['room']} {s['group']}: {s['current']} {s['unit']}, "
                f"mean {s['mean']}, max {s['max']}, range {s['range']}"
            )

    # Air quality — from sparse sensors (few data points but still valuable)
    aq_sparse = {eid: s for eid, s in sparse_sensors.items()
                 if s["group"] in ("co2", "pm25", "voc", "temperature", "humidity")}
    if aq_sparse:
        summary_parts.append(f"\nSPARSE SENSORS ({HISTORY_HOURS}h, few state changes — values stable):")
        for eid, s in sorted(aq_sparse.items(), key=lambda x: x[1]["room"]):
            vals_str = ", ".join(str(v) for v in s["values"][:5])
            summary_parts.append(
                f"  {s['room']} {s['group']}: current {s['current']} {s['unit']} "
                f"({s['samples']} samples: [{vals_str}])"
            )

    # Garage car tracker — raw data for LLM classification
    if garage_events:
        summary_parts.append("\n🚗 GARAGE DOOR EVENTS (classify using temp + outdoor context):")
        for ge in garage_events:
            parts = [f"  {ge['time_local']} — garage temp before: {ge['temp_before']}°C"]
            if ge.get("outdoor_temp") is not None:
                parts.append(f"outdoor: {ge['outdoor_temp']}°C")
            if ge.get("temp_after"):
                after_str = ", ".join(f"{k}={v}°C" for k, v in ge["temp_after"].items())
                parts.append(f"temp after: {after_str}")
            if ge.get("temp_peak_30min") is not None:
                parts.append(f"peak(30min): {ge['temp_peak_30min']}°C")
            if ge.get("temp_min_30min") is not None:
                parts.append(f"min(30min): {ge['temp_min_30min']}°C")
            parts.append(f"delta: {ge.get('delta', 0):+.1f}°C")
            parts.append(f"(preliminary: {ge.get('type', '?')})")
            summary_parts.append(", ".join(parts))
        summary_parts.append("  NOTE: Classify each event as car_returned/car_left/door_only.")
        summary_parts.append("  Consider: outdoor temp vs garage temp, season, time of day,")
        summary_parts.append("  temp delta relative to outdoor-indoor difference.")
        summary_parts.append("  In warm weather, small deltas are expected even with car movement.")

    # Duty cycles
    interesting_dc = {eid: dc for eid, dc in duty_cycles.items()
                      if dc["toggle_count"] > 0 or dc["duty_cycle"] > 0}
    if interesting_dc:
        summary_parts.append("\nSWITCH DUTY CYCLES (24h):")
        for eid, dc in sorted(interesting_dc.items(), key=lambda x: x[1]["on_pct"], reverse=True):
            known = f" [{dc['known_behavior']}]" if dc["known_behavior"] else ""
            summary_parts.append(
                f"  {dc['fname']}: {dc['on_pct']}% on, "
                f"{dc['toggle_count']} toggles, "
                f"total {dc['total_on_min']:.0f}min on{known}"
            )

    # Duty cycle heatmaps — hourly usage patterns
    if duty_heatmaps:
        # Show heatmaps for switches with >2 toggles (interesting patterns only)
        interesting_hm = {eid: hm for eid, hm in duty_heatmaps.items()
                         if eid in interesting_dc and interesting_dc[eid]["toggle_count"] >= 2}
        if interesting_hm:
            summary_parts.append("\nAPPLIANCE HOURLY HEATMAPS (% on per hour, 0-23):")
            for eid, hm in sorted(interesting_hm.items(),
                                   key=lambda x: len(x[1].get("peak_hours", [])), reverse=True):
                hourly = hm["hourly_pct"]
                # Compact format: show non-zero hours
                active_hours = [f"{h}:{hourly[h]:.0f}%" for h in range(24) if hourly.get(h, 0) > 0]
                if active_hours:
                    summary_parts.append(f"  {hm['fname']} ({hm['room']}):")
                    summary_parts.append(f"    active: {', '.join(active_hours)}")
                    if hm.get("peak_hours"):
                        summary_parts.append(f"    peak hours (>50%): {hm['peak_hours']}")

    # Room usage
    if room_usage:
        summary_parts.append(f"\n🏠 ROOM OCCUPANCY (last {HISTORY_HOURS}h, estimated from lights):")
        for room, usage in sorted(room_usage.items(), key=lambda x: x[1]["lit_hours"], reverse=True):
            first = usage.get("first_activity", "?")
            last = usage.get("last_activity", "?")
            peak = f", peak at {usage['peak_hour']}:00" if usage.get("peak_hour") is not None else ""
            summary_parts.append(
                f"  {room}: {usage['lit_hours']}h lit ({usage['lit_minutes']:.0f}min), "
                f"{usage['switch_on_count']} on-events, "
                f"first {first}, last {last}{peak}"
            )
            if usage.get("switches_used"):
                summary_parts.append(f"    switches: {', '.join(usage['switches_used'])}")

    # Room timeline (activity flow)
    if room_timeline:
        summary_parts.append(f"\n📋 ACTIVITY TIMELINE (last {min(len(room_timeline), 30)} events):")
        for ev in room_timeline[-30:]:
            icon = "🟢" if ev["event"] == "ON" else "⚫"
            summary_parts.append(f"  {icon} {ev['time']} {ev['room']}: {ev['switch']} {ev['event']}")

    # Environmental deltas since last analysis
    if env_deltas:
        prev_time = list(env_deltas.values())[0].get("since", "?")
        summary_parts.append(f"\n📈 ENVIRONMENTAL CHANGES (since {prev_time}):")
        for room, metrics in sorted(env_deltas.items()):
            changes = []
            for metric, d in metrics.items():
                if metric == "since":
                    continue
                changes.append(
                    f"{metric}: {d['previous']}→{d['current']}{d['unit']} ({d['trend']}{d['delta']:+.1f})"
                )
            if changes:
                summary_parts.append(f"  {room}: {'; '.join(changes)}")

    # Correlations
    if correlations:
        summary_parts.append(f"\nCORRELATIONS (|r| ≥ {CORR_THRESHOLD}):")
        for c in correlations[:10]:
            lag_str = f", lag {c['lag_hours']}h" if c['lag_hours'] != 0 else ""
            summary_parts.append(
                f"  {c['room']}: {c['sensor_a']} ↔ {c['sensor_b']}: "
                f"r={c['r']:+.3f}{lag_str} — {c['interpretation']}"
            )

    # Anomalies
    if all_anomalies:
        summary_parts.append(f"\nANOMALIES ({len(all_anomalies)} detected):")
        for a in all_anomalies[:8]:
            summary_parts.append(
                f"  {a['label']}: {a['value']} at {a['timestamp'][:16]} "
                f"(z={a['z_score']:+.1f}, mean={a['mean']})"
            )

    data_summary = "\n".join(summary_parts)

    system_prompt = f"""\
You are ClawdBot, an AI home analyst for a family house in Poland.
Analyze the sensor data below and write a detailed "Home Insights" report.
This analysis covers the last {HISTORY_HOURS}h.

IMPORTANT: Write ONLY in English. No Chinese, no other languages.
Do NOT include any prefix like "sample output" — just start writing the report directly.

Your report MUST include these sections:

## 🏠 Room Activity & Occupancy
- Which rooms were used, for how long (from light activity)
- Activity flow: what sequence of rooms were used throughout the day
- Which rooms were unused — unusual? (e.g., bedroom not used during night = no one home?)
- Estimate household routine from the light patterns

## 📈 Environmental Changes
- Temperature, humidity, CO₂ deltas since last analysis
- What changed and why (e.g., "kitchen temp dropped 2°C — window opened?")
- Track trends over time — is the house warming/cooling overall?

## 💨 Air Quality Assessment
- CO₂ levels per room with context (>1000=concerning, >1500=bad)
- VOC and PM2.5 with health context
- Correlate air quality with room usage (occupied rooms should have higher CO₂)

## 🔗 Correlations & Patterns
- Explain significant sensor correlations in plain language
- Note lag effects (e.g., "CO₂ peaks 2h after lights come on")
- Identify automation patterns vs. human behavior

## 🚗 Garage & Vehicle Activity
- Classify each garage door event as: CAR RETURNED / CAR LEFT / DOOR ONLY
- Use temperature differential logic WITH outdoor temp context:
  * Hot engine parked → garage temp rises ABOVE what outdoor temp alone would cause
  * Car leaving → brief cold air ingress (outdoor air enters), then temp recovers
  * Door-only → button pressed but temp barely changes relative to outdoor baseline
- In WARM weather (outdoor >15°C), temp deltas will be SMALL even with car movement
  because garage and outdoor are similar — look at temp TREND shape, not absolute delta
- In COLD weather (outdoor <5°C), temp changes are dramatic — easier to classify
- Consider time of day: 7-9 AM departures, 16-19 PM returns are typical commute patterns
- The "preliminary" classification in the data is a rough heuristic — override it if your
  analysis of the temperature curve and outdoor context suggests differently

## ⚡ Energy & Efficiency
- Which switches have unusual duty cycles
- Lights left on in unoccupied rooms = energy waste
- Known automation devices (listed below) should NOT be flagged
- Analyze APPLIANCE HOURLY HEATMAPS: identify usage patterns, peak hours, and suggest
  scheduling optimizations (e.g., "dishwasher always runs at 20:00 — consider off-peak 02:00")
- Flag pattern changes: if a switch normally active at certain hours is idle, note it

Formatting rules:
- Use emoji for quick scanning: 🌡️ temp, 💨 air, 💡 lights, ⚡ energy, 📊 pattern, ⚠️ warning, ✅ ok
- Translate Polish sensor names in parentheses
- End with 3-5 actionable recommendations
- Be specific: "open bedroom window at 22:00" not "improve ventilation"
- Write 400-600 words
- NEVER repeat the same observation or sentence — each bullet must be unique
- If a correlation appears in multiple rooms, consolidate into ONE statement

Known devices (do NOT flag as anomalies):
- Garaż termometr (garage heater): auto on when humidity high — dehumidification
- Kuchnia ledy góra: always on — physical wall buttons
- Łazienka piwnica-Wentylator: periodic on/off — gas heater safety ventilation
- Łazienka piętro-Wentylator: always on — humidity-triggered
- drzewo (outdoor tree lights): decorative, expected on/off at dusk/dawn
"""

    llm_analysis = call_ollama(system_prompt, data_summary)

    # Sanitize LLM output — strip Chinese / thinking tokens / artifacts / repetition
    if llm_analysis:
        llm_analysis = sanitize_llm_output(llm_analysis)

    report["llm_analysis"] = llm_analysis

    # Add dual timestamps to report
    report["scrape_timestamp"] = scrape_ts
    report["analyze_timestamp"] = dt.isoformat(timespec="seconds")

    # ── Step 9: Save output ───────────────────────────────────────────────
    output_path = DATA_DIR / f"correlate-{dt_file}.json"
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    log(f"Saved: {output_path}")

    # Update symlink
    latest = DATA_DIR / "latest-correlate.json"
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    latest.symlink_to(output_path.name)

    # Save as a "think" note for the notes system
    if llm_analysis:
        note = {
            "type": "home-insights",
            "title": f"Home Insights — {dt_label}",
            "content": llm_analysis,
            "generated": datetime.now().isoformat(timespec="seconds"),
            "model": OLLAMA_MODEL,
            "context": {
                "sensors": report.get("sensor_count", 0),
                "switches": report.get("switch_count", 0),
                "correlations": len(correlations),
                "anomalies": len(all_anomalies),
                "rooms_active": len(room_usage),
                "env_deltas": len(env_deltas),
                "prev_analysis": report.get("prev_analysis_time"),
            },
        }
        note_fname = f"note-home-insights-{dt_file}.json"
        note_path = THINK_DIR / note_fname
        THINK_DIR.mkdir(parents=True, exist_ok=True)
        with open(note_path, "w") as f:
            json.dump(note, f, indent=2)
        log(f"Saved note: {note_path}")

        # Update notes index
        index_path = THINK_DIR / "notes-index.json"
        index = []
        if index_path.exists():
            try:
                with open(index_path) as f:
                    index = json.load(f)
            except Exception:
                pass
        index.insert(0, {
            "file": note_fname,
            "type": "home-insights",
            "title": f"Home Insights — {dt_label}",
            "generated": note["generated"],
            "chars": len(llm_analysis),
        })
        index = index[:50]
        with open(index_path, "w") as f:
            json.dump(index, f, indent=2)

    # ── Step 10: Send Signal alert (only when important) ─────────────────
    elapsed = time.time() - t_start
    log(f"Total time: {elapsed:.0f}s")

    # Determine if anything is genuinely concerning (worth a Signal ping)
    concerns = []

    # Check air quality — CO₂ > 1200 ppm, VOC > 0.5 mg/m³, PM2.5 > 25 µg/m³
    for eid, s in sensor_stats.items():
        if s["group"] == "co2" and s["current"] > 1200:
            concerns.append(f"⚠️ HIGH CO₂: {s['room']} at {s['current']} ppm")
        elif s["group"] == "voc" and s["current"] > 0.5:
            concerns.append(f"⚠️ HIGH VOC: {s['room']} at {s['current']} {s['unit']}")
        elif s["group"] == "pm25" and s["current"] > 25:
            concerns.append(f"⚠️ HIGH PM2.5: {s['room']} at {s['current']} {s['unit']}")
    for eid, s in sparse_sensors.items():
        if s["group"] == "co2" and s["current"] > 1200:
            concerns.append(f"⚠️ HIGH CO₂: {s['room']} at {s['current']} ppm")
        elif s["group"] == "voc" and s["current"] > 0.5:
            concerns.append(f"⚠️ HIGH VOC: {s['room']} at {s['current']} {s['unit']}")

    # Check for anomalies
    if len(all_anomalies) >= 3:
        concerns.append(f"⚠️ {len(all_anomalies)} anomalies detected")

    # Check for unusual temperature (rooms < 15°C or > 28°C)
    for room, t in room_temps.items():
        if t["current"] < 15 and room not in ("Garaż", "Strych", "Piwnica"):
            concerns.append(f"🥶 COLD: {room} at {t['current']:.0f}°C")
        elif t["current"] > 28:
            concerns.append(f"🔥 HOT: {room} at {t['current']:.0f}°C")

    # Garage events are always interesting (car left/returned)
    for ge in garage_events:
        emoji = "🚗" if ge["type"] == "car_returned" else "🚙💨"
        concerns.append(f"{emoji} {ge['time_local']} {ge['type'].replace('_', ' ')}: {ge['detail']}")

    if concerns:
        # Cooldown: suppress same concern types for 6 hours
        cooldown_path = DATA_DIR / "alert-cooldown.json"
        cooldown_data = {}
        try:
            cooldown_data = json.loads(cooldown_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        now_ts = time.time()
        cooldown_secs = 6 * 3600  # 6 hours
        # Key each concern by its type prefix (emoji + category)
        new_concerns = []
        for c in concerns:
            # Use first ~40 chars as the dedup key (covers emoji + metric + room)
            ckey = c[:40]
            last_sent = cooldown_data.get(ckey, 0)
            if now_ts - last_sent > cooldown_secs:
                new_concerns.append(c)
                cooldown_data[ckey] = now_ts
        # Garage events always pass through (time-specific, never duplicate)
        garage = [c for c in concerns if "🚗" in c or "🚙" in c]
        new_concerns = list(dict.fromkeys(new_concerns + garage))  # dedup, preserve order

        if new_concerns:
            alert_parts = [f"🏠 Home Alert — {dt_label}"]
            alert_parts.extend(new_concerns[:8])
            alert_parts.append(f"\n⏱ {elapsed:.0f}s | Full report on dashboard HOME tab")
            signal_send("\n".join(alert_parts))
            cooldown_path.write_text(json.dumps(cooldown_data))
            log(f"Signal alert sent: {len(new_concerns)} new concerns (of {len(concerns)} total)")
        else:
            log(f"All {len(concerns)} concerns already alerted within 6h — suppressed")
    else:
        log(f"No important concerns — skipping Signal alert (routine data saved to dashboard)")

    print(f"  Done. {len(correlations)} correlations, {len(all_anomalies)} anomalies, "
          f"{len(duty_cycles)} duty cycles, {len(room_usage)} active rooms analyzed.")

    # Regenerate dashboard HTML
    try:
        import subprocess as _sp
        _sp.run(["python3", "/opt/netscan/generate-html.py"],
               capture_output=True, timeout=60)
        print("  Dashboard HTML regenerated")
    except Exception:
        pass


def main():
    """Legacy wrapper / argparse entry point."""
    parser = argparse.ArgumentParser(description="HA sensor time-series correlation analysis")
    parser.add_argument('--scrape-only', action='store_true',
                        help='Only collect HA data and compute stats, save raw (no LLM)')
    parser.add_argument('--analyze-only', action='store_true',
                        help='Only run LLM analysis on previously scraped raw data')
    args = parser.parse_args()

    if args.scrape_only:
        run_scrape()
    elif args.analyze_only:
        run_analyze()
    else:
        # Legacy: full run (backward compatible)
        run_scrape()
        run_analyze()


def interpret_correlation(group_a, group_b, r, lag):
    """Generate a human-readable interpretation of a correlation."""
    pair = frozenset([group_a, group_b])
    sign = "positive" if r > 0 else "inverse"
    lag_str = f" with {abs(lag)}h lag" if lag != 0 else ""

    interpretations = {
        frozenset(["temperature", "humidity"]): (
            f"{'warm air holds more moisture' if r > 0 else 'heating dries air'}{lag_str}"
        ),
        frozenset(["co2", "humidity"]): (
            f"{'occupancy drives both CO₂ and humidity' if r > 0 else 'ventilation reduces both'}{lag_str}"
        ),
        frozenset(["temperature", "co2"]): (
            f"{'body heat + breathing correlation' if r > 0 else 'ventilation cools and clears CO₂'}{lag_str}"
        ),
        frozenset(["pm25", "voc"]): (
            f"{'shared pollution source' if r > 0 else 'different pollution patterns'}{lag_str}"
        ),
        frozenset(["co2", "pm25"]): (
            f"{'occupancy-driven — people generate CO₂, stir up particles' if r > 0 else 'different sources'}{lag_str}"
        ),
    }

    if pair in interpretations:
        return interpretations[pair]

    if group_a == group_b:
        return f"same-type {sign} correlation{lag_str} — shared environmental driver"

    return f"{sign} correlation between {group_a} and {group_b}{lag_str}"


if __name__ == "__main__":
    main()
