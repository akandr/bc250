#!/usr/bin/env bash
# gpu-overlay-sampler.sh — 1 Hz sampler of GPU clock, temp, power, GTT, VRAM
# Writes CSV to the path given as $1. Run as background process; kill to stop.
#
# Columns: ts_iso, sclk_mhz, edge_temp_c, power_mw, gtt_gib, vram_gib, mem_avail_mb
set -u
OUT="${1:?usage: $0 OUT_CSV}"
HWMON_BASE=/sys/class/drm/card1/device/hwmon
DRM_DEV=/sys/class/drm/card1/device

# locate the right hwmon subdir (numeric suffix varies post-reboot)
HWMON=""
for h in "$HWMON_BASE"/hwmon*; do
  [[ -r "$h/temp1_input" ]] && HWMON="$h" && break
done

echo "ts_iso,sclk_mhz,edge_temp_c,power_mw,gtt_gib,vram_gib,mem_avail_mb" > "$OUT"

while true; do
  TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  # active GPU clock from pp_dpm_sclk (line ending with *)
  SCLK=$(grep '\*' "$DRM_DEV/pp_dpm_sclk" 2>/dev/null | awk '{print $2}' | tr -d 'Mhz' || echo 0)
  TEMP_MC=$(cat "$HWMON/temp1_input" 2>/dev/null || echo 0)
  POWER_UW=$(cat "$HWMON/power1_average" 2>/dev/null || echo 0)
  GTT_B=$(cat "$DRM_DEV/mem_info_gtt_used" 2>/dev/null || echo 0)
  VRAM_B=$(cat "$DRM_DEV/mem_info_vram_used" 2>/dev/null || echo 0)
  MEMAVAIL_KB=$(awk '/MemAvailable:/{print $2; exit}' /proc/meminfo 2>/dev/null || echo 0)
  TEMP_C=$(awk -v t="$TEMP_MC" 'BEGIN{printf "%.1f", t/1000}')
  POWER_MW=$(awk -v p="$POWER_UW" 'BEGIN{printf "%.0f", p/1000}')
  GTT_GIB=$(awk -v b="$GTT_B" 'BEGIN{printf "%.3f", b/1073741824}')
  VRAM_GIB=$(awk -v b="$VRAM_B" 'BEGIN{printf "%.3f", b/1073741824}')
  MEM_MB=$(awk -v k="$MEMAVAIL_KB" 'BEGIN{printf "%.0f", k/1024}')
  echo "$TS,$SCLK,$TEMP_C,$POWER_MW,$GTT_GIB,$VRAM_GIB,$MEM_MB" >> "$OUT"
  sleep 1
done
