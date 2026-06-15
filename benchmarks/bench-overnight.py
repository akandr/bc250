#!/usr/bin/env python3
"""bench-overnight.py — unsupervised overnight context-sweep + multi-model + multi-build.

Crash-safety design:
  * ctx-MAJOR iteration: do ALL (model, build, env) at ctx=N before moving to N+1.
    => If the box freezes at a high ctx, every smaller-ctx datum is already
       persisted to JSON.
  * Per-cell hard timeout (subprocess.run timeout=).
  * Per-(model,build) "killed" set: if a cell crashes/times-out, that model+build
    is excluded from larger ctx tiers (assume larger will also crash).
  * Conservative ctx caps per model class — we know 32K freezes the 14B/30B
    UMA setup. Granite (Mamba hybrid) gets the high tiers; others stop at 16K.
  * Atomic save: temp-file + rename after every cell.
  * Aggressive cooldown between cells (45s) — lets GTT settle.
  * No ollama / queue-runner / signal-cli — caller stops them first.

Skips:
  * Any cell whose model file does not exist on disk.
  * Any (model, build) marked killed by an earlier failure.
"""
import json, os, subprocess, sys, time, signal

# ---------------- config ----------------
RESULTS = "/tmp/bench-overnight-results.json"
LOG     = "/tmp/bench-overnight.log"
STATE   = "/tmp/bench-overnight-state.json"

BUILDS = {
    "b8200": "/opt/llama.cpp/build/bin/llama-bench",
    "b9165": "/opt/llama.cpp-b9165/build/bin/llama-bench",
}

# Model definitions. max_ctx is the LARGEST ctx tier to attempt for this model.
# Anything above max_ctx is skipped automatically.
MODELS = {
    "deepseek-r1-14b":      {"path": "/opt/models/deepseek-r1-14b.gguf",            "max_ctx": 16384},
    "qwen3-coder-30b-iq2m": {"path": "/opt/models/moe-coder-30b-iq2m.gguf",         "max_ctx": 16384},
    "qwen3.5-35b-iq2m":     {"path": "/opt/models/qwen3.5-35b-a3b-iq2m.gguf",       "max_ctx": 16384},
    "qwen3.6-35b-iq2m":     {"path": "/opt/models/qwen3.6-35b-a3b-iq2m.gguf",       "max_ctx": 16384},
    "gpt-oss-20b-mxfp4":    {"path": "/opt/models/gpt-oss-20b-mxfp4.gguf",          "max_ctx": 24576},
    "granite-4.0-h-tiny":   {"path": "/opt/models/granite-4.0-h-tiny-q4km.gguf",    "max_ctx": 65536},
}

# Order: smallest model first within each ctx tier — if the big one crashes
# the box, the small results are already saved.
MODEL_ORDER = [
    "granite-4.0-h-tiny",
    "deepseek-r1-14b",
    "gpt-oss-20b-mxfp4",
    "qwen3-coder-30b-iq2m",
    "qwen3.5-35b-iq2m",
    "qwen3.6-35b-iq2m",
]

# ctx tiers, ascending. Each tier processes ALL surviving (model, build, env)
# combinations before moving on.
CTX_TIERS = [2048, 4096, 8192, 16384, 24576, 32768, 49152, 65536]

# Best env from earlier sweep; default included for one-tier sanity check.
ENVS = {
    "default":     {},
    "prefer_host": {"GGML_VK_PREFER_HOST_MEMORY": "1"},
}

GEN          = 128
REPEATS      = 2
COOLDOWN_S   = 45
CELL_TIMEOUT = 900   # 15 min per cell

# ---------------- impl ----------------

results = []
killed  = set()  # set of (model, build) pairs to skip after a failure
done    = set()  # set of (build, model, env, ctx_pp) tuples already attempted

def load_existing():
    """Resume support: read prior results and rebuild done/killed sets."""
    if not os.path.exists(RESULTS):
        return
    try:
        prior = json.load(open(RESULTS))
    except Exception as e:
        print(f"could not load prior results: {e}")
        return
    for r in prior:
        results.append(r)
        key = (r.get("build"), r.get("model"), r.get("env"), r.get("ctx_pp"))
        # Retry "skip_no_model" / "skip_above_model_cap" / "skip_killed_earlier"
        # in case the model became available or the kill list changed.
        retryable = {"skip_no_model", "skip_above_model_cap", "skip_killed_earlier"}
        if all(k is not None for k in key) and r.get("status") not in retryable:
            done.add(key)
        # rebuild kill list from prior failures
        if r.get("status") in ("fail", "timeout") or (
            isinstance(r.get("status"), str) and r["status"].startswith("exception")):
            killed.add((r.get("model"), r.get("build")))
    print(f"resumed: {len(results)} prior cells, killed pairs: {sorted(killed)}")

def log(m):
    line = f"[{time.strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

def atomic_save():
    try:
        tmp = RESULTS + ".tmp"
        with open(tmp, "w") as f:
            json.dump(results, f, indent=2)
        os.replace(tmp, RESULTS)
        with open(STATE, "w") as f:
            json.dump({
                "killed": [list(k) for k in killed],
                "completed_cells": len(results),
                "ts": time.time(),
            }, f, indent=2)
    except Exception as e:
        log(f"  SAVE ERROR: {e}")

def mem_snapshot():
    out = {}
    try:
        for k, p in (
            ("gtt_used",  "/sys/class/drm/card1/device/mem_info_gtt_used"),
            ("vram_used", "/sys/class/drm/card1/device/mem_info_vram_used"),
        ):
            with open(p) as f:
                out[k + "_gib"] = round(int(f.read().strip()) / 1024**3, 3)
    except Exception:
        pass
    try:
        with open("/proc/meminfo") as f:
            for ln in f:
                if ln.startswith("MemAvailable:"):
                    out["mem_avail_mib"] = int(ln.split()[1]) // 1024
                    break
    except Exception:
        pass
    return out

