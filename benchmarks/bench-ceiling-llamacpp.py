#!/usr/bin/env python3
"""bench-ceiling-llamacpp.py — push every model's context to the OOM/timeout
ceiling using llama-bench (b9265, prefer_host=1, q4_0 KV, fa 1, ngl 99).

Per-model ladder ABOVE the overnight max. REPEATS=1 because a single 64K cell
on the 14B can already take 20+ minutes.

Crash safety: atomic save after every cell, resumable, 30 min per-cell timeout,
once a model fails it's dropped from further (larger-ctx) tiers.
"""
import json, os, subprocess, sys, time

RESULTS = "/tmp/bench-ceiling-llamacpp-results.json"
LOG     = "/tmp/bench-ceiling-llamacpp.log"
STATE   = "/tmp/bench-ceiling-llamacpp-state.json"

BUILD_BIN = "/opt/llama.cpp-b9265/build/bin/llama-bench"
BUILD_TAG = "b9265"

ENV_EXTRA = {"GGML_VK_PREFER_HOST_MEMORY": "1"}

LADDERS = {
    "deepseek-r1-14b":      [24576, 32768, 49152, 65536],
    "qwen3-coder-30b-iq2m": [24576, 32768, 49152, 65536],
    "qwen3.5-35b-iq2m":     [24576, 32768, 49152, 65536],
    "qwen3.6-35b-iq2m":     [24576, 32768, 49152, 65536],
    "gpt-oss-20b-mxfp4":    [32768, 49152, 65536, 98304, 131072],
    "granite-4.0-h-tiny":   [98304, 131072, 196608, 262144],
}

MODEL_PATHS = {
    "deepseek-r1-14b":      "/opt/models/deepseek-r1-14b.gguf",
    "qwen3-coder-30b-iq2m": "/opt/models/moe-coder-30b-iq2m.gguf",
    "qwen3.5-35b-iq2m":     "/opt/models/qwen3.5-35b-a3b-iq2m.gguf",
    "qwen3.6-35b-iq2m":     "/opt/models/qwen3.6-35b-a3b-iq2m.gguf",
    "gpt-oss-20b-mxfp4":    "/opt/models/gpt-oss-20b-mxfp4.gguf",
    "granite-4.0-h-tiny":   "/opt/models/granite-4.0-h-tiny-q4km.gguf",
}

MODEL_ORDER = [
    "granite-4.0-h-tiny",
    "gpt-oss-20b-mxfp4",
    "deepseek-r1-14b",
    "qwen3-coder-30b-iq2m",
    "qwen3.5-35b-iq2m",
    "qwen3.6-35b-iq2m",
]

GEN          = 128
REPEATS      = 1
COOLDOWN_S   = 60
CELL_TIMEOUT = 1800

results = []
done    = set()
broken  = set()

def log(m):
    line = f"[{time.strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    try:
        with open(LOG, "a") as f: f.write(line + "\n")
    except Exception: pass

def atomic_save():
    try:
        tmp = RESULTS + ".tmp"
        with open(tmp, "w") as f: json.dump(results, f, indent=2)
        os.replace(tmp, RESULTS)
        with open(STATE, "w") as f:
            json.dump({"broken": sorted(broken), "cells": len(results), "ts": time.time()}, f, indent=2)
    except Exception as e:
        log(f"  SAVE ERROR: {e}")

def load_existing():
    if not os.path.exists(RESULTS): return
    try:
        prior = json.load(open(RESULTS))
    except Exception as e:
        log(f"resume read failed: {e}"); return
    for r in prior:
        results.append(r)
        m, c = r.get("model"), r.get("ctx_pp")
        if m and c:
            if r.get("status") not in ("skip_no_model", "skip_broken_earlier"):
                done.add((m, c))
            if r.get("status") in ("fail", "timeout") or str(r.get("status","")).startswith("exception"):
                broken.add(m)
    log(f"resumed: {len(results)} cells; broken={sorted(broken)}")

def mem_snapshot():
    out = {}
    try:
        for k, p in (("gtt_used","/sys/class/drm/card1/device/mem_info_gtt_used"),
                     ("vram_used","/sys/class/drm/card1/device/mem_info_vram_used")):
            with open(p) as f: out[k+"_gib"] = round(int(f.read().strip())/1024**3, 3)
    except Exception: pass
    try:
        with open("/proc/meminfo") as f:
            for ln in f:
                if ln.startswith("MemAvailable:"):
                    out["mem_avail_mib"] = int(ln.split()[1])//1024; break
    except Exception: pass
    return out

