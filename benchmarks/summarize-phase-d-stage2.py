#!/usr/bin/env python3
"""Phase-D Stage-2 summariser — 40-CU filled-context ladder + G3 24-CU comparison.

Produces three tables:
  1. 40-CU long-context ladder (gen tok/s, prefill, needle) per tier.
  2. G3 per-tier Δ: paired 24-CU (G3 fresh oberon-capped run) vs 40-CU, showing
     Δgen and Δpre across the full 4K–64K span.  Both arms ran under the same
     oberon 1500 MHz cap, making this the clock-matched comparison for the article.
  3. Telemetry (sclk mean, edge temp, throttle) for the 40-CU arm.

Usage:
    ./summarize-phase-d-stage2.py [path-to-40cu-stage2.json] [path-to-24cu-g3.json]
"""
from __future__ import annotations
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).parent
DEFAULT_40CU = HERE / "results-phase-d-stage2" / "step-2-ctx-quality.40cu.json"
DEFAULT_24CU_G3 = HERE / "results-phase-d-24cu-ladder" / "step-2-ctx-quality.24cu.json"
PAIRED_16K_24 = HERE / "results-phase-c-r2" / "cu-paired-filled" / "16k" / "24cu" / "step-1-perf.json"
PAIRED_16K_40 = HERE / "results-phase-c-r2" / "cu-paired-filled" / "16k" / "40cu" / "step-1-perf.json"

TIERS = [4096, 8192, 16384, 24576, 32768, 49152, 65536]
G3_MODELS = ["granite-4.0-h-tiny", "qwen3.5-9b-ollama", "gemma4-latest", "deepseek-r1-14b"]


def _ov_agg(runs):
    """Aggregate gpu_overlay telemetry across the ok runs of a cell."""
    ov = [r.get("gpu_overlay", {}) for r in (runs or []) if r.get("status") == "ok"]
    sm = [o["sclk_mhz_mean"] for o in ov if o.get("sclk_mhz_mean")]
    tm = [o["temp_c_max"] for o in ov if o.get("temp_c_max")]
    pw = [o["power_mw_mean"] for o in ov if o.get("power_mw_mean")]
    throttle = any(o.get("throttle_flag") for o in ov)
    return {
        "sclk_mean": round(statistics.mean(sm)) if sm else None,
        "sclk_max": max((o.get("sclk_mhz_max", 0) for o in ov), default=None),
        "temp_max": max(tm) if tm else None,
        "power_w": round(statistics.mean(pw) / 1000) if pw else None,
        "throttle": throttle,
    }


def load_ladder(path: Path) -> dict[tuple[str, int], dict]:
    """{(model, tier): {gen, pp, cv, needle, status, telem}} from a ctx-quality JSON."""
    out: dict[tuple[str, int], dict] = {}
    if not path.exists():
        return out
    raw = json.loads(path.read_text())
    for m, rec in raw.get("results", {}).items():
        for tier_s, t in (rec.get("tiers") or {}).items():
            try:
                tier = int(tier_s)
            except ValueError:
                continue
            agg = t.get("aggregate") or {}
            verify = t.get("verify") or {}
            runs = t.get("runs") or []
            run0 = runs[0] if runs else {}
            out[(m, tier)] = {
                "gen": agg.get("gen_tok_s_median"),
                "pp": agg.get("prefill_tok_s_median"),
                "cv": agg.get("gen_tok_s_cv_pct"),
                "needle": (verify.get("pass"), verify.get("total")),
                "status": run0.get("status"),
                "telem": _ov_agg(runs),
            }
    return out


def load_flat(path: Path) -> dict[str, dict]:
    """Single-tier paired files: {model: {gen, pp}}."""
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    raw = json.loads(path.read_text())
    for m, rec in raw.get("results", raw).items():
        runs = rec.get("runs")
        agg = rec.get("aggregate")
        if runs is None and "tiers" in rec:
            t = next(iter(rec["tiers"].values()))
            runs, agg = t.get("runs"), t.get("aggregate")
        agg = agg or {}
        if agg.get("gen_tok_s_median") is not None:
            out[m] = {"gen": agg["gen_tok_s_median"], "pp": agg.get("prefill_tok_s_median")}
    return out


def f(x, d=1):
    return "—" if x is None else (f"{x:.{d}f}" if isinstance(x, float) else str(x))


def kt(t):
    return f"{t // 1024}K" if t % 1024 == 0 else str(t)


