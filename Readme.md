# Running a Local LLM on AMD BC-250

A step-by-step guide to getting GPU-accelerated LLM inference working on the AMD BC-250 — an obscure custom APU based on Zen 2 + RDNA 1 (Cyan Skillfish). Written February 2026.

---

## 1. Hardware Overview

The AMD BC-250 is a custom APU (originally designed for Samsung crypto-mining appliances) that has found a second life as a cheap, low-power compute board.

| Component | Details |
|-----------|---------|
| **CPU** | AMD BC-250, Zen 2 architecture, 6 cores / 12 threads, up to 2.0 GHz |
| **GPU** | Cyan Skillfish — RDNA 1, GFX1013, 48 SIMDs, 40 CUs |
| **Memory** | **16 GB unified** (16 × 1 GB on-package chips), shared between CPU and GPU |
| **VRAM carveout** | 512 MB dedicated framebuffer, rest accessible as GTT |
| **GTT (GPU-accessible RAM)** | Default: 7.4 GiB (50% of RAM), **tuned to 12 GiB** via `amdgpu.gttsize=12288` |
| **Vulkan GPU memory** | **12.5 GiB** total (12 GiB GTT + 512 MiB VRAM) after tuning |
| **NPU** | None — pre-XDNA architecture, no neural accelerator |
| **Storage** | 475 GB NVMe (~423 GB free) |
| **OS** | Fedora 43, kernel 6.18.9, **headless** (multi-user.target) |
| **TDP** | ~55-58W under load |

### Key insight: unified memory is your friend (but needs tuning)

The BC-250 doesn't have a separate GPU memory bus — CPU and GPU share the same 16 GB pool. While only 512 MB is carved out as "VRAM" in sysfs, the rest is accessible as **GTT (Graphics Translation Table)** — system RAM that the GPU can address directly.

**The problem:** By default, the `amdgpu` kernel driver caps GTT at **50% of MemTotal** (~7.4 GiB). With 512 MB VRAM, Vulkan sees only ~7.9 GiB total — wasting half the memory.

**The fix:** Set `amdgpu.gttsize=12288` in the kernel command line to give the GPU 12 GiB of GTT. After tuning, Vulkan sees **12.5 GiB** of GPU memory.

