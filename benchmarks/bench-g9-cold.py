#!/usr/bin/env python3
"""G9: Cold-start hardening — adds run 3 for qwen3.5:9b and n=3 for deepseek-r1:14b.

Each cold run: ollama stop, sync + echo 3 > drop_caches, then generate 20 tokens.
Records: TTFT (load_ms + prompt_eval), load_ms, gen_tok_s.
Data -> ~/phase-c-out/results/step-g9-cold.json
"""

import json, os, subprocess, time, datetime, urllib.request

OLLAMA = "http://127.0.0.1:11434"
OLLAMA_BIN = "/usr/local/bin/ollama"
OUT = os.path.expanduser("~/phase-c-out/results/step-g9-cold.json")

MODELS = [
    {"tag": "qwen3.5:9b", "label": "qwen3.5:9b (Q4_K_M, 6.6 GiB)", "n_runs": 3},
    {"tag": "deepseek-r1:14b", "label": "deepseek-r1:14b (Q4_K_M, 9.0 GiB)", "n_runs": 3},
]

PROMPT = "Briefly describe the water cycle in two sentences."
N_CTX = 4096
N_PREDICT = 20


def api(endpoint, data=None, timeout=300):
    url = f"{OLLAMA}{endpoint}"
    req = urllib.request.Request(url, method="POST" if data else "GET")
    if data:
        req.add_header("Content-Type", "application/json")
        body = json.dumps(data).encode()
    else:
        body = None
    with urllib.request.urlopen(req, body, timeout=timeout) as resp:
        return json.loads(resp.read())


def ollama_stop():
    """Stop all running models and ensure Ollama is idle."""
    try:
        ps = api("/api/ps", timeout=10)
        if ps and "models" in ps:
            for m in ps["models"]:
                name = m.get("name", "")
                if name:
                    api("/api/generate", {"model": name, "keep_alive": 0}, timeout=30)
                    time.sleep(1)
    except Exception:
        pass
    time.sleep(3)


def drop_caches():
    """Flush kernel page cache."""
    try:
        subprocess.run(
            "sync && echo 3 | sudo -n tee /proc/sys/vm/drop_caches > /dev/null",
            shell=True, timeout=10
        )
    except Exception:
        pass
    time.sleep(2)


def run_cold(model_tag, run_idx):
    """Full cold-start measurement: stop + flush + generate."""
    print(f"    run {run_idx+1}: stopping+flushing...", end=" ", flush=True)
    ollama_stop()
    drop_caches()
    time.sleep(2)
    print(f"generating...", end=" ", flush=True)
    t0 = time.time()
    try:
        resp = api("/api/generate", {
            "model": model_tag,
            "prompt": PROMPT,
            "stream": False,
            "options": {"num_ctx": N_CTX, "num_predict": N_PREDICT},
            "keep_alive": 0,  # unload after this run
            "think": False,
        }, timeout=300)
        wall_s = time.time() - t0
        if "error" in resp:
            print(f"FAIL: {resp['error'][:60]}")
            return {"status": "FAIL", "error": resp["error"][:100], "wall_s": round(wall_s, 2)}
        gen_toks = resp.get("eval_count", 0)
        eval_dur = resp.get("eval_duration", 0) / 1e9
        prompt_dur = resp.get("prompt_eval_duration", 0) / 1e9
        load_dur = resp.get("load_duration", 0) / 1e9
        ttft_s = load_dur + prompt_dur
        gen_tok_s = gen_toks / eval_dur if eval_dur > 0 else 0
        load_s = round(load_dur, 2)
        print(f"TTFT={ttft_s:.1f}s load={load_s}s gen={gen_tok_s:.1f}tok/s")
        return {
            "status": "OK",
            "run_idx": run_idx + 1,
            "ttft_s": round(ttft_s, 2),
            "load_s": load_s,
            "prompt_eval_s": round(prompt_dur, 2),
            "gen_tok_s": round(gen_tok_s, 2),
            "wall_s": round(wall_s, 2),
            "load_ms": round(load_dur * 1000, 0),
            "prompt_eval_count": resp.get("prompt_eval_count", 0),
            "eval_count": gen_toks,
        }
    except Exception as e:
        wall_s = time.time() - t0
        print(f"FAIL: {str(e)[:60]}")
        return {"status": "FAIL", "error": str(e)[:100], "wall_s": round(wall_s, 2)}


def main():
    print(f"""
======================================================================
  G9: Cold-Start Hardening
  Models: {[m['label'] for m in MODELS]}
  Output: {OUT}
======================================================================
""")
    results = []
    total_start = time.time()

    for model in MODELS:
        tag = model["tag"]
        n_runs = model["n_runs"]
        print(f"\n  {model['label']} (n={n_runs})")
        print("  " + "─" * 55)

        model_runs = []
        for i in range(n_runs):
            r = run_cold(tag, i)
            r["model"] = tag
            r["timestamp"] = datetime.datetime.now().isoformat()
            model_runs.append(r)

        ttfts = [r["ttft_s"] for r in model_runs if r["status"] == "OK"]
        loads = [r["load_s"] for r in model_runs if r["status"] == "OK"]
        if ttfts:
            med_ttft = sorted(ttfts)[len(ttfts)//2]
            med_load = sorted(loads)[len(loads)//2]
            print(f"  → TTFT per run: {[round(t,1) for t in ttfts]}")
            print(f"  → Median TTFT: {med_ttft:.1f}s  Median Load: {med_load:.1f}s")
        results.append({"model": tag, "label": model["label"], "runs": model_runs})

    elapsed = time.time() - total_start
    output = {
        "results": results,
        "metadata": {
            "timestamp": datetime.datetime.now().isoformat(),
            "elapsed_min": round(elapsed / 60, 1),
            "prompt": PROMPT,
            "n_ctx": N_CTX,
            "n_predict": N_PREDICT,
        }
    }
    with open(OUT, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n[done] Results saved to {OUT}")
    print(f"Total time: {elapsed/60:.1f} minutes")


if __name__ == "__main__":
    main()
