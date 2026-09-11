#!/usr/bin/env python3
"""Generate the LaTeX replacement for Table 10 (cu_unlock) using actual
paired Pass A (24-CU stock) vs Pass B (40-CU patched) data.

Both passes used identical harness, prompt, KV q4_0, FA on, ollama 0.20.0,
llama.cpp build b9265 — the article's stated protocol for the 40-CU paired
re-run. The original Table 10 numbers come from a different/older dataset
that overstates absolute throughput by ~20-25% relative to the current
build. The replacement uses the actual paired re-run.
"""
import json, os, statistics

DATA = os.path.dirname(os.path.abspath(__file__))
A = json.load(open(os.path.join(DATA, "passA", "step-1-perf.passA-stock24cu-20260603-1115.json")))
B = json.load(open(os.path.join(DATA, "passB", "step-1-perf.passB-patched40cu-20260604-0033.json")))

DISPLAY = {
    "qwen3.5-9b-ollama":     ("qwen3.5:9b (Ollama, Q4\\_K\\_M)",   "9.0B"),
    "gemma4-latest":          ("gemma3:12b (Ollama, Q4\\_K\\_M)",  "12.2B"),
    "deepseek-r1-14b":        ("deepseek-r1-14b (LC, Q4\\_K\\_M)",  "14.0B"),
    "gpt-oss-20b-mxfp4":      ("gpt-oss-20b (LC, MXFP4)",          "20.9B"),
    "gpt-oss-20b-ollama":     ("gpt-oss-20b (Ollama, MXFP4)",      "20.9B"),
    "qwen3-coder-30b-iq2m":   ("qwen3-coder-30b (LC, IQ2\\_M)",    "30.5B"),
    "qwen3.5-35b-iq2m":       ("qwen3.5-35b-a3b (LC, IQ2\\_M)",    "35B/3B"),
    "qwen3.6-35b-iq2m":       ("qwen3.6-35b-a3b (LC, IQ2\\_M)",    "35B/3B"),
    "qwq-32b-iq2m":           ("qwq-32b (LC, IQ2\\_M)",            "32B"),
    "mistral-small-3.2-q3km-ollama": ("mistral-small-3.2 (Ollama, Q3\\_K\\_M)", "24B"),
    "granite-4.0-h-tiny":     ("granite-4.0-h-tiny (LC, hybrid)",  "7B/1B"),
}
ORDER = [
    "qwen3.5-9b-ollama", "gemma4-latest", "deepseek-r1-14b",
    "gpt-oss-20b-mxfp4", "gpt-oss-20b-ollama",
    "qwen3-coder-30b-iq2m", "qwen3.5-35b-iq2m", "qwen3.6-35b-iq2m",
    "qwq-32b-iq2m", "mistral-small-3.2-q3km-ollama",
    "granite-4.0-h-tiny",
]

def med_cv(runs):
    g = [r["gen_tok_s"] for r in runs if r.get("status")=="ok" and r.get("gen_tok_s")]
    p = [r.get("prefill_tok_s") for r in runs if r.get("status")=="ok" and r.get("prefill_tok_s")]
    if not g: return None, None, None, None
    cv_g = (statistics.pstdev(g)/statistics.mean(g)*100) if len(g)>1 else 0.0
    cv_p = (statistics.pstdev(p)/statistics.mean(p)*100) if (p and len(p)>1) else 0.0
    return statistics.median(g), statistics.median(p) if p else None, cv_g, cv_p

print(r"""\begin{table}[t]
\caption{Throughput before and after the 40-CU unlock on the paired
short-prompt re-run (Pass~A: stock 24-CU; Pass~B: patched 40-CU). All
numbers in tok/s, median of 3 runs at 4K context, prompt $\approx$262
tokens, KV~q4\_0, flash attention on, llama.cpp build~b9265, ollama
0.20.0. $\Delta$~columns give the multiplicative speed-up from
24~CUs to 40~CUs.}\label{tab:cu_unlock}
\centering
\scriptsize
\begin{tabular}{@{}lrrcrrc@{}}
\toprule
\textbf{Model} & \textbf{Gen 24} & \textbf{Gen 40} & $\boldsymbol{\Delta_\text{gen}}$
               & \textbf{Pfill 24} & \textbf{Pfill 40} & $\boldsymbol{\Delta_\text{pf}}$ \\
\otoprule""")

deltas_g, deltas_p = [], []
for name in ORDER:
    if name not in A["results"] or name not in B["results"]:
        continue
    ag, ap, _, _ = med_cv(A["results"][name].get("runs", []))
    bg, bp, _, _ = med_cv(B["results"][name].get("runs", []))
    if ag is None or bg is None: continue
    dg = bg / ag
    dp = bp / ap if (ap and bp) else None
    deltas_g.append(dg); deltas_p.append(dp)
    label, _ = DISPLAY[name]
    line = f"{label} & {ag:.2f} & {bg:.2f} & {dg:.2f}$\\times$ "
    if ap and bp:
        line += f"& {ap:.1f} & {bp:.1f} & {dp:.2f}$\\times$ \\\\"
    else:
        line += f"& -- & -- & -- \\\\"
    print(line)

mg = statistics.median(deltas_g)
mp = statistics.median([d for d in deltas_p if d is not None])
print(r"\midrule")
print(rf"\textbf{{Median across set}}  &        &       & \textbf{{{mg:.2f}$\boldsymbol{{\times}}$}}")
print(rf"                            &        &       & \textbf{{{mp:.2f}$\boldsymbol{{\times}}$}} \\")
print(r"""\bottomrule
\end{tabular}
\end{table}""")
