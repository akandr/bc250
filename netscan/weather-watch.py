#!/usr/bin/env python3
"""
weather-watch.py — Weather forecast + HA sensor correlation + air quality forecasting
OpenMeteo free API (no key needed), correlate with Home Assistant sensors,
predict heating needs, alert on severe weather for Łódź.
Also does air quality forecasting: correlate CO2/PM2.5/VOC with weather conditions.
"""

import argparse
import json
import os
import re
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
OLLAMA_MODEL = "huihui_ai/qwen3-abliterated:14b"
OLLAMA_CHAT = f"{OLLAMA_URL}/api/chat"

HASS_URL = os.environ.get("HASS_URL", "http://homeassistant:8123")
HASS_TOKEN = os.environ.get("HASS_TOKEN", "")
if not HASS_TOKEN:
    env_file = Path.home() / ".openclaw" / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("HASS_TOKEN="):
                HASS_TOKEN = line.split("=", 1)[1].strip().strip('"')

SIGNAL_RPC = "http://127.0.0.1:8080/api/v1/rpc"
SIGNAL_FROM = os.environ.get("SIGNAL_ACCOUNT", "+<BOT_PHONE>")
SIGNAL_TO = os.environ.get("SIGNAL_OWNER", "+<OWNER_PHONE>")

DATA_DIR = Path("/opt/netscan/data/weather")
THINK_DIR = Path("/opt/netscan/data/think")

# Łódź coordinates
LAT = REDACTED_LAT
LON = REDACTED_LON

TODAY = datetime.now().strftime("%Y%m%d")
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

# ── OpenMeteo API URLs ─────────────────────────────────────────────────────

FORECAST_URL = (
    f"https://api.open-meteo.com/v1/forecast?"
    f"latitude={LAT}&longitude={LON}"
    f"&hourly=temperature_2m,relative_humidity_2m,apparent_temperature,"
    f"precipitation_probability,precipitation,rain,snowfall,"
    f"wind_speed_10m,wind_gusts_10m,wind_direction_10m,"
    f"cloud_cover,visibility,uv_index,surface_pressure,"
    f"soil_temperature_0cm,soil_moisture_0_to_1cm"
    f"&daily=temperature_2m_max,temperature_2m_min,apparent_temperature_max,"
    f"apparent_temperature_min,precipitation_sum,rain_sum,snowfall_sum,"
    f"precipitation_hours,sunrise,sunset,uv_index_max,"
    f"wind_speed_10m_max,wind_gusts_10m_max,wind_direction_10m_dominant"
    f"&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
    f"is_day,precipitation,rain,snowfall,wind_speed_10m,wind_gusts_10m,"
    f"wind_direction_10m,cloud_cover,surface_pressure"
    f"&timezone=Europe%2FWarsaw&forecast_days=7"
)

AIR_QUALITY_URL = (
    f"https://air-quality-api.open-meteo.com/v1/air-quality?"
    f"latitude={LAT}&longitude={LON}"
    f"&hourly=pm10,pm2_5,carbon_monoxide,nitrogen_dioxide,sulphur_dioxide,"
    f"ozone,alder_pollen,birch_pollen,grass_pollen,mugwort_pollen,"
    f"european_aqi,european_aqi_pm2_5,european_aqi_pm10,"
    f"european_aqi_nitrogen_dioxide,european_aqi_ozone"
    f"&timezone=Europe%2FWarsaw&forecast_days=3"
)

# ── HA sensor mappings ─────────────────────────────────────────────────────

# Indoor sensors for temperature/humidity comparison with outdoor forecast
HA_INDOOR_TEMP_SENSORS = {
    "sensor.air_detector_2_temperatura": "Chłopaki (kids room)",
    "sensor.powietrze_sypialnia_temperatura": "Sypialnia (bedroom)",
    "sensor.1000becdc2_t": "Garaż",
    "sensor.1000bec547_t": "Górna belka (upper beam)",
}

HA_INDOOR_HUMIDITY_SENSORS = {
    "sensor.air_detector_2_wilgotnosc": "Chłopaki (kids room)",
    "sensor.powietrze_sypialnia_wilgotnosc": "Sypialnia (bedroom)",
    "sensor.1000becdc2_h": "Garaż",
    "sensor.1000bec547_h": "Górna belka (upper beam)",
}

