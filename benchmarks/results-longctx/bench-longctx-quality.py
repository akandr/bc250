#!/usr/bin/env python3
"""Long-context quality benchmark — multi-hop reasoning + synthesis at 16K and 32K.

Tests 3 production models on:
  1. Multi-hop reasoning: model must combine 2-3 facts scattered across the context
  2. Long-range synthesis: model must identify themes/contradictions across the full text
  3. At 16K and 32K filled context

Methodology: 80% fill with real text, facts embedded at known positions.
Scoring: deterministic string-containment checks (same as Phase 4 quality).
"""

import json
import time
import datetime
import os
import urllib.request

OLLAMA = "http://127.0.0.1:11434"
RESULTS_DIR = os.path.expanduser("~/phase-c-out/longctx-quality")

# Production set as of Phase D rerun. Only Ollama-served models are testable
# here (bench uses /api/generate). llama-completion models are perf-only and
# don't go through this multi-hop quality suite.
MODELS = [
    "qwen3.5:9b",
    "qwen3.5-35b-a3b-iq2m:latest",
    "gemma4-26b-q3:latest",
    "gemma4:latest",
]
# Models that REQUIRE native sampling (MoE chat-template; greedy temp=0 produces
# empty/loop output even with think:false). Confirmed live on bc250.
NATIVE_SAMPLING_MODELS = {"gemma4:latest", "gemma4-26b-q3:latest"}
NATIVE_SAMPLING_OPTS = {
    "temperature": 1.0, "top_k": 64, "top_p": 0.95, "seed": 42,
}

# ─── Fill blocks (~500 tokens each) ─────────────────────────────────────────

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


# ─── Test definitions ────────────────────────────────────────────────────────

# Multi-hop reasoning: model must combine 2-3 embedded facts to derive an answer
MULTIHOP_TESTS = [
    {
        "name": "multihop_budget",
        "facts": [
            {"text": "[IMPORTANT NOTE: The Meridian Research Institute allocated $4.2 million to Project Aurora for quantum computing research in fiscal year 2025.]",
             "position": 0.20},
            {"text": "[IMPORTANT NOTE: Project Aurora's quantum computing division spent exactly 60% of its allocated budget on hardware procurement, with the remainder going to personnel and operations.]",
             "position": 0.55},
            {"text": "[IMPORTANT NOTE: The hardware procurement budget for Project Aurora was split equally between cryogenic cooling systems and superconducting qubit fabrication.]",
             "position": 0.80},
        ],
        "question": "Based on the notes embedded in the text above, how much money did Project Aurora spend on cryogenic cooling systems? Show your calculation step by step, then give the final answer as a dollar amount.",
        "answer_must_contain": "1.26",  # $4.2M × 60% × 50% = $1.26M
        "task_type": "multi-hop reasoning (3 facts, arithmetic chain)",
    },
    {
        "name": "multihop_population",
        "facts": [
            {"text": "[IMPORTANT NOTE: The island nation of Velanthos had a total population of 840,000 in the 2024 census.]",
             "position": 0.15},
            {"text": "[IMPORTANT NOTE: According to the Velanthos National Bureau of Statistics, exactly 35% of the population lives in the capital city of Port Stellaris.]",
             "position": 0.60},
            {"text": "[IMPORTANT NOTE: Port Stellaris municipal records show that 20% of the city's residents are under the age of 18.]",
             "position": 0.85},
        ],
        "question": "Based on the notes embedded in the text above, how many residents of Port Stellaris are under 18 years old? Show your calculation step by step, then give the final answer as a number.",
        "answer_must_contain": "58,800",  # 840,000 × 35% × 20% = 58,800
        "alt_answers": ["58800", "58 800"],
        "task_type": "multi-hop reasoning (3 facts, arithmetic chain)",
    },
]

