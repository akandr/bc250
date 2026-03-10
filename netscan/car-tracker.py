#!/usr/bin/env python3
"""
car-tracker.py — GPS car movement analysis via SinoTrack
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Fetches GPS tracker data from SinoTrack platform and analyzes:
  - Current position and status (parked/moving)
  - Daily mileage statistics and trends
  - Trip detection (start/end, duration, distance, max speed)
  - Movement patterns (frequent locations, departure times)
  - Idle/driving ratio per day

Uses idle GPU time for LLM synthesis of movement patterns.

Output: /opt/netscan/data/car-tracker/
  - car-tracker-YYYYMMDD.json   (daily analysis)
  - latest-car-tracker.json     (symlink to latest)

Cron: via queue-runner nightly batch

Location on bc250: /opt/netscan/car-tracker.py
"""

import argparse
import base64
import hashlib
import json
import math
import os
import random
import statistics
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from llm_sanitize import sanitize_llm_output

# ── Config ─────────────────────────────────────────────────────────────────
OLLAMA_URL = "http://localhost:11434"
OLLAMA_CHAT = f"{OLLAMA_URL}/api/chat"
OLLAMA_MODEL = "qwen3:14b"

DATA_DIR = Path("/opt/netscan/data/car-tracker")
THINK_DIR = Path("/opt/netscan/data/think")

# ── SinoTrack API Config ──────────────────────────────────────────────────
TRACKER_SERVER = os.environ.get("TRACKER_SERVER", "")
TRACKER_API_PATH = "/APP/AppJson.asp"
TRACKER_IMEI = os.environ.get("TRACKER_IMEI", "")
TRACKER_PASSWORD = os.environ.get("TRACKER_PASSWORD", "")

# SinoTrack protocol separators (from JS _xf77b)
_SEP_ROW = "\x11"       # 0x11 DC1
_SEP_TABLE = "\x1b"      # 0x1b ESC

# Analysis parameters
TRACK_DAYS = 3           # days of GPS track to fetch
SPEED_MOVING_THRESHOLD = 3   # km/h — below this = stationary
PARK_MIN_DURATION = 300      # seconds — minimum stop to count as "parked"
CLUSTER_RADIUS_M = 150       # meters — group nearby stops as same location
MILEAGE_HISTORY_DAYS = 14    # days of mileage history to fetch
MIN_TRIP_DISPLACEMENT_M = 1000  # meters — ignore trips that stay within this radius of start (GPS noise)
MIN_TRIP_DURATION_S = 120     # seconds — ignore trips shorter than 2 minutes (GPS glitches)
MIN_TRIP_DISTANCE_KM = 0.3   # km — ignore trips with less than 300m actual distance
STOP_MIN_DURATION = 1800     # seconds — minimum stop to show in stop log (30 min = real parking)
STOP_MERGE_GAP_S = 600       # seconds — merge stops at same location if gap < 10 min (GPS noise tolerance)

# ── Known locations (for labeling) ────────────────────────────────────────
# Load from env var KNOWN_LOCATIONS_JSON or /opt/netscan/car-known-locations.json
_locs_env = os.environ.get("KNOWN_LOCATIONS_JSON", "")
_locs_file = Path("/opt/netscan/car-known-locations.json")
if _locs_env:
    KNOWN_LOCATIONS = json.loads(_locs_env)
elif _locs_file.exists():
    KNOWN_LOCATIONS = json.loads(_locs_file.read_text())
else:
    KNOWN_LOCATIONS = []

TODAY = datetime.now().strftime("%Y%m%d")


# ── SinoTrack API ─────────────────────────────────────────────────────────

def _b64_encode(s):
    """Base64 encode with UTF-8 (matches JS _xf7a0.Encode)."""
    return base64.b64encode(s.encode('utf-8')).decode('ascii')


def _md5_hex(s):
    """MD5 lowercase hex (matches JS _xf7ed)."""
    return hashlib.md5(s.encode('utf-8')).hexdigest()


def _normalize_server(server):
    """Normalize server URL for AppID calculation (matches JS logic)."""
    n = server.lower().replace("http://", "").replace("https://", "")
    while len(n) % 3:
        n += "/"
    return n