def run_cell(build, model_name, env_name, ctx_pp):
    minfo = MODELS[model_name]
    binary = BUILDS[build]

    if not os.path.exists(minfo["path"]):
        rec = {"build": build, "model": model_name, "env": env_name,
               "ctx_pp": ctx_pp, "status": "skip_no_model"}
        results.append(rec); atomic_save()
        log(f"  SKIP no model file: {minfo['path']}")
        return "skip"

    # Resume: skip cells already attempted in a prior run
    if (build, model_name, env_name, ctx_pp) in done:
        log(f"  SKIP already in prior results")
        return "skip_resume"

    if (model_name, build) in killed:
        rec = {"build": build, "model": model_name, "env": env_name,
               "ctx_pp": ctx_pp, "status": "skip_killed_earlier"}
        results.append(rec); atomic_save()
        log(f"  SKIP (model,build) killed by earlier failure")
        return "skip"

    if ctx_pp > minfo["max_ctx"]:
        rec = {"build": build, "model": model_name, "env": env_name,
               "ctx_pp": ctx_pp, "status": "skip_above_model_cap"}
        results.append(rec); atomic_save()
        log(f"  SKIP ctx {ctx_pp} > model cap {minfo['max_ctx']}")
        return "skip"

    env = os.environ.copy()
    env.update(ENVS[env_name])
    cmd = [binary,
           "-m", minfo["path"],
           "-ngl", "99",
           "-fa", "1",
           "-ctk", "q4_0", "-ctv", "q4_0",
           "-r", str(REPEATS),
           "-p", str(ctx_pp),
           "-n", str(GEN),
           "-o", "json"]
    log(f"RUN build={build} model={model_name} env={env_name} ctx={ctx_pp}")
    log(f"    mem_before={mem_snapshot()}")
    start = time.time()
    parsed, rc, out, err, status = None, None, "", "", "ok"
    try:
        cp = subprocess.run(cmd, env=env, capture_output=True, text=True,
                            timeout=CELL_TIMEOUT)
        wall = time.time() - start
        out, err, rc = cp.stdout, cp.stderr, cp.returncode
        i = out.find("[")
        if i >= 0:
            try:
                parsed = json.loads(out[i:])
            except Exception as e:
                log(f"  parse error: {e}")
        if rc != 0 or parsed is None:
            status = "fail"
    except subprocess.TimeoutExpired:
        wall = time.time() - start
        status = "timeout"
        log(f"  TIMEOUT after {CELL_TIMEOUT}s")
        # Try to clean up any straggler llama-bench
        subprocess.run(["pkill", "-9", "-f", "llama-bench"], check=False)
        time.sleep(5)
    except Exception as e:
        wall = time.time() - start
        status = f"exception:{type(e).__name__}"
        log(f"  EXCEPTION: {e}")

    rec = {
        "ts":       time.strftime("%Y-%m-%d %H:%M:%S"),
        "build":    build,
        "model":    model_name,
        "env":      env_name,
        "env_extra": ENVS[env_name],
        "ctx_pp":   ctx_pp,
        "gen":      GEN,
        "repeats":  REPEATS,
        "wall_s":   round(wall, 2),
        "rc":       rc,
        "status":   status,
        "mem_after": mem_snapshot(),
    }
    if parsed:
        rec["raw"] = parsed
        for row in parsed:
            t = row.get("test", "?")
            ts_ = row.get("avg_ts", 0.0)
            sd  = row.get("stddev_ts", 0.0)
            log(f"   {t}: {ts_:.2f} t/s  (sd {sd:.2f})")
    else:
        rec["stdout_tail"] = out[-2000:] if out else ""
        rec["stderr_tail"] = err[-2000:] if err else ""
        log(f"  FAIL rc={rc} wall={wall:.1f}s — killing (model,build) for higher ctx")
        killed.add((model_name, build))
    results.append(rec)
    atomic_save()
    return status

def main():
    log("=== bench-overnight starting ===")
    load_existing()
    log(f"models: {MODEL_ORDER}")
    log(f"builds: {list(BUILDS)}")
    log(f"envs:   {list(ENVS)}")
    log(f"ctx tiers: {CTX_TIERS}")

    # Quick model-file existence audit up front
    for m, info in MODELS.items():
        ok = os.path.exists(info["path"])
        log(f"  model {m}: {'OK' if ok else 'MISSING'}  ({info['path']})")

    total_planned = 0
    for ctx in CTX_TIERS:
        for m in MODEL_ORDER:
            if MODELS[m]["max_ctx"] < ctx:
                continue
            for b in BUILDS:
                for e in ENVS:
                    total_planned += 1
    log(f"total planned cells (excluding skips): {total_planned}")

    cell_idx = 0
    for ctx in CTX_TIERS:
        log(f"################  CTX TIER {ctx}  ################")
        for model_name in MODEL_ORDER:
            for build in BUILDS:
                for env_name in ENVS:
                    cell_idx += 1
                    log(f"--- cell {cell_idx} ---")
                    run_cell(build, model_name, env_name, ctx)
                    log(f"cooldown {COOLDOWN_S}s")
                    time.sleep(COOLDOWN_S)
        log(f"=== CTX TIER {ctx} done; killed so far: {sorted(killed)} ===")

    log("=== bench-overnight finished cleanly ===")

if __name__ == "__main__":
    main()
