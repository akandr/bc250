#!/usr/bin/env python3
"""Test KV cache quantization and prefill rates on BC-250.
Run on BC-250: python3 /opt/netscan/tmp/test-kv-cache.py
"""
import json
import time
import urllib.request
import sys

# Force unbuffered output
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

OLLAMA_URL = "http://localhost:11434"

def ollama_generate(model, prompt, num_ctx=24576, timeout=300):
    """Send generate request and return timing data."""
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"num_ctx": num_ctx, "num_predict": 50}
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
        "response": resp.get("response", "")[:150],
    }


def ollama_unload(model):
    """Unload model from VRAM."""
    payload = json.dumps({
        "model": model, "keep_alive": 0
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate", payload,
        headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req, timeout=30)
    except Exception:
        pass


def print_result(label, r):
    print(f"  {label}:")
    print(f"    Prompt: {r['prompt_tokens']} tok in {r['prompt_eval_s']:.2f}s = {r['prompt_tok_s']:.1f} tok/s")
    print(f"    Gen:    {r['gen_tokens']} tok in {r['gen_eval_s']:.2f}s = {r['gen_tok_s']:.1f} tok/s")
    print(f"    Load: {r['load_s']:.1f}s  Total: {r['total_s']:.1f}s  Wall: {r['wall_s']:.1f}s")
    print(f"    Response: {r['response'][:80]}...")


def generate_long_prompt(token_count):
    """Generate a prompt of approximately token_count tokens."""
    # ~1.3 tokens per word in English
    words_needed = int(token_count / 1.3)
    # Use a repeating technical paragraph to get predictable tokenization
    base = ("The embedded Linux kernel driver subsystem provides a framework "
            "for hardware abstraction. Camera sensor drivers implement the V4L2 "
            "subdevice interface, handling power management, format negotiation, "
            "and streaming control. The ISP pipeline processes raw Bayer data "
            "through demosaicing, white balance, and color correction stages. ")
    # Repeat base text to reach target
    repeated = (base * (words_needed // 30 + 1))[:words_needed * 5]
    return f"Summarize the following technical document in 2 sentences:\n\n{repeated}"


def main():
    model = "qwen3:14b"

    # =========================================================================
    # EXPERIMENT 1: KV Cache Quantization
    # =========================================================================
    print("=" * 70)
    print("EXPERIMENT 1: KV Cache Quantization Test")
    print("=" * 70)
    print()

    # First check current KV cache type
    print("Checking Ollama environment...")
    import subprocess
    env_out = subprocess.run(
        ["systemctl", "show", "ollama", "--property=Environment"],
        capture_output=True, text=True
    )
    print(f"  Ollama env: {env_out.stdout.strip()[:200]}")
    print()

    # Baseline: default FP16 KV cache at 24K
    print("[1a] Baseline: FP16 KV cache, 24K context")
    r = ollama_generate(model, "Explain the theory of relativity in 3 sentences.", num_ctx=24576)
    print_result("FP16 @ 24K", r)
    baseline_gen = r['gen_tok_s']
    print()

    # Now test with KV cache quantization
    # We need to set OLLAMA_KV_CACHE_TYPE and restart
    for kv_type in ["q8_0", "q4_0"]:
        print(f"\n[1b] Testing KV cache type: {kv_type}")
        print(f"  Setting OLLAMA_KV_CACHE_TYPE={kv_type} and restarting Ollama...")

        # Add KV cache type to systemd override
        subprocess.run([
            "sudo", "bash", "-c",
            f'grep -q KV_CACHE_TYPE /etc/systemd/system/ollama.service.d/override.conf '
            f'&& sudo sed -i "s/OLLAMA_KV_CACHE_TYPE=.*/OLLAMA_KV_CACHE_TYPE={kv_type}/" '
            f'/etc/systemd/system/ollama.service.d/override.conf '
            f'|| sudo sed -i "/OLLAMA_CONTEXT_LENGTH/a Environment=OLLAMA_KV_CACHE_TYPE={kv_type}" '
            f'/etc/systemd/system/ollama.service.d/override.conf'
        ], check=True)
        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
        subprocess.run(["sudo", "systemctl", "restart", "ollama"], check=True)
        time.sleep(5)

        # Wait for Ollama to be ready
        for i in range(30):
            try:
                urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=5)
                break
            except Exception:
                time.sleep(2)

        # Test at same 24K context
        print(f"  Testing {kv_type} @ 24K context...")
        try:
            r = ollama_generate(model, "Explain the theory of relativity in 3 sentences.", num_ctx=24576)
            print_result(f"{kv_type} @ 24K", r)
            delta = r['gen_tok_s'] - baseline_gen
            print(f"    Delta vs FP16: {delta:+.1f} tok/s ({delta/baseline_gen*100:+.1f}%)")
        except Exception as e:
            print(f"    FAILED at 24K: {e}")
            continue

        # Check Ollama logs for KV cache info
        log_out = subprocess.run(
            ["sudo", "journalctl", "-u", "ollama", "-n", "30", "--no-pager"],
            capture_output=True, text=True
        )
        for line in log_out.stdout.splitlines():
            if any(k in line.lower() for k in ["kv", "cache", "quant", "total=", "available="]):
                print(f"    log: {line.strip()[-120:]}")

        # If 24K worked, try 32K
        print(f"\n  Testing {kv_type} @ 32K context...")
        try:
            r = ollama_generate(model, "Explain the theory of relativity in 3 sentences.", num_ctx=32768)
            print_result(f"{kv_type} @ 32K", r)
        except Exception as e:
            print(f"    FAILED at 32K: {e}")

        # If 32K worked, try 48K
        print(f"\n  Testing {kv_type} @ 48K context...")
        try:
            r = ollama_generate(model, "Explain the theory of relativity in 3 sentences.", num_ctx=49152)
            print_result(f"{kv_type} @ 48K", r)
        except Exception as e:
            print(f"    FAILED at 48K: {e}")

        print()

    # Restore FP16 (remove KV_CACHE_TYPE line)
    print("[1c] Restoring FP16 KV cache (removing OLLAMA_KV_CACHE_TYPE)...")
    subprocess.run([
        "sudo", "bash", "-c",
        "sudo sed -i '/OLLAMA_KV_CACHE_TYPE/d' /etc/systemd/system/ollama.service.d/override.conf"
    ], check=True)
    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
    subprocess.run(["sudo", "systemctl", "restart", "ollama"], check=True)
    time.sleep(5)
    for i in range(30):
        try:
            urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=5)
            break
        except Exception:
            time.sleep(2)

    # =========================================================================
    # EXPERIMENT 2: Prefill Rate / TTFT Measurement
    # =========================================================================
    print()
    print("=" * 70)
    print("EXPERIMENT 2: Prefill Rate / TTFT Measurement")
    print("=" * 70)
    print()

    prompt_sizes = [
        ("tiny",    100),
        ("short",   500),
        ("medium",  2000),
        ("long",    5000),
        ("xlarge",  10000),
        ("massive", 15000),
    ]

    print(f"{'Label':>10} | {'Target':>6} | {'Actual':>6} | {'Prefill':>10} | {'TTFT':>8} | {'Gen':>8} | {'Total':>8}")
    print("-" * 80)

    for label, target_tokens in prompt_sizes:
        prompt = generate_long_prompt(target_tokens)
        try:
            r = ollama_generate(model, prompt, num_ctx=24576, timeout=300)
            ttft = r['prompt_eval_s'] + r['load_s']
            print(f"{label:>10} | {target_tokens:>6} | {r['prompt_tokens']:>6} | "
                  f"{r['prompt_tok_s']:>8.1f} t/s | {ttft:>6.2f}s | "
                  f"{r['gen_tok_s']:>6.1f} t/s | {r['total_s']:>6.1f}s")
        except Exception as e:
            print(f"{label:>10} | {target_tokens:>6} |  FAIL  | {str(e)[:40]}")

    print()
    print("Done! All experiments complete.")


if __name__ == "__main__":
    main()
