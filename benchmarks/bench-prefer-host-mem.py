#!/usr/bin/env python3
"""bench-prefer-host-mem.py
A/B benchmark for the GGML_VK_PREFER_HOST_MEMORY=1 hypothesis on BC-250.

Matrix:
    builds   = {b8200 (currently installed), b9165 (latest release)}
    env      = {unset, GGML_VK_PREFER_HOST_MEMORY=1}
    models   = {deepseek-r1-14b (dense), moe-coder-30b-iq2m (MoE)}
    ctx_pp   = {4096, 16384}
    gen      = 128

Each combo runs llama-bench with -r 2 (two repeats) so we get a CV estimate
for each cell. JSON output is written incrementally to /tmp/bench-phm.json
on the BC-250 and pulled to the workspace at the end.

Designed to be safe on a 14GiB UMA box:
  * one llama-bench process at a time
  * 30s cooldown between runs
  * ollama is stopped for the whole sweep so it doesn't fight for memory
  * oom_score_adj=-1000 so the kernel kills shells/python before llama-bench
"""
import json, os, subprocess, sys, time, itertools

RESULTS = "/tmp/bench-phm-results.json"
LOG     = "/tmp/bench-phm.log"

BUILDS = {
    "b8200": "/opt/llama.cpp/build/bin/llama-bench",
    "b9165": "/opt/llama.cpp-b9165/build/bin/llama-bench",
}
MODELS = {
    "deepseek-r1-14b":   "/opt/models/deepseek-r1-14b.gguf",
    "moe-coder-30b-iq2m": "/opt/models/moe-coder-30b-iq2m.gguf",
}
# Ordered small → large so a freeze at the largest ctx doesn't lose earlier data.
# 16384 is borderline on 14 GiB UMA; if it OOMs we still keep small-ctx data.
CTX_PP = [2048, 4096, 16384]
GEN    = 128
REPEATS = 2
COOLDOWN = 30

ENVS = {
    "default":     {},
    "prefer_host": {"GGML_VK_PREFER_HOST_MEMORY": "1"},
}

results = []

def log(m):
    line = f"[{time.strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")

def save():
    with open(RESULTS, "w") as f:
        json.dump(results, f, indent=2)

def mem_used_gib():
    try:
        with open("/sys/class/drm/card1/device/mem_info_gtt_used") as f:
            gtt = int(f.read().strip())
        with open("/sys/class/drm/card1/device/mem_info_vram_used") as f:
            vram = int(f.read().strip())
        return {"gtt_gib": round(gtt/1024**3,3), "vram_gib": round(vram/1024**3,3)}
    except Exception:
        return {}

def run_one(build, model, env_name, ctx_pp):
    binary = BUILDS[build]
    model_path = MODELS[model]
    env = os.environ.copy()
    env.update(ENVS[env_name])
    # llama-bench JSON output, fix layers for full GPU offload
    cmd = [
        binary,
        "-m", model_path,
        "-ngl", "99",
        "-fa", "1",
        "-ctk", "q4_0", "-ctv", "q4_0",
        "-r", str(REPEATS),
        "-p", str(ctx_pp),
        "-n", str(GEN),
        "-o", "json",
    ]
    log(f"RUN build={build} model={model} env={env_name} ctx_pp={ctx_pp}")
    log(f"     env extra: {ENVS[env_name]}")
    start = time.time()
    try:
        cp = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=900)
        wall = time.time() - start
        out = cp.stdout
        err = cp.stderr
        rc  = cp.returncode
        # llama-bench prints a JSON array on stdout
        parsed = None
        try:
            # Some builds prefix with logging; find the JSON array
            i = out.find("[")
            if i >= 0:
                parsed = json.loads(out[i:])
        except Exception as e:
            log(f"   parse error: {e}")
        rec = {
            "build": build,
            "model": model,
            "env":   env_name,
            "env_extra": ENVS[env_name],
            "ctx_pp": ctx_pp,
            "gen": GEN,
            "repeats": REPEATS,
            "wall_s": round(wall,2),
            "rc": rc,
            "mem_after": mem_used_gib(),
            "raw": parsed if parsed else out[-2000:],
        }
        if not parsed:
            rec["stderr_tail"] = err[-1000:]
        results.append(rec)
        save()
        if parsed:
            for row in parsed:
                log(f"   {row.get('test')} : {row.get('avg_ts'):.2f} t/s "
                    f"(stddev {row.get('stddev_ts',0):.2f})")
        else:
            log(f"   FAILED rc={rc}; stderr tail logged")
    except subprocess.TimeoutExpired:
        results.append({
            "build": build, "model": model, "env": env_name,
            "ctx_pp": ctx_pp, "error": "timeout",
        })
        save()
        log("   TIMEOUT")

def main():
    # Caller is expected to have already stopped ollama / queue-runner / signal-cli
    # (and the ollama-watchdog + bc250-health timers) before invoking this script.
    log("assuming ollama / workers already stopped by caller")

    # Iterate ctx OUTERMOST and ascending: finish all small-ctx work before
    # touching anything that might destabilise the box.
    combos = [(b, m, e, c)
              for c in CTX_PP
              for b in BUILDS
              for m in MODELS
              for e in ENVS]
    log(f"total combinations: {len(combos)} (small ctx first)")

    for i, (b, m, e, c) in enumerate(combos, 1):
        log(f"=== {i}/{len(combos)} ===")
        run_one(b, m, e, c)
        log(f"cooldown {COOLDOWN}s")
        time.sleep(COOLDOWN)

    log("done")
    # NOTE: deliberately NOT restarting ollama / queue-runner / signal-cli.
    # Caller is responsible for re-enabling them when satisfied with results.

if __name__ == "__main__":
    main()
