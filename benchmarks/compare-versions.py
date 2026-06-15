#!/usr/bin/env python3
"""Compare old (0.18) vs new (0.20) benchmark results."""
import json

with open("/Users/akandr/projects/bc250/benchmarks/results-llm-comprehensive.json") as f:
    old = json.load(f)
with open("/Users/akandr/projects/bc250/benchmarks/results-rebench-0.20.json") as f:
    new = json.load(f)

old_map = {}
for r in old:
    if r.get("status") == "OK":
        old_map[r["model"]] = r

print(f"{'Model':<50} {'Old':>6} {'New':>6} {'D':>6} {'D%':>6} {'OldPF':>8} {'NewPF':>8}")
print("-" * 95)

deltas = []
for r in new:
    m = r["model"]
    if r.get("status") != "OK":
        print(f"{m:<50} FAIL")
        continue
    ns = r["speed_4k"]
    if m in old_map:
        os_ = old_map[m]["speed_4k"]
        og = os_["gen_tok_s"]
        ng = ns["gen_tok_s"]
        delta = ng - og
        pct = (delta / og * 100) if og > 0 else 0
        deltas.append(pct)
        op = os_["prefill_tok_s"]
        np_ = ns["prefill_tok_s"]
        print(f"{m:<50} {og:>6.1f} {ng:>6.1f} {delta:>+6.1f} {pct:>+5.1f}% {op:>8.1f} {np_:>8.1f}")
    else:
        print(f"{m:<50}    NEW {ns['gen_tok_s']:>6.1f}                      {ns['prefill_tok_s']:>8.1f}")

if deltas:
    print(f"\nAverage generation delta: {sum(deltas)/len(deltas):+.1f}%")
    print(f"Min: {min(deltas):+.1f}%  Max: {max(deltas):+.1f}%")

# Context ceiling comparison
print("\n\nContext Ceiling Comparison:")
print(f"{'Model':<50} {'OldCtx':>8} {'NewCtx':>8} {'Change':>8}")
print("-" * 80)
for r in new:
    m = r["model"]
    if r.get("status") != "OK":
        continue
    new_ctx = r["max_ctx"]
    if m in old_map:
        old_ctx = old_map[m]["max_ctx"]
        change = "same" if old_ctx == new_ctx else f"{old_ctx//1024}K->{new_ctx//1024}K"
        nc = f"{new_ctx//1024}K"
        oc = f"{old_ctx//1024}K"
        print(f"{m:<50} {oc:>8} {nc:>8} {change:>8}")
    else:
        nc = f"{new_ctx//1024}K"
        print(f"{m:<50}      NEW {nc:>8}")
