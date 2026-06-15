#!/usr/bin/env python3
"""Summarize G5 KV-longctx benchmark results (step-kv-longctx.json)."""
import json, sys

path = sys.argv[1] if len(sys.argv) > 1 else "benchmarks/results-kv-longctx/step-kv-longctx.json"
data = json.load(open(path))

print(f"Model: {data.get('model')} ({data.get('model_tag')})")
print(f"Captured: {data.get('captured_at')}")
print(f"n_gen={data.get('n_gen')}  runs/cell={data.get('runs_per_cell')}")
print()

kv_types = data.get("kv_types", [])
tiers = data.get("tiers", [])

print(f"{'ctx':<8}", end="")
for kv in kv_types:
    print(f"  {kv+' gen':>14}  {kv+' pp':>12}", end="")
print()
print("-" * (8 + 28 * len(kv_types)))

for ctx in tiers:
    print(f"{ctx//1024}K{'':<6}", end="")
    for kv in kv_types:
        cell = data["results"].get(kv, {}).get(str(ctx), {})
        agg = cell.get("aggregate", {})
        g = agg.get("gen_tok_s_median")
        pp = agg.get("prefill_tok_s_median")
        cv = agg.get("gen_tok_s_cv_pct")
        n_ok = agg.get("n_ok", 0)
        n_runs = agg.get("n_runs", 0)
        if g:
            gstr = f"{g:.1f} t/s (cv={cv}%)" if cv else f"{g:.1f} t/s"
        else:
            gstr = "FAIL"
        ppstr = f"{pp:.0f} t/s" if pp else "—"
        print(f"  {gstr:>14}  {ppstr:>12}", end="")
    print()

print()
print("KV width effect (Q4_0 vs Q8_0 slowdown):")
for ctx in tiers:
    g40 = data["results"].get("q4_0", {}).get(str(ctx), {}).get("aggregate", {}).get("gen_tok_s_median")
    g80 = data["results"].get("q8_0", {}).get(str(ctx), {}).get("aggregate", {}).get("gen_tok_s_median")
    if g40 and g80:
        delta = (g40 - g80) / g40 * 100
        print(f"  {ctx//1024}K: Q4_0={g40:.1f} Q8_0={g80:.1f} -> Q8_0 is {delta:+.1f}% vs Q4_0")
    else:
        print(f"  {ctx//1024}K: missing data")

print()
print("Per-run detail:")
for kv in kv_types:
    for ctx in tiers:
        cell = data["results"].get(kv, {}).get(str(ctx), {})
        for r in cell.get("runs", []):
            tag = r.get("label", "?")
            status = r.get("status", "?")
            gen = r.get("gen_tok_s", "—")
            pp = r.get("prefill_tok_s", "—")
            n_prompt = r.get("n_prompt", "?")
            needle = r.get("needle_pass", "?")
            temp = r.get("gpu_overlay", {}).get("temp_c_max", "?")
            print(f"  {tag:<30}  status={status}  gen={gen}  pp={pp}  "
                  f"n_prompt={n_prompt}  needle={needle}  temp_max={temp}")