def main():
    path40 = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_40CU
    path24 = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_24CU_G3
    lad = load_ladder(path40)
    lad24 = load_ladder(path24)
    models = sorted({m for (m, _) in lad})

    print(f"# 40-CU filled-context ladder  (source: {path40.name})\n")
    print("## Throughput + needle per tier (gen tok/s, needle X/N)\n")
    head = "| Model | " + " | ".join(kt(t) for t in TIERS) + " |"
    print(head)
    print("|" + "---|" * (len(TIERS) + 1))
    for m in models:
        cells = []
        for t in TIERS:
            r = lad.get((m, t))
            if not r:
                cells.append("·")
                continue
            g = r["gen"]
            if g is None:
                cells.append({"fail": "OOM/fail", "timeout": "timeout"}.get(r["status"], r["status"] or "—"))
            else:
                p, n = r["needle"]
                cells.append(f"{g:.1f} ({p}/{n})" if p is not None else f"{g:.1f}")
        print(f"| {m} | " + " | ".join(cells) + " |")

    print("\n## Prefill tok/s per tier\n")
    print(head)
    print("|" + "---|" * (len(TIERS) + 1))
    for m in models:
        cells = [f(lad.get((m, t), {}).get("pp")) for t in TIERS]
        print(f"| {m} | " + " | ".join(cells) + " |")

    print("\n## Operating point per tier (sclk_mean MHz / edge_max °C / throttle)\n")
    print(head)
    print("|" + "---|" * (len(TIERS) + 1))
    for m in models:
        cells = []
        for t in TIERS:
            r = lad.get((m, t))
            if not r or r["gen"] is None:
                cells.append("·")
                continue
            tl = r["telem"]
            thr = "T" if tl["throttle"] else "-"
            cells.append(f"{f(tl['sclk_mean'])}/{f(tl['temp_max'])}/{thr}")
        print(f"| {m} | " + " | ".join(cells) + " |")

    # G3: Per-tier 24-CU vs 40-CU clock-matched comparison
    if lad24:
        print("\n# G3: Clock-matched 24-CU vs 40-CU per-tier Δ\n")
        print("Both arms ran under the oberon 1500 MHz cap (throttle=True in GPU overlay).\n")
        g3_models = [m for m in G3_MODELS if any((m, t) in lad or (m, t) in lad24 for t in TIERS)]

        print("## Δgen per tier (40-CU gen / 24-CU gen)\n")
        head = "| Model | " + " | ".join(kt(t) for t in TIERS) + " | Median |"
        print(head)
        print("|" + "---|" * (len(TIERS) + 2))
        all_dgs: list[float] = []
        for m in g3_models:
            cells = []
            model_dgs = []
            for t in TIERS:
                r24 = lad24.get((m, t))
                r40 = lad.get((m, t))
                if r24 and r24["gen"] and r40 and r40["gen"]:
                    dg = r40["gen"] / r24["gen"]
                    cells.append(f"{dg:.2f}×")
                    model_dgs.append(dg)
                    all_dgs.append(dg)
                else:
                    cells.append("·")
            med = f"{statistics.median(model_dgs):.2f}×" if model_dgs else "·"
            print(f"| {m} | " + " | ".join(cells) + f" | **{med}** |")
        if all_dgs:
            print(f"\n*Overall median Δgen = {statistics.median(all_dgs):.2f}×*")

        print("\n## Δpre per tier (40-CU prefill / 24-CU prefill)\n")
        print(head)
        print("|" + "---|" * (len(TIERS) + 2))
        all_dps: list[float] = []
        for m in g3_models:
            cells = []
            model_dps = []
            for t in TIERS:
                r24 = lad24.get((m, t))
                r40 = lad.get((m, t))
                if r24 and r24["pp"] and r40 and r40["pp"]:
                    dp = r40["pp"] / r24["pp"]
                    cells.append(f"{dp:.2f}×")
                    model_dps.append(dp)
                    all_dps.append(dp)
                else:
                    cells.append("·")
            med = f"{statistics.median(model_dps):.2f}×" if model_dps else "·"
            print(f"| {m} | " + " | ".join(cells) + f" | **{med}** |")
        if all_dps:
            print(f"\n*Overall median Δpre = {statistics.median(all_dps):.2f}×*")

        print("\n## 24-CU gen tok/s (absolute, for article table)\n")
        print(head)
        print("|" + "---|" * (len(TIERS) + 2))
        for m in g3_models:
            cells = []
            vals = []
            for t in TIERS:
                r24 = lad24.get((m, t))
                if r24 and r24["gen"]:
                    cells.append(f"{r24['gen']:.1f}")
                    vals.append(r24["gen"])
                else:
                    cells.append("·")
            print(f"| {m} | " + " | ".join(cells) + " | · |")

        print("\n## 24-CU sclk / temp per tier\n")
        print(head)
        print("|" + "---|" * (len(TIERS) + 2))
        for m in g3_models:
            cells = []
            for t in TIERS:
                r24 = lad24.get((m, t))
                if not r24 or r24["gen"] is None:
                    cells.append("·")
                    continue
                tl = r24["telem"]
                thr = "T" if tl["throttle"] else "-"
                cells.append(f"{f(tl['sclk_mean'])}/{f(tl['temp_max'])}/{thr}")
            print(f"| {m} | " + " | ".join(cells) + " | · |")
    else:
        print("\n# G3: 24-CU ladder not yet available (run the fresh 7-tier sweep first)\n")

    # Legacy: clean clock-matched 16K Δ spot-check (kept for reference)
    a, b = load_flat(PAIRED_16K_24), load_flat(PAIRED_16K_40)
    common = sorted(set(a) & set(b))
    if common:
        print("\n# Legacy: paired 16K Δ (pre-oberon, confounded clock conditions)\n")
        print("Note: this comparison used non-oberon 24-CU arm — Δ is inflated.\n")
        print("| Model | gen 24 | gen 40 | Δgen | pp 24 | pp 40 | Δpp |")
        print("|---|---:|---:|---:|---:|---:|---:|")
        dgs, dps = [], []
        for m in common:
            g24, g40 = a[m]["gen"], b[m]["gen"]
            p24, p40 = a[m]["pp"], b[m]["pp"]
            dg = g40 / g24 if g24 else None
            dp = p40 / p24 if (p24 and p40) else None
            if dg:
                dgs.append(dg)
            if dp:
                dps.append(dp)
            print(f"| {m} | {f(g24)} | {f(g40)} | {f(dg,2)}× | {f(p24)} | {f(p40)} | "
                  f"{(f(dp,2)+'×') if dp else '—'} |")
        if dgs:
            print(f"\n*Inflated Δgen@16K = {statistics.median(dgs):.2f}× (pre-oberon 24-CU arm)*")


if __name__ == "__main__":
    main()
