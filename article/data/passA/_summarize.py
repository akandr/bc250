#!/usr/bin/env python3
import json, os, statistics

DATA = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(DATA)

new = json.load(open(os.path.join(DATA, "step-1-perf.passA-stock24cu-20260603-1115.json")))

old = None; old_label = None
for cand in ["step-1-perf.40cu-canonical.json", "perf-40cu.summary.json", "step-1-perf.preW1-1780158050.json"]:
    p = os.path.join(PARENT, cand)
    if os.path.exists(p):
        old = json.load(open(p)); old_label = cand; break

def medians(runs):
    g = [r["gen_tok_s"] for r in runs if r.get("status")=="ok" and r.get("gen_tok_s")]
    p = [r.get("prefill_tok_s") for r in runs if r.get("status")=="ok" and r.get("prefill_tok_s")]
    if not g: return None, None, None
    gm = statistics.median(g)
    cv = (statistics.pstdev(g)/statistics.mean(g)*100) if len(g)>1 else 0.0
    pm = statistics.median(p) if p else None
    return gm, pm, cv

def status_summary(runs):
    if not runs: return "no-runs", None
    statuses = [r.get("status") for r in runs]
    ok = statuses.count("ok")
    if ok == len(runs): return "ok({}/{})".format(ok, len(runs)), None
    bad = sorted(set(s for s in statuses if s != "ok"))
    reasons = []
    for r in runs:
        if r.get("status") != "ok":
            reason = r.get("reason") or r.get("skip_reason") or r.get("error") or r.get("note")
            if reason: reasons.append(str(reason)[:60])
    return ",".join(bad) + "({}/{})".format(ok, len(runs)), reasons[0] if reasons else None

print("=== Pass A perf (stock 24-CU, ollama 0.20.0) ===")
print("{:34s} {:>8s} {:>7s} {:>5s}  {:24s}  reason".format("model","gen_med","pp_med","cv%","status"))
print("-"*100)
rows = {}
for name, rec in new["results"].items():
    runs = rec.get("runs", [])
    gm, pm, cv = medians(runs)
    st, reason = status_summary(runs)
    rows[name] = (gm, pm, cv, st)
    print("{:34s} {:>8s} {:>7s} {:>5s}  {:24s}  {}".format(
        name,
        ("{:.2f}".format(gm)) if gm else "-",
        ("{:.1f}".format(pm)) if pm else "-",
        ("{:.2f}".format(cv)) if cv else "-",
        st,
        reason or ""
    ))

if old:
    print()
    print("=== Delta vs prior canonical ({}) - gen_median t/s ===".format(old_label))
    print("{:34s} {:>8s} {:>8s} {:>9s}".format("model","passA","prior","delta%"))
    print("-"*64)
    for name, (g,_,_,_) in rows.items():
        if not g: continue
        orec = old.get("results",{}).get(name)
        if not orec: continue
        oruns = orec.get("runs",[])
        og = None
        if oruns:
            og,_,_ = medians(oruns)
        if og is None:
            og = (orec.get("aggregate") or {}).get("gen_median")
        if og:
            print("{:34s} {:8.2f} {:8.2f} {:+8.1f}%".format(name, g, og, (g/og-1)*100))
else:
    print()
    print("(no prior canonical file found in article/data/)")