HA_AIR_QUALITY_SENSORS = {
    "sensor.air_detector_2_dwutlenek_wegla": ("Chłopaki", "co2"),
    "sensor.powietrze_sypialnia_dwutlenek_wegla": ("Sypialnia", "co2"),
    "sensor.air_detector_2_pm2_5": ("Chłopaki", "pm25"),
    "sensor.powietrze_sypialnia_pm2_5": ("Sypialnia", "pm25"),
    "sensor.air_detector_2_lotne_zwiazki_organiczne": ("Chłopaki", "voc"),
    "sensor.powietrze_sypialnia_lotne_zwiazki_organiczne": ("Sypialnia", "voc"),
}

HA_OUTDOOR_TEMP = "sensor.1000bec2f1_t"

# Severe weather thresholds
SEVERE_WIND_KMH = 60
SEVERE_RAIN_MM = 20  # hourly
SEVERE_SNOW_CM = 5   # hourly
FREEZE_THRESHOLD = -10  # °C
HEAT_THRESHOLD = 33    # °C

# Heating analysis: typical indoor comfort range
COMFORT_TEMP_MIN = 20.0
COMFORT_TEMP_MAX = 24.0
HEATING_OUTDOOR_THRESHOLD = 12.0  # below this, heating likely needed

# ── Helpers ────────────────────────────────────────────────────────────────

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def fetch_json(url, timeout=30):
    """Fetch JSON from URL."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log(f"  Fetch failed {url[:60]}...: {e}")
        return None


def ha_api_get(path):
    """GET from Home Assistant REST API."""
    if not HASS_TOKEN:
        return None
    url = f"{HASS_URL}{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {HASS_TOKEN}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log(f"  HA API error: {e}")
        return None


def ha_get_sensor_value(entity_id):
    """Get current numeric value of an HA sensor."""
    data = ha_api_get(f"/api/states/{entity_id}")
    if data and data.get("state") not in ("unknown", "unavailable", None):
        try:
            return float(data["state"])
        except (ValueError, TypeError):
            return None
    return None


def ha_get_history(entity_id, hours=24):
    """Get sensor history for the past N hours."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    # Use naive ISO format — some HA versions reject timezone-aware timestamps
    start_str = start.strftime("%Y-%m-%dT%H:%M:%S")
    end_str = end.strftime("%Y-%m-%dT%H:%M:%S")
    path = f"/api/history/period/{start_str}?filter_entity_id={entity_id}&end_time={end_str}&minimal_response"
    data = ha_api_get(path)
    if not data:
        # Retry without minimal_response
        path = f"/api/history/period/{start_str}?filter_entity_id={entity_id}&end_time={end_str}"
        data = ha_api_get(path)
    if data and len(data) > 0:
        points = []
        for entry in data[0]:
            try:
                val = float(entry["state"])
                ts = entry.get("last_changed", entry.get("last_updated", ""))
                points.append((ts, val))
            except (ValueError, TypeError, KeyError):
                continue
        return points
    return []


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.rename(path)


def call_ollama(system_prompt, user_prompt, temperature=0.3, max_tokens=2500):
    """Call Ollama LLM."""
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
        "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": 12288},
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
            log(f"  LLM: {elapsed:.0f}s, {tokens} tok")
            return sanitize_llm_output(content)
    except Exception as e:
        log(f"  Ollama call failed: {e}")
        return None


