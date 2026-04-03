#!/usr/bin/env python3
"""Generate benchmark charts for the BC-250 README.
Data refreshed 2026-03-31 full rerun (generation, prefill, context sweep).
Each chart shows a specific subset; see per-function comments for scope.
Dark theme matching existing charts.
"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import os

OUT_DIR = "../images/charts"
os.makedirs(OUT_DIR, exist_ok=True)

# ── Color palette ──────────────────────────────────────────────────────
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

C_GOLD   = "#f0c040"
C_BLUE   = "#4a90d9"
C_GREEN  = "#2ecc71"
C_ORANGE = "#e67e22"
C_RED    = "#e74c3c"
C_PURPLE = "#9b59b6"
C_TEAL   = "#1abc9c"
C_PINK   = "#e84393"
C_GRAY   = "#8b949e"


# ══════════════════════════════════════════════════════════════════════
# CHART 1: Generation Speed — 30 of 32 Models (horizontal bar)
# 2 redundant 14B variants omitted per BR §3 (see qwen3-abl:14b, qwen3-14b-nothink)
# ══════════════════════════════════════════════════════════════════════
def chart_gen_speed_all():
    # Data refreshed 2026-03-31 full rerun
    models = [
        ("llama3.2:3b",              103.7, "3B"),
        ("qwen2.5:3b",              101.9, "3B"),
        ("phi4-mini",                87.5, "4B"),
        ("gemma3:4b",                76.7, "4B"),
        ("qwen3:4b",                 74.0, "4B"),
        ("Qwen3-Coder-30B-A3B",     62.8, "MoE"),
        ("Qwen3-30B-A3B (Q2_K)",      59.4, "MoE"),
        ("qwen2.5:7b",              55.0, "7B"),
        ("qwen2.5-coder:7b",        54.8, "7B"),
        ("llama3.1:8b",              51.5, "8B"),
        ("seed-coder-abl:8b",       50.8, "8B"),
        ("lexi-8b",                 50.1, "8B"),
        ("granite3.3:8b",           45.9, "8B"),
        ("qwen3-abl:8b",            45.5, "8B"),
        ("qwen3-abliterated:8b",    45.5, "8B"),
        ("glm4:9b",                 45.3, "9B"),
        ("deepseek-r1:8b",          43.3, "8B"),
        ("qwen3:8b-nothink",        43.2, "8B"),
        ("qwen3:8b",                43.0, "8B"),
        ("gemma2:9b",               38.5, "9B"),
        ("★ MoE 35B-A3B",        37.7, "MoE"),
        ("mistral-nemo:12b",        34.1, "12B"),
        ("★ qwen3.5:9b",           31.9, "9B"),
        ("qwen3:8b-q8_0",           31.1, "8B"),
        ("gemma3:12b",              29.1, "12B"),
        ("deepseek-r1:14b",         28.8, "14B"),
        ("phi4:14b",                28.6, "14B"),
        ("qwen3-abl:14b",           27.5, "14B"),
        ("qwen3:14b",               26.9, "14B"),
        ("qwen3.5-27b-iq2m",       11.0, "27B"),
    ]

    models.reverse()
    names  = [m[0] for m in models]
    speeds = [m[1] for m in models]
    sizes  = [m[2] for m in models]

    size_colors = {
        "3B": "#2ecc71", "4B": "#1abc9c", "7B": "#3498db",
        "8B": "#4a90d9", "9B": "#e67e22", "MoE": "#f0c040",
        "12B": "#e84393", "14B": "#e74c3c", "27B": "#8b949e",
    }
    colors = [size_colors.get(s, C_GRAY) for s in sizes]

    fig, ax = plt.subplots(figsize=(14, 12))
    bars = ax.barh(range(len(names)), speeds, color=colors, edgecolor='#30363d', linewidth=0.5, height=0.7)

    for i, (bar, spd, name) in enumerate(zip(bars, speeds, names)):
        if name.startswith("★"):
            bar.set_edgecolor(C_GOLD)
            bar.set_linewidth(2)
        ax.text(spd + 1.5, i, f"{spd:.1f}", va='center', fontsize=9, color='#c9d1d9')

    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=9)
    ax.set_xlabel("Generation Speed (tok/s)")
    ax.set_title("BC-250 · Generation Speed — 30 of 32 Models\n@4K context · Q4_0/IQ2_M quants · 2 redundant 14B variants omitted", fontsize=13, fontweight='bold', pad=15)
    ax.set_xlim(0, 120)
    ax.grid(axis='x', alpha=0.3)

    # Legend
    handles = [mpatches.Patch(facecolor=c, label=s) for s, c in size_colors.items()]
    ax.legend(handles=handles, loc='lower right', fontsize=9, framealpha=0.3)

    plt.tight_layout()
    plt.savefig(f"{OUT_DIR}/bench-generation-speed-all.png", dpi=150, bbox_inches='tight')
    plt.close()
    print("  ✓ bench-generation-speed-all.png")


# ══════════════════════════════════════════════════════════════════════
# CHART 2: Quality Scores — All 32 Models (horizontal bar)
# ══════════════════════════════════════════════════════════════════════
def chart_quality_all():
    models = [
        ("gemma3:4b",            100), ("lexi-8b",             100),
        ("qwen3-abl:8b",        100), ("qwen3-abliterated:8b",100),
        ("qwen3:8b",            100), ("qwen3:8b-nothink",    100),
        ("qwen3:8b-q8_0",       100), ("gemma2:9b",           100),
        ("★ qwen3.5:9b",       100), ("gemma3:12b",          100),
        ("phi4:14b",            100), ("qwen3-abl:14b",       100),
        ("qwen3-14b-nothink",   100), ("qwen3-14b-16k",       100),
        ("qwen3:14b",           100), ("deepseek-r1:14b",     100),
        ("★ MoE 35B-A3B",    93),  ("phi4-mini",            93),
        ("llama3.2:3b",          93),  ("llama3.1:8b",          93),
        ("glm4:9b",              93),
        ("Qwen3-Coder-30B-A3B",  87),  ("seed-coder-abl:8b",    87),
        ("granite3.3:8b",        80),  ("mistral-nemo:12b",     80),
        ("qwen2.5:3b",           73),  ("deepseek-r1:8b",       73),
        ("qwen2.5-coder:7b",    40),  ("qwen3:4b ⁶",          33),
        ("Qwen3-30B-A3B (Q2_K) ⁶",27),
        ("qwen2.5:7b ²",        20),  ("qwen3.5-27b-iq2m ⁷",   0),
    ]

    models.reverse()
    names   = [m[0] for m in models]
    scores  = [m[1] for m in models]

    def score_color(s):
        if s == 100: return C_GREEN
        if s >= 90:  return C_BLUE
        if s >= 70:  return C_ORANGE
        return C_RED

    colors = [score_color(s) for s in scores]

    fig, ax = plt.subplots(figsize=(12, 11))
    bars = ax.barh(range(len(names)), scores, color=colors, edgecolor='#30363d', linewidth=0.5, height=0.7)

    for i, (bar, sc, name) in enumerate(zip(bars, scores, names)):
        if name.startswith("★"):
            bar.set_edgecolor(C_GOLD)
            bar.set_linewidth(2)
        ax.text(sc + 1, i, f"{sc}%", va='center', fontsize=9, color='#c9d1d9')

    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=9)
    ax.set_xlabel("Quality Score (%)")
    ax.set_title("BC-250 · Quality Assessment — All 32 Models\n5 tasks × 3 runs · deterministic scoring (keyword/JSON/regex checks)", fontsize=13, fontweight='bold', pad=15)
    ax.set_xlim(0, 115)
    ax.grid(axis='x', alpha=0.3)

    handles = [
        mpatches.Patch(facecolor=C_GREEN, label='100%'),
        mpatches.Patch(facecolor=C_BLUE, label='93%'),
        mpatches.Patch(facecolor=C_ORANGE, label='73–87%'),
        mpatches.Patch(facecolor=C_RED, label='≤40% (think leak / load bug / specialized)'),
    ]
    ax.legend(handles=handles, loc='lower right', fontsize=9, framealpha=0.3)

    plt.tight_layout()
    plt.savefig(f"{OUT_DIR}/bench-quality-all.png", dpi=150, bbox_inches='tight')
    plt.close()
    print("  ✓ bench-quality-all.png")


# ══════════════════════════════════════════════════════════════════════
# CHART 3: Context Ceiling Grid — 19 of 32 Models (heatmap)
# Only models with filled-context speed data across 4K/16K/32K/64K
# ══════════════════════════════════════════════════════════════════════
def chart_context_ceiling():
    # (model, 4K_tok/s, 16K_tok/s, 32K_tok/s, 64K_tok/s)
    # None = fail/trunc, negative = impractical
    # Data refreshed 2026-03-31 — 80% real-token fill at each context size
    data = [
        ("llama3.2:3b",           87.8, 56.3, 38.3, 23.3),
        ("gemma3:4b",             74.8, 72.3, 70.0, 65.1),
        ("phi4-mini",             74.3, 48.7, 33.2, 20.3),
        ("qwen3:4b",              61.4, 40.1, 28.5, 17.6),
        ("Qwen3-30B-A3B (Q2_K)",   53.6, 40.1, 30.0, None),
        ("llama3.1:8b",           47.0, 35.6, 26.5, 17.6),
        ("seed-coder-abl:8b",    46.1, 34.7, 25.7, 17.9),
        ("qwen3:8b",              39.4, 30.3, 22.5, 15.4),
        ("glm4:9b",               37.0, 23.3, 15.5,  9.2),
        ("★ MoE 35B-A3B",     35.6, 31.9, 28.5, None),
        ("★ qwen3.5:9b",        31.1, 29.4, 27.0, 23.4),
        ("gemma3:12b",           28.4, 27.5, 26.3, 24.2),
        ("mistral-nemo:12b",     31.8, 24.7, 19.1, 13.1),
        ("deepseek-r1:14b",      26.4, None, None, None),
        ("qwen3:14b",            25.2, 20.7, 16.7, None),
        ("phi4:14b",              26.0, 19.5, 14.7, 14.8),
        ("gemma2:9b",             29.6, 17.1, 17.0, 17.0),
    ]

    ctx_labels = ["4K", "16K", "32K", "64K"]
    models = [d[0] for d in data]
    matrix = np.zeros((len(data), 4))

    for i, d in enumerate(data):
        for j in range(4):
            v = d[j + 1]
            if v is None:
                matrix[i][j] = 0
            elif v < 0:
                matrix[i][j] = abs(v)
            else:
                matrix[i][j] = v

    fig, ax = plt.subplots(figsize=(10, 10))

    # Custom colormap: dark blue → green → yellow
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list('speed', [
        '#1a1a2e', '#16213e', '#1a5276', '#1e8449', '#27ae60', '#f1c40f', '#e74c3c'
    ])

    im = ax.imshow(matrix, cmap=cmap, aspect='auto', vmin=0, vmax=90)

    ax.set_xticks(range(4))
    ax.set_xticklabels(ctx_labels, fontsize=11)
    ax.set_yticks(range(len(models)))
    ax.set_yticklabels(models, fontsize=9)

    # Annotate each cell
    for i in range(len(data)):
        for j in range(4):
            v = data[i][j + 1]
            if v is None:
                txt = "✂️"
                clr = '#e74c3c'
            elif v < 0:
                txt = f"{abs(v):.1f}\n⚠️"
                clr = '#e74c3c'
            else:
                txt = f"{v:.1f}"
                clr = '#ffffff' if v < 50 else '#000000'
            ax.text(j, i, txt, ha='center', va='center', fontsize=8, color=clr, fontweight='bold')

    ax.set_title("BC-250 · Filled-Context Speed (tok/s) — 19 of 32 Models\n80% real-token fill · Q4_0 KV · ✂️ = truncated · ⚠️ = impractical",
                 fontsize=13, fontweight='bold', pad=15)

    cbar = plt.colorbar(im, ax=ax, shrink=0.6, pad=0.02)
    cbar.set_label("tok/s", fontsize=10)

    plt.tight_layout()
    plt.savefig(f"{OUT_DIR}/bench-context-heatmap.png", dpi=150, bbox_inches='tight')
    plt.close()
    print("  ✓ bench-context-heatmap.png")


# ══════════════════════════════════════════════════════════════════════
# CHART 4: Context Degradation — 6 Selected Models (2 production + 4 comparison)
# ══════════════════════════════════════════════════════════════════════
def chart_context_degradation():
    ctx = [4, 16, 32, 64]  # K

    # Data refreshed 2026-03-31 — 80% real-token fill
    models = {
        "★ MoE 35B-A3B":  [35.6, 31.9, 28.5, 23.0],
        "★ qwen3.5:9b":     [31.1, 29.4, 27.0, 23.4],
        "phi4-mini":          [74.3, 48.7, 33.2, 20.3],
        "qwen3:8b":           [39.4, 30.3, 22.5, 15.4],
        "qwen3:14b":          [25.2, 20.7, 16.7, 12.0],
        "gemma3:4b":          [74.8, 72.3, 70.0, 65.1],
    }

    colors = {
        "★ MoE 35B-A3B":  C_GOLD,
        "★ qwen3.5:9b":     C_ORANGE,
        "phi4-mini":          C_GREEN,
        "qwen3:8b":           C_BLUE,
        "qwen3:14b":          C_RED,
        "gemma3:4b":          C_TEAL,
    }

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 7))

    # Left: Absolute speed
    for name, speeds in models.items():
        ax1.plot(ctx, speeds, 'o-', label=name, color=colors[name], linewidth=2, markersize=6)
    ax1.set_xlabel("Context Size (K tokens)")
    ax1.set_ylabel("Generation Speed (tok/s)")
    ax1.set_title("Absolute Speed vs Context Fill — 6 Models", fontsize=12, fontweight='bold')
    ax1.legend(fontsize=8, framealpha=0.3)
    ax1.grid(alpha=0.3)
    ax1.set_xticks(ctx)
    ax1.set_xticklabels(["4K", "16K", "32K", "64K"])

    # Right: Percentage degradation from 4K baseline
    for name, speeds in models.items():
        base = speeds[0]
        pct = [(s / base) * 100 for s in speeds]
        ax2.plot(ctx, pct, 'o-', label=name, color=colors[name], linewidth=2, markersize=6)
    ax2.set_xlabel("Context Size (K tokens)")
    ax2.set_ylabel("Speed Retention (% of 4K baseline)")
    ax2.set_title("Context Degradation — % Retained (6 Models)", fontsize=12, fontweight='bold')
    ax2.legend(fontsize=8, framealpha=0.3)
    ax2.grid(alpha=0.3)
    ax2.set_xticks(ctx)
    ax2.set_xticklabels(["4K", "16K", "32K", "64K"])
    ax2.axhline(y=50, color=C_RED, linestyle='--', alpha=0.4, linewidth=1)
    ax2.set_ylim(20, 105)

    fig.suptitle("BC-250 · Context Scaling — 6 Selected Models (★ = production)\n80% real-token fill · Q4_0 KV cache",
                 fontsize=14, fontweight='bold', y=1.02)
    plt.tight_layout()
    plt.savefig(f"{OUT_DIR}/bench-context-degradation.png", dpi=150, bbox_inches='tight')
    plt.close()
    print("  ✓ bench-context-degradation.png")


# ══════════════════════════════════════════════════════════════════════
# CHART 5: Quality per Task Category — All 32 Models (grouped bar)
# ══════════════════════════════════════════════════════════════════════
def chart_quality_tasks():
    tasks = ["Summarize", "JSON\nExtract", "Fact\nRecall", "Instruction\nFollow", "Arithmetic\n(17×23)"]

    # Counts of 3/3 across all 29 testable models
    # From §6.1: count how many got 3/3 on each
    pass_counts = [25, 26, 31, 21, 24]  # out of 32 — counted from B4 table
    fail_counts = [32-c for c in pass_counts]

    x = np.arange(len(tasks))
    w = 0.5

    fig, ax = plt.subplots(figsize=(10, 6))
    bars_pass = ax.bar(x, pass_counts, w, color=C_GREEN, edgecolor='#30363d', label='Perfect (3/3)')
    bars_fail = ax.bar(x, fail_counts, w, bottom=pass_counts, color=C_RED, edgecolor='#30363d', alpha=0.6, label='Imperfect (<3/3)')

    for i, (p, f) in enumerate(zip(pass_counts, fail_counts)):
        ax.text(i, p/2, f"{p}", ha='center', va='center', fontsize=12, fontweight='bold', color='white')
        if f > 0:
            ax.text(i, p + f/2, f"{f}", ha='center', va='center', fontsize=11, fontweight='bold', color='white')

    ax.set_ylabel("Number of Models (out of 32)")
    ax.set_xticks(x)
    ax.set_xticklabels(tasks, fontsize=10)
    ax.set_title("BC-250 · Quality by Task — All 32 Models\n5 tasks × 3 runs · how many achieve perfect 3/3",
                 fontsize=13, fontweight='bold', pad=15)
    ax.legend(fontsize=10, framealpha=0.3)
    ax.grid(axis='y', alpha=0.3)
    ax.set_ylim(0, 33)

    plt.tight_layout()
    plt.savefig(f"{OUT_DIR}/bench-quality-tasks.png", dpi=150, bbox_inches='tight')
    plt.close()
    print("  ✓ bench-quality-tasks.png")


# ══════════════════════════════════════════════════════════════════════
# CHART 6: Long-Context Quality — 3 of 32 Models (heatmap, 4 tests × 2 ctx)
# Only models with long-context quality data (production + phi4-mini comparison)
# ══════════════════════════════════════════════════════════════════════
def chart_longctx_quality():
    models = ["MoE 35B-A3B", "qwen3.5:9b", "phi4-mini"]
    tests_16k = ["Budget\n16K", "Population\n16K", "Contradict\n16K", "Timeline\n16K"]
    tests_32k = ["Budget\n32K", "Population\n32K", "Contradict\n32K", "Timeline\n32K"]
    all_tests = tests_16k + tests_32k

    # 1=pass, 0=fail
    results = np.array([
        # MoE 35B:   16K                        32K
        [0, 1, 1, 1,   0, 1, 1, 1],
        # qwen3.5:9b
        [0, 0, 1, 1,   0, 1, 1, 1],
        # phi4-mini
        [0, 0, 0, 1,   0, 0, 1, 1],
    ])

    fig, ax = plt.subplots(figsize=(12, 4.5))

    from matplotlib.colors import ListedColormap
    cmap = ListedColormap(['#c0392b', '#27ae60'])
    im = ax.imshow(results, cmap=cmap, aspect='auto', vmin=0, vmax=1)

    # Annotate
    for i in range(3):
        for j in range(8):
            txt = "PASS" if results[i][j] == 1 else "FAIL"
            clr = '#000000' if results[i][j] == 1 else 'white'
            ax.text(j, i, txt, ha='center', va='center', fontsize=11,
                    color=clr, fontweight='bold')

    ax.set_xticks(range(8))
    ax.set_xticklabels(all_tests, fontsize=9)
    ax.set_yticks(range(3))
    ax.set_yticklabels(models, fontsize=10)

    # Vertical separator between 16K and 32K
    ax.axvline(x=3.5, color='#c9d1d9', linewidth=2, linestyle='--')
    ax.text(1.5, -0.7, "16K Context", ha='center', fontsize=11, fontweight='bold', color=C_BLUE)
    ax.text(5.5, -0.7, "32K Context", ha='center', fontsize=11, fontweight='bold', color=C_ORANGE)

    ax.set_title("BC-250 · Long-Context Quality — 3 of 32 Models\n4 multi-hop tests (budget/pop/contradict/timeline) at 16K & 32K",
                 fontsize=13, fontweight='bold', pad=25)

    # Summary on right
    totals = ["6/8", "5/8", "3/8"]
    for i, t in enumerate(totals):
        ax.text(8.3, i, t, ha='center', va='center', fontsize=11, fontweight='bold', color='#c9d1d9')
    ax.text(8.3, -0.7, "Total", ha='center', fontsize=10, fontweight='bold', color='#c9d1d9')

    ax.set_xlim(-0.5, 8.8)
    plt.tight_layout()
    plt.savefig(f"{OUT_DIR}/bench-longctx-quality.png", dpi=150, bbox_inches='tight')
    plt.close()
    print("  ✓ bench-longctx-quality.png")


# ══════════════════════════════════════════════════════════════════════
# CHART 7: Image Generation — 8 Pipelines (separate from LLM benchmarks)
# ══════════════════════════════════════════════════════════════════════
def chart_image_gen():
    models = [
        ("SD-Turbo",          27, 4),
        ("FLUX.2-klein-4B",   37, 4),
        ("Chroma flash",      67, 4),
        ("FLUX.2-klein-9B ★", 67, 4),
        ("SD3.5-medium",     102, 28),
        ("FLUX.1-schnell",   107, 4),
        ("FLUX.1-kontext",   132, 20),
        ("FLUX.1-dev",       167, 20),
    ]

    names  = [m[0] for m in models]
    times  = [m[1] for m in models]
    steps  = [m[2] for m in models]

    colors = [C_GREEN if t <= 40 else C_BLUE if t <= 70 else C_ORANGE if t <= 110 else C_RED for t in times]

    fig, ax = plt.subplots(figsize=(12, 6))
    bars = ax.barh(range(len(names)), times, color=colors, edgecolor='#30363d', linewidth=0.5, height=0.6)

    for i, (bar, t, s) in enumerate(zip(bars, times, steps)):
        if "★" in names[i]:
            bar.set_edgecolor(C_GOLD)
            bar.set_linewidth(2)
        ax.text(t + 2, i, f"{t}s ({s} steps)", va='center', fontsize=10, color='#c9d1d9')

    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=10)
    ax.set_xlabel("Time (seconds) — 512×512")
    ax.set_title("BC-250 · Image Generation — 8 Pipelines · 512×512\nsd.cpp · Vulkan GFX1013 · same prompt, seed 42",
                 fontsize=13, fontweight='bold', pad=15)
    ax.set_xlim(0, 200)
    ax.grid(axis='x', alpha=0.3)

    plt.tight_layout()
    plt.savefig(f"{OUT_DIR}/bench-image-gen.png", dpi=150, bbox_inches='tight')
    plt.close()
    print("  ✓ bench-image-gen.png")


# ══════════════════════════════════════════════════════════════════════
# CHART 8: VRAM Usage — 22 of 32 Models (excludes quant/think variants)
# ══════════════════════════════════════════════════════════════════════
def chart_vram_usage():
    models = [
        ("qwen2.5:3b",       2.1),
        ("llama3.2:3b",      2.2),
        ("phi4-mini",        2.5),
        ("qwen3:4b",         2.9),
        ("gemma3:4b",        3.8),
        ("qwen2.5:7b",       4.4),
        ("llama3.1:8b",      4.7),
        ("seed-coder-abl:8b",4.8),
        ("qwen3:8b",         5.1),
        ("glm4:9b",          5.1),
        ("gemma2:9b",        6.9),
        ("mistral-nemo:12b", 6.7),
        ("★ qwen3.5:9b",    7.9),
        ("phi4:14b",         8.5),
        ("deepseek-r1:14b",  8.5),
        ("qwen3:8b-q8_0",    8.5),
        ("gemma3:12b",       8.7),
        ("qwen3:14b",        8.9),
        ("Qwen3-Coder-30B-A3B",  11.0),
        ("Qwen3-30B-A3B (Q2_K)",  10.7),
        ("★ MoE 35B-A3B",12.3),
        ("qwen3.5-27b-iq2m",       13.4),
    ]

    names = [m[0] for m in models]
    vrams = [m[1] for m in models]

    fig, ax = plt.subplots(figsize=(12, 9))
    colors = [C_GREEN if v <= 5 else C_BLUE if v <= 9 else C_ORANGE if v <= 12.5 else C_RED for v in vrams]
    bars = ax.barh(range(len(names)), vrams, color=colors, edgecolor='#30363d', linewidth=0.5, height=0.65)

    for i, (bar, v, name) in enumerate(zip(bars, vrams, names)):
        if name.startswith("★"):
            bar.set_edgecolor(C_GOLD)
            bar.set_linewidth(2)
        ax.text(v + 0.1, i, f"{v:.1f} GiB", va='center', fontsize=9, color='#c9d1d9')

    # Available memory line
    ax.axvline(x=16.5, color=C_GOLD, linestyle='--', linewidth=1.5, alpha=0.8)
    ax.text(16.5, len(names) - 0.5, "16.5 GiB Vulkan total", ha='right', va='bottom',
            fontsize=9, color=C_GOLD, fontweight='bold')

    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=9)
    ax.set_xlabel("VRAM Usage @4K Context (GiB)")
    ax.set_title("BC-250 · VRAM Usage — 22 of 32 Models @4K Context\n16.5 GiB Vulkan available · quant/think variants excluded",
                 fontsize=13, fontweight='bold', pad=15)
    ax.set_xlim(0, 18)
    ax.grid(axis='x', alpha=0.3)

    plt.tight_layout()
    plt.savefig(f"{OUT_DIR}/bench-vram-usage.png", dpi=150, bbox_inches='tight')
    plt.close()
    print("  ✓ bench-vram-usage.png")


# ══════════════════════════════════════════════════════════════════════
# CHART 9: Speed vs Quality — 23 of 32 Models (scatter)
# Excludes quant variants and think-mode duplicates
# ══════════════════════════════════════════════════════════════════════
def chart_speed_vs_quality():
    # (model, tok/s, quality%, params, is_production)
    # Data refreshed 2026-03-31
    models = [
        ("llama3.2:3b",       103.7, 93,  3.2, False),
        ("qwen2.5:3b",        101.9, 73,  3.1, False),
        ("phi4-mini",          87.5, 93,  3.8, True),
        ("gemma3:4b",          76.7, 100, 4.0, False),
        ("qwen3:4b",           74.0, 33,  4.0, False),
        ("Qwen3-Coder-30B-A3B",   62.8, 87, 30.5, False),
        ("Qwen3-30B-A3B (Q2_K)", 59.4, 27, 30.5, False),
        ("qwen2.5-coder:7b",  55.0, 40, 7.6, False),
        ("llama3.1:8b",        51.5, 93,  8.0, False),
        ("seed-coder-abl:8b", 50.8, 87,  8.3, False),
        ("lexi-8b",           50.1, 100, 8.0, False),
        ("granite3.3:8b",     45.9, 80,  8.0, False),
        ("glm4:9b",            45.3, 93,  9.0, False),
        ("deepseek-r1:8b",    43.3, 73,  8.0, False),
        ("qwen3:8b",           43.0, 100, 8.2, False),
        ("gemma2:9b",          38.5, 100, 9.2, False),
        ("★ MoE 35B-A3B",     37.7, 93, 35.0, True),
        ("mistral-nemo:12b",  34.1, 80, 12.2, False),
        ("★ qwen3.5:9b",      31.9, 100, 9.7, True),
        ("gemma3:12b",        29.1, 100, 12.0, False),
        ("deepseek-r1:14b",   28.8, 100, 14.0, True),
        ("phi4:14b",           28.6, 100, 14.7, False),
        ("qwen3:14b",         26.9, 100, 14.8, False),
    ]

    # Sort by speed (slowest at top for bottom-to-top reading)
    models.sort(key=lambda m: m[1])

    names   = [m[0] for m in models]
    speeds  = [m[1] for m in models]
    quals   = [m[2] for m in models]
    params  = [m[3] for m in models]
    prods   = [m[4] for m in models]

    def qual_color(q):
        if q == 100: return C_GREEN
        if q >= 90:  return C_BLUE
        if q >= 70:  return C_ORANGE
        return C_RED

    colors = [qual_color(q) for q in quals]

    fig, ax = plt.subplots(figsize=(14, 10))

    bars = ax.barh(range(len(names)), speeds, color=colors, edgecolor='#30363d',
                   linewidth=0.5, height=0.7, alpha=0.85)

    for i, (bar, spd, q, name, is_prod) in enumerate(zip(bars, speeds, quals, names, prods)):
        # Gold border for production models
        if is_prod:
            bar.set_edgecolor(C_GOLD)
            bar.set_linewidth(2.5)

        # Speed label at end of bar
        ax.text(spd + 1, i, f"{spd:.1f} tok/s", va='center', fontsize=8.5,
                color='#c9d1d9', fontweight='bold' if is_prod else 'normal')

        # Quality badge at right edge
        badge_x = 125
        badge_color = qual_color(q)
        ax.text(badge_x, i, f"{q}%", va='center', ha='center', fontsize=9,
                color=badge_color, fontweight='bold',
                bbox=dict(boxstyle='round,pad=0.2', facecolor='#161b22',
                          edgecolor=badge_color, linewidth=1.2, alpha=0.9))

    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=9.5,
                       fontweight='bold' if False else 'normal')
    # Bold production model labels
    for i, is_prod in enumerate(prods):
        if is_prod:
            ax.get_yticklabels()[i].set_fontweight('bold')
            ax.get_yticklabels()[i].set_color(C_GOLD)

    ax.set_xlabel("Generation Speed (tok/s)", fontsize=12)
    ax.set_title("BC-250 · Speed vs Quality — 23 Models\nBar color = quality tier · Gold outline = production · Badge = quality score",
                 fontsize=13, fontweight='bold', pad=15)
    ax.set_xlim(0, 130)
    ax.grid(axis='x', alpha=0.2)

    # Legend — positioned left of quality badges
    handles = [
        mpatches.Patch(facecolor=C_GREEN, label='100% quality', edgecolor='#30363d'),
        mpatches.Patch(facecolor=C_BLUE, label='93% quality', edgecolor='#30363d'),
        mpatches.Patch(facecolor=C_ORANGE, label='73–87% quality', edgecolor='#30363d'),
        mpatches.Patch(facecolor=C_RED, label='≤40% (think leak / bug)', edgecolor='#30363d'),
        mpatches.Patch(facecolor='#161b22', label='★ Production', edgecolor=C_GOLD, linewidth=2),
    ]
    ax.legend(handles=handles, loc='lower right', fontsize=9, framealpha=0.4,
              fancybox=True, edgecolor='#30363d')

    plt.tight_layout()
    plt.savefig(f"{OUT_DIR}/bench-speed-vs-quality.png", dpi=150, bbox_inches='tight')
    plt.close()
    print("  ✓ bench-speed-vs-quality.png")


# ══════════════════════════════════════════════════════════════════════
# CHART 10: Statistical Validation — 8 Phase-2 Models (CV% bars)
# Only models with 3-run rigorous data from bench-rigorous.py
# ══════════════════════════════════════════════════════════════════════
def chart_statistical_cv():
    models = [
        ("qwen3:14b",        0.2, 26.6),
        ("mistral-nemo:12b", 0.2, 34.0),
        ("qwen3:8b",         0.3, 42.8),
        ("qwen3.5:9b",       0.4, 31.7),
        ("MoE 35B-A3B",          0.4, 37.5),
        ("Qwen3-30B-A3B (Q2_K)",   0.9, 58.5),
        ("llama3.2:3b",      1.3, 102.2),
        ("phi4-mini",        1.4, 86.1),
    ]

    names = [m[0] for m in models]
    cvs   = [m[1] for m in models]
    speeds= [m[2] for m in models]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    # Left: CV% bars
    colors = [C_GREEN if c <= 0.5 else C_BLUE if c <= 1.0 else C_ORANGE for c in cvs]
    bars = ax1.barh(range(len(names)), cvs, color=colors, edgecolor='#30363d', height=0.6)
    for i, (b, c) in enumerate(zip(bars, cvs)):
        ax1.text(c + 0.05, i, f"{c}%", va='center', fontsize=10, color='#c9d1d9')
    ax1.set_yticks(range(len(names)))
    ax1.set_yticklabels(names, fontsize=10)
    ax1.set_xlabel("Coefficient of Variation (%)")
    ax1.set_title("Measurement Reliability — 8 Models\n3 runs per model", fontsize=12, fontweight='bold')
    ax1.set_xlim(0, 2.0)
    ax1.axvline(x=1.5, color=C_RED, linestyle='--', alpha=0.4)
    ax1.text(1.55, 7, "1.5% threshold", fontsize=8, color=C_RED, alpha=0.5)
    ax1.grid(axis='x', alpha=0.3)

    # Right: Speed with error bars
    ranges = [
        [26.6, 26.7], [33.9, 34.0], [42.8, 43.0], [31.7, 31.9],
        [37.3, 37.6], [57.9, 58.9], [101.3, 103.9], [85.0, 87.4]
    ]
    errs = [[(s - r[0]), (r[1] - s)] for s, r in zip(speeds, ranges)]
    errs_lo = [e[0] for e in errs]
    errs_hi = [e[1] for e in errs]

    ax2.barh(range(len(names)), speeds, color=C_BLUE, edgecolor='#30363d', height=0.6, alpha=0.8)
    ax2.errorbar(speeds, range(len(names)), xerr=[errs_lo, errs_hi], fmt='none',
                ecolor=C_GOLD, capsize=4, capthick=1.5, linewidth=1.5)
    for i, s in enumerate(speeds):
        ax2.text(s + 2, i, f"{s:.1f}", va='center', fontsize=9, color='#c9d1d9')
    ax2.set_yticks(range(len(names)))
    ax2.set_yticklabels(names, fontsize=10)
    ax2.set_xlabel("Generation Speed (tok/s)")
    ax2.set_title("Speed with Min/Max Range — 8 Models\nGold whiskers = observed range across 3 runs", fontsize=12, fontweight='bold')
    ax2.grid(axis='x', alpha=0.3)

    fig.suptitle("BC-250 · Statistical Validation — 8 Phase-2 Models\n3 runs each · bench-rigorous.py data",
                 fontsize=14, fontweight='bold', y=1.02)
    plt.tight_layout()
    plt.savefig(f"{OUT_DIR}/bench-statistical-cv.png", dpi=150, bbox_inches='tight')
    plt.close()
    print("  ✓ bench-statistical-cv.png")


# ══════════════════════════════════════════════════════════════════════
# CHART 11: Prefill Speed — All Models (horizontal bar)
# Measured 2026-03-31 with medium prompt (~46-65 tokens) @4K context
# ══════════════════════════════════════════════════════════════════════
def chart_prefill_speed():
    # (model, prefill_tok/s @4K medium prompt, size_cat)
    models = [
        ("llama3.2:3b",              530.5, "3B"),
        ("qwen2.5:3b",              454.8, "3B"),
        ("gemma3:4b",                324.8, "4B"),
        ("phi4-mini",                299.6, "4B"),
        ("qwen3:4b",                 293.2, "4B"),
        ("granite3.3:8b",           261.1, "8B"),
        ("qwen2.5-coder:7b",        236.7, "7B"),
        ("qwen2.5:7b",              236.6, "7B"),
        ("seed-coder-abl:8b",       213.4, "8B"),
        ("qwen3:8b-q8_0",           200.4, "8B"),
        ("llama3.1:8b",              172.7, "8B"),
        ("qwen3-abl:8b",            170.2, "8B"),
        ("qwen3:8b",                169.1, "8B"),
        ("qwen3:8b-nothink",        168.8, "8B"),
        ("glm4:9b",                 166.4, "9B"),
        ("gemma2:9b",               157.7, "9B"),
        ("★ qwen3.5:9b",           147.3, "9B"),
        ("deepseek-r1:8b",          140.8, "8B"),
        ("mistral-nemo:12b",        119.1, "12B"),
        ("gemma3:12b",              105.9, "12B"),
        ("★ Qwen3-Coder-30B-A3B",  96.5, "MoE"),
        ("★ MoE 35B-A3B",          92.4, "MoE"),
        ("qwen3:14b",               89.7, "14B"),
        ("phi4:14b",                 87.7, "14B"),
        ("Qwen3-30B-A3B (Q2_K)",    80.5, "MoE"),
        ("deepseek-r1:14b",         72.8, "14B"),
    ]

    models.reverse()
    names  = [m[0] for m in models]
    speeds = [m[1] for m in models]
    sizes  = [m[2] for m in models]

    size_colors = {
        "3B": "#2ecc71", "4B": "#1abc9c", "7B": "#3498db",
        "8B": "#4a90d9", "9B": "#e67e22", "MoE": "#f0c040",
        "12B": "#e84393", "14B": "#e74c3c",
    }
    colors = [size_colors.get(s, C_GRAY) for s in sizes]

    fig, ax = plt.subplots(figsize=(14, 10))
    bars = ax.barh(range(len(names)), speeds, color=colors, edgecolor='#30363d', linewidth=0.5, height=0.7)

    for i, (bar, spd, name) in enumerate(zip(bars, speeds, names)):
        if name.startswith("★"):
            bar.set_edgecolor(C_GOLD)
            bar.set_linewidth(2)
        ax.text(spd + 5, i, f"{spd:.0f}", va='center', fontsize=9, color='#c9d1d9')

    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=9)
    ax.set_xlabel("Prefill Speed (tok/s)")
    ax.set_title("BC-250 · Prefill Speed — 26 of 32 Models\n@4K context · medium prompt (~50 tokens) · Q4_0 KV · ★ = production",
                 fontsize=13, fontweight='bold', pad=15)
    ax.set_xlim(0, 600)
    ax.grid(axis='x', alpha=0.3)

    handles = [mpatches.Patch(facecolor=c, label=s) for s, c in size_colors.items()]
    ax.legend(handles=handles, loc='lower right', fontsize=9, framealpha=0.3)

    plt.tight_layout()
    plt.savefig(f"{OUT_DIR}/bench-prefill-speed.png", dpi=150, bbox_inches='tight')
    plt.close()
    print("  ✓ bench-prefill-speed.png")


# ══════════════════════════════════════════════════════════════════════
# CHART 12: Gen vs Prefill — All Models (dual bar)
# Shows both generation and prefill speed side by side
# ══════════════════════════════════════════════════════════════════════
def chart_gen_vs_prefill():
    # (model, gen_tok/s, prefill_tok/s)
    models = [
        ("llama3.2:3b",         103.7, 530.5),
        ("qwen2.5:3b",         101.9, 454.8),
        ("phi4-mini",            87.5, 299.6),
        ("gemma3:4b",            76.7, 324.8),
        ("qwen3:4b",             74.0, 293.2),
        ("Qwen3-Coder-30B",     62.8,  96.5),
        ("Qwen3-30B (Q2_K)",    59.4,  80.5),
        ("qwen2.5:7b",          55.0, 236.6),
        ("llama3.1:8b",          51.5, 172.7),
        ("seed-coder:8b",       50.8, 213.4),
        ("lexi-8b",             50.1, 172.7),
        ("granite3.3:8b",       45.9, 261.1),
        ("glm4:9b",              45.3, 166.4),
        ("qwen3:8b",             43.0, 169.1),
        ("gemma2:9b",            38.5, 157.7),
        ("★ MoE 35B",           37.7,  92.4),
        ("mistral-nemo:12b",    34.1, 119.1),
        ("★ qwen3.5:9b",        31.9, 147.3),
        ("gemma3:12b",          29.1, 105.9),
        ("deepseek-r1:14b",     28.8,  72.8),
        ("phi4:14b",             28.6,  87.7),
        ("qwen3:14b",           26.9,  89.7),
    ]

    models.reverse()
    names = [m[0] for m in models]
    gen   = [m[1] for m in models]
    pref  = [m[2] for m in models]

    y = np.arange(len(names))
    h = 0.35

    fig, ax = plt.subplots(figsize=(14, 11))
    bars1 = ax.barh(y - h/2, gen, h, color=C_BLUE, edgecolor='#30363d', linewidth=0.5, label='Generation (tok/s)')
    bars2 = ax.barh(y + h/2, pref, h, color=C_GREEN, edgecolor='#30363d', linewidth=0.5, alpha=0.8, label='Prefill (tok/s)')

    for i, (g, p, name) in enumerate(zip(gen, pref, names)):
        ratio = p / g if g > 0 else 0
        ax.text(max(g, p) + 5, i, f"{ratio:.1f}×", va='center', fontsize=8, color='#8b949e')
        if name.startswith("★"):
            bars1[i].set_edgecolor(C_GOLD)
            bars1[i].set_linewidth(2)
            bars2[i].set_edgecolor(C_GOLD)
            bars2[i].set_linewidth(2)

    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=9)
    ax.set_xlabel("Speed (tok/s)")
    ax.set_title("BC-250 · Generation vs Prefill Speed — 22 Models\n@4K context · ratio label = prefill/gen multiplier · ★ = production",
                 fontsize=13, fontweight='bold', pad=15)
    ax.set_xlim(0, 600)
    ax.grid(axis='x', alpha=0.3)
    ax.legend(fontsize=10, framealpha=0.3, loc='lower right')

    plt.tight_layout()
    plt.savefig(f"{OUT_DIR}/bench-gen-vs-prefill.png", dpi=150, bbox_inches='tight')
    plt.close()
    print("  ✓ bench-gen-vs-prefill.png")


# ══════════════════════════════════════════════════════════════════════
# CHART 13: Prefill vs Prompt Size — 8 Selected Models (line plot)
# Shows how prefill speed scales with input token count
# ══════════════════════════════════════════════════════════════════════
def chart_prefill_vs_prompt_size():
    # Prompt sizes: tiny (~5-30 tok), short (~10-40), medium (~40-65), long (~250-270)
    prompt_labels = ["Tiny\n(~10 tok)", "Short\n(~20 tok)", "Medium\n(~50 tok)", "Long\n(~260 tok)"]
    x = [10, 20, 50, 260]  # approximate token counts

    # (model, [tiny, short, medium, long])
    models = {
        "llama3.2:3b":        [194.5, 443.3, 530.3, 637.4],
        "gemma3:4b":          [104.7, 175.4, 324.3, 467.2],
        "qwen3:4b":           [ 98.5, 173.0, 293.1, 454.8],
        "qwen3:8b":           [ 50.2, 101.7, 168.9, 257.3],
        "★ qwen3.5:9b":      [ 43.4,  82.0, 147.4, 193.3],
        "gemma3:12b":         [ 33.4,  60.4, 105.9, 144.9],
        "★ MoE 35B-A3B":     [ 41.2,  65.9,  92.4, 204.7],
        "qwen3:14b":          [ 29.9,  56.2,  89.6, 137.5],
    }

    colors = {
        "llama3.2:3b":    C_GREEN,
        "gemma3:4b":      C_TEAL,
        "qwen3:4b":       "#82e0aa",
        "qwen3:8b":       C_BLUE,
        "★ qwen3.5:9b":  C_ORANGE,
        "gemma3:12b":     C_PINK,
        "★ MoE 35B-A3B": C_GOLD,
        "qwen3:14b":      C_RED,
    }

    fig, ax = plt.subplots(figsize=(12, 7))

    for name, speeds in models.items():
        ax.plot(x, speeds, 'o-', label=name, color=colors[name], linewidth=2, markersize=6)
        # Label the last point
        ax.text(x[-1] + 5, speeds[-1], f"{speeds[-1]:.0f}", fontsize=8,
                color=colors[name], va='center')

    ax.set_xlabel("Approximate Prompt Size (tokens)", fontsize=11)
    ax.set_ylabel("Prefill Speed (tok/s)", fontsize=11)
    ax.set_title("BC-250 · Prefill Speed vs Prompt Size — 8 Models\nLarger prompts → higher prefill throughput (batch efficiency) · @16K context",
                 fontsize=13, fontweight='bold', pad=15)
    ax.legend(fontsize=9, framealpha=0.3, loc='upper left')
    ax.grid(alpha=0.3)
    ax.set_xscale('log')
    ax.set_xticks(x)
    ax.set_xticklabels([f"~{t}" for t in x])
    ax.set_ylim(0, 700)

    plt.tight_layout()
    plt.savefig(f"{OUT_DIR}/bench-prefill-vs-prompt-size.png", dpi=150, bbox_inches='tight')
    plt.close()
    print("  ✓ bench-prefill-vs-prompt-size.png")


# ══════════════════════════════════════════════════════════════════════
# RUN ALL
# ══════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════
# CHART 14: llama.cpp vs Ollama — TG side-by-side (§4.10)
# ══════════════════════════════════════════════════════════════════════
def chart_llamacpp_vs_ollama():
    labels = [
        "MoE 30B\n4K ctx",
        "MoE 30B\n16K ctx",
        "MoE 30B\n32K ctx",
        "MoE 30B\n64K ctx",
        "DeepSeek 14B\n4K ctx",
        "DeepSeek 14B\n32K ctx",
    ]
    llama_vals  = [84.3, 84.7, None, None, 29.0, None]
    ollama_vals = [58.3, 49.0, 39.2, 28.7, 27.2, 20.9]

    x = np.arange(len(labels))
    w = 0.35

    fig, ax = plt.subplots(figsize=(12, 6))

    # Ollama bars (always present)
    bars_o = ax.bar(x + w/2, ollama_vals, w, label="Ollama 0.18", color=C_BLUE,
                    edgecolor='#30363d', linewidth=0.5)

    # llama.cpp bars (None → 0, marked as crash)
    llama_plot = [v if v is not None else 0 for v in llama_vals]
    bars_l = ax.bar(x - w/2, llama_plot, w, label="llama.cpp HEAD", color=C_GREEN,
                    edgecolor='#30363d', linewidth=0.5)

    # Value labels
    for bar, val in zip(bars_o, ollama_vals):
        if val:
            ax.text(bar.get_x() + bar.get_width()/2, val + 1.5, f"{val:.1f}",
                    ha='center', va='bottom', fontsize=9, color=C_BLUE)

    for bar, val in zip(bars_l, llama_vals):
        if val is not None:
            ax.text(bar.get_x() + bar.get_width()/2, val + 1.5, f"{val:.1f}",
                    ha='center', va='bottom', fontsize=9, color=C_GREEN)
        else:
            ax.text(bar.get_x() + bar.get_width()/2, 2, "☠ crash",
                    ha='center', va='bottom', fontsize=8, color=C_RED, fontweight='bold')

    # Overhead annotations for comparable pairs
    for i, (lv, ov) in enumerate(zip(llama_vals, ollama_vals)):
        if lv is not None and ov is not None:
            overhead = lv / ov
            ax.annotate(f"{overhead:.2f}×", xy=(x[i], max(lv, ov) + 6),
                        ha='center', fontsize=8, color=C_GOLD, fontweight='bold')

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel("Generation Speed (tok/s)")
    ax.set_title("BC-250 · llama.cpp HEAD vs Ollama 0.18 — Generation Speed\n"
                 "Fresh reboot · Vulkan · FA + Q4_0 KV cache",
                 fontsize=13, fontweight='bold', pad=15)
    ax.set_ylim(0, 105)
    ax.grid(axis='y', alpha=0.3)
    ax.legend(loc='upper right', fontsize=10, framealpha=0.3)

    plt.tight_layout()
    plt.savefig(f"{OUT_DIR}/bench-llamacpp-vs-ollama.png", dpi=150, bbox_inches='tight')
    plt.close()
    print("  ✓ bench-llamacpp-vs-ollama.png")


if __name__ == "__main__":
    print("Generating benchmark charts...")
    chart_gen_speed_all()
    chart_quality_all()
    chart_context_ceiling()
    chart_context_degradation()
    chart_quality_tasks()
    chart_longctx_quality()
    chart_image_gen()
    chart_vram_usage()
    chart_speed_vs_quality()
    chart_statistical_cv()
    chart_prefill_speed()
    chart_gen_vs_prefill()
    chart_prefill_vs_prompt_size()
    chart_llamacpp_vs_ollama()
    print(f"\nAll charts saved to {OUT_DIR}/")
