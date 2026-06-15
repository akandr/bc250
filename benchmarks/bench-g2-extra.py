#!/usr/bin/env python3
"""Quick perf benchmark for G2 extra dense models (Ollama backend).
Runs STD_PROMPT @ 4096 ctx, n_gen=100, n=3 for each model.
Saves results to /tmp/g2-extra-results.json on the board.
"""
import json, time, urllib.request, urllib.error, statistics, sys

OLLAMA_URL = "http://localhost:11434"

STD_PROMPT = (
    "Compare RISC and CISC processor architectures. Discuss instruction set design "
    "philosophy, pipeline implications, code density tradeoffs, and the historical "
    "context in which each approach evolved. Cover at least: (a) the original MIPS / "
    "SPARC / ARM RISC designs, (b) the x86 CISC lineage and its eventual adoption of "
    "internal micro-op decoding, (c) why modern processors blur the distinction, and "
    "(d) the impact on compiler design, code size, and energy efficiency. Provide a "
    "concrete example of where RISC's reduced instruction set forced a multi-instruction "
    "sequence that a CISC equivalent handled in one opcode, and explain the performance "
    "implications on modern superscalar implementations. Conclude with a paragraph on "
    "the contemporary relevance of the distinction in light of Apple Silicon's M-series, "
    "the recent RISC-V momentum, and the persistence of x86-64 in servers and laptops."
)

MODELS = [
    {"name": "llama3.2:3b",  "ollama_name": "llama3.2:3b",  "kind": "dense", "active_param_b": 3.0,  "total_param_b": 3.0},
    {"name": "qwen3:4b",     "ollama_name": "qwen3:4b",     "kind": "dense", "active_param_b": 4.0,  "total_param_b": 4.0},
    {"name": "qwen3:8b-q8_0","ollama_name": "qwen3:8b-q8_0","kind": "dense", "active_param_b": 8.0,  "total_param_b": 8.0},
    {"name": "qwen3:14b",    "ollama_name": "qwen3:14b",    "kind": "dense", "active_param_b": 14.0, "total_param_b": 14.0},
]

N_RUNS   = 3
N_GEN    = 100
CTX      = 4096
COOLDOWN = 30  # seconds between models


def run_ollama_generate(model_name, prompt, n_gen, ctx):
    payload = json.dumps({
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_ctx": ctx,
            "num_predict": n_gen,
            "temperature": 0.0,
        },
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read())
    wall_s = time.monotonic() - t0

    eval_count    = int(data.get("eval_count", 0))
    eval_dur_ns   = int(data.get("eval_duration", 1))
    prompt_count  = int(data.get("prompt_eval_count", 0))
    prompt_dur_ns = int(data.get("prompt_eval_duration", 1))

    gen_tok_s     = eval_count    / (eval_dur_ns    / 1e9) if eval_count    else 0
    prefill_tok_s = prompt_count  / (prompt_dur_ns  / 1e9) if prompt_count else 0

    return {
        "n_gen": eval_count,
        "n_prompt": prompt_count,
        "gen_tok_s": round(gen_tok_s, 2),
        "prefill_tok_s": round(prefill_tok_s, 2),
        "wall_s": round(wall_s, 2),
        "status": "ok" if eval_count >= 10 else "short",
    }


results = {}

for m in MODELS:
    name = m["name"]
    print(f"\n=== {name} ===", flush=True)
    runs = []
    for i in range(N_RUNS):
        tag = f"{name}-run{i+1}"
        print(f"  run {i+1}/{N_RUNS} ...", end=" ", flush=True)
        try:
            rec = run_ollama_generate(m["ollama_name"], STD_PROMPT + f"\n<!-- {tag} -->", N_GEN, CTX)
        except Exception as e:
            rec = {"status": "error", "error": str(e)}
        runs.append(rec)
        print(f"gen={rec.get('gen_tok_s')} tok/s  prefill={rec.get('prefill_tok_s')} tok/s", flush=True)
        if i < N_RUNS - 1:
            time.sleep(5)

    ok_runs = [r for r in runs if r.get("status") == "ok"]
    gen_vals = [r["gen_tok_s"] for r in ok_runs]
    prefill_vals = [r["prefill_tok_s"] for r in ok_runs]

    results[name] = {
        "kind": m["kind"],
        "active_param_b": m["active_param_b"],
        "total_param_b": m["total_param_b"],
        "backend": "ollama",
        "runs": runs,
        "aggregate": {
            "gen_tok_s_median":    round(statistics.median(gen_vals), 2) if gen_vals else None,
            "prefill_tok_s_median": round(statistics.median(prefill_vals), 2) if prefill_vals else None,
            "n_ok": len(ok_runs),
        },
    }
    print(f"  -> median gen={results[name]['aggregate']['gen_tok_s_median']} tok/s")
    if MODELS.index(m) < len(MODELS) - 1:
        print(f"  cooldown {COOLDOWN}s ...", flush=True)
        time.sleep(COOLDOWN)

out = {"step": "perf-g2-extra", "results": results}
outfile = "/tmp/g2-extra-results.json"
with open(outfile, "w") as f:
    json.dump(out, f, indent=2)
print(f"\nDone. Results → {outfile}")
