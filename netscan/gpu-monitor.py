#!/usr/bin/env python3
"""
gpu-monitor.py — AMD GPU power/thermal monitoring & charting
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Two modes:
  collect — read GPU sysfs sensors, append one row to daily CSV
  chart   — generate colorful PNG charts from collected CSV data

Metrics collected (AMD amdgpu, card1/hwmon2):
  - Power draw (W)      — power1_average (µW → W)
  - Temperature (°C)    — temp1_input (m°C → °C)
  - GPU clock (MHz)     — freq1_input (Hz → MHz)
  - VDD GFX (mV)        — in0_input
  - VDD NB (mV)         — in1_input
  - VRAM used (MB)      — mem_info_vram_used (bytes → MB)
  - GTT used (MB)       — mem_info_gtt_used (bytes → MB)

Output:
  CSV:    /opt/netscan/data/gpu/gpu-YYYYMMDD.csv
  Charts: /opt/netscan/data/gpu/gpu-YYYYMMDD-power.png
          /opt/netscan/data/gpu/gpu-YYYYMMDD-temp.png
          /opt/netscan/data/gpu/gpu-YYYYMMDD-dashboard.png

Cron:
  * * * * * python3 /opt/netscan/gpu-monitor.py collect
  55 23 * * * python3 /opt/netscan/gpu-monitor.py chart
"""

import csv
import os
import struct
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────
GPU_DIR = Path("/opt/netscan/data/gpu")
HWMON = "/sys/class/drm/card1/device/hwmon/hwmon2"
DRM_DEV = "/sys/class/drm/card1/device"

CSV_FIELDS = [
    "timestamp", "power_w", "temp_c", "freq_mhz",
    "vddgfx_mv", "vddnb_mv", "vram_mb", "gtt_mb",
    "throttle_status",
]

# ── Thermal Throttle Bitmask (VanGogh/Rembrandt APU gpu_metrics v2.2) ────
# Source: drivers/gpu/drm/amd/pm/swsmu/inc/amdgpu_smu.h
THROTTLE_BITS = {
    0:  "SPL",        # Sustained Power Limit
    1:  "FPPT",       # Fast PPT (boost)
    2:  "SPPT",       # Slow PPT
    3:  "SPPT_APU",   # Slow PPT APU-specific
    4:  "THM_CORE",   # Thermal — CPU core
    5:  "THM_GFX",    # Thermal — GPU
    6:  "THM_SOC",    # Thermal — SoC
    7:  "TDC_VDD",    # Thermal Design Current — VDD
    8:  "TDC_SOC",    # TDC — SoC
    9:  "TDC_GFX",    # TDC — GFX
    10: "EDC_CPU",    # Electrical Design Current — CPU
    11: "EDC_GFX",    # EDC — GFX
    12: "PROCHOT",    # Processor Hot (external signal)
}

# Which bits indicate *thermal* throttling specifically
THERMAL_THROTTLE_MASK = (1 << 4) | (1 << 5) | (1 << 6) | (1 << 12)  # THM_CORE|THM_GFX|THM_SOC|PROCHOT
# All power-related throttle bits
POWER_THROTTLE_MASK = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3)     # SPL|FPPT|SPPT|SPPT_APU

GPU_METRICS_PATH = "/sys/class/drm/card1/device/gpu_metrics"

# Chart color scheme — vibrant dark theme
COLORS = {
    "bg": "#1a1a2e",
    "panel": "#16213e",
    "grid": "#2a3a5c",
    "text": "#e0e0e0",
    "title": "#ffffff",
    "power": "#ff6b35",       # orange
    "power_fill": "#ff6b3540",
    "power_hist": "#ff6b35",
    "temp": "#00d4ff",        # cyan
    "temp_fill": "#00d4ff30",
    "temp_hist": "#00d4ff",
    "freq": "#a855f7",        # purple
    "freq_fill": "#a855f720",
    "vram": "#22c55e",        # green
    "gtt": "#eab308",         # yellow
    "accent": "#f43f5e",      # rose
    "mild_zone": "#22c55e40", # green transparent (good temp zone)
    "warm_zone": "#eab30840", # yellow transparent
    "hot_zone": "#ef444440",  # red transparent
}

# ── Electricity cost estimation (G11 tariff, PGE Łódź) ──────────────────────
# power1_average = PPT = APU SoC power (CPU + GPU + UMA memory controller)
# Total wall power ≈ (PPT + overhead) / PSU_efficiency
SYSTEM_OVERHEAD_W = 9       # NVMe SSD ~4W, fans ~2W, board/VRM ~3W
PSU_EFFICIENCY = 0.87       # typical external brick at 60-80W load
G11_PLN_PER_KWH = 1.30      # PGE Łódź 2026 gross (energy 0.62 + distribution 0.68)


