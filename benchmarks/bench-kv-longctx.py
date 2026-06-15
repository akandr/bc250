#!/usr/bin/env python3
"""G5: KV-width effect on long-context speed.

Benchmarks qwen3.5:9b at 16K and 32K filled context with Q4_0 vs Q8_0 KV cache,
measuring actual gen tok/s to replace architectural projections in tab:kv_quant.

Run on the board:
  python3 /tmp/bench-kv-longctx.py
Output: ~/phase-c-out/results/step-kv-longctx.json
"""

import json, os, re, subprocess, sys, time, urllib.request, urllib.error
from collections import Counter

sys.stdout.reconfigure(line_buffering=True)

MODEL_TAG    = "qwen3.5:9b"
MODEL_NAME   = "qwen3.5-9b-ollama"
KV_TYPES     = ["q4_0", "q8_0"]
TIERS        = [16384, 32768]
RUNS_PER_CELL = 2
N_GEN        = 200

RESULTS_DIR  = os.path.expanduser("~/phase-c-out/results")
SCRATCH_DIR  = os.path.expanduser("~/phase-c-out")
OVERRIDE_CONF = "/etc/systemd/system/ollama.service.d/override.conf"
SAMPLER      = "/tmp/gpu-overlay-sampler.sh"
OUT_PATH     = f"{RESULTS_DIR}/step-kv-longctx.json"

os.makedirs(RESULTS_DIR, exist_ok=True)
os.makedirs(SCRATCH_DIR, exist_ok=True)

# ---------- fill text and needle (verbatim from bench-phase-c.py) ----------
FILL_BLOCK = (
"The evolution of semiconductor manufacturing represents one of the most\n"
"remarkable engineering achievements in human history. From the first transistor\n"
"at Bell Labs in 1947 to modern 3nm process nodes, the industry has maintained\n"
"exponential scaling for over seven decades. Each generation of lithography\n"
"brought new challenges: optical diffraction limits led to immersion lithography,\n"
"then extreme ultraviolet (EUV) sources. The economics are equally staggering --\n"
"a modern fab costs $20 billion or more, yet produces chips at less than a cent\n"
"per transistor. Memory technologies evolved in parallel: from magnetic core to\n"
"SRAM, DRAM, and now 3D NAND flash with hundreds of layers. The interface between\n"
"processor and memory -- the memory wall -- remains the fundamental bottleneck\n"
"in computing performance. Bandwidth grows slower than compute, creating an\n"
"ever-widening gap that architects address through deeper cache hierarchies,\n"
"prefetching, and data-flow optimizations. On the software side, compilers have\n"
"become extraordinarily sophisticated, performing loop vectorization, automatic\n"
"parallelization, and profile-guided optimization. The interaction between\n"
"hardware and software design creates a co-evolution where each enables and\n"
"constrains the other. In artificial intelligence, this manifests as the\n"
"transformer architecture's quadratic attention mechanism -- theoretically elegant\n"
"but practically bounded by memory bandwidth on real hardware. Quantization\n"
"techniques reduce mathematical precision for throughput, enabled by hardware\n"
"that natively supports reduced-precision arithmetic.\n\n"
)

NEEDLE_CODE       = "DELTA-7-VIOLET-MOUNTAIN-93"
NEEDLE_CODE_EARLY = "OMEGA-4-COBALT-RIVER-17"
NEEDLE_LINE = (
    "\n\n*** SPECIAL NOTE: The secret access code is "
    + NEEDLE_CODE
    + ". You will be asked about this code at the end of the document. ***\n\n"
)
NEEDLE_LINE_EARLY = (
    "\n\n*** EARLY NOTE: The first auxiliary code is "
    + NEEDLE_CODE_EARLY
    + ". Remember both codes. ***\n\n"
)
NEEDLE_INSTR = (
    "You will be shown a long technical document. Embedded somewhere in the document "
    "are TWO special notes, each containing a code in the format WORD-NUMBER-WORD-"
    "WORD-NUMBER. After the document, you must answer one question about both codes. "
    "Read the entire document carefully.\n\nDOCUMENT BEGINS:\n\n"
)
NEEDLE_QUESTION = (
    "\n\nDOCUMENT ENDS.\n\nQUESTION: What were the two codes embedded in the EARLY "
    "NOTE and the SPECIAL NOTE? Respond with ONLY the two codes, one per line, in the "
    "order they appeared in the document, nothing else.\n\nANSWER:"
)

