#!/usr/bin/env python3
"""A10: the competence-probe tier distribution, re-measured on the canonical stack.

The tier distribution reported in the paper was collected under Ollama 0.18.0 on
the patched 40-CU configuration, and the rule that turned per-check scores into a
per-model percentage was never written down: bench-rigorous.py records a
quality_scores dict per run and stops there.  The table therefore could not be
regenerated from the released artefacts.

This harness closes that.  It runs the same five tasks with the same checks --
both copied verbatim from bench-rigorous.py -- on the canonical stack (Ollama
0.20.0, verified-stock 24 CU, the deployed Q4_0 KV cache), states the aggregation
rule explicitly, and writes every raw response so any reader can re-score without
re-running anything.

AGGREGATION RULE (the part that was previously implicit):
  A task-run passes iff every check defined for that task passes.
    summarize          keywords_pass AND sentences_pass
    json_extract       valid_json AND keys_pass
    fact_recall        keywords_pass
    instruction_follow items_pass
    arithmetic         contains_target
  A model's score is passed task-runs / (5 tasks x N runs).
No partial credit, no LLM judge, no human annotation.
"""
import json, os, re, subprocess, sys, time, datetime, urllib.request

OLLAMA = "http://127.0.0.1:11434"
OUT    = os.path.expanduser("~/phase-c-out/results/step-a10-competence-canonical.json")
RUNS   = 3
KV_TYPES = ["q4_0", "q8_0"]     # deployed width first, then the wider reference
OVERRIDE = "/etc/systemd/system/ollama.service.d/override.conf"
sys.stdout.reconfigure(line_buffering=True)
def log(m): print(f"[{datetime.datetime.now():%H:%M:%S}] {m}", flush=True)

QUALITY_TASKS = {
    "summarize": {
        "prompt": """Read the following passage carefully, then write exactly 3 sentences summarizing the key points.

PASSAGE:
The AMD BC-250 is a crypto-mining board built around AMD's Cyan Skillfish APU, featuring a Zen 2 CPU with 6 cores and 12 threads alongside a GFX1013 GPU with 24 compute units and 1536 stream processors. The board has 16 GB of GDDR6 unified memory shared between CPU and GPU (UMA architecture), with a 256-bit memory bus. Originally deployed in multi-board mining racks by ASRock Rack, these boards became available on the secondary market after decommissioning. The GPU does not support ROCm's userspace libraries, making Vulkan the only viable compute path for AI inference. Despite lacking dedicated matrix multiplication hardware (tensor cores), the board can run a 35-billion parameter Mixture-of-Experts language model at 38 tokens per second using quantized weights and a Vulkan compute backend.

YOUR 3-SENTENCE SUMMARY:""",
        "checks": {
            "keywords": ["BC-250", "Vulkan", "16 GB", "35"],
            "min_sentences": 2,
            "max_sentences": 5,
        },
    },
    "json_extract": {
        "prompt": """Extract the following fields from the text below and return ONLY valid JSON. No explanation, no markdown, just the JSON object.

TEXT: "Dr. Maria Chen, age 42, works as a Senior Research Scientist at NVIDIA's Santa Clara campus. She joined in 2018 and specializes in GPU compiler optimization. Her team has 12 members and she holds 7 patents related to shader compilation."

Required fields: name, age, title, company, year_joined, team_size, patent_count

JSON:""",
        "checks": {
            "valid_json": True,
            "required_keys": ["name", "age", "title", "company"],
            "expected_values": {"name": "Maria Chen", "age": 42},
        },
    },
    "fact_recall": {
        "prompt": """I will give you some facts. Memorize them, then answer my question.

FACTS:
- The administrative capital of Myanmar is Naypyidaw (moved from Yangon in 2006).
- The deepest point in the ocean is the Challenger Deep at 10,935 meters.
- The chemical symbol for tungsten is W (from German: Wolfram).
- The speed of light in vacuum is exactly 299,792,458 meters per second.
- Ada Lovelace is widely regarded as the first computer programmer.

QUESTION: What is the chemical symbol for tungsten, and what word is it derived from?

ANSWER:""",
        "checks": {
            "keywords": ["W", "Wolfram"],
        },
    },
    "instruction_follow": {
        "prompt": """List exactly 5 advantages of using solar energy for electricity generation. Format each as a numbered item (1. through 5.). Be concise — one sentence per item. Do not include any introduction or conclusion, just the 5 numbered items.""",
        "checks": {
            "has_numbered_items": 5,
        },
    },
    "arithmetic": {
        "prompt": """Solve this arithmetic problem. Give ONLY the final numerical answer, nothing else.

What is 17 × 23?

Answer:""",
        "checks": {
            "contains_number": 391,
        },
    },
}


