#!/usr/bin/env python3
"""bench-phase-c.py — Phase C orchestrator for the BC-250 scientific extension.

One harness for all sub-phases. Run with --step <name>:

  --step smoke      step 0: 1 cell × 3 runs end-to-end (validates harness)
  --step perf       step 1: C1+C2 perf baseline + stats for all 7 models
  --step cold       step 2: C5 cold-start (unload between runs)
  --step ctx-easy   step 3: C3 filled-ctx scaling at 4K, 16K, 32K
  --step ctx-hard   step 4: C3 filled-ctx scaling at 48K+ (per-model)
  --step qual-32k   step 5: C4 quality at filled 32K
  --step qual-long  step 6: C6 quality at 64K / 128K
  --step env-ab     step 7: C7 prefer_host A/B at filled 32K
  --step kv-quant   step 8: C8 KV-quant trade-off

Each step writes its own results JSON; resumable; atomic save per cell.
Driver: llama-completion (b9265). Real prose fill blocks. 3 runs/cell, median+CV.
GPU overlay sampler runs in background during every cell.

Note: tip-of-tree llama.cpp split llama-cli (interactive only) from llama-completion
(non-interactive). Chat-template models still default to conversation mode, so we
pass -no-cnv. LC_ALL=C is set so locale doesn't turn perf decimals into commas.
"""
import argparse, json, os, re, signal, subprocess, sys, time

BUILD_BIN = "/opt/llama.cpp-b9265/build/bin/llama-completion"
BUILD_TAG = "b9265"
# Scratch must live on /home (NVMe). /tmp is a 7.5G tmpfs and fills during
# long fill-context runs, causing models to emit GDB debug spew instead of
# generation output (observed on granite-4.0-h-tiny @ 4K in the prior run).
SCRATCH_DIR = os.path.expanduser("~/phase-c-out/scratch")
os.makedirs(SCRATCH_DIR, exist_ok=True)
RESULTS_DIR = os.path.expanduser("~/phase-c-out/results")
SAMPLER = "/tmp/gpu-overlay-sampler.sh"

# ---------- model registry ----------
# Order: fast/small first → big/slow last (so failures expose late).
#
# Two backends are used:
#   - "llama-completion" (vanilla llama.cpp b9265, our primary harness)
#   - "ollama"           (Ollama 0.20.0 HTTP API; uses its own bundled llama.cpp
#                          fork that supports community-tagged architectures)
#
# Note on gemma4: the Ollama install on this box has `gemma4/latest` (8.95 GiB)
# and `gemma4-26b-q3/latest` (11.67 GiB) blobs. Vanilla llama.cpp b8600 rejects
# them with "unknown model architecture: 'gemma4'"; b9165/b9265 see the arch but
# the GGUFs use a non-stock tensor layout ("wrong number of tensors; expected
# 2131, got 720") consistent with the prior `results-gemma4-26b-q3.json` note
# `"architecture": "26B MoE A4B"` — a custom MoE layout that only Ollama's
# bundled fork loads. We benchmark them via the Ollama HTTP path and flag
# results with backend="ollama". qwen3.5-9b is also measured via BOTH backends
# to give a calibration factor between the two runtimes.
MODELS = [
    {"name": "qwen3.5-9b-q4km",       "path": "/opt/models/qwen3.5-9b-q4km.gguf",        "kind": "dense",      "backend": "llama-completion"},
    {"name": "qwen3.5-9b-ollama",     "path": "qwen3.5:9b",                              "kind": "dense",      "backend": "ollama", "ollama_mode": "chat"},
    # gemma4-* are the production primary-chat models. Their MoE+chat-template
    # combination collapses to empty/looping output under greedy (temp=0)
    # decoding even when content does appear. They produce coherent text only
    # at their native sampling (temp=1, top_k=64, top_p=0.95). We pin seed=42
    # for reproducibility. think:false is set for ALL chat-mode Ollama models
    # below (qwen3.5:9b and gemma4-* both consume num_predict on hidden
    # reasoning tokens otherwise, leaving message.content empty).
    {"name": "gemma4-latest",         "path": "gemma4:latest",                           "kind": "moe_a4b",    "backend": "ollama", "ollama_mode": "chat", "requires_sampling": True},
    {"name": "deepseek-r1-14b",       "path": "/opt/models/deepseek-r1-14b.gguf",        "kind": "dense",      "backend": "llama-completion"},
    {"name": "gpt-oss-20b-mxfp4",     "path": "/opt/models/gpt-oss-20b-mxfp4.gguf",      "kind": "moe_mxfp4",  "backend": "llama-completion"},
    {"name": "gemma4-26b-q3",         "path": "gemma4-26b-q3:latest",                    "kind": "moe_a4b",    "backend": "ollama", "ollama_mode": "chat", "requires_sampling": True},
    {"name": "qwen3-coder-30b-iq2m",  "path": "/opt/models/moe-coder-30b-iq2m.gguf",     "kind": "moe",        "backend": "llama-completion"},
    {"name": "qwen3.5-35b-iq2m",      "path": "/opt/models/qwen3.5-35b-a3b-iq2m.gguf",   "kind": "moe",        "backend": "llama-completion"},
    {"name": "qwen3.6-35b-iq2m",      "path": "/opt/models/qwen3.6-35b-a3b-iq2m.gguf",   "kind": "moe",        "backend": "llama-completion"},
    {"name": "granite-4.0-h-tiny",    "path": "/opt/models/granite-4.0-h-tiny-q4km.gguf","kind": "mamba_moe",  "backend": "llama-completion"},
    # ---- R-phase additions (May 2026 model refresh) ----
    # Tier 1: mandatory for the updated paper.
    {"name": "gemma4-26b-a4b-iq3m",   "path": "/opt/models/gemma4-26b-a4b-iq3m.gguf",    "kind": "moe_a4b",    "backend": "llama-completion"},
    {"name": "gemma4-26b-a4b-ollama", "path": "gemma4-26b-a4b:latest",                   "kind": "moe_a4b",    "backend": "ollama", "ollama_mode": "chat", "requires_sampling": True},
    # gpt-oss-20b duplicated through Ollama for MXFP4 cross-check (Ollama
    # ships MXFP4 kernels in its bundled fork; vanilla llama.cpp may not).
    {"name": "gpt-oss-20b-ollama",    "path": "gpt-oss:20b",                             "kind": "moe_mxfp4",  "backend": "ollama", "ollama_mode": "chat"},
    # Tier 2: refresh + niche coverage.
    {"name": "qwen3.6-27b-q4km",      "path": "/opt/models/qwen3.6-27b-q4km.gguf",       "kind": "dense",      "backend": "llama-completion"},
    {"name": "glm-5.1-q4km",          "path": "/opt/models/glm-5.1-q4km.gguf",           "kind": "dense",      "backend": "llama-completion"},
    {"name": "llama-3.4-8b-q4km",     "path": "/opt/models/llama-3.4-8b-q4km.gguf",      "kind": "dense",      "backend": "llama-completion"},
]
def get_model(name):
    for m in MODELS:
        if m["name"] == name:
            return m
    raise KeyError(name)

# ---------- R1 harness hardening ----------
# Total physical VRAM budget on the BC-250: 16 GiB UMA. After TTM tuning,
# Vulkan exposes ~16.5 GiB across two heaps but the smaller heap (~5.5 GiB BAR)
# is a soft ceiling for weights+KV that fit there. We use 11 GiB as a safe
# working-set ceiling (matches the larger DEVICE_LOCAL heap with margin for
# context activations + runtime overhead). Cells projecting above this are
# refused before launch instead of OOM-killing the orchestrator.
VM_CEILING_GIB = float(os.environ.get("BC250_VM_CEILING_GIB", "11.0"))
MEM_AVAIL_MIN_GIB = float(os.environ.get("BC250_MEM_AVAIL_MIN_GIB", "1.5"))
# KV-cache bytes per token, per layer, per attention head, for f16. We don't
# have layer/head counts here, so use empirical aggregate from llama.cpp logs:
# - dense 7-9B: ~0.25 MiB/token at f16 (~0.06 MiB at q4_0)
# - 14B dense:  ~0.40 MiB/token at f16
# - 30-35B MoE: ~0.50 MiB/token at f16
# Use a conservative per-model override; default 0.30 MiB/token at f16.
KV_BYTES_PER_TOKEN_F16 = {
    "qwen3.5-9b-q4km": 0.25 * 1024 * 1024,
    "qwen3.5-9b-ollama": 0.25 * 1024 * 1024,
    "gemma4-latest": 0.30 * 1024 * 1024,
    "deepseek-r1-14b": 0.40 * 1024 * 1024,
    "gpt-oss-20b-mxfp4": 0.45 * 1024 * 1024,
    "gemma4-26b-q3": 0.50 * 1024 * 1024,
    "qwen3-coder-30b-iq2m": 0.50 * 1024 * 1024,
    "qwen3.5-35b-iq2m": 0.55 * 1024 * 1024,
    "qwen3.6-35b-iq2m": 0.55 * 1024 * 1024,
    "granite-4.0-h-tiny": 0.20 * 1024 * 1024,
    "gemma4-26b-a4b-iq3m": 0.45 * 1024 * 1024,
    "gemma4-26b-a4b-ollama": 0.45 * 1024 * 1024,
    "gpt-oss-20b-ollama": 0.45 * 1024 * 1024,
    "qwen3.6-27b-q4km": 0.45 * 1024 * 1024,
    "glm-5.1-q4km": 0.30 * 1024 * 1024,
    "llama-3.4-8b-q4km": 0.22 * 1024 * 1024,
}
KV_QUANT_SCALE = {"f16": 1.0, "q8_0": 0.5, "q4_0": 0.25}

