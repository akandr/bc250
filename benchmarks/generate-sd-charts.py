#!/usr/bin/env python3
"""Generate SD pipeline benchmark charts for BC-250 README.

All timings measured on BC-250 (GFX1013, Vulkan, 16 GB UMA, DDR4-3200).
sd.cpp master-525-d6dd6d7, --offload-to-cpu --fa.
"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

OUT_DIR = "../images/charts"

# ── Color palette ──────────────────────────────────────────────────────
C_FLUX   = "#4a90d9"
C_SD3    = "#e87d5f"
C_TURBO  = "#5cb85c"
C_KONTEXT= "#9b59b6"
C_WAN    = "#e67e22"
C_ESRGAN = "#2ecc71"
C_DARK   = "#2c3e50"

plt.rcParams.update({
    'figure.facecolor': '#0d1117',
    'axes.facecolor': '#161b22',
    'axes.edgecolor': '#30363d',
    'axes.labelcolor': '#c9d1d9',
    'text.color': '#c9d1d9',
    'xtick.color': '#8b949e',
    'ytick.color': '#8b949e',
    'grid.color': '#21262d',
    'font.family': 'sans-serif',
    'font.size': 11,
})


# ══════════════════════════════════════════════════════════════════════
# CHART 1: Full SD Pipeline — Horizontal Bar Chart
# ══════════════════════════════════════════════════════════════════════

def chart_pipeline():
    """Horizontal bars showing full pipeline time for each generation mode."""
    pipelines = [
        ("SD-Turbo 512²\n(1 step)", 11, C_TURBO, "11s"),
        ("SD-Turbo 512²\n(4 steps)", 11, C_TURBO, "11s"),
        ("SD3.5-medium 512²\n(28 steps)", 49, C_SD3, "49s"),
        ("FLUX.1-schnell 512²\n(4 steps)", 56, C_FLUX, "56s"),
        ("FLUX.1-schnell 768²\n(4 steps, tiling)", 91, C_FLUX, "91s"),
        ("FLUX.1-schnell 1024²\n(4 steps, tiling)", 146, C_FLUX, "2m 26s"),
        ("Kontext edit 512²\n(28 steps)", 316, C_KONTEXT, "5m 16s"),
        ("Kontext edit 512²\n(from 1200×1600 input)", 647, C_KONTEXT, "10m 47s"),
        ("WAN 2.1 T2V 480×320\n(50 steps, 17 frames)", 2280, C_WAN, "38m"),
    ]

    labels = [p[0] for p in pipelines]
    times = [p[1] for p in pipelines]
    colors = [p[2] for p in pipelines]
    annotations = [p[3] for p in pipelines]

    fig, ax = plt.subplots(figsize=(14, 7))
    y_pos = np.arange(len(labels))

    bars = ax.barh(y_pos, times, color=colors, height=0.6,
                   edgecolor='#30363d', linewidth=0.5)

    for bar, ann in zip(bars, annotations):
        w = bar.get_width()
        xoff = max(w * 0.02, 15)
        ax.text(w + xoff, bar.get_y() + bar.get_height()/2,
                f" {ann}", va='center', ha='left',
                fontsize=10, fontweight='bold', color='#c9d1d9')

    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=10)
    ax.invert_yaxis()
    ax.set_xlabel("Time (seconds)", fontsize=12)
    ax.set_title("BC-250 Image/Video Pipeline — End-to-End Timing",
                 fontsize=14, fontweight='bold', pad=15)

    # Log scale for readability across 11s–2280s range
    ax.set_xscale('log')
    ax.set_xlim(8, 5000)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(
        lambda x, _: f"{int(x)}s" if x < 60 else f"{int(x//60)}m{int(x%60):02d}s" if x % 60 else f"{int(x//60)}m"))
    ax.grid(axis='x', alpha=0.3)

    # Legend
    patches = [
        mpatches.Patch(color=C_TURBO, label='SD-Turbo (fastest)'),
        mpatches.Patch(color=C_SD3, label='SD3.5-medium'),
        mpatches.Patch(color=C_FLUX, label='FLUX.1-schnell'),
        mpatches.Patch(color=C_KONTEXT, label='FLUX.1-Kontext (edit)'),
        mpatches.Patch(color=C_WAN, label='WAN 2.1 T2V (video)'),
    ]
    ax.legend(handles=patches, loc='lower right', fontsize=9,
              facecolor='#161b22', edgecolor='#30363d')

    plt.tight_layout()
    fig.savefig(f"{OUT_DIR}/sd-pipeline-timing.png", dpi=150,
                bbox_inches='tight', facecolor=fig.get_facecolor())
    plt.close()
    print(f"  ✓ sd-pipeline-timing.png")


# ══════════════════════════════════════════════════════════════════════
# CHART 2: ESRGAN Upscale Benchmark — Grouped Bar
# ══════════════════════════════════════════════════════════════════════

def chart_esrgan():
    """Bar chart: ESRGAN tile size vs time, plus 16× double-pass."""
    configs = [
        ("4× tile 128\n512²→2048²", 15, "2048²\n5.1 MB"),
        ("4× tile 192\n512²→2048²", 25, "2048²\n5.1 MB"),
        ("4× tile 256\n512²→2048²", 41, "2048²\n5.1 MB"),
        ("16× (2 passes)\ntile 128\n512²→8192²", 290, "8192²\n67 MB"),
    ]

    labels = [c[0] for c in configs]
    times = [c[1] for c in configs]
    outputs = [c[2] for c in configs]

    fig, ax = plt.subplots(figsize=(10, 6))
    x = np.arange(len(labels))

    # Gradient colors from green (fast) to orange (slow)
    colors = ['#27ae60', '#2ecc71', '#f39c12', '#e74c3c']

    bars = ax.bar(x, times, color=colors, width=0.55,
                  edgecolor='#30363d', linewidth=1)

    for bar, t, out in zip(bars, times, outputs):
        # Time on top of bar
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 5,
                f"{t}s", ha='center', va='bottom',
                fontsize=13, fontweight='bold', color='#c9d1d9')
        # Output info inside bar
        ax.text(bar.get_x() + bar.get_width()/2,
                bar.get_height() * 0.5,
                out, ha='center', va='center',
                fontsize=9, color='white', alpha=0.9)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_ylabel("Time (seconds)", fontsize=12)
    ax.set_title("ESRGAN 4× Upscale — RealESRGAN_x4plus on BC-250\n"
                 "Input: 512×512 generated image, GFX1013 Vulkan",
                 fontsize=13, fontweight='bold', pad=15)
    ax.set_ylim(0, 340)
    ax.grid(axis='y', alpha=0.3)

    # Add "production" arrow
    ax.annotate("← production\n   (tile 192)", xy=(1, 25), xytext=(1.8, 80),
                fontsize=10, color='#2ecc71', fontweight='bold',
                arrowprops=dict(arrowstyle='->', color='#2ecc71', lw=1.5))

    plt.tight_layout()
    fig.savefig(f"{OUT_DIR}/esrgan-upscale-bench.png", dpi=150,
                bbox_inches='tight', facecolor=fig.get_facecolor())
    plt.close()
    print(f"  ✓ esrgan-upscale-bench.png")


# ══════════════════════════════════════════════════════════════════════
# CHART 3: Image Gen Pipeline Breakdown — Stacked Bar
# ══════════════════════════════════════════════════════════════════════

def chart_breakdown():
    """Stacked horizontal bars showing phase breakdown for each pipeline."""
    # Pipeline: (name, [(phase, time, color), ...])
    pipelines = [
        ("SD-Turbo\n512² (1 step)", [
            ("Model load", 3, "#636e72"),
            ("Diffusion", 6, C_TURBO),
            ("VAE decode", 1.5, "#74b9ff"),
            ("Save", 0.5, "#dfe6e9"),
        ]),
        ("SD3.5-medium\n512² (28 steps)", [
            ("CLIP+T5 encode", 3.5, "#fdcb6e"),
            ("Diffusion", 43, C_SD3),
            ("VAE decode", 2.3, "#74b9ff"),
            ("Save", 0.2, "#dfe6e9"),
        ]),
        ("FLUX.1-schnell\n512² (4 steps)", [
            ("CLIP+T5 encode", 8, "#fdcb6e"),
            ("Diffusion", 42, C_FLUX),
            ("VAE decode", 4, "#74b9ff"),
            ("Save", 2, "#dfe6e9"),
        ]),
        ("FLUX.1-schnell\n512² + ESRGAN 4×", [
            ("CLIP+T5 encode", 8, "#fdcb6e"),
            ("Diffusion", 42, C_FLUX),
            ("VAE decode", 4, "#74b9ff"),
            ("ESRGAN 4× (tile 192)", 25, C_ESRGAN),
            ("Save", 2, "#dfe6e9"),
        ]),
        ("Kontext edit\n512² (28 steps)", [
            ("CLIP+T5 encode", 10, "#fdcb6e"),
            ("Diffusion", 280, C_KONTEXT),
            ("VAE decode", 10, "#74b9ff"),
            ("Save", 1, "#dfe6e9"),
        ]),
        ("WAN 2.1 T2V\n480×320 (17 frames)", [
            ("umt5 encode", 4, "#fdcb6e"),
            ("Diffusion (17f×50s)", 2100, C_WAN),
            ("VAE decode", 30, "#74b9ff"),
            ("Save", 6, "#dfe6e9"),
        ]),
    ]

    fig, ax = plt.subplots(figsize=(14, 7))

    y_pos = np.arange(len(pipelines))
    all_phase_names = set()

    for i, (name, phases) in enumerate(pipelines):
        left = 0
        for phase_name, t, color in phases:
            all_phase_names.add(phase_name)
            ax.barh(i, t, left=left, color=color, height=0.55,
                    edgecolor='#30363d', linewidth=0.5)
            # Label inside bar if wide enough
            if t > 15:
                ax.text(left + t/2, i, f"{phase_name}\n{t:.0f}s",
                        ha='center', va='center', fontsize=7,
                        color='white', fontweight='bold')
            left += t
        # Total at end
        ax.text(left + 8, i, f"Σ {left:.0f}s",
                va='center', fontsize=9, fontweight='bold', color='#8b949e')

    ax.set_yticks(y_pos)
    ax.set_yticklabels([p[0] for p in pipelines], fontsize=10)
    ax.invert_yaxis()
    ax.set_xlabel("Time (seconds)", fontsize=12)
    ax.set_title("BC-250 Image Pipeline — Phase Breakdown\n"
                 "GFX1013 Vulkan · sd.cpp master-525 · --offload-to-cpu --fa",
                 fontsize=13, fontweight='bold', pad=15)
    ax.set_xscale('log')
    ax.set_xlim(1, 5000)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(
        lambda x, _: f"{int(x)}s" if x < 60 else f"{int(x//60)}m"))
    ax.grid(axis='x', alpha=0.3)

    # Legend
    legend_items = [
        ("Encode (CLIP/T5)", "#fdcb6e"),
        ("Diffusion", "#6c7a89"),
        ("VAE decode", "#74b9ff"),
        ("ESRGAN 4×", C_ESRGAN),
        ("Save/overhead", "#dfe6e9"),
    ]
    patches = [mpatches.Patch(color=c, label=l) for l, c in legend_items]
    ax.legend(handles=patches, loc='lower right', fontsize=9,
              facecolor='#161b22', edgecolor='#30363d')

    plt.tight_layout()
    fig.savefig(f"{OUT_DIR}/sd-pipeline-breakdown.png", dpi=150,
                bbox_inches='tight', facecolor=fig.get_facecolor())
    plt.close()
    print(f"  ✓ sd-pipeline-breakdown.png")


# ══════════════════════════════════════════════════════════════════════
# CHART 4: Resolution Scaling — Line Chart
# ══════════════════════════════════════════════════════════════════════

def chart_resolution():
    """Line chart: FLUX.1-schnell time vs resolution."""
    resolutions = ["512²", "768×512", "1024×576", "768²", "1024²"]
    pixels_k = [262, 393, 590, 590, 1049]  # in thousands (real values)
    times = [56, 66, 86, 91, 146]

    fig, ax = plt.subplots(figsize=(10, 6))

    ax.plot(pixels_k, times, 'o-', color=C_FLUX, linewidth=2.5,
            markersize=10, markeredgecolor='white', markeredgewidth=1.5,
            zorder=5)

    # Labels: above the dot by default.
    # For the two points sharing x=590K, one goes above and one below.
    label_cfg = {
        "512²":     (0,  16),   # above
        "768×512":  (0,  16),   # above
        "1024×576": (0, -42),   # BELOW (2-line label height clearance)
        "768²":     (0,  16),   # above
        "1024²":    (0,  16),   # above
    }
    va_cfg = {
        "1024×576": "top",      # text top aligns near the dot (text hangs down)
    }
    for px, t, res in zip(pixels_k, times, resolutions):
        ox, oy = label_cfg[res]
        ax.annotate(f"{res}\n{t}s", (px, t),
                    textcoords="offset points", xytext=(ox, oy),
                    ha='center', va=va_cfg.get(res, 'bottom'),
                    fontsize=9, fontweight='bold',
                    color='#c9d1d9')

    # Trend line
    z = np.polyfit(pixels_k, times, 1)
    p = np.poly1d(z)
    x_smooth = np.linspace(200, 1200, 100)
    ax.plot(x_smooth, p(x_smooth), '--', color=C_FLUX, alpha=0.3, linewidth=1)

    ax.set_xlabel("Resolution (thousands of pixels)", fontsize=12)
    ax.set_ylabel("Generation time (seconds)", fontsize=12)
    ax.set_title("FLUX.1-schnell — Time vs Resolution on BC-250\n"
                 "4 steps, Q4_K, --offload-to-cpu --fa, vae-tiling where needed",
                 fontsize=13, fontweight='bold', pad=15)
    ax.grid(alpha=0.3)
    ax.set_ylim(20, 185)

    plt.tight_layout()
    fig.savefig(f"{OUT_DIR}/flux-resolution-scaling.png", dpi=150,
                bbox_inches='tight', facecolor=fig.get_facecolor())
    plt.close()
    print(f"  ✓ flux-resolution-scaling.png")


if __name__ == "__main__":
    import os
    os.makedirs(OUT_DIR, exist_ok=True)
    print("Generating SD pipeline charts...")
    chart_pipeline()
    chart_esrgan()
    chart_breakdown()
    chart_resolution()
    print("Done!")
