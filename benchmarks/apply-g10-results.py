#!/usr/bin/env python3
"""G10 post-processing: compute new Δgen values from n=3 24-CU data vs n=2 40-CU data.

Run after G10 completes and after copying:
  bc250:~/phase-c-out/results/step-2-ctx-quality.json
→ benchmarks/results-phase-d-24cu-ladder/step-2-ctx-quality.24cu.json

Usage: python3 apply-g10-results.py
"""

import json, statistics
from pathlib import Path

HERE = Path(__file__).parent

path_24cu = HERE / "results-phase-d-24cu-ladder" / "step-2-ctx-quality.24cu.json"
path_40cu = HERE / "results-phase-d-stage2" / "step-2-ctx-quality.40cu.json"

TIERS = [4096, 8192, 16384, 24576, 32768, 49152, 65536]
MODELS = [
    "granite-4.0-h-tiny",
    "qwen3.5-9b-ollama",
    "gemma4-latest",
    "deepseek-r1-14b",
]


def load_gen(path):
    if not path.exists():
        print(f"MISSING: {path}")
        return {}
    raw = json.loads(path.read_text())
    out = {}
    for m, rec in raw.get("results", {}).items():
        for tier_s, td in (rec.get("tiers") or {}).items():
            runs = [r["gen_tok_s"] for r in (td.get("runs") or []) if "gen_tok_s" in r]
            n = len(runs)
            if runs:
                out[(m, int(tier_s))] = (statistics.median(runs), n)
    return out


lad24 = load_gen(path_24cu)
lad40 = load_gen(path_40cu)

print(f"24-CU data: {len(lad24)} cells")
print(f"40-CU data: {len(lad40)} cells")
print()

print("Δgen table (40-CU / 24-CU):")
print(f"{'Model':<30} " + " ".join(f"{t//1024}K" for t in TIERS))
print("-" * 90)

all_deltas = []
for m in MODELS:
    row = f"{m:<30}"
    for t in TIERS:
        g24_data = lad24.get((m, t))
        g40_data = lad40.get((m, t))
        if g24_data and g40_data:
            g24, n24 = g24_data
            g40, n40 = g40_data
            delta = g40 / g24
            row += f" {delta:.2f}×"
            all_deltas.append(delta)
        else:
            row += "  ceil."
    print(row)

print()
if all_deltas:
    print(f"Overall median Δgen: {statistics.median(all_deltas):.2f}×")
    print(f"All deltas: {[f'{d:.2f}' for d in sorted(all_deltas)]}")

print()
print("Article table values (for tab:cu_unlock_ctx_g3):")
print("\\toprule")
for m in MODELS:
    row_vals = []
    for t in TIERS:
        if t > 32768 and m == "deepseek-r1-14b":
            row_vals.append("ceil.")
            continue
        g24_data = lad24.get((m, t))
        g40_data = lad40.get((m, t))
        if g24_data and g40_data:
            g24, _ = g24_data
            g40, _ = g40_data
            delta = g40 / g24
            row_vals.append(f"{delta:.2f}\\\\times")
        else:
            row_vals.append("ceil.")
    # Only show 4K, 16K, 32K, 64K (the 4 columns in the table)
    table_tiers = [4096, 16384, 32768, 65536]
    table_vals = []
    for t in table_tiers:
        if t > 32768 and m == "deepseek-r1-14b":
            table_vals.append("ceil.")
            continue
        g24_data = lad24.get((m, t))
        g40_data = lad40.get((m, t))
        if g24_data and g40_data:
            g24, _ = g24_data
            g40, _ = g40_data
            delta = g40 / g24
            table_vals.append(f"${delta:.2f}\\\\times$")
        else:
            table_vals.append("ceil.")
    label = m.replace("-", "\\\\-").replace("_", "\\\\_")
    print(f"{m:<30}  &  " + " & ".join(table_vals) + " \\\\\\\\")
