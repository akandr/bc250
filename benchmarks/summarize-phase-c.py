#!/usr/bin/env python3
"""Summarize Phase C results into a markdown table suitable for the article
and Readme. Prints to stdout; can be piped or redirected.

Usage:
    ./summarize-phase-c.py results-phase-c/step-1-perf.json
"""
import json, sys, os
from statistics import mean

# Friendly names + display order for tables. Models not in this list are
# appended at the end in JSON order.
DISPLAY = [
    ("granite-4.0-h-tiny",      "Granite 4.0-H Tiny (Q4_K_M, Mamba-MoE)"),
    ("gpt-oss-20b-mxfp4",       "GPT-OSS 20B (MXFP4)"),
    ("qwen3-coder-30b-iq2m",    "Qwen3-Coder 30B-A3B (IQ2_M)"),
    ("qwen3.5-35b-iq2m",        "Qwen3.5 35B-A3B (IQ2_M)"),
    ("qwen3.6-35b-iq2m",        "Qwen3.6 35B-A3B (IQ2_M)"),
    ("gemma4-26b-q3",           "Gemma4 26B-A4B Q3 (Ollama-only)"),
    ("gemma4-latest",           "Gemma4 multimodal (Ollama-only)"),
    ("qwen3.5-9b-ollama",       "Qwen3.5 9B (Ollama)"),
    ("qwen3.5-9b-q4km",         "Qwen3.5 9B Q4_K_M (vanilla — INCOMPATIBLE)"),
    ("deepseek-r1-14b",         "DeepSeek-R1 14B (Q4_K_M)"),
]

def fmt(x, p=2):
    if x is None: return "—"
    if isinstance(x, (int, float)): return f"{x:.{p}f}"
    return str(x)

def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    path = sys.argv[1]
    data = json.load(open(path))
    results = data["results"]
    rows = []
    seen = set()
    for key, label in DISPLAY:
        if key in results: rows.append((key, label, results[key])); seen.add(key)
    for k, v in results.items():
        if k not in seen: rows.append((k, k, v))

    print(f"# Phase C — Step 1 perf baseline (n=3 runs/model, STD ~250-token prompt, 4096 ctx, 100 tokens generated)\n")
    print(f"| Model | Backend | Kind | n_ok | gen t/s (median) | CV % | prefill t/s (median) | TTFT s (median) |")
    print(f"|---|---|---|---|---|---|---|---|")
    for key, label, d in rows:
        a = d["aggregate"]
        n_ok = sum(1 for r in d["runs"] if r.get("status") == "ok")
        n = len(d["runs"])
        print(f"| {label} | {d.get('backend','?')} | {d.get('kind','?')} | {n_ok}/{n} "
              f"| {fmt(a.get('gen_tok_s_median'))} | {fmt(a.get('gen_tok_s_cv_pct'))} "
              f"| {fmt(a.get('prefill_tok_s_median'))} | {fmt(a.get('ttft_s_median'))} |")

    print("\n## GPU overlay (run-2 warm steady-state per model)\n")
    print(f"| Model | sclk_mhz_mean | sclk_mhz_max | temp_C_max | throttle |")
    print(f"|---|---|---|---|---|")
    for key, label, d in rows:
        ok_runs = [r for r in d["runs"] if r.get("status") == "ok"]
        if not ok_runs: continue
        rep = ok_runs[-1].get("gpu_overlay", {})
        print(f"| {label} | {fmt(rep.get('sclk_mhz_mean'),1)} | {fmt(rep.get('sclk_mhz_max'),0)} "
              f"| {fmt(rep.get('temp_c_max'),0)} | {rep.get('throttle_flag')} |")

if __name__ == "__main__":
    main()
