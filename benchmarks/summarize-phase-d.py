#!/usr/bin/env python3
"""Phase D summariser — 24 CU baseline vs 40 CU unlocked.

Reads:
  results-phase-c/step-1-perf.24cu-baseline.json     (24 CU step-1)
  results-phase-c/step-1-perf.40cu.json              (40 CU step-1)
  results-phase-c/step-2-ctx-quality.24cu-baseline.json  (24 CU step-2)
  results-phase-c/40cu-cells/ladder-*.json           (40 CU per-tier cells)

Emits two Markdown tables on stdout.
"""

from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).parent / "results-phase-c"
CELLS = ROOT / "40cu-cells"


def load_step1(p: Path) -> dict[str, dict]:
    """Return {model: {gen, pp, ttft, cv}} from a step-1 perf JSON."""
    raw = json.loads(p.read_text())
    out: dict[str, dict] = {}
    results = raw.get("results", raw)
    for m, rec in results.items():
        agg = rec.get("aggregate") or {}
        out[m] = {
            "gen": agg.get("gen_tok_s_median"),
            "pp": agg.get("prefill_tok_s_median"),
            "ttft": agg.get("ttft_s_median"),
            "cv": agg.get("gen_tok_s_cv_pct"),
            "backend": rec.get("backend"),
        }
    return out


def load_step2_24cu(p: Path) -> dict[tuple[str, int], dict]:
    raw = json.loads(p.read_text())
    out: dict[tuple[str, int], dict] = {}
    for m, rec in raw.get("results", {}).items():
        for tier_s, t in (rec.get("tiers") or {}).items():
            try:
                tier = int(tier_s)
            except ValueError:
                continue
            agg = t.get("aggregate") or {}
            verify = t.get("verify") or {}
            out[(m, tier)] = {
                "gen": agg.get("gen_tok_s_median"),
                "pp": agg.get("prefill_tok_s_median"),
                "needle": (verify.get("pass"), verify.get("total")),
                "status": (t.get("runs") or [{}])[0].get("status"),
            }
    return out


def load_40cu_cells() -> dict[tuple[str, int], dict]:
    out: dict[tuple[str, int], dict] = {}
    for f in sorted(CELLS.glob("*.json")):
        try:
            raw = json.loads(f.read_text())
        except Exception:
            continue
        if raw.get("step") != "ctx-quality":
            continue
        for m, rec in raw.get("results", {}).items():
            for tier_s, t in (rec.get("tiers") or {}).items():
                try:
                    tier = int(tier_s)
                except ValueError:
                    continue
                agg = t.get("aggregate") or {}
                verify = t.get("verify") or {}
                run0 = (t.get("runs") or [{}])[0]
                # Newest cell wins if duplicated
                out[(m, tier)] = {
                    "gen": agg.get("gen_tok_s_median"),
                    "pp": agg.get("prefill_tok_s_median"),
                    "needle": (verify.get("pass"), verify.get("total")),
                    "status": run0.get("status"),
                    "n_prompt": run0.get("n_prompt"),
                    "n_gen": run0.get("n_gen"),
                }
    return out


def delta(a, b):
    if a is None or b is None or a == 0:
        return "—"
    return f"{(b - a) / a * 100:+.1f}%"


def fmt(x, digits=2):
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:.{digits}f}"
    return str(x)


def print_step1_table():
    p24 = ROOT / "step-1-perf.24cu-baseline.json"
    p40 = ROOT / "step-1-perf.40cu.json"
    if not (p24.exists() and p40.exists()):
        print("(step-1 files missing)")
        return
    a = load_step1(p24)
    b = load_step1(p40)
    models = sorted(set(a) | set(b))
    print("### Step 1 — short-prompt throughput, 24 CU → 40 CU (n_prompt≈260, n_gen=100)\n")
    print("| Model | Backend | gen 24CU | gen 40CU | Δgen | pp 24CU | pp 40CU | Δpp |")
    print("|---|---|---:|---:|---:|---:|---:|---:|")
    for m in models:
        ra = a.get(m, {})
        rb = b.get(m, {})
        be = rb.get("backend") or ra.get("backend") or "—"
        ga, gb = ra.get("gen"), rb.get("gen")
        pa, pb = ra.get("pp"), rb.get("pp")
        if ga is None and gb is None:
            continue
        print(
            f"| {m} | {be} | {fmt(ga)} | {fmt(gb)} | {delta(ga, gb)} "
            f"| {fmt(pa)} | {fmt(pb)} | {delta(pa, pb)} |"
        )


def print_step2_table():
    a = load_step2_24cu(ROOT / "step-2-ctx-quality.24cu-baseline.json")
    b = load_40cu_cells()
    tiers = sorted({t for (_, t) in (set(a) | set(b))})
    models = sorted({m for (m, _) in (set(a) | set(b))})
    print("\n### Step 2 — filled-context ladder, 24 CU → 40 CU (gen tok/s per tier)\n")
    header = "| Model |"
    sep = "|---|"
    for t in tiers:
        kt = f"{t//1024}K" if t % 1024 == 0 else str(t)
        header += f" {kt} 24CU | {kt} 40CU | Δ |"
        sep += "---:|---:|---:|"
    print(header)
    print(sep)
    for m in models:
        row = f"| {m} |"
        for t in tiers:
            ra, rb = a.get((m, t), {}), b.get((m, t), {})
            ga, gb = ra.get("gen"), rb.get("gen")
            row += f" {fmt(ga, 1)} | {fmt(gb, 1)} | {delta(ga, gb)} |"
        print(row)


def print_step2_compact():
    """A more readable per-model ladder with just 40CU results + needle pass."""
    b = load_40cu_cells()
    models = sorted({m for (m, _) in b})
    print("\n### 40 CU filled-context ladder — per-tier gen tok/s and needle retrieval\n")
    print("| Model | 2K | 4K | 8K | 16K | 24K | 32K | 49K | 64K |")
    print("|---|---|---|---|---|---|---|---|---|")
    show_tiers = [2048, 4096, 8192, 16384, 24576, 32768, 49152, 65536]
    for m in models:
        cells = []
        for t in show_tiers:
            r = b.get((m, t))
            if r is None:
                cells.append("—")
                continue
            g = r.get("gen")
            np_, nt = r.get("needle") or (None, None)
            status = r.get("status") or ""
            if g is None:
                if status == "fail":
                    cells.append("OOM")
                elif status == "parse_fail":
                    cells.append("parse-fail")
                else:
                    cells.append("fail")
            else:
                needle = f"{np_}/{nt}" if np_ is not None else ""
                cells.append(f"{g:.1f} ({needle})")
        print(f"| {m} | " + " | ".join(cells) + " |")


if __name__ == "__main__":
    print_step1_table()
    print_step2_table()
    print_step2_compact()
