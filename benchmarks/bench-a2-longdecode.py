#!/usr/bin/env python3
"""A2: Longer-generation decode spot check, paper revision experiment.

The article measures every decode rate at num_predict=100. R1 asks whether short windows
are contaminated by warm-up/first-token effects and differ from sustained decode. This
harness measures gen tok/s at num_predict = 100 vs 512 vs 1024 on a size-spanning set of
models (short 4K context, matching the gen_speed table), n=3, and reports whether the
100-token rate matches the longer-window rate.

Run on the board (24-CU recovered article config, Ollama 0.20.0, q4_0 KV):
  nohup python3 /tmp/bench-a2-longdecode.py > /tmp/a2-longdecode.log 2>&1 &
Output: ~/phase-c-out/results/step-a2-longdecode.json
"""
import json, os, subprocess, sys, time, datetime, urllib.request

sys.stdout.reconfigure(line_buffering=True)
OLLAMA      = "http://127.0.0.1:11434"
MODELS      = ["phi4-mini:latest", "qwen3.5:9b", "deepseek-r1:14b", "qwen3.5-35b-a3b-iq2m:latest"]
N_PREDICT   = [30, 50, 100, 512, 1024]
NUM_CTX     = 4096
N_RUNS      = 3
PROMPT      = ("Write a detailed, continuous technical essay on the history and future of "
              "computer memory technologies, from magnetic core through DRAM and 3D NAND to "
              "emerging non-volatile memories. Do not use headings or lists; write flowing prose.")
RESULTS_DIR = os.path.expanduser("~/phase-c-out/results")
OUT_PATH    = f"{RESULTS_DIR}/step-a2-longdecode-ext.json"
os.makedirs(RESULTS_DIR, exist_ok=True)

def api(endpoint, data=None, timeout=600):
    req = urllib.request.Request(f"{OLLAMA}{endpoint}", method="POST" if data else "GET")
    body = None
    if data:
        req.add_header("Content-Type", "application/json"); body = json.dumps(data).encode()
    with urllib.request.urlopen(req, body, timeout=timeout) as resp:
        return json.loads(resp.read())

def unload_all():
    try:
        for m in (api("/api/ps", timeout=10) or {}).get("models", []):
            if m.get("name"):
                api("/api/generate", {"model": m["name"], "keep_alive": 0}, timeout=30); time.sleep(1)
    except Exception:
        pass
    time.sleep(3)

def run_one(model, n_predict, run_idx):
    t0 = time.time()
    try:
        resp = api("/api/generate", {
            "model": model, "prompt": f"[a2-run:{run_idx+1}]\n" + PROMPT, "stream": False, "think": False,
            "keep_alive": "10m",
            "options": {"num_ctx": NUM_CTX, "num_predict": n_predict, "temperature": 0.0, "seed": 42},
        }, timeout=600)
        if "error" in resp:
            return {"status": "FAIL", "error": resp["error"][:200]}
        ec = resp.get("eval_count", 0); ed = resp.get("eval_duration", 0)/1e9
        return {"status": "OK", "eval_count": ec,
                "gen_tok_s": round(ec/ed, 3) if ed > 0 else 0,
                "eval_s": round(ed, 2),
                "load_s": round(resp.get("load_duration", 0)/1e9, 2),
                "prompt_eval_s": round(resp.get("prompt_eval_duration", 0)/1e9, 2),
                "wall_s": round(time.time()-t0, 1)}
    except Exception as e:
        return {"status": "TIMEOUT" if "timed out" in str(e).lower() else "FAIL", "error": str(e)[:200]}

def median(vals):
    v = sorted(x for x in vals if isinstance(x,(int,float)) and x > 0)
    return round(v[len(v)//2], 2) if v else None

def main():
    print("="*72)
    print("A2: Longer-decode spot check — num_predict 100 vs 512 vs 1024, n=3, temp=0")
    print(f"Models: {MODELS}  ctx={NUM_CTX}  Started: {datetime.datetime.now().isoformat()}")
    print("="*72)
    kern = subprocess.check_output(["uname","-r"], text=True).strip()
    cu = subprocess.run("journalctl -k -b 0 | grep -o 'active_cu_number [0-9]*' | tail -1",
                        shell=True, capture_output=True, text=True).stdout.strip()
    print(f"[preflight] kernel={kern}  {cu}", flush=True)
    cells, t0 = [], time.time()
    for model in MODELS:
        for npv in N_PREDICT:
            unload_all()
            runs = [dict(run_one(model, npv, i), run_idx=i+1) for i in range(N_RUNS)]
            gv = [r.get("gen_tok_s") for r in runs if r.get("status")=="OK"]
            cell = {"model": model, "num_predict": npv, "num_ctx": NUM_CTX, "n_runs": N_RUNS,
                    "gen_tok_s_median": median(gv), "gen_tok_s_all": gv,
                    "eval_count_median": median([r.get("eval_count") for r in runs]), "runs": runs}
            cells.append(cell)
            print(f"  {model:32s} np={npv:<5} gen_median={cell['gen_tok_s_median']} t/s  (runs {gv})", flush=True)
            with open(OUT_PATH, "w") as f:
                json.dump({"step":"a2-longdecode","captured_at":datetime.datetime.now().isoformat(),
                           "kernel":kern,"cu":cu,"models":MODELS,"n_predict":N_PREDICT,"num_ctx":NUM_CTX,
                           "n_runs":N_RUNS,"sampling":"temp=0 seed=42","cells":cells}, f, indent=2)
    # summary: 100 vs 512 vs 1024 delta
    print("\n" + "="*72 + "\nSUMMARY — decode tok/s vs generation length (delta vs 100-token)\n" + "="*72)
    hdr = f"  {'model':32s}" + "".join(f"{'np='+str(n):>9}" for n in N_PREDICT) + f"{'Δmin':>8}{'Δmax':>8}"
    print(hdr)
    for model in MODELS:
        g = {npv: next((c['gen_tok_s_median'] for c in cells if c['model']==model and c['num_predict']==npv), None) for npv in N_PREDICT}
        row = f"  {model:32s}" + "".join(f"{str(g[n]):>9}" for n in N_PREDICT)
        ds = [ (g[n]-g[100])/g[100]*100 for n in N_PREDICT if g.get(n) and g.get(100) ]
        row += f"{min(ds):+7.1f}%{max(ds):+7.1f}%" if ds else ""
        print(row)
    print(f"\n[done] {round((time.time()-t0)/60,1)} min -> {OUT_PATH}")

if __name__ == "__main__":
    main()
