#!/usr/bin/env python3
"""Build Table 11 (clock_power) replacement from Pass D fresh telemetry."""
import json
from pathlib import Path
D = Path(__file__).parent
d = json.load(open(D/"passD"/"step-1-perf.passD-stock24cu-20260604-1243.json"))["results"]

T11 = [
    ("qwen3.5\\!:\\!9b (Ollama)",       "qwen3.5-9b-ollama"),
    ("deepseek-r1-14b",                 "deepseek-r1-14b-ollama"),
    ("gpt-oss-20b (Ollama)",            "gpt-oss-20b-ollama"),
    ("qwen3-coder-30b (IQ2\\_M)",       "qwen3-coder-30b-iq2m-ollama"),
    ("qwen3.5-35b (IQ2\\_M)",           "qwen3-5-35b-a3b-iq2m-ollama"),
    ("granite-4.0-h-tiny",              "granite3-3-8b-ollama"),
]
print(f"{'art':35s} {'clk_mean':>15s} {'peak':>5s} {'T_max':>6s} status")
rows = []
for art, k in T11:
    if k not in d:
        rows.append((art, None))
        continue
    runs = [r for r in d[k]["runs"] if r.get("status") == "ok"]
    clk_means, sclk_max, t_max = [], [], []
    for r in runs:
        ov = r.get("gpu_overlay") or {}
        if ov.get("samples", 0) > 0:
            cm = ov.get("sclk_mhz_mean")
            mx = ov.get("sclk_mhz_max")
            tm = ov.get("temp_c_max")
            if cm: clk_means.append(cm)
            if mx: sclk_max.append(mx)
            if tm: t_max.append(tm)
    if not clk_means:
        print(f"{art:35s} STALE")
        rows.append((art, None))
        continue
    cl_lo, cl_hi = min(clk_means), max(clk_means)
    pk = max(sclk_max)
    tm = max(t_max)
    print(f"{art:35s} {cl_lo:>5.0f}-{cl_hi:<5.0f} {pk:>5.0f} {tm:>5.0f}")
    rows.append((art, (cl_lo, cl_hi, pk, tm)))

# build LaTeX (no power column — sampler doesn't have it)
out = [
r"\begin{table}[ht]",
r"\caption{GPU clock and edge temperature during 4K-context perf runs",
r"on verified-stock 24-CU, Ollama~0.20.0; ranges in the sclk column",
r"reflect variation across the three runs (per-run time-weighted means).",
r"``Peak sclk'' is the highest DPM tier the 1\,Hz overlay sampler",
r"observed in any run; ``Temp~max'' is the highest edge-sensor reading",
r"across the three runs.  The peak DPM tier observed during these",
r"perf cells is 1\,500\,MHz, matching the wider sustained-inference",
r"sweep of Section~\ref{subsubsec:power_sweep}.  Per-run package power",
r"is not reported in this table because the 1\,Hz sampler used here",
r"records sclk and temperature only; the dedicated 5\,Hz",
r"\texttt{power1\_average} channel is reported in the sustained-",
r"inference sweep below.}\label{tab:clock_power}",
r"\centering",
r"\scriptsize",
r"\begin{tabular}{@{}lccc@{}}",
r"\toprule",
r"\textbf{Model}        & \textbf{Per-run mean sclk} & \textbf{Peak sclk} & \textbf{Temp max} \\",
r"                      & \textbf{(MHz)}             & \textbf{(MHz)}      & \textbf{(\textdegree C)} \\",
r"\otoprule",
]
for art, vals in rows:
    if vals is None:
        out.append(f"{art} & --- & --- & --- \\\\")
    else:
        cl_lo, cl_hi, pk, tm = vals
        if int(cl_lo) == int(cl_hi):
            cl_str = f"{cl_lo:.0f}"
        else:
            cl_str = f"{cl_lo:.0f}--{cl_hi:.0f}"
        out.append(f"{art} & {cl_str} & {pk:.0f} & {tm:.0f} \\\\")
out.append(r"\bottomrule")
out.append(r"\end{tabular}")
out.append(r"\end{table}")
open(D/"_table_clock_power.tex","w").write("\n".join(out)+"\n")
print(f"\nwrote {D/'_table_clock_power.tex'}")
