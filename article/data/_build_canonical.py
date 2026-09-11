#!/usr/bin/env python3
"""Build canonical 'stock 24-CU' perf table by merging Pass A (llama-completion
+ ollama subset) with Pass D (17 ollama re-measurements). Pass A wins for
overlapping keys — its measurements are guaranteed-fresh, while Pass D's
merge=True kept some stale Pass-B-era rows."""
import json
from pathlib import Path

D = Path(__file__).parent
A = json.load(open(D/"passA"/"step-1-perf.passA-stock24cu-20260603-1115.json"))["results"]
DD = json.load(open(D/"passD"/"step-1-perf.passD-stock24cu-20260604-1243.json"))["results"]
B = json.load(open(D/"passB"/"step-1-perf.passB-patched40cu-20260604-0033.json"))["results"]

DLIST = set(("llama3.2-3b-ollama,phi4-mini-ollama,gemma3-4b-ollama,gemma3-12b-ollama,"
             "qwen3-8b-ollama-q4km,qwen3-8b-ollama-q8,mistral-nemo-12b-ollama,"
             "qwen3-14b-ollama,deepseek-r1-14b-ollama,qwen3-30b-a3b-q2k-ollama,"
             "gemma2-9b-ollama,glm4-9b-ollama,granite3-3-8b-ollama,llama3-1-8b-ollama,"
             "qwen3-5-35b-a3b-iq2m-ollama,qwen3-5-27b-iq2m-ollama,"
             "qwen3-coder-30b-iq2m-ollama").split(","))

CANON = {}  # name -> {gen, pp, source}
# Start with Pass A (highest priority — verified stock 24-CU)
for name, m in A.items():
    a = m.get("aggregate", {})
    g, p = a.get("gen_tok_s_median"), a.get("prefill_tok_s_median")
    if g is not None:
        CANON[name] = {"gen": g, "pp": p, "source": "passA-stock24cu",
                       "backend": m.get("backend"), "kind": m.get("kind")}
# Add Pass D fresh-only entries (those in DLIST that weren't in Pass A)
for name in DLIST:
    if name in CANON:  # Pass A already has it; trust Pass A
        continue
    if name in DD:
        a = DD[name].get("aggregate", {})
        g, p = a.get("gen_tok_s_median"), a.get("prefill_tok_s_median")
        if g is not None:
            CANON[name] = {"gen": g, "pp": p, "source": "passD-stock24cu",
                           "backend": DD[name].get("backend"), "kind": DD[name].get("kind")}

# Print canonical
print(f"{'name':35s} {'gen':>8s} {'pp':>8s}  source")
for name in sorted(CANON):
    c = CANON[name]
    g = c["gen"]; p = c["pp"]
    pp_str = "-" if p is None else f"{p:.2f}"
    print(f"{name:35s} {g:>8.2f} {pp_str:>8s}  {c['source']:18s} ({c['backend']})")

# Save canonical
out = D/"_canonical_stock24cu_perf.json"
json.dump(CANON, open(out, "w"), indent=2)
print(f"\nwrote {out} ({len(CANON)} models)")

# Build paired Pass A vs Pass B table for Table 10
PAIRED = []
for name, c in CANON.items():
    if c["source"] != "passA-stock24cu":
        continue
    if name not in B:
        continue
    b = B[name].get("aggregate", {})
    bg, bp = b.get("gen_tok_s_median"), b.get("prefill_tok_s_median")
    if bg is None: continue
    PAIRED.append((name, c["gen"], c["pp"], bg, bp))

print(f"\nPass A↔B paired: {len(PAIRED)} models")
gen_ratios = []
pp_ratios = []
for name, ag, ap, bg, bp in PAIRED:
    gr = bg/ag
    gen_ratios.append(gr)
    if ap and bp:
        pr = bp/ap
        pp_ratios.append(pr)
        print(f"  {name:30s} gen {ag:>6.2f}->{bg:>6.2f} ({gr:.2f}x)  pp {ap:>6.2f}->{bp:>6.2f} ({pr:.2f}x)")
    else:
        print(f"  {name:30s} gen {ag:>6.2f}->{bg:>6.2f} ({gr:.2f}x)  pp n/a")
import statistics
print(f"\n  gen median uplift: {statistics.median(gen_ratios):.3f}x  range {min(gen_ratios):.2f}-{max(gen_ratios):.2f}")
print(f"  pp  median uplift: {statistics.median(pp_ratios):.3f}x  range {min(pp_ratios):.2f}-{max(pp_ratios):.2f}")
