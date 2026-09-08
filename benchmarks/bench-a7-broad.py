#!/usr/bin/env python3
"""A7: broad characterisation of the Q4_0 KV-cache quality penalty.

Steps A5/A6 established that 4-bit KV costs perplexity on four of five models and
nothing on the fifth, and could not say why.  The fifth was the only one carrying
Q8_0 *weights*, which suggests a testable hypothesis: the KV penalty depends on
the precision of the weights it is combined with.  This step tests that directly
and widens every other axis at the same time.

  P1  Controlled weight-quantisation pair.  qwen3:8b at Q4_K_M against the same
      base model at Q8_0 -- identical architecture and training, weight precision
      the only difference.  This is the decisive cell.
  P2  Downstream task accuracy.  HellaSwag and Winogrande at FP16 vs Q4_0 KV:
      perplexity has no calibrated mapping onto task quality, so this asks
      whether the shift is visible in an accuracy metric at all.
  P3  Broad perplexity sweep.  Nine further models, 1.5 B to 35 B, Q4_K_M through
      IQ2_M/IQ3_XXS, at FP16 / Q8_0 / Q4_0 KV.
  P4  KL divergence on more models, so the second metric family is not resting on
      a single model.

Ordered by value: if the run is interrupted, the decisive results are already in.
Canonical stack: llama.cpp b9265, verified-stock 24 CU, oberon 1500 MHz.
"""
import json, math, os, re, subprocess, sys, time, datetime

BIN   = "/opt/llama.cpp-b9265/build/bin/llama-perplexity"
WIKI  = "/home/akandr/wikitext-2-raw/wiki.test.raw"
HELLA = "/home/akandr/hellaswag_val_full.txt"
WINO  = "/home/akandr/winogrande-debiased-eval.csv"
OUT   = os.path.expanduser("~/phase-c-out/results/step-a7-broad.json")
RAW   = os.path.expanduser("~/phase-c-out/results/a7-raw")
KLDIR = "/var/tmp/a7-kl"
TOKENS = 98304
R = "/usr/share/ollama/.ollama/models/blobs"

# (name, path, weight quant, GiB)  -- context is derived from size below
M = {
 "qwen3-8b-q4km":     (f"{R}/sha256-a3de86cd1c132c822487ededd47a324c50491393e6565cd14bafa40d0b8e686f","Q4_K_M",4.87),
 "qwen3-8b-q8_0":     ("/opt/models/qwen3-8b-q8_0.gguf","Q8_0",8.24),
 "qwen2.5-1.5b":      ("/opt/models/qwen2.5-1.5b-q4km.gguf","Q4_K_M",1.04),
 "llama3.2-3b":       (f"{R}/sha256-dde5aa3fc5ffc17176b5e8bdc82f587b24b2678c6c66101bf7da77af9f7ccdff","Q4_K_M",1.88),
 "qwen3-4b":          (f"{R}/sha256-3e4cb14174460404e7a233e531675303b2fbf7749c02f91864fe311ab6344e4f","Q4_K_M",2.33),
 "deepseek-r1-8b":    (f"{R}/sha256-e6a7edc1a4d7d9b2de136a221a57336b76316cfe53a252aeba814496c5ae439d","Q4_K_M",4.87),
 "mistral-nemo-12b":  (f"{R}/sha256-b559938ab7a0392fc9ea9675b82280f2a15669ec3e0e0fc491c9cb0a7681cf94","Q4_0",6.59),
 "qwen3.6-35b-a3b":   ("/opt/models/qwen3.6-35b-a3b-iq2m.gguf","IQ2_M",10.73),
 "qwen3.8-27b":       ("/opt/models/qwen3.8-27b-iq3xxs.gguf","IQ3_XXS",11.10),
 "gemma4-26b-q3":     (f"{R}/sha256-b6aa7809d25f71031b82528ec88877830cb88bf7d210ff291a1a9fbe48e7df37","Q3",11.67),
 "phi4-mini":         (f"{R}/sha256-3c168af1dea0a414299c7d9077e100ac763370e5a98b3c53801a958a47f0a5db","Q4_K_M",2.32),
 "deepseek-r1-14b":   ("/opt/models/deepseek-r1-14b.gguf","Q4_K_M",8.37),
 "qwen3-14b":         ("/opt/models/qwen3-14b.gguf","Q4_K_M",8.64),
}
def ctx_for(gib): return 2048 if gib >= 10.0 else 4096