def weight_gib(model):
    """Best-effort weight size in GiB. Returns 0.0 if path is unresolvable
    (e.g. Ollama tag) so VM guard falls back to KV-only projection."""
    p = model.get("path", "")
    if p and os.path.isfile(p):
        try: return os.path.getsize(p) / (1024**3)
        except Exception: return 0.0
    return 0.0

def projected_vm_gib(model, ctx_tokens, kv_type="q4_0"):
    """Projected GPU working set: weights + KV-cache at the requested ctx.
    Used by the pre-cell guard. Underestimates Ollama-backend weights
    (path is a tag, not a file) — those rely on the MemAvailable gate."""
    w = weight_gib(model)
    kv_bpt = KV_BYTES_PER_TOKEN_F16.get(model["name"], 0.30 * 1024 * 1024)
    kv_bpt *= KV_QUANT_SCALE.get(kv_type, 0.25)
    kv = (ctx_tokens * kv_bpt) / (1024**3)
    return w + kv

def mem_available_gib():
    """Read MemAvailable from /proc/meminfo in GiB."""
    try:
        with open("/proc/meminfo") as f:
            for ln in f:
                if ln.startswith("MemAvailable:"):
                    return int(ln.split()[1]) / (1024**2)  # kB -> GiB
    except Exception:
        pass
    return 999.0  # fail-open so we don't block on weird hosts

def vm_guard(model, ctx_tokens, kv_type, label=""):
    """Returns None if cell may run, else a skip-reason string. The skip
    record is written into results so the cell isn't silently dropped."""
    # Pre-check: model file missing on disk (llama-completion backends only).
    if model.get("backend", "llama-completion") == "llama-completion":
        p = model.get("path", "")
        if p and not os.path.isfile(p):
            return f"model_path_not_found:{p}"
    proj = projected_vm_gib(model, ctx_tokens, kv_type)
    if proj > VM_CEILING_GIB:
        return f"projected_vm_{proj:.1f}gib_over_{VM_CEILING_GIB:.1f}gib_ceiling"
    # Concurrent-VM cap: refuse if host is already under memory pressure
    # (something else is using RAM that the GPU will need to map).
    avail = mem_available_gib()
    if avail < MEM_AVAIL_MIN_GIB:
        # Wait up to 60 s for headroom to recover (e.g. previous cell's
        # mmap drain). If it doesn't, skip the cell instead of hanging.
        for _ in range(12):
            time.sleep(5)
            avail = mem_available_gib()
            if avail >= MEM_AVAIL_MIN_GIB:
                break
        else:
            return f"mem_available_{avail:.1f}gib_under_{MEM_AVAIL_MIN_GIB:.1f}gib_floor"
    return None

def _worker_preexec():
    """Run in the child between fork() and exec(): make the worker the OOM
    killer's preferred target (orchestrator stays alive on memory crunch)."""
    try:
        with open("/proc/self/oom_score_adj", "w") as f:
            f.write("1000")
    except Exception:
        pass
    # Detach from controlling terminal so a SIGINT to the orchestrator
    # can be propagated cleanly (we already use start_new_session in the
    # sampler; do the same here for consistency).
    try: os.setsid()
    except Exception: pass

def sampler_healthcheck():
    """Verify the GPU overlay sampler script exists and emits >=1 line
    within 3 s. Fixes the 40-CU 'samples=0' silent-failure mode where the
    SAMPLER path was wrong and every cell recorded an empty overlay CSV."""
    if not os.path.isfile(SAMPLER):
        print(f"[WARN] sampler script missing at {SAMPLER}; gpu_overlay will be empty", flush=True)
        return False
    test_csv = f"{SCRATCH_DIR}/sampler-healthcheck-{int(time.time())}.csv"
    p = start_sampler(test_csv)
    time.sleep(3)
    stop_sampler(p)
    time.sleep(0.5)
    ok = os.path.exists(test_csv) and os.path.getsize(test_csv) > 0
    if not ok:
        print(f"[WARN] sampler produced no output in 3s ({test_csv}); overlay will be empty", flush=True)
    else:
        try:
            n = sum(1 for _ in open(test_csv))
            print(f"[ok] sampler healthcheck: {n} lines in 3s", flush=True)
        except Exception: pass
    try: os.remove(test_csv)
    except Exception: pass
    return ok

# ---------- prompts ----------
# Standardised baseline prompt (~400 tokens) — RISC vs CISC. Lifted/condensed
# from bench-rigorous.py so new results are byte-comparable to the article.
STD_PROMPT = (
    "Compare RISC and CISC processor architectures. Discuss instruction set design "
    "philosophy, pipeline implications, code density tradeoffs, and the historical "
    "context in which each approach evolved. Cover at least: (a) the original MIPS / "
    "SPARC / ARM RISC designs, (b) the x86 CISC lineage and its eventual adoption of "
    "internal micro-op decoding, (c) why modern processors blur the distinction, and "
    "(d) the impact on compiler design, code size, and energy efficiency. Provide a "
    "concrete example of where RISC's reduced instruction set forced a multi-instruction "
    "sequence that a CISC equivalent handled in one opcode, and explain the performance "
    "implications on modern superscalar implementations. Conclude with a paragraph on "
    "the contemporary relevance of the distinction in light of Apple Silicon's M-series, "
    "the recent RISC-V momentum, and the persistence of x86-64 in servers and laptops."
)

# Real prose fill block — lifted VERBATIM from bench-context-scientific.py for
# direct comparability with the article's existing context-scaling data.
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

# ---------- quality tasks (lifted from bench-rigorous.py) ----------
QUALITY_TASKS = {
    "summarize": {
        "prompt": ("Read the following passage carefully, then write exactly 3 sentences summarizing the key points.\n\n"
                   "PASSAGE:\nThe AMD BC-250 is a crypto-mining board built around AMD's Cyan Skillfish APU, "
                   "featuring a Zen 2 CPU with 6 cores and 12 threads alongside a GFX1013 GPU with 24 compute units "
                   "and 1536 stream processors. The board has 16 GB of GDDR6 unified memory shared between CPU and "
                   "GPU (UMA architecture), with a 256-bit memory bus. Originally deployed in multi-board mining "
                   "racks by ASRock Rack, these boards became available on the secondary market after decommissioning. "
                   "The GPU does not support ROCm's userspace libraries, making Vulkan the only viable compute path "
                   "for AI inference. Despite lacking dedicated matrix multiplication hardware (tensor cores), the "
                   "board can run a 35-billion parameter Mixture-of-Experts language model at 38 tokens per second "
                   "using quantized weights and a Vulkan compute backend.\n\nYOUR 3-SENTENCE SUMMARY:"),
        "max_tokens": 200,
        "checks": {"keywords": ["BC-250", "Vulkan", "16", "35"], "min_sentences": 2, "max_sentences": 5},
    },
    "json_extract": {
        "prompt": ("Extract the following fields from the text below and return ONLY valid JSON. "
                   "No explanation, no markdown, just the JSON object.\n\n"
                   "TEXT: \"Dr. Maria Chen, age 42, works as a Senior Research Scientist at NVIDIA's Santa Clara "
                   "campus. She joined in 2018 and specializes in GPU compiler optimization. Her team has 12 members "
                   "and she holds 7 patents related to shader compilation.\"\n\n"
                   "Required fields: name, age, title, company, year_joined, team_size, patent_count\n\nJSON:"),
        "max_tokens": 200,
        "checks": {"valid_json": True, "required_keys": ["name", "age", "title", "company"],
                   "expected_values": {"name_contains": "Chen", "age": 42}},
    },
    "fact_recall": {
        "prompt": ("I will give you some facts. Memorize them, then answer my question.\n\n"
                   "FACTS:\n"
                   "- The administrative capital of Myanmar is Naypyidaw (moved from Yangon in 2006).\n"
                   "- The deepest point in the ocean is the Challenger Deep at 10,935 meters.\n"
                   "- The chemical symbol for tungsten is W (from German: Wolfram).\n"
                   "- The speed of light in vacuum is exactly 299,792,458 meters per second.\n"
                   "- Ada Lovelace is widely regarded as the first computer programmer.\n\n"
                   "QUESTION: What is the chemical symbol for tungsten, and what word is it derived from?\n\nANSWER:"),
        "max_tokens": 150,
        "checks": {"keywords": ["W", "Wolfram"]},
    },
    "instruction_follow": {
        "prompt": ("List exactly 5 advantages of using solar energy for electricity generation. "
                   "Format each as a numbered item (1. through 5.). Be concise -- one sentence per item. "
                   "Do not include any introduction or conclusion, just the 5 numbered items."),
        "max_tokens": 250,
        "checks": {"has_numbered_items": 5},
    },
    "arithmetic": {
        "prompt": ("Solve this arithmetic problem. Give ONLY the final numerical answer, nothing else.\n\n"
                   "What is 17 x 23?\n\nAnswer:"),
        "max_tokens": 30,
        "checks": {"contains_number": 391},
    },
}

