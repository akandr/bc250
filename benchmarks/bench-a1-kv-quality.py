#!/usr/bin/env python3
"""A1: KV-cache QUALITY comparison (q4_0 vs q8_0 vs f16), paper revision experiment.

The article's Table 6 (tab:kv_longctx) validated q4_0 KV for SPEED only. R1 requires an
ACCURACY comparison across KV widths on the multi-hop and retrieval tasks. This harness
sweeps KV type as the outer loop (restarting Ollama per type, reusing the exact mechanism
from bench-kv-longctx.py) and, under each, scores:
  - the two multi-hop arithmetic-chain tests (verbatim from bench-g6-multihop.py)
  - the two-needle long-context retrieval probe (verbatim from bench-kv-longctx.py)
across the three long-context models at 16K and 32K, n=3, deterministic (temp=0, seed=42)
so any accuracy delta is attributable to KV precision, not sampling noise.

Run on the board (24-CU recovered article config, Ollama 0.20.0):
  nohup python3 /tmp/bench-a1-kv-quality.py > /tmp/a1-kv-quality.log 2>&1 &
Output: ~/phase-c-out/results/step-a1-kv-quality.json
"""

import json, os, re, subprocess, sys, time, datetime, urllib.request, urllib.error

sys.stdout.reconfigure(line_buffering=True)

OLLAMA        = "http://127.0.0.1:11434"
KV_TYPES      = ["q4_0", "q8_0", "f16"]
MODELS        = ["qwen3.5:9b", "qwen3.5-35b-a3b-iq2m:latest", "phi4-mini:latest"]
CTX_SIZES     = [16384, 32768]
N_RUNS        = 3
OVERRIDE_CONF = "/etc/systemd/system/ollama.service.d/override.conf"
RESULTS_DIR   = os.path.expanduser("~/phase-c-out/results")
OUT_PATH      = f"{RESULTS_DIR}/step-a1-kv-quality.json"
os.makedirs(RESULTS_DIR, exist_ok=True)

