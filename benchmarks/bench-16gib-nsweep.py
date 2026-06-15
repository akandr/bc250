#!/usr/bin/env python3
"""
n=3 filled-context ladder at the corrected 16 GiB TTM pool, for the models the
12 GiB cap (tmpfiles.d override) distorted. Produces canonical 16-GiB numbers to
replace the n=1 confirmation data in the JUCS article.

Robustness / safety (hours-long run on a 16 GB board):
  - ONE request at a time, never concurrent.
  - n=3 per cell; report median gen + median prefill.
  - `sudo systemctl restart ollama` + drop_caches BETWEEN MODELS — fully releases
    the prior model's GTT pool (plain `ollama stop`/drop_caches did not).
  - num_predict=80 (enough decode tokens for a stable rate), keep_alive=0.
  - per-request timeout scales with prefill size, capped at 1200 s.
  - climb context; STOP a model's climb on the first failed cell, or once GTT
    high-water passes 14.7 GiB.
  - incremental JSON save after every cell (partial results survive a hang/reset).

Usage (board, queue-runner stopped):
    python3 bench-16gib-nsweep.py
    python3 bench-16gib-nsweep.py "modelA:tag,modelB:tag"   # custom model list
"""
import json, sys, time, subprocess, statistics, urllib.request, urllib.error

OLLAMA      = "http://127.0.0.1:11434"
GTT_USED    = "/sys/class/drm/card1/device/mem_info_gtt_used"
PAGES_LIMIT = "/sys/module/ttm/parameters/pages_limit"
SAFE_GTT    = 14.7
N_RUNS      = 3
TIERS_K     = [4, 16, 32, 64]
OUTDIR      = "/opt/netscan/tmp"

DEFAULT_MODELS = [
    "qwen3.5-35b-a3b-iq2m",
    "gemma4-26b-q3",
    "gpt-oss:20b",
    "qwen3.5-27b-iq2m",
    "hf.co/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:IQ2_M",
]

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

NUM_PREDICT = 128
# Force SUSTAINED generation: a short-answer prompt makes the model stop after a
# few tokens, so the eval_duration is dominated by fixed overhead and the tok/s is
# garbage (and non-monotonic across tiers). An essay task reliably emits >NUM_PREDICT
# tokens, so eval_count saturates the cap and the rate is the true sustained decode.
GEN_TASK = ("\n\nIgnore the repetitive filler above. Write a detailed, multi-paragraph "
            "technical essay of at least 300 words on the tradeoffs of running large "
            "language models on unified-memory APUs: bandwidth vs compute, quantization, "
            "KV-cache growth, and MoE sparsity. Be thorough and specific.")

def one_run(model, ctx, timeout):
    prompt = filler(int(ctx*0.8)) + GEN_TASK
    body = json.dumps({"model":model,"messages":[{"role":"user","content":prompt}],
        "stream":False,"think":False,"keep_alive":0,
        "options":{"num_ctx":ctx,"temperature":0.0,"num_predict":NUM_PREDICT}}).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/chat", data=body,
        headers={"Content-Type":"application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"status":f"HTTP{e.code}","wall":round(time.time()-t0,1)}
    except Exception as e:
        return {"status":"timeout","wall":round(time.time()-t0,1),"err":str(e)[:120]}
    pe, pd = d.get("prompt_eval_count",0), d.get("prompt_eval_duration",1)/1e9
    ec, ed = d.get("eval_count",0), d.get("eval_duration",1)/1e9
    early = ec < 0.6*NUM_PREDICT   # flag unreliable (model stopped early)
    return {"status":"OK","wall":round(time.time()-t0,1),"pe":pe,"ec":ec,
            "gen":round(ec/ed,2) if ed>0 else 0, "pre":round(pe/pd,1) if pd>0 else 0,
            "early_stop":early}

def sweep_model(model):
    print(f"\n{'='*64}\nMODEL {model}  (restart+reclaim)\n{'='*64}", flush=True)
    restart_ollama()
    print(f"  clean: free={free_gib():.1f} GiB gtt={gtt_gib():.1f} GiB "
          f"pages_limit={sysfs_int(PAGES_LIMIT)}", flush=True)
    res = {"model":model, "pages_limit":sysfs_int(PAGES_LIMIT), "n":N_RUNS, "tiers":{}}
    gtt_hw = 0.0
    for t in TIERS_K:
        ctx = t*1024
        timeout = min(1200, max(300, int(ctx*0.8/30)+120))
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
    models = sys.argv[1].split(",") if len(sys.argv)>1 else DEFAULT_MODELS
    print(f"n=3 16-GiB sweep — {len(models)} models, tiers {TIERS_K}K, {time.strftime('%H:%M')}", flush=True)
    for m in models:
        try: sweep_model(m)
        except Exception as e: print(f"  !! {m} errored: {str(e)[:160]}", flush=True)
    print("\nSWEEP COMPLETE", flush=True)

if __name__ == "__main__":
    main()
