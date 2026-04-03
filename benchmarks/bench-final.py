#!/usr/bin/env python3
"""Final benchmark: llama-bench with OOM protection at all context sizes.
Compares raw llama.cpp (Vulkan) vs Ollama on BC-250."""
import subprocess, json, time, os, sys

LLAMA_BENCH = "/opt/llama.cpp/build/bin/llama-bench"
MODELS = {
    "moe-coder-30b": "/opt/models/moe-coder-30b-iq2m.gguf",
    "deepseek-r1-14b": "/opt/models/deepseek-r1-14b.gguf",
}
# Test matrix: (model_key, ctx_size)
# 32K+ crashes the system for BOTH models (model + KV cache exceeds 16GB UMA)
# deepseek 32K: 8.4GB model + KV → system freeze
# MoE 32K: 10.1GB model + KV → system freeze
TESTS = [
    ("deepseek-r1-14b", 4096),
    ("deepseek-r1-14b", 16384),
    ("moe-coder-30b", 4096),
    ("moe-coder-30b", 16384),
]
GEN_TOKENS = 128
COOLDOWN = 15
# llama-bench uses -p (prompt tokens) and -n (gen tokens)
# Context size is implicitly pp + tg, no -c flag exists
results = []
RF = "/tmp/bench-final-results.json"


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def save():
    with open(RF, "w") as f:
        json.dump(results, f, indent=2)


def mem_info():
    """Return dict with mem_avail_mb, swap_used_mb."""
    d = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if parts[0] == "MemAvailable:":
                    d["mem_avail_mb"] = int(parts[1]) // 1024
                elif parts[0] == "SwapTotal:":
                    d["swap_total_mb"] = int(parts[1]) // 1024
                elif parts[0] == "SwapFree:":
                    d["swap_free_mb"] = int(parts[1]) // 1024
    except Exception:
        pass
    d["swap_used_mb"] = d.get("swap_total_mb", 0) - d.get("swap_free_mb", 0)
    return d


def run_llama_bench(model_path, ctx, ngl=99):
    """Run llama-bench via a wrapper script that sets OOM protection."""
    pp = ctx - GEN_TOKENS  # fill context: pp + tg = ctx
    # Write a temp wrapper that sets oom_score_adj then execs llama-bench
    wrapper = f"""#!/bin/bash
echo -1000 > /proc/self/oom_score_adj 2>/dev/null
exec {LLAMA_BENCH} \\
  -m {model_path} \\
  -ngl {ngl} \\
  -fa 1 \\
  -ctk q4_0 -ctv q4_0 \\
  -r 1 \\
  -p {pp} \\
  -n {GEN_TOKENS} \\
  -o json
"""
    wrapper_path = "/tmp/bench-wrapper.sh"
    with open(wrapper_path, "w") as f:
        f.write(wrapper)
    os.chmod(wrapper_path, 0o755)

    # Timeout: bigger ctx = more time. 10 min per test max.
    timeout = 600
    start = time.time()
    try:
        r = subprocess.run(
            ["sudo", "bash", wrapper_path],
            capture_output=True, text=True, timeout=timeout
        )
        wall = time.time() - start
        return r.stdout, r.stderr, r.returncode, wall
    except subprocess.TimeoutExpired:
        return "", "TIMEOUT", -1, time.time() - start


def parse_llama_bench_json(stdout):
    """Parse llama-bench JSON output, return (pp_tok_s, tg_tok_s) or None."""
    try:
        data = json.loads(stdout)
        pp_tok_s = None
        tg_tok_s = None
        for entry in data:
            # llama-bench JSON uses "test" field with values "pp" or "tg"
            # and "avg_ts" for average tokens/second
            test = entry.get("test", "")
            if test == "pp":
                pp_tok_s = entry.get("avg_ts")
            elif test == "tg":
                tg_tok_s = entry.get("avg_ts")
        # If field names are different, try alternatives
        if pp_tok_s is None and tg_tok_s is None and len(data) >= 2:
            # Try n_prompt / n_gen based detection
            for entry in data:
                if entry.get("n_prompt", 0) > 0 and entry.get("n_gen", 0) == 0:
                    pp_tok_s = entry.get("avg_ts") or entry.get("speed")
                elif entry.get("n_gen", 0) > 0:
                    tg_tok_s = entry.get("avg_ts") or entry.get("speed")
        return pp_tok_s, tg_tok_s
    except (json.JSONDecodeError, TypeError, KeyError):
        return None, None


