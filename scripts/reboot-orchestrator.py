#!/usr/bin/env python3
"""reboot-orchestrator.py — Mac-side driver for the Phase-D rerun on BC-250.

For each (model, ctx-tier) cell:
  1. Reboot bc250 (skipped before the very first cell unless --reboot-first).
  2. Wait for SSH to come back.
  3. Verify 40 CU mask via cu_map.sh (expects 5 active WGPs on gfx1013).
  4. Verify /home has >= 30 GiB free.
  5. (If model.backend=="ollama") wait until http://127.0.0.1:11434/api/tags
     responds.
  6. Run one bench cell:
        python3 /tmp/bench-phase-c.py --step ctx-quality \\
            --only <model> --tiers <ctx> --runs 2 --merge
  7. Pull the merged step-2-ctx-quality.json back to Mac.
  8. Parse pass/fail. Decide next tier per --policy.

Per-model context search policy (--policy ceiling-bisect, default):
  - Confirm tiers: 4096, 16384, 32768 (each one cell, reboot between).
  - Then doubling from the highest passing tier (cap = model native ctx),
    e.g. 32K -> 64K -> 128K -> 256K. Stop on the first failure F.
  - Bisect [last_pass, F] to within --bisect-step (default 4096) tokens.

Resumable: writes its own state JSON to ~/projects/bc250/benchmarks/
results-phase-c/40cu-rerun/orchestrator-state.json so re-running the
script continues where it left off.

This script never touches credentials. It relies on:
  - ssh alias `bc250` working (key auth, no password).
  - passwordless sudo on bc250 for `reboot` (already configured).
"""
from __future__ import annotations
import argparse, json, os, pathlib, shlex, subprocess, sys, time
from dataclasses import dataclass, field, asdict
from typing import Optional

# ---------- config ----------
HOST = "bc250"
REMOTE_HOME = "/home/akandr"  # used only for log strings
REMOTE_BENCH = "/tmp/bench-phase-c.py"
REMOTE_RESULTS = "/home/akandr/phase-c-out/results/step-2-ctx-quality.json"
REMOTE_LOG_DIR = "/home/akandr/phase-c-out/logs"
LOCAL_REPO = pathlib.Path(__file__).resolve().parents[1]
LOCAL_BENCH_SRC = LOCAL_REPO / "benchmarks" / "bench-phase-c.py"
LOCAL_OUT_DIR = LOCAL_REPO / "benchmarks" / "results-phase-c" / "40cu-rerun"
LOCAL_RESULTS = LOCAL_OUT_DIR / "step-2-ctx-quality.json"
LOCAL_STATE = LOCAL_OUT_DIR / "orchestrator-state.json"
LOCAL_LOG = LOCAL_OUT_DIR / "orchestrator.log"

# Per-model native max context (used as doubling cap).
MODEL_NATIVE_CTX = {
    "qwen3.5-9b-q4km":      131072,
    "qwen3.5-9b-ollama":    131072,
    "gemma4-latest":        262144,
    "deepseek-r1-14b":      131072,
    "gpt-oss-20b-mxfp4":    131072,
    # gemma4-26b-q3: 12h diag (2026-05-27) found hard cliff between ctx=2048
    # and ctx=4096 on this card (16-25 tok/s at <=2K, runner timeout at >=4K).
    # Capped to 2048 so the ceiling-bisect loop short-circuits and we do not
    # burn cell-timeouts on the wedge. See notes/next-measurements-plan.md M1.
    "gemma4-26b-q3":          2048,
    "qwen3-coder-30b-iq2m": 262144,
    "qwen3.5-35b-iq2m":     262144,
    "qwen3.6-35b-iq2m":     262144,
    "granite-4.0-h-tiny":   131072,
}
OLLAMA_MODELS = {"qwen3.5-9b-ollama", "gemma4-latest", "gemma4-26b-q3"}

CONFIRM_TIERS = [4096, 16384, 32768]
DOUBLING_FROM = 32768  # start doubling after this many tokens