# ---------- llama-cli driver ----------
RE_PROMPT_EVAL = re.compile(r"prompt eval time\s*=\s*([0-9.]+)\s*ms\s*/\s*(\d+)\s*tokens.*?([0-9.]+)\s*tokens per second", re.S)
RE_EVAL        = re.compile(r"\beval time\s*=\s*([0-9.]+)\s*ms\s*/\s*(\d+)\s*runs.*?([0-9.]+)\s*tokens per second", re.S)
RE_LOAD        = re.compile(r"load time\s*=\s*([0-9.]+)\s*ms")
RE_TOTAL       = re.compile(r"total time\s*=\s*([0-9.]+)\s*ms")

OUTPUT_MARKER_BEGIN = "<<<BEGIN_OUTPUT>>>"
OUTPUT_MARKER_END   = "<<<END_OUTPUT>>>"

def start_sampler(out_csv):
    """Spawn the 1Hz GPU overlay sampler; return Popen."""
    return subprocess.Popen(["bash", SAMPLER, out_csv],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)

def stop_sampler(p):
    try:
        os.killpg(os.getpgid(p.pid), signal.SIGTERM)
    except Exception:
        try: p.kill()
        except Exception: pass

def summarise_overlay(csv_path):
    s = {"samples": 0, "throttle_flag": False}
    if not os.path.exists(csv_path):
        return s
    sclks, temps, powers, gtts, vrams = [], [], [], [], []
    with open(csv_path) as f:
        next(f, None)  # skip header
        for line in f:
            try:
                _, sclk, temp, power, gtt, vram, _ = line.strip().split(",")
                sclks.append(int(sclk)); temps.append(float(temp))
                powers.append(int(power)); gtts.append(float(gtt)); vrams.append(float(vram))
            except Exception:
                continue
    s["samples"] = len(sclks)
    if sclks:
        s["sclk_mhz_max"] = max(sclks); s["sclk_mhz_min"] = min(sclks)
        s["sclk_mhz_mean"] = round(sum(sclks)/len(sclks), 1)
        s["temp_c_max"] = max(temps); s["temp_c_mean"] = round(sum(temps)/len(temps), 1)
        s["power_mw_mean"] = round(sum(powers)/len(powers), 0)
        s["gtt_gib_max"] = max(gtts); s["vram_gib_max"] = max(vrams)
        # active-cell throttle heuristic: drop below 1500 MHz while running
        # (idle baseline = 1000 MHz; active expected = 2000 MHz)
        active = [c for c in sclks if c > 1200]   # roughly "non-idle"
        if active and min(active) < 1700:
            s["throttle_flag"] = True
    return s

def parse_timings(stderr_text):
    out = {}
    m = RE_PROMPT_EVAL.search(stderr_text)
    if m:
        out["prompt_eval_ms"] = float(m.group(1))
        out["n_prompt"] = int(m.group(2))
        out["prefill_tok_s"] = float(m.group(3))
    m = RE_EVAL.search(stderr_text)
    if m:
        out["eval_ms"] = float(m.group(1))
        out["n_gen"] = int(m.group(2))
        out["gen_tok_s"] = float(m.group(3))
    m = RE_LOAD.search(stderr_text)
    if m: out["load_ms"] = float(m.group(1))
    m = RE_TOTAL.search(stderr_text)
    if m: out["total_ms"] = float(m.group(1))
    # TTFT proxy: load + prompt_eval
    if "load_ms" in out and "prompt_eval_ms" in out:
        out["ttft_s"] = round((out["load_ms"] + out["prompt_eval_ms"]) / 1000.0, 3)
    return out

def extract_output(stdout_text):
    a = stdout_text.find(OUTPUT_MARKER_BEGIN)
    b = stdout_text.find(OUTPUT_MARKER_END)
    if a < 0:
        return stdout_text.strip()
    a += len(OUTPUT_MARKER_BEGIN)
    if b < 0:
        return stdout_text[a:].strip()
    return stdout_text[a:b].strip()

def run_one(model, ctx, prompt_text, n_gen, env_extra, kv_type="q4_0",
            cell_timeout=1500, overlay_dir=None, label=""):
    """Dispatch to the correct backend."""
    backend = model.get("backend", "llama-completion")
    if backend == "ollama":
        return run_one_ollama(model, ctx, prompt_text, n_gen, env_extra,
                              kv_type=kv_type, cell_timeout=cell_timeout,
                              overlay_dir=overlay_dir, label=label)
    return run_one_llamacompletion(model, ctx, prompt_text, n_gen, env_extra,
                                   kv_type=kv_type, cell_timeout=cell_timeout,
                                   overlay_dir=overlay_dir, label=label)

def run_one_llamacompletion(model, ctx, prompt_text, n_gen, env_extra, kv_type="q4_0",
            cell_timeout=1500, overlay_dir=None, label=""):
    """Run a single llama-completion invocation. Returns dict."""
    rec = {"model": model["name"], "backend": "llama-completion",
           "build": BUILD_TAG, "ctx_target": ctx, "kv_type": kv_type,
           "env_extra": env_extra, "n_gen_target": n_gen, "label": label,
           "projected_vm_gib": round(projected_vm_gib(model, max(ctx, 4096), kv_type), 2)}
    skip = vm_guard(model, max(ctx, 4096), kv_type, label=label)
    if skip:
        rec["status"] = "skip"
        rec["skip_reason"] = skip
        rec["wall_s"] = 0.0
        rec["gpu_overlay"] = {"samples": 0, "throttle_flag": False}
        print(f"   [skip] {model['name']} ctx={ctx} kv={kv_type}: {skip}", flush=True)
        return rec
    # Write prompt to a temp file (avoids shell-escape hell for long prompts)
    pfx = f"{SCRATCH_DIR}/phase-c-cell-{int(time.time()*1000)}"
    pfile = pfx + ".prompt.txt"
    with open(pfile, "w") as f:
        f.write(prompt_text)
    overlay_csv = (overlay_dir or SCRATCH_DIR) + f"/overlay-{int(time.time()*1000)}.csv"
    env = os.environ.copy()
    env["LC_ALL"] = "C"  # avoid locale-formatted decimals (e.g. Polish comma) in perf output
    # Force allocations into heap 1 (DEVICE_LOCAL ~11 GiB) instead of the
    # 5.5 GiB BAR heap. Credit @thehoff (issue #4) for surfacing this on
    # the bc250-40cu-unlock tracker — yields +5-12% tg in our measurements.
    env.setdefault("GGML_VK_PREFER_HOST_MEMORY", "1")
    env.update(env_extra)
    cmd = [BUILD_BIN, "-m", model["path"],
           "-ngl", "99", "-fa", "on",
           "-ctk", kv_type, "-ctv", kv_type,
           "-c", str(max(ctx, 4096)),
           "-n", str(n_gen),
           "--temp", "0", "--seed", "42",
           "-no-cnv", "--no-display-prompt",
           "-f", pfile]
    # Wrap output with markers via a small post-print? llama-cli prints generation
    # directly to stdout; we capture all stdout and treat it as the output.
    sampler = start_sampler(overlay_csv)
    t0 = time.time()
    try:
        cp = subprocess.run(cmd, env=env, capture_output=True, text=True,
                            timeout=cell_timeout, preexec_fn=_worker_preexec)
        wall = time.time() - t0
        rec["wall_s"] = round(wall, 2)
        rec["rc"] = cp.returncode
        timings = parse_timings(cp.stderr)
        rec.update(timings)
        rec["output"] = cp.stdout.strip()
        if cp.returncode != 0:
            rec["status"] = "fail"
            rec["stderr_tail"] = cp.stderr[-2000:]
        elif "gen_tok_s" not in rec:
            rec["status"] = "parse_fail"
            rec["stderr_tail"] = cp.stderr[-2000:]
        else:
            rec["status"] = "ok"
    except subprocess.TimeoutExpired:
        rec["wall_s"] = round(time.time() - t0, 2)
        rec["status"] = "timeout"
        subprocess.run(["pkill", "-9", "-f", "llama-completion"], check=False)
        time.sleep(3)
    except Exception as e:
        rec["wall_s"] = round(time.time() - t0, 2)
        rec["status"] = f"exception:{type(e).__name__}"
        rec["error"] = str(e)[:200]
    finally:
        stop_sampler(sampler)
        time.sleep(0.5)
    rec["gpu_overlay"] = summarise_overlay(overlay_csv)
    rec["overlay_csv"] = overlay_csv
    return rec

