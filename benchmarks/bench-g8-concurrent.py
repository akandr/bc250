#!/usr/bin/env python3
"""G8: Concurrent-contention repeats — n=3 pairs, mean±spread.

Re-runs the W4.D concurrent stress test with n=3 repetitions of the
granite+deepseek-14b stall pair, plus one additional pair (granite+qwen3.5-9b-q4km)
spanning a smaller combined footprint (~10 GiB vs ~12.5 GiB).

Each repetition: serial baseline A, serial baseline B, then parallel.
Data -> ~/phase-c-out/results/step-g8-concurrent.json
Power sampler at 5 Hz via /tmp/gpu-overlay-sampler.sh if available.
"""

import json, os, re, subprocess, time, datetime, struct, signal, threading

LLAMA = "/opt/llama.cpp-b9265/build/bin/llama-completion"
OUT = os.path.expanduser("~/phase-c-out/results/step-g8-concurrent.json")
LOG_DIR = os.path.expanduser("~/phase-c-out/logs")
SAMPLER = "/tmp/gpu-overlay-sampler.sh"

# 4K-token prompt (~16 KB): repeated text block
PROMPT_TEXT = (
    "The concurrent stress test exercises two parallel llama-completion "
    "processes against the AMD BC-250 16 GiB UMA to characterize contention, "
    "throughput degradation, and any VRAM-eviction or thermal effects under "
    "realistic load. Each process runs independently on the shared GPU. "
) * 64

PAIRS = [
    {
        "name": "granite_deepseek14b",
        "m1": "/opt/models/granite-4.0-h-tiny-q4km.gguf",
        "m2": "/opt/models/deepseek-r1-14b.gguf",
        "m1_label": "granite-4.0-h-tiny (~4 GiB)",
        "m2_label": "deepseek-r1-14b (~8.4 GiB)",
        "combined_gib": 12.5,
    },
    {
        # Larger combined footprint (~16 GiB weights) — stresses the UMA ceiling.
        # NOTE: qwen3.5-9b-q4km is b9265-incompatible (rope.dimension_sections
        # array-length error), so it cannot be the second tenant on this build.
        "name": "granite_gptoss20b",
        "m1": "/opt/models/granite-4.0-h-tiny-q4km.gguf",
        "m2": "/opt/models/gpt-oss-20b-mxfp4.gguf",
        "m1_label": "granite-4.0-h-tiny (~4 GiB)",
        "m2_label": "gpt-oss-20b-mxfp4 (~11.3 GiB)",
        "combined_gib": 15.5,
    },
]

N_RUNS = 3
N_GEN = 200
N_CTX = 4096


def run_llama(model_path, tag, timeout=300):
    """Run llama-cli in non-interactive mode, return wall_s."""
    prompt_file = f"{LOG_DIR}/g8-prompt.txt"
    stdout_file = f"{LOG_DIR}/g8-{tag}.stdout"
    stderr_file = f"{LOG_DIR}/g8-{tag}.stderr"
    with open(prompt_file, "w") as f:
        f.write(PROMPT_TEXT)
    t0 = time.time()
    timed_out = False
    # LC_ALL=C forces '.' decimal + no thousands grouping in llama.cpp's
    # perf print (the board's default pl_PL locale prints "99,04" which broke
    # the old parser).
    env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    try:
        proc = subprocess.run(
            [LLAMA, "-m", model_path, "-ngl", "99", "-fa", "on",
             "-ctk", "q4_0", "-ctv", "q4_0",
             "-c", str(N_CTX), "-n", str(N_GEN),
             "--temp", "0", "--seed", "42", "-no-cnv", "--no-display-prompt",
             "-f", prompt_file],
            stdout=open(stdout_file, "w"), stderr=open(stderr_file, "w"),
            timeout=timeout, env=env,
        )
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        rc = -1
        timed_out = True
    wall_s = round(time.time() - t0, 3)
    # Extract gen tok/s from stderr. The line looks like:
    #   eval time = 2009.35 ms / 199 runs (10.10 ms per token, 99.04 tokens per second)
    # The trailing token is "second)" (paren attached), so match on the number
    # immediately preceding "tokens per second", tolerating comma decimals.
    gen_tps = None
    try:
        with open(stderr_file) as sf:
            for line in sf:
                if "eval time" in line and "tokens per second" in line and "prompt eval" not in line:
                    m = re.search(r"([\d.,]+)\s+tokens per second", line)
                    if m:
                        v = m.group(1)
                        if "," in v and "." not in v:
                            v = v.replace(",", ".")   # comma decimal
                        else:
                            v = v.replace(",", "")    # thousands sep
                        gen_tps = float(v)
                    break
    except Exception:
        pass
    # rc<0 (SIGKILL by OOM-killer) or rc>0 (load/arg error) => the process did
    # not complete its decode; wall_s is not a valid completion time.
    ok = (rc == 0 and gen_tps is not None)
    return {"tag": tag, "model": model_path, "rc": rc, "wall_s": wall_s,
            "gen_tok_s": gen_tps, "ok": ok, "timed_out": timed_out}