def build_needle_prompt(target_tokens):
    overhead = (len(NEEDLE_INSTR) + len(NEEDLE_QUESTION)
                + len(NEEDLE_LINE) + len(NEEDLE_LINE_EARLY))
    char_budget = int(target_tokens * 3.8) - overhead
    char_budget = max(char_budget, len(FILL_BLOCK))
    n_blocks = char_budget // len(FILL_BLOCK) + 1
    fill_full = (FILL_BLOCK * n_blocks)[:char_budget]
    n = len(fill_full)
    def snap_at(target):
        i = fill_full.find("\n", target)
        return i if 0 <= i <= target + 200 else target
    early_at = snap_at(int(n * 0.10))
    mid_at   = snap_at(int(n * 0.50))
    if mid_at <= early_at:
        mid_at = early_at + 1
    body = (fill_full[:early_at]
            + NEEDLE_LINE_EARLY
            + fill_full[early_at:mid_at]
            + NEEDLE_LINE
            + fill_full[mid_at:])
    prompt = NEEDLE_INSTR + body + NEEDLE_QUESTION
    depth_primary = round(mid_at / n * 100, 1) if n else 50.0
    depth_early   = round(early_at / n * 100, 1) if n else 10.0
    return prompt, depth_primary, depth_early

def unique_prompt(base, tag):
    return f"[run:{tag[:32]}]\n" + base

def verify_needle(output, code=NEEDLE_CODE, code_early=NEEDLE_CODE_EARLY):
    if not output:
        return False, "empty_output"
    stripped = output.strip()
    non_ws = re.sub(r"\s+", "", stripped)
    if len(non_ws) < 20:
        return False, f"too_short:{len(non_ws)}"
    def _has_code(target):
        norm_out  = re.sub(r"\s+", "", output.upper())
        norm_code = re.sub(r"\s+", "", target.upper())
        if norm_code in norm_out:
            return True, "exact"
        segs = target.upper().split("-")
        pos = 0
        for s in segs:
            i = norm_out.find(s, pos)
            if i < 0:
                return False, s
            pos = i + len(s)
        return True, "ordered_segments"
    ok_p, why_p = _has_code(code)
    if not ok_p:
        return False, f"missing_primary:{why_p}"
    if code_early is not None:
        ok_e, why_e = _has_code(code_early)
        if not ok_e:
            return False, f"missing_early:{why_e}"
        return True, f"both_present:{why_p}|{why_e}"
    return True, why_p