# ============================ MULTI-HOP TESTS (verbatim from bench-g6-multihop.py) =========
FILL_SEMICONDUCTOR = """The evolution of semiconductor manufacturing represents one of the most
remarkable engineering achievements in human history. From the first transistor
at Bell Labs in 1947 to modern 3nm process nodes, the industry has maintained
exponential scaling for over seven decades. Each generation of lithography
brought new challenges: optical diffraction limits led to immersion lithography,
then extreme ultraviolet (EUV) sources. The economics are equally staggering —
a modern fab costs $20 billion or more, yet produces chips at less than a cent
per transistor. Memory technologies evolved in parallel: from magnetic core to
SRAM, DRAM, and now 3D NAND flash with hundreds of layers. The interface between
processor and memory — the memory wall — remains the fundamental bottleneck
in computing performance. Bandwidth grows slower than compute, creating an
ever-widening gap that architects address through deeper cache hierarchies,
prefetching, and data-flow optimizations.\n\n"""
FILL_NETWORK = """Network protocols form the backbone of modern computing infrastructure.
The TCP/IP stack, designed in the 1970s, remains fundamental to internet
communication. At the physical layer, fiber optic cables carry data as pulses
of light across ocean floors, spanning over 1.3 million kilometers globally.
The Domain Name System translates human-readable addresses into IP addresses
through a hierarchical resolution process involving root servers, TLD servers,
and authoritative nameservers. HTTP evolved from a simple document retrieval
protocol to HTTP/2 with multiplexed streams, and now HTTP/3 built on QUIC
uses UDP instead of TCP for lower latency. TLS 1.3 reduced the handshake
from two round trips to one, significantly improving connection setup time.
Content delivery networks cache content at edge locations, reducing latency
for end users. BGP routing tables have grown to over 900,000 prefixes,
creating scalability challenges for core routers.\n\n"""
FILL_BIOLOGY = """Cellular biology reveals intricate mechanisms of life at the molecular level.
DNA replication proceeds at roughly 1000 nucleotides per second in bacteria,
with error rates as low as one mistake per billion base pairs thanks to
proofreading enzymes. The central dogma — DNA to RNA to protein — has been
complicated by discoveries of reverse transcription, RNA interference, and
epigenetic modifications that alter gene expression without changing the
underlying sequence. Mitochondria, the cell's powerhouses, maintain their
own circular genome — a remnant of ancient endosymbiosis. The human genome
contains approximately 20,000 protein-coding genes, far fewer than initially
expected, but alternative splicing generates over 100,000 distinct proteins.
CRISPR-Cas9 gene editing has revolutionized molecular biology by enabling
precise modifications to any genomic locus, though off-target effects remain
a concern for therapeutic applications.\n\n"""
FILL_CLIMATE = """Climate science integrates atmospheric physics, ocean dynamics, and
biogeochemical cycles into complex Earth system models. The greenhouse effect,
first described by Fourier in 1824 and quantified by Arrhenius in 1896,
explains how certain gases trap infrared radiation. Carbon dioxide levels have
risen from 280 ppm pre-industrial to over 420 ppm today, driven primarily
by fossil fuel combustion and deforestation. Ocean acidification proceeds
in parallel — pH has dropped by 0.1 units since pre-industrial times,
threatening calcifying organisms from corals to pteropods. The Arctic has
warmed at roughly twice the global average rate, a phenomenon called Arctic
amplification. Permafrost thaw threatens to release vast stores of methane
and carbon dioxide, potentially creating a positive feedback loop. Climate
models project global temperature increases of 1.5 to 4.5 degrees Celsius
for a doubling of CO2, with the wide range reflecting uncertainty in cloud
feedbacks and aerosol effects.\n\n"""
FILL_ASTRONOMY = """Modern astronomy has revealed a universe far stranger than classical
astronomers imagined. Dark matter, detectable only through gravitational
effects, constitutes roughly 27% of the universe's mass-energy content.
Dark energy, driving accelerating expansion, accounts for about 68%.
Ordinary matter — everything we can see and touch — makes up merely 5%.
Gravitational wave detectors like LIGO have opened a new observational
window, detecting mergers of black holes and neutron stars billions of
light-years away. The Event Horizon Telescope captured the first image
of a black hole shadow in the galaxy M87, confirming predictions of general
relativity. Exoplanet surveys have identified over 5,000 confirmed planets,
with potentially habitable rocky worlds orbiting within the Goldilocks zones
of their host stars. The James Webb Space Telescope observes in infrared,
peering through dust clouds to study the earliest galaxies formed just
a few hundred million years after the Big Bang.\n\n"""

MULTIHOP_TESTS = [
    {
        "name": "multihop_budget",
        "facts": [
            {"text": "[IMPORTANT NOTE: The Meridian Research Institute allocated $4.2 million to Project Aurora for quantum computing research in fiscal year 2025.]", "position": 0.20},
            {"text": "[IMPORTANT NOTE: Project Aurora's quantum computing division spent exactly 60% of its allocated budget on hardware procurement, with the remainder going to personnel and operations.]", "position": 0.55},
            {"text": "[IMPORTANT NOTE: The hardware procurement budget for Project Aurora was split equally between cryogenic cooling systems and superconducting qubit fabrication.]", "position": 0.80},
        ],
        "question": "Based on the notes embedded in the text above, how much money did Project Aurora spend on cryogenic cooling systems? Show your calculation step by step, then give the final answer as a dollar amount.",
        "answer_must_contain": "1.26",
        "alt_answers": ["1,260,000", "1260000", "$1.26"],
    },
    {
        "name": "multihop_population",
        "facts": [
            {"text": "[IMPORTANT NOTE: The island nation of Velanthos had a total population of 840,000 in the 2024 census.]", "position": 0.15},
            {"text": "[IMPORTANT NOTE: According to the Velanthos National Bureau of Statistics, exactly 35% of the population lives in the capital city of Port Stellaris.]", "position": 0.60},
            {"text": "[IMPORTANT NOTE: Port Stellaris municipal records show that 20% of the city's residents are under the age of 18.]", "position": 0.85},
        ],
        "question": "Based on the notes embedded in the text above, how many residents of Port Stellaris are under 18 years old? Show your calculation step by step, then give the final answer as a number.",
        "answer_must_contain": "58,800",
        "alt_answers": ["58800", "58 800"],
    },
]

