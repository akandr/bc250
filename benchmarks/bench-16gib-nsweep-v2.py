#!/usr/bin/env python3
"""
nsweep v2 — same n=3 filled-context ladder as bench-16gib-nsweep.py, but with a
HARD per-request wall-clock deadline via `curl --max-time` instead of urllib's
socket timeout. urllib's timeout only bounds socket *inactivity*, so a near-envelope
cell whose prefill crawls on memory pressure (e.g. qwen3:14b @ 64K, weights+KV at
the 16 GB physical ceiling) can hang the harness for hours while the connection
trickles. curl --max-time bounds total request time, so such a cell fails fast and
is correctly recorded as a timeout.

Changes vs v1:
  - one_run() shells out to curl --max-time <timeout> (hard total deadline).
  - tiers overridable: BC250_TIERS="4,16" python3 bench-16gib-nsweep-v2.py "model".
  - per-run timeout cap raised to 1500 s (user is relaxed on time; gives the
    completable high-context cells a fair chance without risking a multi-hour hang).
Everything else (restart+reclaim between models, GTT high-water guard, essay prompt
forcing ec=128, incremental JSON save) is identical to v1.
"""
import json, sys, os, time, subprocess, statistics

OLLAMA      = "http://127.0.0.1:11434"
GTT_USED    = "/sys/class/drm/card1/device/mem_info_gtt_used"
PAGES_LIMIT = "/sys/module/ttm/parameters/pages_limit"
SAFE_GTT    = 14.7
N_RUNS      = 3
TIERS_K     = [int(x) for x in os.environ.get("BC250_TIERS","4,16,32,64").split(",")]
OUTDIR      = "/opt/netscan/tmp"
NUM_PREDICT = 128

sys.stdout.reconfigure(line_buffering=True)

def sysfs_int(p):
    try: return int(open(p).read().strip())
    except Exception: return 0

def gtt_gib(): return sysfs_int(GTT_USED) / (1024**3)

def free_gib():
    out = subprocess.run(["free","-m"], capture_output=True, text=True).stdout
    for ln in out.splitlines():
        if ln.startswith("Mem:"): return int(ln.split()[3]) / 1024
    return 0.0

def restart_ollama():
    subprocess.run(["sudo","systemctl","restart","ollama"], capture_output=True)
    time.sleep(4)
    subprocess.run(["sudo","sh","-c","echo 3 > /proc/sys/vm/drop_caches"], capture_output=True)
    time.sleep(1)

_UNIT = ("The quick brown fox jumps over the lazy dog while the engineer measures "
         "GTT pages and KV cache growth on this unified-memory APU under load. ")