def check_quality(task_name, response, checks):
    """Score a response against objective checks. Returns dict of scores."""
    scores = {}
    text = response.strip()

    if "keywords" in checks:
        found = sum(1 for kw in checks["keywords"] if kw.lower() in text.lower())
        scores["keywords_found"] = found
        scores["keywords_total"] = len(checks["keywords"])
        scores["keywords_pass"] = found >= len(checks["keywords"]) * 0.5

    if "valid_json" in checks and checks["valid_json"]:
        # Try to extract JSON from response (model may wrap in markdown)
        json_text = text
        if "```json" in json_text:
            json_text = json_text.split("```json")[1].split("```")[0].strip()
        elif "```" in json_text:
            json_text = json_text.split("```")[1].split("```")[0].strip()
        try:
            parsed = json.loads(json_text)
            scores["valid_json"] = True
            if "required_keys" in checks:
                present = sum(1 for k in checks["required_keys"] if k in parsed)
                scores["keys_found"] = present
                scores["keys_total"] = len(checks["required_keys"])
                scores["keys_pass"] = present == len(checks["required_keys"])
            if "expected_values" in checks:
                matched = 0
                for k, v in checks["expected_values"].items():
                    if k in parsed:
                        pv = parsed[k]
                        if isinstance(v, str) and isinstance(pv, str):
                            if v.lower() in pv.lower():
                                matched += 1
                        elif pv == v:
                            matched += 1
                scores["values_matched"] = matched
                scores["values_total"] = len(checks["expected_values"])
        except (json.JSONDecodeError, IndexError):
            scores["valid_json"] = False

    if "has_numbered_items" in checks:
        expected = checks["has_numbered_items"]
        # Count numbered items (e.g., "1.", "2.", etc.)
        items = re.findall(r'^\s*\d+[\.\)]\s', text, re.MULTILINE)
        scores["numbered_items"] = len(items)
        scores["items_expected"] = expected
        scores["items_pass"] = len(items) == expected

    if "contains_number" in checks:
        target = str(checks["contains_number"])
        scores["contains_target"] = target in text
        # Also check for the number anywhere
        numbers = re.findall(r'\d+', text)
        scores["numbers_found"] = numbers[:10]

    if "min_sentences" in checks:
        # Rough sentence count
        sentences = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]
        scores["sentence_count"] = len(sentences)
        scores["sentences_pass"] = (
            checks.get("min_sentences", 0) <= len(sentences) <= checks.get("max_sentences", 999)
        )

    return scores




CHECK_KEYS = {
    "summarize":          ("keywords_pass", "sentences_pass"),
    "json_extract":       ("valid_json", "keys_pass"),
    "fact_recall":        ("keywords_pass",),
    "instruction_follow": ("items_pass",),
    "arithmetic":         ("contains_target",),
}

def task_passed(task_name, scores):
    """A task-run passes iff every check defined for that task passes."""
    keys = CHECK_KEYS[task_name]
    return all(bool(scores.get(k, False)) for k in keys)

def api(ep, data, timeout=600):
    r = urllib.request.Request(f"{OLLAMA}{ep}", data=json.dumps(data).encode(),
                               headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=timeout).read())

def foreign_clients():
    """Count non-Ollama processes holding a connection to the Ollama port.

    Other clients may drive the same server.  A request
    interleaved from another client changes the sequence a model sees, which this
    work shows can change a borderline task outcome, so the count is asserted at
    the start and recorded per model rather than assumed to be zero.
    """
    out = subprocess.run("ss -tnp 2>/dev/null | grep ':11434' | grep -v '\"ollama\"'",
                         shell=True, capture_output=True, text=True).stdout
    return len([l for l in out.splitlines() if l.strip()])

def set_kv(val):
    log(f"  setting OLLAMA_KV_CACHE_TYPE={val}")
    c = open(OVERRIDE).read()
    line = f'Environment="OLLAMA_KV_CACHE_TYPE={val}"'
    c = (re.sub(r'Environment="OLLAMA_KV_CACHE_TYPE=[^"]*"', line, c)
         if "OLLAMA_KV_CACHE_TYPE" in c else c.replace("[Service]", f"[Service]\n{line}", 1))
    open("/tmp/_a10.conf","w").write(c)
    subprocess.run(f"sudo cp /tmp/_a10.conf {OVERRIDE}", shell=True, check=True)
    subprocess.run(["sudo","systemctl","daemon-reload"], check=True)
    subprocess.run(["sudo","systemctl","restart","ollama"], check=True)
    time.sleep(15)
    got = subprocess.run("systemctl show ollama -p Environment", shell=True,
                         capture_output=True, text=True).stdout
    assert f"OLLAMA_KV_CACHE_TYPE={val}" in got, f"KV not applied: {got[:200]}"

