#!/usr/bin/env python3
"""Summarise results-prefer-host-mem.json into a comparison table."""
import json, sys
from collections import defaultdict

path = sys.argv[1] if len(sys.argv) > 1 else "results-prefer-host-mem.json"
data = json.load(open(path))

# group: (build, model, ctx) -> {env: (pp, tg, tg_stddev)}
g = defaultdict(dict)
for r in data:
    raw = r.get("raw")
    if not isinstance(raw, list):
        g[(r["build"], r["model"], r["ctx_pp"])][r["env"]] = (None, None, None)
        continue
    pp = next((x for x in raw if x.get("n_prompt", 0) > 0 and x.get("n_gen", 0) == 0), None)
    tg = next((x for x in raw if x.get("n_gen", 0) > 0 and x.get("n_prompt", 0) == 0), None)
    g[(r["build"], r["model"], r["ctx_pp"])][r["env"]] = (
        round(pp["avg_ts"], 2) if pp else None,
        round(tg["avg_ts"], 2) if tg else None,
        round(tg["stddev_ts"], 3) if tg else None,
    )

print(f"{'model':<22} {'build':<6} {'ctx':>5}  "
      f"{'pp_def':>8} {'pp_phm':>8} {'Δpp%':>6}   "
      f"{'tg_def':>7} {'tg_phm':>7} {'Δtg%':>6}")
print("-" * 96)

for key in sorted(g, key=lambda k: (k[1], k[2], k[0])):
    v = g[key]
    b, m, c = key
    pp_d, tg_d, _ = v.get("default",     (None, None, None))
    pp_p, tg_p, _ = v.get("prefer_host", (None, None, None))
    dpp = (pp_p - pp_d) / pp_d * 100 if pp_d and pp_p else 0
    dtg = (tg_p - tg_d) / tg_d * 100 if tg_d and tg_p else 0
    print(f"{m:<22} {b:<6} {c:>5}  "
          f"{pp_d if pp_d is not None else '-':>8} "
          f"{pp_p if pp_p is not None else '-':>8} {dpp:>+6.1f}   "
          f"{tg_d if tg_d is not None else '-':>7} "
          f"{tg_p if tg_p is not None else '-':>7} {dtg:>+6.1f}")
