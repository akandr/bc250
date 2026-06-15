#!/usr/bin/env python3
"""Merge step-1-perf + g2-extra data → results-moe-active/g2-dataset.json
and print a summary table for G2 verification.
"""
import json, pathlib, statistics

HERE = pathlib.Path(__file__).parent

# ── Known model metadata ─────────────────────────────────────────────────────
META = {
    # ── MoE / hybrid (llama.cpp backend from step-1-perf) ───────────────────
    "qwen3-coder-30b-iq2m":  dict(display="qwen3-coder-30b-A3B",  kind="moe",    active_b=3.0,  total_b=30.0, quant="IQ2\_M",   backend="llama.cpp"),
    "qwen3.5-35b-iq2m":      dict(display="qwen3.5-35b-A3B",      kind="moe",    active_b=3.0,  total_b=35.0, quant="IQ2\_M",   backend="llama.cpp"),
    "qwen3.6-35b-iq2m":      dict(display="qwen3.6-35b-A3B",      kind="moe",    active_b=3.0,  total_b=35.0, quant="IQ2\_M",   backend="llama.cpp"),
    "gpt-oss-20b-mxfp4":     dict(display="gpt-oss-20b-A3.7B",    kind="moe",    active_b=3.7,  total_b=20.0, quant="MXFP4",    backend="llama.cpp"),
    "granite-4.0-h-tiny":    dict(display="granite-4.0-h-tiny",   kind="hybrid", active_b=3.3,  total_b=4.0,  quant="Q4\_K\_M", backend="llama.cpp"),
    # ── Dense llama.cpp ──────────────────────────────────────────────────────
    "deepseek-r1-14b":       dict(display="deepseek-r1:14b",       kind="dense",  active_b=14.0, total_b=14.0, quant="Q4\_K\_M", backend="llama.cpp"),
    # ── Dense Ollama (step-1-perf) ───────────────────────────────────────────
    "qwen3.5-9b-ollama":     dict(display="qwen3.5:9b",            kind="dense",  active_b=9.0,  total_b=9.0,  quant="Q4\_K\_M", backend="ollama"),
    # ── Dense Ollama (g2-extra) ──────────────────────────────────────────────
    "llama3.2:3b":           dict(display="llama3.2:3b",           kind="dense",  active_b=3.0,  total_b=3.0,  quant="Q4\_K\_M", backend="ollama"),
    "qwen3:4b":              dict(display="qwen3:4b",              kind="dense",  active_b=4.0,  total_b=4.0,  quant="Q4\_K\_M", backend="ollama"),
    "qwen3:8b-q8_0":         dict(display="qwen3:8b-Q8\_0",        kind="dense",  active_b=8.0,  total_b=8.0,  quant="Q8\_0",    backend="ollama"),
    "qwen3:14b":             dict(display="qwen3:14b",             kind="dense",  active_b=14.0, total_b=14.0, quant="Q4\_K\_M", backend="ollama"),
}

# ── Load step-1-perf ─────────────────────────────────────────────────────────
perf_path = HERE / "results-phase-c-r2" / "step-1-perf.json"
perf_data = json.loads(perf_path.read_text())["results"]

dataset = {}

for key, meta in META.items():
    gen = None
    n_runs = 0

    if key in perf_data:
        agg = perf_data[key].get("aggregate", {})
        gen = agg.get("gen_tok_s_median")
        n_runs = len(perf_data[key].get("runs", []))
    else:
        # Will be filled from g2-extra below
        pass

    if gen is not None:
        dataset[key] = {**meta, "gen_tok_s": gen, "n_runs": n_runs, "source": "step-1-perf"}

# ── Merge g2-extra results ───────────────────────────────────────────────────
extra_path = HERE / "results-moe-active" / "g2-extra-results.json"
if extra_path.exists():
    extra = json.loads(extra_path.read_text())["results"]
    for key, rec in extra.items():
        gen = rec.get("aggregate", {}).get("gen_tok_s_median")
        n_ok = rec.get("aggregate", {}).get("n_ok", 0)
        if gen is not None and key in META:
            dataset[key] = {**META[key], "gen_tok_s": gen, "n_runs": n_ok, "source": "g2-extra"}

# ── Write merged dataset ─────────────────────────────────────────────────────
out_path = HERE / "results-moe-active" / "g2-dataset.json"
out_path.write_text(json.dumps(dataset, indent=2))
print(f"Wrote {out_path}")

# ── Print summary ────────────────────────────────────────────────────────────
print(f"\n{'Model':<30} {'kind':>7} {'active':>7} {'total':>7} {'gen tok/s':>10} {'n':>3} {'source'}")
print("-" * 85)
for key in sorted(dataset, key=lambda k: dataset[k]["active_b"]):
    r = dataset[key]
    print(f"{key:<30} {r['kind']:>7} {r['active_b']:>6.1f}B {r['total_b']:>6.1f}B "
          f"{r['gen_tok_s']:>10.2f} {r['n_runs']:>3}  {r['source']}")
