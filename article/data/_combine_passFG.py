#!/usr/bin/env python3
"""Combine Pass F + Pass G ctx-quality data into Table 5/Table 9 inputs.

For each (model, tier):
  - collect runs from Pass F (typically n=2) and Pass G (n=1)
  - report n_total, gen_median, pp_median, gen_cv, needle pass count,
    and overall status (ok|partial|timeout|skip|absent).

Honest about:
  - which cells have <3 runs and why
  - which cells had any timeout/skip/http_error
  - exact n per cell (no fudging the small-n cells up to 3)
"""
import json, os, statistics
from pathlib import Path

DATA = Path(__file__).resolve().parent
PASSF_T5 = DATA / "passF/step-ctx-quality-table5.passF-stock24cu-20260604-1910.json"
PASSF_T9 = DATA / "passF/step-ctx-quality-table9-qwq.passF-stock24cu-20260604-1910.json"
PASSG_T5 = DATA / "passG/step-ctx-quality-table5-r3.passG-stock24cu-20260605-0921.json"
PASSG_T9 = DATA / "passG/step-ctx-quality-table9-qwq-r3.passG-stock24cu-20260605-0921.json"


def collect_runs(path):
    """Returns {model: {tier: [run_dict, ...]}} merged."""
    if not path.exists():
        print(f"WARN missing: {path}")
        return {}
    d = json.load(open(path))
    out = {}
    for name, rec in d["results"].items():
        out[name] = {}
        for tier_str, t in rec.get("tiers", {}).items():
            out[name][int(tier_str)] = t.get("runs", [])
    return out


def merge(pf, pg):
    out = {}
    for m in set(list(pf.keys()) + list(pg.keys())):
        out[m] = {}
        f_tiers = pf.get(m, {})
        g_tiers = pg.get(m, {})
        for t in set(list(f_tiers.keys()) + list(g_tiers.keys())):
            out[m][t] = list(f_tiers.get(t, [])) + list(g_tiers.get(t, []))
    return out


def cell_summary(runs):
    n = len(runs)
    statuses = [r.get("status") for r in runs]
    ok_runs = [r for r in runs if r.get("status") == "ok"]
    n_ok = len(ok_runs)
    gens = [r["gen_tok_s"] for r in ok_runs if r.get("gen_tok_s") is not None]
    pps = [r["prefill_tok_s"] for r in ok_runs if r.get("prefill_tok_s") is not None]
    needle_pass = sum(1 for r in ok_runs if r.get("needle_pass") is True)
    needle_total = n_ok  # only ok runs count toward needle denominator
    failed_statuses = [s for s in statuses if s != "ok"]

    if n_ok == 0:
        result_status = failed_statuses[0] if failed_statuses else "absent"
    elif n_ok < n:
        result_status = f"partial:{n_ok}/{n}"
    else:
        result_status = "ok"

    out = {
        "n_total": n,
        "n_ok": n_ok,
        "status": result_status,
        "fail_reasons": failed_statuses,
        "gen_median": statistics.median(gens) if gens else None,
        "gen_cv_pct": (statistics.pstdev(gens) / statistics.mean(gens) * 100) if len(gens) >= 2 else None,
        "pp_median": statistics.median(pps) if pps else None,
        "needle_pass": needle_pass,
        "needle_total": needle_total,
    }
    return out


def fmt_cell(c):
    if c["n_ok"] == 0:
        # all-failed cell
        if "timeout" in c["fail_reasons"]:
            return "timeout"
        if "skip" in c["fail_reasons"]:
            return "skip"
        if "http_error" in c["fail_reasons"]:
            return "error"
        return "—"
    g = c["gen_median"]
    n_ok = c["n_ok"]
    np_ = c["needle_pass"]
    nt = c["needle_total"]
    return f"{g:.1f} t/s  needle={np_}/{nt}  n={n_ok}"


def main():
    pf5 = collect_runs(PASSF_T5)
    pg5 = collect_runs(PASSG_T5)
    pf9 = collect_runs(PASSF_T9)
    pg9 = collect_runs(PASSG_T9)

    m5 = merge(pf5, pg5)
    m9 = merge(pf9, pg9)

    print("=" * 100)
    print("TABLE 5: context scaling (Pass F n=2 + Pass G n=1, target n=3)")
    print("=" * 100)
    T5_TIERS = [4096, 16384, 32768, 65536]
    print(f"  {'model':32s}  " + "  ".join(f"{t:>22s}" for t in ["4K", "16K", "32K", "64K"]))
    print("-" * 130)
    for name, tiers in m5.items():
        cells = {t: cell_summary(tiers.get(t, [])) for t in T5_TIERS}
        row = "  ".join(f"{fmt_cell(cells[t]):>22s}" for t in T5_TIERS)
        print(f"  {name:32s}  {row}")

    print()
    print("=" * 100)
    print("TABLE 9: qwq-32b (Pass F n=2 + Pass G n=1, target n=3)")
    print("=" * 100)
    T9_TIERS = [4096, 8192, 16384]
    print(f"  {'model':32s}  " + "  ".join(f"{t:>22s}" for t in ["4K", "8K", "16K"]))
    print("-" * 110)
    for name, tiers in m9.items():
        cells = {t: cell_summary(tiers.get(t, [])) for t in T9_TIERS}
        row = "  ".join(f"{fmt_cell(cells[t]):>22s}" for t in T9_TIERS)
        print(f"  {name:32s}  {row}")

    # Detailed per-cell breakdown for paper
    print()
    print("=" * 100)
    print("DETAILED CELLS for tex (gen_median, gen_cv%, pp_median, needle, n_ok/n_total, fails)")
    print("=" * 100)
    for name, tiers in sorted(m5.items()):
        for t in T5_TIERS:
            c = cell_summary(tiers.get(t, []))
            cv = f"{c['gen_cv_pct']:.2f}%" if c['gen_cv_pct'] is not None else '---'
            gen = f"{c['gen_median']:.2f}" if c['gen_median'] else '---'
            pp = f"{c['pp_median']:.1f}" if c['pp_median'] else '---'
            print(f"  T5  {name:32s}  {t:>6}  status={c['status']:14s}  "
                  f"gen={gen:>6}  cv={cv:>7}  pp={pp:>6}  "
                  f"needle={c['needle_pass']}/{c['needle_total']}  "
                  f"fails={c['fail_reasons']}")
    for name, tiers in sorted(m9.items()):
        for t in T9_TIERS:
            c = cell_summary(tiers.get(t, []))
            cv = f"{c['gen_cv_pct']:.2f}%" if c['gen_cv_pct'] is not None else '---'
            gen = f"{c['gen_median']:.2f}" if c['gen_median'] else '---'
            pp = f"{c['pp_median']:.1f}" if c['pp_median'] else '---'
            print(f"  T9  {name:32s}  {t:>6}  status={c['status']:14s}  "
                  f"gen={gen:>6}  cv={cv:>7}  pp={pp:>6}  "
                  f"needle={c['needle_pass']}/{c['needle_total']}  "
                  f"fails={c['fail_reasons']}")

    # Save canonical merged output
    out = {"table5": {}, "table9": {}}
    for name, tiers in m5.items():
        out["table5"][name] = {t: cell_summary(tiers.get(t, [])) for t in T5_TIERS}
    for name, tiers in m9.items():
        out["table9"][name] = {t: cell_summary(tiers.get(t, [])) for t in T9_TIERS}
    canonical = DATA / "_canonical_table5_9.json"
    json.dump(out, open(canonical, "w"), indent=2, default=str)
    print(f"\nCanonical merged table written to {canonical}")


if __name__ == "__main__":
    main()
