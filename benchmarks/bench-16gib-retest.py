#!/usr/bin/env python3
"""
16 GiB TTM re-test — does the genuine 16 GiB ceiling change which models/contexts
work, vs the accidental 12 GiB cap (tmpfiles.d override) the canonical benchmarks
ran under?

Safety (lessons from the swap-hang earlier):
  - ONE request at a time, never concurrent.
  - num_predict tiny (just need tok/s + that it completes), keep_alive=0 (unload after).
  - drop_caches between models (reclaims TTM page pool).
  - Climb context; STOP a model's climb on the first 500/timeout, or once GTT
    high-water gets within ~1.3 GiB of the 16 GiB pool (don't push into swap).
  - Per-request timeout scales with prefill size but is capped.
  - Records pages_limit + GTT used (high-water) + free mem per tier, and
    prompt_eval_count to confirm the context was actually filled.

Usage (on the board, queue-runner stopped):
    python3 bench-16gib-retest.py MODEL[:tag] [maxtier_k]
e.g. python3 bench-16gib-retest.py gemma4-26b-q3 32
"""
import json, re, sys, time, subprocess, urllib.request, urllib.error

OLLAMA = "http://127.0.0.1:11434"
GTT_USED = "/sys/class/drm/card1/device/mem_info_gtt_used"
PAGES_LIMIT = "/sys/module/ttm/parameters/pages_limit"
POOL_GIB = 16.0
SAFE_GTT_GIB = 14.7          # stop climbing once GTT high-water passes this
TIERS_K = [4, 16, 32, 48, 64]

sys.stdout.reconfigure(line_buffering=True)

def sysfs_int(path):
    try:
        return int(open(path).read().strip())
    except Exception:
        return 0

def gtt_used_gib():
    return sysfs_int(GTT_USED) / (1024**3)

def free_gib():
    out = subprocess.run(["free", "-m"], capture_output=True, text=True).stdout
    for ln in out.splitlines():
        if ln.startswith("Mem:"):
            return int(ln.split()[3]) / 1024
    return 0.0

def drop_caches():
    subprocess.run(["sudo", "sh", "-c", "echo 3 > /proc/sys/vm/drop_caches"],
                   capture_output=True)

def ollama_stop(model):
    subprocess.run(["ollama", "stop", model], capture_output=True)

# ~4 chars/token filler with some variety so it isn't trivially compressible
_FILLER_UNIT = ("The quick brown fox jumps over the lazy dog while the engineer "
                "measures GTT pages and KV cache growth on the unified memory APU. ")

def filler(n_tokens):
    # aim ~0.8 * tier real tokens; ~4 chars/token
    target_chars = int(n_tokens * 4)
    reps = target_chars // len(_FILLER_UNIT) + 1
    return (_FILLER_UNIT * reps)[:target_chars]

def chat(model, ctx_tokens, num_predict, timeout):
    fill = filler(int(ctx_tokens * 0.8))
    prompt = (fill + "\n\nQUESTION: In one sentence, what animal is mentioned "
              "repeatedly above? Answer:")
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False, "think": False,
        "keep_alive": 0,
        "options": {"num_ctx": ctx_tokens, "temperature": 0.0,
                    "num_predict": num_predict},
    }).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/chat", data=payload,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            d = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"status": f"HTTP {e.code}", "wall_s": round(time.time()-t0, 1),
                "err": e.read().decode(errors="replace")[:160]}
    except Exception as e:
        return {"status": "timeout/err", "wall_s": round(time.time()-t0, 1),
                "err": str(e)[:160]}
    pe = d.get("prompt_eval_count", 0)
    pd = d.get("prompt_eval_duration", 1) / 1e9
    ec = d.get("eval_count", 0)
    ed = d.get("eval_duration", 1) / 1e9
    return {
        "status": "OK", "wall_s": round(time.time()-t0, 1),
        "prompt_tokens": pe, "gen_tok_s": round(ec/ed, 1) if ed > 0 else 0,
        "prefill_tok_s": round(pe/pd, 1) if pd > 0 else 0,
    }

def run_model(model, max_tier_k):
    print(f"\n{'='*64}\nMODEL: {model}   (pages_limit={sysfs_int(PAGES_LIMIT)} = "
          f"{sysfs_int(PAGES_LIMIT)*4096/(1024**3):.0f} GiB)\n{'='*64}")
    ollama_stop(model); time.sleep(2); drop_caches(); time.sleep(1)
    print(f"  clean: free={free_gib():.1f} GiB, gtt_used={gtt_used_gib():.1f} GiB")
    tiers = [t for t in TIERS_K if t <= max_tier_k]
    rows = []
    gtt_hw = 0.0
    for t in tiers:
        ctx = t * 1024
        # prefill of ~0.8*ctx tokens can be slow; scale timeout, cap 20 min
        timeout = min(1200, max(240, int(ctx * 0.8 / 35) + 90))
        print(f"  [{t}K] ctx={ctx} timeout={timeout}s ...", flush=True)
        r = chat(model, ctx, num_predict=48, timeout=timeout)
        g = gtt_used_gib(); gtt_hw = max(gtt_hw, g)
        r.update({"tier_k": t, "gtt_used_gib": round(g, 2),
                  "free_gib_after": round(free_gib(), 2)})
        rows.append(r)
        if r["status"] == "OK":
            print(f"       OK  gen={r.get('gen_tok_s')} tok/s  "
                  f"prefill={r.get('prefill_tok_s')}  pe={r.get('prompt_tokens')}  "
                  f"gtt={g:.1f} GiB  free={r['free_gib_after']:.1f}  ({r['wall_s']}s)")
        else:
            print(f"       {r['status']}  ({r['wall_s']}s)  {r.get('err','')}")
            print(f"       -> stop climbing {model} (failure)")
            break
        if g > SAFE_GTT_GIB:
            print(f"       -> stop climbing {model} (gtt {g:.1f} > safe {SAFE_GTT_GIB})")
            break
    ollama_stop(model); time.sleep(1)
    return {"model": model, "pages_limit": sysfs_int(PAGES_LIMIT),
            "gtt_highwater_gib": round(gtt_hw, 2), "tiers": rows}

def main():
    model = sys.argv[1]
    max_tier_k = int(sys.argv[2]) if len(sys.argv) > 2 else 64
    res = run_model(model, max_tier_k)
    safe = model.replace(":", "-").replace("/", "-")
    out = f"/opt/netscan/tmp/retest16-{safe}.json"
    json.dump(res, open(out, "w"), indent=2)
    print(f"\nsaved -> {out}")
    # one-line verdict
    ok = [r for r in res["tiers"] if r["status"] == "OK"]
    if ok:
        top = max(ok, key=lambda r: r["tier_k"])
        print(f"VERDICT {model}: ran to {top['tier_k']}K filled "
              f"(gen {top.get('gen_tok_s')} tok/s), GTT high-water "
              f"{res['gtt_highwater_gib']} GiB")
    else:
        print(f"VERDICT {model}: did NOT run (first tier failed)")

if __name__ == "__main__":
    main()
