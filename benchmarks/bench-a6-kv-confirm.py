#!/usr/bin/env python3
"""A6: confirmation battery for the Q4_0 KV-cache quality penalty.

A single perplexity sweep (step A5) indicated that Q4_0 KV costs 0.3-1.4 %
perplexity, contradicting the task-level null result.  Overturning a claim in
the paper on one measurement is not sound, so this battery attacks the same
question along five independent axes.  The A5 result stands only if it survives
all of them.

  R1 determinism  Repeat identical cells.  If run-to-run spread is comparable to
                  the effect, there is no effect.  This is the control that must
                  pass before any of the others mean anything.
  R2 corpus       Repeat on wiki.valid.raw, a split never used in A5.  Guards
                  against a corpus-specific artefact.
  R3 context      Sweep n_ctx.  KV quantisation error should grow with the number
                  of quantised KV entries; a penalty that is flat in context, or
                  shrinks, would not be a KV effect.
  R4 models       Widen the model base beyond A5's three.
  R5 metric       KL divergence of the output distribution against an FP16-KV
                  reference: a different metric family, and a direct measure of
                  how far quantised KV moves the model's predictions.

Canonical stack: llama.cpp b9265, verified-stock 24 CU, oberon 1500 MHz.
"""
import json, math, os, re, subprocess, sys, time, datetime

BIN     = "/opt/llama.cpp-b9265/build/bin/llama-perplexity"
WIKI_T  = "/home/akandr/wikitext-2-raw/wiki.test.raw"
WIKI_V  = "/home/akandr/wikitext-2-raw/wiki.valid.raw"
OUT     = os.path.expanduser("~/phase-c-out/results/step-a6-kv-confirm.json")
RAW_DIR = os.path.expanduser("~/phase-c-out/results/a6-raw")
KL_DIR  = "/var/tmp/a6-kl"
TOKENS  = 98304

PHI = ("phi4-mini", "/usr/share/ollama/.ollama/models/blobs/"
       "sha256-3c168af1dea0a414299c7d9077e100ac763370e5a98b3c53801a958a47f0a5db")
MOE = ("qwen3.5-35b-a3b-iq2m", "/usr/share/ollama/.ollama/models/blobs/"
       "sha256-187b5bbb81d7ec1dc228cb04120302cc0d486cb3c988501af87e5f02e15ace0e")
DSK = ("deepseek-r1-14b", "/opt/models/deepseek-r1-14b.gguf")
Q14 = ("qwen3-14b", "/opt/models/qwen3-14b.gguf")
Q8Q = ("qwen3-8b-q8_0", "/opt/models/qwen3-8b-q8_0.gguf")

os.makedirs(RAW_DIR, exist_ok=True); os.makedirs(KL_DIR, exist_ok=True)
sys.stdout.reconfigure(line_buffering=True)
def log(m): print(f"[{datetime.datetime.now():%H:%M:%S}] {m}", flush=True)

def gpu_temp():
    for h in ("/sys/class/drm/card0/device/hwmon", "/sys/class/drm/card1/device/hwmon"):
        try:
            for d in os.listdir(h):
                p = os.path.join(h, d, "temp1_input")
                if os.path.exists(p): return int(open(p).read().strip())/1000.0
        except Exception: pass
    return None