def build_filled_context(target_tokens, facts):
    chars_per_token = 3.8
    target_chars = int(target_tokens * chars_per_token)
    blocks = [FILL_SEMICONDUCTOR, FILL_NETWORK, FILL_BIOLOGY, FILL_CLIMATE, FILL_ASTRONOMY]
    fill_text, i = "", 0
    while len(fill_text) < target_chars:
        fill_text += blocks[i % len(blocks)]; i += 1
    fill_text = fill_text[:target_chars]
    for fact in sorted(facts, key=lambda f: f["position"]):
        pos = int(len(fill_text) * fact["position"])
        nl = fill_text.find("\n\n", pos)
        if nl == -1 or nl > pos + 500:
            nl = pos
        ins = f"\n\n{fact['text']}\n\n"
        fill_text = fill_text[:nl] + ins + fill_text[nl:]
    return fill_text

# ============================ NEEDLE PROBE (verbatim from bench-kv-longctx.py) =============
FILL_BLOCK = (
"The evolution of semiconductor manufacturing represents one of the most\n"
"remarkable engineering achievements in human history. From the first transistor\n"
"at Bell Labs in 1947 to modern 3nm process nodes, the industry has maintained\n"
"exponential scaling for over seven decades. Each generation of lithography\n"
"brought new challenges: optical diffraction limits led to immersion lithography,\n"
"then extreme ultraviolet (EUV) sources. The economics are equally staggering --\n"
"a modern fab costs $20 billion or more, yet produces chips at less than a cent\n"
"per transistor. Memory technologies evolved in parallel: from magnetic core to\n"
"SRAM, DRAM, and now 3D NAND flash with hundreds of layers. The interface between\n"
"processor and memory -- the memory wall -- remains the fundamental bottleneck\n"
"in computing performance. Bandwidth grows slower than compute, creating an\n"
"ever-widening gap that architects address through deeper cache hierarchies,\n"
"prefetching, and data-flow optimizations. On the software side, compilers have\n"
"become extraordinarily sophisticated, performing loop vectorization, automatic\n"
"parallelization, and profile-guided optimization. The interaction between\n"
"hardware and software design creates a co-evolution where each enables and\n"
"constrains the other. In artificial intelligence, this manifests as the\n"
"transformer architecture's quadratic attention mechanism -- theoretically elegant\n"
"but practically bounded by memory bandwidth on real hardware. Quantization\n"
"techniques reduce mathematical precision for throughput, enabled by hardware\n"
"that natively supports reduced-precision arithmetic.\n\n"
)
NEEDLE_CODE       = "DELTA-7-VIOLET-MOUNTAIN-93"
NEEDLE_CODE_EARLY = "OMEGA-4-COBALT-RIVER-17"
NEEDLE_LINE       = "\n\n*** SPECIAL NOTE: The secret access code is " + NEEDLE_CODE + ". You will be asked about this code at the end of the document. ***\n\n"
NEEDLE_LINE_EARLY = "\n\n*** EARLY NOTE: The first auxiliary code is " + NEEDLE_CODE_EARLY + ". Remember both codes. ***\n\n"
NEEDLE_INSTR = ("You will be shown a long technical document. Embedded somewhere in the document "
    "are TWO special notes, each containing a code in the format WORD-NUMBER-WORD-WORD-NUMBER. "
    "After the document, you must answer one question about both codes. Read the entire document "
    "carefully.\n\nDOCUMENT BEGINS:\n\n")
NEEDLE_QUESTION = ("\n\nDOCUMENT ENDS.\n\nQUESTION: What were the two codes embedded in the EARLY "
    "NOTE and the SPECIAL NOTE? Respond with ONLY the two codes, one per line, in the order they "
    "appeared in the document, nothing else.\n\nANSWER:")

