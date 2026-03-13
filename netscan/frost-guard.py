#!/usr/bin/env python3
"""
frost-guard.py — Frost protection alerts for heat pump, garden water, and blinds.

Checks Open-Meteo hourly forecast for Łódź and sends Signal notifications
on state transitions:

  1. Frost protection (heat pump + garden water):
     - FROST: any forecast hour below 0°C
       → "Disable heat pump, cut garden water"
     - SAFE:  all forecast hours above 0°C for next 3 days
       → "Re-enable heat pump and garden water"

  2. Deep frost (external blinds/rollers):
     - CLOSE: any forecast hour ≤ -5°C  → "Enable automated blinds"
     - OPEN:  all forecast hours > 0°C   → "Disable automated blinds"
     (hysteresis: close at -5, open at 0 — won't toggle between -5 and 0)

State persisted in frost-guard-state.json — only notifies on transitions.
No LLM needed, pure API + state machine.
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────

# Łódź coordinates (same as weather-watch.py)
LAT = REDACTED_LAT
LON = REDACTED_LON

SIGNAL_RPC = "http://127.0.0.1:8080/api/v1/rpc"
SIGNAL_FROM = os.environ.get("SIGNAL_ACCOUNT", "+<BOT_PHONE>")
SIGNAL_TO = os.environ.get("SIGNAL_OWNER", "+<OWNER_PHONE>")

DATA_DIR = Path("/opt/netscan/data/weather")
STATE_FILE = DATA_DIR / "frost-guard-state.json"

# Thresholds
FROST_THRESHOLD = 0.0        # °C — frost danger for heat pump / water
DEEP_FROST_THRESHOLD = -5.0  # °C — close blinds/rollers
DEFROST_THRESHOLD = 0.0      # °C — hysteresis: reopen blinds when ALL hours > 0

FORECAST_DAYS = 3

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

FORECAST_URL = (
    f"https://api.open-meteo.com/v1/forecast?"
    f"latitude={LAT}&longitude={LON}"
    f"&hourly=temperature_2m"
    f"&timezone=Europe%2FWarsaw&forecast_days={FORECAST_DAYS}"
)


# ── Helpers ────────────────────────────────────────────────────────────────

def log(msg):
    print(f"[frost-guard] {msg}", flush=True)


def signal_send(msg):
    """Send Signal message via JSON-RPC."""
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


def load_state():
    """Load persisted state, default to safe/open."""
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "frost_protection": "safe",
            "blinds": "open",
            "last_updated": None,
        }


def save_state(state):
    state["last_updated"] = datetime.now().isoformat()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.rename(STATE_FILE)


def fetch_forecast():
    """Fetch hourly temperature forecast from Open-Meteo."""
    req = urllib.request.Request(FORECAST_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    hours = data["hourly"]["time"]          # ["2026-03-13T00:00", ...]
    temps = data["hourly"]["temperature_2m"]  # [2.1, 1.3, ...]
    return list(zip(hours, temps))


# ── Analysis ───────────────────────────────────────────────────────────────

def analyze(hourly):
    """Analyze forecast and return new desired states + reasons."""
    all_temps = []
    frost_details = []
    cold_details = []

    for time_str, temp in hourly:
        if temp is None:
            continue
        all_temps.append(temp)
        if temp < FROST_THRESHOLD:
            frost_details.append(f"  {time_str}: {temp}°C")
        if temp <= DEEP_FROST_THRESHOLD:
            cold_details.append(f"  {time_str}: {temp}°C")

    if not all_temps:
        log("No forecast data available")
        return None, None, [], []

    all_min = min(all_temps)

    log(f"Forecast: overall min {all_min}°C "
        f"({len(hourly)} hours, {FORECAST_DAYS}d ahead)")

    # Frost protection: any hour below 0 → frost
    frost_state = "frost" if all_min < FROST_THRESHOLD else "safe"

    # Blinds: any hour ≤ -5 → closed; all hours > 0 → open; else no change (None)
    if all_min <= DEEP_FROST_THRESHOLD:
        blinds_state = "closed"
    elif all_min > DEFROST_THRESHOLD:
        blinds_state = "open"
    else:
        blinds_state = None  # in hysteresis band — keep current state

    return frost_state, blinds_state, frost_details, cold_details


# ── Main ───────────────────────────────────────────────────────────────────

def run():
    log(f"Fetching {FORECAST_DAYS}-day hourly forecast for Łódź...")
    try:
        hourly = fetch_forecast()
    except Exception as e:
        log(f"Failed to fetch forecast: {e}")
        sys.exit(1)

    state = load_state()
    old_frost = state.get("frost_protection", "safe")
    old_blinds = state.get("blinds", "open")

    new_frost, new_blinds, frost_details, cold_details = analyze(hourly)
    if new_frost is None:
        return

    changed = False

    # ── Frost protection transitions ──
    if new_frost != old_frost:
        if new_frost == "frost":
            detail = "\n".join(frost_details[:5])
            if len(frost_details) > 5:
                detail += f"\n  ... and {len(frost_details) - 5} more hours"
            msg = (
                f"🥶 FROST WARNING — Łódź\n\n"
                f"Temps going below 0°C in next {FORECAST_DAYS} days.\n\n"
                f"→ Disable heat pump\n"
                f"→ Cut garden water supply\n\n"
                f"Coldest hours:\n{detail}"
            )
            log(f"Transition: {old_frost} → frost")
            signal_send(msg)
        else:
            msg = (
                f"✅ FROST CLEAR — Łódź\n\n"
                f"All temps above 0°C for next {FORECAST_DAYS} days.\n\n"
                f"→ Re-enable heat pump\n"
                f"→ Re-enable garden water"
            )
            log(f"Transition: {old_frost} → safe")
            signal_send(msg)
        state["frost_protection"] = new_frost
        changed = True
    else:
        log(f"Frost protection: no change ({old_frost})")

    # ── Blinds transitions ──
    if new_blinds is not None and new_blinds != old_blinds:
        if new_blinds == "closed":
            detail = "\n".join(cold_details[:5])
            if len(cold_details) > 5:
                detail += f"\n  ... and {len(cold_details) - 5} more hours"
            msg = (
                f"🧊 DEEP FROST — Łódź\n\n"
                f"Temps forecast to drop to -5°C or below.\n\n"
                f"→ Enable automated external blinds/rollers\n"
                f"   (keep warmth inside)\n\n"
                f"Coldest hours:\n{detail}"
            )
            log(f"Transition: blinds {old_blinds} → closed")
            signal_send(msg)
        else:
            msg = (
                f"☀️ DEEP FROST OVER — Łódź\n\n"
                f"All forecast temps stable above 0°C.\n\n"
                f"→ Disable automated external blinds/rollers"
            )
            log(f"Transition: blinds {old_blinds} → open")
            signal_send(msg)
        state["blinds"] = new_blinds
        changed = True
    elif new_blinds is None:
        log(f"Blinds: in hysteresis band, keeping {old_blinds}")
    else:
        log(f"Blinds: no change ({old_blinds})")

    if changed:
        save_state(state)
        log("State saved")
    else:
        log("No state changes — no notifications sent")


if __name__ == "__main__":
    run()