def tracker_api_call(cmd, data="", user=""):
    """Make an encoded API call to SinoTrack (replicates JS _xf77e + $.post)."""
    ts = int(time.time() * 1000)
    rand_str = str(int(random.random() * 1e14))
    app_id = _b64_encode(_normalize_server(TRACKER_SERVER))

    # Build token: Cmd + ROW + Data + ROW + Field + ROW + TABLE
    r = cmd + _SEP_ROW + data + _SEP_ROW + "" + _SEP_ROW + _SEP_TABLE
    # Pad to length divisible by 3
    pad_char = str(int(random.random() * 1e14))
    while len(r) % 3:
        r += pad_char[7] if len(pad_char) > 7 else "0"

    token = _b64_encode(r)

    # Sign = md5(timestamp + random + user + appid + token)
    sign_input = str(ts) + rand_str + user + app_id + token
    sign = _md5_hex(sign_input)

    params = urllib.parse.urlencode({
        'strAppID': app_id,
        'strUser': user,
        'nTimeStamp': ts,
        'strRandom': rand_str,
        'strSign': sign,
        'strToken': token,
    }).encode()

    url = TRACKER_SERVER + TRACKER_API_PATH
    req = urllib.request.Request(url, data=params, method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode('utf-8', errors='replace')
            if not text:
                return None
            return json.loads(text)
    except Exception as e:
        log(f"  API call {cmd} failed: {e}")
        return None


def tracker_login():
    """Login via IMEI to SinoTrack. Returns True on success."""
    data = "N'" + TRACKER_IMEI + "',N'" + TRACKER_PASSWORD + "'"
    result = tracker_api_call("Proc_LoginIMEI", data)
    if result and result.get("m_isResultOk") == 1:
        recs = result.get("m_arrRecord", [])
        if recs and recs[0][0] == "1":
            return True
    return False


def tracker_get_position():
    """Get current/last position. Returns dict or None."""
    data = "N'" + TRACKER_IMEI + "'"
    result = tracker_api_call("Proc_GetLastPosition", data, user=TRACKER_IMEI)
    if not result or not result.get("m_arrRecord"):
        return None
    fields = result["m_arrField"]
    rec = result["m_arrRecord"][0]
    return dict(zip(fields, rec))


def tracker_get_track(days=TRACK_DAYS):
    """Get GPS track points for the last N days. Returns list of dicts."""
    now = int(time.time())
    start = now - days * 86400
    data = "N'" + TRACKER_IMEI + "',N'" + str(start) + "',N'" + str(now) + "',N'10000'"
    result = tracker_api_call("Proc_GetTrack", data, user=TRACKER_IMEI)
    if not result or not result.get("m_arrRecord"):
        return []
    fields = result["m_arrField"]
    return [dict(zip(fields, rec)) for rec in result["m_arrRecord"]]


def tracker_get_mileage(days=MILEAGE_HISTORY_DAYS):
    """Get daily mileage for the last N days. Returns list of dicts."""
    now = int(time.time())
    start = now - days * 86400
    data = "N'" + TRACKER_IMEI + "',N'" + str(start) + "',N'" + str(now) + "'"
    result = tracker_api_call("Proc_GetMileageEveryDay", data, user=TRACKER_IMEI)
    if not result or not result.get("m_arrRecord"):
        return []
    fields = result["m_arrField"]
    return [dict(zip(fields, rec)) for rec in result["m_arrRecord"]]


def tracker_get_alarms(days=7):
    """Get alarm events. Returns list of dicts."""
    now = int(time.time())
    start = now - days * 86400
    data = "N'" + TRACKER_IMEI + "',N'" + str(start) + "',N'" + str(now) + "'"
    result = tracker_api_call("Proc_GetTrackAlarm", data, user=TRACKER_IMEI)
    if not result or not result.get("m_arrRecord"):
        return []
    fields = result["m_arrField"]
    return [dict(zip(fields, rec)) for rec in result["m_arrRecord"]]


# ── Geo utilities ──────────────────────────────────────────────────────────

def haversine_m(lat1, lon1, lat2, lon2):
    """Distance in meters between two GPS coordinates."""
    R = 6371000  # Earth radius in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def label_location(lat, lon):
    """Match coordinates to a known location, or reverse-geocode via Nominatim."""
    for loc in KNOWN_LOCATIONS:
        dist = haversine_m(lat, lon, loc["lat"], loc["lon"])
        if dist <= loc["radius_m"]:
            return loc["name"]
    return reverse_geocode(lat, lon)


# ── Reverse geocoding (Nominatim / OpenStreetMap) ─────────────────────────

_GEOCODE_CACHE_FILE = DATA_DIR / "geocode-cache.json"

def _load_geocode_cache():
    """Load persistent geocode cache from disk."""
    try:
        if _GEOCODE_CACHE_FILE.exists():
            with open(_GEOCODE_CACHE_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _save_geocode_cache(cache):
    """Save geocode cache to disk."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(_GEOCODE_CACHE_FILE, "w") as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)
    except Exception as ex:
        print(f"[geocode] WARNING: failed to save cache: {ex}", file=sys.stderr)

_geocode_cache = _load_geocode_cache()

def reverse_geocode(lat, lon):
    """Reverse-geocode coordinates to a human-readable address via Nominatim.
    Returns short address string, or 'lat,lon' on failure.
    Successful results are cached persistently; failures are not cached."""
    key = f"{lat:.4f},{lon:.4f}"
    if key in _geocode_cache:
        return _geocode_cache[key]
    try:
        url = (
            f"https://nominatim.openstreetmap.org/reverse?"
            f"lat={lat}&lon={lon}&format=json&zoom=17&addressdetails=1"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "bc250-car-tracker/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        addr = data.get("address", {})
        # Build a concise address: road + house_number, suburb/neighbourhood, city
        parts = []
        road = addr.get("road", "")
        house = addr.get("house_number", "")
        if road:
            parts.append(f"{road} {house}".strip())
        for field in ("suburb", "neighbourhood", "city_district"):
            if addr.get(field):
                parts.append(addr[field])
                break
        city = addr.get("city") or addr.get("town") or addr.get("village", "")
        if city and city not in parts:
            parts.append(city)
        result = ", ".join(parts) if parts else key
        _geocode_cache[key] = result
        _save_geocode_cache(_geocode_cache)
        time.sleep(1)  # Nominatim rate limit: 1 req/s
        return result
    except Exception as ex:
        print(f"[geocode] WARNING: reverse_geocode({lat},{lon}) failed: {ex}", file=sys.stderr)
        return key


def bearing(lat1, lon1, lat2, lon2):
    """Compass bearing from point 1 to point 2."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    x = math.sin(dlam) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def compass_dir(deg):
    """Convert degrees to compass direction."""
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return dirs[int((deg + 11.25) / 22.5) % 16]


# ── Trip detection ─────────────────────────────────────────────────────────

def detect_trips(track_points):
    """
    Analyze track points to detect distinct trips.

    A trip starts when speed > threshold and ends when parked for > PARK_MIN_DURATION.
    Returns list of trip dicts with start/end times, distance, max speed, etc.
    """
    if not track_points:
        return []

    # Parse and sort by time
    points = []
    for p in track_points:
        try:
            t = int(p.get("nTime", 0))
            lat = float(p.get("dbLat", 0))
            lon = float(p.get("dbLon", 0))
            speed = int(p.get("nSpeed", 0))
            mileage = int(p.get("nMileage", 0))
            if t > 0 and lat != 0 and lon != 0:
                points.append({"t": t, "lat": lat, "lon": lon, "speed": speed, "mileage": mileage})
        except (ValueError, TypeError):
            continue

    points.sort(key=lambda p: p["t"])
    if len(points) < 2:
        return []

    trips = []
    in_trip = False
    trip_start = None
    trip_points = []
    last_moving_time = 0
    max_speed = 0

    for i, pt in enumerate(points):
        is_moving = pt["speed"] > SPEED_MOVING_THRESHOLD

        if not in_trip:
            if is_moving:
                in_trip = True
                trip_start = pt
                trip_points = [pt]
                max_speed = pt["speed"]
                last_moving_time = pt["t"]
        else:
            trip_points.append(pt)
            if is_moving:
                last_moving_time = pt["t"]
                max_speed = max(max_speed, pt["speed"])
            else:
                # Check if we've been stopped long enough to end the trip
                idle_time = pt["t"] - last_moving_time
                if idle_time >= PARK_MIN_DURATION:
                    # End trip at last moving point
                    trip_end = trip_points[-1]
                    # Calculate distance from mileage if available
                    start_mi = trip_start["mileage"]
                    end_mi = trip_end["mileage"]
                    if start_mi > 0 and end_mi > 0 and end_mi >= start_mi:
                        distance_m = (end_mi - start_mi)  # mileage is in meters
                    else:
                        # Sum haversine distances between consecutive points
                        distance_m = 0
                        for j in range(1, len(trip_points)):
                            distance_m += haversine_m(
                                trip_points[j - 1]["lat"], trip_points[j - 1]["lon"],
                                trip_points[j]["lat"], trip_points[j]["lon"]
                            )

                    duration_s = last_moving_time - trip_start["t"]

                    # GPS noise filters
                    max_disp = max(
                        haversine_m(trip_start["lat"], trip_start["lon"], tp["lat"], tp["lon"])
                        for tp in trip_points
                    )
                    distance_km = round(distance_m / 1000, 1)
                    if (max_disp >= MIN_TRIP_DISPLACEMENT_M
                            and duration_s >= MIN_TRIP_DURATION_S
                            and distance_km >= MIN_TRIP_DISTANCE_KM):
                        trips.append({
                            "start_time": trip_start["t"],
                            "end_time": last_moving_time,
                            "start_ts": datetime.fromtimestamp(trip_start["t"]).strftime("%Y-%m-%d %H:%M"),
                            "end_ts": datetime.fromtimestamp(last_moving_time).strftime("%Y-%m-%d %H:%M"),
                            "duration_min": round(duration_s / 60, 1),
                            "distance_km": distance_km,
                            "max_speed_kmh": max_speed,
                            "avg_speed_kmh": round((distance_m / 1000) / (duration_s / 3600), 1) if duration_s > 0 else 0,
                            "start_lat": trip_start["lat"],
                            "start_lon": trip_start["lon"],
                            "end_lat": trip_end["lat"],
                            "end_lon": trip_end["lon"],
                            "start_location": label_location(trip_start["lat"], trip_start["lon"]),
                            "end_location": label_location(trip_end["lat"], trip_end["lon"]),
                            "points": len(trip_points),
                        })

                    in_trip = False
                    trip_points = []
                    max_speed = 0

    # Close any open trip at end of data
    if in_trip and trip_points and last_moving_time > trip_start["t"]:
        trip_end = trip_points[-1]
        start_mi = trip_start["mileage"]
        end_mi = trip_end["mileage"]
        if start_mi > 0 and end_mi > 0 and end_mi >= start_mi:
            distance_m = end_mi - start_mi
        else:
            distance_m = sum(
                haversine_m(trip_points[j - 1]["lat"], trip_points[j - 1]["lon"],
                            trip_points[j]["lat"], trip_points[j]["lon"])
                for j in range(1, len(trip_points))
            )
        duration_s = last_moving_time - trip_start["t"]
        # GPS noise filters
        max_disp = max(
            haversine_m(trip_start["lat"], trip_start["lon"], tp["lat"], tp["lon"])
            for tp in trip_points
        )
        distance_km = round(distance_m / 1000, 1)
        if (max_disp >= MIN_TRIP_DISPLACEMENT_M
                and duration_s >= MIN_TRIP_DURATION_S
                and distance_km >= MIN_TRIP_DISTANCE_KM):
            trips.append({
                "start_time": trip_start["t"],
                "end_time": last_moving_time,
                "start_ts": datetime.fromtimestamp(trip_start["t"]).strftime("%Y-%m-%d %H:%M"),
                "end_ts": datetime.fromtimestamp(last_moving_time).strftime("%Y-%m-%d %H:%M"),
                "duration_min": round(duration_s / 60, 1),
                "distance_km": distance_km,
                "max_speed_kmh": max_speed,
                "avg_speed_kmh": round((distance_m / 1000) / (duration_s / 3600), 1) if duration_s > 0 else 0,
                "start_lat": trip_start["lat"],
                "start_lon": trip_start["lon"],
                "end_lat": trip_end["lat"],
                "end_lon": trip_end["lon"],
                "start_location": label_location(trip_start["lat"], trip_start["lon"]),
                "end_location": label_location(trip_end["lat"], trip_end["lon"]),
                "points": len(trip_points),
            })

    return trips


# ── Stop/parking analysis ─────────────────────────────────────────────────

def detect_stops(track_points):
    """Detect significant parking/stop locations from track data.

    Uses displacement-based stop breaking: a stop is only ended when the car
    actually moves away (displacement > CLUSTER_RADIUS_M from stop center),
    not just because of brief GPS speed spikes. After initial detection,
    nearby consecutive stops with short gaps are merged, and only stops
    >= STOP_MIN_DURATION (30 min) are kept.
    """
    if not track_points:
        return []

    points = []
    for p in track_points:
        try:
            t = int(p.get("nTime", 0))
            lat = float(p.get("dbLat", 0))
            lon = float(p.get("dbLon", 0))
            speed = int(p.get("nSpeed", 0))
            if t > 0:
                points.append({"t": t, "lat": lat, "lon": lon, "speed": speed})
        except (ValueError, TypeError):
            continue

    points.sort(key=lambda p: p["t"])

    # Phase 1: detect raw stops using displacement-based breaking.
    # A speed spike alone doesn't break a stop — the car must actually
    # move away from the stop's average position.
    raw_stops = []
    stop_start = None
    stop_points = []  # only stationary points (for averaging)

    for pt in points:
        if pt["speed"] <= SPEED_MOVING_THRESHOLD:
            if stop_start is None:
                stop_start = pt
            stop_points.append(pt)
        else:
            # Speed spike — check if it's real movement or GPS noise
            if stop_start and stop_points:
                med_lat = statistics.median(p["lat"] for p in stop_points)
                med_lon = statistics.median(p["lon"] for p in stop_points)
                dist_from_stop = haversine_m(med_lat, med_lon, pt["lat"], pt["lon"])
                if dist_from_stop < CLUSTER_RADIUS_M:
                    # Still near the stop center — GPS noise, keep the stop open
                    continue
                # Real movement — close the stop
                duration = stop_points[-1]["t"] - stop_start["t"]
                if duration >= PARK_MIN_DURATION:
                    raw_stops.append({
                        "start_time": stop_start["t"],
                        "end_time": stop_points[-1]["t"],
                        "lat": med_lat,
                        "lon": med_lon,
                        "duration_s": duration,
                    })
            stop_start = None
            stop_points = []

    # Close last stop
    if stop_start and stop_points:
        duration = stop_points[-1]["t"] - stop_start["t"]
        if duration >= PARK_MIN_DURATION:
            med_lat = statistics.median(p["lat"] for p in stop_points)
            med_lon = statistics.median(p["lon"] for p in stop_points)
            raw_stops.append({
                "start_time": stop_start["t"],
                "end_time": stop_points[-1]["t"],
                "lat": med_lat,
                "lon": med_lon,
                "duration_s": duration,
            })

    # Phase 2: merge consecutive stops at the same location with short gaps
    merged = []
    for stop in raw_stops:
        if merged:
            prev = merged[-1]
            gap = stop["start_time"] - prev["end_time"]
            dist = haversine_m(prev["lat"], prev["lon"], stop["lat"], stop["lon"])
            if gap <= STOP_MERGE_GAP_S and dist < CLUSTER_RADIUS_M:
                # Merge: extend previous stop, re-average position weighted by duration
                total_dur = prev["duration_s"] + stop["duration_s"]
                prev["lat"] = (prev["lat"] * prev["duration_s"] + stop["lat"] * stop["duration_s"]) / total_dur
                prev["lon"] = (prev["lon"] * prev["duration_s"] + stop["lon"] * stop["duration_s"]) / total_dur
                prev["end_time"] = stop["end_time"]
                prev["duration_s"] = prev["end_time"] - prev["start_time"]
                continue
        merged.append(stop)

    # Phase 3: filter by STOP_MIN_DURATION and format output
    stops = []
    for s in merged:
        if s["duration_s"] < STOP_MIN_DURATION:
            continue
        avg_lat = round(s["lat"], 6)
        avg_lon = round(s["lon"], 6)
        stops.append({
            "start_time": s["start_time"],
            "end_time": s["end_time"],
            "start_ts": datetime.fromtimestamp(s["start_time"]).strftime("%Y-%m-%d %H:%M"),
            "end_ts": datetime.fromtimestamp(s["end_time"]).strftime("%Y-%m-%d %H:%M"),
            "duration_min": round(s["duration_s"] / 60, 1),
            "lat": avg_lat,
            "lon": avg_lon,
            "location": label_location(avg_lat, avg_lon),
        })

    return stops


def cluster_locations(stops):
    """Group stops by location clusters."""
    clusters = []
    for stop in stops:
        matched = False
        for cluster in clusters:
            dist = haversine_m(stop["lat"], stop["lon"], cluster["lat"], cluster["lon"])
            if dist < CLUSTER_RADIUS_M:
                cluster["visits"] += 1
                cluster["total_minutes"] += stop["duration_min"]
                cluster["all_stops"].append(stop)
                matched = True
                break
        if not matched:
            clusters.append({
                "lat": stop["lat"],
                "lon": stop["lon"],
                "location": stop["location"],
                "visits": 1,
                "total_minutes": stop["duration_min"],
                "all_stops": [stop],
            })

    # Sort by total time spent
    clusters.sort(key=lambda c: c["total_minutes"], reverse=True)
    return clusters


# ── Mileage analysis ──────────────────────────────────────────────────────

def analyze_mileage(mileage_data):
    """Analyze daily mileage trends."""
    entries = []
    for m in mileage_data:
        try:
            t = int(m.get("nTime", 0))
            km = int(m.get("nMileage", 0)) / 1000.0  # mileage in meters -> km
            day = datetime.fromtimestamp(t).strftime("%Y-%m-%d")
            entries.append({"date": day, "timestamp": t, "km": round(km, 1)})
        except (ValueError, TypeError):
            continue

    if not entries:
        return {}

    entries.sort(key=lambda e: e["timestamp"])
    km_values = [e["km"] for e in entries]

    return {
        "daily": entries,
        "total_km": round(sum(km_values), 1),
        "avg_km": round(statistics.mean(km_values), 1) if km_values else 0,
        "max_km": round(max(km_values), 1) if km_values else 0,
        "min_km": round(min(km_values), 1) if km_values else 0,
        "days_tracked": len(entries),
        "zero_days": sum(1 for km in km_values if km < 0.5),
    }


# ── Current status ─────────────────────────────────────────────────────────

def parse_position(pos_data):
    """Parse current position into human-readable status."""
    if not pos_data:
        return None

    try:
        ts = int(pos_data.get("nTime", 0))
        lat = float(pos_data.get("dbLat", 0))
        lon = float(pos_data.get("dbLon", 0))
        speed = int(pos_data.get("nSpeed", 0))
        direction = int(pos_data.get("nDirection", 0))
        mileage = int(pos_data.get("nMileage", 0))
        park_time = int(pos_data.get("nParkTime", 0))
        run_time = int(pos_data.get("nRunTime", 0))
        car_state = int(pos_data.get("nCarState", 0))
        te_state = int(pos_data.get("nTEState", 0))
    except (ValueError, TypeError):
        return None

    now = int(time.time())
    data_age = now - ts if ts > 0 else -1

    status = {
        "timestamp": ts,
        "last_update": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts > 0 else "unknown",
        "data_age_min": round(data_age / 60, 1) if data_age >= 0 else -1,
        "lat": lat,
        "lon": lon,
        "speed_kmh": speed,
        "direction_deg": direction,
        "direction_compass": compass_dir(direction),
        "location": label_location(lat, lon),
        "total_mileage_km": round(mileage / 1000, 1),
        "is_moving": speed > SPEED_MOVING_THRESHOLD,
        "engine_on": bool(car_state & 0x80),  # bit 7 = vibration/engine sensor
        "car_state_raw": car_state,
        "device_state_raw": te_state,
    }

    if run_time > 0:
        status["last_drive_ended"] = datetime.fromtimestamp(run_time).strftime("%Y-%m-%d %H:%M")

    return status


# ── Day-level summary ──────────────────────────────────────────────────────

def daily_summary(trips, stops, mileage_entry=None):
    """Build per-day summaries from trips and stops."""
    days = {}
    for trip in trips:
        day = trip["start_ts"][:10]
        if day not in days:
            days[day] = {"trips": [], "stops": [], "total_km": 0, "total_driving_min": 0, "max_speed": 0}
        days[day]["trips"].append(trip)
        days[day]["total_km"] += trip["distance_km"]
        days[day]["total_driving_min"] += trip["duration_min"]
        days[day]["max_speed"] = max(days[day]["max_speed"], trip["max_speed_kmh"])

    for stop in stops:
        day = stop["start_ts"][:10]
        if day not in days:
            days[day] = {"trips": [], "stops": [], "total_km": 0, "total_driving_min": 0, "max_speed": 0}
        days[day]["stops"].append(stop)

    # Compute per-day parking time
    result = {}
    for day, data in sorted(days.items()):
        total_park = sum(s["duration_min"] for s in data["stops"])
        result[day] = {
            "trip_count": len(data["trips"]),
            "total_km": round(data["total_km"], 1),
            "total_driving_min": round(data["total_driving_min"], 1),
            "total_parked_min": round(total_park, 1),
            "max_speed_kmh": data["max_speed"],
            "trips": data["trips"],
        }

    return result


# ── Drive pattern anomaly detection ────────────────────────────────────────

def detect_drive_anomalies(trips, location_clusters, mileage):
    """Analyze trips for unusual patterns. Returns list of anomaly dicts.

    Flags:
    - Late-night drives (23:00 - 05:00)
    - Unusually long trips (>2h or >100km)
    - New/unknown destinations (not in top clusters)
    - Unusually high speed trips (max > 130 km/h)
    - Gap days (no driving when expected)
    - Weekend vs weekday pattern deviation
    """
    anomalies = []
    if not trips:
        return anomalies

    # Build set of known locations from top clusters
    known_locations = set()
    for c in (location_clusters or [])[:10]:
        known_locations.add(c.get("location", ""))

    # Typical trip stats for baseline
    durations = [t["duration_min"] for t in trips]
    distances = [t["distance_km"] for t in trips]
    speeds = [t["max_speed_kmh"] for t in trips]

    avg_dur = statistics.mean(durations) if durations else 30
    avg_dist = statistics.mean(distances) if distances else 10
    std_dur = statistics.stdev(durations) if len(durations) > 2 else avg_dur
    std_dist = statistics.stdev(distances) if len(distances) > 2 else avg_dist

    for trip in trips:
        trip_anomalies = []

        # Late-night trips (23:00 - 05:00)
        try:
            start_hour = int(trip["start_ts"][11:13])
            if start_hour >= 23 or start_hour < 5:
                trip_anomalies.append(f"late-night start ({start_hour:02d}:00)")
        except (ValueError, IndexError):
            pass

        # Unusually long duration (>2h or >2σ above mean)
        if trip["duration_min"] > 120:
            trip_anomalies.append(f"long duration ({trip['duration_min']:.0f} min)")
        elif std_dur > 0 and trip["duration_min"] > avg_dur + 2 * std_dur:
            trip_anomalies.append(f"unusual duration ({trip['duration_min']:.0f} min, avg={avg_dur:.0f})")

        # Unusually long distance (>100km or >2σ above mean)
        if trip["distance_km"] > 100:
            trip_anomalies.append(f"long distance ({trip['distance_km']:.1f} km)")
        elif std_dist > 0 and trip["distance_km"] > avg_dist + 2 * std_dist:
            trip_anomalies.append(f"unusual distance ({trip['distance_km']:.1f} km, avg={avg_dist:.0f})")

        # High speed
        if trip["max_speed_kmh"] > 130:
            trip_anomalies.append(f"high speed ({trip['max_speed_kmh']} km/h)")

        # Unknown destination
        end_loc = trip.get("end_location", "")
        if end_loc and end_loc not in known_locations and "," in end_loc:
            # Coordinates mean it's not a known place
            trip_anomalies.append(f"new destination ({end_loc})")

        if trip_anomalies:
            anomalies.append({
                "trip": f"{trip['start_ts']} → {trip['end_ts']}",
                "route": f"{trip.get('start_location', '?')} → {trip.get('end_location', '?')}",
                "distance_km": trip["distance_km"],
                "flags": trip_anomalies,
            })

    # Check mileage for gap days (no driving on weekdays)
    if mileage and mileage.get("daily"):
        for entry in mileage["daily"]:
            try:
                d = datetime.strptime(entry["date"], "%Y-%m-%d")
                if d.weekday() < 5 and entry["km"] < 0.5:  # weekday with no driving
                    anomalies.append({
                        "trip": entry["date"],
                        "route": "N/A",
                        "distance_km": 0,
                        "flags": [f"zero-drive weekday ({entry['date']}, {d.strftime('%A')})"],
                    })
            except (ValueError, KeyError):
                pass

    return anomalies


# ── LLM Analysis ──────────────────────────────────────────────────────────

def call_ollama(system_prompt, user_prompt, temperature=0.3, max_tokens=2000):
    """Call Ollama for LLM analysis."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=10) as r:
            tags = json.loads(r.read())
            models = [m["name"] for m in tags.get("models", [])]
            if not any(OLLAMA_MODEL in m for m in models):
                log(f"  Model {OLLAMA_MODEL} not found")
                return None
    except Exception as e:
        log(f"  Ollama health check failed: {e}")
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
            log(f"  LLM: {elapsed:.0f}s, {tokens} tok ({tps:.1f} t/s)")
            return sanitize_llm_output(content)
    except Exception as e:
        log(f"  Ollama call failed: {e}")
        return None


def llm_analyze(status, daily, mileage, location_clusters, trips, drive_anomalies=None):
    """Use LLM to synthesize movement analysis."""
    system = """You are a vehicle movement analyst for a personal car GPS tracker.
Analyze the driving data and provide insights about:
1. Driving patterns (typical commute times, routes, weekend vs weekday)
2. Notable trips (longest, fastest, unusual destinations)
3. Vehicle usage statistics (daily avg, idle days, etc.)
4. Safety observations (speeding, night driving, long continuous drives)
5. Drive pattern anomalies (flagged unusual trips, new destinations, schedule deviations)
6. Any other interesting patterns

IMPORTANT: Respond ONLY in English. Be concise, factual. Use metric units.
The car is located in Łódź, Poland area.
Format: 2-3 paragraphs of analysis, then a bullet list of key findings.
If there are drive anomalies, highlight them with ⚠️ markers."""

    # Build data summary for LLM
    lines = []
    lines.append(f"=== Car Tracker Analysis for {TODAY} ===")
    lines.append("")

    if status:
        lines.append(f"Current: {status.get('location', '?')}, speed={status.get('speed_kmh', 0)} km/h")
        lines.append(f"  Last update: {status.get('last_update', '?')} (age: {status.get('data_age_min', -1):.0f} min)")
        lines.append(f"  Total odometer: {status.get('total_mileage_km', 0):.0f} km")
        if status.get('parked_since'):
            lines.append(f"  Parked since: {status['parked_since']} ({status.get('parked_duration_h', 0):.1f}h)")
        lines.append("")

    if mileage and mileage.get("daily"):
        lines.append("=== Daily Mileage (last 2 weeks) ===")
        for d in mileage["daily"]:
            lines.append(f"  {d['date']}: {d['km']:.1f} km")
        lines.append(f"  Average: {mileage.get('avg_km', 0):.1f} km/day, Total: {mileage.get('total_km', 0):.1f} km")
        lines.append(f"  Zero-drive days: {mileage.get('zero_days', 0)}/{mileage.get('days_tracked', 0)}")
        lines.append("")

    if daily:
        lines.append("=== Daily Trip Summary (last 3 days) ===")
        for day, data in daily.items():
            lines.append(f"  {day}: {data['trip_count']} trips, {data['total_km']:.1f} km, "
                         f"{data['total_driving_min']:.0f} min driving, max {data['max_speed_kmh']} km/h")
        lines.append("")

    if trips:
        lines.append(f"=== Recent Trips ({len(trips)} total) ===")
        for i, t in enumerate(trips[-15:]):  # Last 15 trips
            lines.append(f"  #{i + 1}: {t['start_ts']} -> {t['end_ts']} | "
                         f"{t['start_location']} -> {t['end_location']} | "
                         f"{t['distance_km']} km, {t['duration_min']} min, max {t['max_speed_kmh']} km/h")
        lines.append("")

    if location_clusters:
        lines.append("=== Frequent Locations ===")
        for c in location_clusters[:10]:
            # Handle both in-memory (total_minutes) and JSON-loaded (total_hours) data
            if 'total_minutes' in c:
                hrs = c['total_minutes'] / 60
            else:
                hrs = c.get('total_hours', 0)
            lines.append(f"  {c['location']}: {c['visits']} visits, {hrs:.1f} hrs total")
        lines.append("")

    if drive_anomalies:
        lines.append(f"=== Drive Pattern Anomalies ({len(drive_anomalies)} flagged) ===")
        for a in drive_anomalies[:15]:
            flags = ", ".join(a["flags"])
            lines.append(f"  ⚠️ {a['trip']}: {a['route']} ({a['distance_km']} km) — {flags}")
        lines.append("")

    prompt = "\n".join(lines)
    return call_ollama(system, prompt, temperature=0.4, max_tokens=1500)


# ── Helpers ────────────────────────────────────────────────────────────────

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.rename(path)


# ── Raw data file for scrape/analyze split ─────────────────────────────────
RAW_CAR_FILE = DATA_DIR / "raw-car-tracker.json"


# ── MAIN ───────────────────────────────────────────────────────────────────

# Need urllib.parse for URL encoding in API calls
import urllib.parse


def run_scrape():
    """Steps 1-9: Fetch GPS data, detect trips/stops/clusters. Save raw JSON."""
    dt = datetime.now()
    log("car-tracker scrape starting")
    t_start = time.time()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    scrape_errors = []

    # Step 1: Login
    log("Logging in to SinoTrack (IMEI)...")
    if not tracker_login():
        log("  LOGIN FAILED — aborting")
        sys.exit(1)
    log("  Login OK")

    # Step 2: Get current position
    log("Fetching current position...")
    pos_raw = tracker_get_position()
    status = parse_position(pos_raw)
    if status:
        log(f"  Position: {status['lat']:.5f}, {status['lon']:.5f} ({status['location']})")
        log(f"  Speed: {status['speed_kmh']} km/h, Moving: {status['is_moving']}")
        log(f"  Odometer: {status['total_mileage_km']:.0f} km")
    else:
        log("  WARNING: Could not get position")

    # Step 3: Get track history
    log(f"Fetching {TRACK_DAYS}-day track history...")
    track = tracker_get_track(TRACK_DAYS)
    log(f"  Got {len(track)} track points")

    # Step 4: Get daily mileage
    log(f"Fetching {MILEAGE_HISTORY_DAYS}-day mileage history...")
    mileage_raw = tracker_get_mileage(MILEAGE_HISTORY_DAYS)
    mileage = analyze_mileage(mileage_raw)
    if mileage:
        log(f"  {mileage.get('days_tracked', 0)} days, avg {mileage.get('avg_km', 0):.1f} km/day")

    # Step 5: Get alarms
    log("Fetching alarm history...")
    alarms = tracker_get_alarms(7)
    log(f"  Got {len(alarms)} alarms")

    # Step 6: Detect trips
    log("Detecting trips...")
    trips = detect_trips(track)
    log(f"  Found {len(trips)} trips")
    for t in trips[-5:]:
        log(f"    {t['start_ts']} -> {t['end_ts']}: {t['distance_km']} km, "
            f"max {t['max_speed_kmh']} km/h ({t['start_location']} -> {t['end_location']})")

    # Step 7: Detect stops
    log("Detecting stops...")
    stops = detect_stops(track)
    log(f"  Found {len(stops)} significant stops")

    # Step 8: Cluster locations
    location_clusters = cluster_locations(stops)
    log(f"  {len(location_clusters)} distinct locations")
    for c in location_clusters[:5]:
        log(f"    {c['location']}: {c['visits']} visits, {c['total_minutes']:.0f} min")

    # Step 9: Daily summary
    daily = daily_summary(trips, stops)

    # Step 10: Derive parked_since from track data engine state (bit 7 = vibration sensor)
    if status and not status.get("is_moving") and track:
        # Walk backwards through track points to find when engine last turned off
        engine_off_ts = None
        for p in reversed(track):
            cs = int(p.get("nCarState", 0))
            if cs & 0x80:  # bit 7 = engine/vibration on
                # Next point after this is when engine turned off
                break
            engine_off_ts = int(p.get("nTime", 0))
        if engine_off_ts and engine_off_ts > 0:
            status["parked_since"] = datetime.fromtimestamp(engine_off_ts).strftime("%Y-%m-%d %H:%M")
            status["parked_duration_h"] = round((time.time() - engine_off_ts) / 3600, 1)

    # Save raw intermediate data
    scrape_duration = round(time.time() - t_start, 1)
    raw_data = {
        "scrape_timestamp": dt.isoformat(timespec="seconds"),
        "scrape_duration_seconds": scrape_duration,
        "scrape_version": 1,
        "data": {
            "imei": TRACKER_IMEI,
            "current_status": status,
            "mileage": mileage,
            "trips": trips,
            "stops": [s for s in stops if s["duration_min"] >= 10],
            "location_clusters": [{
                "location": c["location"],
                "lat": c["lat"],
                "lon": c["lon"],
                "visits": c["visits"],
                "total_hours": round(c["total_minutes"] / 60, 1),
            } for c in location_clusters[:20]],
            "daily_summary": daily,
            "alarms": alarms,
            "track_points": len(track),
        },
        "scrape_errors": scrape_errors,
    }
    save_json(RAW_CAR_FILE, raw_data)

    log(f"Scrape done: saved to {RAW_CAR_FILE} ({scrape_duration}s)")


def run_analyze():
    """Load raw data, run LLM analysis, save final output + think note."""
    dt = datetime.now()
    log("car-tracker analyze starting")
    t_start = time.time()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    THINK_DIR.mkdir(parents=True, exist_ok=True)

    # Load raw data
    if not RAW_CAR_FILE.exists():
        log(f"ERROR: Raw data file not found: {RAW_CAR_FILE}")
        print("Run with --scrape-only first to collect GPS data.", file=sys.stderr)
        sys.exit(1)

    try:
        with open(RAW_CAR_FILE) as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log(f"ERROR: Failed to read raw data: {e}")
        sys.exit(1)

    scrape_ts = raw.get("scrape_timestamp", "")
    data = raw.get("data", {})
    status = data.get("current_status")
    mileage = data.get("mileage", {})
    trips = data.get("trips", [])
    stops = data.get("stops", [])
    location_clusters = data.get("location_clusters", [])
    daily = data.get("daily_summary", {})
    alarms = data.get("alarms", [])
    track_points = data.get("track_points", 0)

    # Check staleness
    if scrape_ts:
        try:
            scrape_dt = datetime.fromisoformat(scrape_ts)
            age_hours = (dt - scrape_dt).total_seconds() / 3600
            if age_hours > 48:
                log(f"WARNING: Raw data is {age_hours:.0f}h old (scraped {scrape_ts})")
        except ValueError:
            pass

    log(f"Loaded raw data (scraped {scrape_ts}): {len(trips)} trips")

    # Detect drive pattern anomalies
    log("Detecting drive pattern anomalies...")
    drive_anomalies = detect_drive_anomalies(trips, location_clusters, mileage)
    log(f"  Found {len(drive_anomalies)} anomalies")

    # LLM analysis
    log("Running LLM analysis...")
    llm_text = llm_analyze(status, daily, mileage, location_clusters, trips, drive_anomalies)
    if llm_text:
        log(f"  LLM analysis: {len(llm_text)} chars")
    else:
        log("  LLM analysis skipped/failed")

    # Build output with dual timestamps
    analyze_duration = round(time.time() - t_start, 1)
    output = {
        "generated": dt.isoformat(),
        "imei": data.get("imei", TRACKER_IMEI),
        "current_status": status,
        "mileage": mileage,
        "trips": trips,
        "stops": stops,
        "location_clusters": location_clusters,
        "daily_summary": daily,
        "alarms": alarms,
        "drive_anomalies": drive_anomalies,
        "llm_analysis": llm_text,
        "meta": {
            "scrape_timestamp": scrape_ts,
            "analyze_timestamp": dt.isoformat(timespec="seconds"),
            "timestamp": dt.isoformat(timespec="seconds"),  # backward compat
            "track_points": track_points,
            "track_days": TRACK_DAYS,
            "trip_count": len(trips),
            "stop_count": len(stops),
            "elapsed_s": analyze_duration,
        },
    }

    # Save daily file
    today_str = dt.strftime("%Y%m%d")
    out_path = DATA_DIR / f"car-tracker-{today_str}.json"
    save_json(out_path, output)
    log(f"Saved: {out_path}")

    # Update symlink
    latest = DATA_DIR / "latest-car-tracker.json"
    latest.unlink(missing_ok=True)
    latest.symlink_to(out_path.name)
    log(f"Symlink: {latest}")

    # Save think note for dashboard integration
    think_note = {
        "type": "car-tracker",
        "generated": output["generated"],
        "content": llm_text or "LLM analysis not available",
        "summary": f"{len(trips)} trips, {mileage.get('total_km', 0):.0f} km in {mileage.get('days_tracked', 0)} days",
    }
    note_path = THINK_DIR / f"note-car-tracker-{today_str}.json"
    save_json(note_path, think_note)

    log(f"Analyze done in {analyze_duration:.0f}s")


def main():
    parser = argparse.ArgumentParser(description="Car tracker — GPS movement analysis")
    parser.add_argument('--scrape-only', action='store_true',
                        help='Only fetch GPS data, save raw (no LLM)')
    parser.add_argument('--analyze-only', action='store_true',
                        help='Only run LLM analysis on previously scraped raw data')
    args = parser.parse_args()

    log(f"car-tracker.py starting"
        f"{' (scrape-only)' if args.scrape_only else ' (analyze-only)' if args.analyze_only else ''}")

    if args.scrape_only:
        run_scrape()
    elif args.analyze_only:
        run_analyze()
    else:
        # Legacy: full run (backward compatible)
        run_scrape()
        run_analyze()


if __name__ == "__main__":
    main()
