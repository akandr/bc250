#!/usr/bin/env python3
"""A14: does the Q4_0 KV-cache collapse occur on low-KV-head models from other families?

Run as root (sudo python3 bench-a14-heads-control.py): the Ollama model store is not
readable by other users.

A12 found that every collapsing model is a Qwen2.5 build with two or four key/value
heads, that the eight-head 14B builds of the same family are unaffected, and that
the one non-Qwen model with few heads on the board (gemma2:2b, four) does not
collapse.  That leaves head count confounded with family.  This measures models
from other families with one to five key/value heads under the same protocol as
A12 (llama-perplexity b9265, WikiText-2 test, 4096-token context, 24 chunks, flash
attention on, seed 42), at FP16, Q8_0 and Q4_0 KV.

It also splits the cache on collapsing Qwen2.5 builds: Q4_0 keys with FP16 values,
and FP16 keys with Q4_0 values, to locate the collapse in K or V.
"""
import json, os, re, subprocess, sys, time, datetime, struct

BIN  = "/opt/llama.cpp-b9265/build/bin/llama-perplexity"
WIKI = "/home/akandr/wikitext-2-raw/wiki.test.raw"
OUT  = "/home/akandr/phase-c-out/results/step-a14-heads-control.json"   # absolute: runs under sudo (models dir is root-only)
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

def parse_kv(path):
    with open(path, "rb") as f:
        assert struct.unpack("<I", f.read(4))[0] == 0x46554747
        struct.unpack("<I", f.read(4)); struct.unpack("<Q", f.read(8))
        nkv = struct.unpack("<Q", f.read(8))[0]
        def s():
            n = struct.unpack("<Q", f.read(8))[0]; return f.read(n).decode("utf-8", "replace")
        def val(t):
            if t == 8: return s()
            if t == 9:
                et = struct.unpack("<I", f.read(4))[0]; n = struct.unpack("<Q", f.read(8))[0]
                return [val(et) for _ in range(n)]
            fmt = {0:"<B",1:"<b",2:"<H",3:"<h",4:"<I",5:"<i",6:"<f",7:"<?",10:"<Q",11:"<q",12:"<d"}[t]
            return struct.unpack(fmt, f.read(struct.calcsize(fmt)))[0]
        kv = {}
        for _ in range(nkv):
            k = s(); t = struct.unpack("<I", f.read(4))[0]; kv[k] = val(t)
    return kv

def geometry(path):
    kv = parse_kv(path)
    g = lambda suf: next((v for k, v in kv.items() if k.endswith(suf)), None)
    hk = g(".attention.head_count_kv")
    if isinstance(hk, list): hk = max(hk)          # per-layer arrays on some archs
    return {"arch": kv.get("general.architecture"), "n_head": g(".attention.head_count"),
            "n_head_kv": hk, "n_embd": g(".embedding_length"), "n_layer": g(".block_count"),
            "file_type": kv.get("general.file_type")}

def ppl(path, ctk, ctv, ctx=4096, chunks=24):
    subprocess.run("sync; echo 3 | sudo tee /proc/sys/vm/drop_caches", shell=True, capture_output=True)
    time.sleep(4)
    t0 = time.time()
    r = subprocess.run(["sudo", BIN, "-m", path, "-f", WIKI, "-ngl", "99", "-c", str(ctx),
                        "--chunks", str(chunks), "-fa", "on", "-ctk", ctk, "-ctv", ctv, "--seed", "42"],
                       capture_output=True, text=True, timeout=10800)
    txt = r.stdout + r.stderr
    m = re.search(r"Final estimate: PPL = ([\d.]+)", txt)
    if m: return float(m.group(1)), None, round(time.time() - t0, 1)
    err = "load" if re.search(r"error loading model|unable to load", txt) else ("other: " + txt[-300:])
    return None, err, round(time.time() - t0, 1)

