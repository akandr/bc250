#!/usr/bin/env python3
"""G3 integrator — pair 24-CU and 40-CU long-context ladders for article §5.7.

This script loads the new 24-CU long-context ladder (G3 results) and the
existing 40-CU ladder, then produces a unified summary showing both arms
and the per-tier CU unlock speedup factor (Δ).

Usage:
    ./summarize-g3-24cu-vs-40cu.py [path-to-24cu.json] [path-to-40cu.json]
"""
from __future__ import annotations
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).parent
DEFAULT_24CU = HERE / "results-phase-d-24cu-ladder" / "step-2-ctx-quality.24cu.json"
DEFAULT_40CU = HERE / "results-phase-d-stage2" / "step-2-ctx-quality.40cu.json"

TIERS = [4096, 8192, 16384, 24576, 32768, 49152, 65536]
# G3 models (the 4 scaling models)
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


def f(x, d=1):
    return "—" if x is None else (f"{x:.{d}f}" if isinstance(x, float) else str(x))


def kt(t):
    return f"{t // 1024}K" if t % 1024 == 0 else str(t)


def main():
    path24 = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_24CU
    path40 = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_40CU

    lad24 = load_ladder(path24)
    lad40 = load_ladder(path40)

    models = sorted(set(m for (m, _) in lad24) | set(m for (m, _) in lad40))

    print(f"# G3 Integration: 24-CU vs 40-CU long-context ladders\n")
    print(f"**24-CU source:** {path24.name}")
    print(f"**40-CU source:** {path40.name}\n")

    # ===== 24-CU only (standalone for reference) =====
    print("## 24-CU Ladder (gen tok/s + needle)\n")
    head = "| Model | " + " | ".join(kt(t) for t in TIERS) + " |"
    print(head)
    print("|" + "---|" * (len(TIERS) + 1))
    for m in models:
        cells = []
        for t in TIERS:
            r = lad24.get((m, t))
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

    # ===== 40-CU only (for reference) =====
    print("\n## 40-CU Ladder (gen tok/s + needle)\n")
    print(head)
    print("|" + "---|" * (len(TIERS) + 1))
    for m in models:
        cells = []
        for t in TIERS:
            r = lad40.get((m, t))
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

    # ===== Per-tier Δ speedup (24->40) =====
    print("\n## CU Unlock Speedup (40-CU / 24-CU ratio)\n")
    print(head)
    print("|" + "---|" * (len(TIERS) + 1))
    speedups_by_tier = {t: [] for t in TIERS}
    for m in models:
        cells = []
        for t in TIERS:
            r24 = lad24.get((m, t))
            r40 = lad40.get((m, t))
            g24 = r24.get("gen") if r24 else None
            g40 = r40.get("gen") if r40 else None
            if g24 and g40:
                delta = g40 / g24
                speedups_by_tier[t].append(delta)
                cells.append(f"{delta:.2f}×")
            else:
                cells.append("·")
        print(f"| {m} | " + " | ".join(cells) + " |")

    # Summary statistics
    print("\n## Per-tier median speedup\n")
    print("| Tier | Median Δ | Median % gain |")
    print("|---:|---:|---:|")
    for t in TIERS:
        if speedups_by_tier[t]:
            med_delta = statistics.median(speedups_by_tier[t])
            pct_gain = (med_delta - 1) * 100
            print(f"| {kt(t)} | {med_delta:.2f}× | {pct_gain:+.1f}% |")

    # Prefill comparison
    print("\n## Prefill tok/s @ 24-CU per tier\n")
    print(head)
    print("|" + "---|" * (len(TIERS) + 1))
    for m in models:
        cells = [f(lad24.get((m, t), {}).get("pp")) for t in TIERS]
        print(f"| {m} | " + " | ".join(cells) + " |")

    print("\n## Prefill tok/s @ 40-CU per tier\n")
    print(head)
    print("|" + "---|" * (len(TIERS) + 1))
    for m in models:
        cells = [f(lad40.get((m, t), {}).get("pp")) for t in TIERS]
        print(f"| {m} | " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()