os.makedirs(RAW, exist_ok=True); os.makedirs(KLDIR, exist_ok=True)
sys.stdout.reconfigure(line_buffering=True)
def log(m): print(f"[{datetime.datetime.now():%H:%M:%S}] {m}", flush=True)

def gpu_temp():
    for h in ("/sys/class/drm/card0/device/hwmon","/sys/class/drm/card1/device/hwmon"):
        try:
            for d in os.listdir(h):
                p=os.path.join(h,d,"temp1_input")
                if os.path.exists(p): return int(open(p).read().strip())/1000.0
        except Exception: pass
    return None
def mem_settle(min_mb=4000,max_wait=180):
    subprocess.run("sync; echo 3 | sudo tee /proc/sys/vm/drop_caches",shell=True,capture_output=True)
    t0=time.time()
    while time.time()-t0<max_wait:
        try:
            i={l.split(":")[0]:int(l.split()[1]) for l in open("/proc/meminfo")}
            if i.get("MemAvailable",0)/1024>=min_mb: break
        except Exception: break
        time.sleep(5)
    time.sleep(5)
def cooldown(limit=72,max_wait=600):
    t0=time.time()
    while time.time()-t0<max_wait:
        t=gpu_temp()
        if t is None or t<=limit: return t
        time.sleep(10)
    return gpu_temp()
def per_chunk_nll(run):
    o=[]
    for n,p in enumerate(run,1):
        if p<=0: o.append(None); continue
        o.append(n*math.log(p)-((n-1)*math.log(run[n-2]) if n>1 and run[n-2]>0 else 0.0))
    return o

CELLS=[]
def save(): json.dump({"step":"a7-broad","captured_at":STARTED,"kernel":KERN,"cu":CU,
                       "binary":BIN,"token_budget":TOKENS,"models":{k:{"quant":v[1],"gib":v[2]} for k,v in M.items()},
                       "cells":CELLS}, open(OUT,"w"), indent=1)

