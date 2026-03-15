#!/usr/bin/env python3
"""Follow-up stress test: Actually FILL the large context windows.
Previous test proved allocation works. This proves actual use works.

Run on BC-250: python3 -u /opt/netscan/tmp/test-stress-fill.py
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


def ollama_generate(model, prompt, num_ctx, timeout=1800, num_predict=50):
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

    return {
        "prompt_tokens": pc,
        "prompt_tok_s": pc / pd if pd > 0 else 0,
        "gen_tokens": ec,
        "gen_tok_s": ec / ed if ed > 0 else 0,
        "total_s": resp.get("total_duration", 0) / 1e9,
        "load_s": resp.get("load_duration", 0) / 1e9,
        "wall_s": wall,
        "response": resp.get("response", "")[:200],
    }


def ollama_unload(model):
    payload = json.dumps({"model": model, "keep_alive": 0}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate", payload,
        headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req, timeout=30)
    except Exception:
        pass


def set_kv_cache_type(kv_type):
    if kv_type is None:
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
    for _ in range(30):
        try:
            urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=5)
            return True
        except Exception:
            time.sleep(2)
    return False


def generate_filler(target_tokens):
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


def get_kv_logs():
    log = subprocess.run(
        ["sudo", "journalctl", "-u", "ollama", "-n", "30", "--no-pager"],
        capture_output=True, text=True
    )
    for line in log.stdout.splitlines():
        low = line.lower()
        if any(k in low for k in ["kv cache", "kv self", "total=", "available="]):
            print(f"    log: {line.strip()[-130:]}")


def main():
    print("=" * 70)
    print("STRESS FILL TEST — Prove large contexts actually WORK")
    print("=" * 70)
    print(f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # Set Q4_0
    print("Setting KV cache type to q4_0...")
    if not set_kv_cache_type("q4_0"):
        print("FATAL: Could not set q4_0")
        return

    tests = [
        # (context_size, fill_tokens, label)
        (65536,  30000,  "Q4_0 @ 64K, ~30K tok fill"),
        (98304,  50000,  "Q4_0 @ 96K, ~50K tok fill"),
        (131072, 50000,  "Q4_0 @ 128K, ~50K tok fill"),
        (131072, 90000,  "Q4_0 @ 128K, ~90K tok fill"),
    ]

    results = []
    for ctx, fill, label in tests:
        print(f"\n{'─' * 60}")
        print(f"  {label}")
        print(f"{'─' * 60}")

        ollama_unload(MODEL)
        time.sleep(2)

        prompt = generate_filler(fill)
        print(f"  Generated prompt targeting ~{fill} tokens...")

        try:
            start = time.time()
            r = ollama_generate(MODEL, prompt, num_ctx=ctx, timeout=1800)
            print(f"  RESULT:")
            print(f"    Prompt: {r['prompt_tokens']} tokens in {r['prompt_tokens']/r['prompt_tok_s']:.1f}s = {r['prompt_tok_s']:.1f} tok/s")
            print(f"    Gen:    {r['gen_tokens']} tokens in {r['gen_tokens']/r['gen_tok_s']:.1f}s = {r['gen_tok_s']:.1f} tok/s")
            print(f"    Load: {r['load_s']:.1f}s  Wall: {r['wall_s']:.1f}s")
            resp_preview = r['response'].replace('\n', ' ')[:100]
            print(f"    Response: {resp_preview}...")
            get_kv_logs()
            results.append((label, r))
        except Exception as e:
            print(f"  FAILED: {e}")
            results.append((label, None))
            get_kv_logs()

    # Cleanup
    print(f"\n{'=' * 70}")
    print("CLEANUP: Restoring FP16")
    set_kv_cache_type(None)
    print("Done.")

    # Summary
    print(f"\n{'=' * 70}")
    print("SUMMARY — Stress Fill Results")
    print(f"{'=' * 70}")
    print(f"{'Test':<40} {'Prompt tok':<12} {'Prefill':<12} {'Gen tok/s':<10} {'Wall (s)'}")
    print("─" * 90)
    for label, r in results:
        if r is None:
            print(f"{label:<40} {'FAILED'}")
        else:
            print(f"{label:<40} {r['prompt_tokens']:<12} {r['prompt_tok_s']:<12.1f} {r['gen_tok_s']:<10.1f} {r['wall_s']:.0f}")

    print("\nDone!")


if __name__ == "__main__":
    main()