def estimate_wall_power(ppt_w):
    """Estimate total wall power from APU PPT reading."""
    return (ppt_w + SYSTEM_OVERHEAD_W) / PSU_EFFICIENCY


def calc_energy_cost(rows):
    """Calculate energy consumption and cost from CSV rows.

    Each row = 1 minute sample. Returns dict with:
      wall_avg_w, wall_max_w, energy_kwh, cost_pln, hours_covered
    """
    power_vals = [r["power_w"] for r in rows if r.get("power_w") is not None]
    if not power_vals:
        return None

    wall_powers = [estimate_wall_power(p) for p in power_vals]
    minutes = len(wall_powers)
    hours = minutes / 60.0
    avg_wall = sum(wall_powers) / len(wall_powers)
    energy_kwh = avg_wall * hours / 1000.0  # kWh = W × h / 1000
    cost_pln = energy_kwh * G11_PLN_PER_KWH

    return {
        "ppt_avg_w": sum(power_vals) / len(power_vals),
        "ppt_max_w": max(power_vals),
        "wall_avg_w": avg_wall,
        "wall_max_w": max(wall_powers),
        "energy_kwh": energy_kwh,
        "cost_pln": cost_pln,
        "hours": hours,
        "samples": minutes,
    }


# ── Helpers ────────────────────────────────────────────────────────────────

def read_sysfs(path):
    """Read integer from sysfs file, return None on failure."""
    try:
        return int(Path(path).read_text().strip())
    except Exception:
        return None


def read_throttle_status():
    """Read throttle_status from gpu_metrics v2.2 binary blob.

    Returns integer bitmask (0 = no throttling), or None on failure.
    VanGogh APU gpu_metrics v2.2 layout:
      offset 108: uint32_t throttle_status
    """
    try:
        data = Path(GPU_METRICS_PATH).read_bytes()
        # Verify v2.x header
        if len(data) >= 112 and data[2] == 2:
            return struct.unpack_from("<I", data, 108)[0]
    except Exception:
        pass
    return None


def decode_throttle(status):
    """Decode throttle bitmask into list of active throttle reasons."""
    if not status:
        return []
    return [name for bit, name in sorted(THROTTLE_BITS.items())
            if status & (1 << bit)]


def csv_path(date_str=None):
    """Return CSV path for a given date (default: today)."""
    if not date_str:
        date_str = datetime.now().strftime("%Y%m%d")
    return GPU_DIR / f"gpu-{date_str}.csv"


# ── Collect Mode ───────────────────────────────────────────────────────────

