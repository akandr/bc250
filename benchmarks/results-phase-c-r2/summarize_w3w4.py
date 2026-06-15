#!/usr/bin/env python3
"""Summarize W3+W4 results for article tables."""
import json, os, sys, statistics
from pathlib import Path

ROOT = Path(__file__).parent
W3 = ROOT / "w3"
W4 = ROOT / "w4"

def load(p): return json.load(open(p))

def fmt(x, d=2):
    if x is None: return "—"
    if isinstance(x, (int, float)): return f"{x:.{d}f}"
    return str(x)

# ============ W3.A: power sweep ============
print("="*78)
print("W3.A power sweep (5 models × 2 runs, 5Hz hwmon)")
print("="*78)
pa = load(W3/"step-w3a-perf.json")
pw = load(W3/"power-w3a.json")
print(f"Power: mean={pw['power_mw_mean']/1000:.1f}W  p50={pw['power_mw_p50']/1000:.1f}W  "
      f"p95={pw['power_mw_p95']/1000:.1f}W  max={pw['power_mw_max']/1000:.1f}W")
print(f"Energy={pw['energy_j']/1000:.1f}kJ  duration={pw['duration_s']:.0f}s  "
      f"sclk_mean={pw['sclk_mhz_mean']}MHz  T_max={pw['temp_c_max']}C  GTT_max={pw['gtt_gib_max']:.1f}GiB")
print()
print(f"{'model':30s} {'gen_med':>9s} {'pp_med':>9s} {'cv%':>6s}")
for name, v in pa["results"].items():
    a = v.get("aggregate", {})
    print(f"{name:30s} {fmt(a.get('gen_tok_s_median')):>9s} {fmt(a.get('prefill_tok_s_median')):>9s} {fmt(a.get('gen_tok_s_cv_pct'),3):>6s}")

# energy / token (overall)
total_gen_toks = 0
for v in pa["results"].values():
    for r in v.get("runs", []):
        if r.get("status") == "ok":
            total_gen_toks += r.get("n_gen", 0) or 0
if total_gen_toks:
    e_per_tok = pw["energy_j"] / total_gen_toks
    print(f"\nGross energy/gen_token (incl. load): {e_per_tok:.2f} J/tok over {total_gen_toks} gen tokens")

# ============ W3.B: n=5 stats ============
print("\n" + "="*78)
print("W3.B n=5 stats (6 models × 5 runs)")
print("="*78)
pb = load(W3/"step-w3b-stats-n5.json")
print(f"{'model':30s} {'n_ok':>4s} {'gen_med':>9s} {'gen_p05':>9s} {'gen_p95':>9s} {'cv%':>6s} {'pp_med':>9s} {'ttft_med':>9s}")
for name, v in pb["results"].items():
    a = v.get("aggregate", {})
    runs = v.get("runs", [])
    n_ok = sum(1 for r in runs if r.get("status") == "ok")
    gens = sorted([r["gen_tok_s"] for r in runs if r.get("status") == "ok" and r.get("gen_tok_s") is not None])
    p05 = gens[0] if gens else None
    p95 = gens[-1] if gens else None
    print(f"{name:30s} {n_ok:>4} {fmt(a.get('gen_tok_s_median')):>9s} {fmt(p05):>9s} {fmt(p95):>9s} "
          f"{fmt(a.get('gen_tok_s_cv_pct'),3):>6s} {fmt(a.get('prefill_tok_s_median')):>9s} {fmt(a.get('ttft_s_median')):>9s}")

# ============ W3.C: granite extreme ctx ============
print("\n" + "="*78)
print("W3.C granite-4.0-h-tiny extreme ctx (kv=q4_0)")
print("="*78)
pc = load(W3/"step-w3c-granite-extreme.json")
print(f"{'tier':>8s}  {'status':>10s}  {'n_ok':>4s}  {'needle':>6s}  {'gen_med':>9s}  {'pp_med':>9s}  {'note'}")
for name, v in pc["results"].items():
    for tier_key, t in v.get("tiers", {}).items():
        agg = t.get("aggregate", {})
        runs = t.get("runs", [])
        n_ok = sum(1 for r in runs if r.get("status") == "ok")
        n_needle = sum(1 for r in runs if r.get("needle_pass"))
        st = t.get("ladder_status", "ok")
        note = ""
        if runs:
            sr = runs[0].get("skip_reason", "") or ""
            if sr: note = sr
        print(f"{tier_key:>8s}  {st:>10s}  {n_ok}/{len(runs):<2}  {n_needle}/{len(runs):<3}  "
              f"{fmt(agg.get('gen_tok_s_median')):>9s}  {fmt(agg.get('prefill_tok_s_median')):>9s}  {note}")

