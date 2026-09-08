#!/usr/bin/env python3
"""Replay A9's exact task sequence: is the arithmetic outcome sequence-dependent?

Session restart, request order and request position have each been ruled out.
The only remaining difference between A9 and the isolated re-tests is the exact
set of preceding prompts.  This replays A9's loop verbatim for one model and
reports whether the arithmetic item reproduces its A9 score.
"""
import sys, json, importlib.util, urllib.request, subprocess, time
src=open("/home/akandr/bench-a9-kv-competence.py").read().replace(
    'if __name__ == "__main__":\n    main()','')
a9={}; exec(compile(src,"a9","exec"), a9)
QT=a9["QUALITY_TASKS"]; ck=a9["check_quality"]; tp=a9["task_passed"]
def gen(model,prompt,npred=300):
    r=urllib.request.Request("http://127.0.0.1:11434/api/generate",
        data=json.dumps({"model":model,"prompt":prompt,"stream":False,"think":False,
        "keep_alive":"10m","options":{"num_ctx":4096,"num_predict":npred,
        "temperature":0.0,"seed":42}}).encode(),headers={"Content-Type":"application/json"})
    return json.loads(urllib.request.urlopen(r,timeout=300).read()).get("response","")
for model in ("mistral-nemo:12b","deepseek-r1:14b"):
    for trial in (1,2):
        subprocess.run(["sudo","systemctl","restart","ollama"],check=True); time.sleep(15)
        gen(model,"Hello",10)                      # A9's warmup
        per={}
        for tname,task in QT.items():              # A9's exact order and repetition
            c=0
            for _ in range(3):
                c += tp(tname, ck(tname, gen(model,task["prompt"]), task["checks"]))
            per[tname]=c
        print(f"  {model:20s} trial {trial}: {per}   arithmetic={per['arithmetic']}/3")
