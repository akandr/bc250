#!/usr/bin/env python3
"""G6: Multi-hop quality re-run — larger output budget (1024 tokens), n=3.

Changes from original bench-longctx-quality.py:
- Runs only MULTIHOP_TESTS (synthesis was already reliable at 23/24)
- num_predict=1024 (was 300) to avoid CoT truncation
- N_RUNS=3 per (model, test, ctx) cell
- Run-specific prefix to prevent KV cache reuse across n repetitions
- Data -> ~/phase-c-out/results/step-g6-multihop.json
"""

import json, time, datetime, os, urllib.request

OLLAMA = "http://127.0.0.1:11434"
OUT = os.path.expanduser("~/phase-c-out/results/step-g6-multihop.json")

MODELS = [
    "qwen3.5:9b",
    "qwen3.5-35b-a3b-iq2m:latest",
    "phi4-mini:latest",
]
N_RUNS = 3
NUM_PREDICT = 1024
CTX_SIZES = [16384, 32768]

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
        "task_type": "multi-hop reasoning (3 facts, arithmetic chain)",
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
        "task_type": "multi-hop reasoning (3 facts, arithmetic chain)",
    },
]


def api(endpoint, data=None, timeout=600):
    url = f"{OLLAMA}{endpoint}"
    req = urllib.request.Request(url, method="POST" if data else "GET")
    if data:
        req.add_header("Content-Type", "application/json")
        body = json.dumps(data).encode()
    else:
        body = None
    with urllib.request.urlopen(req, body, timeout=timeout) as resp:
        return json.loads(resp.read())


def unload_all():
    try:
        ps = api("/api/ps", timeout=10)
        if ps and "models" in ps:
            for m in ps["models"]:
                name = m.get("name", "")
                if name:
                    api("/api/generate", {"model": name, "keep_alive": 0}, timeout=30)
                    time.sleep(1)
    except Exception:
        pass
    time.sleep(3)


def build_filled_context(target_tokens, facts):
    chars_per_token = 3.8
    target_chars = int(target_tokens * chars_per_token)
    blocks = [FILL_SEMICONDUCTOR, FILL_NETWORK, FILL_BIOLOGY, FILL_CLIMATE, FILL_ASTRONOMY]
    fill_text = ""
    block_idx = 0
    while len(fill_text) < target_chars:
        fill_text += blocks[block_idx % len(blocks)]
        block_idx += 1
    fill_text = fill_text[:target_chars]
    sorted_facts = sorted(facts, key=lambda f: f["position"])
    offset = 0
    for fact in sorted_facts:
        pos = int(len(fill_text) * fact["position"]) + offset
        newline_pos = fill_text.find("\n\n", pos)
        if newline_pos == -1 or newline_pos > pos + 500:
            newline_pos = pos
        insert_text = f"\n\n{fact['text']}\n\n"
        fill_text = fill_text[:newline_pos] + insert_text + fill_text[newline_pos:]
        offset += len(insert_text)
    return fill_text


def run_one(model, test, ctx_size, run_idx, timeout=900):
    fill_tokens = int(ctx_size * 0.80)
    context = build_filled_context(fill_tokens, test["facts"])
    run_tag = f"[g6-run:{run_idx+1}]\n"  # prevent KV cache reuse across runs
    prompt = run_tag + context + f"\n\n{test['question']}\n\nAnswer:"
    data = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"num_ctx": ctx_size, "num_predict": NUM_PREDICT},
        "keep_alive": "10m",
        "think": False,
    }
    t0 = time.time()
    try:
        resp = api("/api/generate", data, timeout=timeout)
        wall = time.time() - t0
        if "error" in resp:
            return {"status": "FAIL", "error": resp["error"][:200], "wall_s": round(wall, 1)}
        response = resp.get("response", "")
        gen_toks = resp.get("eval_count", 0)
        eval_dur = resp.get("eval_duration", 0) / 1e9
        prompt_dur = resp.get("prompt_eval_duration", 0) / 1e9
        load_dur = resp.get("load_duration", 0) / 1e9
        gen_tok_s = gen_toks / eval_dur if eval_dur > 0 else 0
        resp_lower = response.lower()
        primary_correct = test["answer_must_contain"].lower() in resp_lower
        alt_correct = any(a.lower() in resp_lower for a in test.get("alt_answers", []))
        correct = primary_correct or alt_correct
        return {
            "status": "OK",
            "correct": correct,
            "response_len": len(response),
            "response_preview": response[:300],
            "prompt_eval_count": resp.get("prompt_eval_count", 0),
            "eval_count": gen_toks,
            "gen_tok_s": round(gen_tok_s, 2),
            "ttft_s": round(load_dur + prompt_dur, 2),
            "wall_s": round(wall, 1),
        }
    except Exception as e:
        return {
            "status": "TIMEOUT" if "timed out" in str(e).lower() else "FAIL",
            "error": str(e)[:200],
            "wall_s": round(time.time() - t0, 1),
        }


