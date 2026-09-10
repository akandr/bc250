#!/usr/bin/env python3
"""A15: is the arithmetic outcome for the three "lose only arithmetic at Q8_0" models a
property of the request sequence rather than of the cache width?

A10 scored deepseek-r1:14b, deepseek-r1:8b and mistral-nemo:12b 15/15 at Q4_0 and 12/15
at Q8_0, the three lost runs being the arithmetic item (17 x 23) in every case.  The
Threats section reports that on this runtime a temperature-0, fixed-seed answer to that
item depends on the requests that preceded it in the session.  This measures, for each
of the three models at both widths, the same item under three sequences:

  isolated  the item as the first request of a fresh server session (three sessions)
  replay    A10's exact per-model sequence: warm-up, then five tasks x three runs in
            A10's order with arithmetic last (one session)
  reordered the same fifteen requests with the arithmetic block moved first, and moved
            to the middle (one session each)

Prompts, checks, aggregation and request options are imported from the A10 harness so
nothing differs but the order.  Every response is saved.
"""
import json, os, re, subprocess, sys, time, datetime, urllib.request

OLLAMA   = "http://127.0.0.1:11434"
OVERRIDE = "/etc/systemd/system/ollama.service.d/override.conf"
OUT      = os.path.expanduser("~/phase-c-out/results/step-a15-arith-sequence.json")
MODELS   = [m for m in os.environ.get("A15_MODELS", "deepseek-r1:14b,deepseek-r1:8b,mistral-nemo:12b").split(",") if m]
KV_TYPES = ["q8_0", "q4_0"]          # failing width first; end on the deployed width
RUNS = 3
sys.stdout.reconfigure(line_buffering=True)
def log(m): print(f"[{datetime.datetime.now():%H:%M:%S}] {m}", flush=True)

# --- import A10's tasks, checks and scoring verbatim -----------------------------------
src = open(os.path.expanduser("~/bench-a10-competence-canonical.py")).read()
src = src.replace('if __name__ == "__main__":\n    main()', '')
a10 = {}; exec(compile(src, "a10", "exec"), a10)
QT = a10["QUALITY_TASKS"]; check_quality = a10["check_quality"]; task_passed = a10["task_passed"]
A10_ORDER = list(QT.keys())                                   # summarize, json_extract, fact_recall, instruction_follow, arithmetic
ORDERS = {
    "replay":          A10_ORDER,
    "arith_first":     ["arithmetic"] + [t for t in A10_ORDER if t != "arithmetic"],
    "arith_middle":    ["summarize", "json_extract", "arithmetic", "fact_recall", "instruction_follow"],
}

def api(ep, data, timeout=600):
    r = urllib.request.Request(f"{OLLAMA}{ep}", data=json.dumps(data).encode(),
                               headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=timeout).read())

def gen(model, prompt, npred=300):
    return api("/api/generate", {"model": model, "prompt": prompt, "stream": False, "think": False,
               "keep_alive": "10m", "options": {"num_ctx": 4096, "num_predict": npred,
               "temperature": 0.0, "seed": 42}}, 600)

def foreign_clients():
    out = subprocess.run("ss -tnp 2>/dev/null | grep ':11434' | grep -v '\"ollama\"'",
                         shell=True, capture_output=True, text=True).stdout
    return len([l for l in out.splitlines() if l.strip()])

def restart_ollama():
    subprocess.run(["sudo", "systemctl", "restart", "ollama"], check=True); time.sleep(15)

def set_kv(val):
    log(f"  setting OLLAMA_KV_CACHE_TYPE={val}")
    c = open(OVERRIDE).read()
    line = f'Environment="OLLAMA_KV_CACHE_TYPE={val}"'
    c = (re.sub(r'Environment="OLLAMA_KV_CACHE_TYPE=[^"]*"', line, c)
         if "OLLAMA_KV_CACHE_TYPE" in c else c.replace("[Service]", f"[Service]\n{line}", 1))
    open("/tmp/_a15.conf", "w").write(c)
    subprocess.run(f"sudo cp /tmp/_a15.conf {OVERRIDE}", shell=True, check=True)
    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
    restart_ollama()
    got = subprocess.run("systemctl show ollama -p Environment", shell=True, capture_output=True, text=True).stdout
    assert f"OLLAMA_KV_CACHE_TYPE={val}" in got, f"KV not applied: {got[:200]}"