def build_needle_prompt(target_tokens):
    overhead = len(NEEDLE_INSTR) + len(NEEDLE_QUESTION) + len(NEEDLE_LINE) + len(NEEDLE_LINE_EARLY)
    char_budget = max(int(target_tokens * 3.8) - overhead, len(FILL_BLOCK))
    n_blocks = char_budget // len(FILL_BLOCK) + 1
    fill_full = (FILL_BLOCK * n_blocks)[:char_budget]
    n = len(fill_full)
    def snap_at(t):
        i = fill_full.find("\n", t)
        return i if 0 <= i <= t + 200 else t
    early_at, mid_at = snap_at(int(n * 0.10)), snap_at(int(n * 0.50))
    if mid_at <= early_at:
        mid_at = early_at + 1
    body = fill_full[:early_at] + NEEDLE_LINE_EARLY + fill_full[early_at:mid_at] + NEEDLE_LINE + fill_full[mid_at:]
    return NEEDLE_INSTR + body + NEEDLE_QUESTION

def verify_needle(output, code=NEEDLE_CODE, code_early=NEEDLE_CODE_EARLY):
    if not output:
        return False, "empty_output"
    non_ws = re.sub(r"\s+", "", output.strip())
    if len(non_ws) < 20:
        return False, f"too_short:{len(non_ws)}"
    def _has(target):
        no = re.sub(r"\s+", "", output.upper())
        if re.sub(r"\s+", "", target.upper()) in no:
            return True
        pos = 0
        for s in target.upper().split("-"):
            i = no.find(s, pos)
            if i < 0:
                return False
            pos = i + len(s)
        return True
    if not _has(code):
        return False, "missing_primary"
    if code_early is not None and not _has(code_early):
        return False, "missing_early"
    return True, "both_present"

# ============================ Ollama KV control (from bench-kv-longctx.py) =================
def set_kv_type(kv_type):
    print(f"\n[kv] Setting OLLAMA_KV_CACHE_TYPE={kv_type} ...", flush=True)
    with open(OVERRIDE_CONF) as f:
        content = f.read()
    new_line = f'Environment="OLLAMA_KV_CACHE_TYPE={kv_type}"'
    if "OLLAMA_KV_CACHE_TYPE" in content:
        content = re.sub(r'Environment="OLLAMA_KV_CACHE_TYPE=[^"]*"', new_line, content)
    else:
        content = content.replace("[Service]", f"[Service]\n{new_line}", 1)
    tmp = "/tmp/_a1_override.conf"
    with open(tmp, "w") as f:
        f.write(content)
    subprocess.run(f"sudo cp {tmp} {OVERRIDE_CONF}", shell=True, check=True)
    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
    subprocess.run(["sudo", "systemctl", "restart", "ollama"], check=True)
    time.sleep(15)

def api(endpoint, data=None, timeout=600):
    req = urllib.request.Request(f"{OLLAMA}{endpoint}", method="POST" if data else "GET")
    body = None
    if data:
        req.add_header("Content-Type", "application/json")
        body = json.dumps(data).encode()
    with urllib.request.urlopen(req, body, timeout=timeout) as resp:
        return json.loads(resp.read())

def unload_all():
    try:
        ps = api("/api/ps", timeout=10)
        for m in (ps or {}).get("models", []):
            if m.get("name"):
                api("/api/generate", {"model": m["name"], "keep_alive": 0}, timeout=30)
                time.sleep(1)
    except Exception:
        pass
    time.sleep(3)