def save(obj):
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(obj, f, indent=2)


def main():
    os.makedirs(os.path.dirname(OUT) if os.path.dirname(OUT) else ".", exist_ok=True)

    print(f"""
======================================================================
  G6: Multi-hop Quality Re-run (n={N_RUNS}, num_predict={NUM_PREDICT})
  Models: {MODELS}
  Tests: {[t['name'] for t in MULTIHOP_TESTS]}
  Context: 16K, 32K (80% fill)
  Total cells: {len(MODELS)*len(MULTIHOP_TESTS)*len(CTX_SIZES)*N_RUNS}
  Output: {OUT}
======================================================================
""")

    all_cells = []
    summary = {}
    total_start = time.time()

    for mi, model in enumerate(MODELS):
        print(f"\n[{mi+1}/{len(MODELS)}] {model}")
        print("  " + "─" * 60)
        model_summary = {}

        for ctx in CTX_SIZES:
            ctx_label = f"{ctx // 1024}K"
            print(f"\n  === {ctx_label} context ===")

            # Warm up model
            unload_all()
            try:
                warmup = api("/api/generate", {
                    "model": model, "prompt": "Hello", "stream": False,
                    "options": {"num_ctx": ctx, "num_predict": 5},
                    "keep_alive": "10m", "think": False,
                }, timeout=300)
                if "error" in warmup:
                    print(f"    WARMUP FAIL: {warmup['error'][:60]}")
                    continue
            except Exception as e:
                print(f"    WARMUP FAIL: {str(e)[:60]}")
                continue

            for test in MULTIHOP_TESTS:
                cell_runs = []
                timeout = 700 if ctx <= 16384 else 1000
                print(f"\n    [{test['name']}]")

                for run_i in range(N_RUNS):
                    r = run_one(model, test, ctx, run_i, timeout=timeout)
                    r["model"] = model
                    r["test_name"] = test["name"]
                    r["ctx_size"] = ctx
                    r["run_idx"] = run_i + 1
                    cell_runs.append(r)
                    if r["status"] == "OK":
                        mark = "✅" if r["correct"] else "❌"
                        truncated = " [TRUNCATED]" if r["eval_count"] >= NUM_PREDICT - 5 else ""
                        print(f"      run{run_i+1}: {mark} pec={r['prompt_eval_count']:,} gen={r['gen_tok_s']:.0f} tok/s "
                              f"TTFT={r['ttft_s']:.0f}s eval={r['eval_count']}{truncated}")
                    else:
                        print(f"      run{run_i+1}: ⏱ {r['status']} ({r.get('error','?')[:50]})")

                pass_n = sum(1 for r in cell_runs if r.get("correct"))
                cell = {
                    "model": model,
                    "test_name": test["name"],
                    "ctx_size": ctx,
                    "ctx_label": ctx_label,
                    "n_runs": N_RUNS,
                    "n_pass": pass_n,
                    "pass_rate": round(pass_n / N_RUNS, 3),
                    "runs": cell_runs,
                    "timestamp": datetime.datetime.now().isoformat(),
                }
                all_cells.append(cell)
                print(f"      → score: {pass_n}/{N_RUNS} ({100*pass_n//N_RUNS}%)")

                key = f"{model}|{ctx_label}|{test['name']}"
                model_summary[key] = f"{pass_n}/{N_RUNS}"

                save({"cells": all_cells, "metadata": {
                    "timestamp": datetime.datetime.now().isoformat(),
                    "elapsed_min": round((time.time() - total_start) / 60, 1),
                }})

        summary[model] = model_summary

    elapsed = time.time() - total_start

    print(f"""
======================================================================
  SUMMARY ({elapsed/60:.1f} min)
======================================================================
""")
    print(f"  {'Model':35s} {'Test':25s} {'16K':>6s} {'32K':>6s}")
    print(f"  {'─'*35} {'─'*25} {'─'*6} {'─'*6}")
    for model in MODELS:
        for test in MULTIHOP_TESTS:
            s16 = summary.get(model, {}).get(f"{model}|16K|{test['name']}", "—")
            s32 = summary.get(model, {}).get(f"{model}|32K|{test['name']}", "—")
            print(f"  {model:35s} {test['name']:25s} {s16:>6s} {s32:>6s}")

    print(f"\n  Results saved: {OUT}")
    print(f"  Total time: {elapsed/60:.1f} minutes")


if __name__ == "__main__":
    main()