def run_item(model, tname):
    task = QT[tname]
    try:
        resp = gen(model, task["prompt"])
        txt = resp.get("response", "")
        sc = check_quality(tname, txt, task["checks"])
        return {"task": tname, "passed": task_passed(tname, sc), "scores": sc,
                "response": txt, "eval_count": resp.get("eval_count")}
    except Exception as e:
        return {"task": tname, "passed": False, "error": str(e)[:250]}

def run_sequence(model, order):
    """A10's loop shape: warm-up, then for each task in `order`, RUNS consecutive requests."""
    restart_ollama()
    fc0 = foreign_clients()
    api("/api/generate", {"model": model, "prompt": "Hello", "stream": False,
                          "options": {"num_ctx": 4096, "num_predict": 10}}, 600)
    reqs = []; pos = 1
    for tname in order:
        for i in range(RUNS):
            pos += 1
            r = run_item(model, tname); r["run"] = i + 1; r["position"] = pos; reqs.append(r)
    per = {t: sum(1 for r in reqs if r["task"] == t and r["passed"]) for t in order}
    return {"foreign_clients_at_start": fc0, "order": order, "per_task": per,
            "arithmetic": per["arithmetic"], "total": sum(per.values()), "requests": reqs}

def main():
    kern = subprocess.run(["uname", "-r"], capture_output=True, text=True).stdout.strip()
    cu = subprocess.run("journalctl -k -b 0 | grep -o 'active_cu_number [0-9]*' | tail -1",
                        shell=True, capture_output=True, text=True).stdout.strip()
    ver = subprocess.run(["ollama", "--version"], capture_output=True, text=True).stdout.strip()
    fc = foreign_clients()
    for _ in range(30):                      # tolerate a transient status poll (/api/ps) from cron
        if fc == 0: break
        time.sleep(2); fc = foreign_clients()
    assert fc == 0, f"{fc} foreign client(s) connected to Ollama; stop production services first"
    log(f"{kern} | {cu} | {ver} | foreign Ollama clients: {fc}")
    out = {"step": "a15-arith-sequence", "captured_at": datetime.datetime.now().isoformat(),
           "kernel": kern, "cu": cu, "ollama": ver, "runs_per_task": RUNS, "models": MODELS,
           "kv_types": KV_TYPES, "orders": ORDERS,
           "options": {"num_ctx": 4096, "num_predict": 300, "temperature": 0.0, "seed": 42, "think": False},
           "prompts_and_checks": "imported from bench-a10-competence-canonical.py", "cells": []}
    def save(): json.dump(out, open(OUT, "w"), indent=1)
    t0 = time.time()
    for kv in KV_TYPES:
        set_kv(kv)
        for model in MODELS:
            cell = {"model": model, "kv_type": kv, "isolated": [], "sequences": {}}
            # (A) isolated: first request of a fresh session, three sessions
            for s in range(1, 4):
                restart_ollama()
                r = run_item(model, "arithmetic"); r["session"] = s; r["position"] = 1
                r["foreign_clients"] = foreign_clients()
                cell["isolated"].append(r)
            iso = sum(1 for r in cell["isolated"] if r["passed"])
            # (B) replay and (C) reordered sequences
            for name, order in ORDERS.items():
                cell["sequences"][name] = run_sequence(model, order)
            seqs = cell["sequences"]
            log(f"  {kv} {model:18s} isolated {iso}/3 | replay arith {seqs['replay']['arithmetic']}/3 "
                f"(total {seqs['replay']['total']}/15) | arith_first {seqs['arith_first']['arithmetic']}/3 "
                f"(total {seqs['arith_first']['total']}/15) | arith_middle {seqs['arith_middle']['arithmetic']}/3 "
                f"(total {seqs['arith_middle']['total']}/15)")
            cell["summary"] = {"isolated_pass": iso, **{k: v["arithmetic"] for k, v in seqs.items()}}
            out["cells"].append(cell); save()
    set_kv("q4_0")   # leave the board on the deployed setting
    out["elapsed_min"] = round((time.time() - t0) / 60, 1); save()
    log(f"done -> {OUT}")

if __name__ == "__main__":
    main()