# Long-range synthesis: model must identify patterns across the full context
SYNTHESIS_TESTS = [
    {
        "name": "synthesis_contradictions",
        "facts": [
            {"text": "[STUDY FINDING: A 2024 analysis by Dr. Helena Voss at Cambridge University concluded that global ocean temperatures have been DECREASING by 0.03°C per decade since 2010, contradicting mainstream climate models.]",
             "position": 0.25},
            {"text": "[STUDY FINDING: The Potsdam Institute's 2025 meta-analysis of 847 ocean monitoring stations confirmed a consistent warming trend of 0.08°C per decade in global sea surface temperatures since 2000, accelerating since 2015.]",
             "position": 0.70},
        ],
        "question": "Based on the study findings embedded in the text above, there are two research findings about ocean temperature trends. Do they agree or contradict each other? Explain the specific contradiction, naming both researchers/institutions and the specific temperature trends they report.",
        "answer_must_contain": "contradict",
        "check_extras": ["Voss", "Potsdam", "decreas", "warm"],
        "task_type": "long-range synthesis (identify contradiction across context)",
    },
    {
        "name": "synthesis_timeline",
        "facts": [
            {"text": "[EVENT LOG: January 15, 2025 — Nextera Biotech filed patent #NB-2025-0047 for a novel mRNA delivery mechanism using lipid nanoparticles with a 94% encapsulation efficiency.]",
             "position": 0.10},
            {"text": "[EVENT LOG: March 22, 2025 — GeneStar Therapeutics announced their competing patent #GS-2025-0112 was approved, covering lipid nanoparticle delivery with 97% encapsulation efficiency, three percentage points higher than the Nextera approach.]",
             "position": 0.45},
            {"text": "[EVENT LOG: June 8, 2025 — An independent lab published results showing that encapsulation efficiency above 95% correlates with a 40% reduction in required dosage for therapeutic applications.]",
             "position": 0.75},
        ],
        "question": "Based on the event logs embedded in the text above, list all three events in chronological order. Then answer: which company's technology would benefit from the dosage reduction finding, and why? Be specific about the efficiency threshold.",
        "answer_must_contain": "GeneStar",
        "check_extras": ["97", "95", "Nextera", "94"],
        "task_type": "long-range synthesis (temporal ordering + implication)",
    },
]


# ─── Helpers ─────────────────────────────────────────────────────────────────

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
    """Build context with fill text and embedded facts at specified positions.
    
    facts: list of {"text": str, "position": float (0-1)}
    """
    chars_per_token = 3.8
    target_chars = int(target_tokens * chars_per_token)
    
    blocks = [FILL_SEMICONDUCTOR, FILL_NETWORK, FILL_BIOLOGY, FILL_CLIMATE, FILL_ASTRONOMY]
    
    # Build the base fill text
    fill_text = ""
    block_idx = 0
    while len(fill_text) < target_chars:
        fill_text += blocks[block_idx % len(blocks)]
        block_idx += 1
    fill_text = fill_text[:target_chars]
    
    # Insert facts at their positions (sort by position to maintain offsets)
    sorted_facts = sorted(facts, key=lambda f: f["position"])
    offset = 0
    for fact in sorted_facts:
        pos = int(len(fill_text) * fact["position"]) + offset
        # Find the next paragraph break near the target position
        newline_pos = fill_text.find("\n\n", pos)
        if newline_pos == -1 or newline_pos > pos + 500:
            newline_pos = pos
        insert_pos = newline_pos
        insert_text = f"\n\n{fact['text']}\n\n"
        fill_text = fill_text[:insert_pos] + insert_text + fill_text[insert_pos:]
        offset += len(insert_text)
    
    return fill_text


