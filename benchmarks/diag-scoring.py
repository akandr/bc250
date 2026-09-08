#!/usr/bin/env python3
"""Replicate the A9 scoring path exactly and print what it saw."""
import sys, json, importlib.util, urllib.request
spec=importlib.util.spec_from_file_location("a9","/home/akandr/bench-a9-kv-competence.py")
a9=importlib.util.module_from_spec(spec)
sys.modules["a9"]=a9
# stop it running main()
src=open("/home/akandr/bench-a9-kv-competence.py").read().replace('if __name__ == "__main__":\n    main()','')
exec(compile(src,"a9","exec"), a9.__dict__)

for model in ("mistral-nemo:12b","deepseek-r1:14b","qwen3:8b"):
    print("="*66); print(model)
    for tname in ("arithmetic",):
        task=a9.QUALITY_TASKS[tname]
        print(f"  prompt repr: {task['prompt'][-40:]!r}")
        for run in (1,2,3):
            r=urllib.request.Request("http://127.0.0.1:11434/api/generate",
                data=json.dumps({"model":model,"prompt":task["prompt"],"stream":False,
                "think":False,"keep_alive":"10m","options":{"num_ctx":4096,"num_predict":300,
                "temperature":0.0,"seed":42}}).encode(),headers={"Content-Type":"application/json"})
            resp=json.loads(urllib.request.urlopen(r,timeout=300).read())
            txt=resp.get("response","")
            sc=a9.check_quality(tname,txt,task["checks"])
            print(f"    run{run}: response={txt.strip()[:60]!r}")
            print(f"           scores={sc}")
            print(f"           task_passed()={a9.task_passed(tname,sc)}")
