#!/usr/bin/env python3
"""A3: Flash-attention correctness validation, paper revision experiment.

The long-context capacity story depends on OLLAMA_FLASH_ATTENTION=1 on RADV/Vulkan for
GFX1013, but FA correctness on this GPU is verified only indirectly (needle recall). This
harness validates FA by running the SAME deterministic inputs (temp=0, seed=42) with FA on
vs off and comparing outputs:
  (a) a short fixed-prompt greedy generation  -> exact-match check
  (b) the two-needle retrieval probe at 16K/32K -> recall agreement
Agreement = FA output matches the non-FA reference on this GPU. Disagreement or FA-off OOM
at long context is itself reportable (FA is then a load-bearing capacity dependency).

Run on the board (24-CU recovered article config, Ollama 0.20.0, q4_0 KV):
  nohup python3 /tmp/bench-a3-flashattn.py > /tmp/a3-flashattn.log 2>&1 &
Output: ~/phase-c-out/results/step-a3-flashattn.json
"""
import json, os, re, subprocess, sys, time, datetime, urllib.request

sys.stdout.reconfigure(line_buffering=True)
OLLAMA        = "http://127.0.0.1:11434"
MODELS        = ["qwen3.5:9b", "deepseek-r1:14b"]
CTX_SIZES     = [16384, 32768]
FA_SETTINGS   = ["1", "0"]           # on first (deployed setting), then off (reference)
OVERRIDE_CONF = "/etc/systemd/system/ollama.service.d/override.conf"
RESULTS_DIR   = os.path.expanduser("~/phase-c-out/results")
OUT_PATH      = f"{RESULTS_DIR}/step-a3-flashattn.json"
os.makedirs(RESULTS_DIR, exist_ok=True)

SHORT_PROMPT = ("Continue this text deterministically with a single coherent paragraph about "
                "the memory wall in computer architecture: The fundamental bottleneck in modern computing is")

# ---- needle probe (verbatim from bench-kv-longctx.py) ----
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
"ever-widening gap that architects address through deeper cache hierarchies.\n\n")
NEEDLE_CODE, NEEDLE_CODE_EARLY = "DELTA-7-VIOLET-MOUNTAIN-93", "OMEGA-4-COBALT-RIVER-17"
NEEDLE_LINE       = "\n\n*** SPECIAL NOTE: The secret access code is " + NEEDLE_CODE + ". You will be asked about this code at the end of the document. ***\n\n"
NEEDLE_LINE_EARLY = "\n\n*** EARLY NOTE: The first auxiliary code is " + NEEDLE_CODE_EARLY + ". Remember both codes. ***\n\n"
NEEDLE_INSTR = ("You will be shown a long technical document. Embedded somewhere in the document are TWO "
    "special notes, each containing a code in the format WORD-NUMBER-WORD-WORD-NUMBER. After the document, "
    "you must answer one question about both codes. Read the entire document carefully.\n\nDOCUMENT BEGINS:\n\n")
NEEDLE_QUESTION = ("\n\nDOCUMENT ENDS.\n\nQUESTION: What were the two codes embedded in the EARLY NOTE and the "
    "SPECIAL NOTE? Respond with ONLY the two codes, one per line, in the order they appeared, nothing else.\n\nANSWER:")