# ============ W4.A: determinism / drift ============
print("\n" + "="*78)
print("W4.A determinism / drift (3 passes × 3 models × 3 runs)")
print("="*78)
passes = [load(W4/f"step-w4a-perf-pass{i}.json") for i in (1,2,3)]
models = list(passes[0]["results"].keys())
print(f"{'model':30s} {'pass':>5s}  {'gen_med':>9s}  {'cv%':>6s}  {'pp_med':>9s}")
drift_per_model = {}
for m in models:
    series = []
    for i, p in enumerate(passes, 1):
        a = p["results"].get(m, {}).get("aggregate", {})
        gen = a.get("gen_tok_s_median")
        cv = a.get("gen_tok_s_cv_pct")
        pp = a.get("prefill_tok_s_median")
        series.append(gen)
        print(f"{m:30s} {i:>5}  {fmt(gen):>9s}  {fmt(cv,3):>6s}  {fmt(pp):>9s}")
    if all(x is not None for x in series):
        drift = (max(series) - min(series)) / statistics.mean(series) * 100
        drift_per_model[m] = drift
        print(f"{'  -> drift (max-min)/mean':30s} {'':>5s}  {drift:>8.2f}%")

# ============ W4.B: heap A/B at filled ctx ============
print("\n" + "="*78)
print("W4.B heap A/B (phm0 vs phm1) at ctx tiers 2K/8K/32K")
print("="*78)
pb4 = load(W4/"step-w4b-heap-ab-filled.json")
res = pb4.get("results", {})
print(f"{'model':28s} {'ctx':>6s}  {'phm0 gen':>9s}  {'phm1 gen':>9s}  {'delta%':>7s}")
for name, mv in res.items():
    tiers = mv.get("tiers", {}) if isinstance(mv, dict) else {}
    for ctx_key, cell in tiers.items():
        def med(rlist):
            xs = [r.get("gen_tok_s") for r in rlist if isinstance(r, dict) and r.get("status")=="ok" and r.get("gen_tok_s") is not None]
            return statistics.median(xs) if xs else None
        g0 = med(cell.get("phm0", []))
        g1 = med(cell.get("phm1", []))
        d = ((g1-g0)/g0*100) if (g0 and g1) else None
        print(f"{name:28s} {ctx_key:>6}  {fmt(g0):>9s}  {fmt(g1):>9s}  {fmt(d,2):>7s}")

# ============ W4.C: gpt-oss retry ============
print("\n" + "="*78)
print("W4.C MXFP4 probe + gpt-oss perf retry")
print("="*78)
mp = load(W4/"step-w4c-mxfp4-probe.json")
print("MXFP4 probe:", json.dumps(mp.get("probes", mp), indent=2)[:2000])
gp = load(W4/"step-w4c-gpt-oss-perf.json")
print("\nperf:")
for name, v in gp["results"].items():
    a = v.get("aggregate", {})
    runs = v.get("runs", [])
    n_ok = sum(1 for r in runs if r.get("status") == "ok")
    print(f"  {name:30s} n_ok={n_ok}/{len(runs)}  gen_med={fmt(a.get('gen_tok_s_median'))}  "
          f"cv={fmt(a.get('gen_tok_s_cv_pct'),3)}%  pp={fmt(a.get('prefill_tok_s_median'))}")

# ============ W4.D: concurrent stress ============
print("\n" + "="*78)
print("W4.D concurrent stress (raw llama-completion: granite + deepseek)")
print("="*78)
sa = load(W4/"step-w4d-serial-A.json")
sb = load(W4/"step-w4d-serial-B.json")
pa4 = load(W4/"step-w4d-par-A.json")
pb4d = load(W4/"step-w4d-par-B.json")
pwd4 = load(W4/"power-w4d.json")
print(f"  serial-A (granite)   wall={sa['wall_s']:.2f}s  rc={sa['rc']}")
print(f"  serial-B (deepseek)  wall={sb['wall_s']:.2f}s  rc={sb['rc']}")
print(f"  par-A    (granite)   wall={pa4['wall_s']:.2f}s  rc={pa4['rc']}  -> {pa4['wall_s']/sa['wall_s']:.1f}× slower than serial")
print(f"  par-B    (deepseek)  wall={pb4d['wall_s']:.2f}s  rc={pb4d['rc']}  -> {pb4d['wall_s']/sb['wall_s']:.1f}× slower than serial")
print(f"  Power during parallel: mean={pwd4['power_mw_mean']/1000:.1f}W  p95={pwd4['power_mw_p95']/1000:.1f}W  "
      f"max={pwd4['power_mw_max']/1000:.1f}W  energy={pwd4['energy_j']/1000:.1f}kJ "
      f"GTT_max={pwd4['gtt_gib_max']:.1f}GiB T_max={pwd4['temp_c_max']}C")