CONTROL = [t for t in os.environ.get("A14_CONTROL",
           "gemma3:1b,gemma3:270m,gemma:2b,tinyllama:1.1b,falcon3:1b,smollm2:135m,smollm2:360m,"
           "smollm:135m,smollm:360m,deepseek-r1:1.5b").split(",") if t]
SPLIT   = [t for t in os.environ.get("A14_SPLIT", "qwen2.5:1.5b,qwen2.5:7b").split(",") if t]

out = {"step": "a14-heads-control", "captured_at": datetime.datetime.now().isoformat(),
       "kernel": subprocess.run(["uname", "-r"], capture_output=True, text=True).stdout.strip(),
       "cu": subprocess.run("journalctl -k -b 0 | grep -o 'active_cu_number [0-9]*' | tail -1",
                            shell=True, capture_output=True, text=True).stdout.strip(),
       "binary": BIN, "corpus": WIKI, "n_ctx": 4096, "chunks": 24, "token_budget": 4096 * 24,
       "protocol": "identical to A12 (bench-a12-family.py) plus Q8_0 and a K/V split",
       "control": [], "split": []}

def save(): json.dump(out, open(OUT, "w"), indent=1)

log(f"{out['kernel']} | {out['cu']}")
for tag in CONTROL:
    p = blob(tag)
    if not p or not os.path.exists(p):
        log(f"  {tag:18s} not present, skipped"); out["control"].append({"model": tag, "status": "missing"}); save(); continue
    row = {"model": tag, "gib": round(os.path.getsize(p) / 2**30, 2), **geometry(p)}
    for kv in ("f16", "q8_0", "q4_0"):
        v, err, secs = ppl(p, kv, kv)
        row[kv] = v; row[kv + "_error"] = err; row[kv + "_wall_s"] = secs
    if row.get("f16") and row.get("q4_0"):
        row["ratio_q4"] = round(row["q4_0"] / row["f16"], 4)
        row["ratio_q8"] = round(row["q8_0"] / row["f16"], 4) if row.get("q8_0") else None
        log(f"  {tag:18s} arch={row['arch']:8s} kv_heads={row['n_head_kv']}  f16 {row['f16']:9.4f}  "
            f"q8_0 x{row['ratio_q8']}  q4_0 x{row['ratio_q4']}")
    else:
        log(f"  {tag:18s} FAILED f16={row.get('f16_error')} q4_0={row.get('q4_0_error')}")
    out["control"].append(row); save()

for tag in SPLIT:
    p = blob(tag)
    if not p or not os.path.exists(p):
        log(f"  {tag:18s} not present, skipped"); out["split"].append({"model": tag, "status": "missing"}); save(); continue
    row = {"model": tag, "gib": round(os.path.getsize(p) / 2**30, 2), **geometry(p)}
    for name, ctk, ctv in (("f16", "f16", "f16"), ("k_q4_v_f16", "q4_0", "f16"),
                           ("k_f16_v_q4", "f16", "q4_0"), ("q4_0", "q4_0", "q4_0")):
        v, err, secs = ppl(p, ctk, ctv)
        row[name] = v; row[name + "_error"] = err; row[name + "_wall_s"] = secs
    if row.get("f16"):
        for name in ("k_q4_v_f16", "k_f16_v_q4", "q4_0"):
            row["ratio_" + name] = round(row[name] / row["f16"], 4) if row.get(name) else None
        log(f"  {tag:18s} kv_heads={row['n_head_kv']}  f16 {row['f16']:9.4f}  "
            f"K4/V16 x{row['ratio_k_q4_v_f16']}  K16/V4 x{row['ratio_k_f16_v_q4']}  Q4_0 x{row['ratio_q4_0']}")
    else:
        log(f"  {tag:18s} FAILED f16={row.get('f16_error')}")
    out["split"].append(row); save()

out["finished_at"] = datetime.datetime.now().isoformat(); save()
log(f"done -> {OUT}")
