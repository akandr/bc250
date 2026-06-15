#!/usr/bin/env python3
"""Compute roofline data from measured hardware peaks + step-1-perf decode data.

Reads:
  benchmarks/results-roofline/roofline-raw.txt        (BW and FP peaks, 24-CU)
  article/data/_canonical_stock24cu_perf.json         (verified-stock 24-CU gen tok/s)

Writes:
  benchmarks/results-roofline/roofline-summary.json

NOTE: hardware peaks AND decode throughputs are both verified-stock 24-CU so
the roofline ceiling and the plotted decode points share one CU configuration.
Earlier revisions pulled gen tok/s from step-1-perf.json, which was collected
during the patched 40-CU period; those points are superseded by the canonical
24-CU values used here.
"""
import json
import pathlib
import re

HERE = pathlib.Path(__file__).parent

# ── Hardware peaks (from roofline-raw.txt) ──────────────────────────────────
BW_PEAK_GBS    = 357.29   # GB/s  (streaming copy microbench, 24 CU, oberon 1500 MHz)
FP32_PEAK_GFLOPS = 3901.2  # GFLOP/s (8 parallel FMA chains per thread)

RIDGE_FLOPS_PER_BYTE = FP32_PEAK_GFLOPS / BW_PEAK_GBS  # ≈ 10.92

# ── Quantization arithmetic intensity (AI = 2 / bytes_per_weight) ──────────
QUANT_AI = {
    "Q8_0":   2 / 1.000,    # 2.00 FLOP/byte
    "Q4_K_M": 2 / 0.5625,   # 3.56 FLOP/byte  (4.5 bits/weight)
    "Q4_0":   2 / 0.500,    # 4.00 FLOP/byte
    "MXFP4":  2 / 0.500,    # 4.00 FLOP/byte
    "IQ2_M":  2 / 0.3125,   # 6.40 FLOP/byte  (2.5 bits/weight)
}

# ── Model registry: (display_name, param_B, quant, file_gb, dense) ──────────
# file_gb from: du -sh /opt/models/*.gguf on bc250 (2026-06-07)
# dense=False for MoE/hybrid; active_param_b used for eff BW in those cases.
MODELS = {
    "deepseek-r1-14b": dict(
        label="deepseek-r1:14b\n(Q4_K_M dense)",
        param_b=14.0, active_param_b=14.0,
        quant="Q4_K_M", file_gb=8.4, dense=True,
    ),
    "granite-4.0-h-tiny": dict(
        label="granite-4.0-h-tiny\n(Q4_K_M hybrid)",
        param_b=7.1, active_param_b=3.3,   # hybrid; ~3.3B active estimated
        quant="Q4_K_M", file_gb=4.0, dense=False,
        note="hybrid; small file → possible L2 cache effects",
    ),
    "qwen3.5-35b-iq2m": dict(
        label="qwen3.5-35b-A3B\n(IQ2_M MoE)",
        param_b=35.0, active_param_b=3.0,
        quant="IQ2_M", file_gb=11.0, dense=False,
    ),
    "gpt-oss-20b-mxfp4": dict(
        label="gpt-oss-20b\n(MXFP4 MoE)",
        param_b=20.0, active_param_b=3.7,  # MoE, A3.7B approximate
        quant="MXFP4", file_gb=12.0, dense=False,
    ),
    "qwen3-coder-30b-iq2m": dict(
        label="qwen3-coder-30b-A3B\n(IQ2_M MoE)",
        param_b=30.0, active_param_b=3.0,
        quant="IQ2_M", file_gb=11.0, dense=False,
    ),
}

# ── Load gen_tok_s from the verified-stock 24-CU canonical perf set ─────────
perf_path = HERE.parent / "article" / "data" / "_canonical_stock24cu_perf.json"
perf_data = json.loads(perf_path.read_text())

results = {}
for key, spec in MODELS.items():
    rec = perf_data.get(key, {})
    gen_tok_s = rec.get("gen")
    if gen_tok_s is None:
        continue

    quant = spec["quant"]
    ai = QUANT_AI[quant]
    # Effective BW: for dense models use full file size; for MoE use active fraction.
    # file_gb comes from `du -h` (binary GiB); the peak BW is decimal GB/s
    # (1 GiB traffic / dispatch_s / 1e9), so convert GiB->GB before comparing.
    GIB_TO_GB = (1 << 30) / 1e9  # 1.073741824
    active_frac = spec["active_param_b"] / spec["param_b"]
    eff_weight_gb = spec["file_gb"] * GIB_TO_GB * (1.0 if spec["dense"] else active_frac)
    eff_bw_gbs = eff_weight_gb * gen_tok_s

    # Performance in GFLOP/s: 2 × active_params × gen_tok_s
    perf_gflops = 2.0 * spec["active_param_b"] * 1e9 * gen_tok_s / 1e9

    results[key] = {
        "label": spec["label"],
        "gen_tok_s": gen_tok_s,
        "quant": quant,
        "param_b": spec["param_b"],
        "active_param_b": spec["active_param_b"],
        "file_gb": spec["file_gb"],
        "dense": spec["dense"],
        "ai_flops_per_byte": round(ai, 3),
        "eff_weight_gb": round(eff_weight_gb, 2),
        "eff_bw_gbs": round(eff_bw_gbs, 1),
        "eff_bw_fraction_of_peak": round(eff_bw_gbs / BW_PEAK_GBS, 3),
        "perf_gflops": round(perf_gflops, 1),
        "bw_roofline_bound_gflops": round(ai * BW_PEAK_GBS, 1),
        **({} if "note" not in spec else {"note": spec["note"]}),
    }

summary = {
    "device": "AMD BC-250 (RADV GFX1013)",
    "date": "2026-06-07",
    "config": "24-CU, oberon-governor 1500 MHz",
    "bw_peak_gbs": BW_PEAK_GBS,
    "fp32_peak_gflops": FP32_PEAK_GFLOPS,
    "ridge_flops_per_byte": round(RIDGE_FLOPS_PER_BYTE, 2),
    "quant_ai": QUANT_AI,
    "models": results,
}

out = HERE / "results-roofline" / "roofline-summary.json"
out.write_text(json.dumps(summary, indent=2))
print("Wrote", out)

# ── Print summary table ──────────────────────────────────────────────────────
print(f"\nHardware:   BW {BW_PEAK_GBS:.0f} GB/s | FP32 {FP32_PEAK_GFLOPS:.0f} GFLOP/s | "
      f"ridge {RIDGE_FLOPS_PER_BYTE:.1f} FLOP/byte\n")
print(f"{'Model':<28} {'quant':>6} {'AI':>6} {'gen tok/s':>10} {'eff BW':>10} {'%peak':>7} {'perf GFLOP/s':>13}")
print("-" * 90)
for key, r in results.items():
    print(f"{key:<28} {r['quant']:>6} {r['ai_flops_per_byte']:>6.2f} "
          f"{r['gen_tok_s']:>10.2f} {r['eff_bw_gbs']:>9.1f}G "
          f"{r['eff_bw_fraction_of_peak']*100:>6.1f}% {r['perf_gflops']:>12.1f}")
