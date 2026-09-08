#!/usr/bin/env python3
"""A9: does the competence probe's low-scoring tier reflect KV-cache width?

The paper's Phase-4 battery places four of the 31 cohort models at or below 40 %,
attributing them to think-token leakage or to being specialised/broken.  Step A7
then found that Q4_0 KV can destroy a model outright, and the deployed setting
throughout the paper is Q4_0.  A collapse would land in exactly that tier, so the
attribution needs checking rather than assuming.

This re-runs the paper's own five-task probe -- task prompts and scoring copied
verbatim from bench-rigorous.py -- at Q4_0 against Q8_0 KV, over every model the
Ollama path can serve.  That path is used deliberately: the models in question are
the ones llama.cpp b9265 cannot load, which is why they are absent from the
perplexity work.  A model whose score rises materially at Q8_0 was a KV collapse;
one that scores the same was not.

Canonical stack: Ollama 0.20.0, verified-stock 24 CU, oberon 1500 MHz.
"""
import json, os, re, subprocess, sys, time, datetime, urllib.request

OLLAMA        = "http://127.0.0.1:11434"
OVERRIDE_CONF = "/etc/systemd/system/ollama.service.d/override.conf"
OUT           = os.path.expanduser("~/phase-c-out/results/step-a9-kv-competence.json")
KV_TYPES      = ["q4_0", "q8_0"]
RUNS          = 3
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




def api(endpoint, data, timeout=300):
    req = urllib.request.Request(f"{OLLAMA}{endpoint}",
        data=json.dumps(data).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def set_kv(val):
    log(f"  setting OLLAMA_KV_CACHE_TYPE={val}")
    content = open(OVERRIDE_CONF).read()
    line = f'Environment="OLLAMA_KV_CACHE_TYPE={val}"'
    if "OLLAMA_KV_CACHE_TYPE" in content:
        content = re.sub(r'Environment="OLLAMA_KV_CACHE_TYPE=[^"]*"', line, content)
    else:
        content = content.replace("[Service]", f"[Service]\n{line}", 1)
    open("/tmp/_a9_override.conf", "w").write(content)
    subprocess.run(f"sudo cp /tmp/_a9_override.conf {OVERRIDE_CONF}", shell=True, check=True)
    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
    subprocess.run(["sudo", "systemctl", "restart", "ollama"], check=True)
    time.sleep(15)
    # confirm the runtime actually took the setting
    got = subprocess.run("systemctl show ollama -p Environment", shell=True,
                         capture_output=True, text=True).stdout
    assert f"OLLAMA_KV_CACHE_TYPE={val}" in got, f"KV type not applied: {got[:200]}"

def unload_all():
    try:
        for m in (api("/api/ps", {}, 10) if False else json.loads(
                urllib.request.urlopen(f"{OLLAMA}/api/ps", timeout=10).read())).get("models", []):
            if m.get("name"):
                api("/api/generate", {"model": m["name"], "keep_alive": 0}, 30); time.sleep(1)
    except Exception: pass
    time.sleep(3)

def task_passed(task_name, scores):
    """A task counts as passed when all of its checks pass -- the paper's rule."""
    keys = [k for k in ("keywords_pass","keys_pass","items_pass","sentences_pass") if k in scores]
    ok = all(scores[k] for k in keys) if keys else True
    if "valid_json" in scores: ok = ok and scores["valid_json"]
    if "contains_target" in scores: ok = ok and scores["contains_target"]
    return ok

def list_models():
    d = json.loads(urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=30).read())
    return sorted({m["name"] for m in d.get("models", [])})

def main():
    kern = subprocess.run(["uname","-r"],capture_output=True,text=True).stdout.strip()
    cu = subprocess.run("journalctl -k -b 0 | grep -o 'active_cu_number [0-9]*' | tail -1",
                        shell=True,capture_output=True,text=True).stdout.strip()
    models = list_models()
    log(f"kernel={kern} {cu}; {len(models)} models: {models}")
    out = {"step":"a9-kv-competence","captured_at":datetime.datetime.now().isoformat(),
           "kernel":kern,"cu":cu,"kv_types":KV_TYPES,"runs":RUNS,"models":models,"cells":[]}
    t0=time.time()
    for kv in KV_TYPES:
        set_kv(kv)
        for model in models:
            unload_all()
            try:
                w = api("/api/generate", {"model": model, "prompt": "Hello", "stream": False,
                        "options": {"num_ctx": 4096, "num_predict": 10}}, 180)
                if "error" in w: raise RuntimeError(w["error"][:120])
            except Exception as e:
                log(f"  {kv} {model}: WARMUP FAIL {e}")
                out["cells"].append({"kv_type":kv,"model":model,"status":"WARMUP_FAIL","error":str(e)[:200]})
                json.dump(out, open(OUT,"w"), indent=1); continue
            npass=ntot=0; per={}
            for tname, task in QUALITY_TASKS.items():
                tp=0
                for r in range(RUNS):
                    try:
                        resp = api("/api/generate", {"model": model, "prompt": task["prompt"],
                            "stream": False, "think": False, "keep_alive": "10m",
                            "options": {"num_ctx":4096,"num_predict":300,"temperature":0.0,"seed":42}}, 300)
                        sc = check_quality(tname, resp.get("response",""), task["checks"])
                        ok = task_passed(tname, sc)
                    except Exception:
                        ok = False
                    tp += ok; ntot += 1
                per[tname]=tp; npass+=tp
            rate = 100.0*npass/ntot if ntot else 0.0
            log(f"  {kv} {model:34s} {npass:2d}/{ntot} = {rate:5.1f}%   {per}")
            out["cells"].append({"kv_type":kv,"model":model,"status":"OK","passes":npass,
                                 "total":ntot,"pass_rate":rate,"per_task":per})
            json.dump(out, open(OUT,"w"), indent=1)
    set_kv("q4_0")   # restore the paper's deployed setting
    out["elapsed_min"]=round((time.time()-t0)/60,1)
    json.dump(out, open(OUT,"w"), indent=1)
    log(f"done -> {OUT}")

if __name__ == "__main__":
    main()