def unload_all():
    try:
        ps = json.loads(urllib.request.urlopen(f"{OLLAMA}/api/ps", timeout=10).read())
        for m in ps.get("models", []):
            if m.get("name"):
                api("/api/generate", {"model": m["name"], "keep_alive": 0}, 30); time.sleep(1)
    except Exception: pass
    time.sleep(3)

def main():
    kern = subprocess.run(["uname","-r"], capture_output=True, text=True).stdout.strip()
    cu = subprocess.run("journalctl -k -b 0 | grep -o 'active_cu_number [0-9]*' | tail -1",
                        shell=True, capture_output=True, text=True).stdout.strip()
    ver = subprocess.run(["ollama","--version"], capture_output=True, text=True).stdout.strip()
    kv = subprocess.run("grep -o 'OLLAMA_KV_CACHE_TYPE=[a-z0-9_]*' "
                        "/etc/systemd/system/ollama.service.d/override.conf",
                        shell=True, capture_output=True, text=True).stdout.strip()
    models = sorted({m["name"] for m in
                     json.loads(urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=30).read())["models"]})
    fc = foreign_clients()
    assert fc == 0, (f"{fc} foreign client(s) connected to Ollama; stop the other "
                     f"services before measuring (see RESTORE-SERVICES.sh)")
    log(f"{kern} | {cu} | {ver} | {len(models)} models | foreign Ollama clients: {fc}")
    out = {"step":"a10-competence-canonical", "captured_at":datetime.datetime.now().isoformat(),
           "kernel":kern, "cu":cu, "ollama":ver, "kv_cache_type":kv, "runs":RUNS,
           "aggregation":"a task-run passes iff every check for that task passes; "
                         "model score = passed runs / (5 tasks x N runs)",
           "check_keys":{k:list(v) for k,v in CHECK_KEYS.items()},
           "models":models, "kv_types":KV_TYPES,
           "contamination_guard":"foreign Ollama client count asserted 0 at start and "
                                 "recorded per model; background services stopped",
           "cells":[]}
    t0=time.time()
    for kv in KV_TYPES:
      set_kv(kv)
      for model in models:
        unload_all()
        fc = foreign_clients()
        try:
            w = api("/api/generate", {"model":model, "prompt":"Hello", "stream":False,
                    "options":{"num_ctx":4096,"num_predict":10}}, 300)
            if "error" in w: raise RuntimeError(w["error"][:150])
        except Exception as e:
            log(f"  {model:32s} WARMUP FAIL: {e}")
            out["cells"].append({"model":model,"kv_type":kv,"status":"WARMUP_FAIL",
                                 "foreign_clients":fc,"error":str(e)[:250]})
            json.dump(out, open(OUT,"w"), indent=1); continue
        per={}; runs=[]; npass=ntot=0
        for tname, task in QUALITY_TASKS.items():
            tp=0
            for i in range(RUNS):
                try:
                    resp = api("/api/generate", {"model":model, "prompt":task["prompt"],
                        "stream":False, "think":False, "keep_alive":"10m",
                        "options":{"num_ctx":4096,"num_predict":300,
                                   "temperature":0.0,"seed":42}}, 600)
                    txt = resp.get("response","")
                    sc  = check_quality(tname, txt, task["checks"])
                    ok  = task_passed(tname, sc)
                    runs.append({"task":tname,"run":i+1,"passed":ok,"scores":sc,
                                 "response":txt, "eval_count":resp.get("eval_count")})
                except Exception as e:
                    ok=False
                    runs.append({"task":tname,"run":i+1,"passed":False,"error":str(e)[:250]})
                tp+=ok; ntot+=1
            per[tname]=tp; npass+=tp
        rate=100.0*npass/ntot if ntot else 0.0
        log(f"  {kv} {model:32s} {npass:2d}/{ntot} = {rate:5.1f}%  {per}"
            + ("" if fc==0 else f"  [!] {fc} foreign clients"))
        out["cells"].append({"model":model,"kv_type":kv,"status":"OK","passes":npass,
                             "total":ntot,"pass_rate":rate,"per_task":per,
                             "foreign_clients":fc,"runs":runs})
        json.dump(out, open(OUT,"w"), indent=1)
    set_kv("q4_0")   # leave the board on the deployed setting
    out["elapsed_min"]=round((time.time()-t0)/60,1)
    json.dump(out, open(OUT,"w"), indent=1)
    log(f"done -> {OUT}")

if __name__ == "__main__":
    main()
