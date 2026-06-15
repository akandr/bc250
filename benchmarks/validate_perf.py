#!/usr/bin/env python3
"""Validate new R-phase perf results against the May-16 overnight baseline.

For every (model, ctx) pair present in both datasets:
- new gen_tok_s median  vs  old avg_ts (n_gen>0) median
- new prefill_tok_s median  vs  old avg_ts (n_prompt>0) median
- compute pct delta; flag |delta| > 5% as suspicious

Goal: confirm that the queue-runner contamination did NOT depress
already-successful old throughput numbers.
"""
import json, os, sys, statistics
from collections import defaultdict
from pathlib import Path

NEW = Path("/Users/akandr/projects/bc250/benchmarks/results-phase-c-r2/step-1-perf.json")
OLD = Path("/Users/akandr/projects/bc250/benchmarks/results-overnight-20260516.json")
THRESHOLD_PCT = 5.0  # |delta| > 5% flagged


def load_new(path):
    """Returns {model: {ctx: {'gen': [t/s,...], 'prefill': [t/s,...]}}}."""
    d = json.load(open(path))
    out = defaultdict(lambda: defaultdict(lambda: {"gen": [], "prefill": []}))
    results = d.get("results", d if isinstance(d, dict) else {})
    for model, block in results.items():
        runs = block.get("runs", []) if isinstance(block, dict) else block
        for r in runs:
            if r.get("status") != "ok":
                continue
            ctx = r.get("ctx_target")
            g = r.get("gen_tok_s")
            p = r.get("prefill_tok_s")
            if g is not None:
                out[model][ctx]["gen"].append(g)
            if p is not None:
                out[model][ctx]["prefill"].append(p)
    return out


def load_old(path):
    """Returns {model: {ctx: {'gen': [t/s,...], 'prefill': [t/s,...]}}}.

    overnight schema: each row has raw=[{n_prompt, n_gen, avg_ts, ...}].
    A llama-bench invocation typically emits two raw rows: pp (n_gen=0)
    and tg (n_prompt=0). avg_ts is tokens/sec for that operation.
    """
    d = json.load(open(path))
    out = defaultdict(lambda: defaultdict(lambda: {"gen": [], "prefill": []}))
    for row in d:
        if row.get("status") != "ok":
            continue
        model = row.get("model")
        ctx = row.get("ctx_pp")
        for raw in row.get("raw") or []:
            ts = raw.get("avg_ts")
            if ts is None:
                continue
            n_prompt = raw.get("n_prompt", 0)
            n_gen = raw.get("n_gen", 0)
            if n_gen > 0 and n_prompt == 0:
                out[model][ctx]["gen"].append(ts)
            elif n_prompt > 0 and n_gen == 0:
                out[model][ctx]["prefill"].append(ts)
    return out


def median(lst):
    return statistics.median(lst) if lst else None


def fmt(v):
    return f"{v:6.2f}" if v is not None else "  -   "


def compare(new, old):
    rows = []
    models = sorted(set(new) & set(old))
    for m in sorted(set(new) | set(old)):
        if m not in models:
            tag = "NEW-ONLY" if m in new else "OLD-ONLY"
            print(f"  [{tag}] {m}")
    print()
    print(f"{'model':<24} {'ctx':>6}  {'new gen':>8} {'old gen':>8} {'Δ%':>7}  {'new pf':>8} {'old pf':>8} {'Δ%':>7}  flag")
    print("-" * 100)
    for m in models:
        new_ctxs = new[m]
        old_ctxs = old[m]
        common = sorted(set(new_ctxs) & set(old_ctxs))
        if not common:
            # report best-effort: show whatever ctx new has, compare to any old ctx if only one
            n_only = sorted(new_ctxs)
            o_only = sorted(old_ctxs)
            if len(o_only) == 1 and len(n_only) >= 1:
                # pair single old ctx against each new ctx for inspection
                for nc in n_only:
                    common.append(nc)
                    old_ctxs[nc] = old_ctxs[o_only[0]]
            else:
                print(f"  {m:<24} no overlapping ctx  (new={n_only}, old={o_only})")
                continue
        for ctx in common:
            ng = median(new[m][ctx]["gen"]); og = median(old[m][ctx]["gen"])
            npf = median(new[m][ctx]["prefill"]); opf = median(old[m][ctx]["prefill"])
            dg = (100*(ng-og)/og) if (ng and og) else None
            dp = (100*(npf-opf)/opf) if (npf and opf) else None
            flag = ""
            if dg is not None and abs(dg) > THRESHOLD_PCT: flag += "GEN!"
            if dp is not None and abs(dp) > THRESHOLD_PCT: flag += " PF!"
            dg_s = f"{dg:+6.1f}" if dg is not None else "   -  "
            dp_s = f"{dp:+6.1f}" if dp is not None else "   -  "
            rows.append((m, ctx, ng, og, dg, npf, opf, dp, flag))
            print(f"  {m:<22} {ctx:>6}  {fmt(ng)} {fmt(og)} {dg_s}   {fmt(npf)} {fmt(opf)} {dp_s}  {flag}")
    print()
    # Summary
    gen_deltas = [r[4] for r in rows if r[4] is not None]
    pf_deltas = [r[7] for r in rows if r[7] is not None]
    if gen_deltas:
        print(f"GEN delta: n={len(gen_deltas)}  median={statistics.median(gen_deltas):+.2f}%  "
              f"mean={statistics.mean(gen_deltas):+.2f}%  "
              f"|max|={max(abs(d) for d in gen_deltas):.2f}%  "
              f"flagged(>{THRESHOLD_PCT}%)={sum(1 for d in gen_deltas if abs(d)>THRESHOLD_PCT)}")
    if pf_deltas:
        print(f"PREFILL delta: n={len(pf_deltas)}  median={statistics.median(pf_deltas):+.2f}%  "
              f"mean={statistics.mean(pf_deltas):+.2f}%  "
              f"|max|={max(abs(d) for d in pf_deltas):.2f}%  "
              f"flagged(>{THRESHOLD_PCT}%)={sum(1 for d in pf_deltas if abs(d)>THRESHOLD_PCT)}")
    return rows


def main():
    new_path = Path(sys.argv[1]) if len(sys.argv) > 1 else NEW
    old_path = Path(sys.argv[2]) if len(sys.argv) > 2 else OLD
    if not new_path.exists():
        print(f"NEW results not found: {new_path}")
        print("(after orchestrate completes, rsync ~/phase-c-out/results/ -> benchmarks/results-phase-c-r2/)")
        sys.exit(1)
    if not old_path.exists():
        print(f"OLD baseline not found: {old_path}")
        sys.exit(1)
    print(f"NEW: {new_path}")
    print(f"OLD: {old_path}")
    print()
    new = load_new(new_path)
    old = load_old(old_path)
    compare(new, old)


if __name__ == "__main__":
    main()