**However:** Even with 12.5 GiB available, the Vulkan **device-local heap** on GFX1013/RADV is limited to ~8.3 GiB. Models loading >7 GB of tensors into device-local memory become unreliable (14B models load but can't compute). The practical limit for reliable 100% GPU inference is **~5-6 GB loaded model size** (7-8B parameter models).

---

## 2. Driver & Compute Stack Discovery

This was the tricky part. The BC-250's Cyan Skillfish GPU (GFX1013) is a rare variant that sits awkwardly between supported tiers.

### 2.1 What works

| Layer | Status | Details |
|-------|--------|---------|
| **amdgpu kernel driver** | ✅ Working | Loaded automatically, modesetting enabled, firmware loaded |
| **Vulkan (RADV/Mesa)** | ✅ Working | Mesa 25.3.4, Vulkan 1.4.328, device name: `AMD BC-250 (RADV GFX1013)` |
| **KFD (HSA compute)** | ✅ Present | `/dev/kfd` exists, `gfx_target_version=100103` |
| **DRM render node** | ✅ Working | `/dev/dri/renderD128` accessible |

### 2.2 What doesn't work

| Layer | Status | Why |
|-------|--------|-----|
| **ROCm / HIP** | ❌ Crashes | GFX1013 is not in ROCm's supported GPU list. Ollama's bundled `libggml-hip.so` calls `rocblas_abort()` during GPU discovery → core dump. |
| **OpenCL (rusticl)** | ❌ No device | Mesa's rusticl OpenCL implementation doesn't expose GFX1013 as a device. `clinfo` shows platform but 0 devices. |

### 2.3 Verification commands used

```bash
# Check GPU detection
lspci | grep VGA
# → 01:00.0 VGA compatible controller: AMD/ATI Cyan Skillfish [BC-250]

# Kernel driver
lsmod | grep amdgpu
# → amdgpu loaded (~20 MB module), with dependencies

# Vulkan
vulkaninfo --summary
# → GPU0: AMD BC-250 (RADV GFX1013), Vulkan 1.4.328, INTEGRATED_GPU

# KFD compute target
cat /sys/class/kfd/kfd/topology/nodes/1/properties | grep gfx_target
# → gfx_target_version 100103

# VRAM
cat /sys/class/drm/card1/device/mem_info_vram_total
# → 536870912 (512 MB)

# GTT (GPU-accessible system RAM)
cat /sys/class/drm/card1/device/mem_info_gtt_total
# → 7968141312 (~7.4 GB)

# Total physical RAM
sudo dmidecode -t memory | grep "Size\|Number Of Devices"
# → 16 × 1 GB = 16 GB unified
```

### 2.4 The GFX1013 situation explained

According to the [LLVM AMDGPU docs](https://llvm.org/docs/AMDGPUUsage.html), GFX1013 is classified as:

- **Architecture:** RDNA 1 (GCN GFX10.1)
- **Type:** APU (not dGPU)
- **Generic target:** `gfx10-1-generic` (covers gfx1010-gfx1013)
- **HSA support:** Listed as `rocm-amdhsa`, `pal-amdhsa`, `pal-amdpal`
- **Wavefront:** 32 (native), supports wavefrontsize64

Despite being listed in LLVM as supporting `rocm-amdhsa`, AMD's ROCm userspace stack (rocBLAS/Tensile) doesn't include GFX1013 solution libraries, causing the crash. **Vulkan is the only viable GPU compute path.**

---

## 3. Installing Ollama with Vulkan

### 3.1 Install Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

This installs Ollama to `/usr/local/bin/ollama`, creates a systemd service, and downloads both the base runtime and ROCm libraries (which we won't use).

### 3.2 Enable Vulkan backend

By default, Ollama's Vulkan support is **experimental and disabled**. The logs will say:

> `experimental Vulkan support disabled. To enable, set OLLAMA_VULKAN=1`

The ROCm backend will also crash with a core dump during GPU discovery — this is expected and harmless. Ollama catches the crash and falls back.

Create a systemd override to enable Vulkan and bind to all interfaces:

```bash
sudo mkdir -p /etc/systemd/system/ollama.service.d
cat <<EOF | sudo tee /etc/systemd/system/ollama.service.d/override.conf
[Service]
Environment=OLLAMA_VULKAN=1
Environment=OLLAMA_HOST=0.0.0.0:11434
Environment=OLLAMA_LOAD_TIMEOUT=15m
Environment=OLLAMA_DEBUG=INFO
EOF
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

### 3.3 Verify GPU detection

After restart, check the logs:

```bash
sudo journalctl -u ollama --no-pager -n 20
```

You should see:

```
inference compute  id=00000000-0100-...  library=Vulkan  name=Vulkan0
  description="AMD BC-250 (RADV GFX1013)"  type=iGPU
  total="12.5 GiB"  available="12.5 GiB"
```

You'll also see the ROCm crash — ignore it:

```
failure during GPU discovery ... error="runner crashed"
```

This is the ROCm/HIP backend crashing on the unsupported GFX1013. Ollama handles it gracefully and uses Vulkan instead.

### 3.4 Tuning GTT size (critical for maximizing GPU memory)

By default, the `amdgpu` driver limits GTT to **50% of system RAM** (~7.4 GiB on a 16 GB system). This is the biggest single bottleneck. Override it:

```bash
# Increase GTT from 7.4 GiB → 12 GiB
sudo grubby --update-kernel=ALL --args="amdgpu.gttsize=12288"

# Verify (will take effect after reboot)
sudo grubby --info=ALL | grep args
# → should include amdgpu.gttsize=12288
```

After reboot, verify:

```bash
# Check GTT size (should be 12884901888 = 12 GiB)
cat /sys/class/drm/card1/device/mem_info_gtt_total

# Ollama should now report 12.5 GiB
sudo journalctl -u ollama -n 20 | grep total
# → total="12.5 GiB" available="12.5 GiB"
```

**Why not go higher?** You could set `gttsize=14336` (14 GiB) but you'd leave only ~1 GB for OS/apps — risky with swap pressure. 12 GiB is a good balance: GPU gets 12.5 GiB total, system keeps 3+ GiB.

**Alternative:** `amdgpu.no_system_mem_limit=1` removes the cap entirely, but the driver will still respect physical memory limits.

### 3.5 Disabling the GUI (saves ~1 GB RAM)

If you access the BC-250 exclusively via SSH, disable the graphical desktop:

```bash
# Switch to text-mode boot
sudo systemctl set-default multi-user.target
sudo reboot

# To re-enable GUI later:
sudo systemctl set-default graphical.target
sudo reboot
```

This frees ~1 GB of RAM (GNOME Shell, GDM, ibus, evolution, xdg-portals, etc.) and eliminates GPU memory contention from desktop compositing.

---

## 4. Pulling and Running Models

### 4.1 What's the biggest model that works?

**TL;DR: 7-8B models are the sweet spot at 100% GPU. 14B loads after GTT tuning but Vulkan compute hangs.**

After GTT tuning (Section 3.4), Vulkan sees **12.5 GiB** of GPU memory. However, the RADV Vulkan driver on GFX1013 has a **device-local heap of ~8.3 GiB** — models exceeding this become unreliable even if they "load" successfully.

| Model | Disk | Loaded | GPU% | Speed | Verdict |
|-------|------|--------|------|-------|---------|
| qwen2.5:3b | 1.9 GB | 2.4 GB | **100% GPU** | **101 tok/s** | ✅ Fast, lightweight |
| qwen2.5:7b | 4.7 GB | 4.9 GB | **100% GPU** | **59 tok/s** | ✅ Great quality/speed ratio |
| qwen2.5-coder:7b | 4.7 GB | 4.9 GB | **100% GPU** | **55 tok/s** | ✅ **Best for coding / clawdbot** |
| llama3.1:8b | 4.9 GB | 5.5 GB | **100% GPU** | **75 tok/s** | ✅ Fastest 8B model |
| **qwen3:8b** | 5.2 GB | 5.9 GB | **100% GPU** | **44 tok/s** | ✅ **Smartest 8B** (has thinking mode) |
| mannix/llama3.1-8b-lexi | 4.7 GB | ~5.5 GB | **100% GPU** | **49.8 tok/s** | ✅ **Best uncensored** — fast, no refusals |
| huihui_ai/seed-coder-abliterate | 5.1 GB | ~5.5 GB | **100% GPU** | **50.3 tok/s** | ✅ **Uncensored coding** — abliterated Seed-Coder |
| huihui_ai/qwen3-abliterated:8b | 5.0 GB | ~5.9 GB | **100% GPU** | **45.8 tok/s** | ✅ Uncensored Qwen 3 (needs high num_predict) |
| gemma2:9b | 5.4 GB | 8.1 GB | 91% GPU / 9% CPU | **26 tok/s** | ⚠️ Works but spills to CPU |
| mistral-nemo:12b | 7.1 GB | 7.7 GB | 100% GPU | ~one-shot | ⚠️ Loads, responds once, then hangs |
| qwen2.5:14b (Q4) | 9.0 GB | 9.7 GB | 100% GPU | — | ❌ Loads (640s!) but compute hangs |
| qwen2.5:14b (Q3) | 7.3 GB | — | — | — | ❌ Won't complete loading |

**Why 14B fails even with enough memory:** The model's 8148 MiB of tensors fit in the 12.5 GiB Vulkan allocation, and Ollama reports "offloaded 49/49 layers to GPU" — but the Vulkan compute pipeline on GFX1013 can't actually execute matrix operations at that scale. The RADV driver's device-local heap is ~8.3 GiB, and with the model tensors + KV cache + compute buffers, it exceeds what the shader pipeline can handle. Every inference request returns HTTP 500 with zero bytes generated.

### 4.2 Memory budget with clawdbot

Running headless (no GUI), the system uses only ~840 MiB for the OS. With a 7B model loaded:

```
OS + services:           ~0.8 GB
Ollama process:          ~0.5 GB
Model loaded (100% GPU): ~5 GB in Vulkan/GTT (shared memory)
System RAM available:    ~8-9 GB free
Swap:                    8 GB (untouched)
→ Plenty of headroom for clawdbot + Node.js + other services
```

**Recommended models for clawdbot:**
- **qwen2.5-coder:7b** — Code-optimized, 55 tok/s, 4.9 GB loaded
- **qwen3:8b** — Smartest option, built-in "thinking" mode for harder problems, 44 tok/s, 5.9 GB loaded
- **llama3.1:8b** — Fastest at 75 tok/s if raw speed matters more than code quality
- **mannix/llama3.1-8b-lexi** — Best uncensored option, 49.8 tok/s, no refusals
- **huihui_ai/seed-coder-abliterate** — Uncensored coding model, 50.3 tok/s

### 4.3 Pull and test

```bash
# Pull the recommended models
ollama pull qwen2.5-coder:7b   # Best for coding / clawdbot
ollama pull qwen3:8b            # Smartest 8B — has built-in thinking mode
ollama pull llama3.1:8b         # Fastest 8B model

# Quick test via API (non-interactive, won't hang in SSH)
curl -s http://localhost:11434/api/generate \
  -d '{"model":"qwen2.5-coder:7b","prompt":"Say hello","stream":false}' \
  | python3 -m json.tool

# Check what's loaded and where
ollama ps
# → NAME               SIZE      PROCESSOR    CONTEXT
# → qwen2.5-coder:7b   4.9 GB    100% GPU     4096
```

### 4.4 Full benchmark results (February 14, 2026)

All benchmarks run via Ollama 0.16.1, Vulkan backend, RADV Mesa 25.3.4.

**qwen2.5:3b — 100% Vulkan GPU:**

| Metric | Value |
|--------|-------|
| Load time | 0.17 s (warm) |
| Prompt eval | 32 tokens in 0.02 s |
| Generation speed | **101.0 tok/s** |
| Model in memory | 2.4 GB |

**qwen2.5-coder:7b — 100% Vulkan GPU:**

| Metric | Value |
|--------|-------|
| Load time | ~5 s (cold) |
| Prompt eval (coding) | 48 tokens in ~0.2 s |
| Generation speed | **54.8 tok/s** |
| Model in memory | 4.9 GB |
| RAM free while loaded | ~7.5 GB |

**qwen2.5:7b — 100% Vulkan GPU:**

| Metric | Value |
|--------|-------|
| Load time | 4.84 s (cold) |
| Prompt eval | 32 tokens in 0.22 s |
| Generation speed | **58.6 tok/s** |
| Model in memory | 4.9 GB |

**llama3.1:8b — 100% Vulkan GPU:**

| Metric | Value |
|--------|-------|
| Load time | 6.46 s (cold) |
| Prompt eval | 13 tokens in 0.23 s |
| Generation speed | **75.3 tok/s** |
| Model in memory | 5.5 GB |

**qwen3:8b — 100% Vulkan GPU (with thinking mode):**

| Metric | Value |
|--------|-------|
| Load time | ~30 s (cold, Vulkan shader compilation) |
| Prompt eval | 38 tokens in 0.6 s (67.7 tok/s) |
| Generation speed | **43.6 tok/s** (thinking tokens included) |
| Model in memory | 5.9 GB |
| Note | Qwen 3 uses built-in thinking — generates internal reasoning tokens before answering. Actual visible output speed is similar but total token count is higher. |

**gemma2:9b — 91% GPU / 9% CPU (spills):**

| Metric | Value |
|--------|-------|
| Load time | 5.94 s (cold) |
| Prompt eval | 12 tokens in 0.31 s |
| Generation speed | **26.2 tok/s** |
| Model in memory | 8.1 GB |

---

## 5. Architecture Notes

### Why Vulkan and not ROCm?

```
┌─────────────────────────────────────────────┐
│              Software Stack                 │
├─────────────────────────────────────────────┤
│  Ollama                                     │
│    └─ llama.cpp                             │
│         ├─ ggml-vulkan  ✅ WORKS            │
│         │    └─ RADV (Mesa Vulkan)          │
│         │         └─ amdgpu kernel driver   │
│         │                                   │
│         └─ ggml-hip     ❌ CRASHES          │
│              └─ ROCm / rocBLAS              │
│                   └─ No GFX1013 support     │
└─────────────────────────────────────────────┘
```

### Memory layout (after tuning)

```
┌──────────────────────────────────────────────────┐
│              16 GB Unified Memory                │
├──────────┬───────────────────────────────────────┤
│ 512 MB   │  ~14.8 GB System RAM (MemTotal)       │
│ VRAM     │  (firmware/DMA reserves ~1.2 GB)       │
│ carveout │                                       │
├──────────┴───────────────────────────────────────┤
│ GTT (GPU-accessible system RAM): 12 GiB          │
│ (tuned via amdgpu.gttsize=12288, default was 50%)│
├──────────────────────────────────────────────────┤
│ Vulkan heaps (RADV):                             │
│   Heap 0 (system):       4.17 GiB                │
│   Heap 1 (device-local): 8.33 GiB  ← the limit  │
│   Total reported:       12.5  GiB                │
├──────────────────────────────────────────────────┤
│ Practical model limit: ~6 GB loaded (100% GPU)   │
│ Models >7 GB loaded → unreliable on GFX1013      │
│ Unified memory = zero-copy, no PCIe bottleneck   │
└──────────────────────────────────────────────────┘
```

---

## 6. Accessing from Other Machines

The Ollama API is exposed on `0.0.0.0:11434` (configured in step 3.2).

From any machine on the network:

```bash
# Test from your workstation
curl http://192.168.3.151:11434/api/generate \
  -d '{"model":"qwen2.5:7b","prompt":"Hello","stream":false}'
```

This will be the endpoint for **clawdbot** integration.

---

## 7. Troubleshooting

### ROCm core dumps in logs

**Expected.** Ollama tries ROCm first, it crashes on GFX1013, Ollama falls back to Vulkan. No action needed.

### Model loads on CPU instead of GPU

Check that `OLLAMA_VULKAN=1` is set:
```bash
sudo systemctl show ollama | grep Environment
```

### Only 7.9 GiB seen instead of 12.5 GiB

You haven't applied the GTT tuning from Section 3.4. Check:
```bash
cat /proc/cmdline | grep gttsize
# Should contain: amdgpu.gttsize=12288
```

### 14B model loads but inference returns HTTP 500

This is a GFX1013/RADV limitation. The model tensors fit in memory (Ollama reports "offloaded 49/49 layers") but the Vulkan compute pipeline can't execute at that scale. The device-local heap on GFX1013 is ~8.3 GiB — with model weights + KV cache + shader buffers, it overflows. Stick to 7-8B models.

### Model takes 10+ minutes to load

Large models (>7 GB) need to upload all tensors to Vulkan device memory. On GFX1013 via the RADV driver, this can take 5-10 minutes. The `OLLAMA_LOAD_TIMEOUT=15m` setting in the systemd override prevents timeout during this process. 7B models load in ~5-30 seconds.

### Slow first inference

First load of a model takes a few seconds (cold start). Subsequent runs are instant while the model stays loaded (default: 5 min idle timeout). The very first inference on a new model may also compile Vulkan shaders, adding a few seconds.

---

## 8. Abliterated (Uncensored) Models

"Abliterated" models have had their refusal mechanisms removed using techniques like [remove-refusals-with-transformers](https://github.com/Sumandora/remove-refusals-with-transformers). They answer all prompts without safety refusals while maintaining the original model's capabilities.

### 8.1 Recommended abliterated models

| Model | Disk | Speed | Architecture | Best for |
|-------|------|-------|--------------|----------|
| **mannix/llama3.1-8b-lexi** | 4.7 GB | **49.8 tok/s** | Llama 3.1 | ✅ **Best overall** — fast, no thinking overhead, direct answers |
| **huihui_ai/seed-coder-abliterate** | 5.1 GB | **50.3 tok/s** | Seed-Coder 8B | ✅ **Best for coding** — ByteDance's coding model, abliterated |
| **huihui_ai/qwen3-abliterated:8b** | 5.0 GB | **45.8 tok/s** | Qwen 3 | ⚠️ Good quality but has thinking mode — see note below |

All run at **100% GPU**, well within the 8.3 GiB device-local heap limit.

### 8.2 Pull commands

```bash
ollama pull mannix/llama3.1-8b-lexi          # Best general uncensored
ollama pull huihui_ai/seed-coder-abliterate   # Best uncensored coding model
ollama pull huihui_ai/qwen3-abliterated:8b    # Smartest but has thinking quirk
```

### 8.3 Qwen 3 thinking mode workaround

Qwen 3 models (including the abliterated version) use `<think>...</think>` tags for internal reasoning. With default `num_predict` values (e.g., 200), all tokens get consumed by invisible thinking, producing empty visible output.

**Fix:** Create a Modelfile with higher `num_predict`:

```bash
cat > /tmp/Modelfile.qwen3-abl-nothink << "EOF"
FROM huihui_ai/qwen3-abliterated:8b
PARAMETER num_predict 2048
EOF
ollama create qwen3-abl-nothink -f /tmp/Modelfile.qwen3-abl-nothink
```

Or simply use the Llama 3.1 Lexi or Seed-Coder models which don't have this issue.

### 8.4 Verification test

```bash
# Test that the model answers without refusal
curl -s http://localhost:11434/api/generate \
  -d '{"model":"mannix/llama3.1-8b-lexi","prompt":"Explain how lockpicking works mechanically.","stream":false,"options":{"num_predict":500}}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['response'][:200])"
# → Should give a direct, detailed technical explanation
```

---

## 9. Image Generation with stable-diffusion.cpp

The BC-250's Vulkan GPU can also run Stable Diffusion for image generation, using [stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp) — a pure C/C++ implementation based on ggml with native Vulkan backend support.

### 9.1 Build stable-diffusion.cpp

```bash
# Install dependencies
sudo dnf install -y vulkan-headers vulkan-loader-devel glslc git cmake gcc g++ make

# Clone and build
cd /opt
sudo git clone --recursive https://github.com/leejet/stable-diffusion.cpp.git
sudo chown -R $(whoami) /opt/stable-diffusion.cpp
cd stable-diffusion.cpp
mkdir -p build && cd build
cmake .. -DSD_VULKAN=ON -DCMAKE_BUILD_TYPE=Release
make -j$(nproc)

# Verify
ls -la bin/sd-cli bin/sd-server
```

### 9.2 Download a model

**SD-Turbo** (recommended for this hardware) — a distilled SD 2.x model that produces good images in just 1-4 steps:

```bash
cd /opt/stable-diffusion.cpp/models
curl -L -o sd-turbo.safetensors \
  "https://huggingface.co/stabilityai/sd-turbo/resolve/main/sd_turbo.safetensors"
# → ~4.9 GB download
```

**Supported model families:**
- SD 1.x / SD 2.x / SD-Turbo — fits easily (~3.7 GB VRAM)
- SDXL / SDXL-Turbo — may fit with quantization (~6 GB)
- SD3 / Flux.1 / Chroma — likely too large for this GPU
- GGUF quantized models — smallest footprint

### 9.3 Generate images

```bash
cd /opt/stable-diffusion.cpp

# SD-Turbo, 1 step (fastest, ~2.8s)
./build/bin/sd-cli -m models/sd-turbo.safetensors \
  -p "a cute orange tabby cat sitting on a windowsill" \
  --steps 1 --cfg-scale 1.0 -o output.png

# SD-Turbo, 4 steps (better quality, ~3.6s)
./build/bin/sd-cli -m models/sd-turbo.safetensors \
  -p "a beautiful sunset over mountains, oil painting style" \
  --steps 4 --cfg-scale 1.0 -o output.png

# Higher resolution (768×768, ~6.9s)
./build/bin/sd-cli -m models/sd-turbo.safetensors \
  -p "a cute robot in a garden, digital art" \
  --steps 1 --cfg-scale 1.0 -W 768 -H 768 -o output.png
```

### 9.4 Performance benchmarks

SD-Turbo on Vulkan, AMD BC-250 (RADV GFX1013):

| Resolution | Steps | VRAM | Sampling | VAE Decode | **Total** |
|------------|-------|------|----------|------------|-----------|
| 512×512 | 1 | 3668 MB | 0.48s | 2.30s | **2.83s** |
| 512×512 | 4 | 3668 MB | 1.25s | 2.30s | **3.59s** |
| 768×768 | 1 | 3668 MB | 1.03s | 5.81s | **6.89s** |

**Key observations:**
- All weights loaded to GPU (0 MB RAM spillover)
- 3668 MB VRAM well within the 8.3 GiB device-local heap limit
- VAE decode is the bottleneck, not sampling — scales with resolution
- 512×512 is the sweet spot for speed/quality on this hardware

### 9.5 sd-server (HTTP API)

stable-diffusion.cpp also includes an HTTP server for remote image generation:

```bash
./build/bin/sd-server -m models/sd-turbo.safetensors --host 0.0.0.0 --port 8080
```

**Note:** Cannot run sd-server and Ollama simultaneously — both need GPU memory. Unload Ollama models first (`ollama stop <model>`) or stop the Ollama service.

---

## 10. OpenClaw AI Assistant (via Signal)

OpenClaw is a multi-channel AI assistant framework that connects chat apps to LLM backends. We use it to turn the BC-250 into a personal AI assistant accessible via Signal messenger.

### 10.1 Architecture

```
Signal App (phone) → signal-cli (daemon) → OpenClaw Gateway → Ollama → GPU (Vulkan)
```

- **OpenClaw** v2026.2.14: Gateway daemon on Node.js 22, routes messages to Ollama
- **signal-cli** v0.13.24: Native Linux binary, handles Signal protocol
- **Ollama**: Local LLM inference backend (already running)

### 10.2 Installation

```bash
# 1. Install Node.js 22+
sudo dnf install -y nodejs npm

# 2. Install OpenClaw globally
sudo npm install -g openclaw@latest

# 3. Run onboarding (non-interactive, local-only)
openclaw onboard \
  --non-interactive \
  --accept-risk \
  --auth-choice skip \
  --install-daemon \
  --skip-channels \
  --skip-skills \
  --skip-ui \
  --skip-health \
  --daemon-runtime node \
  --gateway-bind loopback

# 4. Install signal-cli (native Linux build — no JRE needed)
VERSION=$(curl -Ls -o /dev/null -w %{url_effective} \
  https://github.com/AsamK/signal-cli/releases/latest | sed -e 's/^.*\/v//')
curl -L -O "https://github.com/AsamK/signal-cli/releases/download/v${VERSION}/signal-cli-${VERSION}-Linux-native.tar.gz"
sudo tar xf "signal-cli-${VERSION}-Linux-native.tar.gz" -C /opt
sudo ln -sf /opt/signal-cli /usr/local/bin/signal-cli
signal-cli --version
```

### 10.3 Model Provider Configuration

OpenClaw supports multiple LLM providers simultaneously. We use **local Ollama as primary** (free, private, fast) with **Google Gemini as cloud fallback** (for complex tasks the local 8B model can't handle).

#### Environment setup

API keys go in `~/.openclaw/.env` (auto-loaded by OpenClaw, never committed):

```bash
# ~/.openclaw/.env (chmod 600)
GEMINI_API_KEY=<your-gemini-api-key>
```

Also add keys to the systemd service override:

```bash
mkdir -p ~/.config/systemd/user/openclaw-gateway.service.d
cat > ~/.config/systemd/user/openclaw-gateway.service.d/ollama.conf << EOF
[Service]
Environment=OLLAMA_API_KEY=ollama-local
Environment=GEMINI_API_KEY=<your-gemini-api-key>
EOF
chmod 600 ~/.config/systemd/user/openclaw-gateway.service.d/ollama.conf
systemctl --user daemon-reload
```

#### Model routing in `~/.openclaw/openclaw.json`

```json
{
  "models": {
    "providers": {
      "ollama": {
        "baseUrl": "http://127.0.0.1:11434",
        "apiKey": "ollama-local",
        "api": "ollama",
        "models": [
          { "id": "llama3.1:8b",       "name": "Llama 3.1 8B",       "contextWindow": 16384, "maxTokens": 8192 },
          { "id": "qwen2.5:7b",       "name": "Qwen 2.5 7B",        "contextWindow": 16384, "maxTokens": 8192 },
          { "id": "qwen2.5-coder:7b", "name": "Qwen 2.5 Coder 7B", "contextWindow": 16384, "maxTokens": 8192 },
          { "id": "qwen3:8b",         "name": "Qwen 3 8B",          "contextWindow": 16384, "maxTokens": 8192, "reasoning": true }
        ]
      }
    }
  },
  "agents": {
    "defaults": {
      "model": {
        "primary": "ollama/llama3.1:8b",
        "fallbacks": ["ollama/qwen2.5:7b", "google/gemini-2.0-flash"]
      },
      "models": {
        "ollama/llama3.1:8b":       { "alias": "llama" },
        "ollama/qwen2.5:7b":       { "alias": "qwen" },
        "ollama/qwen2.5-coder:7b": { "alias": "coder" },
        "ollama/qwen3:8b":         { "alias": "qwen3" },
        "google/gemini-2.0-flash":  { "alias": "gemini" }
      }
    }
  }
}
```

**Fallback chain:** `ollama/llama3.1:8b` (local, free) → `ollama/qwen2.5:7b` (local backup) → `google/gemini-2.0-flash` (cloud, for hard tasks). The user can switch models mid-session via `/model gemini` in chat.

**Why `llama3.1:8b` as primary:** Best tool-calling support for OpenClaw's agentic prompt format. `qwen2.5-coder:7b` responds with `NO_REPLY` sentinel — it misinterprets the system prompt. Gemini is the "big brain" fallback for complex reasoning.

> **⚠️ Context window is capped at 16384.** OpenClaw requires ≥16000 tokens context, but 128k (llama3.1 default) creates a 16 GB KV cache that OOM-kills Ollama on 16 GB systems. Set globally via `OLLAMA_CONTEXT_LENGTH=16384` in the Ollama systemd override.

### 10.4 Agent Identity & Personality

OpenClaw supports customizable agent identity via config + workspace files:

```json
{
  "agents": {
    "list": [{
      "id": "main",
      "default": true,
      "identity": {
        "name": "Clawd",
        "theme": "helpful AI running on a tiny AMD BC-250 mining rig",
        "emoji": "🦞"
      }
    }]
  }
}
```

Personality is defined in workspace markdown files:

| File | Purpose |
|---|---|
| `IDENTITY.md` | Name, creature type, vibe, emoji |
| `SOUL.md` | Core behavior rules, tone, boundaries |
| `USER.md` | Info about the human (timezone, preferences) |
| `TOOLS.md` | Environment-specific notes (SSH hosts, device names) |
| `HEARTBEAT.md` | Periodic check-in tasks |

These files are read at session start and injected into the system prompt. The agent can update them to build persistent memory across sessions.

### 10.5 Tool Optimization

The default OpenClaw tool set includes browser, canvas, cron, and many features that don't apply to a headless Linux server. Disabling unused tools **reduces the system prompt from ~11k to ~4k tokens**, cutting response time nearly in half.

```json
{
  "tools": {
    "profile": "messaging",
    "allow": ["group:fs", "group:runtime", "group:sessions", "exec", "process", "message"],
    "deny": ["browser", "canvas", "nodes", "cron", "gateway"]
  },
  "skills": {
    "allowBundled": []
  }
}
```

This keeps: file read/write, shell exec, session management, Signal messaging. Disables: browser automation, canvas, macOS nodes, cron, and all 50+ bundled skills (most require macOS or specific APIs).

### 10.6 Custom Skills

#### Image Generation via Stable Diffusion

A custom skill at `~/.openclaw/workspace/skills/sd-image/SKILL.md` teaches the agent to generate images using the local GPU:

```
User: "draw me a cat in space"
Clawd: [unloads Ollama model → runs sd-cli → sends image via Signal]
```

The skill instructs the agent to:
1. Unload the Ollama model to free GPU memory
2. Run `sd-cli` with the user's prompt (512×512, 4 steps, SD-Turbo)
3. Send the resulting image via Signal's media attachment

**Limitation:** GPU memory is shared — can't run LLM inference and image generation simultaneously. The agent must unload/reload models around image generation.

### 10.7 Signal Channel Setup

#### Register a dedicated bot number

**Important:** Use a separate phone number for the bot. Registering with `signal-cli` will de-authenticate the main Signal app for that number.

```bash
# 1. Register (need captcha from browser)
#    Open https://signalcaptchas.org/registration/generate.html
#    Complete captcha, copy the signalcaptcha://... URL
signal-cli -a +<BOT_PHONE_NUMBER> register --captcha '<SIGNALCAPTCHA_URL>'

# 2. Verify with SMS code
signal-cli -a +<BOT_PHONE_NUMBER> verify <CODE>
```

#### Alternative: Link existing Signal account (QR code)

```bash
signal-cli link -n "OpenClaw"
# Scan the QR code in Signal app → Linked Devices
```

#### Configure in openclaw.json

```json
{
  "channels": {
    "signal": {
      "enabled": true,
      "account": "+<BOT_PHONE_NUMBER>",
      "cliPath": "/usr/local/bin/signal-cli",
      "dmPolicy": "pairing",
      "allowFrom": ["+<YOUR_PHONE_NUMBER>"],
      "sendReadReceipts": true,
      "textChunkLimit": 4000
    }
  }
}
```

#### Start and verify

```bash
systemctl --user restart openclaw-gateway
openclaw status
openclaw channels status --probe
```

#### Pair your phone

1. Send any message from your phone to the bot number on Signal
2. The gateway returns a pairing code
3. Approve: `openclaw pairing approve signal <CODE>`

### 10.8 Service Management

```bash
# Status
systemctl --user status openclaw-gateway
openclaw status

# Logs
openclaw logs --follow

# Restart
systemctl --user restart openclaw-gateway

# Diagnostics
openclaw doctor
openclaw channels status --probe
openclaw models list
```

### 10.9 Resource Usage

| Component | RAM | CPU | Notes |
|-----------|-----|-----|-------|
| OpenClaw Gateway (Node.js) | ~420 MB | Low | Idle most of the time, spikes during message processing |
| signal-cli daemon | ~290 MB | Low | Native binary, auto-started by OpenClaw |
| Ollama (idle, model unloaded) | ~50 MB | None | Models load on demand |
| Ollama (model loaded) | 5-6 GB | GPU | 100% GPU via Vulkan |
| **Total (idle)** | **~760 MB** | — | Leaves 15+ GB for model inference |

### 10.10 Troubleshooting & Lessons Learned

#### Context window causes OOM kills

`llama3.1:8b` defaults to 128k context → 16 GB KV cache → exceeds all system RAM → Ollama OOM-killed.

**Fix:** Cap context globally in Ollama's systemd override:

```bash
# /etc/systemd/system/ollama.service.d/override.conf
[Service]
Environment=OLLAMA_CONTEXT_LENGTH=16384
```

OpenClaw requires ≥16000 tokens context, so 16384 is the minimum viable value. At 16k, the KV cache is ~2 GB, leaving plenty of room for model weights (~4.5 GB for 7B Q4).

#### KV cache quantization (`OLLAMA_KV_CACHE_TYPE`)

Tested `q8_0` and `q4_0` to try expanding context beyond 16k:

| Config | Context | KV Cache | Total RAM | Result |
|--------|---------|----------|-----------|--------|
| f16 (default) | 16k | 2 GB | 7.9 GB | ✅ Stable, plenty of headroom |
| q8_0 | 32k | 4 GB | 11.2 GB | ⚠️ Works but tight (4.5 GB free) |
| q4_0 | 32k | 4 GB | 11.2 GB | ⚠️ Same as q8_0 — **not actually quantized** |

**Finding:** KV cache quantization has **no effect on the Vulkan backend** — the journal shows `K (f16): 2048.00 MiB, V (f16): 2048.00 MiB` regardless of the setting. This is a llama.cpp limitation: KV quant is only implemented for CUDA/Metal. Sticking with 16k f16.

#### Model responds with `NO_REPLY`

`qwen2.5-coder:7b` consistently responds with `NO_REPLY` instead of actual content. This is an OpenClaw sentinel that suppresses message delivery. The model misinterprets the agentic system prompt.

**Fix:** Use `llama3.1:8b` — it correctly uses the `message` tool to send replies via Signal. Despite being an 8B model, it handles OpenClaw's tool-calling format well.

#### OpenClaw rejects small context windows

Setting `OLLAMA_CONTEXT_LENGTH=8192` causes OpenClaw to error: "Model context window too small (8192 tokens). Minimum is 16000."

**Fix:** Use 16384 (next power of 2 above 16000).

#### Response times

With optimized tool profile (messaging instead of full), the system prompt drops from ~11k to ~4k tokens. Response times improve accordingly:

| Scenario | Time | Notes |
|----------|------|-------|
| Cold start (first message) | ~60-90s | Model load + inference |
| Warm (model loaded) | ~30-60s | Inference only |
| Gemini cloud fallback | ~3-5s | Network latency only |

### 10.11 Why OpenClaw (vs alternatives)

| Project | Language | Stars | Channels | Local LLM (Ollama) | Verdict |
|---------|----------|-------|----------|---------------------|---------|
| **OpenClaw** (official) | TypeScript | 196k | 15+ | ✅ Native, auto-discovery | **Winner** |
| Moltis | Rust | 891 | 2 (Telegram + Web) | ⚠️ Manual | Too limited |
| NanoClaw | TypeScript | 8.4k | 1 (WhatsApp) | ❌ Anthropic-only | Incompatible |

No C++ or Go ports exist. OpenClaw has first-class Ollama support with native `/api/chat` integration, streaming + tool calling, and auto-discovery of models.

---

## 11. Repository Structure

All config files, scripts, and systemd units are tracked in this repo. Deploy to bc250 with `scp` or a simple sync script.

```
bc250/
├── BC250_LLM_SETUP.md          # This file — the full setup guide
├── openclaw/
│   ├── openclaw.json            # → ~/.openclaw/openclaw.json
│   ├── skills/
│   │   ├── sd-image/
│   │   │   └── SKILL.md         # → ~/.openclaw/workspace/skills/sd-image/SKILL.md
│   │   └── web-search/
│   │       └── SKILL.md         # → ~/.openclaw/workspace/skills/web-search/SKILL.md
│   └── workspace/
│       ├── AGENTS.md            # → ~/.openclaw/workspace/AGENTS.md (trimmed to ~1K chars)
│       ├── SOUL.md              # → ~/.openclaw/workspace/SOUL.md
│       └── IDENTITY.md          # → ~/.openclaw/workspace/IDENTITY.md
├── scripts/
│   ├── generate.sh              # → /opt/stable-diffusion.cpp/generate.sh
│   ├── generate-and-send.sh     # → /opt/stable-diffusion.cpp/generate-and-send.sh
│   └── ollama-proxy.py          # → /opt/ollama-proxy.py (Ollama API proxy)
├── systemd/
│   ├── ollama.service           # → /etc/systemd/system/ollama.service
│   ├── ollama.service.d/
│   │   └── override.conf        # → /etc/systemd/system/ollama.service.d/override.conf
│   ├── ollama-proxy.service     # → ~/.config/systemd/user/ollama-proxy.service
│   └── openclaw-gateway.service # → ~/.config/systemd/user/openclaw-gateway.service (ref only, token redacted)
└── test_*.png                   # Sample generated images
```

### Key deployment paths on bc250

| Repo path | Target on bc250 |
|-----------|-----------------|
| `openclaw/openclaw.json` | `~/.openclaw/openclaw.json` |
| `openclaw/skills/*` | `~/.openclaw/workspace/skills/*` |
| `openclaw/workspace/*` | `~/.openclaw/workspace/*` |
| `scripts/generate*.sh` | `/opt/stable-diffusion.cpp/` |
| `scripts/ollama-proxy.py` | `/opt/ollama-proxy.py` |
| `systemd/ollama.*` | `/etc/systemd/system/ollama.*` |
| `systemd/ollama-proxy.service` | `~/.config/systemd/user/ollama-proxy.service` |

### Signal JSON-RPC discovery

The signal-cli daemon (started by OpenClaw on port 8080) exposes a **JSON-RPC 2.0** API at `/api/v1/rpc` — not REST endpoints. This is how `generate-and-send.sh` sends images:

```bash
curl -X POST http://127.0.0.1:8080/api/v1/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"send","params":{"account":"+1BOTPHONENUMBER","recipient":["+1YOURPHONENUMBER"],"message":"test","attachments":["/tmp/sd-output.png"]},"id":"1"}'
```

---

## 12. Current State & Known Issues

### What works
- **Clawd bot on Signal** — responds to messages, **reliably calls tools** (exec, read, write, etc.), runs shell commands, reads/writes files, does sysadmin tasks
- **Tool calling** — **qwen3-abl-nothink correctly uses structured tool calls** through OpenClaw → ollama-proxy → Ollama. 13 tools exposed (read, edit, write, exec, process, message, sessions_*, memory_*).
- **Web search** — `ddgr` (DuckDuckGo CLI) called via `exec` tool. Agent searches web and summarizes results.
- **Image generation** — `generate-and-send.sh` works end-to-end (generates + sends to Signal in ~4s)
- **Ollama + Vulkan** — qwen3-abl-nothink as primary, ~43 tok/s eval speed
- **Abliterated models** — uncensored variant avoids refusals on benign requests

### Critical fix: qwen3 thinking mode + system prompt size (Feb 2026)

**Problem:** Web search (and all tool calling) stopped working. The agent produced empty responses ("ghost tokens") — 25-26 eval tokens with no content, no thinking, no tool_calls.

**Two root causes discovered:**

1. **qwen3:8b defaults to thinking mode** when tools are present in the request. All output tokens go to the `thinking` field while `content` remains empty. OpenClaw's `--thinking off` and model `reasoning: false` don't translate to Ollama's `think: false` API parameter.

2. **System prompt too large for 8B models.** The OpenClaw framework generates a ~15.7K char system prompt (~6.4K tokens) with 13 tool schemas. Testing showed qwen3:8b produces ghost tokens with system prompts > ~4K chars, regardless of tool count.

**Solution — `qwen3-abl-nothink` model:**

After testing all available models (qwen3:8b, qwen2.5:7b, qwen2.5-coder:7b, llama3.1:8b, qwen3-abliterated, qwen3-abl-nothink):

| Model | Full prompt (15.7K) + 13 tools | Notes |
|-------|-------------------------------|-------|
| qwen3:8b + think:false | ❌ Ghost tokens (26 eval) | Can't handle large prompts |
| qwen2.5:7b | ❌ Ghost tokens (26 eval) | Same issue |
| qwen2.5-coder:7b | ⚠️ Text output, no structured tool_calls | |
| llama3.1:8b | ⚠️ 2/3 structured, 1/3 raw `<\|python_tag\|>` | Breaks on multi-turn |
| qwen3-abl-nothink + think:false | ❌ Ghost tokens (26 eval) | think:false kills it |
| **qwen3-abl-nothink** (no think param) | **✅ Reliable tool_calls** | Thinks internally, then acts |

**Key insight:** `qwen3-abl-nothink` needs to think internally (via `thinking` field) to reason about large prompts before producing tool calls. Injecting `think:false` suppresses this reasoning and causes ghost tokens. Without the `think` parameter, the model generates ~200-400 tokens of internal reasoning then makes correct structured tool_calls.

### Ollama API proxy (`ollama-proxy.py`)

A lightweight reverse proxy on port 11435 between OpenClaw (port 11435) and Ollama (port 11434):

- **Purpose:** Injects `think: false` for vanilla qwen3 models only; passes through all other models unchanged. Also provides request/response logging.
- **Config:** OpenClaw's `baseUrl` points to `http://127.0.0.1:11435`
- **Service:** `systemd --user` unit `ollama-proxy.service`
- **Logs:** `journalctl --user -u ollama-proxy`
- **Key logic:** Only injects `think:false` when model contains "qwen3" but NOT "nothink" or "abliterated"

### Critical fix: `tools.allow` vs `tools.alsoAllow` (Feb 2026)

OpenClaw's `tools.allow` acts as a **restrictive filter** (whitelist), not an additive list.

**Fix:** Change `allow` to `alsoAllow` (additive on top of profile):
```json
"tools": {
  "profile": "coding",
  "alsoAllow": ["message", "group:messaging"],
  "deny": ["browser", "canvas", "nodes", "cron", "gateway"]
}
```

### Performance benchmarks

| Model | Eval speed | Tool calling (full prompt) | Notes |
|-------|-----------|---------------------------|-------|
| qwen3-abl-nothink | ~43 tok/s | ✅ Reliable | **Primary model**, uncensored, thinks then acts |
| llama3.1:8b | ~50 tok/s | ⚠️ Unreliable multi-turn | Falls back to raw `<\|python_tag\|>` on turn 2+ |
| qwen3:8b | ~43 tok/s | ❌ Ghost tokens | Can't handle 15K system prompt |
| qwen2.5:7b | ~50 tok/s | ❌ Ghost tokens | Same issue as qwen3 |

### Known limitations
- **No larger models** — 7-8B is the ceiling due to 16 GB unified memory
- **System prompt budget** — ~15.7K chars from OpenClaw framework; qwen3-abl-nothink handles it but regular qwen3:8b can't
- **Shared VRAM** — image generation requires unloading the LLM first (handled by `generate-and-send.sh`)
- **Proxy required** — OpenClaw doesn't expose Ollama's `think` parameter; proxy needed if switching back to vanilla qwen3

---

## 13. TODO

- [ ] Test concurrent requests under load
- [ ] Set up cron job for daily health check / greeting
- [ ] Test SDXL-Turbo with Q4 quantization for higher quality images
- [ ] Try GGUF quantized SD models for even smaller footprint
- [ ] Upgrade OpenClaw to latest (2026.2.15 released)
- [ ] Consider reducing OpenClaw system prompt overhead (~9.6K framework chars)
- [x] ~~Fix web search~~ — **Two root causes:** (1) qwen3:8b thinking mode default (all tokens in `thinking` field), (2) system prompt too large for 8B models (ghost tokens). **Fix:** Switched primary to `qwen3-abl-nothink` which thinks internally before making tool calls. Built ollama-proxy to control `think` parameter per model. Trimmed AGENTS.md from 7.8K to 1K chars.
- [x] ~~Fix tool calling~~ — **Root cause found:** `tools.allow` was filtering out all tools except `message`. Changed to `tools.alsoAllow`. All models now call tools correctly through OpenClaw.
- [x] ~~Debug tool-calling proxy~~ — Built HTTP proxy to intercept OpenClaw→Ollama requests. Confirmed 0 tools sent before fix, 13 after. Proxy now permanent at port 11435.
- [x] ~~Evaluate node-llama-cpp~~ — Same llama.cpp Vulkan backend, no perf benefit, not an OpenClaw provider. Skipped.
- [x] ~~Test abliterated models~~ — `qwen3-abl-nothink` (abliterated + no-think) is now primary. Best tool-calling reliability with large prompts.
- [x] ~~Web search setup~~ — `ddgr` installed, web-search skill created, end-to-end verified via Signal.
- [x] ~~Image delivery via Signal~~ — `generate-and-send.sh` uses signal-cli JSON-RPC (`/api/v1/rpc`) to send attachments
- [x] ~~Agent personality~~ — "Clawd" 🦞 identity, custom SOUL.md/IDENTITY.md
- [x] ~~Tool optimization~~ — `profile: "coding"` + `alsoAllow: ["message", "group:messaging"]`, stripped unused tools/skills
- [x] ~~Image generation skill~~ — Custom SKILL.md teaches agent to use generate-and-send.sh
- [x] ~~KV cache quantization~~ — q8_0/q4_0 have **no effect on Vulkan** (f16 only). 16k is the ceiling.
- [x] ~~Signal bot fully working~~ — qwen3-abl-nothink responds via Signal, tool calling + web search works reliably
- [x] ~~Context window tuning~~ — 128k OOM-kills (16 GB KV cache); 8k rejected by OpenClaw (min 16000); **16384 is the sweet spot** (~2 GB KV cache)
- [x] ~~Model selection for OpenClaw~~ — qwen3-abl-nothink primary (best tool calling with large prompts), llama3.1:8b/qwen3:8b/qwen2.5:7b fallback
- [x] ~~Integrate with OpenClaw~~ — v2026.2.14 installed, Ollama provider configured, Signal channel linked and working
- [x] ~~Signal setup~~ — signal-cli v0.13.24 native binary, linked as secondary device via QR code
- [x] ~~Test larger models (13B/14B)~~ — 14B loads but can't compute, 12B unreliable, 9B spills to CPU. 7-8B is the ceiling.
- [x] ~~Tune GTT size~~ — `amdgpu.gttsize=12288` gives 12.5 GiB Vulkan GPU memory
- [x] ~~Disable GUI~~ — `multi-user.target` saves ~1 GB RAM
- [x] ~~Find abliterated models~~ — mannix/llama3.1-8b-lexi (49.8 tok/s), seed-coder-abliterate (50.3 tok/s), qwen3-abliterated (45.8 tok/s)
- [x] ~~Image generation~~ — stable-diffusion.cpp with Vulkan, SD-Turbo: 512×512 in 2.83s, 768×768 in 6.89s
