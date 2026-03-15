#!/usr/bin/env python3
"""Test extreme context limits on BC-250 with KV cache quantization.
Previous tests showed Q4_0 and Q8_0 work on Vulkan with zero penalty up to 48K.
Time to find the ACTUAL ceiling.

With ~14.5 GiB usable VRAM (16 GiB UMA - system):
- Model: ~8.5 GiB
- Available for KV: ~6 GiB
- Q4_0: ~0.046 GiB/K → theoretical max ~130K
- Q8_0: ~0.083 GiB/K → theoretical max ~72K

Run on BC-250: python3 -u /opt/netscan/tmp/test-extreme-context.py
"""
import json
import time
import urllib.request
import subprocess
import sys

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

OLLAMA_URL = "http://localhost:11434"
MODEL = "qwen3:14b"


def ollama_generate(model, prompt, num_ctx, timeout=600, num_predict=50):
    """Send generate request and return timing data."""
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"num_ctx": num_ctx, "num_predict": num_predict}
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate", payload,
        headers={"Content-Type": "application/json"}
    )
    start = time.time()
    resp = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    wall = time.time() - start

    pc = resp.get("prompt_eval_count", 0)
    pd = resp.get("prompt_eval_duration", 1) / 1e9
    ec = resp.get("eval_count", 0)
    ed = resp.get("eval_duration", 1) / 1e9
    td = resp.get("total_duration", 0) / 1e9
    load = resp.get("load_duration", 0) / 1e9

    return {
        "prompt_tokens": pc,
        "prompt_eval_s": pd,
        "prompt_tok_s": pc / pd if pd > 0 else 0,
        "gen_tokens": ec,
        "gen_eval_s": ed,
        "gen_tok_s": ec / ed if ed > 0 else 0,
        "total_s": td,
        "load_s": load,
        "wall_s": wall,
        "response": resp.get("response", "")[:200],
    }


def ollama_unload(model):
    """Unload model from VRAM."""
    payload = json.dumps({"model": model, "keep_alive": 0}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate", payload,
        headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req, timeout=30)
    except Exception:
        pass


def wait_for_ollama(max_wait=60):
    """Wait for Ollama to become responsive after restart."""
    for _ in range(max_wait // 2):
        try:
            urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=5)
            return True
        except Exception:
            time.sleep(2)
    return False


def set_kv_cache_type(kv_type):
    """Set KV cache type in Ollama systemd override and restart."""
    if kv_type is None:
        # Remove KV cache type setting
        subprocess.run([
            "sudo", "bash", "-c",
            "sed -i '/OLLAMA_KV_CACHE_TYPE/d' /etc/systemd/system/ollama.service.d/override.conf"
        ], check=True)
    else:
        subprocess.run([
            "sudo", "bash", "-c",
            f'grep -q KV_CACHE_TYPE /etc/systemd/system/ollama.service.d/override.conf '
            f'&& sed -i "s/OLLAMA_KV_CACHE_TYPE=.*/OLLAMA_KV_CACHE_TYPE={kv_type}/" '
            f'/etc/systemd/system/ollama.service.d/override.conf '
            f'|| sed -i "/OLLAMA_CONTEXT_LENGTH/a Environment=OLLAMA_KV_CACHE_TYPE={kv_type}" '
            f'/etc/systemd/system/ollama.service.d/override.conf'
        ], check=True)
    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
    subprocess.run(["sudo", "systemctl", "restart", "ollama"], check=True)
    time.sleep(5)
    if not wait_for_ollama():
        print("  WARNING: Ollama did not become responsive in time!")
        return False
    return True