# ---------- GPU sampler ----------
def start_sampler(out_csv):
    if not os.path.isfile(SAMPLER):
        print(f"  [WARN] sampler not found at {SAMPLER}; no GPU telemetry", flush=True)
        return None
    return subprocess.Popen(["bash", SAMPLER, out_csv],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def stop_sampler(proc, out_csv):
    if proc is None:
        return {"samples": 0}
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    result = {"samples": 0, "throttle_flag": False}
    try:
        with open(out_csv) as f:
            lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        if not lines:
            return result
        result["samples"] = len(lines)
        sclk_vals = []
        temp_vals = []
        throttle_flags = []
        for line in lines:
            parts = line.split(",")
            if len(parts) >= 5:
                try:
                    sclk_vals.append(float(parts[1]))
                    temp_vals.append(float(parts[2]))
                    throttle_flags.append(parts[4].strip().lower() in ("1", "true"))
                except Exception:
                    pass
        if sclk_vals:
            result["sclk_mhz_mean"] = round(sum(sclk_vals) / len(sclk_vals), 1)
            result["sclk_mhz_max"]  = max(sclk_vals)
            result["sclk_mhz_min"]  = min(sclk_vals)
        if temp_vals:
            result["temp_c_max"]  = max(temp_vals)
            result["temp_c_mean"] = round(sum(temp_vals) / len(temp_vals), 1)
        if throttle_flags:
            result["throttle_flag"] = any(throttle_flags)
    except Exception as e:
        result["sampler_error"] = str(e)
    return result

# ---------- Ollama KV type control ----------
def set_kv_type(kv_type):
    """Update override.conf KV_CACHE_TYPE line and restart Ollama."""
    print(f"\n[kv] Setting OLLAMA_KV_CACHE_TYPE={kv_type} ...", flush=True)
    try:
        with open(OVERRIDE_CONF) as f:
            content = f.read()
    except Exception:
        print(f"  [WARN] Could not read {OVERRIDE_CONF}; trying sudo tee approach", flush=True)
        content = ""
    # Replace existing KV_CACHE_TYPE line or insert after [Service]
    new_line = f'Environment="OLLAMA_KV_CACHE_TYPE={kv_type}"'
    if "OLLAMA_KV_CACHE_TYPE" in content:
        content = re.sub(r'Environment="OLLAMA_KV_CACHE_TYPE=[^"]*"', new_line, content)
    else:
        content = content.replace("[Service]", f"[Service]\n{new_line}", 1)
    tmp = "/tmp/_kv_longctx_override.conf"
    with open(tmp, "w") as f:
        f.write(content)
    subprocess.run(f"sudo cp {tmp} {OVERRIDE_CONF}", shell=True, check=True)
    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
    subprocess.run(["sudo", "systemctl", "restart", "ollama"], check=True)
    time.sleep(15)
    env = subprocess.check_output(
        "systemctl show ollama --property=Environment --no-pager", shell=True, text=True
    ).strip()
    print(f"  Ollama env: {env[:200]}", flush=True)

# ---------- single Ollama call ----------
def run_one_ollama(ctx, prompt_text, kv_type, tag):
    overlay_csv = f"{SCRATCH_DIR}/overlay-kvlc-{int(time.time()*1000)}.csv"
    cell_timeout = max(900, int(ctx / 40) + N_GEN * 3 + 180)
    rec = {
        "model": MODEL_NAME,
        "backend": "ollama",
        "kv_type": kv_type,
        "ctx_target": ctx,
        "n_gen_target": N_GEN,
        "label": tag,
        "cell_timeout_s": cell_timeout,
    }
    url = "http://127.0.0.1:11434/api/chat"
    payload = {
        "model": MODEL_TAG,
        "messages": [{"role": "user", "content": prompt_text}],
        "stream": False,
        "think": False,
        "keep_alive": "30s",
        "options": {
            "num_ctx": ctx,
            "num_predict": N_GEN,
            "temperature": 0.0,
            "seed": 42,
        },
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    sampler_proc = start_sampler(overlay_csv)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=cell_timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        wall = time.time() - t0
        rec["wall_s"] = round(wall, 2)
        rec["rc"] = 0
        if "eval_count" in data and data.get("eval_duration"):
            rec["n_gen"] = int(data["eval_count"])
            rec["eval_ms"] = round(data["eval_duration"] / 1e6, 2)
            rec["gen_tok_s"] = round(data["eval_count"] / (data["eval_duration"] / 1e9), 3)
        if "prompt_eval_count" in data and data.get("prompt_eval_duration"):
            rec["n_prompt"] = int(data["prompt_eval_count"])
            rec["prompt_eval_ms"] = round(data["prompt_eval_duration"] / 1e6, 2)
            rec["prefill_tok_s"] = round(data["prompt_eval_count"] / (data["prompt_eval_duration"] / 1e9), 3)
        if data.get("load_duration"):
            rec["load_ms"] = round(data["load_duration"] / 1e6, 2)
        msg = data.get("message") or {}
        rec["output"] = (msg.get("content") or "").strip()[:2000]
        rec["status"] = "ok" if "gen_tok_s" in rec else "parse_fail"
    except urllib.error.HTTPError as e:
        rec["wall_s"] = round(time.time() - t0, 2)
        rec["status"] = "http_error"
        rec["error"] = f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:300]}"
    except Exception as e:
        rec["wall_s"] = round(time.time() - t0, 2)
        rec["status"] = f"exception:{type(e).__name__}"
        rec["error"] = str(e)[:300]
    rec["gpu_overlay"] = stop_sampler(sampler_proc, overlay_csv)
    return rec

