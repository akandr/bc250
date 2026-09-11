#!/usr/bin/env python3
"""Find which Pass D rows are byte-identical to Pass B (stale due to merge=True)."""
import json
from pathlib import Path
D = Path(__file__).parent
A = json.load(open(D/"passA"/"step-1-perf.passA-stock24cu-20260603-1115.json"))["results"]
B = json.load(open(D/"passB"/"step-1-perf.passB-patched40cu-20260604-0033.json"))["results"]
DD = json.load(open(D/"passD"/"step-1-perf.passD-stock24cu-20260604-1243.json"))["results"]

DLIST = set("llama3.2-3b-ollama,phi4-mini-ollama,gemma3-4b-ollama,gemma3-12b-ollama,qwen3-8b-ollama-q4km,qwen3-8b-ollama-q8,mistral-nemo-12b-ollama,qwen3-14b-ollama,deepseek-r1-14b-ollama,qwen3-30b-a3b-q2k-ollama,gemma2-9b-ollama,glm4-9b-ollama,granite3-3-8b-ollama,llama3-1-8b-ollama,qwen3-5-35b-a3b-iq2m-ollama,qwen3-5-27b-iq2m-ollama,qwen3-coder-30b-iq2m-ollama".split(","))
print("Pass D only-list size:", len(DLIST))
print()
hdr = f"{'name':38s} {'A_gen':>9s} {'B_gen':>9s} {'D_gen':>9s}  flag"
print(hdr)
print("-" * len(hdr))
for name in sorted(set(list(A)+list(B)+list(DD))):
    a = A.get(name, {}).get("aggregate", {}).get("gen_tok_s_median")
    b = B.get(name, {}).get("aggregate", {}).get("gen_tok_s_median")
    d = DD.get(name, {}).get("aggregate", {}).get("gen_tok_s_median")
    flag = ""
    if d is not None and b is not None and abs(d-b) < 0.001:
        flag = " D==B (stale, NOT remeasured)"
    elif d is not None and a is not None and abs(d-a) < 0.001:
        flag = " D==A (carried from Pass A)"
    elif d is not None and name in DLIST:
        flag = " fresh Pass D"
    fa = "-" if a is None else f"{a:.2f}"
    fb = "-" if b is None else f"{b:.2f}"
    fd = "-" if d is None else f"{d:.2f}"
    print(f"{name:38s} {fa:>9s} {fb:>9s} {fd:>9s}  {flag}")