def collect():
    """Read all GPU sensors and append one row to today's CSV."""
    GPU_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()

    # Read sensors
    power_uw = read_sysfs(f"{HWMON}/power1_average")
    temp_mc = read_sysfs(f"{HWMON}/temp1_input")
    freq_hz = read_sysfs(f"{HWMON}/freq1_input")
    vddgfx = read_sysfs(f"{HWMON}/in0_input")
    vddnb = read_sysfs(f"{HWMON}/in1_input")
    vram_b = read_sysfs(f"{DRM_DEV}/mem_info_vram_used")
    gtt_b = read_sysfs(f"{DRM_DEV}/mem_info_gtt_used")
    throttle = read_throttle_status()

    row = {
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        "power_w": round(power_uw / 1e6, 2) if power_uw is not None else "",
        "temp_c": round(temp_mc / 1000, 1) if temp_mc is not None else "",
        "freq_mhz": round(freq_hz / 1e6) if freq_hz is not None else "",
        "vddgfx_mv": vddgfx if vddgfx is not None else "",
        "vddnb_mv": vddnb if vddnb is not None else "",
        "vram_mb": round(vram_b / (1024**2)) if vram_b is not None else "",
        "gtt_mb": round(gtt_b / (1024**2)) if gtt_b is not None else "",
        "throttle_status": f"0x{throttle:04x}" if throttle is not None else "",
    }

    # Log throttle events to stderr for syslog visibility
    if throttle:
        reasons = decode_throttle(throttle)
        print(f"THROTTLE: 0x{throttle:04x} [{', '.join(reasons)}] "
              f"temp={row['temp_c']}°C power={row['power_w']}W",
              file=sys.stderr)

    fpath = csv_path()
    write_header = not fpath.exists()

    with open(fpath, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


# ── Chart Mode ─────────────────────────────────────────────────────────────

def load_csv(date_str=None):
    """Load CSV data for a given date, return list of dicts with parsed values."""
    fpath = csv_path(date_str)
    if not fpath.exists():
        print(f"No data file: {fpath}")
        return []

    rows = []
    with open(fpath) as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                # Parse throttle_status (hex string → int), backward-compatible
                ts_raw = r.get("throttle_status", "")
                if ts_raw and ts_raw.startswith("0x"):
                    throttle_val = int(ts_raw, 16)
                elif ts_raw:
                    throttle_val = int(ts_raw)
                else:
                    throttle_val = 0

                parsed = {
                    "time": datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S"),
                    "power_w": float(r["power_w"]) if r["power_w"] else None,
                    "temp_c": float(r["temp_c"]) if r["temp_c"] else None,
                    "freq_mhz": float(r["freq_mhz"]) if r["freq_mhz"] else None,
                    "vddgfx_mv": float(r["vddgfx_mv"]) if r["vddgfx_mv"] else None,
                    "vddnb_mv": float(r["vddnb_mv"]) if r["vddnb_mv"] else None,
                    "vram_mb": float(r["vram_mb"]) if r["vram_mb"] else None,
                    "gtt_mb": float(r["gtt_mb"]) if r["gtt_mb"] else None,
                    "throttle": throttle_val,
                }
                rows.append(parsed)
            except (ValueError, KeyError):
                continue
    return rows


def calc_throttle_stats(rows):
    """Calculate throttle duration and breakdown from rows.

    Each row = 1 minute sample. Returns dict with:
      total_minutes, thermal_minutes, power_minutes,
      by_reason (dict of reason -> minutes), episodes (list of start/end/reasons)
    """
    total = 0
    thermal = 0
    power = 0
    by_reason = {}
    episodes = []  # list of (start_time, end_time, reasons_set)
    current_episode_start = None
    current_reasons = set()
    prev_throttled = False

    for r in rows:
        ts = r.get("throttle", 0)
        if ts:
            total += 1
            reasons = decode_throttle(ts)
            for reason in reasons:
                by_reason[reason] = by_reason.get(reason, 0) + 1
            if ts & THERMAL_THROTTLE_MASK:
                thermal += 1
            if ts & POWER_THROTTLE_MASK:
                power += 1
            if not prev_throttled:
                current_episode_start = r["time"]
                current_reasons = set(reasons)
            else:
                current_reasons.update(reasons)
            prev_throttled = True
        else:
            if prev_throttled and current_episode_start:
                episodes.append((current_episode_start, r["time"], current_reasons))
            prev_throttled = False
            current_episode_start = None
            current_reasons = set()

    # Close any trailing episode
    if prev_throttled and current_episode_start and rows:
        episodes.append((current_episode_start, rows[-1]["time"], current_reasons))

    return {
        "total_minutes": total,
        "thermal_minutes": thermal,
        "power_minutes": power,
        "by_reason": by_reason,
        "episodes": episodes,
        "samples": len(rows),
    }


def setup_style():
    """Configure matplotlib for dark-themed charts."""
    import matplotlib
    matplotlib.use("Agg")  # headless backend
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    plt.rcParams.update({
        "figure.facecolor": COLORS["bg"],
        "axes.facecolor": COLORS["panel"],
        "axes.edgecolor": COLORS["grid"],
        "axes.labelcolor": COLORS["text"],
        "axes.grid": True,
        "grid.color": COLORS["grid"],
        "grid.alpha": 0.4,
        "text.color": COLORS["text"],
        "xtick.color": COLORS["text"],
        "ytick.color": COLORS["text"],
        "font.size": 11,
        "axes.titlesize": 14,
        "figure.titlesize": 16,
        "legend.facecolor": COLORS["panel"],
        "legend.edgecolor": COLORS["grid"],
        "legend.labelcolor": COLORS["text"],
    })
    return plt, mdates


def chart_power(rows, date_str, out_dir):
    """Generate power consumption chart: timeline + duration histogram."""
    plt, mdates = setup_style()

    times = [r["time"] for r in rows if r["power_w"] is not None]
    power = [r["power_w"] for r in rows if r["power_w"] is not None]
    if not power:
        return None

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6),
                                    gridspec_kw={"width_ratios": [2.5, 1]})
    fig.suptitle(f"BC-250 GPU Power Consumption  \u2014  {date_str}",
                 fontsize=16, fontweight="bold", color=COLORS["title"])

    # ── Left: Power over time ──
    ax1.plot(times, power, color=COLORS["power"], linewidth=1.2, alpha=0.9)
    ax1.fill_between(times, power, alpha=0.15, color=COLORS["power"])

    # Running average (5-sample window)
    if len(power) > 5:
        import numpy as np
        kernel = np.ones(5) / 5
        avg = np.convolve(power, kernel, mode="valid")
        ax1.plot(times[2:2+len(avg)], avg, color="#ffd166",
                 linewidth=2, alpha=0.8, label="5-min avg", linestyle="--")
        ax1.legend(loc="upper right")

    avg_power = sum(power) / len(power)
    max_power = max(power)
    min_power = min(power)
    ax1.axhline(y=avg_power, color="#ffd166", linewidth=1, linestyle=":",
                alpha=0.6)
    ax1.text(times[0], avg_power + 0.5, f"avg: {avg_power:.1f}W",
             color="#ffd166", fontsize=9, alpha=0.8)

    ax1.set_ylabel("Power (W)")
    ax1.set_xlabel("Time")
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax1.xaxis.set_major_locator(mdates.HourLocator(interval=2))
    ax1.set_title("Power Draw Over Time", color=COLORS["title"])

    # Stats box with cost estimate
    cost_info = calc_energy_cost(rows)
    stats = f"Min: {min_power:.1f}W\nAvg: {avg_power:.1f}W\nMax: {max_power:.1f}W"
    if cost_info and cost_info["hours"] > 0:
        wall_avg = cost_info["wall_avg_w"]
        daily_kwh = wall_avg * 24 / 1000
        daily_pln = daily_kwh * G11_PLN_PER_KWH
        monthly_pln = daily_pln * 30
        stats += (f"\n\u2500\u2500\u2500 est. wall: {wall_avg:.0f}W \u2500\u2500\u2500"
                  f"\n{daily_kwh:.2f} kWh/day"
                  f"\n{daily_pln:.2f} PLN/day"
                  f"\n{monthly_pln:.1f} PLN/mo")
    ax1.text(0.02, 0.97, stats, transform=ax1.transAxes, fontsize=10,
             verticalalignment="top", color=COLORS["text"],
             bbox=dict(boxstyle="round,pad=0.4", facecolor=COLORS["bg"],
                       edgecolor=COLORS["grid"], alpha=0.8))

    # ── Right: Power histogram (time at each power level) ──
    # Each sample = 1 minute, so count = minutes at that power level
    import numpy as np
    bins = np.arange(int(min_power) - 1, int(max_power) + 3, 1)
    if len(bins) < 3:
        bins = np.linspace(min_power - 1, max_power + 1, 20)

    counts, edges, patches = ax2.hist(power, bins=bins, orientation="horizontal",
                                       color=COLORS["power_hist"], alpha=0.8,
                                       edgecolor=COLORS["bg"], linewidth=0.5)
    # Color gradient based on power level
    import matplotlib.cm as cm
    norm = plt.Normalize(min_power, max_power)
    cmap = cm.YlOrRd
    for patch, edge in zip(patches, edges[:-1]):
        patch.set_facecolor(cmap(norm(edge)))

    ax2.set_ylabel("Power (W)")
    ax2.set_xlabel("Minutes at level")
    ax2.set_title("Time Distribution", color=COLORS["title"])

    plt.tight_layout()
    out = out_dir / f"gpu-{date_str}-power.png"
    fig.savefig(out, dpi=150, bbox_inches="tight",
                facecolor=COLORS["bg"], edgecolor="none")
    plt.close(fig)
    return out


