#!/usr/bin/env python3
"""A12: how far does the Qwen2.5 KV-cache collapse extend, and is it confined to it?

Step A10 found that four Qwen2.5 models collapse under the deployed Q4_0 KV cache
and recover at Q8_0.  Four is too small a sample to support either half of the
claim, and the models were simply the ones already on the board rather than a
designed sample, so more were pulled: the rest of the Qwen2.5 line, and
similar-sized models from other families as a control.

For each model this measures perplexity over WikiText-2 at FP16 and Q4_0 KV.
A collapse is a ratio far above the 1.6 % ceiling seen on unaffected models.
"""
import json, os, re, subprocess, sys, time, datetime

BIN  = "/opt/llama.cpp-b9265/build/bin/llama-perplexity"
WIKI = "/home/akandr/wikitext-2-raw/wiki.test.raw"
OUT  = os.path.expanduser("~/phase-c-out/results/step-a12-family.json")
sys.stdout.reconfigure(line_buffering=True)
def log(m): print(f"[{datetime.datetime.now():%H:%M:%S}] {m}", flush=True)

def blob(tag):
    root = "/usr/share/ollama/.ollama/models"
    fam, ver = tag.split(":")
    man = f"{root}/manifests/registry.ollama.ai/library/{fam}/{ver}"
    if not os.path.exists(man): return None
    for l in json.load(open(man)).get("layers", []):
        if "model" in l.get("mediaType", ""):
            return root + "/blobs/" + l["digest"].replace(":", "-")
    return None

def ppl(path, kv, ctx=4096, chunks=24):
    subprocess.run("sync; echo 3 | sudo tee /proc/sys/vm/drop_caches",
                   shell=True, capture_output=True)
    time.sleep(4)
    r = subprocess.run(["sudo", BIN, "-m", path, "-f", WIKI, "-ngl", "99",
                        "-c", str(ctx), "--chunks", str(chunks), "-fa", "on",
                        "-ctk", kv, "-ctv", kv, "--seed", "42"],
                       capture_output=True, text=True, timeout=10800)
    blob_ = r.stdout + r.stderr
    m = re.search(r"Final estimate: PPL = ([\d.]+)", blob_)
    if m: return float(m.group(1)), None
    err = "load" if re.search(r"error loading model|unable to load", blob_) else "other"
    return None, err

QWEN25 = ["qwen2.5:0.5b","qwen2.5:1.5b","qwen2.5:3b","qwen2.5:7b","qwen2.5:14b",
          "qwen2.5-coder:1.5b","qwen2.5-coder:3b","qwen2.5-coder:7b","qwen2.5-coder:14b"]
OTHERS = ["llama3.2:1b","llama3.2:3b","gemma2:2b","qwen3:1.7b","qwen3:4b","phi4-mini:latest"]

out = {"step":"a12-family","captured_at":datetime.datetime.now().isoformat(),
       "kernel":subprocess.run(["uname","-r"],capture_output=True,text=True).stdout.strip(),
       "cu":subprocess.run("journalctl -k -b 0 | grep -o 'active_cu_number [0-9]*' | tail -1",
                           shell=True,capture_output=True,text=True).stdout.strip(),
       "cells":[]}
for group, tags in (("qwen2.5", QWEN25), ("other-families", OTHERS)):
    for tag in tags:
        p = blob(tag if ":" in tag else tag+":latest")
        if not p:
            log(f"  {tag:22s} not present"); continue
        row = {"group":group, "model":tag, "gib":round(os.path.getsize(p)/2**30,2)}
        for kv in ("f16","q4_0"):
            v, err = ppl(p, kv)
            row[kv] = v; row[kv+"_error"] = err
        if row.get("f16") and row.get("q4_0"):
            row["ratio"] = round(row["q4_0"]/row["f16"], 3)
            log(f"  {group:14s} {tag:22s} f16 {row['f16']:9.4f}  q4_0 {row['q4_0']:11.4f}  "
                f"x{row['ratio']:.2f}")
        else:
            log(f"  {group:14s} {tag:22s} FAILED ({row.get('f16_error')}/{row.get('q4_0_error')})")
        out["cells"].append(row)
        json.dump(out, open(OUT,"w"), indent=1)
log(f"done -> {OUT}")
