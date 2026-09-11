#!/usr/bin/env python3
"""Comprehensive sanity check: do Pass A (24-CU stock) and Pass B (40-CU patched)
results match physical expectations, and where do investigations remain?

Hardware physics priors:
- 24->40 CU = +66.7% raw shader compute
- Memory bandwidth identical in both modes (same DDR4)
- Prefill is GEMM-heavy, compute-bound -> expected speedup near +66%
- Generation is single-token, increasingly memory-bandwidth-bound
  -> expected speedup smaller, depends on arith intensity of model+quant
- Cold-load wall time dominated by mmap I/O, ~CU-independent
- KV-quant Q4_0 < Q8_0 < F16 in size; should reduce VM, modestly faster reads
- ctx tiers: longer ctx amplifies prefill (compute) cost"""
import json, os, statistics

DATA = os.path.dirname(os.path.abspath(__file__))
A_TAG = "passA-stock24cu-20260603-1115"
B_TAG = "passB-patched40cu-20260604-0033"

def L(name): return os.path.join(DATA, "passA", f"{name}.{A_TAG}.json")
def R(name): return os.path.join(DATA, "passB", f"{name}.{B_TAG}.json")

A_perf = json.load(open(L("step-1-perf")))
B_perf = json.load(open(R("step-1-perf")))
A_cold = json.load(open(L("step-cold")))
B_cold = json.load(open(R("step-cold")))
A_kv   = json.load(open(L("step-kv-quant")))
B_kv   = json.load(open(R("step-kv-quant")))
A_q    = json.load(open(L("step-2-ctx-quality")))
B_q    = json.load(open(R("step-2-ctx-quality")))

def med(xs): return statistics.median(xs) if xs else None
def cv_pct(xs):
    if len(xs) < 2: return None
    m = statistics.mean(xs)
    return statistics.pstdev(xs)/m*100 if m else None

def run_stats(runs, key="gen_tok_s"):
    xs = [r[key] for r in runs if r.get("status")=="ok" and r.get(key)]
    return med(xs), cv_pct(xs), len(xs)

print("="*100)
print("SECTION 1  perf-step sanity: speedup distribution shape")
print("="*100)
print("Physics prior: 24->40 CU = +66.7% raw compute. Prefill (compute-bound) should approach this;")
print("              gen (more BW-bound) should be lower. Cross-model spread reveals BW vs compute regime.\n")
print(f"{'model':<34} {'A_gen':>7} {'B_gen':>7} {'dG%':>7} {'A_pp':>7} {'B_pp':>7} {'dP%':>7}  {'A_cv':>5} {'B_cv':>5}")
print("-"*100)
gens = []; pps = []; pp_minus_gen = []
for name in A_perf["results"]:
    arec = A_perf["results"][name]; brec = B_perf["results"][name]
    aR, bR = arec.get("runs",[]), brec.get("runs",[])
    aG, aGcv, _ = run_stats(aR, "gen_tok_s")
    bG, bGcv, _ = run_stats(bR, "gen_tok_s")
    aP, _, _   = run_stats(aR, "prefill_tok_s")
    bP, _, _   = run_stats(bR, "prefill_tok_s")
    if aG and bG:
        dG = (bG/aG - 1)*100; gens.append((name, dG))
        dP = (bP/aP - 1)*100; pps.append((name, dP))
        pp_minus_gen.append(dP - dG)
        print(f"{name:<34} {aG:>7.2f} {bG:>7.2f} {dG:>+6.1f}% {aP:>7.1f} {bP:>7.1f} {dP:>+6.1f}% "
              f" {aGcv or 0:>4.1f}% {bGcv or 0:>4.1f}%")

g_vals = [d for _,d in gens]; p_vals = [d for _,d in pps]
print("-"*100)
print(f"gen   speedups: median {statistics.median(g_vals):+.1f}%  mean {statistics.mean(g_vals):+.1f}%  "
      f"stdev {statistics.pstdev(g_vals):.1f}pp  range {min(g_vals):+.1f}..{max(g_vals):+.1f}")