def signal_send(msg):
    """Send Signal message."""
    payload = json.dumps({
        "jsonrpc": "2.0", "method": "send", "id": 1,
        "params": {
            "account": SIGNAL_FROM,
            "recipient": [SIGNAL_TO],
            "message": msg,
        }
    }).encode()
    req = urllib.request.Request(SIGNAL_RPC, data=payload, headers={
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
            log("  Signal alert sent")
    except Exception as e:
        log(f"  Signal send failed: {e}")


def wind_direction_label(deg):
    """Convert wind direction degrees to compass label."""
    if deg is None:
        return "?"
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    idx = int((deg + 11.25) / 22.5) % 16
    return dirs[idx]


# ── Data Collection ────────────────────────────────────────────────────────

def fetch_forecast():
    """Fetch 7-day forecast from OpenMeteo."""
    log("Fetching OpenMeteo forecast...")
    data = fetch_json(FORECAST_URL)
    if not data:
        log("  Failed to fetch forecast")
        return None

    log(f"  Got forecast: {len(data.get('hourly', {}).get('time', []))} hourly points, "
        f"{len(data.get('daily', {}).get('time', []))} daily points")
    return data


def fetch_air_quality():
    """Fetch air quality forecast from OpenMeteo."""
    log("Fetching OpenMeteo air quality...")
    data = fetch_json(AIR_QUALITY_URL)
    if not data:
        log("  Failed to fetch air quality")
        return None

    log(f"  Got AQ forecast: {len(data.get('hourly', {}).get('time', []))} hourly points")
    return data


def collect_ha_sensors():
    """Collect current HA sensor readings."""
    log("Collecting HA sensor readings...")
    readings = {
        "indoor_temp": {},
        "indoor_humidity": {},
        "air_quality": {},
        "outdoor_temp": None,
        "history": {},
    }

    # Current indoor temperatures
    for eid, room in HA_INDOOR_TEMP_SENSORS.items():
        val = ha_get_sensor_value(eid)
        if val is not None:
            readings["indoor_temp"][room] = val
            log(f"  {room} temp: {val}°C")

    # Current indoor humidity
    for eid, room in HA_INDOOR_HUMIDITY_SENSORS.items():
        val = ha_get_sensor_value(eid)
        if val is not None:
            readings["indoor_humidity"][room] = val

    # CO2 / air quality sensors
    for eid, (room, metric) in HA_AIR_QUALITY_SENSORS.items():
        val = ha_get_sensor_value(eid)
        if val is not None:
            readings["air_quality"][f"{room}_{metric}"] = val
            log(f"  {room} {metric}: {val}")

    # Current outdoor temp
    readings["outdoor_temp"] = ha_get_sensor_value(HA_OUTDOOR_TEMP)
    if readings["outdoor_temp"] is not None:
        log(f"  Outdoor temp (HA sensor): {readings['outdoor_temp']}°C")

    # 24h history for key sensors (for trend correlation)
    for eid, room in HA_INDOOR_TEMP_SENSORS.items():
        hist = ha_get_history(eid, hours=24)
        if hist:
            readings["history"][f"{room}_temp"] = hist

    for eid, (room, metric) in HA_AIR_QUALITY_SENSORS.items():
        hist = ha_get_history(eid, hours=24)
        if hist:
            readings["history"][f"{room}_{metric}"] = hist

    outdoor_hist = ha_get_history(HA_OUTDOOR_TEMP, hours=24)
    if outdoor_hist:
        readings["history"]["outdoor_temp"] = outdoor_hist

    log(f"  Collected {len(readings['indoor_temp'])} temp, "
        f"{len(readings['indoor_humidity'])} humidity, "
        f"{len(readings['air_quality'])} AQ sensors, "
        f"{len(readings['history'])} history series")
    return readings


# ── Analysis ───────────────────────────────────────────────────────────────

def detect_severe_weather(forecast):
    """Detect severe weather conditions in the forecast."""
    alerts = []
    if not forecast:
        return alerts

    hourly = forecast.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    wind_speed = hourly.get("wind_speed_10m", [])
    wind_gusts = hourly.get("wind_gusts_10m", [])
    precip = hourly.get("precipitation", [])
    rain = hourly.get("rain", [])
    snow = hourly.get("snowfall", [])
    visibility = hourly.get("visibility", [])

    for i, t in enumerate(times[:72]):  # Next 72 hours
        hour_label = t.replace("T", " ")

        # Extreme wind
        if i < len(wind_gusts) and wind_gusts[i] and wind_gusts[i] >= SEVERE_WIND_KMH:
            alerts.append({
                "type": "wind",
                "severity": "high" if wind_gusts[i] >= 80 else "medium",
                "time": hour_label,
                "detail": f"Wind gusts {wind_gusts[i]:.0f} km/h",
            })

        # Heavy rain
        if i < len(rain) and rain[i] and rain[i] >= SEVERE_RAIN_MM:
            alerts.append({
                "type": "heavy_rain",
                "severity": "high" if rain[i] >= 40 else "medium",
                "time": hour_label,
                "detail": f"Rain {rain[i]:.1f} mm/h",
            })

        # Heavy snow
        if i < len(snow) and snow[i] and snow[i] >= SEVERE_SNOW_CM:
            alerts.append({
                "type": "heavy_snow",
                "severity": "high" if snow[i] >= 10 else "medium",
                "time": hour_label,
                "detail": f"Snowfall {snow[i]:.1f} cm/h",
            })

        # Extreme cold
        if i < len(temps) and temps[i] is not None and temps[i] <= FREEZE_THRESHOLD:
            alerts.append({
                "type": "extreme_cold",
                "severity": "high" if temps[i] <= -15 else "medium",
                "time": hour_label,
                "detail": f"Temperature {temps[i]:.1f}°C",
            })

        # Extreme heat
        if i < len(temps) and temps[i] is not None and temps[i] >= HEAT_THRESHOLD:
            alerts.append({
                "type": "extreme_heat",
                "severity": "high" if temps[i] >= 37 else "medium",
                "time": hour_label,
                "detail": f"Temperature {temps[i]:.1f}°C",
            })

        # Poor visibility
        if i < len(visibility) and visibility[i] is not None and visibility[i] < 500:
            alerts.append({
                "type": "fog",
                "severity": "medium",
                "time": hour_label,
                "detail": f"Visibility {visibility[i]:.0f}m",
            })

    # Deduplicate: keep first occurrence of each type per severity
    seen = set()
    deduped = []
    for a in alerts:
        key = (a["type"], a["severity"])
        if key not in seen:
            seen.add(key)
            deduped.append(a)

    return deduped


def analyze_heating_needs(forecast, ha_sensors):
    """Predict heating requirements based on forecast + current indoor temps."""
    if not forecast:
        return None

    hourly = forecast.get("hourly", {})
    times = hourly.get("time", [])
    outdoor_temps = hourly.get("temperature_2m", [])

    indoor_temps = ha_sensors.get("indoor_temp", {})
    avg_indoor = statistics.mean(indoor_temps.values()) if indoor_temps else None

    # Next 48 hours of outdoor temps
    forecast_48h = []
    for i, t in enumerate(times[:48]):
        if i < len(outdoor_temps) and outdoor_temps[i] is not None:
            forecast_48h.append({
                "time": t,
                "outdoor_temp": outdoor_temps[i],
            })

    if not forecast_48h:
        return None

    # Find coldest period
    coldest = min(forecast_48h, key=lambda x: x["outdoor_temp"])
    avg_outdoor_48h = statistics.mean(p["outdoor_temp"] for p in forecast_48h)

    # Hours below heating threshold
    cold_hours = sum(1 for p in forecast_48h if p["outdoor_temp"] < HEATING_OUTDOOR_THRESHOLD)

    # Estimate heat loss: simplified — if outdoor drops 10°C below indoor,
    # heating needs to compensate roughly proportionally
    heat_deficit = 0
    for p in forecast_48h:
        if avg_indoor and p["outdoor_temp"] < avg_indoor:
            heat_deficit += (avg_indoor - p["outdoor_temp"])

    return {
        "avg_indoor_now": round(avg_indoor, 1) if avg_indoor else None,
        "avg_outdoor_48h": round(avg_outdoor_48h, 1),
        "coldest_point": {
            "time": coldest["time"],
            "temp": coldest["outdoor_temp"],
        },
        "cold_hours": cold_hours,
        "heat_deficit_degree_hours": round(heat_deficit, 0),
        "heating_intensity": (
            "high" if cold_hours > 30 and avg_outdoor_48h < 0 else
            "medium" if cold_hours > 20 or avg_outdoor_48h < 5 else
            "low" if cold_hours > 8 else "minimal"
        ),
    }


def analyze_air_quality_correlation(air_quality, ha_sensors):
    """Correlate outdoor AQ forecast with indoor sensor readings."""
    if not air_quality:
        return None

    hourly = air_quality.get("hourly", {})
    times = hourly.get("time", [])
    pm25_forecast = hourly.get("pm2_5", [])
    pm10_forecast = hourly.get("pm10", [])
    no2_forecast = hourly.get("nitrogen_dioxide", [])
    ozone_forecast = hourly.get("ozone", [])
    aqi_forecast = hourly.get("european_aqi", [])
    pollen = {
        "birch": hourly.get("birch_pollen", []),
        "grass": hourly.get("grass_pollen", []),
        "alder": hourly.get("alder_pollen", []),
        "mugwort": hourly.get("mugwort_pollen", []),
    }

    # Current indoor CO2 readings
    indoor_co2 = {}
    for key, val in ha_sensors.get("air_quality", {}).items():
        if "co2" in key:
            room = key.replace("_co2", "")
            indoor_co2[room] = val

    # Next 24h AQ summary
    aq_24h = []
    for i in range(min(24, len(times))):
        entry = {"time": times[i]}
        if i < len(pm25_forecast) and pm25_forecast[i] is not None:
            entry["pm25"] = pm25_forecast[i]
        if i < len(pm10_forecast) and pm10_forecast[i] is not None:
            entry["pm10"] = pm10_forecast[i]
        if i < len(aqi_forecast) and aqi_forecast[i] is not None:
            entry["aqi"] = aqi_forecast[i]
        if i < len(ozone_forecast) and ozone_forecast[i] is not None:
            entry["ozone"] = ozone_forecast[i]
        aq_24h.append(entry)

    # Find worst AQ hours
    worst_pm25 = max((e.get("pm25", 0) for e in aq_24h), default=0)
    worst_aqi = max((e.get("aqi", 0) for e in aq_24h), default=0)

    # Pollen forecast peaks (next 24h)
    pollen_peaks = {}
    for name, values in pollen.items():
        if values:
            peak = max(values[:24]) if len(values) >= 24 else max(values) if values else 0
            if peak and peak > 10:
                pollen_peaks[name] = peak

    # Ventilation recommendation
    # Best hour to open windows: low PM2.5 + moderate temp
    best_ventilation_hours = []
    for i, entry in enumerate(aq_24h):
        pm = entry.get("pm25", 999)
        aqi = entry.get("aqi", 999)
        if pm < 15 and aqi < 30:
            best_ventilation_hours.append(entry["time"])

    return {
        "indoor_co2": indoor_co2,
        "aq_24h_summary": {
            "worst_pm25": worst_pm25,
            "worst_aqi": worst_aqi,
            "avg_pm25": round(statistics.mean(e.get("pm25", 0) for e in aq_24h if e.get("pm25")), 1) if any(e.get("pm25") for e in aq_24h) else None,
        },
        "pollen_peaks": pollen_peaks,
        "best_ventilation_hours": best_ventilation_hours[:5],
        "ventilation_advice": (
            "avoid" if worst_pm25 > 35 or worst_aqi > 60 else
            "limited" if worst_pm25 > 25 or worst_aqi > 40 else
            "good" if worst_pm25 < 15 else "moderate"
        ),
        "aq_hourly": aq_24h,
    }


def build_summary_text(forecast, air_quality, ha_sensors, severe_alerts,
                       heating, aq_correlation):
    """Build text summary for LLM analysis."""
    lines = []
    now = datetime.now()
    lines.append(f"=== Weather & Environment Report — {now.strftime('%Y-%m-%d %H:%M')} ===")
    lines.append(f"Location: Łódź, Poland ({LAT}°N, {LON}°E)")
    lines.append("")

    # Current conditions
    if forecast and forecast.get("current"):
        c = forecast["current"]
        lines.append("=== CURRENT CONDITIONS ===")
        lines.append(f"  Temperature: {c.get('temperature_2m', '?')}°C (feels like {c.get('apparent_temperature', '?')}°C)")
        lines.append(f"  Humidity: {c.get('relative_humidity_2m', '?')}%")
        lines.append(f"  Wind: {c.get('wind_speed_10m', '?')} km/h {wind_direction_label(c.get('wind_direction_10m'))}")
        lines.append(f"    gusts: {c.get('wind_gusts_10m', '?')} km/h")
        lines.append(f"  Cloud cover: {c.get('cloud_cover', '?')}%")
        lines.append(f"  Precipitation: {c.get('precipitation', 0)} mm, rain: {c.get('rain', 0)} mm")
        lines.append(f"  Pressure: {c.get('surface_pressure', '?')} hPa")
        lines.append(f"  Daylight: {'yes' if c.get('is_day') else 'no'}")
        lines.append("")

    # Daily forecast
    if forecast and forecast.get("daily"):
        d = forecast["daily"]
        lines.append("=== 7-DAY FORECAST ===")
        for i, day in enumerate(d.get("time", [])[:7]):
            tmax = d.get("temperature_2m_max", [None])[i] if i < len(d.get("temperature_2m_max", [])) else None
            tmin = d.get("temperature_2m_min", [None])[i] if i < len(d.get("temperature_2m_min", [])) else None
            precip = d.get("precipitation_sum", [0])[i] if i < len(d.get("precipitation_sum", [])) else 0
            wind_max = d.get("wind_speed_10m_max", [0])[i] if i < len(d.get("wind_speed_10m_max", [])) else 0
            sunrise = d.get("sunrise", [""])[i] if i < len(d.get("sunrise", [])) else ""
            sunset = d.get("sunset", [""])[i] if i < len(d.get("sunset", [])) else ""
            snow = d.get("snowfall_sum", [0])[i] if i < len(d.get("snowfall_sum", [])) else 0

            lines.append(f"  {day}: {tmin}°C / {tmax}°C, precip {precip:.1f}mm"
                         f"{f', snow {snow:.1f}cm' if snow else ''}"
                         f", wind max {wind_max:.0f}km/h"
                         f", sunrise {sunrise[-5:] if sunrise else '?'} sunset {sunset[-5:] if sunset else '?'}")
        lines.append("")

    # Indoor vs outdoor comparison
    if ha_sensors.get("indoor_temp"):
        lines.append("=== INDOOR vs OUTDOOR COMPARISON ===")
        outdoor_now = ha_sensors.get("outdoor_temp")
        if outdoor_now is not None:
            lines.append(f"  Outdoor sensor: {outdoor_now}°C")
        for room, temp in sorted(ha_sensors["indoor_temp"].items()):
            delta = ""
            if outdoor_now is not None:
                delta = f" (Δ{temp - outdoor_now:+.1f}°C vs outdoor)"
            lines.append(f"  {room}: {temp}°C{delta}")
        if ha_sensors.get("indoor_humidity"):
            for room, hum in sorted(ha_sensors["indoor_humidity"].items()):
                lines.append(f"  {room} humidity: {hum}%")
        lines.append("")

    # Heating analysis
    if heating:
        lines.append("=== HEATING NEEDS FORECAST ===")
        lines.append(f"  Current avg indoor: {heating['avg_indoor_now']}°C")
        lines.append(f"  48h avg outdoor forecast: {heating['avg_outdoor_48h']}°C")
        lines.append(f"  Coldest point: {heating['coldest_point']['temp']}°C at {heating['coldest_point']['time']}")
        lines.append(f"  Hours below {HEATING_OUTDOOR_THRESHOLD}°C: {heating['cold_hours']}/48")
        lines.append(f"  Heat deficit: {heating['heat_deficit_degree_hours']} degree-hours")
        lines.append(f"  Heating intensity: {heating['heating_intensity']}")
        lines.append("")

    # Severe weather alerts
    if severe_alerts:
        lines.append("=== ⚠️  SEVERE WEATHER ALERTS ===")
        for a in severe_alerts:
            icon = "🔴" if a["severity"] == "high" else "🟡"
            lines.append(f"  {icon} {a['type'].upper()}: {a['detail']} at {a['time']}")
        lines.append("")

    # Air quality
    if aq_correlation:
        lines.append("=== AIR QUALITY FORECAST ===")
        aqsum = aq_correlation.get("aq_24h_summary", {})
        lines.append(f"  Worst PM2.5 (24h): {aqsum.get('worst_pm25', '?')} µg/m³")
        lines.append(f"  Worst AQI (24h): {aqsum.get('worst_aqi', '?')}")
        lines.append(f"  Avg PM2.5: {aqsum.get('avg_pm25', '?')} µg/m³")
        lines.append(f"  Ventilation advice: {aq_correlation.get('ventilation_advice', '?')}")
        if aq_correlation.get("best_ventilation_hours"):
            lines.append(f"  Best ventilation windows: {', '.join(h[-5:] for h in aq_correlation['best_ventilation_hours'])}")
        if aq_correlation.get("pollen_peaks"):
            for p, val in aq_correlation["pollen_peaks"].items():
                lines.append(f"  {p.capitalize()} pollen: {val} grains/m³")

        # Indoor CO2
        co2 = aq_correlation.get("indoor_co2", {})
        if co2:
            lines.append("  Indoor CO₂:")
            for room, val in co2.items():
                status = "✅ good" if val < 800 else "⚠️ moderate" if val < 1200 else "🔴 high"
                lines.append(f"    {room}: {val} ppm ({status})")
        lines.append("")

    return "\n".join(lines)


def llm_analyze(summary_text):
    """Use LLM to produce weather intelligence briefing."""
    system = """\
You are ClawdBot, weather intelligence analyst for a family house in Łódź, Poland.
Analyze weather forecast + indoor sensor data and produce an actionable briefing.

Your report MUST include these sections:

## ☀️ Weather Overview
- Current conditions and how they compare to the forecast
- Key weather changes in the next 48 hours
- Weekend outlook

## 🌡️ Heating & Comfort
- Will indoor temps stay comfortable without intervention?
- When heating will be needed most (overnight, morning, etc.)
- Specific recommendations (e.g., "reduce heating tonight, outdoor stays above 12°C")
- Compare HA sensor vs forecast outdoor temps — if they diverge, note calibration issue

## 💨 Air Quality & Ventilation
- When to open windows (best AQ hours) vs when to keep closed
- If indoor CO₂ is high, recommend specific ventilation windows with good outdoor AQ
- Pollen alerts if relevant (spring/summer allergy concern)
- Correlate: CO₂ rises in occupied rooms → needed ventilation vs outdoor AQ tradeoff

## ⚠️ Weather Alerts
- Flag ANY severe weather (heavy rain, strong wind, snow, ice, fog)
- Practical impact: "move outdoor furniture inside", "expect school delays"
- Driving conditions for the Łódź area

## 📊 Weekly Trend
- Temperature trend over 7 days (warming/cooling pattern)
- Precipitation outlook (dry spell or wet period ahead)
- Any seasonal transitions

Formatting: Use emoji for scannability. Be concise and actionable.
Write 300-500 words. End with 3-5 bullet points of specific actions.
Write ONLY in English. No Chinese."""

    return call_ollama(system, summary_text, temperature=0.3, max_tokens=2000)


# ── Run ────────────────────────────────────────────────────────────────────

def run_scrape():
    """Phase 1: Collect all data."""
    log("=== weather-watch scrape ===")

    forecast = fetch_forecast()
    air_quality = fetch_air_quality()
    ha_sensors = collect_ha_sensors()

    # Analysis
    severe_alerts = detect_severe_weather(forecast)
    heating = analyze_heating_needs(forecast, ha_sensors)
    aq_correlation = analyze_air_quality_correlation(air_quality, ha_sensors)

    if severe_alerts:
        log(f"  ⚠️  {len(severe_alerts)} severe weather alerts!")

    raw_data = {
        "timestamp": datetime.now().isoformat(),
        "forecast": forecast,
        "air_quality": air_quality,
        "ha_sensors": {
            "indoor_temp": ha_sensors.get("indoor_temp", {}),
            "indoor_humidity": ha_sensors.get("indoor_humidity", {}),
            "air_quality": ha_sensors.get("air_quality", {}),
            "outdoor_temp": ha_sensors.get("outdoor_temp"),
            # Don't save history to raw (too big)
        },
        "severe_alerts": severe_alerts,
        "heating_analysis": heating,
        "aq_correlation": aq_correlation,
    }

    raw_path = DATA_DIR / "raw-weather.json"
    save_json(raw_path, raw_data)
    log(f"Saved raw data to {raw_path}")

    return raw_data


def run_analyze():
    """Phase 2: LLM analysis + save + alert."""
    log("=== weather-watch analyze ===")

    raw_path = DATA_DIR / "raw-weather.json"
    if not raw_path.exists():
        log("No raw data — run scrape first")
        return

    raw = json.loads(raw_path.read_text())

    forecast = raw.get("forecast")
    air_quality = raw.get("air_quality")
    ha_sensors = raw.get("ha_sensors", {})
    severe_alerts = raw.get("severe_alerts", [])
    heating = raw.get("heating_analysis")
    aq_correlation = raw.get("aq_correlation")

    # Build summary for LLM
    summary = build_summary_text(forecast, air_quality, ha_sensors,
                                  severe_alerts, heating, aq_correlation)

    # LLM analysis
    analysis = llm_analyze(summary)
    if not analysis:
        log("LLM analysis failed")
        analysis = "LLM analysis unavailable"

    # Save results
    result = {
        "timestamp": datetime.now().isoformat(),
        "date": TODAY,
        "current": forecast.get("current") if forecast else None,
        "daily_forecast": forecast.get("daily") if forecast else None,
        "severe_alerts": severe_alerts,
        "heating_analysis": heating,
        "aq_correlation": aq_correlation,
        "ha_comparison": ha_sensors,
        "analysis": analysis,
    }

    out_path = DATA_DIR / f"weather-{TODAY}.json"
    save_json(out_path, result)

    latest = DATA_DIR / "weather-latest.json"
    latest.unlink(missing_ok=True)
    latest.symlink_to(out_path.name)

    # Think note
    note = {
        "timestamp": datetime.now().isoformat(),
        "source": "weather-watch",
        "category": "environment",
        "title": f"Weather & Environment — {datetime.now().strftime('%Y-%m-%d')}",
        "content": analysis,
        "metadata": {
            "severe_alerts": len(severe_alerts),
            "heating_intensity": heating.get("heating_intensity") if heating else None,
            "ventilation_advice": aq_correlation.get("ventilation_advice") if aq_correlation else None,
        },
    }
    ts = datetime.now().strftime("%Y%m%d-%H%M")
    note_path = THINK_DIR / f"note-weather-{ts}.json"
    save_json(note_path, note)

    log(f"Saved {out_path}, think note")

    # Signal alerts
    alert_parts = []

    # Severe weather → always alert
    high_alerts = [a for a in severe_alerts if a["severity"] == "high"]
    if high_alerts:
        alert_parts.append("⚠️ SEVERE WEATHER:")
        for a in high_alerts[:3]:
            alert_parts.append(f"  {a['detail']} at {a['time']}")
        alert_parts.append("")

    # Heating advisory
    if heating and heating.get("heating_intensity") in ("high",):
        alert_parts.append(f"🌡️ Heating: {heating['heating_intensity']} — "
                           f"coldest {heating['coldest_point']['temp']}°C at "
                           f"{heating['coldest_point']['time'][-5:]}")
        alert_parts.append("")

    # AQ advisory
    if aq_correlation:
        if aq_correlation.get("ventilation_advice") == "avoid":
            alert_parts.append("💨 Air quality: POOR — keep windows closed")
        if aq_correlation.get("pollen_peaks"):
            peaks = ", ".join(f"{k}: {v}" for k, v in aq_correlation["pollen_peaks"].items())
            alert_parts.append(f"🌿 Pollen alert: {peaks}")

    if alert_parts:
        msg = "🌤 Weather Watch\n" + "\n".join(alert_parts)
        if analysis:
            # Add first actionable recommendation from analysis
            for line in analysis.split("\n"):
                if line.strip().startswith("- ") or line.strip().startswith("• "):
                    msg += f"\n\nTip: {line.strip()[2:]}"
                    break
        signal_send(msg[:1500])
    else:
        log("  No weather alerts needed")


def main():
    parser = argparse.ArgumentParser(description="Weather forecast + HA correlation")
    parser.add_argument("--scrape-only", action="store_true", help="Only collect data")
    parser.add_argument("--analyze-only", action="store_true", help="Only run LLM analysis")
    args = parser.parse_args()

    if args.scrape_only:
        run_scrape()
    elif args.analyze_only:
        run_analyze()
    else:
        run_scrape()
        run_analyze()


if __name__ == "__main__":
    main()
