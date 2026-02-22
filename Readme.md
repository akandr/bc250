# AMD BC-250: Local LLM, Image Generation & Signal Bot

<!-- Title updated: the guide covers far more than just LLM — it includes
     stable-diffusion.cpp image generation, OpenClaw + Signal bot integration,
     and extensive driver/compute stack documentation. -->

A step-by-step guide to getting GPU-accelerated LLM inference, image generation, and a Signal chat bot working on the AMD BC-250 — an obscure custom APU based on Zen 2 + RDNA 1 (Cyan Skillfish). Written February 2026.

---

## 1. Hardware Overview

The AMD BC-250 is a custom APU (originally designed for Samsung crypto-mining appliances) that has found a second life as a cheap compute board for hobbyists.

<!-- Note: previously described as "low-power" — at 220W TDP this is NOT a
     low-power board. Idle with governor tuning is 35-45W, but under load
     it draws up to ~225W at the wall. -->

| Component | Details |
|-----------|---------|
| **CPU** | AMD BC-250, Zen 2 architecture, 6 cores / 12 threads, up to 2.0 GHz |
| **GPU** | Cyan Skillfish — RDNA 1, GFX1013, 48 SIMDs, **24 CUs** |
| **Memory** | **16 GB unified** (16 × 1 GB on-package chips), shared between CPU and GPU |
| **VRAM carveout** | 512 MB dedicated framebuffer, rest accessible as GTT |
| **GTT (GPU-accessible RAM)** | Default: 7.4 GiB (50% of RAM), **tuned to 12 GiB** via `amdgpu.gttsize=12288` |
| **Vulkan GPU memory** | **12.5 GiB** total (12 GiB GTT + 512 MiB VRAM) after tuning |
| **NPU** | None — pre-XDNA architecture, no neural accelerator |
| **Storage** | 475 GB NVMe (~423 GB free) |
| **OS** | Fedora 43, kernel 6.18.9, **headless** (multi-user.target) |
| **TDP** | **220W** (board-level) — see power notes below |

<!-- CU count fix: previously listed as "40 CUs" which was wrong.
     48 SIMDs ÷ 2 SIMDs/CU = 24 CUs. 24 CUs × 64 stream processors = 1536 SPs,
     matching videocardz.net specs. The "40 CUs" figure likely confused this with
     Navi 10 (RX 5700 XT, which has 40 CUs). The official bc250-documentation
     repo (github.com/mothenjoyer69/bc250-documentation) also confirms 24 CUs.
     Note: that repo calls the GPU "RDNA2" but LLVM's AMDGPU target table lists
     GFX1013 under GFX10.1 (RDNA 1), not GFX10.3 (RDNA 2). The Cyan Skillfish
     silicon is a unique hybrid — RDNA 1 base with some RDNA 2 features (like
     basic ray tracing). We label it RDNA 1 here per the LLVM classification. -->

<!-- TDP fix: previously listed as "~55-58W under load" which was WRONG.
     The BC-250 board TDP is 220W per the official bc250-documentation repo:
     "Keep in mind the BC-250 has a TDP of 220W" and community measurements.
     Real-world power consumption at the wall:
       - Idle (stock firmware, no governor):  70-130W (!)
       - Idle (with oberon-governor tuned):   35-45W
       - Under full GPU load (gaming/compute): ~200-225W
     The old "55-58W" figure was likely a misreading of idle-with-governor power.
     Sources: reddit.com/r/linux_gaming, reddit.com/r/homelab, and
     github.com/mothenjoyer69/bc250-documentation -->

### Key insight: unified memory is your friend (but needs tuning)

The BC-250 doesn't have a separate GPU memory bus — CPU and GPU share the same 16 GB pool. While only 512 MB is carved out as "VRAM" in sysfs, the rest is accessible as **GTT (Graphics Translation Table)** — system RAM that the GPU can address directly.

**The problem:** By default, the `amdgpu` kernel driver caps GTT at **50% of MemTotal** (~7.4 GiB). With 512 MB VRAM, Vulkan sees only ~7.9 GiB total — wasting half the memory.

**The fix:** Set `amdgpu.gttsize=12288` in the kernel command line to give the GPU 12 GiB of GTT. After tuning, Vulkan sees **12.5 GiB** of GPU memory.

**However:** Even with 12.5 GiB available, the kernel's **TTM memory manager** defaults to capping GPU allocations at ~7.4 GiB (50% of RAM) — a second bottleneck independent of the GTT size. By increasing `ttm.pages_limit` to match the GTT (12 GiB = 3,145,728 pages), the full 12.5 GiB becomes usable. After both fixes, the practical limit for 100% GPU inference is **~12 GB loaded model size** — enough for **14B parameter models**.

<!-- TTM fix (July 2026): The default ttm.pages_limit = totalram_pages/2 ≈ 7.42 GiB.
     This was the REAL bottleneck preventing 14B models from computing — the device-local
     heap reported 8.33 GiB but TTM could only back ~7.4 GiB of allocations. Fix:
     echo 3145728 > /sys/module/ttm/parameters/pages_limit
     Persisted via /etc/modprobe.d/ttm-gpu-memory.conf and /etc/tmpfiles.d/gpu-ttm-memory.conf -->

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

### 3.5 Tuning TTM pages_limit (critical for 14B models)

Even with GTT set to 12 GiB, the kernel's **TTM memory manager** applies its own cap at ~50% of system RAM (~7.4 GiB). This invisible second bottleneck prevents models >7 GB from computing — they load fine but produce HTTP 500 errors during inference.

<!-- This was THE breakthrough that unlocked 14B models on BC-250. The TTM pages_limit
     defaults to totalram_pages/2 (kernel source: drivers/gpu/drm/ttm/ttm_sys_manager.c).
     With 15.2 GiB visible RAM, that's ~7.42 GiB — less than the 8.33 GiB device-local
     Vulkan heap. So TTM silently denied allocations that RADV promised were available. -->

```bash
# Runtime fix (immediate, no reboot needed)
echo 3145728 | sudo tee /sys/module/ttm/parameters/pages_limit   # 12 GiB
echo 3145728 | sudo tee /sys/module/ttm/parameters/page_pool_size

# Persistent — modprobe.d (applied when ttm module loads at boot)
echo "options ttm pages_limit=3145728 page_pool_size=3145728" | \
  sudo tee /etc/modprobe.d/ttm-gpu-memory.conf

# Persistent — tmpfiles.d (fallback, applied by systemd-tmpfiles)
printf "w /sys/module/ttm/parameters/pages_limit - - - - 3145728\n\
w /sys/module/ttm/parameters/page_pool_size - - - - 3145728\n" | \
  sudo tee /etc/tmpfiles.d/gpu-ttm-memory.conf

# Regenerate initramfs to include new modprobe.d config
sudo dracut -f
```

Verify:
```bash
cat /sys/module/ttm/parameters/pages_limit
# → 3145728 (= 12 GiB in 4 KiB pages)
```

**How to calculate:** `pages = GiB × 1024 × 1024 / 4 = GiB × 262144`. So 12 GiB = `12 × 262144 = 3145728` pages.

**Result:** After this fix, qwen3:14b loads (12 GB) and runs at **27 tok/s, 100% GPU**. Previously it would hang during compute.

### 3.6 Disabling the GUI (saves ~1 GB RAM)

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

**TL;DR: After TTM tuning, 14B models run at 100% GPU (~27 tok/s). 7-8B models remain the fastest option (~45-75 tok/s).**

After GTT tuning (Section 3.4) and TTM fix (Section 3.5), Vulkan sees **12.5 GiB** of GPU memory with the full allocation backed by TTM. The RADV device-local heap is ~8.3 GiB, and non-device-local is ~4.2 GiB — Ollama's Vulkan backend uses both, enabling models up to ~12 GB loaded.

| Model | Disk | Loaded | GPU% | Speed | Verdict |
|-------|------|--------|------|-------|---------|
| qwen2.5:3b | 1.9 GB | 2.4 GB | **100% GPU** | **101 tok/s** | ✅ Fast, lightweight |
| qwen2.5:7b | 4.7 GB | 4.9 GB | **100% GPU** | **59 tok/s** | ✅ Great quality/speed ratio |
| qwen2.5-coder:7b | 4.7 GB | 4.9 GB | **100% GPU** | **55 tok/s** | ✅ Good for coding |
| llama3.1:8b | 4.9 GB | 5.5 GB | **100% GPU** | **75 tok/s** | ✅ Fastest 8B model |
| **qwen3:8b** | 5.2 GB | 5.9 GB | **100% GPU** | **44 tok/s** | ✅ Smartest 8B (has thinking mode) |
| qwen3-abl-nothink (8B) | 5.0 GB | 7.6 GB | **100% GPU** | **46 tok/s** | ✅ Reliable tool calling |
| mannix/llama3.1-8b-lexi | 4.7 GB | ~5.5 GB | **100% GPU** | **49.8 tok/s** | ✅ Best uncensored 8B |
| huihui_ai/seed-coder-abliterate | 5.1 GB | ~5.5 GB | **100% GPU** | **50.3 tok/s** | ✅ Uncensored coding |
| **mistral-nemo:12b** | 7.1 GB | 10 GB | **100% GPU** | **34 tok/s** | ✅ Great 12B option (after TTM fix) |
| **qwen3-14b-abl-nothink** | 9.0 GB | 11 GB | **100% GPU** | **27.5 tok/s** | ✅ **Primary model** — best quality |
| **qwen3:14b** | 9.3 GB | 12 GB | **100% GPU** | **27 tok/s** | ✅ Largest working model |
| huihui_ai/qwen3-abliterated:14b | 9.0 GB | 11 GB | **100% GPU** | **27.7 tok/s** | ✅ Uncensored 14B |
| gemma2:9b | 5.4 GB | 8.1 GB | 91% GPU / 9% CPU | **26 tok/s** | ⚠️ Works but spills to CPU |