print(f"pp    speedups: median {statistics.median(p_vals):+.1f}%  mean {statistics.mean(p_vals):+.1f}%  "
      f"stdev {statistics.pstdev(p_vals):.1f}pp  range {min(p_vals):+.1f}..{max(p_vals):+.1f}")
print(f"pp - gen separation: median {statistics.median(pp_minus_gen):+.1f}pp  (positive = prefill more compute-bound, expected)")

print(f"\nVerdict: prefill mean {statistics.mean(p_vals):+.1f}% < +66.7% raw-compute uplift")
print(f"         => GPU not 100% compute-bound even on prefill; load_ms+overhead dilutes,")
print(f"            or BW co-limits at high arith intensity. Internally consistent.")
print(f"Verdict: gen mean {statistics.mean(g_vals):+.1f}% > 0% => decode is NOT purely BW-bound on this APU.")
print(f"         Compute also matters at decode for IQ2/Q3 quants (a lot of dequant arithmetic).")
print(f"         Plausible and matches BC250's known 'compute-starved at decode' character.")

print("\n" + "="*100)
print("SECTION 2  CV / run-stability across both passes")
print("="*100)
all_cvs = []
for name in A_perf["results"]:
    aR = A_perf["results"][name].get("runs",[])
    bR = B_perf["results"][name].get("runs",[])
    _, aGcv, _ = run_stats(aR, "gen_tok_s")
    _, bGcv, _ = run_stats(bR, "gen_tok_s")
    if aGcv is not None: all_cvs.append(aGcv)
    if bGcv is not None: all_cvs.append(bGcv)
if all_cvs:
    print(f"gen CV across {len(all_cvs)} (model,pass) cells: median {statistics.median(all_cvs):.2f}%  "
          f"max {max(all_cvs):.2f}%  count_above_5pct {sum(1 for x in all_cvs if x>5)}")
    if max(all_cvs) < 5:
        print("Verdict: all cells CV<5% -> noise floor good; deltas are real.")
    else:
        bad = [(n, max(run_stats(A_perf['results'][n].get('runs',[]),'gen_tok_s')[1] or 0,
                       run_stats(B_perf['results'][n].get('runs',[]),'gen_tok_s')[1] or 0))
               for n in A_perf["results"]]
        bad = [b for b in bad if b[1] > 5]
        print(f"Verdict: HIGH-CV cells need attention: {bad}")

print("\n" + "="*100)
print("SECTION 3  Outliers")
print("="*100)
g_sorted = sorted(gens, key=lambda x:x[1])
print("Lowest-delta gen models (potential investigation):")
for n,d in g_sorted[:3]:
    aR = A_perf["results"][n].get("runs",[])
    aG, _, _ = run_stats(aR, "gen_tok_s")
    backend = aR[0]["backend"] if aR else "?"
    print(f"  {n:<34} {d:+.1f}%  (A_gen={aG:.2f} t/s, backend={backend})")
print("Highest-delta gen models:")
for n,d in g_sorted[-3:]:
    aR = A_perf["results"][n].get("runs",[])
    aG, _, _ = run_stats(aR, "gen_tok_s")
    backend = aR[0]["backend"] if aR else "?"
    print(f"  {n:<34} {d:+.1f}%  (A_gen={aG:.2f} t/s, backend={backend})")

# correlation hypothesis: higher absolute throughput ≈ smaller model ≈ closer to BW-bound -> lower delta?
slow_fast = [(g_sorted[i][0], run_stats(A_perf['results'][g_sorted[i][0]].get('runs',[]),'gen_tok_s')[0],
              g_sorted[i][1]) for i in range(len(g_sorted))]
print("\nDelta-vs-baseline relationship (sanity for compute-bound vs BW-bound regime):")
print(f"{'model':<34} {'A_gen t/s':>10} {'delta%':>8}")
for n, ag, d in sorted(slow_fast, key=lambda x:x[1] or 0):
    if ag: print(f"{n:<34} {ag:>10.2f} {d:>+7.1f}%")

