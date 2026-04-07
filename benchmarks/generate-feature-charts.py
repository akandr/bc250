#!/usr/bin/env python3
"""Generate benchmark charts for new BC-250 features:
 - Whisper transcription speed (real speech, wall time)
 - Whisper memory impact (swap delta alongside Ollama)
 - Model speed comparison (draft/9B/MoE for routing context)
 - Speculative decoding feasibility (memory budget)
 - Signal pipeline architecture overview

Dark theme matching existing charts.
Data source: real TTS speech benchmarks on BC-250.
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
C_TURBO  = "#5cb85c"
C_LARGE  = "#e87d5f"
C_MOE    = "#4a90d9"
C_9B     = "#e67e22"
C_3B     = "#9b59b6"
C_VRAM   = "#e74c3c"
C_KV     = "#f39c12"
C_FREE   = "#2ecc71"
C_SWAP   = "#e74c3c"
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
# CHART 1: Whisper Transcription — Wall Time (real speech)
# ══════════════════════════════════════════════════════════════════════

def chart_whisper_wall():
    """Bar chart comparing wall time for large-v3-turbo vs large-v3
    using real TTS speech (flite), not silence."""
    # Real speech benchmarks: en_short (3.6s), en_medium (18.2s), en_long (39.2s)
    labels = ['3.6s\n(short)', '18.2s\n(medium)', '39.2s\n(long)']
    turbo_wall = [3.33, 3.46, 4.30]
    large_wall = [7.90, 8.94, 8.09]

    x = np.arange(len(labels))
    w = 0.32

    fig, ax = plt.subplots(figsize=(9, 5.5))
    b1 = ax.bar(x - w/2, turbo_wall, w, color=C_TURBO, label='large-v3-turbo (1.6 GB)',
                edgecolor='none', zorder=3)
    b2 = ax.bar(x + w/2, large_wall, w, color=C_LARGE, label='large-v3 (2.9 GB)',
                edgecolor='none', zorder=3)

    # Value labels — safe placement above each bar
    for rect in b1:
        h = rect.get_height()
        ax.text(rect.get_x() + rect.get_width()/2, h + 0.2, f'{h:.1f}s',
                ha='center', va='bottom', fontsize=10, color=C_TURBO, fontweight='bold')
    for rect in b2:
        h = rect.get_height()
        ax.text(rect.get_x() + rect.get_width()/2, h + 0.2, f'{h:.1f}s',
                ha='center', va='bottom', fontsize=10, color=C_LARGE, fontweight='bold')

    # Speedup annotations — placed well below title
    for i in range(len(labels)):
        ratio = large_wall[i] / turbo_wall[i]
        mid_x = x[i]
        ax.text(mid_x, max(turbo_wall[i], large_wall[i]) + 1.2,
                f'{ratio:.1f}×', ha='center', va='bottom',
                fontsize=9, color='#8b949e', style='italic')

    ax.set_xlabel('Audio Duration (real TTS speech)')
    ax.set_ylabel('Wall Time (seconds)')
    ax.set_title('Whisper Transcription Speed — Real Speech — BC-250 (Vulkan)',
                 fontsize=13, fontweight='bold', pad=12)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.legend(loc='upper left', framealpha=0.8, edgecolor='#30363d', facecolor='#161b22')
    ax.grid(axis='y', alpha=0.3, zorder=0)
    ax.set_ylim(0, 13)

    plt.tight_layout()
    plt.savefig(f'{OUT_DIR}/whisper-wall-time.png', dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Saved {OUT_DIR}/whisper-wall-time.png")


# ══════════════════════════════════════════════════════════════════════
# CHART 2: Whisper Memory Impact (swap delta alongside Ollama)
# ══════════════════════════════════════════════════════════════════════

def chart_whisper_memory():
    """Bar chart showing swap increase caused by each whisper model
    when Ollama is already loaded (10 GB+ model in VRAM)."""
    labels = ['3.6s\n(short)', '18.2s\n(medium)', '39.2s\n(long)']
    turbo_swap = [0, 2, 0]       # MB swap delta
    large_swap = [1043, 1209, 0]  # MB swap delta (0 on 3rd = already swapped)

    x = np.arange(len(labels))
    w = 0.32

    fig, ax = plt.subplots(figsize=(9, 5.5))
    b1 = ax.bar(x - w/2, turbo_swap, w, color=C_TURBO, label='large-v3-turbo (1.6 GB)',
                edgecolor='none', zorder=3)
    b2 = ax.bar(x + w/2, large_swap, w, color=C_SWAP, label='large-v3 (2.9 GB)',
                edgecolor='none', zorder=3)

    # Value labels — safe placement above each bar
    for i, rect in enumerate(b1):
        h = rect.get_height()
        label = f'{turbo_swap[i]} MB' if turbo_swap[i] > 0 else '0'
        ax.text(rect.get_x() + rect.get_width()/2, max(h, 10) + 20,
                label, ha='center', va='bottom', fontsize=10,
                color=C_TURBO, fontweight='bold')
    for i, rect in enumerate(b2):
        h = rect.get_height()
        if large_swap[i] > 0:
            ax.text(rect.get_x() + rect.get_width()/2, h + 20,
                    f'+{large_swap[i]} MB', ha='center', va='bottom',
                    fontsize=10, color=C_SWAP, fontweight='bold')
        else:
            ax.text(rect.get_x() + rect.get_width()/2, 30,
                    '0*', ha='center', va='bottom',
                    fontsize=10, color=C_SWAP, fontweight='bold')

    # Danger zone
    ax.axhspan(500, 1400, alpha=0.08, color=C_SWAP, zorder=1)
    ax.text(2.4, 1300, 'swap pressure zone', ha='right', fontsize=9,
            color=C_SWAP, alpha=0.6, style='italic')

    # Footnote for the 0 on 3rd run
    ax.text(2.0, -280, '* 3rd run: 0 swap because earlier runs already evicted pages',
            fontsize=8, color='#6c757d', ha='center', style='italic')

    ax.set_xlabel('Audio Duration (real TTS speech)', labelpad=30)
    ax.set_ylabel('Swap Increase (MB)')
    ax.set_title('Whisper Memory Impact — Alongside Ollama (10 GB model loaded)',
                 fontsize=13, fontweight='bold', pad=12)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.legend(loc='upper left', framealpha=0.8, edgecolor='#30363d', facecolor='#161b22')
    ax.grid(axis='y', alpha=0.3, zorder=0)
    ax.set_ylim(-50, 1400)

    plt.tight_layout()
    plt.savefig(f'{OUT_DIR}/whisper-memory.png', dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Saved {OUT_DIR}/whisper-memory.png")


# ══════════════════════════════════════════════════════════════════════
# CHART 3: Model Speed Comparison (Routing Context)
# ══════════════════════════════════════════════════════════════════════

def chart_model_speed():
    """Horizontal bar: gen speed + prefill speed for all four routing models."""
    models = ['qwen2.5:3b\n(draft candidate)',
              '★ gemma4-26b-q3\n26B MoE A4B',
              'qwen3.5-35b-a3b-iq2m\nMoE 35B-A3B',
              'qwen3.5:9b\nQ4_K_M']
    gen_speed = [101.9, 39.0, 37.7, 31.8]
    prefill_speed = [309.2, 1238.0, 69.1, 109.0]
    sizes_gb = [1.8, 13.5, 10.6, 6.1]

    y = np.arange(len(models))
    h = 0.3

    fig, ax = plt.subplots(figsize=(10, 6.5))
    b1 = ax.barh(y + h/2, gen_speed, h, color=C_MOE, label='Generation (tok/s)', edgecolor='none', zorder=3)
    b2 = ax.barh(y - h/2, prefill_speed, h, color=C_9B, label='Prefill (tok/s)', edgecolor='none', zorder=3)

    for i, rect in enumerate(b1):
        w = rect.get_width()
        ax.text(w + 3, rect.get_y() + rect.get_height()/2,
                f'{gen_speed[i]:.1f}', va='center', fontsize=10, color=C_MOE, fontweight='bold')
    for i, rect in enumerate(b2):
        w = rect.get_width()
        ax.text(w + 3, rect.get_y() + rect.get_height()/2,
                f'{prefill_speed[i]:.1f}', va='center', fontsize=10, color=C_9B, fontweight='bold')

    # Size annotations — right-aligned, well away from bar labels
    for i in range(len(models)):
        ax.text(max(max(prefill_speed), max(gen_speed)) * 1.12, y[i], f'{sizes_gb[i]} GB',
                va='center', ha='right',
                fontsize=9, color='#8b949e',
                bbox=dict(boxstyle='round,pad=0.2', facecolor='#161b22',
                          edgecolor='#30363d', alpha=0.8))

    ax.set_xlabel('Tokens / second')
    ax.set_title('Model Speed Comparison — Smart Routing Context', fontsize=13, fontweight='bold', pad=20)
    ax.set_yticks(y)
    ax.set_yticklabels(models, fontsize=10)
    ax.legend(loc='upper right', framealpha=0.8, edgecolor='#30363d', facecolor='#161b22')
    ax.grid(axis='x', alpha=0.3, zorder=0)
    ax.set_xlim(0, max(max(prefill_speed), max(gen_speed)) * 1.15)

    # Routing role labels — right of the prefill bar (longest bar per model)
    # y-offsets to avoid collision with GB size annotations
    label_y_offsets = [0, 0.22, 0, 0]
    for i, label in enumerate(['spec decode draft (blocked)',
                               'primary chat (≤40K tokens)',
                               'fallback chat (>40K tokens)',
                               'vision + long ctx route']):
        right_edge = max(gen_speed[i], prefill_speed[i])
        alpha = 0.6 if i == 0 else 0.9
        ax.text(right_edge + 35, y[i] + label_y_offsets[i], f'← {label}',
                fontsize=8, color='#8b949e', va='center', ha='left', alpha=alpha,
                style='italic')

    plt.tight_layout()
    plt.savefig(f'{OUT_DIR}/model-routing-speed.png', dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Saved {OUT_DIR}/model-routing-speed.png")


# ══════════════════════════════════════════════════════════════════════
# CHART 4: Speculative Decoding — Memory Budget
# ══════════════════════════════════════════════════════════════════════

def chart_spec_decode_memory():
    """Stacked bar showing memory usage for spec decode scenario."""
    scenarios = ['Draft only\n(qwen2.5:3b)', 'Verifier only\n(Qwen MoE 35B-A3B)', 'Dual load\n(spec decode)', 'Available\n(16 GB UMA)']
    draft_sizes = [1.8, 0, 1.8, 0]
    verifier_sizes = [0, 10.6, 10.6, 0]
    kv_sizes = [0.2, 1.8, 2.0, 0]
    os_sizes = [1.5, 1.5, 1.5, 0]
    available = [0, 0, 0, 16]

    x = np.arange(len(scenarios))
    w = 0.5

    fig, ax = plt.subplots(figsize=(9, 5.5))

    # Available total
    ax.bar(x, available, w, color=C_FREE, alpha=0.2, edgecolor=C_FREE, linewidth=1.5, zorder=2, label='Available')

    # Stacked components
    b1 = ax.bar(x, draft_sizes, w, color=C_3B, edgecolor='none', zorder=3, label='Draft (3B)')
    b2 = ax.bar(x, verifier_sizes, w, bottom=draft_sizes, color=C_MOE, edgecolor='none', zorder=3, label='Verifier (MoE)')
    b3_bottom = [d + v for d, v in zip(draft_sizes, verifier_sizes)]
    b3 = ax.bar(x, kv_sizes, w, bottom=b3_bottom, color=C_KV, edgecolor='none', zorder=3, label='KV cache')
    b4_bottom = [b + k for b, k in zip(b3_bottom, kv_sizes)]
    b4 = ax.bar(x, os_sizes, w, bottom=b4_bottom, color='#6c757d', edgecolor='none', zorder=3, label='OS + overhead')

    # Total labels
    totals = [sum(x) for x in zip(draft_sizes, verifier_sizes, kv_sizes, os_sizes)]
    for i in range(3):
        t = totals[i]
        ax.text(x[i], t + 0.3, f'{t:.1f} GB', ha='center', va='bottom',
                fontsize=11, fontweight='bold',
                color='#e74c3c' if t > 14 else '#c9d1d9')

    # 16 GB limit line
    ax.axhline(y=16, color='#e74c3c', linestyle='--', alpha=0.6, zorder=4)
    ax.text(3.35, 16.3, '16 GB UMA limit', ha='right', fontsize=9, color='#e74c3c', alpha=0.8)

    # BLOCKED watermark on dual load
    ax.text(2, 8, 'BLOCKED\nby Ollama', ha='center', va='center',
            fontsize=16, fontweight='bold', color='#e74c3c', alpha=0.4,
            rotation=15, zorder=5)

    ax.set_ylabel('Memory (GB)')
    ax.set_title('Speculative Decoding Memory Budget — BC-250', fontsize=13, fontweight='bold', pad=12)
    ax.set_xticks(x)
    ax.set_xticklabels(scenarios, fontsize=10)
    ax.legend(loc='upper left', framealpha=0.8, edgecolor='#30363d', facecolor='#161b22', fontsize=9)
    ax.grid(axis='y', alpha=0.3, zorder=0)
    ax.set_ylim(0, 18.5)

    plt.tight_layout()
    plt.savefig(f'{OUT_DIR}/spec-decode-memory.png', dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Saved {OUT_DIR}/spec-decode-memory.png")


# ══════════════════════════════════════════════════════════════════════
# CHART 5: Signal Pipeline Architecture
# ══════════════════════════════════════════════════════════════════════

def chart_signal_pipeline():
    """Flow diagram showing the Signal message routing pipeline."""
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 6)
    ax.axis('off')

    box_style = dict(boxstyle='round,pad=0.4', facecolor='#21262d', edgecolor='#58a6ff', linewidth=1.5)
    box_green = dict(boxstyle='round,pad=0.4', facecolor='#1a2b1a', edgecolor=C_TURBO, linewidth=1.5)
    box_orange = dict(boxstyle='round,pad=0.4', facecolor='#2b1f0e', edgecolor=C_9B, linewidth=1.5)
    box_blue = dict(boxstyle='round,pad=0.4', facecolor='#0e1b2b', edgecolor=C_MOE, linewidth=1.5)
    box_purple = dict(boxstyle='round,pad=0.4', facecolor='#1f0e2b', edgecolor=C_3B, linewidth=1.5)

    # Input
    ax.text(1, 3, 'Signal\nMessage', ha='center', va='center', fontsize=12,
            fontweight='bold', bbox=box_style)

    # Router
    ax.text(3.5, 3, 'Message\nRouter', ha='center', va='center', fontsize=11,
            fontweight='bold', bbox=dict(boxstyle='round,pad=0.4', facecolor='#2d1f0e',
            edgecolor='#f0ad4e', linewidth=2))

    # Branches
    ax.text(6.5, 5, 'Audio\nWhisper-cli\nlarge-v3-turbo', ha='center', va='center',
            fontsize=10, bbox=box_green)
    ax.text(6.5, 3, 'Image\nqwen3.5:9b\nvision', ha='center', va='center',
            fontsize=10, bbox=box_orange)
    ax.text(6.5, 1, 'Text\nchoose_chat_model()\ngemma4 or Qwen MoE', ha='center', va='center',
            fontsize=10, bbox=box_blue)

    # Output
    ax.text(9.5, 5, 'Transcription\n→ LLM summary', ha='center', va='center',
            fontsize=10, bbox=box_green)
    ax.text(9.5, 3, 'Image\nanalysis', ha='center', va='center',
            fontsize=10, bbox=box_orange)
    ax.text(9.5, 1, 'Chat\nresponse', ha='center', va='center',
            fontsize=10, bbox=box_blue)

    # Arrows
    arrow_opts = dict(arrowstyle='->', color='#58a6ff', lw=1.5)
    ax.annotate('', xy=(2.5, 3), xytext=(1.8, 3), arrowprops=arrow_opts)
    ax.annotate('', xy=(5.3, 5), xytext=(4.3, 3.3), arrowprops=dict(arrowstyle='->', color=C_TURBO, lw=1.5))
    ax.annotate('', xy=(5.3, 3), xytext=(4.3, 3), arrowprops=dict(arrowstyle='->', color=C_9B, lw=1.5))
    ax.annotate('', xy=(5.3, 1), xytext=(4.3, 2.7), arrowprops=dict(arrowstyle='->', color=C_MOE, lw=1.5))

    ax.annotate('', xy=(8.5, 5), xytext=(7.6, 5), arrowprops=dict(arrowstyle='->', color=C_TURBO, lw=1.5))
    ax.annotate('', xy=(8.5, 3), xytext=(7.6, 3), arrowprops=dict(arrowstyle='->', color=C_9B, lw=1.5))
    ax.annotate('', xy=(8.5, 1), xytext=(7.6, 1), arrowprops=dict(arrowstyle='->', color=C_MOE, lw=1.5))

    # Labels on arrows
    ax.text(4.45, 4.35, 'audio/*', fontsize=9, color=C_TURBO, fontweight='bold',
            rotation=0, rotation_mode='anchor')
    ax.text(4.6, 3.15, 'image/*', fontsize=9, color=C_9B, fontweight='bold',
            rotation=0, rotation_mode='anchor')
    ax.text(4.45, 1.65, 'text', fontsize=9, color=C_MOE, fontweight='bold',
            rotation=0, rotation_mode='anchor')

    # Title
    ax.text(6, 5.8, 'Signal Message Processing Pipeline — BC-250',
            ha='center', va='top', fontsize=14, fontweight='bold')

    # Size/speed annotations
    ax.text(6.5, 4.15, '~4s for 30s audio', fontsize=8, ha='center', color='#8b949e', style='italic')
    ax.text(6.5, 2.15, 'think: false, 65K ctx', fontsize=8, ha='center', color='#8b949e', style='italic')
    ax.text(6.5, 0.15, '\u226440K \u2192 gemma4-26b, >40K \u2192 qwen3.5-35b-a3b', fontsize=8, ha='center', color='#8b949e', style='italic')

    plt.savefig(f'{OUT_DIR}/signal-pipeline.png', dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Saved {OUT_DIR}/signal-pipeline.png")


# ══════════════════════════════════════════════════════════════════════
# CHART 6: Whisper Memory Budget (stacked, shows why turbo fits)
# ══════════════════════════════════════════════════════════════════════

def chart_whisper_budget():
    """Horizontal stacked bar showing memory layout for each scenario:
    Ollama model + whisper model + OS/buffers vs 16 GB physical RAM."""
    scenarios = [
        'Ollama only\n(baseline)',
        'Ollama +\nlarge-v3-turbo',
        'Ollama +\nlarge-v3',
    ]
    # Component sizes in GB
    ollama_model = [10.6, 10.6, 10.6]
    whisper_model = [0.0, 1.6, 2.9]
    os_buffers = [3.5, 3.5, 3.5]
    total = [o + w + b for o, w, b in zip(ollama_model, whisper_model, os_buffers)]

    y = np.arange(len(scenarios))
    h = 0.5

    fig, ax = plt.subplots(figsize=(10, 4))

    # Stacked horizontal bars
    b1 = ax.barh(y, ollama_model, h, color=C_MOE, label='Ollama model (10.6 GB)',
                 edgecolor='none', zorder=3)
    b2 = ax.barh(y, whisper_model, h, left=ollama_model, color=C_TURBO,
                 label='Whisper model', edgecolor='none', zorder=3)
    b3 = ax.barh(y, os_buffers, h,
                 left=[o + w for o, w in zip(ollama_model, whisper_model)],
                 color='#6c757d', label='OS + buffers (~3.5 GB)',
                 edgecolor='none', zorder=3)

    # 16 GB line
    ax.axvline(x=16.0, color=C_SWAP, linewidth=2, linestyle='--', zorder=4, alpha=0.9)
    ax.text(16.1, 2.35, '16 GB\nphysical\nRAM', fontsize=9, color=C_SWAP,
            fontweight='bold', va='top')

    # Overflow annotation for large-v3
    overflow = total[2] - 16.0
    if overflow > 0:
        ax.annotate(f'+{overflow:.1f} GB → swap',
                    xy=(16.0, 0), xytext=(17.5, -0.15),
                    fontsize=10, color=C_SWAP, fontweight='bold',
                    arrowprops=dict(arrowstyle='->', color=C_SWAP, lw=1.5),
                    zorder=5)

    # "Fits" annotation for turbo
    ax.text(total[1] + 0.2 + 1.2, 1, '✓ fits', fontsize=10, color=C_TURBO,
            fontweight='bold', va='center')

    # Total labels at end of each bar
    for i, t in enumerate(total):
        ax.text(t + 0.15, i, f'{t:.1f} GB', fontsize=9, va='center',
                color='#8b949e')

    # Component size labels inside bars
    for i in range(len(scenarios)):
        # Ollama label (always show)
        ax.text(ollama_model[i] / 2, i, '10.6', fontsize=9, ha='center',
                va='center', color='white', fontweight='bold')
        # Whisper label (if visible)
        if whisper_model[i] > 0.5:
            mid = ollama_model[i] + whisper_model[i] / 2
            ax.text(mid, i, f'{whisper_model[i]}', fontsize=9, ha='center',
                    va='center', color='white', fontweight='bold')

    ax.set_xlabel('Memory (GB)')
    ax.set_title('Whisper Memory Budget — Why large-v3-turbo Wins on 16 GB UMA',
                 fontsize=13, fontweight='bold', pad=12)
    ax.set_yticks(y)
    ax.set_yticklabels(scenarios)
    ax.set_xlim(0, 19)
    ax.legend(loc='lower left', framealpha=0.8, edgecolor='#30363d',
              facecolor='#161b22', fontsize=9)
    ax.grid(axis='x', alpha=0.3, zorder=0)

    plt.tight_layout()
    plt.savefig(f'{OUT_DIR}/whisper-memory-budget.png', dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Saved {OUT_DIR}/whisper-memory-budget.png")


# ══════════════════════════════════════════════════════════════════════

def main():
    print("Generating new feature benchmark charts...")
    chart_whisper_wall()
    chart_whisper_memory()
    chart_whisper_budget()
    chart_model_speed()
    chart_spec_decode_memory()
    chart_signal_pipeline()
    print("Done — all charts saved to images/charts/")


if __name__ == "__main__":
    main()
