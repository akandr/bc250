#!/usr/bin/env python3
"""BC-250 LLM Benchmark Suite — March 2026
Tests all models at multiple context sizes on 16.5 GiB Vulkan.
Measures: load time, prefill tok/s, generation tok/s, GPU layers, total VRAM.
"""

import json, time, subprocess, sys, os, signal

OLLAMA = "http://localhost:11434"

# Models to test — ordered by size
MODELS = [
    "qwen2.5:3b",
    "qwen3:8b",
    "huihui_ai/qwen3-abliterated:8b",
    "qwen2.5:7b",
    "qwen2.5-coder:7b",
    "llama3.1:8b",
    "mannix/llama3.1-8b-lexi:latest",
    "huihui_ai/seed-coder-abliterate:latest",
    "gemma2:9b",
    "mistral-nemo:12b",
    "qwen3:14b",
    "huihui_ai/qwen3-abliterated:14b",
    "phi4:14b",
    "hf.co/unsloth/Qwen3-30B-A3B-GGUF:Q2_K",
]

# Context sizes to test — adapt per model size
CONTEXT_SIZES_SMALL = [4096, 8192, 16384, 24576, 32768, 49152, 65536]  # ≤8B
CONTEXT_SIZES_MED   = [4096, 8192, 16384, 24576, 32768, 49152]         # 9-12B
CONTEXT_SIZES_LARGE = [4096, 8192, 16384, 24576, 32768, 40960]         # 14B
CONTEXT_SIZES_XL    = [4096, 8192, 16384, 24576]                       # 30B

# Test prompt — moderate size to measure both prefill and generation
TEST_PROMPT = "Write a detailed technical analysis of memory management in modern operating systems, covering virtual memory, paging, segmentation, and garbage collection. Include comparisons between Linux, Windows, and macOS approaches."

# Short prompt for quick speed measurement
QUICK_PROMPT = "Explain quantum computing in one paragraph."

import urllib.request

def ollama_api(endpoint, data=None, timeout=300):
    url = f"{OLLAMA}{endpoint}"
    req = urllib.request.Request(url, method="POST" if data else "GET")
    if data:
        req.add_header("Content-Type", "application/json")
        body = json.dumps(data).encode()
    else:
        body = None
    try:
        with urllib.request.urlopen(req, body, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}

def ollama_generate(model, prompt, num_ctx, timeout=300):
    """Generate with streaming disabled, return timing info."""
    data = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"num_ctx": num_ctx, "num_predict": 100},
        "keep_alive": "1m",
    }
    url = f"{OLLAMA}/api/generate"
    req = urllib.request.Request(url, method="POST")
    req.add_header("Content-Type", "application/json")
    body = json.dumps(data).encode()
    try:
        with urllib.request.urlopen(req, body, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}

def unload_model():
    """Unload any loaded model."""
    # List running models
    try:
        ps = ollama_api("/api/ps")
        if ps and "models" in ps:
            for m in ps["models"]:
                name = m.get("name", "")
                if name:
                    ollama_api("/api/generate", {"model": name, "keep_alive": 0})
                    time.sleep(2)
    except:
        pass
    time.sleep(3)

def get_gpu_mem():
    """Read GTT used from sysfs."""
    try:
        with open("/sys/class/drm/card1/device/mem_info_gtt_used") as f:
            return int(f.read().strip()) / (1024**3)  # GiB
    except:
        return -1

def get_model_info(model):
    """Get model parameter count string."""
    try:
        result = subprocess.run(["ollama", "show", model], capture_output=True, text=True, timeout=10)
        for line in result.stdout.splitlines():
            if "parameters" in line.lower():
                parts = line.strip().split()
                if len(parts) >= 2:
                    return parts[-1]
    except:
        pass
    return "?"

def get_context_sizes(model):
    """Pick context sizes based on model size."""
    info = get_model_info(model)
    if "30" in info:
        return CONTEXT_SIZES_XL
    elif "14" in info:
        return CONTEXT_SIZES_LARGE
    elif "12" in info or "9" in info:
        return CONTEXT_SIZES_MED
    else:
        return CONTEXT_SIZES_SMALL

