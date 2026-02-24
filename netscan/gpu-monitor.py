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
]

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


# ── Helpers ────────────────────────────────────────────────────────────────

def read_sysfs(path):
    """Read integer from sysfs file, return None on failure."""
    try:
        return int(Path(path).read_text().strip())
    except Exception:
        return None


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

    row = {
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        "power_w": round(power_uw / 1e6, 2) if power_uw is not None else "",
        "temp_c": round(temp_mc / 1000, 1) if temp_mc is not None else "",
        "freq_mhz": round(freq_hz / 1e6) if freq_hz is not None else "",
        "vddgfx_mv": vddgfx if vddgfx is not None else "",
        "vddnb_mv": vddnb if vddnb is not None else "",
        "vram_mb": round(vram_b / (1024**2)) if vram_b is not None else "",
        "gtt_mb": round(gtt_b / (1024**2)) if gtt_b is not None else "",
    }

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
                parsed = {
                    "time": datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S"),
                    "power_w": float(r["power_w"]) if r["power_w"] else None,
                    "temp_c": float(r["temp_c"]) if r["temp_c"] else None,
                    "freq_mhz": float(r["freq_mhz"]) if r["freq_mhz"] else None,
                    "vddgfx_mv": float(r["vddgfx_mv"]) if r["vddgfx_mv"] else None,
                    "vddnb_mv": float(r["vddnb_mv"]) if r["vddnb_mv"] else None,
                    "vram_mb": float(r["vram_mb"]) if r["vram_mb"] else None,
                    "gtt_mb": float(r["gtt_mb"]) if r["gtt_mb"] else None,
                }
                rows.append(parsed)
            except (ValueError, KeyError):
                continue
    return rows


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

    # Stats box
    stats = f"Min: {min_power:.1f}W\nAvg: {avg_power:.1f}W\nMax: {max_power:.1f}W"
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

    # Stats box
    stats = f"Min: {min_temp:.1f}°C\nAvg: {avg_temp:.1f}°C\nMax: {max_temp:.1f}°C"
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
    """Generate combined 4-panel dashboard: power, temp, frequency, memory."""
    plt, mdates = setup_style()
    import numpy as np

    fig, axes = plt.subplots(2, 2, figsize=(18, 10))
    fig.suptitle(f"BC-250 GPU Dashboard  \u2014  {date_str}",
                 fontsize=18, fontweight="bold", color=COLORS["title"], y=0.98)

    times = [r["time"] for r in rows]

    # ── Panel 1: Power ──
    ax = axes[0, 0]
    power = [r["power_w"] for r in rows]
    valid_p = [(t, p) for t, p in zip(times, power) if p is not None]
    if valid_p:
        t_p, v_p = zip(*valid_p)
        ax.plot(t_p, v_p, color=COLORS["power"], linewidth=1, alpha=0.9)
        ax.fill_between(t_p, v_p, alpha=0.15, color=COLORS["power"])
        avg = sum(v_p) / len(v_p)
        ax.axhline(y=avg, color="#ffd166", linewidth=1, linestyle=":", alpha=0.6)
        ax.set_title(f"POWER  (avg: {avg:.1f}W, max: {max(v_p):.1f}W)",
                     color=COLORS["power"])
    ax.set_ylabel("Watts")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=4))

    # ── Panel 2: Temperature ──
    ax = axes[0, 1]
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
        ax.set_title(f"TEMP  (avg: {avg:.1f}\u00b0C, max: {max(v_t):.1f}\u00b0C)",
                     color=COLORS["temp"])
    ax.set_ylabel("°C")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=4))

    # ── Panel 3: GPU Clock Frequency ──
    ax = axes[1, 0]
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
    ax = axes[1, 1]
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

    plt.tight_layout(rect=[0, 0, 1, 0.95])
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

    # Cleanup: keep last 30 days of charts + CSVs
    for pattern in ["gpu-*-power.png", "gpu-*-temp.png", "gpu-*-dashboard.png", "gpu-*.csv"]:
        files = sorted(GPU_DIR.glob(pattern))
        for old in files[:-30]:
            old.unlink(missing_ok=True)

    print(f"Done — {len(rows)} samples charted")


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: gpu-monitor.py <collect|chart> [YYYYMMDD]")
        print("  collect         — append one sensor reading to today's CSV")
        print("  chart           — generate charts for today (or given date)")
        print("  chart YYYYMMDD  — generate charts for specific date")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "collect":
        collect()

    elif cmd == "chart":
        date_str = sys.argv[2] if len(sys.argv) > 2 else None
        generate_charts(date_str)

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