def run_one_ollama(model, ctx, prompt_text, n_gen, env_extra, kv_type="q4_0",
                   cell_timeout=1500, overlay_dir=None, label=""):
    """Run a single Ollama call. Returns dict in same schema.

    Uses /api/chat when model["ollama_mode"]=="chat" (chat-template models
    like gemma4-* and qwen3.5:9b — these emit empty/garbage when hit with
    raw /api/generate because their template tokens swallow the prompt).
    Otherwise uses /api/generate. Mode is set in the MODELS registry; default
    is "generate" for backward compatibility with Step 1 perf numbers.
    """
    import urllib.request, urllib.error
    ollama_mode = model.get("ollama_mode", "generate")
    rec = {"model": model["name"], "backend": "ollama",
           "build": "ollama-0.20.0", "ctx_target": ctx, "kv_type": "ollama-default",
           "env_extra": env_extra, "n_gen_target": n_gen, "label": label,
           "ollama_model_tag": model["path"], "ollama_mode": ollama_mode,
           "projected_vm_gib": round(projected_vm_gib(model, max(ctx, 4096), kv_type), 2)}
    skip = vm_guard(model, max(ctx, 4096), kv_type, label=label)
    if skip:
        rec["status"] = "skip"
        rec["skip_reason"] = skip
        rec["wall_s"] = 0.0
        rec["gpu_overlay"] = {"samples": 0, "throttle_flag": False}
        print(f"   [skip-ollama] {model['name']} ctx={ctx}: {skip}", flush=True)
        return rec
    overlay_csv = (overlay_dir or SCRATCH_DIR) + f"/overlay-{int(time.time()*1000)}.csv"
    # Sampling: greedy (temp=0) by default for reproducibility. Models flagged
    # `requires_sampling` (gemma4-*) must run at native params or they emit
    # gibberish loops; we still pin seed=42 so two runs with identical inputs
    # produce identical outputs (Ollama's seed is honored across requests).
    if model.get("requires_sampling"):
        options = {
            "num_ctx": max(ctx, 4096),
            "num_predict": n_gen,
            "temperature": 1.0,
            "top_k": 64,
            "top_p": 0.95,
            "seed": 42,
        }
    else:
        options = {
            "num_ctx": max(ctx, 4096),
            "num_predict": n_gen,
            "temperature": 0.0,
            "seed": 42,
        }
    if ollama_mode == "chat":
        url = "http://127.0.0.1:11434/api/chat"
        payload = {
            "model": model["path"],
            "messages": [{"role": "user", "content": prompt_text}],
            "stream": False,
            # Disable hidden reasoning tokens. Without this, qwen3.5:9b and
            # gemma4-* consume the entire num_predict budget on <think>...
            # content that never reaches message.content -- surface output
            # appears empty. Verified against both models via curl.
            "think": False,
            "keep_alive": "30s",
            "options": options,
        }
    else:
        url = "http://127.0.0.1:11434/api/generate"
        payload = {
            "model": model["path"],
            "prompt": prompt_text,
            "stream": False,
            "keep_alive": "30s",
            "options": options,
        }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    sampler = start_sampler(overlay_csv)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=cell_timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        wall = time.time() - t0
        rec["wall_s"] = round(wall, 2)
        rec["rc"] = 0
        # Ollama returns durations in nanoseconds.
        if "eval_count" in data and data.get("eval_duration"):
            rec["n_gen"] = int(data["eval_count"])
            rec["eval_ms"] = round(data["eval_duration"] / 1e6, 2)
            rec["gen_tok_s"] = round(data["eval_count"] / (data["eval_duration"] / 1e9), 3)
        if "prompt_eval_count" in data and data.get("prompt_eval_duration"):
            rec["n_prompt"] = int(data["prompt_eval_count"])
            rec["prompt_eval_ms"] = round(data["prompt_eval_duration"] / 1e6, 2)
            rec["prefill_tok_s"] = round(data["prompt_eval_count"] / (data["prompt_eval_duration"] / 1e9), 3)
        if data.get("load_duration"):
            rec["load_ms"] = round(data["load_duration"] / 1e6, 2)
        if data.get("total_duration"):
            rec["total_ms"] = round(data["total_duration"] / 1e6, 2)
        if "load_ms" in rec and "prompt_eval_ms" in rec:
            rec["ttft_s"] = round((rec["load_ms"] + rec["prompt_eval_ms"]) / 1000.0, 3)
        if ollama_mode == "chat":
            msg = data.get("message") or {}
            rec["output"] = (msg.get("content") or "").strip()
        else:
            rec["output"] = (data.get("response") or "").strip()
        if "gen_tok_s" not in rec:
            rec["status"] = "parse_fail"
            rec["raw_response"] = {k: v for k, v in data.items() if k != "context"}
        else:
            rec["status"] = "ok"
    except urllib.error.HTTPError as e:
        rec["wall_s"] = round(time.time() - t0, 2)
        rec["status"] = "http_error"
        rec["error"] = f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:300]}"
    except urllib.error.URLError as e:
        rec["wall_s"] = round(time.time() - t0, 2)
        rec["status"] = "url_error"
        rec["error"] = str(e)[:300]
    except (TimeoutError, socket_timeout_error()) as e:
        rec["wall_s"] = round(time.time() - t0, 2)
        rec["status"] = "timeout"
        rec["error"] = str(e)[:200]
    except Exception as e:
        rec["wall_s"] = round(time.time() - t0, 2)
        rec["status"] = f"exception:{type(e).__name__}"
        rec["error"] = str(e)[:200]
    finally:
        stop_sampler(sampler)
        time.sleep(0.5)
    rec["gpu_overlay"] = summarise_overlay(overlay_csv)
    rec["overlay_csv"] = overlay_csv
    return rec

def socket_timeout_error():
    import socket
    return socket.timeout

# ---------- quality scoring ----------
def score_output(task_name, output):
    task = QUALITY_TASKS[task_name]; checks = task["checks"]
    score, details = 1.0, {}
    if "keywords" in checks:
        hit = sum(1 for kw in checks["keywords"] if kw.lower() in output.lower())
        details["keyword_hit"] = f"{hit}/{len(checks['keywords'])}"
        if hit < len(checks["keywords"]): score -= 0.5 * (1 - hit/len(checks["keywords"]))
    if "min_sentences" in checks:
        n = len([s for s in re.split(r"[.!?]+", output) if s.strip()])
        details["sentences"] = n
        if not (checks["min_sentences"] <= n <= checks.get("max_sentences", 99)):
            score -= 0.3
    if checks.get("valid_json"):
        # try to extract JSON region
        i = output.find("{"); j = output.rfind("}")
        try:
            obj = json.loads(output[i:j+1]) if i >= 0 and j > i else None
        except Exception:
            obj = None
        details["valid_json"] = obj is not None
        if obj is None: score = 0
        else:
            for k in checks.get("required_keys", []):
                if k not in obj: score -= 0.15
            for k, v in checks.get("expected_values", {}).items():
                if k.endswith("_contains"):
                    real = obj.get(k[:-len("_contains")], "")
                    if isinstance(real, str) and v.lower() not in real.lower():
                        score -= 0.15
                elif obj.get(k) != v: score -= 0.15
    if "has_numbered_items" in checks:
        n = sum(1 for ln in output.splitlines() if re.match(r"^\s*\d+[.)]", ln))
        details["numbered"] = n
        if n != checks["has_numbered_items"]: score -= 0.5
    if "contains_number" in checks:
        target = str(checks["contains_number"])
        details["target"] = target
        if target not in output: score = 0
    return max(0.0, round(score, 3)), details

