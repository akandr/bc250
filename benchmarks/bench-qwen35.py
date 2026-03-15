#!/usr/bin/env python3
"""Benchmark qwen3.5:9b vs qwen3:14b on BC-250 (16.5 GiB Vulkan)"""
import json, time, subprocess, sys

OLLAMA = "http://localhost:11434"
RESULTS = "/opt/netscan/tmp/bench-qwen35-results.json"

MODELS = [
    "qwen3.5:9b",
    "qwen3:14b",
]

# Context sizes to test (tokens)
CTX_SIZES = [4096, 8192, 16384, 24576, 32768, 40960, 48000, 65536]

# Short prompt for generation benchmark
PROMPT = "Explain the concept of quantum entanglement in simple terms. Be thorough but concise."

# Longer prompt to fill context for prefill benchmark
LONG_PROMPT = "Summarize the following: " + ("The quick brown fox jumps over the lazy dog. " * 200)

def ollama_unload():
    """Unload all models"""
    import urllib.request
    try:
        data = json.dumps({"model": "", "keep_alive": 0}).encode()
        # Just list and unload
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

def run_bench(model, num_ctx, prompt):
    """Run a single benchmark, return dict with results or None on failure"""
    import urllib.request
    
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_ctx": num_ctx,
            "num_predict": 200,
            "temperature": 0.7,
        },
        "think": False,
    }
    
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{OLLAMA}/api/generate",
        data=data,
        headers={"Content-Type": "application/json"}
    )
    
    try:
        resp = urllib.request.urlopen(req, timeout=300)
        result = json.loads(resp.read())
        
        gen_count = result.get("eval_count", 0)
        gen_ns = result.get("eval_duration", 1)
        prefill_count = result.get("prompt_eval_count", 0)
        prefill_ns = result.get("prompt_eval_duration", 1)
        load_ns = result.get("load_duration", 0)
        
        gen_tps = gen_count / (gen_ns / 1e9) if gen_ns > 0 else 0
        prefill_tps = prefill_count / (prefill_ns / 1e9) if prefill_ns > 0 else 0
        load_s = load_ns / 1e9
        
        return {
            "model": model,
            "num_ctx": num_ctx,
            "gen_tok_s": round(gen_tps, 1),
            "prefill_tok_s": round(prefill_tps, 1),
            "gen_count": gen_count,
            "prefill_count": prefill_count,
            "load_s": round(load_s, 1),
            "status": "OK",
        }
    except Exception as e:
        return {
            "model": model,
            "num_ctx": num_ctx,
            "status": f"FAIL: {e}",
        }

def get_gpu_info():
    """Get GPU layer info from ollama ps"""
    import urllib.request
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

def main():
    results = []
    
    for model in MODELS:
        print(f"\n{'='*60}")
        print(f"BENCHMARKING: {model}")
        print(f"{'='*60}")
        
        for ctx in CTX_SIZES:
            print(f"\n  ctx={ctx:,}...", end=" ", flush=True)
            
            # Unload first
            ollama_unload()
            time.sleep(2)
            
            # Run benchmark
            r = run_bench(model, ctx, PROMPT)
            
            if r and r["status"] == "OK":
                # Get GPU info after model is loaded
                gpu = get_gpu_info()
                r.update(gpu)
                print(f"gen={r['gen_tok_s']:.1f} tok/s, prefill={r['prefill_tok_s']:.1f} tok/s, "
                      f"GPU={r.get('gpu_pct', '?')}%, VRAM={r.get('size_vram', 0)/1e9:.1f}G, "
                      f"load={r['load_s']:.1f}s")
            else:
                print(f"FAILED: {r.get('status', 'unknown')}")
                # If we fail at this context, skip larger ones
                results.append(r)
                print(f"  Skipping remaining context sizes for {model}")
                break
            
            results.append(r)
        
        # Unload after each model
        ollama_unload()
    
    # Save results
    with open(RESULTS, "w") as f:
        json.dump(results, f, indent=2)
    
    print(f"\n\nResults saved to {RESULTS}")
    
    # Print summary
    print(f"\n{'='*80}")
    print(f"SUMMARY")
    print(f"{'='*80}")
    print(f"{'Model':<20} {'Ctx':>8} {'Gen tok/s':>10} {'Prefill':>10} {'GPU%':>5} {'VRAM':>8} {'Status'}")
    print("-" * 80)
    for r in results:
        if r["status"] == "OK":
            print(f"{r['model']:<20} {r['num_ctx']:>8,} {r['gen_tok_s']:>10.1f} {r['prefill_tok_s']:>10.1f} "
                  f"{r.get('gpu_pct', '?'):>5} {r.get('size_vram', 0)/1e9:>7.1f}G")
        else:
            print(f"{r['model']:<20} {r['num_ctx']:>8,} {'':>10} {'':>10} {'':>5} {'':>8} {r['status'][:40]}")

if __name__ == "__main__":
    main()