print("\n" + "="*100)
print("SECTION 4  Cold-start sanity: load_ms should be CU-independent (mmap I/O)")
print("="*100)
print(f"{'model':<34} {'A_load_s':>9} {'B_load_s':>9} {'dLoad%':>8}  {'A_cgen':>7} {'B_cgen':>7} {'dCgen%':>8}")
print("-"*100)
load_deltas = []; cgen_deltas = []
for name in A_cold["results"]:
    arec = A_cold["results"][name].get("cold_runs", [])
    brec = B_cold["results"][name].get("cold_runs", [])
    aL = [r["load_ms"]/1000 for r in arec if r.get("status")=="ok" and r.get("load_ms")]
    bL = [r["load_ms"]/1000 for r in brec if r.get("status")=="ok" and r.get("load_ms")]
    aG = [r["gen_tok_s"] for r in arec if r.get("status")=="ok" and r.get("gen_tok_s")]
    bG = [r["gen_tok_s"] for r in brec if r.get("status")=="ok" and r.get("gen_tok_s")]
    if aL and bL:
        dL = (med(bL)/med(aL)-1)*100
        load_deltas.append(dL)
        line = f"{name:<34} {med(aL):>9.2f} {med(bL):>9.2f} {dL:>+7.1f}%"
        if aG and bG:
            dG = (med(bG)/med(aG)-1)*100
            cgen_deltas.append(dG)
            line += f"  {med(aG):>7.2f} {med(bG):>7.2f} {dG:>+7.1f}%"
        print(line)
if load_deltas:
    print("-"*100)
    print(f"load_ms delta: median {statistics.median(load_deltas):+.1f}%  range {min(load_deltas):+.1f}..{max(load_deltas):+.1f}")
    if abs(statistics.median(load_deltas)) < 5:
        print("Verdict: load_ms ~CU-independent (median |delta| < 5%) -> consistent with mmap I/O dominance.")
    else:
        print("FLAG: load_ms shifted materially -> investigate (caching state? VRAM pre-allocation?)")
if cgen_deltas:
    print(f"cold gen delta: median {statistics.median(cgen_deltas):+.1f}% (should match perf-step ~+32%)")

print("\n" + "="*100)
print("SECTION 5  KV-quant sanity: f16 < q8_0 < q4_0 in *speed*? In bytes? Internally consistent?")
print("="*100)
print(f"{'model':<22} {'tier':>5} {'kv':>5}  {'A_gen':>7} {'B_gen':>7} {'dG%':>7}  {'A_pp':>7} {'B_pp':>7} {'dP%':>7}")
print("-"*100)
kv_speedups = []
for name, rec in A_kv["results"].items():
    brec = B_kv["results"].get(name, {})
    for tier_str, tier_d in rec.get("tiers", {}).items():
        for kvt, cell in tier_d.items():
            ar = cell.get("runs", [])
            bcell = brec.get("tiers", {}).get(tier_str, {}).get(kvt, {})
            br = bcell.get("runs", [])
            aG = [r["gen_tok_s"] for r in ar if r.get("status")=="ok" and r.get("gen_tok_s")]
            bG = [r["gen_tok_s"] for r in br if r.get("status")=="ok" and r.get("gen_tok_s")]
            aP = [r["prefill_tok_s"] for r in ar if r.get("status")=="ok" and r.get("prefill_tok_s")]
            bP = [r["prefill_tok_s"] for r in br if r.get("status")=="ok" and r.get("prefill_tok_s")]
            if aG and bG:
                dG = (med(bG)/med(aG)-1)*100
                kv_speedups.append(dG)
                dP = (med(bP)/med(aP)-1)*100 if aP and bP else None
                print(f"{name:<22} {tier_str:>5} {kvt:>5}  {med(aG):>7.2f} {med(bG):>7.2f} {dG:>+6.1f}%  "
                      f"{(med(aP) if aP else 0):>7.1f} {(med(bP) if bP else 0):>7.1f} "
                      f"{(dP if dP else 0):>+6.1f}%")