def chart_temp(rows, date_str, out_dir):
    """Generate temperature chart: timeline + zone histogram."""
    plt, mdates = setup_style()

    times = [r["time"] for r in rows if r["temp_c"] is not None]
    temp = [r["temp_c"] for r in rows if r["temp_c"] is not None]
    if not temp:
        return None

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6),
                                    gridspec_kw={"width_ratios": [2.5, 1]})
    fig.suptitle(f"BC-250 GPU Temperature  \u2014  {date_str}",
                 fontsize=16, fontweight="bold", color=COLORS["title"])

    # ── Left: Temperature over time with thermal zones ──
    # Zone backgrounds
    ax1.axhspan(0, 60, alpha=0.08, color="#22c55e", label="Cool (<60°C)")
    ax1.axhspan(60, 75, alpha=0.08, color="#eab308", label="Warm (60-75°C)")
    ax1.axhspan(75, 100, alpha=0.08, color="#ef4444", label="Hot (>75°C)")

    ax1.plot(times, temp, color=COLORS["temp"], linewidth=1.2, alpha=0.9)
    ax1.fill_between(times, temp, alpha=0.12, color=COLORS["temp"])

    # Running average
    if len(temp) > 5:
        import numpy as np
        kernel = np.ones(5) / 5
        avg = np.convolve(temp, kernel, mode="valid")
        ax1.plot(times[2:2+len(avg)], avg, color="#a78bfa",
                 linewidth=2, alpha=0.8, label="5-min avg", linestyle="--")

    avg_temp = sum(temp) / len(temp)
    max_temp = max(temp)
    min_temp = min(temp)
    ax1.axhline(y=avg_temp, color="#a78bfa", linewidth=1, linestyle=":",
                alpha=0.6)
    ax1.text(times[0], avg_temp + 0.5, f"avg: {avg_temp:.1f}°C",
             color="#a78bfa", fontsize=9, alpha=0.8)

    ax1.set_ylabel("Temperature (°C)")
    ax1.set_xlabel("Time")
    ax1.set_ylim(max(0, min_temp - 5), max(max_temp + 5, 65))
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax1.xaxis.set_major_locator(mdates.HourLocator(interval=2))
    ax1.set_title("GPU Temperature Over Time", color=COLORS["title"])
    ax1.legend(loc="upper right", fontsize=9)

    # Throttle event overlay — red shaded regions on temp chart
    throttle_stats = calc_throttle_stats(rows)
    for ep_start, ep_end, ep_reasons in throttle_stats["episodes"]:
        ax1.axvspan(ep_start, ep_end, alpha=0.25, color="#ef4444",
                    zorder=0)

    # Stats box with throttle info
    stats = f"Min: {min_temp:.1f}°C\nAvg: {avg_temp:.1f}°C\nMax: {max_temp:.1f}°C"
    if throttle_stats["total_minutes"] > 0:
        stats += (f"\n\u2500\u2500 Throttle \u2500\u2500"
                  f"\n\u26a0 {throttle_stats['total_minutes']}min total"
                  f"\n  thermal: {throttle_stats['thermal_minutes']}min"
                  f"\n  power: {throttle_stats['power_minutes']}min"
                  f"\n  episodes: {len(throttle_stats['episodes'])}")
    else:
        stats += "\n\u2714 No throttling"
    ax1.text(0.02, 0.97, stats, transform=ax1.transAxes, fontsize=10,
             verticalalignment="top", color=COLORS["text"],
             bbox=dict(boxstyle="round,pad=0.4", facecolor=COLORS["bg"],
                       edgecolor=COLORS["grid"], alpha=0.8))

    # ── Right: Temperature zone histogram ──
    import numpy as np
    zone_labels = ["<50°C", "50-55°C", "55-60°C", "60-65°C", "65-70°C",
                   "70-75°C", "75-80°C", ">80°C"]
    zone_edges = [0, 50, 55, 60, 65, 70, 75, 80, 200]
    zone_colors = ["#22c55e", "#4ade80", "#86efac", "#fde047",
                   "#facc15", "#f97316", "#ef4444", "#dc2626"]

    counts, _ = np.histogram(temp, bins=zone_edges)
    bars = ax2.barh(zone_labels, counts, color=zone_colors, edgecolor=COLORS["bg"],
                    linewidth=0.5, alpha=0.85)

    # Add count labels on bars
    for bar, count in zip(bars, counts):
        if count > 0:
            ax2.text(bar.get_width() + 0.5, bar.get_y() + bar.get_height() / 2,
                     f"{count}m", va="center", fontsize=9, color=COLORS["text"])

    ax2.set_xlabel("Minutes in zone")
    ax2.set_title("Temperature Zones", color=COLORS["title"])

    plt.tight_layout()
    out = out_dir / f"gpu-{date_str}-temp.png"
    fig.savefig(out, dpi=150, bbox_inches="tight",
                facecolor=COLORS["bg"], edgecolor="none")
    plt.close(fig)
    return out


