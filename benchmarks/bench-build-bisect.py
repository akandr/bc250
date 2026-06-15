#!/usr/bin/env python3
"""bench-build-bisect.py — locate the b8200 -> b9165 deepseek-r1-14b@ctx=8192
regression by sweeping 4 builds × 2 envs with REPEATS=3.

Targets the −6% to −12% tg regression observed in the overnight sweep.
Also smoke-tests gpt-oss & granite at one mid-ctx point on each build to
confirm the regression is dense-14B-specific (or not).
"""
import json, os, subprocess, sys, time

RESULTS = "/tmp/bench-build-bisect-results.json"
LOG     = "/tmp/bench-build-bisect.log"

BUILDS = {
    "b8200": "/opt/llama.cpp/build/bin/llama-bench",
    "b8600": "/opt/llama.cpp-b8600/build/bin/llama-bench",
    "b9165": "/opt/llama.cpp-b9165/build/bin/llama-bench",
    "b9265": "/opt/llama.cpp-b9265/build/bin/llama-bench",
}

ENVS = {
    "default":     {},
    "prefer_host": {"GGML_VK_PREFER_HOST_MEMORY": "1"},
}

# Primary signal: deepseek @ 8K (the regression cell).
# Smoke checks: gpt-oss @ 8K and granite @ 16K to confirm scope.
TARGETS = [
    {"model": "deepseek-r1-14b",    "path": "/opt/models/deepseek-r1-14b.gguf",         "ctx": 8192},
    {"model": "gpt-oss-20b-mxfp4",  "path": "/opt/models/gpt-oss-20b-mxfp4.gguf",       "ctx": 8192},
    {"model": "granite-4.0-h-tiny", "path": "/opt/models/granite-4.0-h-tiny-q4km.gguf", "ctx": 16384},
]

GEN          = 128
REPEATS      = 3
COOLDOWN_S   = 45
CELL_TIMEOUT = 900

results = []
done    = set()

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
        k = (r.get("build"), r.get("model"), r.get("env"), r.get("ctx_pp"))
        if all(x is not None for x in k) and r.get("status") != "skip_no_bin":
            done.add(k)
    log(f"resumed: {len(results)} prior cells")

def mem_snapshot():
    out = {}
    try:
        for k, p in (("gtt_used","/sys/class/drm/card1/device/mem_info_gtt_used"),
                     ("vram_used","/sys/class/drm/card1/device/mem_info_vram_used")):
            with open(p) as f: out[k+"_gib"] = round(int(f.read().strip())/1024**3, 3)
    except Exception: pass
    return out

def run_cell(build, target, env_name):
    binary = BUILDS[build]
    ctx_pp = target["ctx"]
    model_name = target["model"]
    path = target["path"]
    if not os.path.exists(binary):
        rec = {"build": build, "model": model_name, "env": env_name,
               "ctx_pp": ctx_pp, "status": "skip_no_bin"}
        results.append(rec); atomic_save()
        log(f"  SKIP no binary: {binary}")
        return
    key = (build, model_name, env_name, ctx_pp)
    if key in done:
        log(f"  SKIP already done: {key}")
        return

    env = os.environ.copy(); env.update(ENVS[env_name])
    cmd = [binary, "-m", path, "-ngl", "99", "-fa", "1",
           "-ctk", "q4_0", "-ctv", "q4_0",
           "-r", str(REPEATS), "-p", str(ctx_pp), "-n", str(GEN), "-o", "json"]
    log(f"RUN build={build} model={model_name} env={env_name} ctx={ctx_pp}  (r={REPEATS})")
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
        subprocess.run(["pkill", "-9", "-f", "llama-bench"], check=False); time.sleep(5)
    except Exception as e:
        wall = time.time() - start; status = f"exception:{type(e).__name__}"
        log(f"  EXCEPTION: {e}")

    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "build": build, "model": model_name, "env": env_name,
           "env_extra": ENVS[env_name], "ctx_pp": ctx_pp, "gen": GEN, "repeats": REPEATS,
           "wall_s": round(wall, 2), "rc": rc, "status": status,
           "mem_after": mem_snapshot()}
    if parsed:
        rec["raw"] = parsed
        for row in parsed:
            log(f"   {row.get('test','?')}: {row.get('avg_ts',0):.2f} t/s  (sd {row.get('stddev_ts',0):.2f})")
    else:
        rec["stdout_tail"] = out[-2000:] if out else ""
        rec["stderr_tail"] = err[-2000:] if err else ""
        log(f"  FAIL rc={rc} wall={wall:.1f}s")
    results.append(rec); atomic_save()

def main():
    log("=== bench-build-bisect starting ===")
    log(f"builds: {list(BUILDS)}")
    log(f"envs:   {list(ENVS)}")
    log(f"targets: {[(t['model'], t['ctx']) for t in TARGETS]}")
    load_existing()
    # build-major so we get each build's complete picture before moving on
    # (if box dies, we still have full data for prior builds)
    for build in BUILDS:
        log(f"################  BUILD {build}  ################")
        for target in TARGETS:
            for env_name in ENVS:
                run_cell(build, target, env_name)
                log(f"cooldown {COOLDOWN_S}s")
                time.sleep(COOLDOWN_S)
    log("=== bench-build-bisect finished cleanly ===")

if __name__ == "__main__":
    main()
