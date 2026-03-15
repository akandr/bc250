#!/usr/bin/env python3
"""Benchmark qwen3.5-27b-iq2m vs qwen3.5:9b vs qwen3:14b on BC-250"""
import json, time, urllib.request

OLLAMA = "http://localhost:11434"
RESULTS = "/opt/netscan/tmp/bench-qwen35-27b-results.json"

MODELS = [
    "qwen3.5-27b-iq2m",
    "qwen3.5:9b",
]

CTX_SIZES = [4096, 8192, 16384, 24576, 32768]

PROMPT = "Explain the concept of quantum entanglement in simple terms. Be thorough but concise."

def ollama_unload():
    try:
        req = urllib.request.Request(f"{OLLAMA}/api/ps")
        resp = urllib.request.urlopen(req, timeout=10)
        ps = json.loads(resp.read())
        for m in ps.get("models", []):
            data = json.dumps({"model": m["name"], "keep_alive": 0}).encode()
            req = urllib.request.Request(f"{OLLAMA}/api/generate", data=data,
                                        headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=30)
    except:
        pass
    time.sleep(3)

def run_bench(model, num_ctx):
    payload = {
        "model": model,
        "prompt": PROMPT,
        "stream": False,
        "options": {"num_ctx": num_ctx, "num_predict": 200, "temperature": 0.7},
        "think": False,
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/generate", data=data,
                                headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=600)
        result = json.loads(resp.read())
        gen_count = result.get("eval_count", 0)
        gen_ns = result.get("eval_duration", 1)
        prefill_ns = result.get("prompt_eval_duration", 1)
        prefill_count = result.get("prompt_eval_count", 0)
        load_ns = result.get("load_duration", 0)
        return {
            "model": model, "num_ctx": num_ctx,
            "gen_tok_s": round(gen_count / (gen_ns / 1e9), 1) if gen_ns > 0 else 0,
            "prefill_tok_s": round(prefill_count / (prefill_ns / 1e9), 1) if prefill_ns > 0 else 0,
            "load_s": round(load_ns / 1e9, 1),
            "status": "OK",
        }
    except Exception as e:
        return {"model": model, "num_ctx": num_ctx, "status": f"FAIL: {e}"}

def get_gpu_info():
    try:
        req = urllib.request.Request(f"{OLLAMA}/api/ps")
        resp = urllib.request.urlopen(req, timeout=10)
        ps = json.loads(resp.read())
        for m in ps.get("models", []):
            return {
                "size_vram": m.get("size_vram", 0),
                "size": m.get("size", 0),
                "gpu_pct": round(m.get("size_vram", 0) / max(m.get("size", 1), 1) * 100),
            }
    except:
        pass
    return {}

results = []
for model in MODELS:
    print(f"\n{'='*60}\nBENCHMARKING: {model}\n{'='*60}")
    for ctx in CTX_SIZES:
        print(f"  ctx={ctx:,}...", end=" ", flush=True)
        ollama_unload()
        time.sleep(2)
        r = run_bench(model, ctx)
        if r["status"] == "OK":
            gpu = get_gpu_info()
            r.update(gpu)
            print(f"gen={r['gen_tok_s']:.1f} tok/s, prefill={r['prefill_tok_s']:.1f}, "
                  f"GPU={r.get('gpu_pct','?')}%, VRAM={r.get('size_vram',0)/1e9:.1f}G, load={r['load_s']:.1f}s")
        else:
            print(f"FAILED: {r['status']}")
            results.append(r)
            break
        results.append(r)
    ollama_unload()

with open(RESULTS, "w") as f:
    json.dump(results, f, indent=2)

print(f"\n{'='*80}\nSUMMARY\n{'='*80}")
print(f"{'Model':<25} {'Ctx':>8} {'Gen tok/s':>10} {'Prefill':>10} {'GPU%':>5} {'VRAM':>8}")
print("-" * 80)
for r in results:
    if r["status"] == "OK":
        print(f"{r['model']:<25} {r['num_ctx']:>8,} {r['gen_tok_s']:>10.1f} {r['prefill_tok_s']:>10.1f} "
              f"{r.get('gpu_pct','?'):>5} {r.get('size_vram',0)/1e9:>7.1f}G")
    else:
        print(f"{r['model']:<25} {r['num_ctx']:>8,} {'':>10} {'':>10} {'':>5} {'':>8} {r['status'][:30]}")