def chart_dashboard(rows, date_str, out_dir):
    """Generate combined dashboard: power, temp, frequency, memory + throttle."""
    plt, mdates = setup_style()
    import numpy as np

    # Use 3 rows: top 2x2 grid + bottom throttle timeline
    fig = plt.figure(figsize=(18, 14))
    gs = fig.add_gridspec(3, 2, height_ratios=[1, 1, 0.5], hspace=0.35)
    axes = [[fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[0, 1])],
            [fig.add_subplot(gs[1, 0]), fig.add_subplot(gs[1, 1])]]
    ax_throttle = fig.add_subplot(gs[2, :])
    fig.suptitle(f"BC-250 GPU Dashboard  \u2014  {date_str}",
                 fontsize=18, fontweight="bold", color=COLORS["title"], y=0.98)

    times = [r["time"] for r in rows]

    # ── Panel 1: Power ──
    ax = axes[0][0]
    power = [r["power_w"] for r in rows]
    valid_p = [(t, p) for t, p in zip(times, power) if p is not None]
    if valid_p:
        t_p, v_p = zip(*valid_p)
        ax.plot(t_p, v_p, color=COLORS["power"], linewidth=1, alpha=0.9)
        ax.fill_between(t_p, v_p, alpha=0.15, color=COLORS["power"])
        avg = sum(v_p) / len(v_p)
        ax.axhline(y=avg, color="#ffd166", linewidth=1, linestyle=":", alpha=0.6)
        wall_est = estimate_wall_power(avg)
        daily_pln = wall_est * 24 / 1000 * G11_PLN_PER_KWH
        ax.set_title(f"POWER  (avg: {avg:.1f}W, wall~{wall_est:.0f}W, ~{daily_pln:.2f} PLN/day)",
                     color=COLORS["power"])
    ax.set_ylabel("Watts")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=4))

    # ── Panel 2: Temperature ──
    ax = axes[0][1]
    temp = [r["temp_c"] for r in rows]
    valid_t = [(t, v) for t, v in zip(times, temp) if v is not None]
    if valid_t:
        t_t, v_t = zip(*valid_t)
        ax.axhspan(0, 60, alpha=0.06, color="#22c55e")
        ax.axhspan(60, 75, alpha=0.06, color="#eab308")
        ax.axhspan(75, 100, alpha=0.06, color="#ef4444")
        ax.plot(t_t, v_t, color=COLORS["temp"], linewidth=1, alpha=0.9)
        ax.fill_between(t_t, v_t, alpha=0.12, color=COLORS["temp"])
        avg = sum(v_t) / len(v_t)
        ax.axhline(y=avg, color="#a78bfa", linewidth=1, linestyle=":", alpha=0.6)
        # Mark throttle events with red spans on temperature panel
        ts_stats = calc_throttle_stats(rows)
        for ep_start, ep_end, _ in ts_stats["episodes"]:
            ax.axvspan(ep_start, ep_end, alpha=0.2, color="#ef4444", zorder=0)
        if ts_stats["total_minutes"] > 0:
            ax.set_title(
                f"TEMP  (avg: {avg:.1f}\u00b0C, max: {max(v_t):.1f}\u00b0C, "
                f"\u26a0 throttled {ts_stats['total_minutes']}min)",
                color="#ef4444")
        else:
            ax.set_title(f"TEMP  (avg: {avg:.1f}\u00b0C, max: {max(v_t):.1f}\u00b0C)",
                         color=COLORS["temp"])
    ax.set_ylabel("°C")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=4))

    # ── Panel 3: GPU Clock Frequency ──
    ax = axes[1][0]
    freq = [r["freq_mhz"] for r in rows]
    valid_f = [(t, v) for t, v in zip(times, freq) if v is not None]
    if valid_f:
        t_f, v_f = zip(*valid_f)
        ax.plot(t_f, v_f, color=COLORS["freq"], linewidth=1, alpha=0.9)
        ax.fill_between(t_f, v_f, alpha=0.1, color=COLORS["freq"])

        # Show DPM levels as horizontal lines
        for level, mhz in [(0, 1000), (1, 1500), (2, 2000)]:
            ax.axhline(y=mhz, color=COLORS["grid"], linewidth=0.8,
                       linestyle="--", alpha=0.3)
            ax.text(t_f[0], mhz + 20, f"DPM{level}: {mhz}MHz",
                    fontsize=8, color=COLORS["text"], alpha=0.5)

        # Percentage at each level
        at_1000 = sum(1 for v in v_f if v <= 1100)
        at_1500 = sum(1 for v in v_f if 1100 < v <= 1700)
        at_2000 = sum(1 for v in v_f if v > 1700)
        total = len(v_f)
        ax.set_title(
            f"CLOCK  (1GHz: {at_1000*100//total}%, "
            f"1.5GHz: {at_1500*100//total}%, 2GHz: {at_2000*100//total}%)",
            color=COLORS["freq"])
    ax.set_ylabel("MHz")
    ax.set_ylim(800, 2200)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=4))

    # ── Panel 4: Memory Usage ──
    ax = axes[1][1]
    vram = [r["vram_mb"] for r in rows]
    gtt = [r["gtt_mb"] for r in rows]
    valid_v = [(t, v) for t, v in zip(times, vram) if v is not None]
    valid_g = [(t, v) for t, v in zip(times, gtt) if v is not None]
    if valid_v:
        t_v, v_v = zip(*valid_v)
        ax.plot(t_v, [v / 1024 for v in v_v], color=COLORS["vram"],
                linewidth=1.5, alpha=0.9, label="VRAM")
    if valid_g:
        t_g, v_g = zip(*valid_g)
        ax.plot(t_g, [v / 1024 for v in v_g], color=COLORS["gtt"],
                linewidth=1.5, alpha=0.9, label="GTT")
        # Capacity lines
        ax.axhline(y=0.5, color=COLORS["vram"], linewidth=0.8,
                   linestyle="--", alpha=0.3)
        ax.axhline(y=13.0, color=COLORS["gtt"], linewidth=0.8,
                   linestyle="--", alpha=0.3)
    ax.set_ylabel("GB")
    ax.set_title("MEMORY  (VRAM + GTT)", color=COLORS["vram"])
    ax.legend(loc="upper right", fontsize=9)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=4))

    # ── Panel 5: Throttle Timeline (bottom, full width) ──
    ax = ax_throttle
    throttle_vals = [r.get("throttle", 0) for r in rows]
    has_throttle_data = any(v != 0 for v in throttle_vals)

    if has_throttle_data:
        # Plot thermal vs power throttle as stacked colored regions
        thermal_t = []
        thermal_v = []
        power_t = []
        power_v = []
        for r in rows:
            ts = r.get("throttle", 0)
            t = r["time"]
            thermal_t.append(t)
            thermal_v.append(1 if ts & THERMAL_THROTTLE_MASK else 0)
            power_t.append(t)
            power_v.append(1 if ts & POWER_THROTTLE_MASK else 0)

        ax.fill_between(thermal_t, thermal_v, step="post",
                        alpha=0.6, color="#ef4444", label="Thermal")
        ax.fill_between(power_t, [v * 0.5 for v in power_v], step="post",
                        alpha=0.6, color="#f97316", label="Power limit")

        throttle_stats = calc_throttle_stats(rows)
        total_min = throttle_stats["total_minutes"]
        pct = total_min * 100 / len(rows) if rows else 0

        # Episode markers
        for ep_start, ep_end, ep_reasons in throttle_stats["episodes"]:
            duration = (ep_end - ep_start).total_seconds() / 60
            if duration >= 3:  # label episodes >= 3 min
                mid = ep_start + (ep_end - ep_start) / 2
                ax.annotate(f"{duration:.0f}m", xy=(mid, 0.85),
                            fontsize=8, color="#ffffff", ha="center",
                            fontweight="bold")

        ax.set_title(
            f"THROTTLE  ({total_min}min = {pct:.1f}% of day, "
            f"{len(throttle_stats['episodes'])} episodes)",
            color="#ef4444")
        ax.legend(loc="upper right", fontsize=9)
    else:
        ax.text(0.5, 0.5, "\u2714  No throttling detected",
                transform=ax.transAxes, ha="center", va="center",
                fontsize=14, color="#22c55e", fontweight="bold")
        ax.set_title("THROTTLE  (none)", color="#22c55e")

    ax.set_yticks([0, 1])
    ax.set_yticklabels(["Normal", "Throttled"])
    ax.set_ylim(-0.1, 1.3)
    ax.set_xlabel("Time")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=2))

    plt.tight_layout(rect=[0, 0, 1, 0.96])
    out = out_dir / f"gpu-{date_str}-dashboard.png"
    fig.savefig(out, dpi=150, bbox_inches="tight",
                facecolor=COLORS["bg"], edgecolor="none")
    plt.close(fig)
    return out