def main():
    log("=" * 60)
    log("llama-bench comparison benchmark (with OOM protection)")
    log("=" * 60)

    m = mem_info()
    log(f"Memory: {m.get('mem_avail_mb', '?')}MB avail, swap used: {m.get('swap_used_mb', '?')}MB")

    # Verify models exist
    for name, path in MODELS.items():
        if not os.path.exists(path):
            log(f"ERROR: Model not found: {path}")
            sys.exit(1)
        size_gb = os.path.getsize(path) / (1024**3)
        log(f"Model: {name} = {size_gb:.1f} GB")

    log(f"\nRunning {len(TESTS)} tests...")
    log(f"{'Model':<18} {'Ctx':>5} {'PP tok/s':>10} {'TG tok/s':>10} {'Wall':>7} {'Swap delta':>10}")
    log("-" * 65)

    for model_key, ctx in TESTS:
        model_path = MODELS[model_key]
        log(f"\nStarting: {model_key} ctx={ctx//1024}K")

        m_pre = mem_info()
        swap_pre = m_pre.get("swap_used_mb", 0)

        # Drop caches before each test
        subprocess.run("sync; echo 3 > /proc/sys/vm/drop_caches",
                       shell=True, capture_output=True)
        time.sleep(3)

        stdout, stderr, rc, wall = run_llama_bench(model_path, ctx)

        m_post = mem_info()
        swap_post = m_post.get("swap_used_mb", 0)
        swap_delta = swap_post - swap_pre

        result = {
            "backend": "llama.cpp",
            "model": model_key,
            "ctx": ctx,
            "rc": rc,
            "wall_s": round(wall, 1),
            "swap_pre": swap_pre,
            "swap_post": swap_post,
        }

        if rc == 0:
            pp_tok_s, tg_tok_s = parse_llama_bench_json(stdout)
            if pp_tok_s is not None:
                result["pp_tok_s"] = round(pp_tok_s, 2)
                result["tg_tok_s"] = round(tg_tok_s, 2)
                log(f"{model_key:<18} {ctx//1024:>4}K {pp_tok_s:>10.2f} {tg_tok_s:>10.2f} {wall:>6.0f}s {swap_delta:>+5}M")
            else:
                result["error"] = "parse_failed"
                result["stdout_head"] = stdout[:1000]
                log(f"{model_key:<18} {ctx//1024:>4}K  PARSE FAIL  {wall:>6.0f}s")
                log(f"  stdout preview: {stdout[:200]}")
        elif rc == -1:
            result["error"] = "timeout"
            log(f"{model_key:<18} {ctx//1024:>4}K  TIMEOUT     {wall:>6.0f}s")
        else:
            result["error"] = f"exit_{rc}"
            result["stderr_head"] = stderr[:500]
            log(f"{model_key:<18} {ctx//1024:>4}K  FAIL rc={rc} {wall:>6.0f}s")

        results.append(result)
        save()

        # Cooldown between tests
        log(f"  Cooling down {COOLDOWN}s...")
        time.sleep(COOLDOWN)

    log("\n" + "=" * 65)
    log("SUMMARY")
    log("=" * 65)
    log(f"{'Model':<18} {'Ctx':>5} {'PP tok/s':>10} {'TG tok/s':>10} {'Status':>8}")
    log("-" * 55)
    for r in results:
        pp = r.get("pp_tok_s", "-")
        tg = r.get("tg_tok_s", "-")
        status = "OK" if "pp_tok_s" in r else r.get("error", "?")
        pp_s = f"{pp:>10.2f}" if isinstance(pp, (int, float)) else f"{pp:>10}"
        tg_s = f"{tg:>10.2f}" if isinstance(tg, (int, float)) else f"{tg:>10}"
        log(f"{r['model']:<18} {r['ctx']//1024:>4}K {pp_s} {tg_s} {status:>8}")

    save()
    log(f"\nResults saved: {RF}")
    log("DONE")


if __name__ == "__main__":
    main()
