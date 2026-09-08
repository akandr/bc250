#!/usr/bin/env python3
"""Diagnostic: why does the arithmetic task fail at Q8_0 KV but pass at Q4_0?

Step A9 recorded 3/3 -> 0/3 on the arithmetic item for three models when the KV
cache was widened from Q4_0 to Q8_0.  Widening the cache should not lose tasks,
so this is either a real greedy-path flip or an artefact of the harness.  A9 did
not retain the responses, so this re-runs just that item on the affected models
at both widths and prints what the model actually said.
"""
import json, re, subprocess, time, urllib.request, sys
OLLAMA="http://127.0.0.1:11434"; OVERRIDE="/etc/systemd/system/ollama.service.d/override.conf"
PROMPT=("Solve this arithmetic problem. Give ONLY the final numerical answer, nothing else.\n\n"
        "What is 17 × 23?\n\nAnswer:")
MODELS=["deepseek-r1:14b","deepseek-r1:8b","mistral-nemo:12b","qwen3:8b"]
def api(ep,d,t=300):
    r=urllib.request.Request(f"{OLLAMA}{ep}",data=json.dumps(d).encode(),
                             headers={"Content-Type":"application/json"})
    return json.loads(urllib.request.urlopen(r,timeout=t).read())
def set_kv(v):
    c=open(OVERRIDE).read(); line=f'Environment="OLLAMA_KV_CACHE_TYPE={v}"'
    c=re.sub(r'Environment="OLLAMA_KV_CACHE_TYPE=[^"]*"',line,c) if "OLLAMA_KV_CACHE_TYPE" in c \
      else c.replace("[Service]",f"[Service]\n{line}",1)
    open("/tmp/_d.conf","w").write(c)
    subprocess.run(f"sudo cp /tmp/_d.conf {OVERRIDE}",shell=True,check=True)
    subprocess.run(["sudo","systemctl","daemon-reload"],check=True)
    subprocess.run(["sudo","systemctl","restart","ollama"],check=True); time.sleep(15)
out={}
for kv in ("q4_0","q8_0"):
    set_kv(kv)
    for m in MODELS:
        try:
            r=api("/api/generate",{"model":m,"prompt":PROMPT,"stream":False,"think":False,
                  "keep_alive":"5m","options":{"num_ctx":4096,"num_predict":300,
                  "temperature":0.0,"seed":42}},300)
            txt=r.get("response","")
            out.setdefault(m,{})[kv]={"eval_count":r.get("eval_count"),
                "done_reason":r.get("done_reason"),"has_391":"391" in txt,
                "len":len(txt),"head":txt[:200],"tail":txt[-160:] if len(txt)>200 else ""}
        except Exception as e:
            out.setdefault(m,{})[kv]={"error":str(e)[:150]}
set_kv("q4_0")
for m,v in out.items():
    print("="*70); print(m)
    for kv in ("q4_0","q8_0"):
        d=v.get(kv,{})
        print(f"  --- {kv}: 391={d.get('has_391')} tokens={d.get('eval_count')} "
              f"reason={d.get('done_reason')} len={d.get('len')}")
        print(f"      head: {d.get('head','')[:180]!r}")
        if d.get('tail'): print(f"      tail: {d.get('tail')[:150]!r}")
