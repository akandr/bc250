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

# ── Config ─────────────────────────────────────────────────────────────────
OLLAMA_URL = "http://localhost:11434"
OLLAMA_CHAT = f"{OLLAMA_URL}/api/chat"
OLLAMA_MODEL = "huihui_ai/qwen3-abliterated:14b"

HASS_URL = os.environ.get("HASS_URL", "http://homeassistant:8123")
HASS_TOKEN = os.environ.get("HASS_TOKEN", "")

SIGNAL_RPC = "http://127.0.0.1:8080/api/v1/rpc"
SIGNAL_FROM = "+<BOT_PHONE>"
SIGNAL_TO = "+<OWNER_PHONE>"

DATA_DIR = Path("/opt/netscan/data/correlate")
THINK_DIR = Path("/opt/netscan/data/think")
HISTORY_HOURS = 48        # 48h gives more data for slow-updating sensors
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


# ── Ollama LLM ─────────────────────────────────────────────────────────────

def call_ollama(system_prompt, user_prompt, temperature=0.3, max_tokens=1500):
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
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens},
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

def main():
    t_start = time.time()
    now = datetime.now()
    dt_label = now.strftime("%d %b %Y")
    dt_file = now.strftime("%Y%m%d")

    print(f"[{now:%Y-%m-%d %H:%M:%S}] ha-correlate starting")
    print(f"  Analyzing last {HISTORY_HOURS}h of HA sensor data")

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
    all_entity_ids = list(numeric_sensors.keys()) + list(switch_entities.keys())

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

    for eid, ts in switch_ts.items():
        info = switch_entities[eid]
        dc = compute_duty_cycle(ts, HISTORY_HOURS)
        if dc:
            dc["fname"] = info["fname"]
            dc["room"] = info["room"]
            dc["known_behavior"] = KNOWN_DEVICES.get(eid, {}).get("behavior", "")
            duty_cycles[eid] = dc

    log(f"Analyzed {len(duty_cycles)} switch duty cycles")

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
        "correlations": correlations[:20],  # top 20
        "anomalies": all_anomalies[:30],    # max 30
    }

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

    system_prompt = """\
You are ClawdBot, an AI home analyst for a family house in Poland.
Analyze the sensor data below and write a concise "Home Insights" report.

IMPORTANT: Write ONLY in English. No Chinese, no other languages.
Do NOT include any prefix like "sample output" — just start writing the report directly.

Rules:
- Focus on actionable findings: what's unusual, what patterns emerge, what could be improved
- Explain correlations in plain language (e.g., "bedroom CO₂ rises after 22:00 as occupants sleep")
- Note if duty cycles seem abnormal or wasteful
- Compare temperatures across rooms — any that stand out?
- Air quality: flag any concerning levels with context
- Note interesting temporal patterns (day vs. night differences)
- Use emoji for quick scanning: 🌡️ temp, 💨 air, 💡 lights, ⚡ energy, 📊 pattern, ⚠️ warning, ✅ ok
- Known devices that are automation-controlled should not be flagged as anomalies
- Translate Polish sensor names in parentheses
- End with 2-3 actionable recommendations
- Keep under 500 words
"""

    llm_analysis = call_ollama(system_prompt, data_summary)

    # Sanitize LLM output — strip Chinese prefix / 'sample output' artifacts
    if llm_analysis:
        # Remove any leading Chinese characters or non-ASCII junk
        llm_analysis = re.sub(r'^[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef：:]+\s*', '', llm_analysis)
        llm_analysis = re.sub(r'^(?:sample output|example output)[:\s]*', '', llm_analysis, flags=re.IGNORECASE)
        llm_analysis = llm_analysis.strip()

    report["llm_analysis"] = llm_analysis

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
                "sensors": len(numeric_ts),
                "switches": len(switch_ts),
                "correlations": len(correlations),
                "anomalies": len(all_anomalies),
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

    # ── Step 10: Send Signal alert ────────────────────────────────────────
    elapsed = time.time() - t_start
    log(f"Total time: {elapsed:.0f}s")

    # Build compact Signal alert
    alert_parts = [f"📊 Home Insights — {dt_label}"]

    # Top findings
    if room_temps:
        temps = [f"{r}: {t['current']:.0f}°C" for r, t in sorted(room_temps.items())]
        alert_parts.append(f"🌡️ {', '.join(temps[:5])}")

    if correlations:
        alert_parts.append(f"🔗 {len(correlations)} correlations found")
        top = correlations[0]
        alert_parts.append(f"   Top: {top['sensor_a']} ↔ {top['sensor_b']} (r={top['r']:+.2f})")

    if all_anomalies:
        alert_parts.append(f"⚠️ {len(all_anomalies)} anomalies detected")

    interesting_switches = [dc for dc in duty_cycles.values()
                           if dc["toggle_count"] >= 3 and not dc["known_behavior"]]
    if interesting_switches:
        alert_parts.append(
            f"⚡ Active switches: " +
            ", ".join(f"{s['fname']} ({s['on_pct']:.0f}%)" for s in interesting_switches[:3])
        )

    alert_parts.append(f"\n⏱ {elapsed:.0f}s, {len(numeric_ts)} sensors, {len(switch_ts)} switches")

    signal_send("\n".join(alert_parts))

    print(f"  Done. {len(correlations)} correlations, {len(all_anomalies)} anomalies, "
          f"{len(duty_cycles)} duty cycles analyzed.")


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
