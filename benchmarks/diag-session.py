#!/usr/bin/env python3
"""Is a temperature-0, fixed-seed Ollama generation reproducible across sessions?

A9 scored mistral-nemo 3/3 on the arithmetic item; the same request now returns a
wrong answer stably.  Within one session the result is stable, so the variation
must be session-level.  This restarts the server between trials and repeats the
same request, to separate within-session stability from across-session
reproducibility.  If they differ, a temperature-0 benchmark is repeatable only
within a server lifetime -- which is a benchmark-validity property worth knowing.
"""
import json, subprocess, time, urllib.request, collections
ARITH=("Solve this arithmetic problem. Give ONLY the final numerical answer, nothing else.\n\n"
       "What is 17 × 23?\n\nAnswer:")
def gen(model):
    r=urllib.request.Request("http://127.0.0.1:11434/api/generate",
        data=json.dumps({"model":model,"prompt":ARITH,"stream":False,"think":False,
        "keep_alive":"5m","options":{"num_ctx":4096,"num_predict":300,
        "temperature":0.0,"seed":42}}).encode(),headers={"Content-Type":"application/json"})
    return json.loads(urllib.request.urlopen(r,timeout=300).read()).get("response","").strip()
for model in ("mistral-nemo:12b","deepseek-r1:8b"):
    print("="*68); print(model)
    seen=collections.Counter()
    for s in range(1,6):
        subprocess.run(["sudo","systemctl","restart","ollama"],check=True); time.sleep(15)
        outs=[]
        for r in range(2):
            try: o=gen(model)
            except Exception as e: o=f"ERR {e}"[:30]
            outs.append(("391" in o, o[:38]))
        key=tuple(x[0] for x in outs); seen[key]+=1
        print(f"  session {s}: " + "  ".join(f"[{'PASS' if p else 'fail'}] {t!r}" for p,t in outs))
    print(f"  -> distinct across-session outcomes: {dict(seen)}")
