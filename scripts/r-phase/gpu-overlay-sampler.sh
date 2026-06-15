#!/usr/bin/env bash
# gpu-overlay-sampler.sh — 1Hz CSV sampler for AMD BC-250 GPU telemetry.
# CSV columns (matching bench-phase-c.py `summarise_overlay` parser):
#   ts_ms, sclk_mhz, temp_c, power_mw, gtt_gib, vram_gib, dpm_state
#
# Auto-detects the BC-250 amdgpu card (vendor 0x1002 device 0x13fe, or any
# amdgpu card if 13fe not found). Runs until SIGTERM; ~negligible CPU cost.
set -u
OUT="${1:?usage: $0 <out.csv>}"

# --- locate amdgpu card (prefer device 0x13fe Cyan Skillfish) ---
CARD=""
for c in /sys/class/drm/card*; do
    [[ -d "$c/device" ]] || continue
    vendor=$(cat "$c/device/vendor" 2>/dev/null || echo "")
    device=$(cat "$c/device/device" 2>/dev/null || echo "")
    if [[ "$vendor" == "0x1002" && "$device" == "0x13fe" ]]; then
        CARD="$c"; break
    fi
    if [[ -z "$CARD" && "$vendor" == "0x1002" ]]; then
        CARD="$c"  # fall back to first amdgpu
    fi
done
[[ -z "$CARD" ]] && { echo "no amdgpu card found" >&2; exit 1; }

DEV="$CARD/device"
HWMON=$(ls -d "$DEV"/hwmon/hwmon* 2>/dev/null | head -1)

read_sclk_mhz() {
    # parse pp_dpm_sclk: lines look like "1: 1000Mhz *" — pick the * row
    local f="$DEV/pp_dpm_sclk"
    if [[ -r "$f" ]]; then
        awk '/\*/ {gsub(/Mhz/,"",$2); print int($2); exit}' "$f" 2>/dev/null && return
    fi
    # fallback: hwmon freq1_input is Hz
    if [[ -n "$HWMON" && -r "$HWMON/freq1_input" ]]; then
        awk '{print int($1/1000000)}' "$HWMON/freq1_input" 2>/dev/null
    fi
}

read_dpm_state() {
    local f="$DEV/pp_dpm_sclk"
    [[ -r "$f" ]] || { echo "?"; return; }
    awk '/\*/ {print $1; exit}' "$f" | tr -d ':' 2>/dev/null
}

read_temp_c() {
    [[ -n "$HWMON" && -r "$HWMON/temp1_input" ]] || { echo 0; return; }
    awk '{printf "%.1f", $1/1000}' "$HWMON/temp1_input" 2>/dev/null
}

read_power_mw() {
    if [[ -n "$HWMON" && -r "$HWMON/power1_average" ]]; then
        awk '{print int($1/1000)}' "$HWMON/power1_average" 2>/dev/null  # uW -> mW
    elif [[ -n "$HWMON" && -r "$HWMON/power1_input" ]]; then
        awk '{print int($1/1000)}' "$HWMON/power1_input" 2>/dev/null
    else
        echo 0
    fi
}

read_gtt_gib() {
    local f="$DEV/mem_info_gtt_used"
    [[ -r "$f" ]] || { echo 0; return; }
    awk '{printf "%.3f", $1/1073741824}' "$f" 2>/dev/null
}

read_vram_gib() {
    local f="$DEV/mem_info_vram_used"
    [[ -r "$f" ]] || { echo 0; return; }
    awk '{printf "%.3f", $1/1073741824}' "$f" 2>/dev/null
}

# write header
echo "ts_ms,sclk_mhz,temp_c,power_mw,gtt_gib,vram_gib,dpm_state" > "$OUT"

trap 'exit 0' TERM INT

while true; do
    ts=$(date +%s%3N)
    sclk=$(read_sclk_mhz); sclk=${sclk:-0}
    temp=$(read_temp_c);   temp=${temp:-0}
    pw=$(read_power_mw);   pw=${pw:-0}
    gtt=$(read_gtt_gib);   gtt=${gtt:-0}
    vram=$(read_vram_gib); vram=${vram:-0}
    dpm=$(read_dpm_state); dpm=${dpm:-?}
    echo "$ts,$sclk,$temp,$pw,$gtt,$vram,$dpm" >> "$OUT"
    sleep 1
done