<!-- Table updated July 2026: After increasing ttm.pages_limit from 7.42 GiB to 12 GiB,
     14B models now run successfully at 100% GPU. Previously, 14B loaded but compute hung
     because TTM couldn't back the full device-local heap. mistral-nemo:12b also now works
     reliably instead of hanging after one response. -->

**Why 14B works now (but didn't before):** The kernel's TTM (Translation Table Manager) memory subsystem defaults `pages_limit` to ~50% of system RAM (~7.4 GiB). Even though the GPU's Vulkan device-local heap reports 8.33 GiB, TTM could only actually back ~7.4 GiB of GPU memory allocations. A 14B model at ~9-10 GB loaded exceeded this invisible cap, causing compute hangs. Increasing `ttm.pages_limit` to 12 GiB (matching GTT) removes this bottleneck.

### 4.2 Memory budget with clawdbot

Running headless (no GUI), the system uses only ~840 MiB for the OS. With the 14B primary model loaded:

```
OS + services:           ~0.8 GB
Ollama process:          ~0.5 GB
Model loaded (100% GPU): ~11 GB in Vulkan/GTT (shared memory)
System RAM available:    ~1.8-3 GB free
Swap:                    8 GB (mostly untouched)
→ Tight but stable — clawdbot + Node.js + proxy fit in remaining RAM
```

With an 8B model (fallback):

```
OS + services:           ~0.8 GB
Ollama process:          ~0.5 GB
Model loaded (100% GPU): ~7.6 GB in Vulkan/GTT
System RAM available:    ~5-6 GB free
Swap:                    8 GB (untouched)
→ Plenty of headroom
```

**Recommended models for clawdbot:**
- **qwen3-14b-abl-nothink** — **Primary**: best quality, abliterated, 27.5 tok/s, 11 GB loaded
- **qwen3-abl-nothink (8B)** — Fallback: faster (46 tok/s), smaller (7.6 GB), still reliable tool calling
- **mistral-nemo:12b** — Alternative: good quality, 34 tok/s, 10 GB loaded
- **qwen3:14b** — Standard (non-abliterated) 14B, 27 tok/s

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
│ TTM pages_limit: 12 GiB (3,145,728 pages)       │
│ (tuned via modprobe.d, default was 50% ≈ 7.4 GiB)│
│ ⚠️ DEFAULT TTM LIMIT WAS THE REAL BOTTLENECK     │
├──────────────────────────────────────────────────┤
│ Vulkan heaps (RADV):                             │
│   Heap 0 (system):       4.17 GiB                │
│   Heap 1 (device-local): 8.33 GiB                │
│   Total reported:       12.5  GiB                │
├──────────────────────────────────────────────────┤
│ Practical model limit: ~12 GB loaded (100% GPU)  │
│ 14B models: ✅ 27 tok/s at 11-12 GB loaded       │
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

This was caused by the **TTM `pages_limit` bottleneck** — NOT a GFX1013/RADV limitation. The default TTM limit (~7.4 GiB) prevented full use of the 12.5 GiB Vulkan memory.

**Fix:** Increase TTM pages_limit to match GTT:
```bash
# Runtime fix (immediate)
echo 3145728 | sudo tee /sys/module/ttm/parameters/pages_limit
echo 3145728 | sudo tee /sys/module/ttm/parameters/page_pool_size

# Persistent (survives reboot)
echo "options ttm pages_limit=3145728 page_pool_size=3145728" | sudo tee /etc/modprobe.d/ttm-gpu-memory.conf
echo "w /sys/module/ttm/parameters/pages_limit - - - - 3145728" | sudo tee /etc/tmpfiles.d/gpu-ttm-memory.conf
echo "w /sys/module/ttm/parameters/page_pool_size - - - - 3145728" | sudo tee -a /etc/tmpfiles.d/gpu-ttm-memory.conf
sudo dracut -f  # Regenerate initramfs
```

After this fix, qwen3:14b runs at **27 tok/s, 100% GPU, 12 GB loaded**.

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
| **qwen3-14b-abl-nothink** | 9.0 GB | **27.5 tok/s** | Qwen 3 14B | ✅ **Primary model** — smartest, reliable tool calling, no refusals |
| **huihui_ai/qwen3-abliterated:14b** | 9.0 GB | **27.7 tok/s** | Qwen 3 14B | ✅ **Best abliterated 14B** — same base, marginally faster than standard |
| **qwen3-abl-nothink** (8B) | 5.0 GB | **46 tok/s** | Qwen 3 8B | ✅ **Fastest abliterated** — fallback for speed-sensitive tasks |
| **mannix/llama3.1-8b-lexi** | 4.7 GB | **49.8 tok/s** | Llama 3.1 | ✅ Fast, no thinking overhead, direct answers |
| **huihui_ai/seed-coder-abliterate** | 5.1 GB | **50.3 tok/s** | Seed-Coder 8B | ✅ **Best for coding** — ByteDance's coding model, abliterated |

<!-- Section updated July 2026: added 14B abliterated variants. Quality testing confirms
     zero performance/intelligence loss from abliteration — identical code quality and
     reasoning compared to standard qwen3:14b. The abliterated 14B is marginally faster
     (27.5 vs 27.0 tok/s) due to 1 GB smaller model file (different quantization). -->

All models run at **100% GPU**. The 14B models require the TTM fix (Section 3.5) to work reliably.

**Abliterated 14B vs standard 14B:** In side-by-side testing (coding tasks, reasoning, tool calling), the abliterated variant shows **zero measurable quality or intelligence loss**. Both produce identical algorithmic solutions with similar explanation quality. The abliterated variant is marginally faster due to a slightly smaller model file.

### 8.2 Pull commands

```bash
# 14B abliterated (primary model after TTM fix)
ollama pull huihui_ai/qwen3-abliterated:14b
# Create nothink variant:
cat > /tmp/Modelfile.qwen3-14b-abl-nothink << "EOF"
FROM huihui_ai/qwen3-abliterated:14b
PARAMETER num_predict 2048
PARAMETER repeat_penalty 1
PARAMETER temperature 0.6
PARAMETER top_k 20
PARAMETER top_p 0.95
EOF
ollama create qwen3-14b-abl-nothink -f /tmp/Modelfile.qwen3-14b-abl-nothink

# 8B abliterated (fallback)
ollama pull huihui_ai/qwen3-abliterated:8b
ollama pull mannix/llama3.1-8b-lexi
ollama pull huihui_ai/seed-coder-abliterate
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

### 9.2 Download models

#### FLUX.1-schnell (recommended — best quality)

**FLUX.1-schnell** is a 12B parameter flow-matching model by Black Forest Labs (Apache 2.0). It produces excellent images in just 4 steps. On the BC-250 with 16 GB unified memory, we use quantized variants to fit:

```bash
mkdir -p /opt/stable-diffusion.cpp/models/flux && cd /opt/stable-diffusion.cpp/models/flux

# Diffusion model — Q4_K quantized (6.5 GB VRAM)
curl -L -O "https://huggingface.co/second-state/FLUX.1-schnell-GGUF/resolve/main/flux1-schnell-q4_k.gguf"

# VAE decoder (320 MB, runs on GPU)
curl -L -O "https://huggingface.co/second-state/FLUX.1-schnell-GGUF/resolve/main/ae.safetensors"

# CLIP-L text encoder (235 MB, runs on CPU)
curl -L -O "https://huggingface.co/second-state/FLUX.1-schnell-GGUF/resolve/main/clip_l.safetensors"

# T5-XXL text encoder — Q4_K_M quantized (2.9 GB, runs on CPU)
# NOTE: fp16 (9.2 GB) causes OOM on 16 GB unified memory; Q8_0 (5 GB) causes thrashing
curl -L -O "https://huggingface.co/city96/t5-v1_1-xxl-encoder-gguf/resolve/main/t5-v1_1-xxl-encoder-Q4_K_M.gguf"
```

**Memory budget (fits in 16 GB unified):**
| Component | Size | Location |
|-----------|------|----------|
| FLUX Q4_K diffusion | 6,566 MB | VRAM |
| VAE decoder | 95 MB | VRAM |
| T5-XXL Q4_K_M encoder | 2,900 MB | RAM (CPU) |
| CLIP-L encoder | 235 MB | RAM (CPU) |
| Compute buffers | ~400 MB | VRAM |
| **Total** | **~10,200 MB** | Fits with 5+ GB headroom |

#### SD-Turbo (fallback — fastest)

**SD-Turbo** — a distilled SD 2.x model, much smaller but lower quality:

```bash
cd /opt/stable-diffusion.cpp/models
curl -L -o sd-turbo.safetensors \
  "https://huggingface.co/stabilityai/sd-turbo/resolve/main/sd_turbo.safetensors"
# → ~4.9 GB download, uses only 3.7 GB VRAM
```

### 9.3 Generate images

#### FLUX.1-schnell (recommended)

```bash
cd /opt/stable-diffusion.cpp

# FLUX.1-schnell, 4 steps, 512×512 (~48s total including load)
./build/bin/sd-cli \
  --diffusion-model models/flux/flux1-schnell-q4_k.gguf \
  --vae models/flux/ae.safetensors \
  --clip_l models/flux/clip_l.safetensors \
  --t5xxl models/flux/t5-v1_1-xxl-encoder-Q4_K_M.gguf \
  --clip-on-cpu \
  -p "a cute orange tabby cat sitting on a windowsill" \
  --steps 4 --cfg-scale 1.0 --sampling-method euler \
  -W 512 -H 512 -o output.png
```

**⚠️ sd-cli hang bug:** On GFX1013 (Cyan Skillfish), sd-cli writes the image correctly but hangs indefinitely during Vulkan resource cleanup. The workaround is to run it in the background, poll for the output file, and kill the process after the image appears. This is handled automatically by `generate-and-send.sh`.

#### SD-Turbo (fastest fallback)

```bash
# SD-Turbo, 1 step (fastest, ~2.8s)
./build/bin/sd-cli -m models/sd-turbo.safetensors \
  -p "a cute orange tabby cat sitting on a windowsill" \
  --steps 1 --cfg-scale 1.0 -o output.png

# SD-Turbo, 4 steps (better quality, ~3.6s)
./build/bin/sd-cli -m models/sd-turbo.safetensors \
  -p "a beautiful sunset over mountains, oil painting style" \
  --steps 4 --cfg-scale 1.0 -o output.png
```

### 9.4 Performance benchmarks

#### FLUX.1-schnell on Vulkan, AMD BC-250 (RADV GFX1013):

| Phase | Time | Notes |
|-------|------|-------|
| Model load | 12.5s | Diffusion Q4_K (6.5 GB) → VRAM |
| T5 conditioning | 11.0s | Q4_K_M encoder on CPU (2.9 GB RAM) |
| Sampling (4 steps) | 19.8s | ~4.9s per step |
| VAE decode | 2.3s | |
| **Total** | **~48s** | Including load; ~33s inference only |

Memory: 6,660 MB VRAM + 3,395 MB RAM = **10,055 MB total** (comfortable within 16 GB)

#### SD-Turbo on Vulkan, AMD BC-250 (RADV GFX1013):

| Resolution | Steps | VRAM | Sampling | VAE Decode | **Total** |
|------------|-------|------|----------|------------|-----------|
| 512×512 | 1 | 3668 MB | 0.48s | 2.30s | **2.83s** |
| 512×512 | 4 | 3668 MB | 1.25s | 2.30s | **3.59s** |
| 768×768 | 1 | 3668 MB | 1.03s | 5.81s | **6.89s** |

**Key observations:**
- FLUX.1-schnell produces dramatically better images than SD-Turbo, but takes ~48s vs ~3s
- FLUX Q4_K fits with ~5 GB headroom; fp16 and Q8_0 variants cause OOM on 16 GB
- T5-XXL encoder quantization matters: fp16 (9.2 GB) → OOM; Q8_0 (5 GB) → thrashing; **Q4_K_M (2.9 GB) → perfect fit**
- sd-cli hangs after generation on GFX1013 (Vulkan cleanup bug) — requires background kill workaround

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

- **OpenClaw** v2026.2.17: Gateway daemon on Node.js 22, routes messages to Ollama. Native thinking model support.
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

OpenClaw supports multiple LLM providers simultaneously. We use **local Ollama exclusively** — all 6 models run on the BC-250 via Vulkan, with no cloud fallback configured.

<!-- Updated Feb 2026: previously described Gemini cloud fallback. Current
     setup is 100% local — no API keys needed beyond the Ollama placeholder. -->

#### Environment setup

API keys go in `~/.openclaw/.env` (auto-loaded by OpenClaw, never committed):

```bash
# ~/.openclaw/.env (chmod 600)
# Only Ollama is used — no cloud API keys needed.
# The OLLAMA_API_KEY is a placeholder required by OpenClaw's provider config.
```

Also add the Ollama key to the systemd service override:

```bash
mkdir -p ~/.config/systemd/user/openclaw-gateway.service.d
cat > ~/.config/systemd/user/openclaw-gateway.service.d/ollama.conf << EOF
[Service]
Environment=OLLAMA_API_KEY=ollama-local
EOF
chmod 600 ~/.config/systemd/user/openclaw-gateway.service.d/ollama.conf
systemctl --user daemon-reload
```

#### Model routing in `~/.openclaw/openclaw.json`

<!-- Updated July 2026: upgraded to OpenClaw 2026.2.17 with native thinking model support.
     Primary model: huihui_ai/qwen3-abliterated:14b with reasoning:true and thinkingDefault:"high".
     Proxy REMOVED — no longer needed since OpenClaw 2026.2.17 handles thinking natively.
     Direct Ollama connection on port 11434. Context windows expanded to 32k for thinking model. -->

```json
{
  "models": {
    "providers": {
      "ollama": {
        "baseUrl": "http://127.0.0.1:11434",
        "apiKey": "ollama-local",
        "api": "ollama",
        "models": [
          { "id": "huihui_ai/qwen3-abliterated:14b", "name": "Qwen 3 14B Abliterated (Thinking)", "contextWindow": 16384, "maxTokens": 8192, "reasoning": true },
          { "id": "qwen3:14b",                       "name": "Qwen 3 14B",                        "contextWindow": 16384, "maxTokens": 4096 },
          { "id": "qwen3-14b-abl-nothink:latest",    "name": "Qwen 3 14B Abliterated NoThink",    "contextWindow": 16384, "maxTokens": 4096 },
          { "id": "mistral-nemo:12b",                 "name": "Mistral Nemo 12B",                  "contextWindow": 16384, "maxTokens": 4096 }
        ]
      }
    }
  },
  "agents": {
    "defaults": {
      "model": {
        "primary": "ollama/huihui_ai/qwen3-abliterated:14b",
        "fallbacks": ["ollama/qwen3:14b", "ollama/qwen3-14b-abl-nothink:latest", "ollama/mistral-nemo:12b"]
      },
      "thinkingDefault": "high",
      "timeoutSeconds": 600
    }
  }
}
```

**Thinking model:** The primary model `huihui_ai/qwen3-abliterated:14b` runs with `reasoning: true`, which enables OpenClaw 2026.2.17's native thinking support. The model produces internal reasoning (in the `thinking` field) before generating structured tool calls. `thinkingDefault: "high"` tells the model to think deeply. `timeoutSeconds: 600` allows up to 10 minutes for complex reasoning chains.

**Fallback chain:** `huihui_ai/qwen3-abliterated:14b` (14B thinking, primary) → `qwen3:14b` (14B standard) → `qwen3-14b-abl-nothink` (14B no-think) → `mistral-nemo:12b` (12B). All models are local — no cloud fallback.

**Why `huihui_ai/qwen3-abliterated:14b` as primary:** After upgrading OpenClaw to 2026.2.17 (native thinking support), the abliterated Qwen3 14B with reasoning enabled produces the best results. It thinks through problems deeply before acting, handles complex tool-calling chains reliably (~27 tok/s), and the abliterated variant avoids unnecessary refusals with zero quality loss. The proxy workaround is no longer needed.

**No proxy needed:** OpenClaw 2026.2.17 natively supports the `reasoning` flag and passes the correct `think` parameter to Ollama. The `ollama-proxy` (port 11435) has been **disabled** and is no longer required. Direct connection to Ollama on port 11434.

> **⚠️ Context window is set to 16384.** OpenClaw requires ≥16000 tokens context. At 16k, the KV cache is ~2.5 GB, leaving room for model weights (~8.4 GB for 14B Q4). Using 32768 wastes ~2.5 GB on larger KV cache — avoid on 16 GB systems. Set globally via `OLLAMA_CONTEXT_LENGTH=16384` in the Ollama systemd override. 128k OOM-kills on 16 GB systems.

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
| `TOOLS.md` | Explicit tool commands (image gen, web search, system diagnostics) |
| `HEARTBEAT.md` | Periodic check-in tasks |

These files are read at session start and injected into the system prompt. The agent can update them to build persistent memory across sessions.

### 10.5 Tool Optimization

The default OpenClaw tool set includes browser, canvas, cron, and many features that don't apply to a headless Linux server. Disabling unused tools **reduces the system prompt from ~11k to ~4k tokens**, cutting response time nearly in half.

<!-- Updated to match actual deployed config. Previously showed "messaging"
     profile with "allow" key. Actual config uses "coding" profile with
     "alsoAllow" (additive, not restrictive). See "Critical fix: tools.allow
     vs tools.alsoAllow" in Section 15 for why this matters. -->

```json
{
  "tools": {
    "profile": "coding",
    "alsoAllow": ["message", "group:messaging"],
    "deny": ["browser", "canvas", "nodes", "cron", "gateway"]
  },
  "skills": {
    "allowBundled": []
  }
}
```

This keeps: file read/write, shell exec, session management, Signal messaging (via `alsoAllow`). Disables: browser automation, canvas, macOS nodes, cron, and all 50+ bundled skills (most require macOS or specific APIs). **Important:** Use `alsoAllow` (additive), not `allow` (restrictive whitelist) — see Section 15.

### 10.6 Custom Skills

#### Image Generation via FLUX.1-schnell

The agent generates images using the local GPU via an **async two-script architecture** that solves the memory coexistence problem (SD and Ollama can't run simultaneously on 16 GB unified memory):

```
User: "draw me a cyberpunk cat"
Clawd: [calls exec → wrapper returns instantly → replies "Generating your image..."]
       [background: waits 45s → stops Ollama → runs SD → sends image → restarts Ollama]
User receives: 🎨 cyberpunk cat image via Signal (~100s total)
```

**How it works:**
1. `TOOLS.md` (injected as Project Context) tells the model the exact command
2. Model calls `exec` with `/opt/stable-diffusion.cpp/generate-and-send.sh <prompt>`
3. **Wrapper** (`generate-and-send.sh`) starts the worker in background, returns immediately
4. **Worker** (`generate-and-send-worker.sh`) orchestrates the pipeline:
   - Waits 45s for OpenClaw to finish its model response (the "generating your image" message)
   - Stops Ollama service (`systemctl stop ollama`) to fully free VRAM
   - Runs FLUX.1-schnell via sd-cli (4 steps, 512×512, ~48s)
   - Restarts Ollama service
   - Sends image via Signal JSON-RPC

**Why async?** OpenClaw's exec tool has a timeout. If the script ran synchronously (~50s), OpenClaw would report "Command still running" and try to call Ollama again — triggering model reload while SD is using the GPU → OOM kill. The async approach returns instantly, letting the model respond, then the background worker safely takes over the GPU.

**Why 45s delay?** The model needs time to generate its response ("Generating your image, it will arrive in about a minute.") before the worker stops Ollama. On cold start, model loading can take 30-60s; on warm start, response takes 10-20s. 45s covers both cases.

**Limitation:** During image generation (~50s), the bot is **offline** — Ollama is stopped. Messages sent during this window will be queued and processed after Ollama restarts.

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

With optimized tool profile (coding + alsoAllow) and thinking model:

| Scenario | Time | Notes |
|----------|------|-------|
| Cold start (first message) | ~60-90s | Model load + thinking + inference |
| Warm (model loaded, simple) | ~10-30s | Thinking + inference |
| Warm (model loaded, complex) | ~30-90s | Deep reasoning + inference |
| Image generation (FLUX) | ~48s | Including model load, kills LLM first |

### 10.11 Why OpenClaw (vs alternatives)

| Project | Language | Stars | Channels | Local LLM (Ollama) | Verdict |
|---------|----------|-------|----------|---------------------|---------|
| **OpenClaw** (official) | TypeScript | 196k | 15+ | ✅ Native, auto-discovery | **Winner** |
| Moltis | Rust | 891 | 2 (Telegram + Web) | ⚠️ Manual | Too limited |
| NanoClaw | TypeScript | 8.4k | 1 (WhatsApp) | ❌ Anthropic-only | Incompatible |

No C++ or Go ports exist. OpenClaw has first-class Ollama support with native `/api/chat` integration, streaming + tool calling, and auto-discovery of models.

---

## 11. Netscan Monitoring Ecosystem

A comprehensive research, monitoring, and intelligence system built on the BC-250. Everything lives under `/opt/netscan/` on the server. The web dashboard is at `http://192.168.3.151:8888`.

### 11.1 Architecture

```
Cron schedule → Scripts (bash/python) → Ollama (phi4:14b via Vulkan) → JSON data → generate-html.py → Dashboard
                                                                     → Signal alerts (career, watchlist)
```

The system runs **autonomously via cron** — no human intervention needed. During quiet hours (23:00–08:00), the OpenClaw gateway is stopped and the GPU is exclusively available for batch processing. During daytime, the gateway runs and cron jobs use a GPU lock to avoid conflicts with chat.

### 11.2 Scripts

| Script | Purpose | GPU? |
|--------|---------|------|
| `career-scan.py` | Two-phase career intelligence scanner (see §12) | Yes — phi4:14b |
| `idle-think.sh` | The brain — 8 task types producing JSON research notes | Yes |
| `repo-watch.sh` | Monitors 5 upstream repos (GStreamer, libcamera, v4l-utils, FFmpeg, LinuxTV) | No |
| `lore-digest.sh` | Kernel mailing list digests (linux-media, soc-bringup) | Yes |
| `ha-journal.py` | Home Assistant analysis (climate, sensors, anomalies) | Yes |
| `ha-observe.py` | Quick HA queries (weather, climate, specific sensors) | No |
| `scan.sh` | Network scan via nmap (192.168.3.0/24) | No |
| `enumerate.sh` | Deep service enumeration of discovered hosts | No |
| `vulnscan.sh` | Weekly vulnerability scan (Sundays) | No |
| `presence.sh` | Phone presence tracker (AK's phone MAC detection) | No |
| `gpu-monitor.sh` | Samples Ollama `/api/ps` every minute, logs to TSV | No |
| `watchdog.py` | Integrity checks — cron health, disk space, service status | No |
| `generate-html.py` | Builds the entire web dashboard from all data sources | No |
| `report.sh` | Morning HTML report rebuild | No |

### 11.3 Cron Schedule

#### Quiet Hours (23:00–08:00) — Gateway stopped, GPU free for batch

| Time | Script | GPU? | Notes |
|------|--------|------|-------|
| 22:55 | `systemctl --user stop openclaw-gateway` | — | Chat offline |
| 23:30 | `repo-watch.sh --all` | No | |
| 00:30 | `ha-journal.py` | Yes (locked) | |
| 01:00 | `career-scan.py` | Yes (locked) | Two-phase, 15–60 min |
| 04:00 | `scan.sh` | No | Network scan |
| 04:30 | `enumerate.sh` | No | Deep service enum |
| 05:00 | `lore-digest.sh --all` | Yes (locked) | |
| 05:30 | `vulnscan.sh` (Sundays) | No | Weekly only |
| 06:00 | `watchdog.py` | No | Full run |
| 06:30 | `idle-think.sh` | Yes (locked) | |
| 07:00 | `idle-think.sh` | Yes (locked) | |
| 08:00 | `systemctl --user start openclaw-gateway` | — | Chat online |

#### Daytime (08:00–22:59) — Signal chat active, GPU guard on

| Time | Script | GPU? | Notes |
|------|--------|------|-------|
| 08:00, 14:00 | `repo-watch.sh --all` | No | |
| 08:30 | `report.sh` | No | Morning HTML rebuild |
| 09:30, 15:30, 20:30 | `ha-journal.py` | Yes (locked) | |
| 16:00 | `idle-think.sh` | Yes (locked) | Afternoon session |
| 18:00 | `repo-watch.sh --all --notify` | No | |
| 19:00 | `idle-think.sh --task signal` | Yes (locked) | THE daily notification |

#### Always Running

| Frequency | Script | Notes |
|-----------|--------|-------|
| Every 5 min | `presence.sh` | Phone detection |
| Every 1 min | `gpu-monitor.sh` | GPU utilization log |
| Every 30 min | `watchdog.py --live-only` | Live integrity checks |

### 11.4 Data Locations

| Data | Path |
|------|------|
| Research notes | `/opt/netscan/data/think/note-<type>-<date>-<time>.json` |
| Notes index | `/opt/netscan/data/think/notes-index.json` |
| Career scans | `/opt/netscan/data/career/scan-<date>.json` |
| Latest career scan | `/opt/netscan/data/career/latest-scan.json` (symlink) |
| Repo items | `/opt/netscan/data/repos/<project>/items-<date>.json` |
| Repo digests | `/opt/netscan/data/repos/<project>/digest-<date>.json` |
| Mailing list digests | `/opt/netscan/data/lkml/digest-<date>.json`, `.../soc/...` |
| Network hosts | `/opt/netscan/data/hosts-db.json` |
| Phone presence | `/opt/netscan/data/presence-state.json` |
| GPU load log | `/opt/netscan/data/gpu-load.tsv` |
| Cron logs | `/opt/netscan/data/{think,career,ha-journal}-cron.log` |
| Watchlist | `/opt/netscan/watchlist.json` (auto-evolving interest tracker) |

### 11.5 Dashboard

The web dashboard at `http://192.168.3.151:8888` (served by nginx) provides:

- **DASHBOARD** — Overview with host count, presence status, latest notes
- **HOSTS** — All discovered network devices
- **PRESENCE** — Phone detection timeline
- **LINUX-MEDIA / SOC-BRINGUP** — Kernel mailing list digests
- **GSTREAMER / LIBCAMERA / V4L-UTILS / FFMPEG / LINUXTV** — Repo feeds
- **ISSUES** — LLM-generated issues/concerns from analysis
- **NOTES** — All idle-think research notes
- **LOAD** — GPU utilization heatmap and breakdown
- **SECURITY** — Host security scoring
- **CAREER** — Career scan results (`career.html`)
- **HISTORY** — Changelog
- **LOG** — Raw scan logs

### 11.6 Configuration Files

| File | Purpose |
|------|---------|
| `profile.json` | Public interests — tracked repos, keywords, technologies |
| `profile-private.json` | Career context — target companies, salary expectations (gitignored) |
| `watchlist.json` | Auto-evolving interest tracker — topics, sources, status |
| `digest-feeds.json` | RSS/feed URLs for mailing list digests |
| `repo-feeds.json` | Repository URLs and API endpoints for repo-watch |

### 11.7 GPU Contention & Model Eviction

The BC-250 has **16 GB unified memory** shared between CPU and GPU. Only one large LLM model can be loaded at a time.

**The problem (discovered Feb 2026):** The career-scan cron job at 01:00 uses `phi4:14b` (~10.1 GiB), but `qwen3-14b-abl-nothink` from the OpenClaw gateway was still resident in Ollama with `keep_alive: 60m`, consuming ~11 GiB. With both models, Ollama reported: *"model requires 10.1 GiB but only 9.4 GiB is available"*.

**The fix:** Explicit model eviction before preloading the career scanner's model:

```python
# Evict ALL loaded models before starting
for model in loaded_models:
    requests.post("http://localhost:11434/api/generate",
                  json={"model": model["name"], "keep_alive": 0})

# Then preload the scanner's model
requests.post("http://localhost:11434/api/generate",
              json={"model": "phi4:14b", "keep_alive": "60m"})
```

This pattern should be used by **any batch script** that needs the GPU during quiet hours.

---

## 12. Career Intelligence Scanner (`career-scan.py`)

An automated career opportunity scanner that scrapes company career pages, job boards, and intelligence sites, then uses a local LLM to analyze matches against a candidate profile.

### 12.1 Two-Phase Anti-Hallucination Architecture

<!-- Implemented Feb 2026, commit 74cfa0d. Previous single-phase approach had
     the LLM seeing the candidate profile during extraction, causing it to
     hallucinate job listings that matched the profile but didn't exist. -->

The scanner uses a **two-phase approach** to prevent LLM hallucination:

**Phase 1 — Profile-Free Extraction:** The LLM extracts job listings from raw HTML **without seeing the candidate profile**. It only receives the page content and instructions to extract structured job data. This prevents the model from "helpfully" inventing jobs that match the candidate.

**Phase 2 — Individual Job Analysis:** Each extracted job is analyzed **one at a time** against the candidate profile. The LLM scores relevance, assesses remote compatibility, and identifies matching skills. By analyzing jobs individually (not in batches), the model can't cross-contaminate results.

```
Page HTML → Phase 1 (extract, no profile) → Raw job list
                                                    ↓
Candidate Profile + Single Job → Phase 2 (analyze) → Scored match
                                                    ↓ (repeat per job)
                                              Aggregated results → JSON + Signal alerts
```

### 12.2 Scoring & Alerts

| Category | Score | Signal Alert? | Criteria |
|----------|-------|---------------|----------|
| ⚡ Hot match | ≥ 70% | ✅ Yes (up to 5 per scan) | Any location, not software houses |
| 🌍 Worth checking | 55–69% | ✅ Yes (up to 2 per scan) | Must be remote-compatible |
| Good match | 40–54% | ❌ Dashboard only | |
| Weak/No match | < 40% | ❌ Dashboard only | |

**Software house filter:** Companies like SII, GlobalLogic, Sysgo, etc. appear on the dashboard but **never trigger Signal alerts** — they're body-shopping intermediaries.

### 12.3 Salary Estimation

Deterministic Python-based estimation (no LLM) using `SALARY_TIERS` and `COMPANY_TIER_MAP`:

| Tier | Examples | B2B (PLN/mo) | UoP (PLN/mo) |
|------|----------|--------------|---------------|
| Tier 1 (FAANG) | Google, Nvidia, AMD | 45–65k | 32–48k |
| Tier 2 (Big tech) | Samsung, Qualcomm, Intel | 35–50k | 25–38k |
| Tier 3 (Good) | Ericsson, Fujitsu, Arm | 28–40k | 20–30k |
| Tier 4 (Average) | TCL Research, Thales | 22–32k | 16–24k |

### 12.4 Target Companies

| Company | Industry | Interest |
|---------|----------|----------|
| Nvidia | Silicon | Tegra, Jetson, kernel drivers |
| Google | Silicon | ChromeOS, Pixel, Android camera |
| AMD | Silicon | GPU drivers, ROCm, RDNA |
| Intel | Silicon | IPU, camera, kernel team |
| Samsung | Silicon | Exynos, camera ISP |
| Amazon | Tech | Ring, Alexa, embedded Linux |
| Qualcomm | Silicon | Snapdragon camera, MIPI CSI |
| Arm | Silicon | Mali GPU, kernel |
| Ericsson | Telecom | Platform SW, Linköping/remote |
| Fujitsu | Enterprise | Mainboard FW, Augsburg/remote |
| Thales | Defense | DIS, embedded, Kraków |
| HARMAN | Automotive | Current employer monitoring |

### 12.5 Technical Details

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Model | `phi4:14b` | Best extraction accuracy; abliterated variant avoids refusals |
| `num_ctx` | 6144 | UMA memory constraint — 16k wastes KV cache on a 16 GB system |
| `keep_alive` | 60m | Keeps model hot between pages (scan takes 15–60 min) |
| `temperature` | 0.3 | Low for deterministic extraction |
| HTTP timeout | 120s per LLM call | Some pages are large |
| HTTP 500 retry | 45s wait, 1 retry | Ollama occasionally 500s under memory pressure |
| GoWork delay | 3s between pages | Avoids 429 rate limiting (13 companies ≈ 40s) |
| Selenium | 4.41.0 + geckodriver | Installed but unused — old GoWork URLs are SSR |

### 12.6 Signal Alert Delivery

Alerts are sent via `signal-cli`'s JSON-RPC API:

```bash
curl -X POST http://127.0.0.1:8080/api/v1/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"send","params":{
    "account":"+48532825716",
    "recipient":["+48503326388"],
    "message":"⚡ Hot match (75%): Senior Embedded Linux Engineer @ Nvidia\n🏠 Remote | 💰 B2B 45-65k PLN/mo"
  },"id":"1"}'
```

### 12.7 Cron Stdout Buffering Fix

<!-- Discovered Feb 2026, commit 81b5995. The career-scan.py cron job
     produced zero output because Python's stdout was fully buffered
     when running under cron (no TTY). -->

**Problem:** `career-scan.py` produced **zero output** when run from cron. The script crashed mid-execution, but the crash message was lost because stdout was fully buffered (no TTY under cron).

**Fix:** Add at the top of the script:

```python
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)
```

This ensures every `print()` is flushed immediately, so cron log files capture output even if the script crashes.

### 12.8 Schedule

- **Nightly at 01:00** — Full scan via cron (quiet hours, GPU locked)
- Results written to `/opt/netscan/data/career/scan-YYYY-MM-DD.json`
- `latest-scan.json` symlinked to most recent scan
- Dashboard at `http://bc250:8888/career.html`

### 12.9 GoWork.pl Company Intelligence

<!-- Implemented Feb 2026. The NEW GoWork.pl is a Next.js SPA that renders
     all company reviews client-side (React Server Components). Simple HTTP
     scraping returns only the global homepage feed, not company-specific
     reviews. Even Selenium+headless Firefox fails — the page detects
     automation and doesn't load company content.
     
     SOLUTION: The OLD opinie_czytaj,{entity_id} URLs are still served
     with full server-side rendering (SSR). These pages contain actual
     review text, ratings, dates, and role types. -->

**The problem:** GoWork.pl rewrote their site as a Next.js SPA. The new `/opinie/{company};eid,{id}` URLs return a client-rendered shell — all review content is loaded via JavaScript. Neither `urllib` nor headless Firefox can extract actual reviews.

**The fix:** GoWork still serves the OLD `/opinie_czytaj,{entity_id}` format as fully server-rendered HTML. These pages contain:
- Company name, city, industry
- Overall rating (X.X/5) and total review count
- Individual reviews with dates, role type, and full text

| Company | Entity ID | Reviews | Rating |
|---------|-----------|---------|--------|
| Ericsson | 8528 | ~97 | 2.2/5 |
| Samsung | 21451047 | ~435 | 1.8/5 |
| HARMAN | 1036892 | ~161 | 2.6/5 |
| Intel | 930747 | ~336 | 2.3/5 |
| NVIDIA | 21622584 | ~6 | 2.8/5 |
| Qualcomm | 20727487 | ~0 | — |
| AMD | 26904732 | ~0 | — |
| Google | 949234 | ~65 | 2.4/5 |
| Arm | 23971017 | ~0 | — |
| Fujitsu | 365816 | ~1083 | 2.3/5 |
| Thales | 239192 | ~1633 | — |
| Amazon | 1026920 | ~918 | 1.9/5 |
| TCL Research | 23966243 | ~32 | — |

**Rate limiting:** GoWork serves 429 if fetched too fast. The scanner adds a 3-second delay between pages (~40s total for 13 companies).

**Entity ID discovery:** Old entity IDs were found via DuckDuckGo search (Google returns CAPTCHAs from servers). The IDs are NOT the same as the `eid` parameter in the new SPA URLs.

### 12.10 Persistent Company Intel Database

<!-- Implemented Feb 2026. Previously, scan_intel_sources() discarded all
     intel data after one LLM analysis call. Now GoWork review snapshots
     are stored persistently with dates for trend tracking. -->

Each nightly scan saves a GoWork snapshot to `/opt/netscan/data/career/company-intel.json`:

```json
{
  "Samsung Electronics Polska Warszawa": {
    "entity_id": "21451047",
    "url": "https://www.gowork.pl/opinie_czytaj,21451047",
    "snapshots": [
      {
        "scan_date": "2026-02-23",
        "rating": "1.8",
        "review_count": "435",
        "latest_reviews": [
          {"date": "04.02.2026", "role": "Inne", "text": "przygotujcie sie na powrót do biur 5 dni..."},
          {"date": "18.02.2026", "role": "Pracownik", "text": "Praca w tej firmie to podręcznikowy przykład pułapki lojalności..."}
        ]
      }
    ]
  }
}
```

**Features:**
- Accumulates over time — old data preserved with dates
- Keeps last 90 days of snapshots per company
- Tracks rating changes, review count growth, sentiment shifts
- Review text gives insider info on layoffs, salary, management, culture

---

## 13. Clawd Bot Workspace & Memory

OpenClaw's agent (Clawd 🦞) has a persistent workspace at `~/.openclaw/workspace/` on bc250. These files survive context compaction and define the bot's autonomous behavior and memory.

### 13.1 Workspace Files

| File | Purpose |
|------|---------|
| `WORKFLOW_AUTO.md` | **Autonomous behavior loop** — read on every context reset. Defines session start ritual, proactive behaviors, memory protocol, quiet hours, GPU awareness |
| `HEARTBEAT.md` | Periodic awareness — full cron schedule (quiet/daytime tables), role description, commands for checking fresh data |
| `ECOSYSTEM.md` | Complete netscan documentation — scripts, data locations, cron schedule, dashboard pages, how to help AK |
| `SOUL.md` | Core personality — "act, don't ask", direct, chill lobster vibes, no corporate speak |
| `IDENTITY.md` | Name ("Clawd"), creature type, emoji (🦞) |
| `USER.md` | Info about AK — timezone (Europe/Warsaw), preferences |
| `AGENTS.md` | Trimmed agent instructions (~1K chars, down from 7.8K to save context) |
| `TOOLS.md` | Explicit tool commands (image gen, web search, system diagnostics) |
| `skills/career-intel/SKILL.md` | Career scanner instructions — how to trigger scans, check results, interpret scores |
| `skills/sd-image/SKILL.md` | Image generation instructions — FLUX.1-schnell async pipeline |
| `skills/web-search/SKILL.md` | Web search via `ddgr` (DuckDuckGo CLI) |

### 13.2 Memory System

Clawd has **no built-in persistent memory** across context resets. The memory system is a convention using markdown files:

```
~/.openclaw/workspace/memory/
└── 2026-02-22.md    # Daily notes — decisions, findings, action items
```

**Protocol:**
1. After meaningful conversations, Clawd appends to `memory/YYYY-MM-DD.md`
2. On every context reset, `WORKFLOW_AUTO.md` instructs Clawd to read today + yesterday's memory files
3. This is the bot's **only persistent memory** — everything else is lost on compaction

**What gets remembered:** Career decisions, technical insights, tasks and outcomes, monitoring findings, user preferences/feedback.

**What doesn't:** Casual greetings, generic questions, image generation requests.

### 13.3 WORKFLOW_AUTO.md — The Autonomous Loop

This is the key file that OpenClaw's Post-Compaction Audit reads after every context reset. It defines what Clawd does "when waking up with amnesia":

1. **Read memory** — today + yesterday's `memory/YYYY-MM-DD.md`
2. **Read HEARTBEAT.md** — what needs periodic attention
3. **Scan fresh data** — latest career scan, research notes, cron logs
4. **Proactive behaviors:**
   - Morning briefing (08:00–10:00): check overnight data, weather, anomalies
   - Career awareness: check latest scan when asked about jobs
   - Home awareness: HA queries when asked about the house
   - Repo/kernel watch: check upstream activity when asked
5. **GPU awareness** — check `ollama/api/ps` before running heavy tasks
6. **Quiet hours** — no proactive messages 23:00–08:00

---

## 14. Repository Structure

All config files, scripts, and systemd units are tracked in this repo. Deploy to bc250 with `scp` or a simple sync script.

```
bc250/
├── Readme.md                    # This file — the full setup guide
├── career-scan.py               # → /opt/netscan/career-scan.py (two-phase career scanner)
├── openclaw.json                # → ~/.openclaw/openclaw.json (thinking model config)
├── SKILL-sd-image.md            # → ~/.openclaw/workspace/skills/sd-image/SKILL.md
├── generate-and-send.sh         # → /opt/stable-diffusion.cpp/generate-and-send.sh (async wrapper)
├── generate-and-send-worker.sh  # → /opt/stable-diffusion.cpp/generate-and-send-worker.sh (background worker)
├── openclaw-gateway.service     # → ~/.config/systemd/user/openclaw-gateway.service (ref)
├── ollama-proxy.py              # HISTORICAL — ollama-proxy (disabled since OpenClaw 2026.2.17)
├── netscan/                     # → /opt/netscan/ (monitoring ecosystem)
│   ├── career-scan.py           # Two-phase anti-hallucination career scanner
│   ├── generate-html.py         # Dashboard builder — all data → HTML
│   ├── idle-think.sh            # The brain — 8 task types, JSON research notes
│   ├── repo-watch.sh            # Upstream repo monitor (GStreamer, libcamera, etc.)
│   ├── lore-digest.sh           # Kernel mailing list digests
│   ├── ha-journal.py            # Home Assistant journal analysis
│   ├── ha-observe.py            # Quick HA queries
│   ├── scan.sh                  # Network scan (nmap)
│   ├── enumerate.sh             # Deep service enumeration
│   ├── vulnscan.sh              # Weekly vulnerability scan
│   ├── presence.sh              # Phone presence detection
│   ├── gpu-monitor.sh           # GPU utilization sampler
│   ├── watchdog.py              # Cron health, disk, service integrity
│   ├── report.sh                # Morning HTML report rebuild
│   ├── profile.json             # Public interests/keywords
│   ├── profile-private.json     # Career context (gitignored)
│   ├── watchlist.json           # Auto-evolving interest tracker
│   ├── digest-feeds.json        # Mailing list feed URLs
│   └── repo-feeds.json          # Repository API endpoints
├── openclaw/
│   ├── TOOLS.md                 # → ~/.openclaw/workspace/TOOLS.md (injected into system prompt)
│   ├── SKILL-web-search.md      # → ~/.openclaw/workspace/skills/web-search/SKILL.md
│   ├── openclaw.json            # Alternative/reference openclaw config
│   ├── skills/
│   │   ├── sd-image/
│   │   │   └── SKILL.md         # → ~/.openclaw/workspace/skills/sd-image/SKILL.md
│   │   └── web-search/
│   │       └── SKILL.md         # → ~/.openclaw/workspace/skills/web-search/SKILL.md
│   └── workspace/
│       ├── AGENTS.md            # → ~/.openclaw/workspace/AGENTS.md (trimmed to ~1K chars)
│       ├── SOUL.md              # → ~/.openclaw/workspace/SOUL.md
│       └── IDENTITY.md          # → ~/.openclaw/workspace/IDENTITY.md
├── openclaw/
│   ├── TOOLS.md                 # → ~/.openclaw/workspace/TOOLS.md (injected into system prompt)
│   ├── SKILL-web-search.md      # → ~/.openclaw/workspace/skills/web-search/SKILL.md
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
│   ├── generate.sh              # → /opt/stable-diffusion.cpp/generate.sh (SD-Turbo only)
│   └── generate-and-send.sh     # → /opt/stable-diffusion.cpp/generate-and-send.sh (old version)
├── systemd/
│   ├── ollama.service           # → /etc/systemd/system/ollama.service
│   ├── ollama.service.d/
│   │   └── override.conf        # → /etc/systemd/system/ollama.service.d/override.conf
│   └── openclaw-gateway.service # → ~/.config/systemd/user/openclaw-gateway.service (ref only)
└── test_*.png                   # Sample generated images
```

### Key deployment paths on bc250

| Repo path | Target on bc250 |
|-----------|-----------------|
| `openclaw.json` | `~/.openclaw/openclaw.json` |
| `SKILL-sd-image.md` | `~/.openclaw/workspace/skills/sd-image/SKILL.md` |
| `generate-and-send.sh` | `/opt/stable-diffusion.cpp/generate-and-send.sh` |
| `openclaw/skills/*` | `~/.openclaw/workspace/skills/*` |
| `openclaw/workspace/*` | `~/.openclaw/workspace/*` |
| `scripts/generate*.sh` | `/opt/stable-diffusion.cpp/` |
| `systemd/ollama.*` | `/etc/systemd/system/ollama.*` |

### Signal JSON-RPC discovery

The signal-cli daemon (started by OpenClaw on port 8080) exposes a **JSON-RPC 2.0** API at `/api/v1/rpc` — not REST endpoints. This is how `generate-and-send.sh` sends images:

```bash
curl -X POST http://127.0.0.1:8080/api/v1/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"send","params":{"account":"+1BOTPHONENUMBER","recipient":["+1YOURPHONENUMBER"],"message":"test","attachments":["/tmp/sd-output.png"]},"id":"1"}'
```

---

## 15. Current State & Known Issues

### What works
- **Clawd bot on Signal** — responds to messages with **deep thinking** (reasoning:true), **reliably calls tools** (exec, read, write, etc.), runs shell commands, reads/writes files, does sysadmin tasks
- **Thinking model** — `huihui_ai/qwen3-abliterated:14b` with `reasoning: true` and `thinkingDefault: "high"`. Thinks deeply before acting, produces excellent tool calls. ~27 tok/s, 100% GPU.
- **Tool calling** — Thinking model correctly uses structured tool calls through OpenClaw → Ollama (direct, no proxy). 13 tools exposed (read, edit, write, exec, process, message, sessions_*, memory_*).
- **Web search** — `ddgr` (DuckDuckGo CLI) called via `exec` tool. Agent searches web and summarizes results. BTC price queries work end-to-end.
- **Image generation (FLUX.1-schnell)** — Async two-script architecture: wrapper returns instantly, background worker stops Ollama → runs SD → sends image → restarts Ollama. 512×512 in ~48s, image delivered via Signal. **No OOM kills.**
- **Netscan monitoring ecosystem** — 14+ scripts running on cron, covering career scanning, repo monitoring, mailing list digests, Home Assistant analysis, network scanning, research note generation, and watchlist-based Signal notifications. Full dashboard at `http://bc250:8888`.
- **Career scanner (two-phase)** — Anti-hallucination architecture: Phase 1 extracts jobs without candidate profile, Phase 2 scores individually. Signal alerts for hot (≥70%) and worth-checking (55-69% + remote) matches. Salary estimation is deterministic Python, not LLM. Nightly at 01:00 via cron.
- **Bot memory system** — WORKFLOW_AUTO.md (autonomous behavior loop), HEARTBEAT.md (cron schedule awareness), ECOSYSTEM.md (full monitoring docs), memory/ directory for daily conversation notes. Clawd reads memory on every context reset.
- **Model eviction for batch jobs** — Explicit `keep_alive=0` eviction of competing models before GPU-heavy cron jobs. Solves the "qwen3 still resident, phi4 can't load" problem.
- **System diagnostics** — Full diagnostic toolkit installed: `sensors`, `htop`, `radeontop`, `perf`, `strace`, `nmap`, `iftop`, `nethogs`, `iostat`, `smartctl`, `stress-ng`, `psutil`, plus raw sysfs GPU metrics. All commands documented in TOOLS.md — the bot can self-diagnose temps, memory pressure, GPU utilization, network traffic, disk health, and run stress tests.
- **TOOLS.md approach** — Explicit command instructions injected as Project Context into system prompt. More reliable than skill-read chains for 14B models.
- **Image generation (SD-Turbo fallback)** — 512×512 in ~3s, much faster but lower quality
- **Ollama + Vulkan** — `huihui_ai/qwen3-abliterated:14b` as primary, ~27 tok/s eval speed, 100% GPU
- **No proxy needed** — OpenClaw 2026.2.17 natively handles thinking model parameters. `ollama-proxy` disabled.

### Thinking model integration (July 2026)

**OpenClaw 2026.2.17** added native thinking model support via the `reasoning: true` model flag and `thinkingDefault` agent setting. This eliminated the need for the `ollama-proxy` workaround.

The thinking model (`huihui_ai/qwen3-abliterated:14b`) produces internal reasoning in the `thinking` field, then generates structured tool calls or text responses. With `thinkingDefault: "high"`, the model takes 5-30 seconds to think through complex problems before responding. `timeoutSeconds: 600` allows up to 10 minutes for complex multi-step reasoning chains.

### Historical: qwen3 thinking mode + system prompt size (Feb 2026)

**Problem:** Web search (and all tool calling) stopped working. The agent produced empty responses ("ghost tokens") — 25-26 eval tokens with no content, no thinking, no tool_calls.

**Two root causes discovered:**

1. **qwen3:8b defaults to thinking mode** when tools are present in the request. All output tokens go to the `thinking` field while `content` remains empty. OpenClaw's `--thinking off` and model `reasoning: false` don't translate to Ollama's `think: false` API parameter.

2. **System prompt too large for 8B models.** The OpenClaw framework generates a ~15.7K char system prompt (~6.4K tokens) with 13 tool schemas. Testing showed qwen3:8b produces ghost tokens with system prompts > ~4K chars, regardless of tool count.

**Solution evolution:**
1. **Feb 2026:** Built `ollama-proxy` to inject `think: false` for vanilla qwen3 models. Used `qwen3-abl-nothink` as primary.
2. **July 2026:** Upgraded to OpenClaw 2026.2.17 with native thinking support. Switched to `huihui_ai/qwen3-abliterated:14b` with `reasoning: true`. **Proxy disabled** — no longer needed.

### Historical: Ollama API proxy (`ollama-proxy.py`)

A lightweight reverse proxy that was used on port 11435 between OpenClaw and Ollama (port 11434). **Disabled since OpenClaw 2026.2.17** — native thinking model support made it unnecessary.

- **Previous purpose:** Injected `think: false` for vanilla qwen3 models; provided request/response logging
- **Current status:** Service stopped and disabled. Code preserved in repo for reference.

### FLUX.1-schnell memory analysis

| Variant | Diffusion | T5-XXL | Total | Result |
|---------|-----------|--------|-------|--------|
| Q8_0 + fp16 t5xxl | 12 GB | 9.2 GB | 21.2 GB | ❌ OOM-killed |
| Q4_K + fp16 t5xxl | 6.5 GB | 9.2 GB | 15.7 GB | ❌ OOM-killed |
| Q4_K + Q8_0 t5xxl | 6.5 GB | 5.0 GB | 11.5 GB | ⚠️ Works with --mmap but thrashes |
| **Q4_K + Q4_K_M t5xxl** | **6.5 GB** | **2.9 GB** | **10 GB** | **✅ Comfortable fit, 5 GB headroom** |

**Key finding:** The T5-XXL text encoder quantization is the critical variable. The city96/t5-v1_1-xxl-encoder-gguf Q4_K_M variant (2.9 GB) provides the best balance of quality and memory usage.

### sd-cli Vulkan cleanup bug (GFX1013)

`sd-cli` writes images correctly but **hangs indefinitely** after generation during Vulkan resource deallocation on the BC-250's GFX1013 GPU. This is a known issue with the RADV Vulkan driver for Cyan Skillfish.

**Workaround in `generate-and-send.sh`:** Run sd-cli in background, poll for the output image file every 3 seconds, then `kill` the process once the image appears. Uses `nohup` + `disown` to survive SSH session drops during GPU-intensive generation.

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

| Model | Eval speed | Tool calling | Thinking | Notes |
|-------|-----------|--------------|----------|-------|
| **huihui_ai/qwen3-abliterated:14b** | **~27 tok/s** | ✅ Reliable | ✅ Deep reasoning | **Primary model**, thinking enabled |
| qwen3:14b | ~27 tok/s | ✅ Reliable | ✅ Available | Standard variant fallback |
| qwen3-14b-abl-nothink | ~27.5 tok/s | ✅ Reliable | ❌ Disabled | Non-thinking fallback |
| mistral-nemo:12b | ~34 tok/s | ✅ Works | ❌ N/A | Good alternative 12B option |

### Known limitations
- **Shared VRAM** — image generation requires stopping Ollama entirely (handled by async worker with 45s safety delay). Bot is offline during SD generation (~50s).
- **Single-model GPU** — Only one 14B model fits at a time. Batch jobs (career-scan, idle-think) must explicitly evict the gateway's model before loading their own. See §11.7 for the eviction pattern.
- **Cron stdout buffering** — Python scripts run by cron have fully buffered stdout (no TTY). Crashes lose all output. Fix: `sys.stdout.reconfigure(line_buffering=True)` at script start. See §12.7.
- **Chinese thinking token leakage** — The abliterated Qwen3 model occasionally outputs Chinese reasoning fragments (e.g., `句话`) even with `/no_think` in TOOLS.md. Cosmetic issue — doesn't affect functionality.
- **OpenClaw doesn't send `think: true` to Ollama** — The `createOllamaStreamFn` in model-auth-CxlTW8uU.js doesn't include the `think` parameter in the API body. Qwen3 operates in `/no_think` mode. Thinking works through the model's natural reasoning, not Ollama's explicit thinking mode.
- **sd-cli hangs on GFX1013** — Vulkan cleanup bug requires background kill workaround
- **14B memory pressure** — with 11 GB GPU + ~2 GB OS, only ~2 GB free RAM when 14B is loaded. Stable but tight.
- **FLUX generation time** — ~48s per image (including model load). Fast enough for chat but not interactive.
- **Cold start latency** — First request after Ollama restart takes 30-60s (model loading). Subsequent requests are 10-20s.
- **Career scan duration** — 15–60 min per full scan (scraping + LLM analysis). Runs at 01:00 during quiet hours to avoid impacting chat.

---

## 16. TODO

- [ ] Test concurrent requests under load
- [x] ~~Install system diagnostic tools~~ — **Done!** `htop`, `sysstat` (iostat/mpstat/sar), `strace`, `nmap`, `iftop`, `nethogs`, `iperf3`, `socat`, `ncdu`, `nload`, `radeontop`, `python3-psutil`, `powertop`, `stress-ng`, `perf`. All documented in TOOLS.md for the bot to use autonomously.
- [x] ~~Set up cron job for daily health check / greeting~~ — **Done!** Full netscan cron ecosystem running — see §11.3. Morning report at 08:30, health checks via watchdog every 30 min, Signal notification at 19:00.
- [ ] Consider reducing OpenClaw system prompt overhead (~9.6K framework chars)
- [ ] Try higher FLUX resolution (768×768) — will need more VRAM, may require further quantization
- [ ] Fix Chinese thinking token leakage — try stronger `/no_think` enforcement or model switch
- [ ] Re-enable Ollama `think: true` once OpenClaw properly handles thinking blocks separately from content
- [ ] Add cron failure detection to WORKFLOW_AUTO.md — proactively check `*-cron.log` for errors
- [ ] Weekly career summary — synthesize a week's worth of scans into a condensed Signal report
- [x] ~~Career intelligence scanner~~ — **Done!** Two-phase anti-hallucination architecture (commit 74cfa0d). Phase 1: profile-free extraction. Phase 2: individual job analysis. Signal alerts for hot (≥70%) and worth-checking (55-69% + remote). See §12.
- [x] ~~Fix career scan cron failure~~ — **Done!** (commits 81b5995, aa13b88). Three root causes: (1) stdout buffered under cron, (2) qwen3 model still resident blocking phi4, (3) alert threshold too strict (≥80 AND remote → ≥70 any location). Fixes: `line_buffering=True`, explicit model eviction, relaxed thresholds.
- [x] ~~Bot memory & workspace setup~~ — **Done!** WORKFLOW_AUTO.md, HEARTBEAT.md, ECOSYSTEM.md, career-intel SKILL.md, memory/ directory. See §13.
- [x] ~~End-to-end Signal test: text message → thinking model → response~~ — **Working!** BTC price query, general questions, all functional.
- [x] ~~End-to-end Signal test: image request → FLUX generation → image delivery~~ — **Working!** Async architecture: wrapper returns instantly, worker stops Ollama → SD → Signal → restart. No OOM.
- [x] ~~Fix image gen OOM kill~~ — **Two-script async architecture.** Wrapper (`generate-and-send.sh`) returns instantly, worker (`generate-and-send-worker.sh`) runs in background: waits 45s for model response → stops Ollama → runs SD → sends Signal → restarts Ollama.
- [x] ~~Fix web search~~ — **TOOLS.md approach.** Explicit commands injected as Project Context. More reliable than skill-read chains. Model correctly calls `ddgr` for web search.
- [x] ~~Fix skill discovery~~ — 14B model in /no_think mode can't follow OpenClaw's skill-read chain (scan available_skills → read SKILL.md → follow instructions). **Solution:** Put commands directly in TOOLS.md.
- [x] ~~Upgrade OpenClaw to 2026.2.17~~ — **Native thinking model support!** `reasoning: true` + `thinkingDefault: "high"`. Proxy no longer needed.
- [x] ~~Switch to thinking model~~ — `huihui_ai/qwen3-abliterated:14b` with `reasoning: true`. Thinks deeply (5-30s) before acting. ~27 tok/s.
- [x] ~~FLUX.1-schnell image generation~~ — **Working!** Q4_K diffusion (6.5 GB) + Q4_K_M t5xxl (2.9 GB) = 10 GB. 48s at 512×512. sd-cli hang workaround with background kill.
- [x] ~~Remove proxy dependency~~ — ollama-proxy disabled. OpenClaw 2026.2.17 handles thinking natively. Direct Ollama on port 11434.
- [x] ~~Fix web search~~ — **Two root causes:** (1) qwen3:8b thinking mode default (all tokens in `thinking` field), (2) system prompt too large for 8B models (ghost tokens). **Fix:** Switched primary to `qwen3-abl-nothink` which thinks internally before making tool calls. Built ollama-proxy to control `think` parameter per model. Trimmed AGENTS.md from 7.8K to 1K chars.
- [x] ~~Fix tool calling~~ — **Root cause found:** `tools.allow` was filtering out all tools except `message`. Changed to `tools.alsoAllow`. All models now call tools correctly through OpenClaw.
- [x] ~~Debug tool-calling proxy~~ — Built HTTP proxy to intercept OpenClaw→Ollama requests. Confirmed 0 tools sent before fix, 13 after. Proxy now disabled (was permanent at port 11435).
- [x] ~~Evaluate node-llama-cpp~~ — Same llama.cpp Vulkan backend, no perf benefit, not an OpenClaw provider. Skipped.
- [x] ~~Test abliterated models~~ — `huihui_ai/qwen3-abliterated:14b` with reasoning is now primary. Best tool-calling reliability with deep thinking.
- [x] ~~Web search setup~~ — `ddgr` installed, web-search skill created, end-to-end verified via Signal.
- [x] ~~Image delivery via Signal~~ — `generate-and-send.sh` uses signal-cli JSON-RPC (`/api/v1/rpc`) to send attachments
- [x] ~~Agent personality~~ — "Clawd" 🦞 identity, custom SOUL.md/IDENTITY.md
- [x] ~~Tool optimization~~ — `profile: "coding"` + `alsoAllow: ["message", "group:messaging"]`, stripped unused tools/skills
- [x] ~~Image generation skill~~ — Custom SKILL.md teaches agent to use generate-and-send.sh (updated for FLUX)
- [x] ~~KV cache quantization~~ — q8_0/q4_0 have **no effect on Vulkan** (f16 only). 16k is the ceiling.
- [x] ~~Signal bot fully working~~ — thinking model responds via Signal, tool calling + web search works reliably
- [x] ~~Context window tuning~~ — 128k OOM-kills (16 GB KV cache); 8k rejected by OpenClaw (min 16000); **16384 is the sweet spot** (~2.5 GB KV cache). 32768 wastes 2.5 GB on extra KV cache — stick with 16384.
- [x] ~~Model selection for OpenClaw~~ — huihui_ai/qwen3-abliterated:14b thinking primary, qwen3:14b / qwen3-14b-abl-nothink / mistral-nemo:12b fallback
- [x] ~~Integrate with OpenClaw~~ — v2026.2.17 installed, Ollama provider configured (direct, no proxy), Signal channel linked and working
- [x] ~~Signal setup~~ — signal-cli v0.13.24 native binary, linked as secondary device via QR code
- [x] ~~Test larger models (13B/14B)~~ — **SUCCESS after TTM fix!** 14B runs at 27 tok/s, 100% GPU. Root cause: default `ttm.pages_limit` capped GPU allocations at 7.4 GiB. Fix: increase to 12 GiB (3,145,728 pages). Persisted via `/etc/modprobe.d/ttm-gpu-memory.conf`.
- [x] ~~Evaluate abliterated 14B~~ — huihui_ai/qwen3-abliterated:14b shows zero quality/intelligence loss vs standard qwen3:14b. Now primary with thinking enabled.
- [x] ~~Tune GTT size~~ — `amdgpu.gttsize=12288` gives 12.5 GiB Vulkan GPU memory
- [x] ~~Disable GUI~~ — `multi-user.target` saves ~1 GB RAM
- [x] ~~Find abliterated models~~ — huihui_ai/qwen3-abliterated:14b is the winner — abliterated + thinking + 14B
- [x] ~~Image generation~~ — stable-diffusion.cpp with Vulkan: **FLUX.1-schnell** 512×512 in 48s (primary), SD-Turbo 512×512 in 2.83s (fallback)
- [x] ~~Test SDXL-Turbo / FLUX~~ — **FLUX.1-schnell works!** SDXL-Turbo skipped in favor of FLUX (much better quality). See §9 for details.