# ---------- multi-run aggregator ----------
def median(xs): xs = sorted(xs); n = len(xs); return xs[n//2] if n%2 else (xs[n//2-1]+xs[n//2])/2
def cv_pct(xs):
    if len(xs) < 2: return 0.0
    m = sum(xs)/len(xs)
    if m == 0: return 0.0
    var = sum((x-m)**2 for x in xs) / (len(xs)-1)
    return round((var**0.5) / m * 100, 2)

def aggregate(runs, fields=("gen_tok_s", "prefill_tok_s", "ttft_s")):
    agg = {}
    for f in fields:
        xs = [r[f] for r in runs if isinstance(r.get(f), (int, float))]
        if xs:
            agg[f+"_median"] = round(median(xs), 3)
            agg[f+"_min"] = round(min(xs), 3)
            agg[f+"_max"] = round(max(xs), 3)
            agg[f+"_cv_pct"] = cv_pct(xs)
    return agg

# ---------- atomic save ----------
def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f: json.dump(obj, f, indent=2)
    os.replace(tmp, path)

# ---------- prompt builders ----------
def build_filled_prompt(target_tokens, instruction="\nBased on the above, write a brief 2-sentence summary of the key themes discussed."):
    # rough estimate 1 token ~= 3.8 chars
    chars = int(target_tokens * 3.8)
    block = FILL_BLOCK
    n_blocks = max(1, chars // len(block))
    text = (block * n_blocks)[:chars]
    return text + instruction

# ---------- per-run prompt uniquifier ----------
# Ollama keeps models warm with keep_alive and re-uses the KV-cache across
# requests when the prefix matches. That makes runs 2..N look unrealistically
# fast on prefill (we measured 5000+ t/s on smoke runs 2/3 vs ~220 on run 1).
# To get *real* prefill numbers we prepend a short unique preamble (~30 tokens)
# that defeats prefix matching while keeping the bulk of the prompt identical
# so reasoning/scoring stays comparable.
def unique_prompt(base, tag):
    nonce = f"[run-tag {tag} | nonce {int(time.time()*1000)}]\n"
    preamble = (
        f"You are participating in benchmark run {tag}. The session identifier "
        f"is {nonce.strip()}. Ignore the identifier and answer the following:\n\n"
    )
    return preamble + base

# ---------- step: smoke ----------
def step_smoke():
    out_path = f"{RESULTS_DIR}/step-0-smoke.json"
    model = get_model("deepseek-r1-14b")
    print(f"[smoke] model={model['name']} ctx=4096 prompt=STD runs=3")
    runs = []
    for i in range(3):
        print(f"[smoke] run {i+1}/3 ...", flush=True)
        rec = run_one(model, 4096, STD_PROMPT, n_gen=100, env_extra={},
                      cell_timeout=600, overlay_dir=RESULTS_DIR, label=f"smoke-run{i+1}")
        runs.append(rec)
        print(f"   status={rec['status']} gen={rec.get('gen_tok_s')} pp={rec.get('prefill_tok_s')} "
              f"ttft={rec.get('ttft_s')} wall={rec.get('wall_s')}s "
              f"throttle={rec.get('gpu_overlay',{}).get('throttle_flag')} "
              f"clk_mean={rec.get('gpu_overlay',{}).get('sclk_mhz_mean')} "
              f"temp_max={rec.get('gpu_overlay',{}).get('temp_c_max')}")
        save_json(out_path, {"step": "smoke", "model": model["name"], "runs": runs})
        time.sleep(20)
    agg = aggregate(runs)
    result = {"step": "smoke", "model": model["name"], "runs": runs, "aggregate": agg}
    save_json(out_path, result)
    print("\n=== smoke aggregate ===")
    for k, v in agg.items(): print(f"  {k}: {v}")
    return result

# ---------- step: smoke-ollama (validate Ollama backend path) ----------
def step_smoke_ollama():
    """Run BOTH gemma4 variants AND qwen3.5-9b via Ollama (calibration peer).
    qwen3.5-9b-ollama vs qwen3.5-9b-q4km (llama-completion) lets us quantify the
    Ollama-vs-vanilla overhead and decide whether gemma4 numbers are comparable.
    """
    out_path = f"{RESULTS_DIR}/step-0b-smoke-ollama.json"
    targets = ["qwen3.5-9b-ollama", "gemma4-latest", "gemma4-26b-q3"]
    all_results = {}
    for name in targets:
        model = get_model(name)
        print(f"\n[smoke-ollama] model={model['name']} ctx=4096 prompt=STD runs=3")
        runs = []
        for i in range(3):
            print(f"  run {i+1}/3 ...", flush=True)
            # Unique prompt per run so Ollama's prefix-KV-cache reuse cannot
            # inflate prefill numbers. Without this runs 2-3 hit 5000+ t/s
            # prefill which is cache hit, not real throughput.
            p = unique_prompt(STD_PROMPT, f"{name}-{i+1}")
            rec = run_one(model, 4096, p, n_gen=100, env_extra={},
                          cell_timeout=900, overlay_dir=RESULTS_DIR,
                          label=f"smoke-ollama-{name}-run{i+1}")
            runs.append(rec)
            print(f"   status={rec['status']} gen={rec.get('gen_tok_s')} pp={rec.get('prefill_tok_s')} "
                  f"ttft={rec.get('ttft_s')} wall={rec.get('wall_s')}s "
                  f"n_prompt={rec.get('n_prompt')} n_gen={rec.get('n_gen')} "
                  f"clk_mean={rec.get('gpu_overlay',{}).get('sclk_mhz_mean')} "
                  f"temp_max={rec.get('gpu_overlay',{}).get('temp_c_max')}")
            all_results[name] = {"runs": runs, "aggregate": aggregate(runs)}
            save_json(out_path, {"step": "smoke-ollama", "results": all_results})
            time.sleep(15)
        agg = aggregate(runs)
        print(f"  AGG {name}: median gen={agg.get('gen_tok_s_median')} t/s  pp={agg.get('prefill_tok_s_median')} t/s  cv={agg.get('gen_tok_s_cv_pct')}%")
    save_json(out_path, {"step": "smoke-ollama", "results": all_results})
    print("\n=== smoke-ollama summary ===")
    for name, r in all_results.items():
        a = r["aggregate"]
        g = a.get("gen_tok_s_median"); p = a.get("prefill_tok_s_median"); c = a.get("gen_tok_s_cv_pct")
        print(f"  {name:25s}  gen_median={str(g):>8}  pp_median={str(p):>10}  cv={str(c):>5}%")
    return all_results

# ---------- ollama service control ----------
def ollama_running():
    try:
        urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2).read()
        return True
    except Exception:
        return False

def ollama_stop():
    # systemctl stop ollama only kills `ollama serve` — the child `ollama runner`
    # process keeps the model's GTT pages mapped, eating 8-12 GiB. Always pkill the runner.
    was_running = ollama_running()
    if was_running:
        print("[svc] stopping ollama service ...", flush=True)
        subprocess.run(["sudo", "-n", "systemctl", "stop", "ollama"], check=False)
        for _ in range(20):
            if not ollama_running(): break
            time.sleep(0.5)
    # Hard-kill any leftover ollama runner processes (they survive systemctl stop).
    subprocess.run(["pkill", "-KILL", "-f", "ollama runner"], check=False)
    time.sleep(1.0)
    print(f"[svc] ollama running={ollama_running()} (was {was_running})", flush=True)

def mem_available_gib():
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                kb = int(line.split()[1])
                return kb / 1024 / 1024
    except Exception:
        return 0.0
    return 0.0

def wait_for_mem(target_gib=2.0, timeout_s=20):
    # Best-effort: drop kernel page cache + nudge ollama runners away, then poll MemAvailable.
    subprocess.run(["sudo", "-n", "sysctl", "-w", "vm.drop_caches=3"], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if mem_available_gib() >= target_gib:
            return True
        time.sleep(1.0)
    return mem_available_gib() >= target_gib

def ollama_start():
    if ollama_running(): return
    print("[svc] starting ollama service ...", flush=True)
    subprocess.run(["sudo", "-n", "systemctl", "start", "ollama"], check=False)
    for _ in range(30):
        if ollama_running(): break
        time.sleep(1.0)
    print(f"[svc] ollama running={ollama_running()}", flush=True)

# ---------- step: perf (Step 1 — baseline across all 10 models) ----------
def step_perf(only="", merge=False):
    """All 10 models, 3 runs each, STD_PROMPT @ 4096 ctx, n_gen=100.

    Runs llama-completion models first (with ollama stopped to free RAM),
    then ollama models. Each run uses a unique prompt to defeat any KV-cache
    prefix reuse so prefill numbers are real.

    only:  if non-empty, restrict to comma-separated model names
    merge: if True, load existing JSON and only overwrite entries we re-run
    """
    out_path = f"{RESULTS_DIR}/step-1-perf.json"
    all_results = {}
    if merge and os.path.exists(out_path):
        try:
            existing = json.load(open(out_path))
            all_results = existing.get("results", {})
            print(f"[perf] merge mode: loaded {len(all_results)} existing entries")
        except Exception as e:
            print(f"[perf] merge load failed: {e}; starting fresh")
            all_results = {}
    selected = set(s.strip() for s in only.split(",") if s.strip())
    def keep(m):
        return (not selected) or m["name"] in selected
    # Partition by backend
    lc_models = [m for m in MODELS if m.get("backend", "llama-completion") == "llama-completion" and keep(m)]
    ol_models = [m for m in MODELS if m.get("backend") == "ollama" and keep(m)]
    print(f"[perf] {len(lc_models)} llama-completion models, {len(ol_models)} ollama models"
          + (f"  only={sorted(selected)}" if selected else ""))

    def run_model(model, runs_n=3, sleep_between=30):
        print(f"\n[perf] === {model['name']} ({model.get('backend')}) ===", flush=True)
        runs = []
        for i in range(runs_n):
            tag = f"perf-{model['name']}-run{i+1}"
            print(f"  run {i+1}/{runs_n}  tag={tag}", flush=True)
            p = unique_prompt(STD_PROMPT, tag)
            rec = run_one(model, 4096, p, n_gen=100, env_extra={},
                          cell_timeout=900, overlay_dir=RESULTS_DIR, label=tag)
            runs.append(rec)
            print(f"   status={rec['status']} gen={rec.get('gen_tok_s')} pp={rec.get('prefill_tok_s')} "
                  f"ttft={rec.get('ttft_s')} wall={rec.get('wall_s')}s "
                  f"n_prompt={rec.get('n_prompt')} n_gen={rec.get('n_gen')} "
                  f"throttle={rec.get('gpu_overlay',{}).get('throttle_flag')} "
                  f"clk_mean={rec.get('gpu_overlay',{}).get('sclk_mhz_mean')} "
                  f"temp_max={rec.get('gpu_overlay',{}).get('temp_c_max')}")
            all_results[model["name"]] = {"runs": runs, "aggregate": aggregate(runs),
                                          "backend": model.get("backend"),
                                          "kind": model.get("kind")}
            save_json(out_path, {"step": "perf", "results": all_results})
            if i < runs_n - 1:
                time.sleep(sleep_between)
        agg = aggregate(runs)
        print(f"  AGG {model['name']}: gen_median={agg.get('gen_tok_s_median')} t/s  "
              f"pp_median={agg.get('prefill_tok_s_median')} t/s  cv={agg.get('gen_tok_s_cv_pct')}%")

    # Pass A: llama-completion (ollama stopped)
    if lc_models:
        ollama_stop()
        for m in lc_models:
            wait_for_mem(target_gib=2.0, timeout_s=20)
            run_model(m)
            ollama_stop()  # kill any runner that survived (defence in depth)
            time.sleep(45)  # cooldown between models

    # Pass B: ollama
    if ol_models:
        ollama_start()
        time.sleep(5)
        for m in ol_models:
            run_model(m)
            time.sleep(45)

    # Final summary table
    save_json(out_path, {"step": "perf", "results": all_results})
    print("\n=== perf summary ===")
    print(f"  {'model':28s} {'backend':18s} {'gen_med':>9s} {'pp_med':>10s} {'ttft_med':>10s} {'cv':>6s}")
    for name, r in all_results.items():
        a = r["aggregate"]
        g = a.get("gen_tok_s_median"); p = a.get("prefill_tok_s_median")
        t = a.get("ttft_s_median");    c = a.get("gen_tok_s_cv_pct")
        print(f"  {name:28s} {str(r.get('backend')):18s} {str(g):>9s} {str(p):>10s} {str(t):>10s} {str(c):>5s}%")
    return all_results

# ---------- step: ctx-quality (filled-context sweep with output verification) ----------
CTX_TIERS = [4096, 16384, 32768, 49152, 65536]
# Two needles at different depths catches "lost in the middle" failures and
# stops greedy-MoE/repeat-loop outputs from coincidentally containing a
# fragment of a single code. The PRIMARY (mid) needle is the original
# DELTA-... code so step-2 results stay comparable to the May-22 baseline.
NEEDLE_CODE = "DELTA-7-VIOLET-MOUNTAIN-93"
NEEDLE_CODE_EARLY = "OMEGA-4-COBALT-RIVER-17"
NEEDLE_LINE = (
    "\n\n*** SPECIAL NOTE: The secret access code is "
    + NEEDLE_CODE
    + ". You will be asked about this code at the end of the document. ***\n\n"
)
NEEDLE_LINE_EARLY = (
    "\n\n*** EARLY NOTE: The first auxiliary code is "
    + NEEDLE_CODE_EARLY
    + ". Remember both codes. ***\n\n"
)
NEEDLE_INSTR = (
    "You will be shown a long technical document. Embedded somewhere in the document "
    "are TWO special notes, each containing a code in the format WORD-NUMBER-WORD-"
    "WORD-NUMBER. After the document, you must answer one question about both codes. "
    "Read the entire document carefully.\n\nDOCUMENT BEGINS:\n\n"
)
NEEDLE_QUESTION = (
    "\n\nDOCUMENT ENDS.\n\nQUESTION: What were the two codes embedded in the EARLY "
    "NOTE and the SPECIAL NOTE? Respond with ONLY the two codes, one per line, in the "
    "order they appeared in the document, nothing else.\n\nANSWER:"
)

def build_needle_prompt(target_tokens):
    """Build a prompt of ~target_tokens tokens with two needles:
    EARLY at ~10% depth, PRIMARY at ~50% depth.
    Returns (prompt, depth_pct_primary, depth_pct_early).
    """
    overhead = (len(NEEDLE_INSTR) + len(NEEDLE_QUESTION)
                + len(NEEDLE_LINE) + len(NEEDLE_LINE_EARLY))
    char_budget = int(target_tokens * 3.8) - overhead
    char_budget = max(char_budget, len(FILL_BLOCK))
    n_blocks = char_budget // len(FILL_BLOCK) + 1
    fill_full = (FILL_BLOCK * n_blocks)[:char_budget]
    n = len(fill_full)
    # Snap insert points to nearest newline so we don't split a sentence.
    def snap_at(target):
        i = fill_full.find("\n", target)
        return i if 0 <= i <= target + 200 else target
    early_at = snap_at(int(n * 0.10))
    mid_at = snap_at(int(n * 0.50))
    if mid_at <= early_at:
        mid_at = early_at + 1
    # Insert from the right so earlier offset stays valid.
    body = (fill_full[:early_at]
            + NEEDLE_LINE_EARLY
            + fill_full[early_at:mid_at]
            + NEEDLE_LINE
            + fill_full[mid_at:])
    prompt = NEEDLE_INSTR + body + NEEDLE_QUESTION
    depth_primary = round(mid_at / n * 100, 1) if n else 50.0
    depth_early = round(early_at / n * 100, 1) if n else 10.0
    return prompt, depth_primary, depth_early

def _max_repeat_ngram_frac(text, n=6):
    """Return fraction of `text` covered by the most-repeated character n-gram.
    Used to detect degenerate loops like 'A.M.A.N.T.A.M.A.N.T...' or repeated
    number lists. Returns 0.0 for very short outputs.
    """
    if len(text) < n * 4:
        return 0.0
    from collections import Counter
    grams = [text[i:i+n] for i in range(0, len(text) - n + 1)]
    c = Counter(grams)
    top, count = c.most_common(1)[0]
    # Whitespace-only n-grams (e.g. '      ') aren't a degeneracy signal.
    if top.strip() == "":
        if len(c) < 2:
            return 0.0
        top, count = c.most_common(2)[1]
    return (count * n) / max(len(text), 1)

def _ascii_letter_density(text):
    if not text:
        return 0.0
    letters = sum(1 for ch in text if ("a" <= ch.lower() <= "z"))
    return letters / len(text)

def verify_needle(output, code=NEEDLE_CODE, code_early=NEEDLE_CODE_EARLY):
    """Strict multi-check verifier.

    Returns (pass: bool, reason: str). Rejects:
      - empty output
      - short output (<20 non-ws chars) -- guards against a fragment-only hit
      - looping/degenerate output (max 6-gram covers >40% of text)
      - non-ASCII-letter degeneration (<40% ASCII letters in trimmed output)
      - missing primary needle
      - missing early needle (only when ctx-quality run includes one;
        callers without an early needle pass code_early=None)
    """
    if not output:
        return False, "empty_output"
    stripped = output.strip()
    non_ws = re.sub(r"\s+", "", stripped)
    if len(non_ws) < 20:
        return False, f"too_short:{len(non_ws)}"
    if _max_repeat_ngram_frac(stripped, n=6) > 0.40:
        return False, "repeat_loop"
    if _ascii_letter_density(stripped) < 0.40:
        return False, "low_letter_density"
    def _has_code(target):
        norm_out = re.sub(r"\s+", "", output.upper())
        norm_code = re.sub(r"\s+", "", target.upper())
        if norm_code in norm_out:
            return True, "exact"
        segs = target.upper().split("-")
        pos = 0
        for s in segs:
            i = norm_out.find(s, pos)
            if i < 0:
                return False, s
            pos = i + len(s)
        return True, "ordered_segments"
    ok_primary, why_primary = _has_code(code)
    if not ok_primary:
        return False, f"missing_primary:{why_primary}"
    if code_early is not None:
        ok_early, why_early = _has_code(code_early)
        if not ok_early:
            return False, f"missing_early:{why_early}"
        return True, f"both_present:{why_primary}|{why_early}"
    return True, why_primary

def step_ctx_quality(only="", merge=False, tiers=None, runs_n=2, n_gen=200):
    if tiers is None:
        tiers = CTX_TIERS
    out_path = f"{RESULTS_DIR}/step-2-ctx-quality.json"
    all_results = {}
    if merge and os.path.exists(out_path):
        try:
            existing = json.load(open(out_path))
            all_results = existing.get("results", {})
            print(f"[ctx-quality] merge mode: loaded {len(all_results)} existing model entries")
        except Exception as e:
            print(f"[ctx-quality] merge load failed: {e}; starting fresh")
            all_results = {}
    selected = set(s.strip() for s in only.split(",") if s.strip())
    def keep(m):
        return (not selected) or m["name"] in selected
    lc_models = [m for m in MODELS if m.get("backend", "llama-completion") == "llama-completion" and keep(m)]
    ol_models = [m for m in MODELS if m.get("backend") == "ollama" and keep(m)]
    print(f"[ctx-quality] tiers={tiers}  runs_n={runs_n}  n_gen={n_gen}")
    print(f"[ctx-quality] {len(lc_models)} llama-completion + {len(ol_models)} ollama models"
          + (f"  only={sorted(selected)}" if selected else ""))

    config_blob = {"tiers": tiers, "runs_n": runs_n, "n_gen": n_gen, "needle_code": NEEDLE_CODE}

    def run_model(model):
        name = model["name"]
        print(f"\n[ctx-quality] === {name} ({model.get('backend')}) ===", flush=True)
        per_model = all_results.setdefault(name, {"backend": model.get("backend"),
                                                   "kind": model.get("kind"),
                                                   "tiers": {}})
        ladder_alive = True
        for ctx in tiers:
            tier_key = str(ctx)
            existing_tier = per_model["tiers"].get(tier_key, {})
            existing_ok = sum(1 for r in existing_tier.get("runs", []) if r.get("status") == "ok")
            if merge and existing_ok >= runs_n:
                print(f"  [{ctx}] already complete ({existing_ok}/{runs_n} ok runs) -- skip", flush=True)
                continue
            if not ladder_alive:
                print(f"  [{ctx}] ladder stopped (prior tier failed) -- skip", flush=True)
                per_model["tiers"][tier_key] = {"runs": [], "aggregate": {}, "ladder_status": "stopped"}
                save_json(out_path, {"step": "ctx-quality", "results": all_results, "config": config_blob})
                continue
            prompt, depth_pct, depth_pct_early = build_needle_prompt(ctx)
            prompt_chars = len(prompt)
            # Generous budget — IQ2_M MoE at 64K can drop below 50 t/s prefill
            # (qwen3-coder-30b timed out at 1339s on the first run of this
            # sweep). Formula: ctx/40 covers prefill at 40 t/s worst case;
            # n_gen*3 covers gen at ~7 t/s worst case; +180 cell overhead.
            cell_timeout = max(900, int(ctx / 40) + n_gen * 3 + 180)
            runs = []
            tier_ok = 0
            for i in range(runs_n):
                tag = f"ctxq-{name}-{ctx}-r{i+1}"
                print(f"  [{ctx}] run {i+1}/{runs_n} (target {ctx} tok, needle@{depth_pct}%, "
                      f"prompt_chars={prompt_chars}, timeout={cell_timeout}s)  tag={tag}",
                      flush=True)
                p = unique_prompt(prompt, tag)
                rec = run_one(model, ctx, p, n_gen=n_gen, env_extra={},
                              cell_timeout=cell_timeout, overlay_dir=RESULTS_DIR, label=tag)
                out_text = rec.get("output", "") or ""
                if len(out_text) > 4000:
                    rec["output_truncated"] = True
                    out_text = out_text[:4000]
                rec["output"] = out_text
                rec["output_len_chars"] = len(out_text)
                npass, reason = verify_needle(out_text)
                rec["needle_pass"] = npass
                rec["needle_reason"] = reason
                rec["needle_depth_pct"] = depth_pct
                rec["needle_depth_pct_early"] = depth_pct_early
                runs.append(rec)
                if rec.get("status") == "ok":
                    tier_ok += 1
                print(f"     status={rec.get('status')} gen={rec.get('gen_tok_s')} "
                      f"pp={rec.get('prefill_tok_s')} n_prompt={rec.get('n_prompt')} "
                      f"n_gen={rec.get('n_gen')} needle={npass} ({reason}) "
                      f"out_chars={len(out_text)} "
                      f"throttle={rec.get('gpu_overlay',{}).get('throttle_flag')} "
                      f"temp_max={rec.get('gpu_overlay',{}).get('temp_c_max')}",
                      flush=True)
                npass_total = sum(1 for r in runs if r.get("needle_pass"))
                per_model["tiers"][tier_key] = {
                    "runs": runs,
                    "aggregate": aggregate(runs),
                    "verify": {"pass": npass_total, "total": len(runs),
                               "pass_rate": round(npass_total / len(runs), 3)},
                    "ladder_status": "ok",
                    "depth_pct": depth_pct,
                    "prompt_chars": prompt_chars,
                }
                save_json(out_path, {"step": "ctx-quality", "results": all_results, "config": config_blob})
                time.sleep(15 if ctx <= 16384 else 30)
            if tier_ok == 0:
                ladder_alive = False
                print(f"  [{ctx}] all runs failed -- stopping ladder for {name}", flush=True)
                per_model["tiers"][tier_key]["ladder_status"] = "failed"
            time.sleep(45)
        print(f"  SUMMARY {name}:")
        for ctx in tiers:
            tk = str(ctx)
            t = per_model["tiers"].get(tk, {})
            a = t.get("aggregate", {}); v = t.get("verify", {})
            print(f"    {ctx:>6}  gen={a.get('gen_tok_s_median')} t/s  "
                  f"pp={a.get('prefill_tok_s_median')} t/s  "
                  f"needle={v.get('pass',0)}/{v.get('total',0)}  "
                  f"status={t.get('ladder_status','-')}")

    if lc_models:
        ollama_stop()
        for m in lc_models:
            run_model(m)
            time.sleep(60)
    if ol_models:
        ollama_start()
        time.sleep(5)
        for m in ol_models:
            run_model(m)
            time.sleep(60)

    save_json(out_path, {"step": "ctx-quality", "results": all_results, "config": config_blob})

    print("\n=== ctx-quality summary ===")
    print(f"  {'model':28s}  " + "  ".join(f"{t:>14s}" for t in [str(x) for x in tiers]))
    for name in [m["name"] for m in MODELS if (not selected) or m["name"] in selected]:
        if name not in all_results:
            continue
        r = all_results[name]
        cells = []
        for ctx in tiers:
            tk = str(ctx)
            t = r["tiers"].get(tk, {})
            a = t.get("aggregate", {}); v = t.get("verify", {})
            g = a.get("gen_tok_s_median")
            np_ = v.get("pass", 0); nt = v.get("total", 0)
            if t.get("ladder_status") == "stopped":
                cells.append("        skipped")
            elif g is None:
                cells.append("        no-data")
            else:
                cells.append(f"{g:>6.1f} t/s {np_}/{nt}")
        print(f"  {name:28s}  " + "  ".join(f"{c:>14s}" for c in cells))
    return all_results

# ---------- step: env-snapshot ----------
def step_env_snapshot():
    """Captures the software/hardware stack the rest of the run executes on.
    Written to env-snapshot.json once; later steps reference its hash so a
    paper table can prove every cell ran on the same stack.
    """
    out_path = f"{RESULTS_DIR}/env-snapshot.json"
    def run_cmd(cmd, timeout=10):
        try:
            cp = subprocess.run(cmd, shell=True, capture_output=True,
                                text=True, timeout=timeout)
            return cp.stdout.strip() if cp.returncode == 0 else f"ERR:rc={cp.returncode}:{cp.stderr.strip()[:200]}"
        except Exception as e:
            return f"ERR:{type(e).__name__}:{str(e)[:200]}"
    snap = {
        "step": "env-snapshot",
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "hostname": run_cmd("hostname"),
        "kernel": run_cmd("uname -a"),
        "mesa_version": run_cmd("glxinfo -B 2>/dev/null | grep -E 'OpenGL version|Mesa' | head -3"),
        "vulkan_devices": run_cmd("vulkaninfo --summary 2>/dev/null | head -40"),
        "ollama_version": run_cmd("ollama --version"),
        "ollama_models": run_cmd("ollama list"),
        "llama_cpp_build": BUILD_TAG,
        "llama_cpp_path": BUILD_BIN,
        "llama_cpp_help_head": run_cmd(f"{BUILD_BIN} --version 2>&1 | head -5"),
        "ttm_pages_limit": run_cmd("cat /sys/module/ttm/parameters/pages_limit"),
        "amdgpu_params": run_cmd("for f in /sys/module/amdgpu/parameters/{gtt_size_mb,vm_fragment_size}; do echo $f=$(cat $f 2>/dev/null); done"),
        "drm_card": run_cmd("for c in /sys/class/drm/card*/device; do echo $c=$(cat $c/device 2>/dev/null) vendor=$(cat $c/vendor 2>/dev/null); done"),
        "cpu_info": run_cmd("grep -m1 'model name' /proc/cpuinfo"),
        "mem_total": run_cmd("grep MemTotal /proc/meminfo"),
        "mem_available": run_cmd("grep MemAvailable /proc/meminfo"),
        "cmdline": run_cmd("cat /proc/cmdline"),
        "sclk_dpm": run_cmd("cat /sys/class/drm/card*/device/pp_dpm_sclk 2>/dev/null | head -10"),
        "cu_active": run_cmd("dmesg 2>/dev/null | grep -iE 'cu.*active|spi.*pg_enable|shader.*array' | tail -10"),
        "ggml_env": {k: v for k, v in os.environ.items() if k.startswith(("GGML_", "VK_", "RADV_", "MESA_"))},
        "harness_build": "phase-c-R1",
        "vm_ceiling_gib": VM_CEILING_GIB,
        "mem_avail_min_gib": MEM_AVAIL_MIN_GIB,
    }
    save_json(out_path, snap)
    print(f"[env-snapshot] wrote {out_path}")
    return snap

# ---------- step: mxfp4-probe ----------
def step_mxfp4_probe():
    """Probe whether MXFP4 weights run natively on RADV / Vulkan vs fall back.
    Loads gpt-oss-20b under llama-completion (and optionally Ollama) with a
    minimal generation, captures stderr for kernel selection clues, and
    classifies: native_mxfp4 / fallback_q4 / fail. Either outcome is a
    publishable finding for the paper.
    """
    out_path = f"{RESULTS_DIR}/mxfp4-probe.json"
    result = {"step": "mxfp4-probe", "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "probes": {}}

    # Probe 1: llama-completion vanilla
    lc_model = get_model("gpt-oss-20b-mxfp4") if any(m["name"] == "gpt-oss-20b-mxfp4" for m in MODELS) else None
    if lc_model and os.path.isfile(lc_model["path"]):
        print("[mxfp4-probe] vanilla llama-completion ...")
        rec = run_one(lc_model, 4096, STD_PROMPT + "\n\nProbe MXFP4 path.", n_gen=32,
                      env_extra={"GGML_LOG_LEVEL": "debug"}, cell_timeout=300,
                      overlay_dir=RESULTS_DIR, label="mxfp4-probe-lc")
        stderr = rec.get("stderr_tail", "")
        cls = "fail"
        if rec.get("status") == "ok":
            sl = stderr.lower()
            if "mxfp4" in sl and ("kernel" in sl or "type" in sl or "f_mul_mat" in sl):
                cls = "native_mxfp4"
            elif "q4" in sl or "fallback" in sl or "dequantize" in sl:
                cls = "fallback_q4"
            else:
                cls = "ok_unclassified"
        result["probes"]["llama_completion"] = {
            "classification": cls,
            "status": rec.get("status"),
            "gen_tok_s": rec.get("gen_tok_s"),
            "wall_s": rec.get("wall_s"),
            "skip_reason": rec.get("skip_reason"),
            "stderr_excerpt": stderr[-3000:] if stderr else "",
        }
    else:
        result["probes"]["llama_completion"] = {"classification": "skip_no_gguf",
                                                "note": "/opt/models/gpt-oss-20b-mxfp4.gguf not present"}

    # Probe 2: Ollama (bundled fork may have MXFP4 kernels)
    ol_model = get_model("gpt-oss-20b-ollama") if any(m["name"] == "gpt-oss-20b-ollama" for m in MODELS) else None
    if ol_model:
        print("[mxfp4-probe] ollama bundled fork ...")
        ollama_start(); time.sleep(3)
        rec = run_one(ol_model, 4096, STD_PROMPT + "\n\nProbe MXFP4 path.", n_gen=32,
                      env_extra={}, cell_timeout=300, overlay_dir=RESULTS_DIR,
                      label="mxfp4-probe-ollama")
        result["probes"]["ollama"] = {
            "classification": "native_mxfp4" if rec.get("status") == "ok" else "fail_or_skip",
            "status": rec.get("status"),
            "gen_tok_s": rec.get("gen_tok_s"),
            "wall_s": rec.get("wall_s"),
            "skip_reason": rec.get("skip_reason"),
            "output_excerpt": (rec.get("output") or "")[:500],
        }
    save_json(out_path, result)
    print(f"[mxfp4-probe] wrote {out_path}")
    for backend, p in result["probes"].items():
        print(f"  {backend}: {p.get('classification')}  gen={p.get('gen_tok_s')} t/s  status={p.get('status')}")
    return result

# ---------- step: heap-ab ----------
HEAP_AB_DEFAULT_MODELS = [
    "qwen3.5-9b-q4km", "deepseek-r1-14b",
    "gpt-oss-20b-mxfp4", "qwen3-coder-30b-iq2m",
    "qwen3.5-35b-iq2m", "qwen3.6-35b-iq2m",
]
HEAP_AB_CTX_TIERS = [2048, 8192, 32768]

def step_heap_ab(only="", merge=False, runs_n=2, tiers=None):
    """Controlled A/B of GGML_VK_PREFER_HOST_MEMORY at multiple ctx sizes.
    For each (model, ctx) cell: run 2x with PHM=1 (forces heap 1, DEVICE_LOCAL
    11 GiB) and 2x with PHM=0 (default placement; may put weights into the
    5.5 GiB BAR heap). Reports median tg + paired delta. Replaces the §5.9
    anchor table with a real 6x3x2 matrix.
    """
    ctx_tiers = tiers if tiers else HEAP_AB_CTX_TIERS
    out_path = f"{RESULTS_DIR}/step-heap-ab.json"
    selected = [s.strip() for s in only.split(",") if s.strip()] or HEAP_AB_DEFAULT_MODELS
    all_results = {}
    if merge and os.path.exists(out_path):
        try:
            existing = json.load(open(out_path))
            all_results = existing.get("results", {})
            print(f"[heap-ab] merge: loaded {len(all_results)} existing model entries")
        except Exception as e:
            print(f"[heap-ab] merge load failed: {e}; starting fresh")
    ollama_stop()  # llama-completion only for now (path-resolvable)
    for name in selected:
        try:
            model = get_model(name)
        except KeyError:
            print(f"[heap-ab] unknown model '{name}'; skipping")
            continue
        if model.get("backend") != "llama-completion":
            print(f"[heap-ab] {name}: backend={model.get('backend')} skipped (PHM env doesn't reach Ollama)")
            continue
        all_results.setdefault(name, {"tiers": {}})
        for ctx in ctx_tiers:
            cell = {"phm0": [], "phm1": []}
            for variant, env in [("phm1", {"GGML_VK_PREFER_HOST_MEMORY": "1"}),
                                  ("phm0", {"GGML_VK_PREFER_HOST_MEMORY": "0"})]:
                for i in range(runs_n):
                    tag = f"heap-ab-{name}-{ctx}-{variant}-run{i+1}"
                    print(f"\n[heap-ab] {tag}", flush=True)
                    p = unique_prompt(STD_PROMPT, tag)
                    rec = run_one(model, ctx, p, n_gen=120, env_extra=env,
                                  cell_timeout=1200, overlay_dir=RESULTS_DIR, label=tag)
                    cell[variant].append(rec)
                    print(f"  status={rec['status']} gen={rec.get('gen_tok_s')} "
                          f"pp={rec.get('prefill_tok_s')} wall={rec.get('wall_s')}s "
                          f"skip={rec.get('skip_reason')}")
                    all_results[name]["tiers"][str(ctx)] = {
                        "phm0": cell["phm0"], "phm1": cell["phm1"],
                        "phm0_agg": aggregate(cell["phm0"]) if cell["phm0"] else {},
                        "phm1_agg": aggregate(cell["phm1"]) if cell["phm1"] else {},
                    }
                    save_json(out_path, {"step": "heap-ab", "results": all_results,
                                          "models": selected, "ctx_tiers": HEAP_AB_CTX_TIERS,
                                          "runs_per_variant": runs_n})
                    time.sleep(20)
            # Cell summary
            t = all_results[name]["tiers"][str(ctx)]
            g0 = t["phm0_agg"].get("gen_tok_s_median")
            g1 = t["phm1_agg"].get("gen_tok_s_median")
            delta = (g1 - g0) / g0 * 100 if (g0 and g1) else None
            print(f"  CELL {name} @ ctx={ctx}: phm0={g0} t/s  phm1={g1} t/s  "
                  f"delta={delta:.1f}% (phm1 vs phm0)" if delta is not None else
                  f"  CELL {name} @ ctx={ctx}: phm0={g0}  phm1={g1}  (incomplete)")
        time.sleep(45)  # cooldown between models
    save_json(out_path, {"step": "heap-ab", "results": all_results,
                          "models": selected, "ctx_tiers": ctx_tiers,
                          "runs_per_variant": runs_n})
    # Final compact table
    print("\n=== heap-ab summary (gen_tok_s_median, phm1/phm0/delta%) ===")
    print(f"  {'model':28s}  " + "  ".join(f"{c:>18s}" for c in [f"ctx={c}" for c in ctx_tiers]))
    for name in selected:
        r = all_results.get(name, {})
        cells = []
        for ctx in ctx_tiers:
            t = r.get("tiers", {}).get(str(ctx), {})
            g0 = t.get("phm0_agg", {}).get("gen_tok_s_median")
            g1 = t.get("phm1_agg", {}).get("gen_tok_s_median")
            if g0 and g1:
                d = (g1 - g0) / g0 * 100
                cells.append(f"{g1:>5.1f}/{g0:>5.1f}/{d:+5.1f}%")
            else:
                cells.append("        no-data")
        print(f"  {name:28s}  " + "  ".join(f"{c:>18s}" for c in cells))
    return all_results

# ---------- main ----------
def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    sampler_healthcheck()
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", required=True, choices=["smoke", "smoke-ollama", "perf", "cold", "ctx-easy",
                                                       "ctx-hard", "qual-32k", "qual-long",
                                                       "ctx-quality",
                                                       "env-ab", "kv-quant",
                                                       "env-snapshot", "mxfp4-probe", "heap-ab"])
    ap.add_argument("--only", default="", help="comma-separated model names to restrict to")
    ap.add_argument("--merge", action="store_true", help="merge into existing JSON instead of overwriting")
    ap.add_argument("--tiers", default="", help="comma-separated ctx tiers for ctx-quality")
    ap.add_argument("--runs", type=int, default=2, help="runs per cell for ctx-quality")
    ap.add_argument("--n-gen", type=int, default=200, help="tokens to generate per cell for ctx-quality")
    args = ap.parse_args()
    if args.step == "smoke":
        step_smoke()
    elif args.step == "smoke-ollama":
        step_smoke_ollama()
    elif args.step == "perf":
        step_perf(only=args.only, merge=args.merge)
    elif args.step == "ctx-quality":
        tiers = [int(x) for x in args.tiers.split(",") if x.strip()] if args.tiers else None
        step_ctx_quality(only=args.only, merge=args.merge, tiers=tiers,
                         runs_n=args.runs, n_gen=args.n_gen)
    elif args.step == "env-snapshot":
        step_env_snapshot()
    elif args.step == "mxfp4-probe":
        step_mxfp4_probe()
    elif args.step == "heap-ab":
        tiers = [int(x) for x in args.tiers.split(",") if x.strip()] if args.tiers else None
        step_heap_ab(only=args.only, merge=args.merge, runs_n=args.runs, tiers=tiers)
    else:
        print(f"step {args.step} not implemented yet (will add after smoke validation)")

if __name__ == "__main__":
    main()