# ---------- aggregate ----------
def aggregate(runs):
    gen_vals  = [r["gen_tok_s"]    for r in runs if r.get("status") == "ok" and "gen_tok_s" in r]
    pre_vals  = [r["prefill_tok_s"] for r in runs if r.get("status") == "ok" and "prefill_tok_s" in r]
    agg = {}
    if gen_vals:
        agg["gen_tok_s_median"]  = round(sorted(gen_vals)[len(gen_vals) // 2], 2)
        agg["gen_tok_s_mean"]    = round(sum(gen_vals) / len(gen_vals), 2)
        if len(gen_vals) > 1:
            cv = (max(gen_vals) - min(gen_vals)) / agg["gen_tok_s_mean"] * 100
            agg["gen_tok_s_cv_pct"] = round(cv, 2)
    if pre_vals:
        agg["prefill_tok_s_median"] = round(sorted(pre_vals)[len(pre_vals) // 2], 2)
    agg["n_ok"] = len(gen_vals)
    agg["n_runs"] = len(runs)
    return agg

# ---------- main ----------
def main():
    print("=" * 70)
    print("G5: KV-width long-context speed benchmark")
    print(f"Model: {MODEL_TAG}  Tiers: {TIERS}  KV types: {KV_TYPES}")
    print(f"Runs per cell: {RUNS_PER_CELL}  n_gen: {N_GEN}")
    print(f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # preflight
    try:
        cu_mode = open("/sys/module/amdgpu/parameters/bc250_cc_write_mode").read().strip()
        oberon  = subprocess.check_output(["systemctl", "is-active", "oberon-governor"], text=True).strip()
        print(f"[preflight] cu_mode={cu_mode}  oberon={oberon}", flush=True)
        if cu_mode != "0":
            print("[preflight] WARN: not in 24-CU mode!", flush=True)
        if oberon != "active":
            print("[preflight] WARN: oberon-governor not active!", flush=True)
    except Exception as e:
        print(f"[preflight] {e}", flush=True)

    all_results = {}

    for kv_type in KV_TYPES:
        print(f"\n{'='*50}")
        print(f"  KV TYPE: {kv_type}")
        print(f"{'='*50}")
        set_kv_type(kv_type)

        kv_results = {}
        for ctx in TIERS:
            prompt_base, depth_pct, depth_early = build_needle_prompt(ctx)
            prompt_chars = len(prompt_base)
            cell_timeout = max(900, int(ctx / 40) + N_GEN * 3 + 180)
            print(f"\n  [ctx={ctx}] prompt_chars={prompt_chars}  timeout={cell_timeout}s", flush=True)

            runs = []
            for i in range(RUNS_PER_CELL):
                tag = f"kv-lc-{kv_type}-{ctx//1024}k-r{i+1}"
                p = unique_prompt(prompt_base, tag)
                print(f"    run {i+1}/{RUNS_PER_CELL}  tag={tag}", flush=True)
                rec = run_one_ollama(ctx, p, kv_type, tag)
                npass, reason = verify_needle(rec.get("output", ""))
                rec["needle_pass"] = npass
                rec["needle_reason"] = reason
                rec["needle_depth_pct"] = depth_pct
                runs.append(rec)
                print(f"      status={rec.get('status')}  gen={rec.get('gen_tok_s')} t/s  "
                      f"pp={rec.get('prefill_tok_s')} t/s  n_prompt={rec.get('n_prompt')}  "
                      f"needle={npass} ({reason})  "
                      f"temp_max={rec.get('gpu_overlay',{}).get('temp_c_max')}  "
                      f"throttle={rec.get('gpu_overlay',{}).get('throttle_flag')}", flush=True)

            agg = aggregate(runs)
            print(f"  [ctx={ctx}] AGG  gen_median={agg.get('gen_tok_s_median')} t/s  "
                  f"pp_median={agg.get('prefill_tok_s_median')} t/s  "
                  f"cv={agg.get('gen_tok_s_cv_pct')}%  n_ok={agg.get('n_ok')}/{agg.get('n_runs')}", flush=True)

            kv_results[str(ctx)] = {"runs": runs, "aggregate": agg}

        all_results[kv_type] = kv_results

    # restore q4_0 (production config)
    print("\n[restore] Restoring production KV config (q4_0) ...", flush=True)
    set_kv_type("q4_0")
    print("[restore] Done.", flush=True)

    # print summary table
    print(f"\n{'='*70}")
    print("SUMMARY  (gen tok/s, median over 2 runs)")
    print(f"{'ctx':<8}", end="")
    for kv in KV_TYPES:
        print(f"  {kv:>12}", end="")
    print()
    print("-" * (8 + 14 * len(KV_TYPES)))
    for ctx in TIERS:
        print(f"{ctx//1024}K{'':<6}", end="")
        for kv in KV_TYPES:
            agg = all_results.get(kv, {}).get(str(ctx), {}).get("aggregate", {})
            g = agg.get("gen_tok_s_median")
            print(f"  {f'{g:.1f} t/s' if g else 'FAIL/SKIP':>12}", end="")
        print()

    output = {
        "step": "kv-longctx",
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": MODEL_NAME,
        "model_tag": MODEL_TAG,
        "kv_types": KV_TYPES,
        "tiers": TIERS,
        "runs_per_cell": RUNS_PER_CELL,
        "n_gen": N_GEN,
        "results": all_results,
    }
    with open(OUT_PATH, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n[done] Saved to {OUT_PATH}")

if __name__ == "__main__":
    main()
