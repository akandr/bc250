#!/usr/bin/env python3
"""Analyse the occupancy / memory-level-parallelism sweep.

Reads the three raw runs produced by scripts/bc250-occupancy.sh:
  benchmarks/results-occupancy/occ-run{1,2,3}.txt

Writes:
  benchmarks/results-occupancy/occupancy-summary.json
  article/fig-occupancy.pdf

The sweep streams a FIXED 1 GiB working set (read+write of a 512 MiB
host-coherent buffer in the UMA address space) with a grid-stride copy
kernel, varying only the launched workgroup count (= active wavefronts =
occupancy). Total bytes moved is constant; achieved bandwidth therefore
isolates the effect of parallelism. We report the per-occupancy median of
three runs, normalise to the measured saturating plateau, and overlay the
single-decode-stream effective bandwidth (deepseek-r1-14b, 191 GB/s, from
the roofline anchor) to show where autoregressive decode sits on the curve.
"""
import json
import pathlib
import re
import statistics

HERE = pathlib.Path(__file__).parent
RESDIR = HERE / "results-occupancy"
RUNS = [RESDIR / f"occ-run{i}.txt" for i in (1, 2, 3)]

# Roofline anchor (verified-stock 24-CU), from results-roofline/roofline-summary.json
DECODE_EFF_GBS = 191.0          # deepseek-r1-14b single-stream effective bandwidth
DECODE_LABEL = "deepseek-r1-14b decode\n(single stream, 191 GB/s)"
ROOFLINE_COPY_PEAK = 357.29     # J.UCS streaming-copy "peak" reference

LINE_RE = re.compile(
    r"OCC groups=(\d+) threads=(\d+) wave32=(\d+) gbs=([\d.]+) frac_peak=([\d.]+)")


def parse(path):
    out = {}
    for line in path.read_text().splitlines():
        m = LINE_RE.match(line)
        if m:
            groups = int(m.group(1))
            out[groups] = dict(threads=int(m.group(2)),
                               wave32=int(m.group(3)),
                               gbs=float(m.group(4)))
    return out


def main():
    runs = [parse(p) for p in RUNS]
    groups_all = sorted(runs[0].keys())

    rows = []
    for g in groups_all:
        gbs_vals = [r[g]["gbs"] for r in runs if g in r]
        rows.append(dict(groups=g,
                         threads=runs[0][g]["threads"],
                         wave32=runs[0][g]["wave32"],
                         gbs_median=statistics.median(gbs_vals),
                         gbs_min=min(gbs_vals),
                         gbs_max=max(gbs_vals),
                         n=len(gbs_vals)))

    # Saturating plateau = median of the top of the curve (groups >= 2048),
    # which is monotone and noise-stable.
    plateau = statistics.median(
        [r["gbs_median"] for r in rows if r["groups"] >= 2048])
    for r in rows:
        r["frac_sat"] = r["gbs_median"] / plateau

    # Knee: first occupancy reaching >= 90% of the plateau.
    knee = next(r for r in rows if r["frac_sat"] >= 0.90)
    decode_frac_sat = DECODE_EFF_GBS / plateau

    # Occupancy-equivalent of decode: interpolate where the curve crosses 191.
    below = max((r for r in rows if r["gbs_median"] <= DECODE_EFF_GBS),
                key=lambda r: r["gbs_median"])
    above = min((r for r in rows if r["gbs_median"] >= DECODE_EFF_GBS),
                key=lambda r: r["gbs_median"])
    frac = ((DECODE_EFF_GBS - below["gbs_median"]) /
            (above["gbs_median"] - below["gbs_median"]))
    decode_eq_wave32 = below["wave32"] + frac * (above["wave32"] - below["wave32"])

    summary = dict(
        device="AMD BC-250 (RADV GFX1013)",
        config="24-CU stock, oberon-governor 1500 MHz, host-coherent UMA",
        bytes_moved=1073741824,
        runs=len(RUNS),
        plateau_gbs=round(plateau, 1),
        roofline_copy_peak_gbs=ROOFLINE_COPY_PEAK,
        min_occ_gbs=rows[0]["gbs_median"],
        min_occ_frac_sat=round(rows[0]["frac_sat"], 4),
        knee_wave32=knee["wave32"],
        knee_groups=knee["groups"],
        knee_gbs=round(knee["gbs_median"], 1),
        decode_eff_gbs=DECODE_EFF_GBS,
        decode_frac_sat=round(decode_frac_sat, 3),
        decode_equiv_wave32=round(decode_eq_wave32),
        knee_over_decode_occ=round(knee["wave32"] / decode_eq_wave32, 1),
        sweep=rows,
    )
    (RESDIR / "occupancy-summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != "sweep"}, indent=2))

    _plot(rows, plateau, decode_eq_wave32)


def _plot(rows, plateau, decode_eq_wave32):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    x = [r["wave32"] for r in rows]
    y = [r["gbs_median"] for r in rows]
    ylo = [r["gbs_min"] for r in rows]
    yhi = [r["gbs_max"] for r in rows]

    fig, ax = plt.subplots(figsize=(3.4, 2.5))
    ax.fill_between(x, ylo, yhi, color="#cfe3f3", alpha=0.7, linewidth=0)
    ax.plot(x, y, "-o", color="#1f6fb2", ms=3, lw=1.2, label="achieved bandwidth")

    ax.axhline(plateau, color="#444", ls=":", lw=0.9)
    ax.text(x[1], plateau + 6, f"saturating plateau ≈ {plateau:.0f} GB/s",
            fontsize=6.5, color="#444")

    ax.axhline(DECODE_EFF_GBS, color="#c0392b", ls="--", lw=0.9)
    ax.text(x[1], DECODE_EFF_GBS - 26,
            "single-stream decode (191 GB/s)", fontsize=6.5, color="#c0392b")
    ax.plot([decode_eq_wave32], [DECODE_EFF_GBS], "D", color="#c0392b", ms=4)

    ax.set_xscale("log", base=2)
    ax.set_xlabel("active wavefronts (wave32, log scale)", fontsize=7.5)
    ax.set_ylabel("achieved streaming BW (GB/s)", fontsize=7.5)
    ax.tick_params(labelsize=6.5)
    ax.set_ylim(0, plateau * 1.12)
    ax.grid(True, which="both", ls=":", lw=0.3, alpha=0.5)
    ax.legend(fontsize=6.5, loc="lower right", frameon=False)
    fig.tight_layout(pad=0.3)
    out = HERE.parent / "article" / "fig-occupancy.pdf"
    fig.savefig(out, bbox_inches="tight")
    print(f"wrote {out}")


if __name__ == "__main__":
    DECODE_EFF_GBS = 191.0
    main()