# ============================ run one cell ================================================
def run_multihop(model, test, ctx, kv_type, run_idx):
    context = build_filled_context(int(ctx * 0.80), test["facts"])
    prompt = f"[a1-run:{run_idx+1}]\n" + context + f"\n\n{test['question']}\n\nAnswer:"
    timeout = 700 if ctx <= 16384 else 1000
    t0 = time.time()
    try:
        resp = api("/api/generate", {
            "model": model, "prompt": prompt, "stream": False, "think": False,
            "keep_alive": "10m",
            "options": {"num_ctx": ctx, "num_predict": 1024, "temperature": 0.0, "seed": 42},
        }, timeout=timeout)
        if "error" in resp:
            return {"status": "FAIL", "error": resp["error"][:200], "wall_s": round(time.time()-t0,1)}
        r = resp.get("response", "").lower()
        correct = (test["answer_must_contain"].lower() in r) or any(a.lower() in r for a in test.get("alt_answers", []))
        gd = resp.get("eval_duration", 0)/1e9
        return {"status": "OK", "correct": correct,
                "eval_count": resp.get("eval_count", 0),
                "prompt_eval_count": resp.get("prompt_eval_count", 0),
                "gen_tok_s": round(resp.get("eval_count", 0)/gd, 2) if gd > 0 else 0,
                "response_preview": resp.get("response", "")[:300], "wall_s": round(time.time()-t0,1)}
    except Exception as e:
        return {"status": "TIMEOUT" if "timed out" in str(e).lower() else "FAIL",
                "error": str(e)[:200], "wall_s": round(time.time()-t0,1)}

def run_needle(model, ctx, kv_type, run_idx):
    prompt = f"[a1-run:{run_idx+1}]\n" + build_needle_prompt(ctx)
    timeout = max(900, int(ctx/40) + 200*3 + 180)
    t0 = time.time()
    try:
        resp = api("/api/chat", {
            "model": model, "messages": [{"role": "user", "content": prompt}], "stream": False,
            "think": False, "keep_alive": "10m",
            "options": {"num_ctx": ctx, "num_predict": 200, "temperature": 0.0, "seed": 42},
        }, timeout=timeout)
        if "error" in resp:
            return {"status": "FAIL", "error": resp["error"][:200], "wall_s": round(time.time()-t0,1)}
        out = (resp.get("message") or {}).get("content", "").strip()
        npass, reason = verify_needle(out)
        gd = resp.get("eval_duration", 0)/1e9
        return {"status": "OK", "correct": npass, "needle_reason": reason,
                "eval_count": resp.get("eval_count", 0),
                "prompt_eval_count": resp.get("prompt_eval_count", 0),
                "gen_tok_s": round(resp.get("eval_count", 0)/gd, 2) if gd > 0 else 0,
                "response_preview": out[:200], "wall_s": round(time.time()-t0,1)}
    except Exception as e:
        return {"status": "TIMEOUT" if "timed out" in str(e).lower() else "FAIL",
                "error": str(e)[:200], "wall_s": round(time.time()-t0,1)}

# ============================ main =======================================================
def preflight():
    try:
        cu = subprocess.check_output("journalctl -k -b 0 | grep -o 'active_cu_number [0-9]*' | tail -1",
                                     shell=True, text=True).strip()
    except Exception:
        cu = "unknown"
    ob = subprocess.run(["systemctl", "is-active", "oberon-governor"], capture_output=True, text=True).stdout.strip()
    kern = subprocess.check_output(["uname", "-r"], text=True).strip()
    print(f"[preflight] kernel={kern}  {cu}  oberon={ob}", flush=True)
    return {"kernel": kern, "cu": cu, "oberon": ob}

