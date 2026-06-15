#!/usr/bin/env python3
"""
gemma4-26b-q3 fitness eval for the Signal-chat / netscan primary role.

Three things we want to know before committing it as the primary text model:
  1. Quality battery  — the same 5 tasks used in §B4 (it has no score there).
  2. Synthesis        — 3 representative "think"-style jobs (career/company/market).
  3. Parser compat    — does its output survive the PRODUCTION parsers in
                        queue-runner.py?  Specifically the `^EXEC:` anchor and
                        json.loads, and whether chain-of-thought leaks when
                        Ollama `think=False` (the chat default).

Run ON THE BOARD (talks to localhost:11434). Stop queue-runner first to avoid
OOM/model-swap contention:

    sudo systemctl stop queue-runner
    python3 bench-gemma4-eval.py
    sudo systemctl start queue-runner
"""
import json, re, sys, time, urllib.request

MODEL   = sys.argv[1] if len(sys.argv) > 1 else "gemma4-26b-q3"
OLLAMA  = "http://127.0.0.1:11434"
N_RUNS  = 2
TIMEOUT = 300   # if a single capped call exceeds 5 min, the model is swap-bound
OUT     = f"/opt/netscan/tmp/eval-{MODEL.replace(':','-').replace('/','-')}.json"

sys.stdout.reconfigure(line_buffering=True)

# ── Production parsers, copied verbatim from queue-runner.py ──────────────────
RE_THINK = re.compile(r'<think>.*?</think>', re.DOTALL)          # line 1854
RE_EXEC  = re.compile(r'^EXEC:\s*(.+)$', re.MULTILINE)           # line 2129

def strip_think(text):
    return RE_THINK.sub('', text)

def find_exec(text):
    m = RE_EXEC.search(text)
    return m.group(1).strip() if m else None

# ── Ollama chat (mirrors the production call: think flag + num_ctx) ───────────
def chat(system, user, think=False, num_ctx=8192, temp=0.7, num_predict=512):
    payload = json.dumps({
        "model": MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user",   "content": user}],
        "stream": False,
        "think": think,
        "options": {"num_ctx": num_ctx, "temperature": temp,
                    "num_predict": num_predict},
    }).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/chat", data=payload,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:200]
        return {"content": "", "reasoning": "", "wall_s": round(time.time() - t0, 1),
                "eval_count": 0, "prompt_eval_count": 0,
                "error": f"HTTP {e.code}: {body}"}
    except Exception as e:
        return {"content": "", "reasoning": "", "wall_s": round(time.time() - t0, 1),
                "eval_count": 0, "prompt_eval_count": 0, "error": str(e)[:200]}
    msg = data.get("message", {})
    return {
        "content": (msg.get("content") or "").strip(),
        "reasoning": (msg.get("thinking") or msg.get("reasoning") or ""),
        "wall_s": round(time.time() - t0, 1),
        "eval_count": data.get("eval_count", 0),
        "prompt_eval_count": data.get("prompt_eval_count", 0),
    }