def run_cell(model_name, ctx_pp):
    path = MODEL_PATHS[model_name]
    if not os.path.exists(path):
        rec = {"build": BUILD_TAG, "model": model_name, "ctx_pp": ctx_pp,
               "env": "prefer_host", "status": "skip_no_model"}
        results.append(rec); atomic_save()
        log(f"  SKIP no model file: {path}")
        return "skip"
    if (model_name, ctx_pp) in done:
        log(f"  SKIP already in prior results: {model_name}@{ctx_pp}")
        return "skip_resume"
    if model_name in broken:
        rec = {"build": BUILD_TAG, "model": model_name, "ctx_pp": ctx_pp,
               "env": "prefer_host", "status": "skip_broken_earlier"}
        results.append(rec); atomic_save()
        log(f"  SKIP {model_name} broken earlier")
        return "skip"

    env = os.environ.copy(); env.update(ENV_EXTRA)
    cmd = [BUILD_BIN, "-m", path, "-ngl", "99", "-fa", "1",
           "-ctk", "q4_0", "-ctv", "q4_0",
           "-r", str(REPEATS), "-p", str(ctx_pp), "-n", str(GEN), "-o", "json"]
    log(f"RUN model={model_name} ctx={ctx_pp}  (timeout {CELL_TIMEOUT}s)")
    log(f"    mem_before={mem_snapshot()}")
    start = time.time(); parsed = None; status = "ok"; rc = None; out = ""; err = ""
    try:
        cp = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=CELL_TIMEOUT)
        wall = time.time() - start
        out, err, rc = cp.stdout, cp.stderr, cp.returncode
        i = out.find("[")
        if i >= 0:
            try: parsed = json.loads(out[i:])
            except Exception as e: log(f"  parse error: {e}")
        if rc != 0 or parsed is None: status = "fail"
    except subprocess.TimeoutExpired:
        wall = time.time() - start; status = "timeout"
        log(f"  TIMEOUT after {CELL_TIMEOUT}s")
        subprocess.run(["pkill", "-9", "-f", "llama-bench"], check=False)
        time.sleep(5)
    except Exception as e:
        wall = time.time() - start; status = f"exception:{type(e).__name__}"
        log(f"  EXCEPTION: {e}")

    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "build": BUILD_TAG, "model": model_name, "env": "prefer_host",
           "env_extra": ENV_EXTRA, "ctx_pp": ctx_pp, "gen": GEN, "repeats": REPEATS,
           "wall_s": round(wall, 2), "rc": rc, "status": status,
           "mem_after": mem_snapshot()}
    if parsed:
        rec["raw"] = parsed
        for row in parsed:
            log(f"   {row.get('test','?')}: {row.get('avg_ts',0):.2f} t/s  (sd {row.get('stddev_ts',0):.2f})")
    else:
        rec["stdout_tail"] = out[-2000:] if out else ""
        rec["stderr_tail"] = err[-2000:] if err else ""
        log(f"  FAIL rc={rc} wall={wall:.1f}s  — {model_name} dropped from further tiers")
        broken.add(model_name)
    results.append(rec); atomic_save()
    return status

def main():
    log("=== bench-ceiling-llamacpp starting ===")
    log(f"build: {BUILD_TAG}  env: prefer_host")
    if not os.path.exists(BUILD_BIN):
        log(f"FATAL: bench binary missing: {BUILD_BIN}"); sys.exit(1)
    load_existing()
    all_ctx = sorted({c for L in LADDERS.values() for c in L})
    for ctx in all_ctx:
        log(f"################  CEILING CTX {ctx}  ################")
        for m in MODEL_ORDER:
            if ctx not in LADDERS[m]: continue
            run_cell(m, ctx)
            log(f"cooldown {COOLDOWN_S}s")
            time.sleep(COOLDOWN_S)
        log(f"=== CEILING CTX {ctx} done; broken={sorted(broken)} ===")
    log("=== bench-ceiling-llamacpp finished cleanly ===")

if __name__ == "__main__":
    main()
