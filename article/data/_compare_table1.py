#!/usr/bin/env python3
"""Compare article Table 1 (`tab:gen_speed`) claims vs Pass D / Pass A
measurements. Build replacement table rows in LaTeX."""
import json, sys
from pathlib import Path

D = Path(__file__).parent
PASSD = json.load(open(D/"passD"/"step-1-perf.passD-stock24cu-20260604-1243.json"))["results"]
PASSA = json.load(open(D/"passA"/"step-1-perf.passA-stock24cu-20260603-1115.json"))["results"]
CANON = json.load(open(D/"_canonical_stock24cu_perf.json"))

# article Table 1 rows: model, params, quant, gen, prefill, vram, qual
ARTICLE_T1 = [
    ("llama3.2:3b",        "3.2B",  "Q4_K_M",  103.8, 484.8,  2.2, "93%",  False),
    ("phi4-mini",          "3.8B",  "Q4_K_M",   87.0, 346.3,  2.5, "93%",  False),
    ("gemma3:4b",          "4B",    "Q4_K_M",   76.5, 357.1,  3.8, "100%", False),
    ("qwen3:8b",           "8.2B",  "Q4_K_M",   43.1, 192.7,  5.1, "100%", False),
    ("qwen3.5:9b",         "9.7B",  "Q4_K_M",   31.7, 171.3,  7.9, "100%", True),
    ("mistral-nemo:12b",   "12.2B", "Q4_0",     34.1, 159.8,  6.7, "80%",  False),
    ("qwen3:14b",          "14.8B", "Q4_K_M",   26.8, 108.6,  8.9, "100%", False),
    ("deepseek-r1:14b",    "14B",   "Q4_K_M",   28.7, 101.4,  8.5, "100%", False),
    ("qwen3.5-35b-a3b",    "35/3B", "IQ2_M",    37.5, 127.5, 12.3, "93%",  True),
]

# Map article model name -> Pass D ollama key
PASS_D_MAP = {
    "llama3.2:3b":        "llama3.2-3b-ollama",
    "phi4-mini":          "phi4-mini-ollama",
    "gemma3:4b":          "gemma3-4b-ollama",
    "qwen3:8b":           "qwen3-8b-ollama-q4km",
    "qwen3.5:9b":         "qwen3.5-9b-ollama",
    "mistral-nemo:12b":   "mistral-nemo-12b-ollama",
    "qwen3:14b":          "qwen3-14b-ollama",
    "deepseek-r1:14b":    "deepseek-r1-14b-ollama",
    "qwen3.5-35b-a3b":    "qwen3-5-35b-a3b-iq2m-ollama",
}

def get_agg(passdata, key):
    if key not in passdata: return None
    a = passdata[key].get("aggregate", {})
    return a.get("gen_tok_s_median"), a.get("prefill_tok_s_median")

def get_canon(key):
    if key not in CANON: return None
    c = CANON[key]
    return c["gen"], c["pp"], c["source"]

print("\n=== Table 1 / tab:gen_speed comparison (CANONICAL) ===")
print(f"{'model':18s} {'art_gen':>8s} {'CAN_gen':>8s} {'Δ%':>7s}    {'art_pp':>8s} {'CAN_pp':>8s} {'Δ%':>7s}    source")
rows = []
for model, params, quant, art_gen, art_pp, vram, qual, star in ARTICLE_T1:
    key = PASS_D_MAP[model]
    res = get_canon(key)
    if res is None:
        print(f"{model:18s}  ** MISSING ({key}) **")
        rows.append((model, params, quant, None, None, vram, qual, star, None))
        continue
    d_gen, d_pp, src = res
    d_pct = (d_gen - art_gen) / art_gen * 100
    p_pct = (d_pp - art_pp) / art_pp * 100 if d_pp else 0
    print(f"{model:18s} {art_gen:>8.1f} {d_gen:>8.2f} {d_pct:>+7.1f}    "
          f"{art_pp:>8.1f} {d_pp:>8.2f} {p_pct:>+7.1f}    {src}")
    rows.append((model, params, quant, d_gen, d_pp, vram, qual, star, src))

# Estimate effective CU-unlock factor implied by article-vs-stock
print("\n=== implied article ÷ canonical stock 24-CU ratio ===")
for model, _, _, art_gen, _, _, _, _ in ARTICLE_T1:
    key = PASS_D_MAP[model]
    res = get_canon(key)
    if res is None: continue
    d_gen, _, _ = res
    print(f"  {model:22s} article/stock_24CU = {art_gen/d_gen:.3f}")

# Build LaTeX replacement
def fmt_qual(q):
    return q.replace("%", "\\%")

print("\n=== LaTeX replacement: tab:gen_speed (stock 24-CU Ollama 0.20.0) ===")
out = []
out.append(r"\begin{table}[ht]")
out.append(r"\caption{Generation speed and VRAM for representative models at 4K")
out.append(r"context. $\star$~recommended. Stock 24-CU configuration under")
out.append(r"Ollama~0.20.0 (re-measured on verified-stock 24-CU; supersedes")
out.append(r"the original 0.18.0 numbers — see Section~\ref{subsec:errata}).}\label{tab:gen_speed}")
out.append(r"\centering")
out.append(r"\scriptsize")
out.append(r"\setlength{\tabcolsep}{3pt}")
out.append(r"\begin{tabular}{@{}lccrrcr@{}}")
out.append(r"\toprule")
out.append(r"\textbf{Model} & \textbf{Par.} & \textbf{Quant} & \textbf{Gen} & \textbf{Pfill} & \textbf{VRAM} & \textbf{Qual.} \\")
out.append(r" & & & \textbf{(t/s)} & \textbf{(t/s)} & \textbf{(GiB)} & \\")
out.append(r"\otoprule")
out.append(r"\multicolumn{7}{@{}l}{\scriptsize $^a$\,Qwen3.5-35B-A3B (35\,B total, 3\,B active per token), IQ2\_M $\approx$2.5\,bits/weight.} \\")
out.append(r"\addlinespace[1pt]")
for model, params, quant, d_gen, d_pp, vram, qual, star, src in rows:
    star_p = r"$\star$ " if star else ""
    suffix = r"\,$^a$" if model == "qwen3.5-35b-a3b" else ""
    nm_safe = model.replace(":", r"\!:\!").replace("_", r"\_")
    nm = f"{star_p}{nm_safe}{suffix}"
    if d_gen is None:
        out.append(f"{nm} & {params} & {quant.replace('_',r'_')} & --- & --- & {vram:.1f} & {fmt_qual(qual)} \\\\")
    else:
        q = quant.replace('_', r'\_')
        out.append(f"{nm} & {params} & {q} & {d_gen:>5.1f} & {d_pp:>5.1f} & {vram:.1f} & {fmt_qual(qual)} \\\\")
out.append(r"\bottomrule")
out.append(r"\end{tabular}")
out.append(r"\end{table}")
new_tex = "\n".join(out)
print(new_tex)
open(D/"_table1_replacement.tex", "w").write(new_tex+"\n")
print(f"\nwrote {D/'_table1_replacement.tex'}")