# ── 1. Quality battery (same prompts/checks as bench-rigorous.py §B4) ─────────
QUALITY_TASKS = {
    "summarize": {
        "prompt": """Read the following passage carefully, then write exactly 3 sentences summarizing the key points.

PASSAGE:
The AMD BC-250 is a crypto-mining board built around AMD's Cyan Skillfish APU, featuring a Zen 2 CPU with 6 cores and 12 threads alongside a GFX1013 GPU with 24 compute units and 1536 stream processors. The board has 16 GB of GDDR6 unified memory shared between CPU and GPU (UMA architecture), with a 256-bit memory bus. Originally deployed in multi-board mining racks by ASRock Rack, these boards became available on the secondary market after decommissioning. The GPU does not support ROCm's userspace libraries, making Vulkan the only viable compute path for AI inference. Despite lacking dedicated matrix multiplication hardware (tensor cores), the board can run a 35-billion parameter Mixture-of-Experts language model at 38 tokens per second using quantized weights and a Vulkan compute backend.

YOUR 3-SENTENCE SUMMARY:""",
        "check": lambda r: (
            all(k.lower() in r.lower() for k in ["BC-250", "Vulkan", "16 GB", "35"]),
            f"kw {sum(k.lower() in r.lower() for k in ['BC-250','Vulkan','16 GB','35'])}/4, "
            f"sentences~{r.count('.') }"),
    },
    "json_extract": {
        "prompt": """Extract the following fields from the text below and return ONLY valid JSON. No explanation, no markdown, just the JSON object.

TEXT: "Dr. Maria Chen, age 42, works as a Senior Research Scientist at NVIDIA's Santa Clara campus. She joined in 2018 and specializes in GPU compiler optimization. Her team has 12 members and she holds 7 patents related to shader compilation."

Required fields: name, age, title, company, year_joined, team_size, patent_count

JSON:""",
        "check": lambda r: _json_check(r),
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
        "check": lambda r: ("W" in r and "wolfram" in r.lower(),
                             "W+Wolfram present" if "wolfram" in r.lower() else "missing"),
    },
    "instruction_follow": {
        "prompt": """List exactly 5 advantages of using solar energy for electricity generation. Format each as a numbered item (1. through 5.). Be concise — one sentence per item. Do not include any introduction or conclusion, just the 5 numbered items.""",
        "check": lambda r: (_count_numbered(r) == 5,
                             f"{_count_numbered(r)} numbered items"),
    },
    "arithmetic": {
        "prompt": """Solve this arithmetic problem. Give ONLY the final numerical answer, nothing else.

What is 17 × 23?

Answer:""",
        "check": lambda r: ("391" in r, "has 391" if "391" in r else f"got: {r[:40]!r}"),
    },
}

def _count_numbered(r):
    return len(re.findall(r'^\s*\d+\.', r, re.MULTILINE))

def _json_check(r):
    body = r.strip()
    body = re.sub(r'^```(?:json)?|```$', '', body, flags=re.MULTILINE).strip()
    m = re.search(r'\{.*\}', body, re.DOTALL)
    if not m:
        return (False, "no JSON object found")
    try:
        obj = json.loads(m.group(0))
    except Exception as e:
        return (False, f"json.loads failed: {e}")
    keys = ["name", "age", "title", "company"]
    ok = all(k in obj for k in keys)
    return (ok, f"valid JSON, keys {sum(k in obj for k in keys)}/4")

# ── 2. Synthesis jobs (representative of the netscan 'think' workload) ────────
SYNTH_TASKS = {
    "career_synth":
        "You are a career analyst. A software engineer with 8 years in embedded "
        "Linux and GPU drivers is weighing three job offers: (A) a GPU compiler "
        "role at a chip vendor, (B) a staff embedded role at an automotive Tier-1, "
        "(C) a remote ML-infra role at a startup. Produce a concise structured "
        "comparison: 1) a one-line verdict, 2) top 3 decision factors, 3) the main "
        "red flag for each option. Be specific and decisive.",
    "company_synth":
        "Summarize the strategic position of a mid-size camera-sensor manufacturer "
        "facing: rising competition from a larger rival, a new patent-infringement "
        "lawsuit, and growing demand for automotive IR sensors. Give: a 2-sentence "
        "situation summary, 3 risks ranked by severity, and 1 concrete opportunity.",
    "market_synth":
        "You are a market analyst. Given that GPU memory prices rose 18% this "
        "quarter, a major foundry cut capacity, and AI-accelerator demand keeps "
        "climbing, write a tight 4-sentence outlook for the consumer GPU segment "
        "over the next two quarters, ending with a single actionable takeaway.",
}

# ── 3. Parser-compat: the real chat system prompt shape (EXEC tool use) ───────
EXEC_SYSTEM = (
    "You are a helpful assistant with shell tool access. When you need live data, "
    "emit a single line that begins EXACTLY with 'EXEC:' followed by the command, "
    "with nothing before it on that line. Examples:\n"
    "EXEC: curl -s wttr.in/Wroclaw?format=3\n"
    "EXEC: ddgr --num 5 --noprompt \"query\"\n"
    "Do NOT include <think> tags or reasoning before the EXEC line. After tool "
    "output is returned you answer the user normally."
)
EXEC_PROMPTS = {
    "exec_weather": "What's the weather in Wroclaw right now?",
    "exec_search":  "Search the web for the latest AMD RDNA4 release date.",
}

