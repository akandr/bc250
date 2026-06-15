#!/usr/bin/env python3
"""
Heap-placement cliff A/B, re-run at the CORRECTED 16 GiB TTM pool.

Background: §B11a (and the experiment dropped from JUCS) tested
GGML_VK_PREFER_HOST_MEMORY (BAR/host-visible heap vs DEVICE_LOCAL) and found NO
monotonic cliff with granite under what we now know was a 12 GiB pages_limit cap.
A community report of a ~12% dip on qwen3.5:9b at 65K was never reproduced (that
model is b9265-incompatible). At the real 16 GiB the working-set/BAR crossover
dynamics differ, so we re-test with a LARGE model that strongly crosses the
5.5 GiB BAR heap and reaches 64K (the 35B-A3B MoE, 12.5 GiB), plus granite as the
original comparison baseline.

Method: llama-bench directly (no Ollama), GGML_VK_PREFER_HOST_MEMORY unset vs =1,
-p {4096,32768,65536} -n 128 -r 2, JSON output parsed for tg (gen) and pp (prefill).
Cliff = phm1/phm0 tg ratio dropping below 1 as context grows.

Run on the board (queue-runner stopped):
    python3 bench-heap-phm-16gib.py
"""
import json, os, subprocess, sys, time

BENCH = "/opt/llama.cpp-b9265/build/bin/llama-bench"
MODELS = {
    "qwen3.5-35b-a3b-iq2m": "/opt/models/qwen3.5-35b-a3b-iq2m.gguf",  # 12.5 GiB, crosses BAR, ->64K
    "granite-4.0-h-tiny":   "/opt/models/granite-4.0-h-tiny-q4km.gguf",  # 4.0 GiB, original baseline
}
CTX  = [4096, 32768, 65536]
GEN  = 128
REPS = 2
ARMS = {"phm0": None, "phm1": "1"}   # GGML_VK_PREFER_HOST_MEMORY unset vs 1
OUT  = "/opt/netscan/tmp/heap-phm-16gib.json"

sys.stdout.reconfigure(line_buffering=True)

def drop_caches():
    subprocess.run(["sudo","sh","-c","echo 3 > /proc/sys/vm/drop_caches"], capture_output=True)

def run_cell(model_path, ctx, arm_val, timeout):
    env = dict(os.environ)
    if arm_val is not None:
        env["GGML_VK_PREFER_HOST_MEMORY"] = arm_val
    else:
        env.pop("GGML_VK_PREFER_HOST_MEMORY", None)
    cmd = [BENCH, "-m", model_path, "-p", str(ctx), "-n", str(GEN),
           "-r", str(REPS), "-o", "json"]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "wall": round(time.time()-t0,1)}
    if r.returncode != 0:
        return {"status": f"rc{r.returncode}", "err": r.stderr[-200:], "wall": round(time.time()-t0,1)}
    try:
        rows = json.loads(r.stdout)
    except Exception:
        return {"status": "parse_err", "out": r.stdout[-200:], "wall": round(time.time()-t0,1)}
    pp = tg = None
    for row in rows:
        nt, ng = row.get("n_prompt",0), row.get("n_gen",0)
        if ng > 0 and nt == 0: tg = row.get("avg_ts")     # generation row
        elif nt > 0 and ng == 0: pp = row.get("avg_ts")   # prefill row
    return {"status":"OK", "pp": round(pp,1) if pp else None,
            "tg": round(tg,2) if tg else None, "wall": round(time.time()-t0,1)}

def main():
    res = {"build":"b9265","pages_limit":int(open("/sys/module/ttm/parameters/pages_limit").read()),
           "cells":[]}
    print(f"Heap prefer_host_mem A/B @ 16 GiB (pages_limit={res['pages_limit']})\n")
    for mname, mpath in MODELS.items():
        print(f"=== {mname} ===")
        for ctx in CTX:
            timeout = min(1200, max(180, int(ctx/40)+120))
            row = {"model":mname, "ctx":ctx}
            for arm, val in ARMS.items():
                drop_caches(); time.sleep(1)
                c = run_cell(mpath, ctx, val, timeout)
                row[arm] = c
                print(f"  {ctx:>6} {arm}: {c.get('status')} tg={c.get('tg')} pp={c.get('pp')} ({c.get('wall')}s)")
            t0, t1 = row["phm0"].get("tg"), row["phm1"].get("tg")
            if t0 and t1:
                row["ratio"] = round(t1/t0, 3)
                print(f"         -> phm1/phm0 tg ratio = {row['ratio']}")
            res["cells"].append(row)
            json.dump(res, open(OUT,"w"), indent=2)
    print(f"\nsaved -> {OUT}")
    print("\n=== SUMMARY (phm1/phm0 tg ratio; <1 = host-mem hurts = cliff) ===")
    for r in res["cells"]:
        if "ratio" in r: print(f"  {r['model']:24} {r['ctx']:>6}: {r['ratio']}")

if __name__ == "__main__":
    main()