def run(part, model, kv, fa="on", mode="ppl", tasks=None, ctx=None, chunks=None, extra=None, tag_x=""):
    path,quant,gib = M[model]
    ctx = ctx or ctx_for(gib)
    tag=f"{part}__{model}__{mode}__{kv}__fa{fa}__c{ctx}{tag_x}"
    cmd=["sudo",BIN,"-m",path,"-ngl","99","-c",str(ctx),"-fa",fa,"-ctk",kv,"-ctv",kv,"--seed","42"]
    if mode=="ppl":     cmd+= ["-f",WIKI,"--chunks",str(chunks or max(1,TOKENS//ctx))]
    elif mode=="hella": cmd+= ["-f",HELLA,"--hellaswag","--hellaswag-tasks",str(tasks)]
    elif mode=="wino":  cmd+= ["-f",WINO,"--winogrande","--winogrande-tasks",str(tasks)]
    cmd+= (extra or [])
    mem_settle(); tp=cooldown()
    log(f"  {tag}: start (edge {tp}C)")
    t0=time.time()
    for attempt in (1,2):
        pr=subprocess.run(cmd,capture_output=True,text=True,timeout=28800)
        blob=pr.stdout+pr.stderr
        if "Failed to allocate a buffer" not in blob or attempt==2: break
        log(f"  {tag}: transient RADV failure, settling"); mem_settle(5000,300)
    wall=time.time()-t0
    open(os.path.join(RAW,tag+".log"),"w").write(blob)
    c={"part":part,"model":model,"weight_quant":quant,"model_gib":gib,"kv_type":kv,
       "flash_attn":fa,"mode":mode,"n_ctx":ctx,"wall_s":round(wall,1),"tag":tag,
       "edge_c_pre":tp,"status":"FAILED"}
    if mode=="ppl":
        rn=[float(x) for x in re.findall(r"\[\d+\]([\d.]+)",blob)]
        m=re.search(r"Final estimate: PPL = ([\d.]+) \+/- ([\d.]+)",blob)
        if m: c.update(ppl=float(m.group(1)),ppl_stderr=float(m.group(2)),
                       per_chunk_nll=per_chunk_nll(rn),chunks_seen=len(rn),status="OK")
        k=re.search(r"Mean\s+KLD:\s+([\d.eE+-]+)",blob)
        if k:
            c["mean_kld"]=float(k.group(1)); c["status"]="OK"
            for nm,pat in [("same_top1_pct",r"Same top p:\s*([\d.]+)"),
                           ("median_kld",r"Median\s+KLD:\s+([\d.eE+-]+)"),
                           ("ppl_ratio_base",r"Mean PPL\(Q\)/PPL\(base\)\s*:\s*([\d.]+)")]:
                mm=re.search(pat,blob)
                if mm: c[nm]=float(mm.group(1))
    else:
        acc=re.findall(r"^\s*(\d+)\s+([\d.]+)\s*$",blob,re.M)
        if acc: c.update(tasks=int(acc[-1][0]),accuracy=float(acc[-1][1]),status="OK")
    log(f"  {tag}: " + (f"PPL {c.get('ppl')}" if c.get('ppl') else "")
        + (f" KLD {c.get('mean_kld')}" if c.get('mean_kld') else "")
        + (f"acc {c.get('accuracy')}% over {c.get('tasks')}" if c.get('accuracy') else "")
        + ("" if c["status"]=="OK" else "FAILED") + f" ({wall/60:.1f} min)")
    CELLS.append(c); save(); return c

STARTED=datetime.datetime.now().isoformat()
KERN=subprocess.run(["uname","-r"],capture_output=True,text=True).stdout.strip()
CU=subprocess.run("journalctl -k -b 0 | grep -o 'active_cu_number [0-9]*' | tail -1",
                  shell=True,capture_output=True,text=True).stdout.strip()

# --- P1: the decisive controlled pair -----------------------------------------
log("=== P1 controlled weight-quantisation pair (qwen3:8b, Q4_K_M vs Q8_0) ===")
for mo in ("qwen3-8b-q4km","qwen3-8b-q8_0"):
    for kv in ("f16","q8_0","q4_0"):
        run("P1_weightquant",mo,kv)

# --- P2: does it show up in task accuracy? ------------------------------------
log("=== P2 downstream task accuracy (HellaSwag / Winogrande) ===")
for mo in ("qwen3-8b-q4km","qwen3-8b-q8_0","phi4-mini","qwen3-14b"):
    for kv in ("f16","q4_0"):
        run("P2_hellaswag",mo,kv,mode="hella",tasks=400,ctx=2048)
for mo in ("qwen3-8b-q4km","qwen3-8b-q8_0","phi4-mini"):
    for kv in ("f16","q4_0"):
        run("P2_winogrande",mo,kv,mode="wino",tasks=400,ctx=2048)

# --- P3: broad perplexity sweep -----------------------------------------------
log("=== P3 broad perplexity sweep ===")
for mo in ("qwen2.5-1.5b","llama3.2-3b","qwen3-4b","deepseek-r1-8b",
           "mistral-nemo-12b","qwen3.6-35b-a3b","qwen3.8-27b","gemma4-26b-q3"):
    for kv in ("f16","q8_0","q4_0"):
        run("P3_broad",mo,kv)

# --- P4: KL divergence on more models -----------------------------------------
log("=== P4 KL divergence, additional models ===")
for mo in ("qwen3-8b-q4km","qwen3-8b-q8_0","deepseek-r1-14b","qwen3-14b"):
    base=os.path.join(KLDIR,f"{mo}-base.dat")
    try:
        run("P4_klbase",mo,"f16",ctx=2048,chunks=4,extra=["--kl-divergence-base",base],tag_x="__base")
        if os.path.exists(base) and os.path.getsize(base)>0:
            for kv,fa in (("q8_0","on"),("q4_0","on"),("f16","off")):
                run("P4_kld",mo,kv,fa=fa,ctx=2048,chunks=4,
                    extra=["--kl-divergence","--kl-divergence-base",base])
    except Exception as e:
        log(f"  {mo} KL skipped: {e}")
    finally:
        subprocess.run(f"sudo rm -f {base}",shell=True,capture_output=True)
subprocess.run(f"sudo rm -rf {KLDIR}",shell=True,capture_output=True)

save(); log(f"done -> {OUT}")
