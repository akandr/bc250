---
name: home-assistant
description: "Observe Home Assistant: lights, temperature, air quality, weather, appliances, covers, anomalies. exec: HASS_TOKEN=$HASS_TOKEN HASS_URL=$HASS_URL /opt/netscan/ha-observe.py <command> [args]. Commands: rooms, lights, climate, weather, history <entity> [hours], anomalies, appliances, covers, entity <id>, entities <regex>, snapshot."
metadata: {"openclaw": {"always": true, "emoji": "🏠"}}
---

# Home Assistant Observer (READ-ONLY)

You have read-only access to AK's Home Assistant instance via `/opt/netscan/ha-observe.py`.
**You MUST NOT trigger any actions, automations, or service calls on Home Assistant.**
Your role: observe, analyze, report, detect anomalies.

## How to use

Always use the `exec` tool with the HASS env vars:

```bash
HASS_TOKEN=$HASS_TOKEN HASS_URL=$HASS_URL /opt/netscan/ha-observe.py <command>
```

## Commands

| Command | What it does |
|---------|-------------|
| `rooms` | Room-by-room summary: temperature, humidity, air quality, lights, covers |
| `lights` | Which lights and switches are ON/OFF right now |
| `climate` | Temperature + humidity + air quality across all zones + outside weather |
| `weather` | Detailed weather: temp, humidity, pressure, wind, UV, sun times |
| `history sensor.entity_id [hours]` | Last N hours of a sensor with stats (mean, stdev, IQR outliers, z-score, trend) |
| `anomalies` | Scans ALL numeric sensors for statistical anomalies (z-score, IQR) over 48h |
| `appliances` | Washer (pralka), dryer (suszarka), fridge (lodówka) status |
| `covers` | Blinds/shades open/closed state with position |
| `entity sensor.some_id` | Full detail of one entity (all attributes) |
| `entities regex` | List entities matching a regex pattern |
| `snapshot` | All entities grouped by domain (verbose — use sparingly) |

## When to use what

- **"which lights are on?"** → `lights`
- **"how's the air quality?"** → `climate`
- **"what's the temperature in the bedroom?"** → `rooms` or `entities sypialnia`
- **"is the washer running?"** → `appliances`
- **"are the blinds closed?"** → `covers`
- **"anything weird going on?"** → `anomalies`
- **"show me CO₂ in boys room for last 12h"** → `history sensor.air_detector_2_dwutlenek_wegla 12`
- **"what's the weather"** → `weather`
- **"is it cold outside?"** → `weather`

## Room name mapping (Polish → English)

- Salon = Living room
- Sypialnia = Bedroom
- Kuchnia = Kitchen
- Łazienka = Bathroom
- Pokój chłopców = Boys' room (sensors: air_detector_2)
- Pokój komputerowy = Office
- Garaż = Garage
- Piwnica = Basement
- Dach/Strych = Roof/Attic (sensors: okno_dachowe, górna_belka)

## Air quality reference thresholds

| Metric | Good | Warning | Bad |
|--------|------|---------|-----|
| CO₂ | <1000 ppm | 1000-1500 | >1500 |
| PM2.5 | <12 µg/m³ | 12-35 | >35 |
| VOC | <0.3 mg/m³ | 0.3-0.5 | >0.5 |
| HCHO | <0.03 mg/m³ | 0.03-0.08 | >0.08 |

## Key sensor entity IDs

- `sensor.air_detector_2_*` — Boys' room air quality (CO₂, PM2.5, VOC, HCHO, temp, humidity)
- `sensor.air_detector_*` — Bedroom air quality
- `sensor.1000bec2f1_t/h` — Okno dachowe (roof window) temp/humidity
- `sensor.1000bec547_t/h` — Górna belka (upper beam) temp/humidity
- `sensor.1000becdc2_t/h` — Garaż (garage) temp/humidity
- `weather.forecast_dom` — Weather forecast (met.no)

## Behavioral rules

1. **READ ONLY** — never suggest executing HA service calls
2. When reporting, translate Polish sensor names to English for AK's convenience
3. For anomaly reports, provide context: "CO₂ at 1200ppm is high but may be normal if kids are sleeping with windows closed"
4. If AK asks "anything unusual?", run `anomalies` first, then `climate` for context
5. Keep responses concise — summarize, don't dump raw data
6. If a sensor shows "unavailable", mention it briefly (device may be offline)
7. Cross-reference: if indoor CO₂ is high and outside temp is fine, suggest opening windows