def mem_settle(min_avail_mb=4000, max_wait=180):
    subprocess.run("sync; echo 3 | sudo tee /proc/sys/vm/drop_caches", shell=True, capture_output=True)
    t0=time.time()
    while time.time()-t0 < max_wait:
        try:
            info={l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
            if info.get("MemAvailable",0)/1024 >= min_avail_mb: break
        except Exception: break
        time.sleep(5)
    time.sleep(5)

def cooldown(limit=72, max_wait=600):
    t0=time.time()
    while time.time()-t0 < max_wait:
        t=gpu_temp()
        if t is None or t <= limit: return t
        time.sleep(10)
    return gpu_temp()

def per_chunk_nll(running):
    out=[]
    for n,p in enumerate(running, start=1):
        if p<=0: out.append(None); continue
        cur=n*math.log(p)
        prev=(n-1)*math.log(running[n-2]) if n>1 and running[n-2]>0 else 0.0
        out.append(cur-prev)
    return out

RESULTS=[]
def save():
    json.dump({"step":"a6-kv-confirm","captured_at":STARTED,"kernel":KERN,"cu":CU,
               "binary":BIN,"token_budget":TOKENS,"cells":RESULTS}, open(OUT,"w"), indent=1)

def run(axis, model, path, ctx, kv, corpus, rep=1, fa="on", chunks=None, extra=None, tag_extra=""):
    chunks = chunks or max(1, TOKENS//ctx)
    tag=f"{axis}__{model}__{os.path.basename(corpus).split('.')[1]}__c{ctx}__{kv}__fa{fa}__r{rep}{tag_extra}"
    cmd=["sudo",BIN,"-m",path,"-f",corpus,"-ngl","99","-c",str(ctx),"--chunks",str(chunks),
         "-fa",fa,"-ctk",kv,"-ctv",kv,"--seed","42"]+(extra or [])
    mem_settle(); t_pre=cooldown()
    log(f"  {tag}: start (edge {t_pre}C)")
    t0=time.time()
    proc=subprocess.run(cmd,capture_output=True,text=True,timeout=21600)
    wall=time.time()-t0
    blob=proc.stdout+proc.stderr
    open(os.path.join(RAW_DIR,tag+".log"),"w").write(blob)
    running=[float(x) for x in re.findall(r"\[\d+\]([\d.]+)",blob)]
    m=re.search(r"Final estimate: PPL = ([\d.]+) \+/- ([\d.]+)",blob)
    ppl,err=(float(m.group(1)),float(m.group(2))) if m else (None,None)
    kl=None
    km=re.search(r"Mean KLD:\s+([\d.eE+-]+)",blob)
    if km: kl=float(km.group(1))
    r={"axis":axis,"model":model,"n_ctx":ctx,"kv_type":kv,"flash_attn":fa,
       "corpus":os.path.basename(corpus),"repeat":rep,"chunks":chunks,
       "ppl":ppl,"ppl_stderr":err,"mean_kld":kl,"chunks_seen":len(running),
       "per_chunk_nll":per_chunk_nll(running) if running else [],
       "wall_s":round(wall,1),"status":"OK" if (ppl is not None or kl is not None) else "FAILED",
       "edge_c_pre":t_pre,"tag":tag}
    log(f"  {tag}: " + (f"PPL {ppl}" if ppl else "") + (f" KLD {kl}" if kl else "")
        + ("" if r["status"]=="OK" else " FAILED") + f" ({wall/60:.1f} min)")
    RESULTS.append(r); save(); return r

STARTED=datetime.datetime.now().isoformat()
KERN=subprocess.run(["uname","-r"],capture_output=True,text=True).stdout.strip()
CU=subprocess.run("journalctl -k -b 0 | grep -o 'active_cu_number [0-9]*' | tail -1",
                  shell=True,capture_output=True,text=True).stdout.strip()

# --- R1 determinism: the control everything else depends on -------------------
log("=== R1 determinism (phi4-mini, wiki.test, c=4096) ===")
for rep in (2,3):
    for kv in ("f16","q4_0"):
        run("R1_determinism",PHI[0],PHI[1],4096,kv,WIKI_T,rep=rep)

# --- R2 independent corpus split ---------------------------------------------
log("=== R2 corpus (phi4-mini, wiki.valid, c=4096) ===")
for kv in ("f16","q8_0","q4_0"):
    run("R2_corpus",PHI[0],PHI[1],4096,kv,WIKI_V)

# --- R3 context scaling: does the penalty grow with KV entries? --------------
log("=== R3 context scaling (phi4-mini, wiki.test) ===")
for ctx in (1024,2048,8192):
    for kv in ("f16","q4_0"):
        run("R3_context",PHI[0],PHI[1],ctx,kv,WIKI_T)

# --- R4 more models ----------------------------------------------------------
log("=== R4 additional models ===")
for name,path in (Q8Q,Q14):
    for kv in ("f16","q4_0"):
        run("R4_models",name,path,4096,kv,WIKI_T)

# --- R5 repeats on the other two A5 models -----------------------------------
log("=== R5 determinism on MoE and deepseek ===")
for name,path,ctx in ((MOE[0],MOE[1],2048),(DSK[0],DSK[1],4096)):
    for kv in ("f16","q4_0"):
        run("R5_repeat",name,path,ctx,kv,WIKI_T,rep=2)

# --- R6 KL divergence: a different metric family -----------------------------
log("=== R6 KL divergence (phi4-mini, c=2048, 4 chunks) ===")
base=os.path.join(KL_DIR,"phi4-mini-f16-base.dat")
try:
    run("R6_klbase",PHI[0],PHI[1],2048,"f16",WIKI_T,chunks=4,
        extra=["--kl-divergence-base",base],tag_extra="__savebase")
    sz=os.path.getsize(base)/2**30 if os.path.exists(base) else 0
    log(f"  KL base written: {sz:.2f} GiB")
    if sz>0:
        for kv,fa in (("q8_0","on"),("q4_0","on"),("f16","off")):
            run("R6_kld",PHI[0],PHI[1],2048,kv,WIKI_T,chunks=4,fa=fa,
                extra=["--kl-divergence","--kl-divergence-base",base])
except Exception as e:
    log(f"  R6 skipped: {e}")
finally:
    subprocess.run(f"sudo rm -rf {KL_DIR}", shell=True, capture_output=True)

save(); log(f"done -> {OUT}")
