#!/usr/bin/env python3
"""A5: Graded KV-width and flash-attention validation by perplexity.

Paper revision experiment, follow-up to A1 and A3.

The task-level probes used in the paper (multi-hop pass counts, needle recall)
are high-ceiling: the capable models score at ceiling under every KV width, so
the probe cannot resolve a degradation that leaves task success intact.  This
harness replaces that bounded claim with a graded one.  Perplexity over
WikiText-2 is teacher-forced, so it is also the right instrument for the
flash-attention check: it compares the two kernels on identical token sequences
and never lets a single differing logit divert the continuation, which is what
made exact token match unusable in the generation-based check.

Matrix per model: flash attention on x KV in {f16, q8_0, q4_0}, plus
flash attention off at f16 KV as the paired kernel reference.

Canonical stack: llama.cpp b9265 (the build every llama.cpp number in the paper
comes from; llama-perplexity added to the same build tree and configuration),
verified-stock 24 CU, oberon 1500 MHz.

Output: JSON with the final PPL, its reported standard error, and the per-chunk
negative log-likelihood recovered from the running estimates, so conditions can
be compared pairwise on identical text rather than by their standard errors.
"""
import json, math, os, re, subprocess, sys, time, datetime

BIN     = "/opt/llama.cpp-b9265/build/bin/llama-perplexity"
CORPUS  = "/home/akandr/wiki.test.raw"
OUT     = os.path.expanduser("~/phase-c-out/results/step-a5-perplexity.json")
RAW_DIR = os.path.expanduser("~/phase-c-out/results/a5-raw")
# llama-perplexity holds logits for every position in the window
# (n_ctx x n_vocab x 4 B), which on a 16 GB unified pool is a first-class
# memory cost: 4096 x 152k vocab is ~2.5 GiB on top of the weights.  The
# context is therefore set per model so that weights + logits + KV stay
# resident; an earlier 4096-token attempt drove the 10.6 GiB MoE into active
# paging.  Comparisons are within a model across conditions, so the per-model
# context does not affect them.  TOKEN_BUDGET keeps the evaluated token count
# equal across models.
TOKEN_BUDGET = int(os.environ.get("A5_TOKENS", "98304"))
COOL_C  = 72          # match the paper's per-cell thermal gate
COOL_MAX= 600

MODELS = [   # (name, path, n_ctx)
    ("qwen3.5-35b-a3b-iq2m",
     "/usr/share/ollama/.ollama/models/blobs/"
     "sha256-187b5bbb81d7ec1dc228cb04120302cc0d486cb3c988501af87e5f02e15ace0e", 2048),
    ("phi4-mini",
     "/usr/share/ollama/.ollama/models/blobs/"
     "sha256-3c168af1dea0a414299c7d9077e100ac763370e5a98b3c53801a958a47f0a5db", 4096),
    ("deepseek-r1-14b", "/opt/models/deepseek-r1-14b.gguf", 4096),
]
# (label, flash_attn, kv_type)
CONDS = [
    ("fa_on_f16",  "on",  "f16"),    # reference
    ("fa_on_q8_0", "on",  "q8_0"),
    ("fa_on_q4_0", "on",  "q4_0"),   # the deployed configuration
    ("fa_off_f16", "off", "f16"),    # paired kernel reference for fa_on_f16
]

os.makedirs(RAW_DIR, exist_ok=True)
sys.stdout.reconfigure(line_buffering=True)

def log(m): print(f"[{datetime.datetime.now():%H:%M:%S}] {m}", flush=True)

def gpu_temp():
    for h in ("/sys/class/drm/card0/device/hwmon", "/sys/class/drm/card1/device/hwmon"):
        try:
            for d in os.listdir(h):
                p = os.path.join(h, d, "temp1_input")
                if os.path.exists(p):
                    return int(open(p).read().strip()) / 1000.0
        except Exception:
            pass
    return None

def gpu_clock():
    for p in ("/sys/class/drm/card0/device/pp_dpm_sclk", "/sys/class/drm/card1/device/pp_dpm_sclk"):
        try:
            for line in open(p):
                if "*" in line:
                    return int(re.search(r"(\d+)Mhz", line).group(1))
        except Exception:
            pass
    return None

def mem_settle(min_avail_mb=4000, max_wait=180):
    """Wait for the previous cell's GTT and page cache to be reclaimed.

    Killing or exiting a llama.cpp process does not return its GTT pages
    instantly; launching the next cell too early makes RADV fail a
    buffer allocation that would otherwise succeed.  This waits for
    available memory to recover and drops the page cache first.
    """
    subprocess.run("sync; echo 3 | sudo tee /proc/sys/vm/drop_caches",
                   shell=True, capture_output=True)
    t0 = time.time()
    while time.time() - t0 < max_wait:
        try:
            info = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
            if info.get("MemAvailable", 0) / 1024 >= min_avail_mb:
                break
        except Exception:
            break
        time.sleep(5)
    time.sleep(5)
    info = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
    return round(info.get("MemAvailable", 0) / 1024)

