#!/usr/bin/env python3
"""Does the answer depend on the request's position in the session?

Across-session tests showed request 1 is bit-reproducible but request 1 and
request 2 differ from each other.  The paper's Phase-4 battery runs five tasks
three times each, so its arithmetic item is request 13-15 of the session, not
request 1.  This asks the identical question at increasing positions in a fresh
session: if the answer changes with position, then temperature 0 plus a fixed
seed is not sufficient for reproducibility on this stack -- the request sequence
has to be fixed too.
"""
import json, subprocess, time, urllib.request
ARITH=("Solve this arithmetic problem. Give ONLY the final numerical answer, nothing else.\n\n"
       "What is 17 × 23?\n\nAnswer:")
FILLER="Write one short sentence about semiconductors."
def gen(p,model,npred=300):
    r=urllib.request.Request("http://127.0.0.1:11434/api/generate",
        data=json.dumps({"model":model,"prompt":p,"stream":False,"think":False,
        "keep_alive":"10m","options":{"num_ctx":4096,"num_predict":npred,
        "temperature":0.0,"seed":42}}).encode(),headers={"Content-Type":"application/json"})
    return json.loads(urllib.request.urlopen(r,timeout=300).read()).get("response","").strip()
for model in ("mistral-nemo:12b",):
    for probe_at in (1, 5, 13):
        subprocess.run(["sudo","systemctl","restart","ollama"],check=True); time.sleep(15)
        for i in range(probe_at-1): gen(FILLER,model,40)
        out=gen(ARITH,model)
        print(f"  {model}  probe as request #{probe_at:>2}: {out[:44]!r}  "
              f"{'PASS' if '391' in out else 'fail'}")