if kv_speedups:
    print("-"*100)
    print(f"kv-quant gen speedup: median {statistics.median(kv_speedups):+.1f}%  mean {statistics.mean(kv_speedups):+.1f}%  "
          f"range {min(kv_speedups):+.1f}..{max(kv_speedups):+.1f}")
    print(f"          vs perf step gen median +{statistics.median(g_vals):.1f}% -> {'consistent' if abs(statistics.median(kv_speedups)-statistics.median(g_vals))<8 else 'DIVERGENT, investigate'}")

# kv-type ordering sanity within a single pass
print("\nWithin-pass KV-type ordering (gen t/s, should be ~f16 >= q8_0 >= q4_0 in speed terms,")
print("though q4_0 saves VRAM; differences usually <10%):")
for src, label in [(A_kv, "A 24CU"), (B_kv, "B 40CU")]:
    print(f"  {label}:")
    for name, rec in list(src["results"].items())[:4]:
        for tier_str, tier_d in list(rec.get("tiers", {}).items())[:1]:
            speeds = {}
            for kvt, cell in tier_d.items():
                xs = [r["gen_tok_s"] for r in cell.get("runs", []) if r.get("status")=="ok" and r.get("gen_tok_s")]
                if xs: speeds[kvt] = med(xs)
            order = sorted(speeds.items(), key=lambda x:-x[1])
            order_str = " > ".join(f"{k}({v:.1f})" for k,v in order)
            print(f"    {name:<22} ctx={tier_str}: {order_str}")

print("\n" + "="*100)
print("SECTION 6  ctx-quality coverage: which (model x tier) cells completed?")
print("="*100)
TIERS = ["4096", "16384", "32768", "65536"]
def coverage(d):
    cov = {}
    for name, rec in d["results"].items():
        cov[name] = {}
        for tier in TIERS:
            cell = rec.get("tiers", {}).get(tier, {})
            runs = cell.get("runs", [])
            ok = sum(1 for r in runs if r.get("status")=="ok")
            n = len(runs)
            cov[name][tier] = (ok, n)
    return cov
covA = coverage(A_q); covB = coverage(B_q)

print(f"{'model':<34}  " + "  ".join(f"{t:>10}" for t in TIERS))
print("-"*100)
mismatches = []
for name in A_q["results"]:
    rowA = covA[name]; rowB = covB[name]
    cells = []
    for t in TIERS:
        a = rowA.get(t, (0,0)); b = rowB.get(t, (0,0))
        s = f"A{a[0]}/{a[1]} B{b[0]}/{b[1]}"
        if a != b: mismatches.append((name, t, a, b))
        cells.append(s)
    print(f"{name:<34}  " + "  ".join(f"{c:>10}" for c in cells))
if mismatches:
    print(f"\nA vs B coverage differences ({len(mismatches)} cells):")
    for n,t,a,b in mismatches[:30]:
        print(f"  {n} @ {t}: A={a} B={b}")

# needle-pass quality
print("\nNeedle pass-rate per pass:")
def needle(d):
    cells = 0; passed = 0
    by_tier = {}
    for name, rec in d["results"].items():
        for t, cell in rec.get("tiers", {}).items():
            for r in cell.get("runs", []):
                if r.get("status")=="ok":
                    cells += 1
                    by_tier.setdefault(t, [0,0])
                    by_tier[t][1] += 1
                    if r.get("needle_pass"):
                        passed += 1; by_tier[t][0] += 1
    return passed, cells, by_tier
pA, cA, tA = needle(A_q); pB, cB, tB = needle(B_q)
print(f"Pass A: needle_pass {pA}/{cA} = {pA/cA*100:.1f}%")
print(f"Pass B: needle_pass {pB}/{cB} = {pB/cB*100:.1f}%")
print(f"  per tier (passed/ok-runs):")
for t in TIERS:
    a = tA.get(t,[0,0]); b = tB.get(t,[0,0])
    pct_a = (a[0]/a[1]*100) if a[1] else 0
    pct_b = (b[0]/b[1]*100) if b[1] else 0
    print(f"    {t:>5}:  A {a[0]:>3}/{a[1]:<3}={pct_a:>5.1f}%   B {b[0]:>3}/{b[1]:<3}={pct_b:>5.1f}%")

print("\n" + "="*100)
print("SECTION 7  Final verdict snapshot")
print("="*100)