def generate_charts(date_str=None):
    """Generate all charts for a given date."""
    if not date_str:
        date_str = datetime.now().strftime("%Y%m%d")

    rows = load_csv(date_str)
    if not rows:
        print(f"No data for {date_str}")
        return

    print(f"Generating charts for {date_str} ({len(rows)} samples)...")

    out_dir = GPU_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    p = chart_power(rows, date_str, out_dir)
    if p:
        print(f"  ⚡ Power chart: {p}")

    t = chart_temp(rows, date_str, out_dir)
    if t:
        print(f"  🌡️  Temp chart:  {t}")

    d = chart_dashboard(rows, date_str, out_dir)
    if d:
        print(f"  📊 Dashboard:   {d}")

    # Print throttle summary
    ts = calc_throttle_stats(rows)
    if ts["total_minutes"] > 0:
        pct = ts["total_minutes"] * 100 / ts["samples"]
        print(f"  ⚠️  Throttled:   {ts['total_minutes']}min ({pct:.1f}%) "
              f"in {len(ts['episodes'])} episodes")
        if ts["thermal_minutes"]:
            print(f"       thermal: {ts['thermal_minutes']}min")
        if ts["power_minutes"]:
            print(f"       power:   {ts['power_minutes']}min")
        for reason, mins in sorted(ts["by_reason"].items(), key=lambda x: -x[1]):
            print(f"       {reason}: {mins}min")
    else:
        print(f"  ✅ No throttling detected")

    # Cleanup: keep last 30 days of charts + CSVs
    for pattern in ["gpu-*-power.png", "gpu-*-temp.png", "gpu-*-dashboard.png", "gpu-*.csv"]:
        files = sorted(GPU_DIR.glob(pattern))
        for old in files[:-30]:
            old.unlink(missing_ok=True)

    print(f"Done — {len(rows)} samples charted")