def run():
    results = {"model": MODEL, "ts": time.strftime("%Y-%m-%d %H:%M"),
               "quality": {}, "synthesis": {}, "parser_compat": {}}

    print(f"\n=== gemma4-26b-q3 eval — {results['ts']} ===\n")

    # 1. Quality battery
    print("[1/3] Quality battery (5 tasks × %d runs, think=False)" % N_RUNS)
    for name, task in QUALITY_TASKS.items():
        runs = []
        for i in range(N_RUNS):
            r = chat("You are a precise assistant. Answer directly.",
                     task["prompt"], think=False, num_ctx=4096, temp=0.0,
                     num_predict=300)
            ok, detail = task["check"](r["content"])
            runs.append({"pass": ok, "detail": detail, "wall_s": r["wall_s"],
                         "out": r["content"][:300]})
            print(f"   {name:18s} run{i+1}: {'PASS' if ok else 'FAIL'}  ({detail}, {r['wall_s']}s)")
        results["quality"][name] = {
            "pass_count": sum(x["pass"] for x in runs), "n": N_RUNS, "runs": runs}

    qtot = sum(v["pass_count"] for v in results["quality"].values())
    qmax = N_RUNS * len(QUALITY_TASKS)
    print(f"   → quality {qtot}/{qmax} ({100*qtot//qmax}%)\n")

    # 2. Synthesis
    print("[2/3] Synthesis jobs (think=False)")
    for name, prompt in SYNTH_TASKS.items():
        r = chat("You are a sharp, concise analyst.", prompt, think=False,
                 num_ctx=8192, num_predict=600)
        out = r["content"]
        usable = len(out) > 120 and "<think>" not in out
        print(f"   {name:14s}: {len(out)} chars, {r['wall_s']}s, "
              f"{'USABLE' if usable else 'CHECK'}")
        results["synthesis"][name] = {"usable": usable, "chars": len(out),
                                      "wall_s": r["wall_s"], "out": out}

    # 3. Parser compat — the crux
    print("\n[3/3] Parser compatibility (production parsers)")
    for name, prompt in EXEC_PROMPTS.items():
        r = chat(EXEC_SYSTEM, prompt, think=False, num_ctx=4096, num_predict=250)
        raw = r["content"]
        stripped = strip_think(raw)
        had_think_tag = "<think>" in raw
        leaked_reasoning = bool(r["reasoning"]) or (had_think_tag and "<think>" in stripped)
        cmd = find_exec(stripped)
        exec_ok = cmd is not None
        # would the production reply leak reasoning to the user?
        user_reply = re.sub(r'^EXEC:.*$', '', stripped, flags=re.MULTILINE).strip()
        print(f"   {name:14s}: EXEC {'DETECTED' if exec_ok else 'MISSED'} "
              f"| think-tag={had_think_tag} | reasoning-leak={leaked_reasoning}")
        if exec_ok:
            print(f"        cmd: {cmd[:80]}")
        else:
            print(f"        raw[:160]: {raw[:160]!r}")
        results["parser_compat"][name] = {
            "exec_detected": exec_ok, "cmd": cmd, "had_think_tag": had_think_tag,
            "reasoning_leak": leaked_reasoning, "user_reply_len": len(user_reply),
            "raw": raw[:400]}

    # JSON parser already covered by quality.json_extract
    results["parser_compat"]["json_extract_valid"] = (
        results["quality"]["json_extract"]["pass_count"] == N_RUNS)

    with open(OUT, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved → {OUT}")

    # Verdict
    exec_all = all(results["parser_compat"][k]["exec_detected"]
                   for k in EXEC_PROMPTS)
    leak_any = any(results["parser_compat"][k]["reasoning_leak"]
                   for k in EXEC_PROMPTS)
    print("\n=== VERDICT ===")
    print(f"  Quality:        {qtot}/{qmax}")
    print(f"  EXEC parsing:   {'OK (both detected)' if exec_all else 'BROKEN — anchor missed'}")
    print(f"  JSON parsing:   {'OK' if results['parser_compat']['json_extract_valid'] else 'BROKEN'}")
    print(f"  Reasoning leak: {'YES — CoT leaks with think=False' if leak_any else 'no'}")

if __name__ == "__main__":
    run()