def cooldown():
    t0 = time.time()
    while time.time() - t0 < COOL_MAX:
        t = gpu_temp()
        if t is None or t <= COOL_C:
            return t
        time.sleep(10)
    return gpu_temp()

def per_chunk_nll(running):
    """Recover per-chunk mean NLL from the cumulative PPL series.

    llama-perplexity prints the running estimate after chunk n, which is
    exp(mean NLL over chunks 1..n).  The per-chunk mean NLL is therefore
    n*ln(P_n) - (n-1)*ln(P_{n-1}), which lets two conditions be compared on
    identical text chunk by chunk instead of through their standard errors.
    """
    out = []
    for n, p in enumerate(running, start=1):
        if p <= 0:
            out.append(None); continue
        cur = n * math.log(p)
        prev = (n - 1) * math.log(running[n - 2]) if n > 1 and running[n - 2] > 0 else 0.0
        out.append(cur - prev)
    return out

def run(model_name, model_path, ctx, chunks, cond_label, fa, kv):
    tag = f"{model_name}__{cond_label}__c{ctx}"
    raw_path = os.path.join(RAW_DIR, tag + ".log")
    cmd = ["sudo", BIN, "-m", model_path, "-f", CORPUS, "-ngl", "99",
           "-c", str(ctx), "--chunks", str(chunks),
           "-fa", fa, "-ctk", kv, "-ctv", kv, "--seed", "42"]
    avail = mem_settle()
    t_pre = cooldown()
    log(f"  {tag}: start (edge {t_pre}C, sclk {gpu_clock()}MHz, avail {avail}MB)")
    attempts = 0
    while True:
        attempts += 1
        t0 = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=14400)
        wall = time.time() - t0
        blob = proc.stdout + proc.stderr
        running = [float(x) for x in re.findall(r"\[\d+\]([\d.]+)", blob)]
        m = re.search(r"Final estimate: PPL = ([\d.]+) \+/- ([\d.]+)", blob)
        ppl, err = (float(m.group(1)), float(m.group(2))) if m else (None, None)
        # A RADV buffer-allocation failure is a transient memory-reclaim race,
        # not a property of the cell; retry once after a longer settle.
        transient = ppl is None and "Failed to allocate a buffer" in blob
        if not transient or attempts >= 2:
            break
        log(f"  {tag}: transient RADV allocation failure, settling and retrying")
        mem_settle(min_avail_mb=5000, max_wait=300)
    open(raw_path, "w").write(blob)
    nll = per_chunk_nll(running) if running else []
    ok = ppl is not None
    log(f"  {tag}: {'PPL %.5f +/- %.5f' % (ppl, err) if ok else 'FAILED'} "
        f"({len(running)} chunks, {wall/60:.1f} min)")
    return {
        "model": model_name, "condition": cond_label, "flash_attn": fa,
        "kv_type": kv, "n_ctx": ctx, "chunks_requested": chunks,
        "chunks_seen": len(running), "ppl": ppl, "ppl_stderr": err,
        "running_ppl": running, "per_chunk_nll": nll,
        "wall_s": round(wall, 1), "status": "OK" if ok else "FAILED",
        "edge_c_pre": t_pre, "edge_c_post": gpu_temp(), "attempts": attempts,
        "mem_avail_mb_pre": avail,
        "sclk_mhz_post": gpu_clock(),
        "returncode": proc.returncode, "raw_log": raw_path,
    }

def main():
    started = datetime.datetime.now().isoformat()
    kern = subprocess.run(["uname", "-r"], capture_output=True, text=True).stdout.strip()
    cu = subprocess.run("journalctl -k -b 0 | grep -o 'active_cu_number [0-9]*' | tail -1",
                        shell=True, capture_output=True, text=True).stdout.strip()
    results = []
    out = {"step": "a5-perplexity", "captured_at": started, "kernel": kern, "cu": cu,
           "binary": BIN, "corpus": CORPUS, "token_budget": TOKEN_BUDGET,
           "conditions": [c[0] for c in CONDS], "cells": results}
    for mname, mpath, ctx in MODELS:
        chunks = max(1, TOKEN_BUDGET // ctx)
        log(f"=== {mname} (n_ctx={ctx}, chunks={chunks}) ===")
        for clabel, fa, kv in CONDS:
            try:
                results.append(run(mname, mpath, ctx, chunks, clabel, fa, kv))
            except Exception as e:
                log(f"  {mname}/{clabel}: EXCEPTION {e}")
                results.append({"model": mname, "condition": clabel, "flash_attn": fa,
                                "kv_type": kv, "status": "EXCEPTION", "error": str(e)})
            json.dump(out, open(OUT, "w"), indent=1)
    out["elapsed_min"] = round(
        (datetime.datetime.now() - datetime.datetime.fromisoformat(started)).total_seconds() / 60, 1)
    json.dump(out, open(OUT, "w"), indent=1)
    log(f"done -> {OUT}")

if __name__ == "__main__":
    main()