def run_parallel(m1, m2, tag_a, tag_b, timeout=300):
    """Run two llama-cli processes in parallel, return both results."""
    results = {}
    def worker(model, tag):
        results[tag] = run_llama(model, tag, timeout=timeout)

    t_a = threading.Thread(target=worker, args=(m1, tag_a))
    t_b = threading.Thread(target=worker, args=(m2, tag_b))
    t_a.start()
    time.sleep(3)   # stagger model load
    t_b.start()
    t_a.join()
    t_b.join()
    return results.get(tag_a), results.get(tag_b)


def sampler_available():
    if not os.path.exists(SAMPLER):
        return False
    try:
        r = subprocess.run(["bash", SAMPLER, "--probe"], timeout=5, capture_output=True)
        return r.returncode == 0
    except Exception:
        return False


def main():
    os.makedirs(LOG_DIR, exist_ok=True)

    print(f"""
======================================================================
  G8: Concurrent-Contention Repeats (n={N_RUNS})
  Pairs: {[p['name'] for p in PAIRS]}
  n_gen={N_GEN}, n_ctx={N_CTX}
  Output: {OUT}
======================================================================
""")

    all_results = []
    total_start = time.time()

    for pair in PAIRS:
        name = pair["name"]
        m1, m2 = pair["m1"], pair["m2"]

        # Check model files exist
        for path in [m1, m2]:
            if not os.path.isfile(path):
                print(f"[SKIP] {name}: model not found: {path}")
                break
        else:
            print(f"\n{'='*60}")
            print(f"  PAIR: {name}")
            print(f"  M1: {pair['m1_label']}")
            print(f"  M2: {pair['m2_label']}")
            print(f"  Combined: ~{pair['combined_gib']} GiB")
            print(f"{'='*60}")

            pair_results = {"pair": name, "m1": m1, "m2": m2,
                            "m1_label": pair["m1_label"], "m2_label": pair["m2_label"],
                            "combined_gib": pair["combined_gib"],
                            "serial_a": [], "serial_b": [], "parallel": []}

            for rep in range(N_RUNS):
                print(f"\n  [rep {rep+1}/{N_RUNS}]")

                # Serial A
                print(f"    serial A...", end=" ", flush=True)
                ra = run_llama(m1, f"{name}-ser-a-r{rep+1}", timeout=120)
                pair_results["serial_a"].append(ra)
                print(f"wall={ra['wall_s']:.1f}s gen={ra['gen_tok_s']}tok/s")
                time.sleep(5)

                # Serial B
                print(f"    serial B...", end=" ", flush=True)
                rb = run_llama(m2, f"{name}-ser-b-r{rep+1}", timeout=300)
                pair_results["serial_b"].append(rb)
                print(f"wall={rb['wall_s']:.1f}s gen={rb['gen_tok_s']}tok/s")
                time.sleep(10)

                # Parallel — start sampler
                sampler_pid = None
                sampler_csv = f"{LOG_DIR}/g8-power-{name}-r{rep+1}.csv"
                if sampler_available():
                    p = subprocess.Popen(
                        ["bash", SAMPLER, "--sample", sampler_csv, "--hz", "5", "--label", f"g8-{name}-r{rep+1}"],
                        stdout=open(f"{LOG_DIR}/g8-sampler-{name}-r{rep+1}.log", "w"),
                        stderr=subprocess.STDOUT
                    )
                    sampler_pid = p.pid
                    time.sleep(1)

                print(f"    parallel A+B...", end=" ", flush=True)
                par_timeout = max(int(rb["wall_s"] * 80) + 120, 2400)
                pa, pb = run_parallel(
                    m1, m2,
                    f"{name}-par-a-r{rep+1}",
                    f"{name}-par-b-r{rep+1}",
                    timeout=par_timeout,
                )

                if sampler_pid:
                    subprocess.run(["kill", "-TERM", str(sampler_pid)], capture_output=True)
                    time.sleep(1)

                par_rec = {"a": pa, "b": pb, "sampler_csv": sampler_csv if sampler_pid else None}
                pair_results["parallel"].append(par_rec)

                stall_a = round(pa["wall_s"] / ra["wall_s"], 1) if ra["wall_s"] > 0 else None
                stall_b = round(pb["wall_s"] / rb["wall_s"], 1) if rb["wall_s"] > 0 else None
                print(f"A={pa['wall_s']:.1f}s ({stall_a}×)  B={pb['wall_s']:.1f}s ({stall_b}×)")

                time.sleep(30)  # cooldown

            # Summary for this pair
            wall_a_serial = [r["wall_s"] for r in pair_results["serial_a"]]
            wall_b_serial = [r["wall_s"] for r in pair_results["serial_b"]]
            wall_a_par = [r["a"]["wall_s"] for r in pair_results["parallel"]]
            wall_b_par = [r["b"]["wall_s"] for r in pair_results["parallel"]]

            def stats(vals):
                if not vals:
                    return {}
                mean = sum(vals) / len(vals)
                return {"mean": round(mean, 2), "min": round(min(vals), 2),
                        "max": round(max(vals), 2), "n": len(vals)}

            # Throughput-based slowdown (decode tok/s), only across runs where
            # BOTH the serial and parallel side completed cleanly (ok==True).
            gen_a_ser = [r.get("gen_tok_s") for r in pair_results["serial_a"]]
            gen_b_ser = [r.get("gen_tok_s") for r in pair_results["serial_b"]]
            gen_a_par = [r["a"].get("gen_tok_s") for r in pair_results["parallel"]]
            gen_b_par = [r["b"].get("gen_tok_s") for r in pair_results["parallel"]]
            ok_a = [r.get("ok") for r in pair_results["serial_a"]]
            ok_b = [r.get("ok") for r in pair_results["serial_b"]]
            ok_pa = [r["a"].get("ok") for r in pair_results["parallel"]]
            ok_pb = [r["b"].get("ok") for r in pair_results["parallel"]]

            def gen_slow(gser, gpar, okser, okpar):
                # serial/parallel tok/s ratio (>1 = parallel is slower)
                out = []
                for gs, gp, os_, op in zip(gser, gpar, okser, okpar):
                    if os_ and op and gp and gp > 0:
                        out.append(gs / gp)
                return out

            pair_results["summary"] = {
                "serial_a_wall": stats(wall_a_serial),
                "serial_b_wall": stats(wall_b_serial),
                "parallel_a_wall": stats(wall_a_par),
                "parallel_b_wall": stats(wall_b_par),
                "serial_a_gen": stats([g for g in gen_a_ser if g]),
                "serial_b_gen": stats([g for g in gen_b_ser if g]),
                "parallel_a_gen": stats([g for g in gen_a_par if g]),
                "parallel_b_gen": stats([g for g in gen_b_par if g]),
                "gen_slow_a": stats(gen_slow(gen_a_ser, gen_a_par, ok_a, ok_pa)),
                "gen_slow_b": stats(gen_slow(gen_b_ser, gen_b_par, ok_b, ok_pb)),
                "ok_serial_a": sum(1 for x in ok_a if x), "ok_serial_b": sum(1 for x in ok_b if x),
                "ok_parallel_a": sum(1 for x in ok_pa if x), "ok_parallel_b": sum(1 for x in ok_pb if x),
                "killed": sum(1 for r in pair_results["serial_a"] + pair_results["serial_b"]
                              if r.get("rc", 0) < 0)
                         + sum(1 for r in pair_results["parallel"]
                               if r["a"].get("rc", 0) < 0 or r["b"].get("rc", 0) < 0),
                "stall_a": stats([pa/sa for pa, sa in zip(wall_a_par, wall_a_serial) if sa > 0]),
                "stall_b": stats([pb/sb for pb, sb in zip(wall_b_par, wall_b_serial) if sb > 0]),
            }
            print(f"\n  PAIR SUMMARY ({name}):")
            s = pair_results["summary"]
            print(f"    M1 serial gen:   {s['serial_a_gen']}")
            print(f"    M2 serial gen:   {s['serial_b_gen']}")
            print(f"    M1 parallel gen: {s['parallel_a_gen']}")
            print(f"    M2 parallel gen: {s['parallel_b_gen']}")
            print(f"    ok serial a/b = {s['ok_serial_a']}/{s['ok_serial_b']}, "
                  f"parallel a/b = {s['ok_parallel_a']}/{s['ok_parallel_b']}, killed={s['killed']}")
            def _fmt(st):
                return (f"{st['mean']:.2f}× (range {st['min']:.2f}–{st['max']:.2f}, n={st['n']})"
                        if st else "no clean runs")
            print(f"    Gen slowdown M1: {_fmt(s['gen_slow_a'])}")
            print(f"    Gen slowdown M2: {_fmt(s['gen_slow_b'])}")
            print(f"    Wall stall M1:   {_fmt(s['stall_a'])}   M2: {_fmt(s['stall_b'])}")

            all_results.append(pair_results)

    elapsed = time.time() - total_start
    output = {
        "results": all_results,
        "metadata": {
            "timestamp": datetime.datetime.now().isoformat(),
            "elapsed_min": round(elapsed / 60, 1),
            "n_runs": N_RUNS,
            "n_gen": N_GEN,
            "n_ctx": N_CTX,
        }
    }
    with open(OUT, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n[done] Results saved to {OUT}")
    print(f"Total time: {elapsed/60:.1f} minutes")


if __name__ == "__main__":
    main()