def run_test(model, test, ctx_size, timeout=600):
    """Run a single test at a given context size."""
    fill_tokens = int(ctx_size * 0.80)
    context = build_filled_context(fill_tokens, test["facts"])
    
    prompt = context + f"\n\n{test['question']}\n\nAnswer:"
    
    opts = {"num_ctx": ctx_size, "num_predict": 300}
    if model in NATIVE_SAMPLING_MODELS:
        opts.update(NATIVE_SAMPLING_OPTS)
    data = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": opts,
        "keep_alive": "5m",
        "think": False,
    }
    
    t0 = time.time()
    try:
        resp = api("/api/generate", data, timeout=timeout)
        wall = time.time() - t0
        
        if "error" in resp:
            return {"status": "FAIL", "error": resp["error"][:200], "wall_s": round(wall, 1)}
        
        response = resp.get("response", "")
        pec = resp.get("prompt_eval_count", 0)
        gen_toks = resp.get("eval_count", 0)
        eval_dur = resp.get("eval_duration", 0) / 1e9
        prompt_dur = resp.get("prompt_eval_duration", 0) / 1e9
        load_dur = resp.get("load_duration", 0) / 1e9
        gen_tok_s = gen_toks / eval_dur if eval_dur > 0 else 0
        
        # Check answer
        resp_lower = response.lower()
        primary_correct = test["answer_must_contain"].lower() in resp_lower
        
        # Check alternative answers
        alt_correct = False
        for alt in test.get("alt_answers", []):
            if alt.lower() in resp_lower:
                alt_correct = True
                break
        
        correct = primary_correct or alt_correct
        
        # Check extras if present
        extras_found = {}
        for extra in test.get("check_extras", []):
            extras_found[extra] = extra.lower() in resp_lower
        
        return {
            "status": "OK",
            "correct": correct,
            "response": response,
            "prompt_chars": len(prompt),
            "prompt_question": test["question"],
            "prompt_facts": [f["text"] for f in test["facts"]],
            "prompt_fact_positions": [f["position"] for f in test["facts"]],
            "answer_must_contain": test["answer_must_contain"],
            "alt_answers": test.get("alt_answers", []),
            "check_extras": test.get("check_extras", []),
            "extras_found": extras_found,
            "prompt_eval_count": pec,
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


def short_name(model):
    m = model.replace(":latest", "")
    m = m.replace("qwen3.5-35b-a3b-iq2m", "MoE 35B")
    m = m.replace("qwen3.5:9b", "qwen3.5:9b")
    m = m.replace("phi4-mini", "phi4-mini")
    return m


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    results_file = f"{RESULTS_DIR}/longctx-quality-{ts}.json"
    
    all_tests = MULTIHOP_TESTS + SYNTHESIS_TESTS
    ctx_sizes = [16384, 32768]
    
    total_trials = len(MODELS) * len(all_tests) * len(ctx_sizes)
    
    print(f"""
======================================================================
  LONG-CONTEXT QUALITY BENCHMARK
  Models: {len(MODELS)}
  Tests: {len(MULTIHOP_TESTS)} multi-hop + {len(SYNTHESIS_TESTS)} synthesis = {len(all_tests)}
  Context: 16K, 32K (80% fill)
  Total trials: {total_trials}
  Results: {results_file}
======================================================================
""")
    
    all_results = []
    summary = {}
    start = time.time()
    
    for mi, model in enumerate(MODELS):
        name = short_name(model)
        print(f"\n  [{mi+1}/{len(MODELS)}] {name}")
        print("  " + "─" * 60)
        
        summary[name] = {}
        
        for ctx in ctx_sizes:
            ctx_label = f"{ctx // 1024}K"
            print(f"\n    === {ctx_label} context ===")
            
            # Warm up model at this context size
            unload_all()
            try:
                warmup = api("/api/generate", {
                    "model": model, "prompt": "Hello", "stream": False,
                    "options": {"num_ctx": ctx, "num_predict": 5},
                    "keep_alive": "5m", "think": False,
                }, timeout=300)
                if "error" in warmup:
                    print(f"    WARMUP FAIL: {warmup['error'][:60]}")
                    continue
            except Exception as e:
                print(f"    WARMUP FAIL: {str(e)[:60]}")
                continue
            
            correct_count = 0
            total_count = 0
            
            for test in all_tests:
                total_count += 1
                timeout = 600 if ctx <= 16384 else 900
                
                r = run_test(model, test, ctx, timeout=timeout)
                r["model"] = model
                r["short_name"] = name
                r["test_name"] = test["name"]
                r["task_type"] = test["task_type"]
                r["ctx_size"] = ctx
                r["ctx_label"] = ctx_label
                r["timestamp"] = datetime.datetime.now().isoformat()
                all_results.append(r)
                
                if r["status"] == "OK":
                    mark = "✅" if r["correct"] else "❌"
                    correct_count += r["correct"]
                    extras_str = ""
                    if r.get("extras_found"):
                        found = sum(1 for v in r["extras_found"].values() if v)
                        total_e = len(r["extras_found"])
                        extras_str = f" extras={found}/{total_e}"
                    print(f"    {mark} {test['name']:30s} pec={r['prompt_eval_count']:,}  gen={r['gen_tok_s']:.0f} tok/s  TTFT={r['ttft_s']:.0f}s{extras_str}")
                    if not r["correct"]:
                        print(f"       expected '{test['answer_must_contain']}' in: {r['response'][:120]}")
                else:
                    print(f"    ⏱ {test['name']:30s} {r['status']} ({r.get('error', '?')[:60]})")
            
            summary[name][ctx_label] = f"{correct_count}/{total_count}"
            print(f"    → {ctx_label} score: {correct_count}/{total_count}")
    
    elapsed = time.time() - start
    
    # Save results
    output = {
        "results": all_results,
        "summary": summary,
        "metadata": {
            "timestamp": datetime.datetime.now().isoformat(),
            "elapsed_min": round(elapsed / 60, 1),
            "models": len(MODELS),
            "tests_per_ctx": len(all_tests),
            "ctx_sizes": ctx_sizes,
        }
    }
    with open(results_file, "w") as f:
        json.dump(output, f, indent=2)
    
    print(f"""
======================================================================
  SUMMARY — {elapsed / 60:.1f} minutes
======================================================================
""")
    # Print summary table
    print(f"  {'Model':30s} {'16K':>8s} {'32K':>8s}")
    print(f"  {'─'*30} {'─'*8} {'─'*8}")
    for name, scores in summary.items():
        s16 = scores.get("16K", "—")
        s32 = scores.get("32K", "—")
        print(f"  {name:30s} {s16:>8s} {s32:>8s}")
    
    print(f"\n  Multi-hop: {len(MULTIHOP_TESTS)} tests (3-fact arithmetic chains)")
    print(f"  Synthesis: {len(SYNTHESIS_TESTS)} tests (contradiction detection, temporal reasoning)")
    print(f"\n  Results saved: {results_file}")
    print(f"  Total time: {elapsed / 60:.1f} minutes")


if __name__ == "__main__":
    main()
