#!/usr/bin/env python3
"""Paired Pass A (24-CU stock) vs Pass B (40-CU patched) summary.
Both passes use bench-phase-c.py harness, ollama 0.20.0, identical scope.
"""
import json, os, statistics

DATA = os.path.dirname(os.path.abspath(__file__))
A = json.load(open(os.path.join(DATA, "passA", "step-1-perf.passA-stock24cu-20260603-1115.json")))
B = json.load(open(os.path.join(DATA, "passB", "step-1-perf.passB-patched40cu-20260604-0033.json")))

def medians(runs):
    g = [r["gen_tok_s"] for r in runs if r.get("status")=="ok" and r.get("gen_tok_s")]
    p = [r.get("prefill_tok_s") for r in runs if r.get("status")=="ok" and r.get("prefill_tok_s")]
    if not g: return None, None, None
    cv = (statistics.pstdev(g)/statistics.mean(g)*100) if len(g)>1 else 0.0
    return statistics.median(g), (statistics.median(p) if p else None), cv

def status(runs):
    s = [r.get("status") for r in runs]
    if not s: return "no-runs"
    ok = s.count("ok")
    if ok == len(s): return "ok({}/{})".format(ok, len(s))
    bad = sorted(set(x for x in s if x != "ok"))
    return ",".join(bad) + "({}/{})".format(ok, len(s))

names = list(A["results"].keys())  # canonical order from A
print("=" * 96)
print("Paired perf: Pass A (24-CU stock) vs Pass B (40-CU patched), ollama 0.20.0, n=3")
print("=" * 96)
hdr = "{:34s} {:>9s} {:>9s} {:>10s} {:>9s} {:>9s} {:>10s}".format(
    "model", "A:gen", "B:gen", "delta_gen%", "A:pp", "B:pp", "delta_pp%")
print(hdr); print("-" * 96)

deltas_gen = []
deltas_pp = []
for name in names:
    arec = A["results"].get(name, {})
    brec = B["results"].get(name, {})
    ag, ap, _ = medians(arec.get("runs", []))
    bg, bp, _ = medians(brec.get("runs", []))
    line = "{:34s}".format(name)
    line += " {:>9s}".format(("%.2f" % ag) if ag else "-")
    line += " {:>9s}".format(("%.2f" % bg) if bg else "-")
    if ag and bg:
        d = (bg/ag - 1) * 100
        deltas_gen.append((name, d))
        line += " {:>+9.1f}%".format(d)
    else:
        astatus = status(arec.get("runs", []))
        bstatus = status(brec.get("runs", []))
        line += " {:>10s}".format("[{}|{}]".format(astatus.split("(")[0], bstatus.split("(")[0]))
    line += " {:>9s}".format(("%.1f" % ap) if ap else "-")
    line += " {:>9s}".format(("%.1f" % bp) if bp else "-")
    if ap and bp:
        dp = (bp/ap - 1) * 100
        deltas_pp.append((name, dp))
        line += " {:>+9.1f}%".format(dp)
    else:
        line += " {:>10s}".format("-")
    print(line)

if deltas_gen:
    vals = [d for _, d in deltas_gen]
    print("-" * 96)
    print("gen-throughput speedup from 40-CU patch (n={}):  median {:+.1f}%  mean {:+.1f}%  min {:+.1f}%  max {:+.1f}%".format(
        len(vals), statistics.median(vals), statistics.mean(vals), min(vals), max(vals)))
if deltas_pp:
    vals = [d for _, d in deltas_pp]
    print("prefill speedup from 40-CU patch (n={}):         median {:+.1f}%  mean {:+.1f}%  min {:+.1f}%  max {:+.1f}%".format(
        len(vals), statistics.median(vals), statistics.mean(vals), min(vals), max(vals)))