# Per-cell timeout headroom (orchestrator-side). bench script imposes its own
# per-call timeout; this is the outer ssh-command timeout.
# 90 min covers 128K cells on slow models with swap pressure; override via
# --cell-timeout for the retry pass.
CELL_SSH_TIMEOUT_S = 60 * 90   # 90 min per cell (default; --cell-timeout overrides)
REBOOT_WAIT_S = 25 * 60        # 25 min upper bound to come back
PING_INTERVAL_S = 10

# ---------- helpers ----------

def log(msg: str):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOCAL_OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCAL_LOG, "a") as f:
        f.write(line + "\n")

def sh(cmd: list[str], timeout: Optional[int]=None, check: bool=False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, check=check)

def sh_safe(cmd: list[str], timeout: Optional[int]=None) -> Optional[subprocess.CompletedProcess]:
    """Like sh() but swallows TimeoutExpired/OSError, returning None on failure."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        log(f"  sh_safe TIMEOUT (>{timeout}s): {' '.join(cmd[:3])}...")
        return None
    except (OSError, ValueError) as e:
        log(f"  sh_safe EXC ({type(e).__name__}): {e}")
        return None

def ssh(remote_cmd: str, timeout: int=60) -> subprocess.CompletedProcess:
    return sh(["ssh", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15",
               HOST, remote_cmd], timeout=timeout)

def ssh_safe(remote_cmd: str, timeout: int=60) -> Optional[subprocess.CompletedProcess]:
    return sh_safe(["ssh", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15",
                    HOST, remote_cmd], timeout=timeout)

def scp_to(local: pathlib.Path, remote: str, timeout: int=120) -> bool:
    cp = sh_safe(["scp", "-q", "-o", "ConnectTimeout=10",
                  str(local), f"{HOST}:{remote}"], timeout=timeout)
    return bool(cp and cp.returncode == 0)

def scp_from(remote: str, local: pathlib.Path, timeout: int=120) -> bool:
    local.parent.mkdir(parents=True, exist_ok=True)
    cp = sh_safe(["scp", "-q", "-o", "ConnectTimeout=10",
                  f"{HOST}:{remote}", str(local)], timeout=timeout)
    return bool(cp and cp.returncode == 0)

def wait_for_ssh(max_wait_s: int=REBOOT_WAIT_S) -> bool:
    deadline = time.time() + max_wait_s
    while time.time() < deadline:
        cp = sh(["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
                 HOST, "true"], timeout=15)
        if cp.returncode == 0:
            return True
        time.sleep(PING_INTERVAL_S)
    return False

def verify_40cu() -> tuple[bool, str]:
    """Reads /sys/.../gpu_metrics or runs cu_map.sh. We use a simpler check:
    parse /sys/class/drm/card*/device/pp_dpm_sclk presence + dmesg/sysfs CU
    count. cu_map.sh prints `WGPs active: N/5`; we look for `5/5`.
    """
    cp = ssh("bash ~/bc250-40cu-unlock/scripts/cu_map.sh 2>&1", timeout=30)
    out = (cp.stdout or "") + (cp.stderr or "")
    if "40/40 CUs active" in out:
        return True, "40/40 CUs active"
    return False, out[-400:]

def verify_disk_space(min_gib: int=30) -> tuple[bool, str]:
    cp = ssh("df -BG /home | tail -1", timeout=15)
    if cp.returncode != 0:
        return False, "df failed"
    parts = cp.stdout.split()
    if len(parts) < 4:
        return False, cp.stdout.strip()
    avail = int(parts[3].rstrip("G"))
    return avail >= min_gib, f"avail={avail}G"

def wait_for_ollama(max_wait_s: int=120) -> bool:
    deadline = time.time() + max_wait_s
    while time.time() < deadline:
        cp = ssh("curl -fsS -m 5 http://127.0.0.1:11434/api/tags >/dev/null && echo ok", timeout=15)
        if cp.returncode == 0 and "ok" in cp.stdout:
            return True
        time.sleep(5)
    return False

def hard_reset_bc250() -> bool:
    """Power-cycle the BC-250 via Home Assistant (`bc250_reset` on Mac).
    Returns True if the helper executed; the caller still needs to
    wait_for_ssh() afterwards."""
    log("HARD RESET via bc250_reset (Home Assistant power cycle) ...")
    try:
        cp = sh(["bc250_reset"], timeout=30)
        log(f"  bc250_reset rc={cp.returncode} stdout={(cp.stdout or '').strip()[:200]}")
        return cp.returncode == 0
    except Exception as e:
        log(f"  bc250_reset FAILED: {type(e).__name__}: {e}")
        return False

def reboot_bc250():
    log("rebooting bc250 (soft via ssh) ...")
    try:
        ssh("sudo -n systemctl stop ollama 2>/dev/null; sync; sudo -n /sbin/reboot", timeout=20)
    except Exception as e:
        log(f"  soft reboot ssh failed: {type(e).__name__}: {e}")
    time.sleep(15)  # let it actually go down

def ensure_bc250_up(max_soft_wait_s: int=180) -> bool:
    """Wait for SSH; if not back within `max_soft_wait_s`, hard-reset and
    wait again up to REBOOT_WAIT_S."""
    if wait_for_ssh(max_wait_s=max_soft_wait_s):
        return True
    log(f"  soft wait exceeded {max_soft_wait_s}s -- escalating to hard reset")
    hard_reset_bc250()
    time.sleep(20)
    return wait_for_ssh(max_wait_s=REBOOT_WAIT_S)

def push_bench_script() -> bool:
    log(f"pushing bench script to {REMOTE_BENCH}")
    return scp_to(LOCAL_BENCH_SRC, REMOTE_BENCH)

def run_cell(model: str, ctx: int, runs: int, n_gen: int) -> tuple[bool, dict]:
    """Run a single bench cell on bc250 and pull the merged JSON back.

    Returns (cell_ok, tier_record) where tier_record is the per-tier dict from
    the JSON (or {} if unavailable). cell_ok considers both runs-status and
    needle pass: cell is OK only if at least 1 needle-passing run.
    """
    ssh(f"mkdir -p {REMOTE_LOG_DIR}", timeout=15)
    log_remote = f"{REMOTE_LOG_DIR}/cell-{model}-{ctx}-{int(time.time())}.log"
    cmd = (f"python3 {REMOTE_BENCH} --step ctx-quality --only {shlex.quote(model)} "
           f"--tiers {ctx} --runs {runs} --n-gen {n_gen} --merge "
           f"> {log_remote} 2>&1; echo CELLRC=$?; tail -40 {log_remote}")
    log(f"  running cell: model={model} ctx={ctx} runs={runs} n_gen={n_gen}")
    t0 = time.time()
    try:
        cp = ssh(cmd, timeout=CELL_SSH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        dur = time.time() - t0
        log(f"  cell wall={dur:.0f}s TIMEOUT (>{CELL_SSH_TIMEOUT_S}s)")
        # Best effort: try to pull whatever the bench wrote before timeout.
        scp_from(REMOTE_RESULTS, LOCAL_RESULTS)
        return False, {"_timeout": True, "wall_s": dur}
    dur = time.time() - t0
    log(f"  cell wall={dur:.0f}s rc={cp.returncode}")
    if cp.stdout:
        for line in cp.stdout.strip().splitlines()[-20:]:
            log(f"    | {line}")
    # Pull JSON regardless of rc.
    if not scp_from(REMOTE_RESULTS, LOCAL_RESULTS):
        log(f"  WARN: could not pull {REMOTE_RESULTS}")
        return False, {}
    try:
        data = json.loads(LOCAL_RESULTS.read_text())
    except Exception as e:
        log(f"  WARN: bad JSON: {e}")
        return False, {}
    tier = data.get("results", {}).get(model, {}).get("tiers", {}).get(str(ctx), {})
    runs_list = tier.get("runs", [])
    n_ok = sum(1 for r in runs_list if r.get("status") == "ok")
    n_pass = sum(1 for r in runs_list if r.get("needle_pass"))
    log(f"  tier[{ctx}] runs={len(runs_list)} status_ok={n_ok} needle_pass={n_pass}")
    return (n_pass >= 1), tier

# ---------- state ----------

@dataclass
class ModelState:
    name: str
    confirmed: dict = field(default_factory=dict)   # ctx (str) -> bool pass
    doubling_results: dict = field(default_factory=dict)  # ctx (str) -> bool
    bisect_low: Optional[int] = None
    bisect_high: Optional[int] = None
    ceiling: Optional[int] = None
    done: bool = False

@dataclass
class State:
    models: dict = field(default_factory=dict)
    cell_count: int = 0
    first_cell: bool = True

    def save(self):
        LOCAL_STATE.parent.mkdir(parents=True, exist_ok=True)
        LOCAL_STATE.write_text(json.dumps(asdict(self), indent=2, default=str))

    @classmethod
    def load(cls) -> "State":
        if LOCAL_STATE.exists():
            d = json.loads(LOCAL_STATE.read_text())
            st = cls()
            st.cell_count = d.get("cell_count", 0)
            st.first_cell = d.get("first_cell", True)
            for n, md in (d.get("models") or {}).items():
                st.models[n] = ModelState(**md)
            return st
        return cls()

# ---------- preflight ----------

def preflight(skip_cu_check: bool=False) -> bool:
    log("preflight: waiting for SSH ...")
    if not ensure_bc250_up():
        log("FATAL: SSH not reachable even after hard reset")
        return False
    if not skip_cu_check:
        ok, info = verify_40cu()
        log(f"preflight: 40 CU check: {'OK' if ok else 'FAIL'}  ({info[:120]})")
        if not ok:
            log("FATAL: 40 CU not active. Aborting.")
            return False
    ok, info = verify_disk_space()
    log(f"preflight: disk space: {'OK' if ok else 'FAIL'}  ({info})")
    if not ok:
        log("FATAL: insufficient /home space")
        return False
    if not wait_for_ollama():
        log("FATAL: ollama not responding")
        return False
    log("preflight: OK")
    return True

# ---------- main loop ----------

def next_doubling_target(passed: list[int], native_cap: int) -> Optional[int]:
    """Given the sorted ascending list of ctx values that PASSED, propose the
    next doubling target above the highest pass. None if we've hit native cap.
    """
    if not passed:
        return DOUBLING_FROM
    hi = max(passed)
    if hi >= native_cap:
        return None
    target = max(hi * 2, DOUBLING_FROM)
    return min(target, native_cap)

def run_model_loop(state: State, ms: ModelState, args) -> None:
    name = ms.name
    native_cap = MODEL_NATIVE_CTX.get(name, 65536)
    log(f"\n========== MODEL: {name}  native_cap={native_cap} ==========")

    # Phase 1: confirm tiers
    for ctx in CONFIRM_TIERS:
        if ctx > native_cap:
            log(f"  confirm[{ctx}]: above native_cap {native_cap}, skipping")
            continue
        if str(ctx) in ms.confirmed:
            log(f"  confirm[{ctx}]: already done = {ms.confirmed[str(ctx)]}")
            continue
        ok = run_one_cell(state, ms, ctx, args)
        ms.confirmed[str(ctx)] = ok
        state.save()
        if not ok:
            log(f"  confirm[{ctx}] FAILED -- skipping ceiling search for {name}")
            ms.ceiling = (max([int(k) for k, v in ms.confirmed.items() if v] or [0])) or None
            ms.done = True
            state.save()
            return

    # Phase 2: doubling
    while True:
        passed_so_far = ([int(k) for k, v in ms.confirmed.items() if v]
                         + [int(k) for k, v in ms.doubling_results.items() if v])
        target = next_doubling_target(sorted(set(passed_so_far)), native_cap)
        if target is None:
            # reached native cap and last test passed
            ms.ceiling = native_cap
            log(f"  doubling reached native cap {native_cap}; ceiling = {native_cap}")
            break
        if str(target) in ms.doubling_results:
            log(f"  doubling[{target}]: already done = {ms.doubling_results[str(target)]}")
            if not ms.doubling_results[str(target)]:
                break  # need to bisect
            continue
        ok = run_one_cell(state, ms, target, args)
        ms.doubling_results[str(target)] = ok
        state.save()
        if not ok:
            break

    # Phase 3: bisect between last_pass and first_fail (if any failure)
    fails = [int(k) for k, v in ms.doubling_results.items() if not v]
    passes = ([int(k) for k, v in ms.confirmed.items() if v]
              + [int(k) for k, v in ms.doubling_results.items() if v])
    if fails and passes:
        lo = max(p for p in passes if p < min(fails))
        hi = min(fails)
        log(f"  bisect: [{lo} .. {hi}] step={args.bisect_step}")
        while hi - lo > args.bisect_step:
            mid = ((lo + hi) // 2 // args.bisect_step) * args.bisect_step
            if mid <= lo:
                break
            if str(mid) in ms.doubling_results:
                ok = ms.doubling_results[str(mid)]
            else:
                ok = run_one_cell(state, ms, mid, args)
                ms.doubling_results[str(mid)] = ok
                state.save()
            if ok:
                lo = mid
            else:
                hi = mid
        ms.ceiling = lo
    elif passes and not fails:
        ms.ceiling = max(passes)

    ms.done = True
    state.save()
    log(f"  CEILING for {name}: {ms.ceiling}")

def run_one_cell(state: State, ms: ModelState, ctx: int, args) -> bool:
    """Reboot (unless first cell or --no-reboot), preflight, run cell."""
    if not state.first_cell and not args.no_reboot:
        reboot_bc250()
    state.first_cell = False
    state.save()
    if not preflight(skip_cu_check=args.skip_cu_check):
        log("preflight failed; pausing 60s then retrying once")
        time.sleep(60)
        if not preflight(skip_cu_check=args.skip_cu_check):
            log("preflight failed twice; recording cell as FAIL")
            return False
    if not push_bench_script():
        log("WARN: failed to push bench script (using existing remote copy)")
    state.cell_count += 1
    state.save()
    ok, _tier = run_cell(ms.name, ctx, args.runs, args.n_gen)
    return ok

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=",".join(MODEL_NATIVE_CTX),
                    help="comma-separated model names from MODELS_TO_RUN")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--n-gen", type=int, default=200)
    ap.add_argument("--bisect-step", type=int, default=4096)
    ap.add_argument("--cell-timeout", type=int, default=None,
                    help="override per-cell SSH timeout in seconds (default 5400)")
    ap.add_argument("--reboot-first", action="store_true",
                    help="reboot even before the very first cell")
    ap.add_argument("--no-reboot", action="store_true",
                    help="DEBUG: skip reboots between cells")
    ap.add_argument("--skip-cu-check", action="store_true",
                    help="DEBUG: do not run cu_map.sh (e.g. if not vendored on box)")
    ap.add_argument("--reset", action="store_true",
                    help="DANGER: wipe orchestrator state and start fresh")
    args = ap.parse_args()

    if args.cell_timeout:
        global CELL_SSH_TIMEOUT_S
        CELL_SSH_TIMEOUT_S = args.cell_timeout

    if args.reset and LOCAL_STATE.exists():
        LOCAL_STATE.unlink()
        log("orchestrator state reset")

    state = State.load()
    if args.reboot_first:
        state.first_cell = False

    models_to_run = [m.strip() for m in args.models.split(",") if m.strip()]
    log(f"orchestrator start: {len(models_to_run)} model(s)  runs={args.runs}  n_gen={args.n_gen}")
    log(f"models: {models_to_run}")

    for name in models_to_run:
        if name not in MODEL_NATIVE_CTX:
            log(f"SKIP: unknown model {name}")
            continue
        ms = state.models.get(name) or ModelState(name=name)
        state.models[name] = ms
        if ms.done:
            log(f"skip {name}: already done (ceiling={ms.ceiling})")
            continue
        try:
            run_model_loop(state, ms, args)
        except KeyboardInterrupt:
            log("interrupted by user; state saved")
            state.save()
            sys.exit(130)
        except Exception as e:
            log(f"EXC during {name}: {type(e).__name__}: {e}")
            state.save()
            # continue to next model

    log("\n========== SUMMARY ==========")
    for name, ms in state.models.items():
        log(f"  {name:22s}  ceiling={ms.ceiling}  confirmed={ms.confirmed}  doubling={ms.doubling_results}")

if __name__ == "__main__":
    main()
