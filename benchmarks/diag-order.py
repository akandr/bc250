#!/usr/bin/env python3
"""Does a deterministic request depend on what preceded it in the session?

Step A9 scored mistral-nemo 3/3 on the arithmetic item; the identical request in
isolation fails.  The only difference is that A9 ran four other tasks first
against a loaded model.  If request history changes the answer, then a
temperature-0, fixed-seed benchmark is not reproducible unless the request
sequence is also fixed -- which would be a benchmark-validity finding, not a
curiosity.  This runs the arithmetic item cold, then after the four preceding
tasks, alternating, three times each.
"""
import json, subprocess, time, urllib.request
OLLAMA="http://127.0.0.1:11434"
ARITH=("Solve this arithmetic problem. Give ONLY the final numerical answer, nothing else.\n\n"
       "What is 17 × 23?\n\nAnswer:")
PRE=[("summarize","Read the following passage carefully, then write exactly 3 sentences summarizing the key points.\n\nPASSAGE:\nThe AMD BC-250 is a crypto-mining board built around AMD's Cyan Skillfish APU, featuring a Zen 2 CPU with 6 cores and 12 threads alongside a GFX1013 GPU with 24 compute units and 1536 stream processors. The board has 16 GB of GDDR6 unified memory shared between CPU and GPU (UMA architecture), with a 256-bit memory bus.\n\nYOUR 3-SENTENCE SUMMARY:"),
     ("json","Extract the following fields from the text below and return ONLY valid JSON.\n\nTEXT: \"Dr. Maria Chen, age 42, works as a Senior Research Scientist at NVIDIA.\"\n\nRequired fields: name, age, title, company\n\nJSON:"),
     ("fact","FACTS:\n- The chemical symbol for tungsten is W (from German: Wolfram).\n\nQUESTION: What is the chemical symbol for tungsten?\n\nANSWER:"),
     ("instr","List exactly 5 advantages of using solar energy. Format each as a numbered item (1. through 5.).")]
def gen(model,prompt,keep="10m"):
    r=urllib.request.Request(f"{OLLAMA}/api/generate",
        data=json.dumps({"model":model,"prompt":prompt,"stream":False,"think":False,
        "keep_alive":keep,"options":{"num_ctx":4096,"num_predict":300,
        "temperature":0.0,"seed":42}}).encode(),headers={"Content-Type":"application/json"})
    return json.loads(urllib.request.urlopen(r,timeout=300).read()).get("response","")
def unload(model):
    try: gen(model,"x",keep=0)
    except Exception: pass
    time.sleep(3)
for model in ["mistral-nemo:12b","deepseek-r1:14b"]:
    print("="*66); print(model)
    for trial in (1,2,3):
        unload(model)
        cold=gen(model,ARITH)
        for _,p in PRE: gen(model,p)
        warm=gen(model,ARITH)
        c391="391" in cold; w391="391" in warm
        print(f"  trial {trial}:  cold={cold.strip()[:40]!r} ({'PASS' if c391 else 'fail'})"
              f"   after-4-tasks={warm.strip()[:40]!r} ({'PASS' if w391 else 'fail'})"
              + ("   <-- ORDER MATTERS" if c391!=w391 else ""))
