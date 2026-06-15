#!/usr/bin/env python3
"""summarize-overnight.py — render the overnight bench JSON as readable tables."""
import json, sys
from collections import defaultdict

path = sys.argv[1] if len(sys.argv) > 1 else "results-overnight-20260516.json"
data = json.load(open(path))

# (build, model, env, ctx) -> (pp_avg, tg_avg, tg_sd, status)
flat = {}
for r in data:
    key = (r["build"], r["model"], r["env"], r["ctx_pp"])
    raw = r.get("raw")
    if not isinstance(raw, list):
        flat[key] = (None, None, None, r.get("status", "?"))
        continue
    pp = next((x for x in raw if x.get("n_prompt", 0) > 0 and x.get("n_gen", 0) == 0), None)
    tg = next((x for x in raw if x.get("n_gen", 0) > 0 and x.get("n_prompt", 0) == 0), None)
    flat[key] = (
        round(pp["avg_ts"], 2) if pp else None,
        round(tg["avg_ts"], 2) if tg else None,
        round(tg["stddev_ts"], 2) if tg else None,
        "ok",
    )

models = sorted({k[1] for k in flat})
builds = sorted({k[0] for k in flat})
envs   = sorted({k[2] for k in flat})
ctxs   = sorted({k[3] for k in flat})

print("=" * 100)
print("PART 1 — TG (token generation) tok/s by model × build × env × ctx")
print("=" * 100)
hdr = f"{'model':<24} {'build':<6} {'env':<11}  " + " ".join(f"{c:>8}" for c in ctxs)
print(hdr)
print("-" * len(hdr))
for m in models:
    for b in builds:
        for e in envs:
            row = f"{m:<24} {b:<6} {e:<11}  "
            for c in ctxs:
                v = flat.get((b, m, e, c))
                if v is None:
                    cell = "-"
                elif v[3] != "ok":
                    cell = v[3][:7]
                elif v[1] is None:
                    cell = "?"
                else:
                    cell = f"{v[1]:>6.2f}"
                row += f"{cell:>8} "
            print(row)
        print()

print()
print("=" * 100)
print("PART 2 — PP (prompt processing) tok/s by model × build × env × ctx")
print("=" * 100)
print(hdr)
print("-" * len(hdr))
for m in models:
    for b in builds:
        for e in envs:
            row = f"{m:<24} {b:<6} {e:<11}  "
            for c in ctxs:
                v = flat.get((b, m, e, c))
                if v is None:
                    cell = "-"
                elif v[3] != "ok":
                    cell = v[3][:7]
                elif v[0] is None:
                    cell = "?"
                else:
                    cell = f"{v[0]:>6.1f}"
                row += f"{cell:>8} "
            print(row)
        print()

print()
print("=" * 100)
print("PART 3 — PREFER_HOST_MEMORY=1 effect (Δ%, prefer_host vs default)")
print("=" * 100)
print(f"{'model':<24} {'build':<6} {'ctx':>6}    {'Δtg %':>8}   {'Δpp %':>8}")
print("-" * 70)
for m in models:
    for b in builds:
        for c in ctxs:
            d = flat.get((b, m, "default", c))
            p = flat.get((b, m, "prefer_host", c))
            if not d or not p or d[3] != "ok" or p[3] != "ok":
                continue
            if d[1] is None or p[1] is None:
                continue
            dtg = (p[1] - d[1]) / d[1] * 100
            dpp = (p[0] - d[0]) / d[0] * 100 if d[0] and p[0] else 0
            print(f"{m:<24} {b:<6} {c:>6}    {dtg:>+7.1f}%   {dpp:>+7.1f}%")

print()
print("=" * 100)
print("PART 4 — b9165 vs b8200 effect (Δ%, b9165 vs b8200 at env=prefer_host)")
print("=" * 100)
print(f"{'model':<24} {'env':<11} {'ctx':>6}    {'Δtg %':>8}   {'Δpp %':>8}")
print("-" * 70)
for m in models:
    for e in envs:
        for c in ctxs:
            old = flat.get(("b8200", m, e, c))
            new = flat.get(("b9165", m, e, c))
            if not old or not new or old[3] != "ok" or new[3] != "ok":
                continue
            if old[1] is None or new[1] is None:
                continue
            dtg = (new[1] - old[1]) / old[1] * 100
            dpp = (new[0] - old[0]) / old[0] * 100 if old[0] and new[0] else 0
            print(f"{m:<24} {e:<11} {c:>6}    {dtg:>+7.1f}%   {dpp:>+7.1f}%")