def build_needle_prompt(target_tokens):
    overhead = len(NEEDLE_INSTR)+len(NEEDLE_QUESTION)+len(NEEDLE_LINE)+len(NEEDLE_LINE_EARLY)
    cb = max(int(target_tokens*3.8)-overhead, len(FILL_BLOCK))
    fill = (FILL_BLOCK * (cb//len(FILL_BLOCK)+1))[:cb]; n = len(fill)
    def snap(t):
        i = fill.find("\n", t); return i if 0 <= i <= t+200 else t
    e, m = snap(int(n*0.10)), snap(int(n*0.50))
    if m <= e: m = e+1
    return NEEDLE_INSTR + fill[:e] + NEEDLE_LINE_EARLY + fill[e:m] + NEEDLE_LINE + fill[m:] + NEEDLE_QUESTION

def verify_needle(out):
    if not out or len(re.sub(r"\s+","",out)) < 20: return False
    no = re.sub(r"\s+","",out.upper())
    def has(t):
        if re.sub(r"\s+","",t.upper()) in no: return True
        pos=0
        for s in t.upper().split("-"):
            i=no.find(s,pos)
            if i<0: return False
            pos=i+len(s)
        return True
    return has(NEEDLE_CODE) and has(NEEDLE_CODE_EARLY)

def set_fa(val):
    print(f"\n[fa] Setting OLLAMA_FLASH_ATTENTION={val} ...", flush=True)
    with open(OVERRIDE_CONF) as f: content = f.read()
    line = f'Environment="OLLAMA_FLASH_ATTENTION={val}"'
    if "OLLAMA_FLASH_ATTENTION" in content:
        content = re.sub(r'Environment="OLLAMA_FLASH_ATTENTION=[^"]*"', line, content)
    else:
        content = content.replace("[Service]", f"[Service]\n{line}", 1)
    with open("/tmp/_a3_override.conf","w") as f: f.write(content)
    subprocess.run(f"sudo cp /tmp/_a3_override.conf {OVERRIDE_CONF}", shell=True, check=True)
    subprocess.run(["sudo","systemctl","daemon-reload"], check=True)
    subprocess.run(["sudo","systemctl","restart","ollama"], check=True)
    time.sleep(15)

def api(endpoint, data, timeout=900):
    req = urllib.request.Request(f"{OLLAMA}{endpoint}", method="POST")
    req.add_header("Content-Type","application/json")
    with urllib.request.urlopen(req, json.dumps(data).encode(), timeout=timeout) as r:
        return json.loads(r.read())

def unload_all():
    try:
        req = urllib.request.Request(f"{OLLAMA}/api/ps")
        with urllib.request.urlopen(req, timeout=10) as r: ps = json.loads(r.read())
        for m in (ps or {}).get("models", []):
            if m.get("name"): api("/api/generate", {"model": m["name"], "keep_alive": 0}, timeout=30); time.sleep(1)
    except Exception: pass
    time.sleep(3)

def gen_short(model):
    try:
        resp = api("/api/generate", {"model": model, "prompt": SHORT_PROMPT, "stream": False, "think": False,
                   "keep_alive": "10m", "options": {"num_ctx": 4096, "num_predict": 120, "temperature": 0.0, "seed": 42}}, timeout=300)
        if "error" in resp: return {"status":"FAIL","error":resp["error"][:150]}
        return {"status":"OK","output":resp.get("response","").strip(),"eval_count":resp.get("eval_count",0)}
    except Exception as e:
        return {"status":"FAIL","error":str(e)[:150]}

def needle(model, ctx):
    try:
        resp = api("/api/chat", {"model": model, "messages":[{"role":"user","content":build_needle_prompt(ctx)}],
                   "stream": False, "think": False, "keep_alive": "10m",
                   "options": {"num_ctx": ctx, "num_predict": 200, "temperature": 0.0, "seed": 42}},
                   timeout=max(900, int(ctx/40)+600))
        if "error" in resp: return {"status":"FAIL","error":resp["error"][:150]}
        out = (resp.get("message") or {}).get("content","").strip()
        return {"status":"OK","recall":verify_needle(out),"output":out[:200],"prompt_eval_count":resp.get("prompt_eval_count",0)}
    except Exception as e:
        return {"status":"FAIL","error":str(e)[:150]}

def main():
    print("="*72)
    print("A3: Flash-attention correctness (FA on vs off) — output/recall agreement, temp=0")
    print(f"Models: {MODELS}  Ctx: {CTX_SIZES}  Started: {datetime.datetime.now().isoformat()}")
    print("="*72)
    kern = subprocess.check_output(["uname","-r"], text=True).strip()
    cu = subprocess.run("journalctl -k -b 0 | grep -o 'active_cu_number [0-9]*' | tail -1",
                        shell=True, capture_output=True, text=True).stdout.strip()
    print(f"[preflight] kernel={kern}  {cu}", flush=True)

    data = {"short": {}, "needle": {}}
    t0 = time.time()
    for fa in FA_SETTINGS:
        set_fa(fa)
        for model in MODELS:
            unload_all()
            s = gen_short(model)
            data["short"].setdefault(model, {})[fa] = s
            print(f"  [FA={fa}] {model} short: {s['status']} eval={s.get('eval_count')}", flush=True)
            for ctx in CTX_SIZES:
                # FA-off has no attention memory savings; at 32K it exceeds the 16 GB pool and
                # OOM-kills the runner (a capacity finding, not a correctness test). Skip it.
                if fa == "0" and ctx > 16384:
                    data["needle"].setdefault(f"{model}|{ctx//1024}K", {})[fa] = {
                        "status": "SKIP_OOM", "note": "FA-off exceeds 16 GB pool at 32K on this board"}
                    print(f"  [FA={fa}] {model} needle {ctx//1024}K: SKIP (FA-off OOMs at 32K — capacity note)", flush=True)
                    continue
                unload_all()
                nd = needle(model, ctx)
                data["needle"].setdefault(f"{model}|{ctx//1024}K", {})[fa] = nd
                print(f"  [FA={fa}] {model} needle {ctx//1024}K: {nd['status']} recall={nd.get('recall')}", flush=True)
            _save(data, kern, cu, t0)

    # ---- compare FA on vs off ----
    print("\n" + "="*72 + "\nCOMPARISON — FA on (1) vs off (0)\n" + "="*72)
    comparison = {"short_exact_match": {}, "needle_recall_agreement": {}}
    for model in MODELS:
        on, off = data["short"].get(model,{}).get("1",{}), data["short"].get(model,{}).get("0",{})
        if on.get("status")=="OK" and off.get("status")=="OK":
            match = on["output"] == off["output"]
            # token-level agreement even if not byte-identical
            comparison["short_exact_match"][model] = {"exact": match,
                "on_preview": on["output"][:120], "off_preview": off["output"][:120]}
            print(f"  {model} short exact-match: {match}")
        else:
            comparison["short_exact_match"][model] = {"exact": None,
                "on_status": on.get("status"), "off_status": off.get("status")}
            print(f"  {model} short: on={on.get('status')} off={off.get('status')}")
    for key in sorted(data["needle"].keys()):
        on, off = data["needle"][key].get("1",{}), data["needle"][key].get("0",{})
        r_on, r_off = on.get("recall"), off.get("recall")
        agree = (r_on == r_off) if (on.get("status")=="OK" and off.get("status")=="OK") else None
        comparison["needle_recall_agreement"][key] = {"fa_on": r_on, "fa_off": r_off, "agree": agree,
            "on_status": on.get("status"), "off_status": off.get("status")}
        print(f"  {key} needle recall: FA_on={r_on} FA_off={r_off} agree={agree}"
              + (f"  (off {off.get('status')})" if off.get("status")!="OK" else ""))

    print("\n[restore] FA=1 deployed setting ...", flush=True)
    set_fa("1")
    _save(data, kern, cu, t0, comparison)
    print(f"[done] {round((time.time()-t0)/60,1)} min -> {OUT_PATH}")

def _save(data, kern, cu, t0, comparison=None):
    obj = {"step":"a3-flashattn","captured_at":datetime.datetime.now().isoformat(),"kernel":kern,"cu":cu,
           "models":MODELS,"ctx_sizes":CTX_SIZES,"fa_settings":FA_SETTINGS,"sampling":"temp=0 seed=42",
           "elapsed_min":round((time.time()-t0)/60,1),"data":data}
    if comparison: obj["comparison"] = comparison
    with open(OUT_PATH,"w") as f: json.dump(obj, f, indent=2)

if __name__ == "__main__":
    main()