def main():
    print("="*72)
    print("A1: KV-cache QUALITY (q4_0 vs q8_0 vs f16) — multi-hop + needle, n=3, temp=0")
    print(f"Models: {MODELS}")
    print(f"KV types: {KV_TYPES}  Ctx: {CTX_SIZES}  Started: {datetime.datetime.now().isoformat()}")
    print("="*72)
    pf = preflight()

    cells = []
    total0 = time.time()
    for kv in KV_TYPES:
        set_kv_type(kv)
        for model in MODELS:
            for ctx in CTX_SIZES:
                ctx_label = f"{ctx//1024}K"
                print(f"\n[{kv}] {model} @ {ctx_label} — warmup", flush=True)
                unload_all()
                try:
                    w = api("/api/generate", {"model": model, "prompt": "Hello", "stream": False,
                            "options": {"num_ctx": ctx, "num_predict": 5}, "keep_alive": "10m", "think": False}, timeout=300)
                    if "error" in w:
                        print(f"  WARMUP FAIL: {w['error'][:80]} — recording OOM/skip", flush=True)
                        cells.append({"kv_type": kv, "model": model, "ctx": ctx, "task": "_warmup",
                                      "status": "WARMUP_FAIL", "error": w["error"][:200]})
                        save(cells, pf, total0); continue
                except Exception as e:
                    print(f"  WARMUP EXC: {str(e)[:80]}", flush=True)
                    cells.append({"kv_type": kv, "model": model, "ctx": ctx, "task": "_warmup",
                                  "status": "WARMUP_EXC", "error": str(e)[:200]})
                    save(cells, pf, total0); continue

                # multi-hop tasks
                for test in MULTIHOP_TESTS:
                    runs = [dict(run_multihop(model, test, ctx, kv, i), run_idx=i+1) for i in range(N_RUNS)]
                    npass = sum(1 for r in runs if r.get("correct"))
                    cells.append({"kv_type": kv, "model": model, "ctx": ctx, "ctx_label": ctx_label,
                                  "task": test["name"], "task_type": "multihop",
                                  "n_runs": N_RUNS, "n_pass": npass, "pass_rate": round(npass/N_RUNS,3),
                                  "gen_tok_s_median": _median([r.get("gen_tok_s") for r in runs]),
                                  "runs": runs})
                    print(f"  [{kv}] {model} {ctx_label} {test['name']}: {npass}/{N_RUNS}", flush=True)
                    save(cells, pf, total0)

                # needle retrieval task
                runs = [dict(run_needle(model, ctx, kv, i), run_idx=i+1) for i in range(N_RUNS)]
                npass = sum(1 for r in runs if r.get("correct"))
                cells.append({"kv_type": kv, "model": model, "ctx": ctx, "ctx_label": ctx_label,
                              "task": "needle_2code", "task_type": "retrieval",
                              "n_runs": N_RUNS, "n_pass": npass, "pass_rate": round(npass/N_RUNS,3),
                              "gen_tok_s_median": _median([r.get("gen_tok_s") for r in runs]),
                              "runs": runs})
                print(f"  [{kv}] {model} {ctx_label} needle_2code: {npass}/{N_RUNS}", flush=True)
                save(cells, pf, total0)

    print("\n[restore] q4_0 deployed KV ...", flush=True)
    set_kv_type("q4_0")
    print(f"[done] {round((time.time()-total0)/60,1)} min  ->  {OUT_PATH}", flush=True)
    _summary(cells)

def _median(vals):
    v = sorted(x for x in vals if isinstance(x, (int, float)) and x > 0)
    return round(v[len(v)//2], 2) if v else None

def save(cells, pf, total0):
    with open(OUT_PATH, "w") as f:
        json.dump({"step": "a1-kv-quality", "captured_at": datetime.datetime.now().isoformat(),
                   "preflight": pf, "kv_types": KV_TYPES, "models": MODELS, "ctx_sizes": CTX_SIZES,
                   "n_runs": N_RUNS, "sampling": "temp=0 seed=42", "elapsed_min": round((time.time()-total0)/60,1),
                   "cells": cells}, f, indent=2)

def _summary(cells):
    print("\n" + "="*72 + "\nSUMMARY — pass rate by KV type (accuracy, not speed)\n" + "="*72)
    tasks = ["multihop_budget", "multihop_population", "needle_2code"]
    for model in MODELS:
        print(f"\n{model}")
        hdr = f"  {'ctx/task':<24}" + "".join(f"{kv:>10}" for kv in KV_TYPES)
        print(hdr); print("  " + "-"*(24+10*len(KV_TYPES)))
        for ctx in CTX_SIZES:
            for task in tasks:
                row = f"  {str(ctx//1024)+'K '+task:<24}"
                for kv in KV_TYPES:
                    c = next((c for c in cells if c.get("kv_type")==kv and c.get("model")==model
                              and c.get("ctx")==ctx and c.get("task")==task), None)
                    row += f"{(str(c['n_pass'])+'/'+str(c['n_runs'])) if c and 'n_pass' in c else '—':>10}"
                print(row)

if __name__ == "__main__":
    main()