# ── Cost Report ────────────────────────────────────────────────────────────

def cost_report(date_str=None, days=None):
    """Print electricity cost report for a date or date range."""
    if days:
        # Multi-day summary
        today = datetime.now()
        all_rows = []
        daily_stats = []
        for d in range(days):
            dt = today - timedelta(days=d)
            ds = dt.strftime("%Y%m%d")
            day_rows = load_csv(ds)
            if day_rows:
                info = calc_energy_cost(day_rows)
                if info:
                    daily_stats.append((ds, info))
                    all_rows.extend(day_rows)

        if not daily_stats:
            print("No data found")
            return

        print(f"\n{'='*60}")
        print(f"  BC-250 Electricity Cost Report  ({days}-day lookback)")
        print(f"  G11 tariff: {G11_PLN_PER_KWH:.2f} PLN/kWh (PGE Łódź 2026)")
        print(f"  System overhead: +{SYSTEM_OVERHEAD_W}W, PSU eff: {PSU_EFFICIENCY*100:.0f}%")
        print(f"{'='*60}\n")

        total_kwh = 0
        total_pln = 0
        for ds, info in sorted(daily_stats):
            # Project full 24h from the samples we have
            proj_kwh = info["wall_avg_w"] * 24 / 1000
            proj_pln = proj_kwh * G11_PLN_PER_KWH
            total_kwh += proj_kwh
            total_pln += proj_pln
            print(f"  {ds}  {info['hours']:5.1f}h sampled  "
                  f"PPT avg {info['ppt_avg_w']:5.1f}W  "
                  f"wall ~{info['wall_avg_w']:.0f}W  "
                  f"~{proj_kwh:.2f} kWh/day  "
                  f"~{proj_pln:.2f} PLN/day")

        n = len(daily_stats)
        avg_daily_kwh = total_kwh / n
        avg_daily_pln = total_pln / n
        print(f"\n  {'─'*56}")
        print(f"  Average:  {avg_daily_kwh:.2f} kWh/day  =  {avg_daily_pln:.2f} PLN/day")
        print(f"  Monthly:  {avg_daily_kwh*30:.1f} kWh/mo   =  {avg_daily_pln*30:.1f} PLN/mo")
        print(f"  Yearly:   {avg_daily_kwh*365:.0f} kWh/yr   =  {avg_daily_pln*365:.0f} PLN/yr")
        print()

    else:
        # Single day
        if not date_str:
            date_str = datetime.now().strftime("%Y%m%d")
        rows = load_csv(date_str)
        if not rows:
            print(f"No data for {date_str}")
            return

        info = calc_energy_cost(rows)
        if not info:
            print("No power data in CSV")
            return

        proj_kwh = info["wall_avg_w"] * 24 / 1000
        proj_pln = proj_kwh * G11_PLN_PER_KWH

        print(f"\n  BC-250 Power Cost  —  {date_str}")
        print(f"  {'─'*44}")
        print(f"  Samples:       {info['samples']} ({info['hours']:.1f} hours)")
        print(f"  PPT (SoC):     avg {info['ppt_avg_w']:.1f}W, max {info['ppt_max_w']:.1f}W")
        print(f"  Est. wall:     avg {info['wall_avg_w']:.0f}W, max {info['wall_max_w']:.0f}W")
        print(f"  Measured:      {info['energy_kwh']:.3f} kWh in {info['hours']:.1f}h")
        print(f"  Measured cost: {info['cost_pln']:.3f} PLN")
        print(f"  {'─'*44}")
        print(f"  Projected 24h: {proj_kwh:.2f} kWh  =  {proj_pln:.2f} PLN/day")
        print(f"  Monthly est:   {proj_kwh*30:.1f} kWh  =  {proj_pln*30:.1f} PLN/mo")
        print(f"  Yearly est:    {proj_kwh*365:.0f} kWh  =  {proj_pln*365:.0f} PLN/yr")
        print(f"  G11 tariff:    {G11_PLN_PER_KWH:.2f} PLN/kWh (PGE Łódź 2026)")

        # Throttle summary
        ts = calc_throttle_stats(rows)
        print(f"  {'─'*44}")
        if ts["total_minutes"] > 0:
            pct = ts["total_minutes"] * 100 / ts["samples"]
            print(f"  Throttled:     {ts['total_minutes']}min ({pct:.1f}%), "
                  f"{len(ts['episodes'])} episodes")
            if ts["thermal_minutes"]:
                print(f"    thermal:     {ts['thermal_minutes']}min")
            if ts["power_minutes"]:
                print(f"    power limit: {ts['power_minutes']}min")
            for reason, mins in sorted(ts["by_reason"].items(), key=lambda x: -x[1]):
                print(f"    {reason:12s}  {mins}min")
        else:
            print(f"  Throttled:     none ✓")
        print()


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: gpu-monitor.py <collect|chart|cost> [YYYYMMDD|--days=N]")
        print("  collect         — append one sensor reading to today's CSV")
        print("  chart           — generate charts for today (or given date)")
        print("  chart YYYYMMDD  — generate charts for specific date")
        print("  cost            — show today's electricity cost estimate")
        print("  cost YYYYMMDD   — cost for specific date")
        print("  cost --days=N   — cost summary for last N days")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "collect":
        collect()

    elif cmd == "chart":
        date_str = sys.argv[2] if len(sys.argv) > 2 else None
        generate_charts(date_str)

    elif cmd == "cost":
        if len(sys.argv) > 2 and sys.argv[2].startswith("--days="):
            days = int(sys.argv[2].split("=")[1])
            cost_report(days=days)
        else:
            date_str = sys.argv[2] if len(sys.argv) > 2 else None
            cost_report(date_str)

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