def filler(ntok):
    s = _UNIT * (int(ntok*4)//len(_UNIT) + 1)
    return s[:int(ntok*4)]

GEN_TASK = ("\n\nIgnore the repetitive filler above. Write a detailed, multi-paragraph "
            "technical essay of at least 300 words on the tradeoffs of running large "
            "language models on unified-memory APUs: bandwidth vs compute, quantization, "
            "KV-cache growth, and MoE sparsity. Be thorough and specific.")

def one_run(model, ctx, timeout):
    prompt = filler(int(ctx*0.8)) + GEN_TASK
    body = json.dumps({"model":model,"messages":[{"role":"user","content":prompt}],
        "stream":False,"think":False,"keep_alive":0,
        "options":{"num_ctx":ctx,"temperature":0.0,"num_predict":NUM_PREDICT}})
    t0 = time.time()
    try:
        p = subprocess.run(
            ["curl","-s","--max-time",str(timeout),"-X","POST",
             f"{OLLAMA}/api/chat","-H","Content-Type: application/json",
             "--data-binary","@-"],
            input=body.encode(), capture_output=True, timeout=timeout+30)
    except subprocess.TimeoutExpired:
        return {"status":"timeout","wall":round(time.time()-t0,1)}
    wall = round(time.time()-t0,1)
    if p.returncode != 0 or not p.stdout.strip():
        # curl 28 = max-time exceeded
        return {"status":"timeout" if p.returncode==28 else f"curl{p.returncode}","wall":wall}
    try:
        d = json.loads(p.stdout)
    except Exception:
        return {"status":"badjson","wall":wall}
    if "error" in d:
        return {"status":"err","wall":wall,"err":str(d["error"])[:120]}
    pe, pd = d.get("prompt_eval_count",0), d.get("prompt_eval_duration",1)/1e9
    ec, ed = d.get("eval_count",0), d.get("eval_duration",1)/1e9
    early = ec < 0.6*NUM_PREDICT
    return {"status":"OK","wall":wall,"pe":pe,"ec":ec,
            "gen":round(ec/ed,2) if ed>0 else 0, "pre":round(pe/pd,1) if pd>0 else 0,
            "early_stop":early}

def sweep_model(model):
    print(f"\n{'='*64}\nMODEL {model}  (restart+reclaim)  tiers={TIERS_K}\n{'='*64}", flush=True)
    restart_ollama()
    print(f"  clean: free={free_gib():.1f} GiB gtt={gtt_gib():.1f} GiB "
          f"pages_limit={sysfs_int(PAGES_LIMIT)}", flush=True)
    res = {"model":model, "pages_limit":sysfs_int(PAGES_LIMIT), "n":N_RUNS, "tiers":{}}
    gtt_hw = 0.0
    for t in TIERS_K:
        ctx = t*1024
        timeout = min(1500, max(300, int(ctx*0.8/30)+120))
        runs = [one_run(model, ctx, timeout) for _ in range(N_RUNS)]
        g = gtt_gib(); gtt_hw = max(gtt_hw, g)
        oks = [r for r in runs if r["status"]=="OK"]
        cell = {"runs":runs, "gtt_gib":round(g,2), "free_gib":round(free_gib(),2)}
        if oks:
            gens = [r["gen"] for r in oks]; pres = [r["pre"] for r in oks]
            cell.update({"ok":len(oks), "gen_med":round(statistics.median(gens),2),
                "pre_med":round(statistics.median(pres),1),
                "gen_runs":gens, "pe":oks[-1]["pe"]})
            ec = oks[-1].get("ec", 0); flagged = any(r.get("early_stop") for r in oks)
            print(f"  [{t:>2}K] gen_med={cell['gen_med']} (n={len(oks)}/{N_RUNS} {gens}) "
                  f"pre_med={cell['pre_med']} pe={oks[-1]['pe']} ec={ec}"
                  f"{' EARLY-STOP!' if flagged else ''} gtt={g:.1f} free={cell['free_gib']:.1f}", flush=True)
        else:
            cell.update({"ok":0, "status":runs[0]["status"]})
            print(f"  [{t:>2}K] FAIL ({runs[0]['status']}) gtt={g:.1f} -> stop climb", flush=True)
        res["tiers"][str(t)] = cell
        json.dump(res, open(f"{OUTDIR}/nsweep-{model.replace(':','-').replace('/','-')}.json","w"), indent=2)
        if not oks: break
        if g > SAFE_GTT:
            print(f"  -> gtt {g:.1f} > {SAFE_GTT}, stop climb", flush=True); break
    res["gtt_highwater"] = round(gtt_hw,2)
    json.dump(res, open(f"{OUTDIR}/nsweep-{model.replace(':','-').replace('/','-')}.json","w"), indent=2)
    okt = [int(k) for k,v in res["tiers"].items() if v.get("ok")]
    print(f"  VERDICT {model}: max tier {max(okt) if okt else 'none'}K, gtt_hw {gtt_hw:.1f} GiB", flush=True)
    return res

def main():
    models = sys.argv[1].split(",") if len(sys.argv)>1 else []
    if not models:
        print("usage: bench-16gib-nsweep-v2.py 'modelA:tag,modelB:tag'"); sys.exit(1)
    print(f"n=3 16-GiB sweep v2 (curl hard-deadline) — {len(models)} models, "
          f"tiers {TIERS_K}K, {time.strftime('%H:%M')}", flush=True)
    for m in models:
        try: sweep_model(m)
        except Exception as e: print(f"  !! {m} errored: {str(e)[:160]}", flush=True)
    print("\nSWEEP COMPLETE", flush=True)

if __name__ == "__main__":
    main()
