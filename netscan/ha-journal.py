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

import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

# ─── Config ───

DATA_DIR = "/opt/netscan/data"
THINK_DIR = os.path.join(DATA_DIR, "think")
HA_OBSERVE = "/opt/netscan/ha-observe.py"

OLLAMA_URL = "http://localhost:11434"
OLLAMA_CHAT = f"{OLLAMA_URL}/api/chat"
OLLAMA_MODEL = "qwen3-14b-abl-nothink:latest"

os.makedirs(THINK_DIR, exist_ok=True)

# Load HA credentials from openclaw .env
ENV_FILE = os.path.expanduser("~/.openclaw/.env")
if os.path.exists(ENV_FILE):
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


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
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens},
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
        return content
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


# ─── Main ───

SYSTEM_PROMPT = """\
You are ClawdBot, an AI home observer for a family house in Poland.
Your job: analyze Home Assistant sensor data, spot patterns, and write
a concise journal entry about the state of the home.

Rules:
- Be factual and concise. No filler, no greetings.
- Use emoji for quick scanning: 🌡️ temp, 💨 air quality, 💡 lights, 🪟 covers, ⚠️ warnings, ✅ normal
- Flag anything unusual: high CO₂, open windows when cold, lights left on, temperature anomalies
- Compare with previous observation if provided — note what changed
- Include time context (night vs. day, season-appropriate behavior)
- Air quality thresholds: CO₂ >1000=concerning >1500=bad, PM2.5 >25=moderate >50=poor, VOC >0.5=elevated
- Write in English, translate Polish sensor names
- End with a one-line overall assessment
- Keep it under 400 words
"""


def main():
    quick = "--quick" in sys.argv

    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] ha-journal starting ({'quick' if quick else 'full'})")

    # Guard: don't compete for GPU
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

    # Guard: don't evict a model that openclaw-gateway is actively using
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/ps")
        resp = urllib.request.urlopen(req, timeout=5)
        ps_data = json.loads(resp.read())
        running_models = ps_data.get("models", [])
        for m in running_models:
            name = m.get("name", "")
            # If the gateway's model is loaded and it's NOT our journal model,
            # that means the gateway is mid-conversation — back off
            if name and name != OLLAMA_MODEL:
                print(f"  Ollama busy with {name} (likely gateway) — skipping")
                return
    except Exception:
        pass  # can't reach ollama — will fail later at call_ollama anyway

    # Collect HA data
    print("  Collecting HA data...")
    climate_data = run_ha("climate")
    rooms_data = run_ha("rooms") if not quick else ""
    anomalies_data = run_ha("anomalies") if not quick else ""
    lights_data = run_ha("lights")

    if not climate_data or climate_data.startswith("[error"):
        print(f"  Failed to get HA data: {climate_data}")
        return

    # Build the user prompt
    now = datetime.now()
    parts = [
        f"Date/time: {now.strftime('%A, %d %B %Y, %H:%M')} (Poland, CET)",
        "",
        "=== CLIMATE & AIR QUALITY ===",
        climate_data,
        "",
        "=== LIGHTS ===",
        lights_data,
    ]

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
            prev["content"][:800],  # truncate to avoid huge prompt
        ]

    user_prompt = "\n".join(parts)

    print(f"  Prompt: {len(user_prompt)} chars")

    # Ask Ollama
    print("  Calling Ollama for analysis...")
    analysis = call_ollama(SYSTEM_PROMPT, user_prompt)
    if not analysis:
        print("  Ollama failed — saving raw data as fallback")
        analysis = (
            f"⚠️ LLM analysis unavailable — raw snapshot:\n\n"
            f"{climate_data}\n\n{lights_data}"
        )

    # Save note
    title = f"Home Journal — {now.strftime('%d %b %Y, %H:%M')}"
    context = {
        "mode": "quick" if quick else "full",
        "sensors_collected": sum(1 for x in [climate_data, rooms_data, anomalies_data, lights_data] if x),
        "previous_available": prev is not None,
    }
    save_note(title, analysis, context)
    print("  Done.")


if __name__ == "__main__":
    main()