def run_benchmark(model, num_ctx):
    """Run a single benchmark: load model, generate, measure."""
    result = {
        "model": model,
        "num_ctx": num_ctx,
        "status": "unknown",
    }

    # Unload first
    unload_model()
    gtt_before = get_gpu_mem()

    # Generate (this triggers model load + inference)
    t0 = time.time()
    resp = ollama_generate(model, QUICK_PROMPT, num_ctx, timeout=300)
    t_total = time.time() - t0

    if "error" in resp:
        result["status"] = f"FAIL: {resp['error'][:80]}"
        print(f"  FAIL: {resp['error'][:80]}")
        return result

    gtt_after = get_gpu_mem()

    # Extract metrics
    eval_count = resp.get("eval_count", 0)
    eval_duration = resp.get("eval_duration", 0) / 1e9  # ns → s
    prompt_eval_count = resp.get("prompt_eval_count", 0)
    prompt_eval_duration = resp.get("prompt_eval_duration", 0) / 1e9

    gen_toks = eval_count / eval_duration if eval_duration > 0 else 0
    prefill_toks = prompt_eval_count / prompt_eval_duration if prompt_eval_duration > 0 else 0
    load_duration = resp.get("load_duration", 0) / 1e9

    # Check ollama ps for GPU layers
    ps = ollama_api("/api/ps")
    gpu_pct = "?"
    vram_str = "?"
    if ps and "models" in ps:
        for m in ps["models"]:
            if model in m.get("name", ""):
                details = m.get("details", {})
                sd = m.get("size_vram", 0)
                st = m.get("size", 0)
                gpu_pct = f"{sd/st*100:.0f}%" if st > 0 else "?"
                vram_str = f"{sd/(1024**3):.1f}G"
                break

    result.update({
        "status": "OK",
        "gen_tok_s": round(gen_toks, 1),
        "prefill_tok_s": round(prefill_toks, 1),
        "load_s": round(load_duration, 1),
        "total_s": round(t_total, 1),
        "gtt_used_gib": round(gtt_after, 2),
        "gtt_delta_gib": round(gtt_after - gtt_before, 2),
        "gpu_pct": gpu_pct,
        "vram": vram_str,
        "eval_tokens": eval_count,
        "prompt_tokens": prompt_eval_count,
    })

    result["status"] = "OK"
    return result

def main():
    print("=" * 80)
    print("BC-250 LLM Benchmark Suite — March 2026")
    print(f"Vulkan 16.5 GiB · Ollama 0.16.1 · {time.strftime('%Y-%m-%d %H:%M')}")
    print("=" * 80)

    results = []

    for model in MODELS:
        ctx_sizes = get_context_sizes(model)
        info = get_model_info(model)
        print(f"\n{'─'*60}")
        print(f"MODEL: {model} ({info})")
        print(f"Context sizes to test: {ctx_sizes}")
        print(f"{'─'*60}")

        for ctx in ctx_sizes:
            ctx_k = f"{ctx//1024}K"
            print(f"  Testing {ctx_k} context...", end=" ", flush=True)

            r = run_benchmark(model, ctx)
            results.append(r)

            if r["status"] == "OK":
                print(f"✅ {r['gen_tok_s']} tok/s gen, {r['prefill_tok_s']} tok/s prefill, "
                      f"GPU: {r['gpu_pct']}, GTT: {r['gtt_used_gib']:.1f} GiB, "
                      f"Load: {r['load_s']}s")
            else:
                print(f"❌ {r['status']}")
                # If this context failed, skip larger contexts for this model
                print(f"  Skipping remaining context sizes for {model}")
                break

    # Save results
    outfile = "/opt/netscan/tmp/bench-llm-results.json"
    with open(outfile, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {outfile}")

    # Print summary table
    print("\n" + "=" * 100)
    print("SUMMARY TABLE")
    print("=" * 100)
    print(f"{'Model':<45} {'Ctx':>5} {'Gen t/s':>8} {'Pre t/s':>8} {'GPU':>5} {'GTT GiB':>8} {'Status':>8}")
    print("-" * 100)
    for r in results:
        ctx_k = f"{r['num_ctx']//1024}K"
        if r["status"] == "OK":
            print(f"{r['model']:<45} {ctx_k:>5} {r['gen_tok_s']:>8.1f} {r['prefill_tok_s']:>8.1f} "
                  f"{r['gpu_pct']:>5} {r['gtt_used_gib']:>8.1f} {'OK':>8}")
        else:
            print(f"{r['model']:<45} {ctx_k:>5} {'—':>8} {'—':>8} {'—':>5} {'—':>8} {'FAIL':>8}")

if __name__ == "__main__":
    main()