def generate_filler(target_tokens):
    """Generate text that fills approximately target_tokens tokens of context."""
    # ~1.3 tokens per word, add some margin
    words = int(target_tokens / 1.3)
    base = ("The embedded Linux kernel driver subsystem provides a framework "
            "for hardware abstraction. Camera sensor drivers implement the V4L2 "
            "subdevice interface, handling power management, format negotiation, "
            "and streaming control. The ISP pipeline processes raw Bayer data "
            "through demosaicing, white balance, and color correction stages. "
            "Memory mapping allows zero-copy buffer sharing between kernel space "
            "and userland applications. DMA engines transfer data efficiently "
            "without CPU intervention. The scheduler manages IRQ handling and "
            "workqueue processing to maintain real-time deadlines. ")
    repeated = (base * (words // 50 + 1))
    text = " ".join(repeated.split()[:words])
    return f"Summarize the following technical document in exactly 2 sentences:\n\n{text}"


def get_vram_info():
    """Get VRAM usage from Ollama logs."""
    log = subprocess.run(
        ["sudo", "journalctl", "-u", "ollama", "-n", "50", "--no-pager"],
        capture_output=True, text=True
    )
    vram_lines = []
    for line in log.stdout.splitlines():
        low = line.lower()
        if any(k in low for k in ["total=", "available=", "kv cache", "kv self", "model size"]):
            vram_lines.append(line.strip()[-130:])
    return vram_lines


def print_result(label, r):
    print(f"  {label}:")
    print(f"    Prompt: {r['prompt_tokens']} tok in {r['prompt_eval_s']:.2f}s = {r['prompt_tok_s']:.1f} tok/s")
    print(f"    Gen:    {r['gen_tokens']} tok in {r['gen_eval_s']:.2f}s = {r['gen_tok_s']:.1f} tok/s")
    print(f"    Load: {r['load_s']:.1f}s  Total: {r['total_s']:.1f}s  Wall: {r['wall_s']:.1f}s")
    resp_preview = r['response'].replace('\n', ' ')[:100]
    print(f"    Response: {resp_preview}...")


def test_context_size(kv_type, ctx_size, prompt, timeout=600):
    """Test a specific context size. Returns result dict or None on failure."""
    label = f"{kv_type} @ {ctx_size//1024}K"
    print(f"\n  Testing {label}...")
    try:
        r = ollama_generate(MODEL, prompt, num_ctx=ctx_size, timeout=timeout)
        print_result(label, r)
        # Get VRAM info
        vram = get_vram_info()
        for v in vram[-5:]:
            print(f"    log: {v}")
        return r
    except Exception as e:
        print(f"    FAILED {label}: {e}")
        return None


def main():
    print("=" * 70)
    print("EXTREME CONTEXT LIMIT TEST — Finding the ceiling")
    print("=" * 70)
    print(f"Model: {MODEL}")
    print(f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # Simple prompt for initial tests (just checks if context SIZE is allocatable)
    simple_prompt = "Explain the theory of relativity in 3 sentences."

    # Results collector
    results = []

    # =========================================================================
    # PHASE 1: Q4_0 — Push to the absolute maximum
    # =========================================================================
    print("=" * 70)
    print("PHASE 1: Q4_0 KV Cache — Finding the ceiling")
    print("  Previous: 48K confirmed at 27.3 tok/s with 1.1 GiB KV")
    print("  Theory: ~130K max before OOM")
    print("=" * 70)

    if not set_kv_cache_type("q4_0"):
        print("FATAL: Could not set q4_0")
        return

    # Test sizes from 48K upward
    q4_sizes = [49152, 65536, 81920, 98304, 114688, 131072]
    q4_labels = ["48K", "64K", "80K", "96K", "112K", "128K"]

    for ctx, label in zip(q4_sizes, q4_labels):
        print(f"\n{'─' * 50}")
        print(f"  Q4_0 @ {label} (num_ctx={ctx})")
        print(f"{'─' * 50}")

        # Unload model first to force fresh allocation at new context size
        ollama_unload(MODEL)
        time.sleep(2)

        r = test_context_size("q4_0", ctx, simple_prompt, timeout=600)
        if r is None:
            print(f"\n  ★ Q4_0 ceiling found: FAILS at {label}")
            results.append(("q4_0", label, ctx, None))
            break
        else:
            results.append(("q4_0", label, ctx, r))
            # If gen speed dropped below 10 tok/s, note it but continue
            if r['gen_tok_s'] < 10:
                print(f"\n  ⚠ Gen speed critically low ({r['gen_tok_s']:.1f} tok/s) at {label}")

    # =========================================================================
    # PHASE 2: Q8_0 — More conservative, find its ceiling
    # =========================================================================
    print()
    print("=" * 70)
    print("PHASE 2: Q8_0 KV Cache — Finding the ceiling")
    print("  Previous: 48K confirmed at 27.2 tok/s with 2.0 GiB KV")
    print("  Theory: ~72K max before OOM")
    print("=" * 70)

    if not set_kv_cache_type("q8_0"):
        print("FATAL: Could not set q8_0")
        return

    q8_sizes = [49152, 65536, 81920, 98304]
    q8_labels = ["48K", "64K", "80K", "96K"]

    for ctx, label in zip(q8_sizes, q8_labels):
        print(f"\n{'─' * 50}")
        print(f"  Q8_0 @ {label} (num_ctx={ctx})")
        print(f"{'─' * 50}")

        ollama_unload(MODEL)
        time.sleep(2)

        r = test_context_size("q8_0", ctx, simple_prompt, timeout=600)
        if r is None:
            print(f"\n  ★ Q8_0 ceiling found: FAILS at {label}")
            results.append(("q8_0", label, ctx, None))
            break
        else:
            results.append(("q8_0", label, ctx, r))

    # =========================================================================
    # PHASE 3: Stress test — Fill the biggest working context with real data
    # =========================================================================
    print()
    print("=" * 70)
    print("PHASE 3: Stress test — Fill largest working Q4_0 context with real data")
    print("=" * 70)

    # Find the largest working Q4_0 context
    max_q4 = None
    for kv, label, ctx, r in reversed(results):
        if kv == "q4_0" and r is not None:
            max_q4 = (label, ctx)
            break

    if max_q4:
        label, ctx = max_q4
        print(f"\n  Stress testing Q4_0 @ {label} with ~{ctx*3//4//1024}K tokens of prompt")

        if not set_kv_cache_type("q4_0"):
            print("FATAL: Could not set q4_0 for stress test")
        else:
            ollama_unload(MODEL)
            time.sleep(2)

            # Fill ~75% of context with prompt data
            fill_tokens = ctx * 3 // 4
            big_prompt = generate_filler(fill_tokens)
            print(f"  Generated prompt (~{fill_tokens} target tokens)...")

            r = test_context_size("q4_0", ctx, big_prompt, timeout=900)
            if r:
                results.append(("q4_0-stress", f"{label}-filled", ctx, r))
                print(f"\n  ★ Successfully processed ~{r['prompt_tokens']} tokens in {label} context!")
            else:
                print(f"\n  ✗ Stress test FAILED at {label}")
    else:
        print("  No working Q4_0 results to stress test")

    # =========================================================================
    # CLEANUP: Restore production config
    # =========================================================================
    print()
    print("=" * 70)
    print("CLEANUP: Restoring FP16 KV cache (production default)")
    print("=" * 70)
    set_kv_cache_type(None)
    print("  Done. Ollama restored to FP16 KV cache.")

    # =========================================================================
    # SUMMARY
    # =========================================================================
    print()
    print("=" * 70)
    print("SUMMARY — Extreme Context Test Results")
    print("=" * 70)
    print(f"{'KV Type':<12} {'Context':<10} {'Gen tok/s':<12} {'Prefill tok/s':<15} {'Load (s)':<10} {'Status'}")
    print("─" * 70)
    for kv, label, ctx, r in results:
        if r is None:
            print(f"{kv:<12} {label:<10} {'—':<12} {'—':<15} {'—':<10} FAILED/OOM")
        else:
            print(f"{kv:<12} {label:<10} {r['gen_tok_s']:<12.1f} {r['prompt_tok_s']:<15.1f} {r['load_s']:<10.1f} OK")

    print()
    print("Done!")


if __name__ == "__main__":
    main()
