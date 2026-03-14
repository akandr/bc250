```
 ██████╗  ██████╗       ██████╗ ███████╗ ██████╗
 ██╔══██╗██╔════╝       ╚════██╗██╔════╝██╔═████╗
 ██████╔╝██║      █████╗ █████╔╝███████╗██║██╔██║
 ██╔══██╗██║      ╚════╝██╔═══╝ ╚════██║████╔╝██║
 ██████╔╝╚██████╗       ███████╗███████║╚██████╔╝
 ╚═════╝  ╚═════╝       ╚══════╝╚══════╝ ╚═════╝
```

<div align="center">

**GPU-accelerated AI home server on an obscure AMD APU — Vulkan inference, autonomous intelligence, Signal chat**

`Zen 2 · RDNA 1.5 · 16 GB unified · Vulkan · 14B @ 27 tok/s · 337 autonomous jobs/cycle · 130 dashboard pages`

</div>

> A complete guide to running a **35B-parameter MoE LLM**, **FLUX.2 image generation**, and 337 autonomous jobs on the AMD BC-250 — an obscure APU (Zen 2 CPU + Cyan Skillfish RDNA 1.5 GPU) found in Samsung's blockchain/distributed-ledger rack appliances. Not a "crypto mining GPU," not a PS5 prototype — it's a custom SoC that Samsung used for private DLT infrastructure, repurposed here as a headless AI server with a community-patched BIOS.
>
> **March 2026** · Qwen3.5-35B MoE at 38 tok/s, FLUX.2-klein-4B at 20s/image, hardware-specific driver workarounds, memory tuning discoveries, and real-world benchmarks that aren't documented anywhere else.

> **What makes this unique:** The BC-250's Cyan Skillfish GPU (`GFX1013`) is possibly the only RDNA 1.5 silicon running production LLM inference. ROCm doesn't support it. OpenCL doesn't expose it. The only viable compute path is **Vulkan** — and even that required discovering two hidden kernel memory bottlenecks (GTT cap + TTM pages_limit) before 14B models would run.

---

## ░░ Contents

| § | Section | For | What you'll find |
|:---:|---------|-----|------------------|
| | **`PART I ─ HARDWARE & SETUP`** | | |
| [1](#1-hardware-overview) | Hardware Overview | BC-250 owners | Specs, memory architecture, power |
| [2](#2-driver--compute-stack) | Driver & Compute Stack | BC-250 owners | What works (Vulkan), what doesn't (ROCm) |
| [3](#3-ollama--vulkan-setup) | Ollama + Vulkan Setup | BC-250 owners | Install, GPU memory tuning (GTT + TTM) |
| [4](#4-models--benchmarks) | Models & Benchmarks | LLM users | Model compatibility, speed, memory budget |
| | **`PART II ─ AI STACK`** | | |
| [5](#5-signal-chat-bot) | Signal Chat Bot | Bot builders | Direct Signal chat via queue-runner, LLM tool use |
| [6](#6-image-generation) | Image Generation | Creative users | FLUX.2-klein-4B, synchronous pipeline |
| | **`PART III ─ MONITORING & INTEL`** | | |
| [7](#7-netscan-ecosystem) | Netscan Ecosystem | Home lab admins | 337 jobs, queue-runner v7, 130-page dashboard |
| [8](#8-career-intelligence) | Career Intelligence | Job seekers | Two-phase scanner, salary, patents |
| | **`PART IV ─ REFERENCE`** | | |
| [9](#9-repository-structure) | Repository Structure | Contributors | File layout, deployment paths |
| [10](#10-troubleshooting) | Troubleshooting | Everyone | Common issues and fixes |
| [11](#11-known-limitations--todo) | Known Limitations & TODO | Maintainers | What's broken, what's planned |
| [A](#appendix-a-openclaw-archive) | OpenClaw Archive | Historical | Original architecture, why we ditched it |

---

# `PART I` — Hardware & Setup

## 1. Hardware Overview

The AMD BC-250 is a custom APU originally designed for Samsung's blockchain/distributed-ledger rack appliances (not a traditional "mining GPU"). It's a full SoC — Zen 2 CPU and Cyan Skillfish RDNA 1.5 GPU on a single package, with 16 GB of on-package unified memory. Samsung deployed these in rack-mount enclosures for private DLT workloads; decommissioned boards now sell for ~$100–150 on the secondhand market, making them possibly the cheapest way to run 14B LLMs on dedicated hardware.

> **Not a PlayStation 5.** Despite superficial similarities (both use Zen 2 + 16 GB memory), the BC-250 has nothing to do with the PS5. The PS5's Oberon SoC is **RDNA 2** (GFX10.3, gfx1030+); the BC-250's Cyan Skillfish is **RDNA 1.5** (GFX10.1, gfx1013) — a hybrid architecture: GFX10.1 instruction set (RDNA 1) but with **hardware ray tracing support** (full `VK_KHR_ray_tracing_pipeline`, `VK_KHR_acceleration_structure`, `VK_KHR_ray_query`). LLVM's AMDGPU processor table lists GFX1013 as product "TBA" under GFX10.1, confirming it was never a retail part. Samsung also licensed RDNA 2 for mobile (Exynos 2200 / Xclipse 920) — that's a completely separate deal.
>
> **Why "RDNA 1.5"?** GFX1013 doesn't fit cleanly into AMD's public RDNA generations. It has the RDNA 1 (GFX10.1) ISA and shader compiler target, but includes hardware ray tracing — a feature AMD only shipped publicly with RDNA 2 (GFX10.3). This makes Cyan Skillfish a transitional/custom design, likely built for Samsung's specific workload requirements. We call it "RDNA 1.5" as a practical label.

> **BIOS and CPU governor are not stock.** The board ships with a minimal Samsung BIOS meant for rack operation. A community-patched BIOS (from [Miyconst's YouTube tutorial](https://www.youtube.com/watch?v=YLO3fYyCo2s)) enables standard UEFI features (boot menu, NVMe boot, fan control). The CPU `performance` governor is set explicitly — the stock `schedutil` governor causes latency spikes during LLM inference.

| Component | Details |
|-----------|---------|
| **CPU** | Zen 2 — 6c/12t @ 2.0 GHz |
| **GPU** | Cyan Skillfish — RDNA 1.5, `GFX1013`, 24 CUs (1536 SPs), ray tracing capable |
| **Memory** | **16 GB unified** (16 × 1 GB on-package), shared CPU/GPU |
| **VRAM** | 512 MB BIOS-carved framebuffer (same physical UMA pool — see note below) |
| **GTT** | **16 GiB** (tuned via `ttm.pages_limit=4194304`, default 7.4 GiB) |
| **Vulkan total** | **16.5 GiB** after tuning |
| **Storage** | 475 GB NVMe |
| **OS** | Fedora 43, kernel 6.18.9, headless |
| **TDP** | 220W board (between jobs: 55–60W measured, true idle w/o model: ~35W) |
| **BIOS** | Community-patched UEFI (not Samsung stock) — [Miyconst tutorial](https://www.youtube.com/watch?v=YLO3fYyCo2s) |
| **CPU governor** | `performance` (stock `schedutil` causes LLM latency spikes) |

### Unified memory is your friend (but needs tuning)

CPU and GPU share the same 16 GB physical pool (UMA — Unified Memory Architecture). The 512 MB "dedicated framebuffer" reported by `mem_info_vram_total` is carved from the *same* physical memory — it's a BIOS reservation, not separate silicon. The rest is accessible as **GTT (Graphics Translation Table)**.

> **UMA reality:** On unified memory, "100% GPU offload" means the model weights and KV cache live in GTT-mapped pages that the GPU accesses directly — there's no PCIe copy. However, it's still the same physical RAM the CPU uses. "Fallback to CPU" on UMA isn't catastrophic like on discrete GPUs (no bus transfer penalty), but GPU ALUs are faster than CPU ALUs for matrix ops.

**Two bottlenecks must be fixed:**

1. **GTT cap** — `amdgpu` driver defaults to 50% of RAM (~7.4 GiB). The legacy fix was `amdgpu.gttsize=14336` in kernel cmdline, but this is no longer needed.
2. **TTM pages_limit** — kernel TTM memory manager independently caps allocations at ~7.4 GiB. Fix: `ttm.pages_limit=4194304` (16 GiB in 4K pages). **This is the only tuning needed.**

> ✅ **GTT migration complete (March 2026):** `amdgpu.gttsize` was removed from kernel cmdline. With `ttm.pages_limit=4194304` alone, GTT grew from 14→16 GiB and Vulkan available from 14.0→16.5 GiB. The deprecated parameter was actually *limiting* the allocation.

After tuning: Vulkan sees **16.5 GiB** — enough for **14B parameter models at 40K context with Q4_0 KV cache, all inference on GPU**.

---

## 2. Driver & Compute Stack

The BC-250's `GFX1013` sits awkwardly between supported driver tiers.

| Layer | Status | Notes |
|-------|:------:|-------|
| **amdgpu kernel driver** | ✅ | Auto-detected, firmware loaded |
| **Vulkan (RADV/Mesa)** | ✅ | Mesa 25.3.4, Vulkan 1.4.328 |
| **ROCm / HIP** | ❌ | `rocblas_abort()` — GFX1013 not in GPU list |
| **OpenCL (rusticl)** | ❌ | Mesa's rusticl doesn't expose GFX1013 |

**Why ROCm fails:** GFX1013 is listed in LLVM as supporting `rocm-amdhsa`, but AMD's ROCm userspace (rocBLAS/Tensile) doesn't ship GFX1013 solution libraries. **Vulkan is the only viable GPU compute path.**

<details>
<summary>▸ Verification commands</summary>

```bash
vulkaninfo --summary
# → GPU0: AMD BC-250 (RADV GFX1013), Vulkan 1.4.328, INTEGRATED_GPU

cat /sys/class/drm/card1/device/mem_info_vram_total   # → 536870912 (512 MB)
cat /sys/class/drm/card1/device/mem_info_gtt_total    # → 15032385536 (14 GiB)
```

</details>

---

## 3. Ollama + Vulkan Setup

### 3.1 Install and enable Vulkan

```bash
curl -fsSL https://ollama.com/install.sh | sh

# Enable Vulkan backend (disabled by default)
sudo mkdir -p /etc/systemd/system/ollama.service.d
cat <<EOF | sudo tee /etc/systemd/system/ollama.service.d/override.conf
[Service]
Environment=OLLAMA_VULKAN=1
Environment=OLLAMA_HOST=0.0.0.0:11434
Environment=OLLAMA_KEEP_ALIVE=30m
Environment=OLLAMA_MAX_LOADED_MODELS=1
Environment=OLLAMA_FLASH_ATTENTION=1
Environment=OLLAMA_GPU_OVERHEAD=0
Environment=OLLAMA_CONTEXT_LENGTH=24576
OOMScoreAdjust=-1000
EOF
sudo systemctl daemon-reload && sudo systemctl restart ollama
```

> `OOMScoreAdjust=-1000` protects Ollama from the OOM killer — the model process must survive at all costs (see §3.4).

> ROCm will crash during startup — expected and harmless. Ollama catches it and uses Vulkan.

### 3.2 Tune GTT size

> ✅ **No longer needed.** The `amdgpu.gttsize` parameter was removed in March 2026. With `ttm.pages_limit=4194304` alone, GTT allocates 16 GiB (more than the old 14 GiB). Verify:

```bash
cat /sys/class/drm/card1/device/mem_info_gtt_total  # → 17179869184 (16 GiB)
# If you still have amdgpu.gttsize in cmdline, remove it:
sudo grubby --update-kernel=ALL --remove-args="amdgpu.gttsize=14336"
```

### 3.3 Tune TTM pages_limit ← *unlocks 14B models*

This was the breakthrough. Without this fix, 14B models load fine but produce HTTP 500 during inference.

```bash
# Runtime (immediate)
echo 4194304 | sudo tee /sys/module/ttm/parameters/pages_limit
echo 4194304 | sudo tee /sys/module/ttm/parameters/page_pool_size

# Persistent
echo "options ttm pages_limit=4194304 page_pool_size=4194304" | \
  sudo tee /etc/modprobe.d/ttm-gpu-memory.conf
printf "w /sys/module/ttm/parameters/pages_limit - - - - 4194304\n\
w /sys/module/ttm/parameters/page_pool_size - - - - 4194304\n" | \
  sudo tee /etc/tmpfiles.d/gpu-ttm-memory.conf
sudo dracut -f
```

### 3.4 Context window — the silent killer

Ollama allocates KV cache based on the model's declared context window. The default `qwen3-abliterated:14b` declares `num_ctx 40960` — that's **~16 GB** of KV cache + weights. While the raw numbers fit in 16 GB RAM, **TTM fragmentation** prevents the kernel from allocating contiguous pages for the KV cache, causing OOM kills or deadlocks.

**Fix:** Set `OLLAMA_CONTEXT_LENGTH=24576` in the Ollama systemd override (see §3.3). This caps all inference to 24K context regardless of model defaults.

This reduces total memory from **~16 GB** (40960 ctx) to **~12.3 GB** (24576 ctx). The standard `qwen3:14b` model is used directly — no custom Modelfile needed.

> **Why 24K?** Systematic testing (see §4.4) showed 24K is the maximum context that runs at full speed (~27 tok/s) with adequate headroom. 26K works but is 10% slower due to swap pressure. 28K+ deadlocks.

### 3.5 Swap — NVMe-backed safety net

With the model consuming 11+ GB on a 14 GB system, disk swap is essential for surviving inference peaks.

```bash
# Create 16 GB swap file (btrfs requires dd, not fallocate)
sudo dd if=/dev/zero of=/swapfile bs=1M count=16384 status=progress
sudo chattr +C /swapfile   # disable btrfs copy-on-write
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon -p 10 /swapfile

# Make permanent
echo '/swapfile none swap sw,pri=10 0 0' | sudo tee -a /etc/fstab
```

**Disable/reduce zram** — zram compresses pages in *physical* RAM, competing with the model:

```bash
sudo mkdir -p /etc/systemd/zram-generator.conf.d
echo -e '[zram0]\nzram-size = 2048' | sudo tee /etc/systemd/zram-generator.conf.d/small.conf
# Or disable entirely: zram-size = 0
```

### 3.6 Verify

```bash
sudo journalctl -u ollama -n 20 | grep total
# → total="11.1 GiB" available="11.1 GiB"  (with qwen3-14b-16k)
free -h
# → Swap: 15Gi total, ~1.4Gi used
```

### 3.7 Disable GUI (saves ~1 GB)

```bash
sudo systemctl set-default multi-user.target && sudo reboot
```

### 3.8 CPU governor — lock to `performance`

The stock `schedutil` governor down-clocks during idle, causing 50–100ms latency spikes at inference start. Lock all cores to full speed:

```bash
# Runtime (immediate)
echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor

# Persistent (systemd-tmpfiles)
echo 'w /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor - - - - performance' | \
  sudo tee /etc/tmpfiles.d/cpu-governor.conf
```

### Memory layout after tuning

**16 GB Unified Memory**

| Region | Size | Notes |
|--------|------|-------|
| VRAM carveout | 512 MB | BIOS-reserved from UMA pool (not separate memory) |
| GTT | **16 GiB** | Tuned via `ttm.pages_limit=4194304` (default 7.4 GiB). `amdgpu.gttsize` removed — no longer needed. |
| TTM pages_limit | **16 GiB** | `ttm.pages_limit=4194304` — the only memory tuning parameter needed |

| Vulkan heap | Size |
|-------------|------|
| Device-local | 8.33 GiB |
| Host-visible | 8.17 GiB |
| **Total** | **16.5 GiB** → 14B models fit, all inference on GPU (UMA — same physical pool) |

| Consumer | Usage | Notes |
|----------|-------|-------|
| Model weights (qwen3:14b) | 8.2 GiB GPU + 0.4 GiB CPU | Q4_K_M quantization |
| KV cache (FP16 @ 24K) | 3.8 GiB | With Q4_0: only 1.8 GiB for 40K context |
| Compute graph | 0.17 GiB | GPU-side |
| signal-cli + queue-runner | ~1.0 GiB | System RAM |
| OS + services | ~0.9 GiB | Headless Fedora 43 |
| NVMe swap | 16 GiB (374 MB used) | Safety net |
| zram | 0 B (allocated, not active) | Device exists but disksize=0 |
| **Total loaded** | **12.5 GiB** (FP16) / **10.6 GiB** (Q4_0) | **3.9–5.9 GiB free** |

---

## 4. Models & Benchmarks

### 4.1 Compatibility table

> Ollama 0.18.0 · Vulkan · RADV Mesa 25.3.4 · 16.5 GiB Vulkan · FP16 KV · March 14 2026

| Model | Params | Quant | tok/s | Prefill | Max Ctx | VRAM @4K | GPU | Status |
|-------|:------:|:-----:|:-----:|:-------:|:-------:|:--------:|:---:|--------|
| **qwen3.5-35b-a3b-iq2m** | **35B/3B** | **UD-IQ2_M** | **38** | **75** | **16K** | **12.3 GiB** | **100%** | **🏆 Smartest — MoE** |
| **qwen3.5:9b** | **9.7B** | **Q4_K_M** | **32** | **111** | **65K** | **8.6 GiB** | **100%** | **🏆 Best context+vision** |
| qwen2.5:3b | 3.1B | Q4_K_M | **104** | — | **64K** | 3.4 GiB | 100% | ✅ Fast, lightweight |
| qwen2.5:7b | 7.6B | Q4_K_M | **56** | — | **64K** | 6.5 GiB | 100% | ✅ Great quality/speed |
| qwen2.5-coder:7b | 7.6B | Q4_K_M | **56** | — | **64K** | 6.4 GiB | 100% | ✅ Code-focused |
| llama3.1:8b | 8.0B | Q4_K_M | **52** | — | **48K** | 11.0 GiB | 100% | ✅ Fast 8B |
| mannix/llama3.1-8b-lexi | 8.0B | Q4_0 | **51** | — | **48K** | 10.6 GiB | 100% | ✅ Uncensored 8B |
| huihui_ai/seed-coder-abliterate | 8.3B | Q4_K_M | **52** | — | **64K** | 9.1 GiB | 100% | ✅ Code gen, uncensored |
| qwen3:8b | 8.2B | Q4_K_M | **44** | — | **64K** | 9.8 GiB | 100% | ✅ Thinking mode |
| huihui_ai/qwen3-abliterated:8b | 8.2B | Q4_K_M | **46** | — | **64K** | 9.7 GiB | 100% | ✅ Abliterated 8B |
| gemma2:9b | 9.2B | Q4_0 | **38** | — | **48K** | 9.2 GiB | 100% | ✅ Fixed! (was 91%) |
| mistral-nemo:12b | 12.2B | Q4_0 | **34** | — | **24K** | 10.8 GiB | 100% | ⚠️ 32K deadlocks |
| qwen3:14b | 14.8B | Q4_K_M | **27** | **60** | **24K** | 13.5 GiB | 100% | ✅ Previous primary |
| huihui_ai/qwen3-abliterated:14b | 14.8B | Q4_K_M | **28** | — | **24K** | 11.4 GiB | 100% | ✅ Abliterated |
| phi4:14b | 14.7B | Q4_K_M | **29** | — | **40K** | 11.8 GiB | 100% | 🏆 Best 14B context |
| Qwen3-30B-A3B (Q2_K) | 30.5B | Q2_K | **61** | — | **16K** | 11.5 GiB | 100% | ⚠️ MoE fast, heavy quant |
| qwen3.5-27b-iq2m | 26.9B | IQ2_M | **0** | — | — | 13.5 GiB | 100% | ❌ Non-functional¹ |

> ¹ **Why 27B dense fails:** The dense architecture requires all 27B parameters in every forward pass. Without matrix cores (GFX1013 has none), each token requires ~27B multiplications through general-purpose shader cores. Result: 0 tokens generated in 5 minutes. The 35B MoE with only 3B active params per token avoids this entirely — compute is ~9× less per token despite having more total knowledge stored.

> **March 14 — Qwen3.5 era:** Ollama upgraded 0.16.1→0.18.0 (required for Qwen3.5). The **qwen3.5-35b-a3b MoE** (35B total, 3B active per token) at IQ2_M quantization is now the smartest model on BC-250: 38 tok/s, 75 tok/s prefill, 16K context, multimodal (vision+tools+thinking). The **qwen3.5:9b** provides 65K context with vision when longer documents are needed. Both are Qwen3.5 architecture — dramatically newer than Qwen3.

### 4.2 Benchmark visualization

**Generation speed (tok/s) — higher is better:**

```
Model                    tok/s    Max Ctx   ██ = 10 tok/s
─────────────────────────────────────────────────────────
qwen2.5:3b               104      64K  ██████████▌
Qwen3-30B-A3B Q2_K        61      16K  ██████▏
qwen2.5:7b                56      64K  █████▌
qwen2.5-coder:7b          56      64K  █████▌
llama3.1:8b                52      48K  █████▏
seed-coder-abl:8b          52      64K  █████▏
lexi-8b (uncensored)      51      48K  █████
qwen3-abl:8b              46      64K  ████▌
qwen3:8b                  44      64K  ████▍
★ qwen3.5-35b-a3b MoE     38      16K  ███▊  ← SMARTEST (35B/3B)
gemma2:9b                 38      48K  ███▊
★ qwen3.5:9b               32      65K  ███▏  ← best ctx + vision
mistral-nemo:12b          34      24K  ███▍
phi4:14b                  29      40K  ██▉
qwen3-abl:14b             28      24K  ██▊
qwen3:14b                 27      24K  ██▋
qwen3.5-27b (dense)        0       —   ❌ non-functional
```

**Context ceiling per model (FP16 KV, all GPU):**

```
Model             4K   8K  16K  24K  32K  48K  64K
────────────────────────────────────────────────────
qwen2.5:3b        ✅   ✅   ✅   ✅   ✅   ✅   ✅
qwen2.5:7b        ✅   ✅   ✅   ✅   ✅   ✅   ✅
qwen2.5-coder:7b  ✅   ✅   ✅   ✅   ✅   ✅   ✅
qwen3:8b          ✅   ✅   ✅   ✅   ✅   ✅   ✅
qwen3-abl:8b      ✅   ✅   ✅   ✅   ✅   ✅   ✅
seed-coder:8b     ✅   ✅   ✅   ✅   ✅   ✅   ✅
★ qwen3.5:9b      ✅   ✅   ✅   ✅   ✅   ✅   ✅
llama3.1:8b       ✅   ✅   ✅   ✅   ✅   ✅   ❌
lexi-8b           ✅   ✅   ✅   ✅   ✅   ✅   ❌
gemma2:9b         ✅   ✅   ✅   ✅   ✅   ✅   —
mistral-nemo:12b  ✅   ✅   ✅   ✅   ❌   —    —
qwen3:14b         ✅   ✅   ✅   ✅   ❌   —    —
qwen3-abl:14b     ✅   ✅   ✅   ✅   ❌   —    —
phi4:14b          ✅   ✅   ✅   ✅   ✅   —    —
★ 35B-A3B iq2m    ✅   ✅   ✅   ❌   —    —    —
30B-A3B Q2_K      ✅   ✅   ✅   ❌   —    —    —
qwen3.5-27b iq2m  ❌   —    —    —    —    —    —
```

> ✅ = works 100% GPU | ❌ = timeout/deadlock | — = not tested (too large)

**Key insight:** Speed is constant across context sizes with FP16 KV (speed only degrades when the context is actually *filled* — see §4.5). The context ceiling is purely a memory constraint: weights + KV cache + compute graph must fit in 16.5 GiB.

### 4.3 Model testing journey

The path to running 14B models on this hardware was non-trivial. Here's the chronological evolution, documented through git history and trial-and-error:

```
  Feb 17 ─── Initial setup: Ollama + Vulkan on BC-250
  │          Only 7–8B models worked. 14B loaded but hung during inference.
  │          → Committed: dfc9179 "BC-250 setup: Ollama+Vulkan, OpenClaw+Signal"
  │
  Feb 18 ─── THE BREAKTHROUGH: TTM pages_limit discovery
  │          Found kernel TTM memory manager secretly caps GPU allocs at 50% RAM.
  │          Fix: ttm.pages_limit=3145728 (12 GiB) → 14B models compute!
  │          → Committed: bbe052f "unlock 14B models via TTM fix"
  │          Results: qwen3-14b-abl-nothink 27.5 tok/s, mistral-nemo:12b 34.4 tok/s
  │
  Feb 18 ─── Image generation: FLUX.1-schnell via sd.cpp + Vulkan
  │          512×512 in 48s, 4 steps. GFX1013 bug: hangs after write → poll+kill.
  │          → Committed: 339a936 "FLUX.1-schnell image gen"
  │
  Feb 22 ─── Single model decision: qwen3-abliterated:14b only
  │          Eliminated fallback chains (caused timeout doom loops).
  │          → Committed: c4a2599 "Single model, no fallbacks"
  │
  Feb 25 ─── Context window experiment: 16K → 24K
  │          Enabled flash attention. KV cache 3.8 GB, weights 8 GB = 12.3 GB.
  │          → Committed: 4c01574 "enable flash attention, bump context 16384→24576"
  │
  Feb 26 ─── REVERT: 24K context causes deadlock ❌
  │          12.3 GB total exceeded headroom. Weights spilled to CPU (417 MB),
  │          Vulkan inference hung. 140 consecutive HTTP 500 errors over 8 hours.
  │          → Committed: 4b6836f "revert num_ctx 24576→16384"
  │
  Feb 26 ─── Conservative: drop to 12K context
  │          Saves 640 MiB KV cache. Extra safety margin.
  │          → Committed: d85a823 "num_ctx 16384→12288"
  │
  Mar 5 ──── v7: Remove OpenClaw gateway, free 700 MB RAM
  │          Bumped GTT 12→14 GiB, TTM 3M→4M pages. Context back to 16K.
  │          → Committed: 4f41926 "v7: Replace OpenClaw with standalone Signal"
  │
  Mar 7 ──── Tested phi4:14b, Qwen3-30B-A3B (Q2_K), seed-coder
  │          phi4: 25 tok/s, good reasoning but slower than qwen3.
  │          30B MoE: fits at Q2_K (11 GB) but ~12 tok/s, heavy quality loss.
  │          seed-coder: decent for code, 52 tok/s, but not general-purpose.
  │          Decision: keep qwen3:14b as primary. ✅
  │
  Mar 10 ─── Context window re-test: 16K → 24K ✅
  │          v7 freed 700 MB + 16 GB GTT = enough headroom for 24K.
  │          Tested 16K–32K in 2K steps. 24K: full speed (26.7 t/s), 1.5 GB free.
  │          26K: 10% slower. 28K+: deadlocks. Production bumped to 24K.
  │
  Mar 14 ─── COMPREHENSIVE BENCHMARK: 14 models × 7 context sizes
             GTT migration (14→16 GiB) + Vulkan 14.0→16.5 GiB unlocked dramatically
             higher context ceilings. Full automated benchmark of all models.
             KEY FINDINGS:
             • gemma2:9b: now 100% GPU at 38 tok/s (was 91% spill, 26 tok/s)
             • phi4:14b: 29 tok/s at 40K context — best 14B for long context
             • 30B MoE: actually 61 tok/s (not 12) at 16K — MoE sparse activation
             • 7-8B models: 64K context on all qwen variants
             • FLUX.1-schnell: 56s @512², 91s @768², 146s @1024² (with tiling)
             • FLUX.1-dev: 279s @512² but fails at 768² (guidance model too large)
             • SD-Turbo: 11s @512², 21s @768² (fast but low quality)
             • sd-cli bug: must use --diffusion-model not -m for FLUX GGUF files
  │
  Mar 14 ─── QWEN3.5 ERA: smartest model found ★
             Upgraded Ollama 0.16.1→0.18.0 (required for Qwen3.5).
             Tested 4 Qwen3.5 variants:
             ✅ qwen3.5:9b: 32 tok/s, 65K context, multimodal (vision+tools+thinking)
             ✅ qwen3.5-35b-a3b-iq2m (MoE): 38 tok/s, 16K ctx — SMARTEST MODEL
                35B total params, only 3B active per token. MoE wins on GFX1013
                because no matrix cores means fewer active params = faster inference.
             ❌ qwen3.5-27b-iq2m (dense): 0 tok/s — completely non-functional
                27B dense params × no matrix cores = 0 tokens in 5 minutes
             Switched production from qwen3:14b (27 tok/s, 24K) to dual-model:
                Primary: 35B-A3B MoE (38 tok/s, 16K) — intelligence king
                Fallback: qwen3.5:9b (32 tok/s, 65K) — when context > 16K needed
  │
  Mar 14 ─── FLUX.2-klein-4B: fastest image gen ever ★
             Downloaded FLUX.2-klein-4B (2.3 GB diffusion + 2.4 GB Qwen3-4B encoder
             + 321 MB FLUX.2 VAE). Results vs FLUX.1-schnell at same prompt:
             512×512: 20s vs 30s (1.5× faster, 40% less VRAM!)
             768×768: 30s vs 91s (3× faster!)
             1024×1024: 63s (never worked with schnell at all!)
             Set as new default for all Signal image generation.
```

### 4.4 Context window experiments

The context window directly controls KV cache size, and on 16 GB unified memory, every megabyte counts. After v7 (OpenClaw removal freed ~700 MB, GTT bumped to 14 GB), we re-tested all context sizes systematically:

**Context window vs memory (qwen3:14b Q4_K_M, flash attention, 16 GB GTT)**

| Context | RAM Used | Free | Swap | Speed | Status |
|--------:|---------:|-----:|-----:|------:|--------|
| 8192 | ~9.5 GB | 6.5 GB | — | ~27 t/s | ✅ Safe |
| 12288 | ~10.3 GB | 5.7 GB | — | ~27 t/s | ✅ Conservative |
| 16384 | ~11.1 GB | 4.9 GB | — | ~27 t/s | ✅ Comfortable |
| 18432 | ~13.2 GB | 2.7 GB | 0.9 GB | 26.8 t/s | ✅ Works |
| 20480 | ~13.7 GB | 2.3 GB | 0.9 GB | 26.8 t/s | ✅ Works |
| 22528 | ~14.0 GB | 2.0 GB | 0.9 GB | 26.7 t/s | ✅ Works |
| **24576** | **~14.4 GB** | **1.5 GB** | **0.9 GB** | **26.7 t/s** | **✅ Production** |
| 26624 | ~14.6 GB | 1.3 GB | 1.0 GB | 23.9 t/s | ⚠️ 10% slower |
| 28672 | ~14.2 GB | — | 1.7 GB | timeout | ❌ Deadlocks |
| 32768 | ~15.7 GB | 0.2 GB | 2.1 GB | timeout | ❌ Deadlocks |
| 40960 | ~16.0 GB | 0 | — | — | 💀 TTM fragmentation¹ |

> **24K is the sweet spot** — full speed (~27 tok/s), leaves ~1.5 GB for OS/services with stable swap at 0.9 GB. 26K works but inference drops 10% due to swap pressure. 28K+ deadlocks under Vulkan.
>
> ¹ **Why 40K fails isn't raw OOM.** The math: 9.3 GB weights + 2 GB KV cache + 1 GB OS ≈ 12.3 GB < 16 GB available. The actual failure is **TTM fragmentation** — the kernel's TTM memory manager can't allocate a contiguous block large enough for the KV cache because physical pages are fragmented across GPU and CPU consumers. This is a UMA-specific problem: on discrete GPUs with dedicated VRAM, fragmentation doesn't cross the PCIe boundary.

> **History:** The original 24K experiment (Feb 25) deadlocked because OpenClaw gateway consumed ~700 MB. After v7 removed OpenClaw and bumped GTT to 14 GB (Mar 5), 24K became stable. Flash attention (`OLLAMA_FLASH_ATTENTION=1`) is essential — without it, 24K would not fit.

### 4.5 KV cache quantization — breaking the context ceiling

**UPDATE (March 2026):** KV cache quantization **WORKS on Vulkan**. Our README previously stated it was a no-op — that was wrong. Tested on Ollama 0.16.1 + RADV Mesa 25.3.4:

| KV Type | 24K ctx | 32K ctx | 48K ctx | KV Cache Size @24K | Gen tok/s | Notes |
|---------|:-------:|:-------:|:-------:|:------------------:|:---------:|-------|
| **FP16** (default) | ✅ | ⚠️ 10% slow | ❌ deadlock | ~3.8 GiB | 27.2 | Current production |
| **Q8_0** | ✅ | ✅ | ✅ | **2.0 GiB** | 27.3 | Conservative upgrade |
| **Q4_0** | ✅ | ✅ | ✅ | **1.1 GiB** | 27.3 | ← recommended |

**KV cache scaling (Q4_0): ~45 MiB per 1K tokens** (16K=720M, 24K=1.1G, 40K=1.8G).

**Extreme context tests (Q4_0):** Ollama's scheduler auto-sizes KV to what fits in VRAM. With 14.5 GiB available, model weights 8.2 GiB, the maximum KV allocation is **~40K tokens** (1.8 GiB). Requesting larger `num_ctx` is accepted but the runner silently caps and truncates prompts to the actual KV limit.

**Generation speed degrades with context fill (Q4_0, all layers on GPU):**

| Tokens in context | Gen tok/s | Prefill tok/s | Notes |
|:-----------------:|:---------:|:-------------:|-------|
| ~100 (empty) | 27.2 | 58 | Headline number |
| 3,300 | 24.6 | 113 | Typical Signal chat |
| 10,000 | 20.7 | 70 | Long job output |
| 30,000 | **13.4** | 53 | Heavy document analysis |
| 40,960 (max fill) | **~10*** | ~42 | Theoretical, near KV limit |

\* *Estimated from degradation curve. One test at 41K showed 1.2 tok/s, but that was caused by model partial offload (21/41 layers spilled to CPU), not normal operation.*

**Q8_0 ceiling:** Fits up to ~64K context on GPU. At 80K, KV cache spills to CPU (7 tok/s — unusable). Non-deterministic — depends on memory state at load time.

**To enable (recommended production config):**
```bash
# Add to /etc/systemd/system/ollama.service.d/override.conf:
Environment=OLLAMA_KV_CACHE_TYPE=q4_0
# Then: sudo systemctl daemon-reload && sudo systemctl restart ollama
# Ollama will auto-size KV to ~40K tokens (1.8 GiB)
```

> **Quality note:** Q8_0 is virtually lossless for KV cache. Q4_0 may degrade output quality on complex reasoning — needs quality evaluation. For production, Q4_0's 40K context with 13 tok/s at 30K fill is the practical sweet spot.

### 4.6 Prefill (prompt evaluation) benchmarks

On UMA, both prefill and generation share memory bandwidth (~51 GB/s DDR4-3200). Prefill is the time the model spends "reading" the prompt before generating the first token.

**Prefill rate vs prompt size (qwen3:14b Q4_K_M, FP16 KV cache, 24K context):**

| Prompt Size | Tokens | Prefill | Gen tok/s | TTFT (warm) |
|-------------|:------:|--------:|----------:|------------:|
| Tiny | 86 | 88 tok/s | 27.2 | ~1s |
| Short | 353 | 67 tok/s | 27.2 | ~5s |
| Medium | 1,351 | 128 tok/s | 26.1 | ~11s |
| Long | 3,354 | 113 tok/s | 24.6 | ~30s |
| XL | 6,686 | 88 tok/s | 22.5 | ~76s |
| Massive | 10,014 | 70 tok/s | 20.7 | ~143s |

> **Observations:** Prefill peaks at 128 tok/s for medium prompts, then degrades with context length — likely attention computation scaling (O(n²)) plus UMA bandwidth saturation. Generation rate also degrades: 27.2 tok/s with small context → 20.7 tok/s at 10K tokens. This means real-world Signal chat (3K system prompt + conversation) runs at ~24–25 tok/s, not the headline 27 tok/s.

### 4.2 Memory budget

**qwen3.5-35b-a3b-iq2m · headless server (from Ollama logs)**

| Component | MoE @4K ctx | MoE @16K ctx | Notes |
|-----------|:----------:|:------------:|-------|
| Model weights (GPU) | 10.3 GiB | ~8.2 GiB | 41/41 layers on Vulkan0; spills to CPU at higher ctx |
| Model weights (CPU) | 0.3 GiB | ~0.4 GiB | Spilled layers + embeddings |
| KV cache (GPU) | **1.6 GiB** | **~3.8 GiB** | Grows ~0.4 GiB per 1K tokens |
| Compute graph | ~0.2 GiB | ~0.2 GiB | GPU-side |
| **Ollama total** | **12.3 GiB** | **~12.5 GiB** | Ollama dynamically spills weights to make room for KV |
| OS + services | ~0.9 GiB | ~0.9 GiB | Headless Fedora 43 |
| **Free (of 16.5 Vulkan)** | **~4.2 GiB** | **~4.0 GiB** | |
| NVMe swap | 16 GiB | | Safety net |

> **MoE memory dynamics:** As context grows, Ollama intelligently spills weight layers from GPU to CPU to maintain a ~12.5 GiB total. The MoE's total weight (11 GB GGUF) is larger than qwen3:14b (9.3 GB), but only 3B params activate per token — so CPU-spilled layers that aren't selected experts cause zero compute penalty. At 24K+ context, the KV cache exceeds what can fit alongside the weights, causing OOM or timeout.

### 4.3 Model recommendations

**Qwen3.5** is the latest generation — multimodal (vision + tools + thinking), Apache 2.0.

| Use Case | Recommended Model | tok/s | Max Ctx | Why |
|----------|-------------------|:-----:|:-------:|-----|
| **🏆 General AI / smartest** | qwen3.5-35b-a3b-iq2m | 38 | 16K | 35B knowledge, 3B active, fastest reasoning |
| **🏆 Long context / vision** | qwen3.5:9b | 32 | **65K** | Multimodal, perfect context scaling, vision |
| **Long context (14B)** | phi4:14b | 29 | **40K** | Only 14B that reaches 40K context |
| **Fast batch jobs** | qwen2.5:7b | 56 | 64K | 2× faster than 14B, 64K context |
| **Code generation** | qwen2.5-coder:7b | 56 | 64K | Same speed as base, code-specialized |
| **Speed-critical** | qwen2.5:3b | 104 | 64K | 4× faster, use for simple tasks |
| **Previous primary** | qwen3:14b (abliterated) | 28 | 24K | Replaced by Qwen3.5 models |

> **Production dual-model config:** `qwen3.5-35b-a3b-iq2m` as primary with `OLLAMA_CONTEXT_LENGTH=16384`. For tasks needing >16K context or vision (image analysis), switch to `qwen3.5:9b` which handles 65K context and can process images.
>
> The MoE wins over the 9B dense model in generation speed (38 vs 32 tok/s) because only 3B parameters activate per token on hardware without matrix cores — fewer multiplications wins. The 9B wins in prefill speed (111 vs 75 tok/s) and context capacity (65K vs 16K) because its smaller total weight leaves more room for KV cache.

```bash
# Primary model (smartest, 35B MoE) — custom GGUF via Modelfile
# See tmp/Modelfile-qwen35-35b-a3b for setup
ollama create qwen3.5-35b-a3b-iq2m -f Modelfile-qwen35-35b-a3b

# High-context model (vision+65K, official Ollama)
ollama pull qwen3.5:9b

# Context is capped via OLLAMA_CONTEXT_LENGTH=16384 in systemd (see §3.3, §3.4)
# Individual requests can override with {"options": {"num_ctx": 65536}} when using 9b
```

---

# `PART II` — AI Stack

## 5. Signal Chat Bot

The BC-250 runs a personal AI assistant accessible via Signal messenger — no gateway, no middleware. signal-cli runs as a standalone systemd service exposing a JSON-RPC API, and queue-runner handles all LLM interaction directly.

```
  Signal --> signal-cli (JSON-RPC :8080) --> queue-runner --> Ollama --> GPU (Vulkan)
```

> **Software:** signal-cli v0.13.24 (native binary) · Ollama 0.16+ · queue-runner v7

### 5.1 Why not OpenClaw

OpenClaw was the original gateway (v2026.2.26, Node.js). It was replaced because:

| Problem | Impact |
|---------|--------|
| **~700 MB RSS** | On a 16 GB system, that's 4.4% of RAM wasted on a routing layer |
| **15+ second overhead per job** | Agent turn setup, tool resolution, system prompt injection — for every cron job |
| **Unreliable model routing** | Fallback chains and timeout cascades caused 5-min "fetch failed" errors |
| **No subprocess support** | Couldn't run Python/bash scripts directly — had to shell out through the agent |
| **9.6K system prompt** | Couldn't be trimmed below ~4K tokens without breaking tool dispatch |
| **Orphan processes** | signal-cli children survived gateway OOM kills, holding port 8080 |

The replacement: queue-runner talks to signal-cli and Ollama directly via HTTP APIs. Zero middleware.

> See [Appendix A](#appendix-a--openclaw-archive) for the original OpenClaw configuration.

### 5.2 signal-cli service

signal-cli runs as a standalone systemd daemon with JSON-RPC:

```ini
# /etc/systemd/system/signal-cli.service
[Unit]
Description=signal-cli JSON-RPC daemon
After=network.target

[Service]
Type=simple
ExecStart=/opt/signal-cli/bin/signal-cli --output=json \
  -u +<BOT_PHONE> jsonRpc --socket http://127.0.0.1:8080
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Register a separate phone number for the bot via `signal-cli register` or `signal-cli link`.

### 5.3 Chat architecture

Between every queued job, `queue-runner.py` polls the signal-cli journal for incoming messages:

```
queue-runner v7 — continuous loop

  job N  →  check Signal inbox  →  chat (if msg)  →  job N+1
                    |                     |
                    v                     v
            journalctl -u           Ollama /api/chat
            signal-cli              (16K ctx, /think)
                    |                     |
                    |               EXEC cmd ← tool use
                    |                     |
                    v                     v
            signal-cli              signal-cli
            JSON-RPC :8080          send reply
```

**Key parameters:**

| Setting | Value | Purpose |
|---------|:-----:|---------|
| `SIGNAL_CHAT_CTX` | 16384 | MoE model context window (use 65K for 9b fallback) |
| `SIGNAL_CHAT_MAX_EXEC` | 3 | Max shell commands per message (search → fetch → verify) |
| `SIGNAL_EXEC_TIMEOUT_S` | 30 | Per-command timeout |
| `SIGNAL_MAX_REPLY` | 1800 | Signal message character limit |

### 5.4 Tool use — EXEC

The LLM can request shell commands via `EXEC(command)` in its response. queue-runner intercepts these, runs them, feeds stdout back into the conversation, and lets the LLM synthesize a final answer:

```
  User: "what's the disk usage?"
  LLM:  [thinking...] EXEC(df -h /)
  Runner: executes → feeds output back
  LLM:  "Root is 67% full, 48G free on your 128GB NVMe."
```

Supported patterns: web search (`ddgr`), file reads (`cat`, `head`), system diagnostics (`journalctl`, `systemctl`, `df`, `free`), data queries (`jq` on JSON files). Up to 3 commands per turn.

### 5.5 Image generation via chat

When the LLM detects an image request, it emits `EXEC(/opt/stable-diffusion.cpp/generate-and-send "prompt")`. queue-runner intercepts this pattern and handles it synchronously:

1. Stop Ollama (free GPU VRAM)
2. Run sd-cli with FLUX.2-klein-4B (4 steps, 512×512, ~20s)
3. Send image as Signal attachment
4. Restart Ollama

Bot is offline during generation (~25–40s total including model reload).

> ⚠️ **GFX1013 bug:** sd-cli hangs after writing the output image (Vulkan cleanup). queue-runner polls for the file and kills the process.

### 5.6 Personality — "Clawd"

The system prompt defines a cynical, darkly funny personality ("House MD meets a sysadmin lobster"). Key traits:
- Direct, no corporate speak, no disclaimers
- Dark humor about the hardware constraints
- Full access to `/opt/netscan/data/` for grounded answers
- Knows AK's professional context (embedded Linux, camera drivers, V4L2/libcamera)
- Uncensored creative generation (abliterated model, no safety filters)

The personality is baked into `queue-runner.py`'s `SYSTEM_PROMPT` — no external workspace files needed.

### 5.7 Response times

| Scenario | Latency |
|----------|:-------:|
| Text reply (warm) | 10–30s |
| Complex reasoning with tool use | 30–90s |
| Image generation (FLUX.2-klein 512²) | ~20s |
| Cold start (model reload) | 30–60s |

---

## 6. Image Generation

Stable Diffusion via [stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp) with native Vulkan backend.

<details>
<summary>▸ Build from source</summary>

```bash
sudo dnf install -y vulkan-headers vulkan-loader-devel glslc git cmake gcc g++ make
cd /opt && sudo git clone --recursive https://github.com/leejet/stable-diffusion.cpp.git
sudo chown -R $(whoami) /opt/stable-diffusion.cpp && cd stable-diffusion.cpp
mkdir -p build && cd build && cmake .. -DSD_VULKAN=ON -DCMAKE_BUILD_TYPE=Release
make -j$(nproc)
```

</details>

### 6.1 Models

**FLUX.2-klein-4B** — recommended, fastest, Apache 2.0:

```bash
mkdir -p /opt/stable-diffusion.cpp/models/flux2 && cd /opt/stable-diffusion.cpp/models/flux2
# Diffusion model (4B, Q4_0, 2.3 GB)
curl -L -O "https://huggingface.co/leejet/FLUX.2-klein-4B-GGUF/resolve/main/flux-2-klein-4b-Q4_0.gguf"
# Qwen3-4B text encoder (Q4_K_M, 2.4 GB)
curl -L -o qwen3-4b-Q4_K_M.gguf "https://huggingface.co/unsloth/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf"
# FLUX.2 VAE (321 MB) — different from FLUX.1 VAE!
curl -L -o flux2-vae.safetensors "https://huggingface.co/Comfy-Org/vae-text-encorder-for-flux-klein-4b/resolve/main/split_files/vae/flux2-vae.safetensors"
```

> Memory: 2.3 GB VRAM (diffusion) + 3.6 GB VRAM (Qwen3-4B encoder) + 95 MB (VAE) = ~6 GB total. Uses Qwen3-4B LLM as text encoder instead of CLIP+T5 — simpler setup, smaller footprint.

**FLUX.1-schnell** — previous default, Apache 2.0:

```bash
mkdir -p /opt/stable-diffusion.cpp/models/flux && cd /opt/stable-diffusion.cpp/models/flux
curl -L -O "https://huggingface.co/second-state/FLUX.1-schnell-GGUF/resolve/main/flux1-schnell-q4_k.gguf"
curl -L -O "https://huggingface.co/second-state/FLUX.1-schnell-GGUF/resolve/main/ae.safetensors"
curl -L -O "https://huggingface.co/second-state/FLUX.1-schnell-GGUF/resolve/main/clip_l.safetensors"
curl -L -O "https://huggingface.co/city96/t5-v1_1-xxl-encoder-gguf/resolve/main/t5-v1_1-xxl-encoder-Q4_K_M.gguf"
```

> Memory: 6.5 GB VRAM (diffusion) + 2.9 GB RAM (T5-XXL Q4_K_M) = ~10 GB total.

**Chroma flash Q4_0** — alternative, open-source:

```bash
cd /opt/stable-diffusion.cpp/models/flux
curl -L -o chroma-unlocked-v47-flash-Q4_0.gguf "https://huggingface.co/leejet/Chroma-GGUF/resolve/main/chroma-unlocked-v47-flash-Q4_0.gguf"
# Reuses existing T5-XXL and FLUX.1 ae.safetensors from above
```

> Memory: 5.1 GB VRAM (diffusion) + 3.2 GB RAM (T5-XXL) = ~8.4 GB total.

**SD-Turbo** — fast fallback, lower quality:

```bash
cd /opt/stable-diffusion.cpp/models
curl -L -o sd-turbo.safetensors \
  "https://huggingface.co/stabilityai/sd-turbo/resolve/main/sd_turbo.safetensors"
```

### 6.2 Performance

*Benchmarked 2026-03-14, sd.cpp master-525-d6dd6d7, Vulkan GFX1013 (16.5 GiB), Ollama stopped.*

> **Important:** FLUX GGUF files must use `--diffusion-model` flag, not `-m`. The `-m` flag fails with "get sd version from file failed" because GGUF metadata is empty after tensor name conversion. This applies to all sd.cpp versions.

**🏆 FLUX.2-klein-4B Q4_0 — new default (fastest, smallest):**

| Resolution | Steps | Time | s/step | Notes |
|:----------:|:-----:|:----:|:------:|-------|
| 512×512 | 4 | **20s** | 3.95 | Default, ~6 GB VRAM total |
| 512×512 | 8 | **26s** | 2.66 | Better quality, GPU warm |
| 768×768 | 4 | **30s** | 5.43 | Great quality, no tiling |
| 1024×1024 | 4 | **63s** | 10.18 | VAE tiling required |
| 1024×1024 | 4 | ❌ FAIL | — | Without `--vae-tiling` (VAE OOM) |

> FLUX.2-klein uses a Qwen3-4B LLM as text encoder (instead of CLIP+T5), producing richer text understanding. At 4 steps it's 1.5× faster than FLUX.1-schnell and uses 40% less VRAM. The `--offload-to-cpu` flag is essential (manages UMA allocation pools).

**FLUX.1-schnell Q4_K — previous default:**

| Resolution | Steps | Time | Notes |
|:----------:|:-----:|:----:|-------|
| 512×512 | 4 | **30s** | ~10 GB VRAM (6.5 diffusion + 3.4 encoders) |
| 768×768 | 4 | **91s** | VAE tiling kicks in |
| 1024×1024 | 4 | **146s** | VAE tiling, good quality |
| 512×512 | 8 | **77s** | More steps, marginal improvement |

**Chroma flash Q4_0 — quality alternative (reuses T5+VAE from FLUX.1):**

| Resolution | Steps | Time | Notes |
|:----------:|:-----:|:----:|-------|
| 512×512 | 4 | **85s** | Sampling 46s + encoder 37s |
| 512×512 | 8 | **130s** | Sampling 96s |
| 768×768 | 8 | **240s** | Sampling 195s |

> Chroma uses cfg-based guidance (like FLUX.1-dev) but is fully open. Quality is better than schnell per step, but 4× slower than FLUX.2-klein.

**FLUX.1-dev Q4_K_S — high-quality, slow (city96/FLUX.1-dev-gguf, 6.8 GB):**

| Resolution | Steps | Time | Notes |
|:----------:|:-----:|:----:|-------|
| 512×512 | 20 | **279s** | Sampling 253s (12.65 s/step), ~6.6 GB VRAM |
| 768×768 | 20 | ❌ FAIL | Guidance model compute graph exceeds VRAM |

**SD-Turbo — fast fallback:**

| Resolution | Steps | Time | Notes |
|:----------:|:-----:|:----:|-------|
| 512×512 | 1 | **11s** | Minimum viable, ~2 GB VRAM |
| 768×768 | 4 | **21s** | Decent for quick previews |

**Head-to-head comparison (same prompt, same hardware, back-to-back):**

| Model | 512² @4s | 768² @4s | VRAM | Diffusion | Encoder |
|-------|:--------:|:--------:|:----:|:---------:|:-------:|
| **FLUX.2-klein-4B** | **20s** | **30s** | **6 GB** | 2.3 GB | Qwen3-4B (2.4 GB) |
| FLUX.1-schnell | 30s | 91s | 10 GB | 6.5 GB | CLIP+T5 (3.4 GB) |
| Chroma flash | 85s | 240s⁸ | 8.4 GB | 5.1 GB | T5 (3.2 GB) |
| FLUX.1-dev | 279s²⁰ | ❌ | 10 GB | 6.8 GB | CLIP+T5 (3.4 GB) |
| SD-Turbo | 11s¹ | 21s | 2 GB | 2 GB | (built-in) |

> FLUX.2-klein-4B is the clear winner: fastest at every resolution, smallest VRAM footprint, newest architecture. SD-Turbo remains the fastest option for previews but quality is significantly lower.

**Summary: recommended settings for production**

| Use case | Model | Resolution | Steps | Time |
|----------|-------|:----------:|:-----:|:----:|
| **Default (Signal)** | **FLUX.2-klein-4B** | **512×512** | **4** | **~20s** |
| **High quality** | **FLUX.2-klein-4B** | **768×768** | **4** | **~30s** |
| **Poster/wallpaper** | **FLUX.2-klein-4B** | **1024×1024** | **4** | **~63s** |
| Quick preview | SD-Turbo | 512×512 | 1 | ~11s |
| Best quality (slow) | Chroma flash | 512×512 | 8 | ~130s |

```bash
# FLUX.2-klein-4B — recommended production command:
/opt/stable-diffusion.cpp/build/bin/sd-cli \
  --diffusion-model models/flux2/flux-2-klein-4b-Q4_0.gguf \
  --vae models/flux2/flux2-vae.safetensors \
  --llm models/flux2/qwen3-4b-Q4_K_M.gguf \
  -p "your prompt here" \
  --cfg-scale 1.0 --steps 4 -H 512 -W 512 \
  --offload-to-cpu --diffusion-fa -v \
  -o output.png
```

### 6.2.1 Upgrade roadmap — beyond the current stack

sd.cpp (master-525+) supports more models. The BC-250 has ~16.5 GB with Ollama stopped (post-GTT migration). All models use `--offload-to-cpu` (UMA — no PCIe penalty).

**Image generation — tested models:**

| Model | Params | GGUF Size | Total RAM¹ | Steps | Quality | Status |
|-------|:------:|:---------:|:----------:|:-----:|:-------:|--------|
| **FLUX.2-klein-4B Q4_0** | **4B** | **2.3 GB** | **~6 GB** | **4** | **★★★** | **✅ Current default, 20s @512²** |
| FLUX.1-schnell Q4_K | 12B | 6.5 GB | ~10 GB | 4 | ★★ | ✅ Previous default, 30s @512² |
| Chroma flash Q4_0 | 12B | 5.1 GB | ~8.4 GB | 4–8 | ★★★ | ✅ Tested — 85s @512², better quality |
| FLUX.1-dev Q4_K_S | 12B | 6.8 GB | ~10 GB | 20 | ★★★★ | ✅ Tested — 279s @512², ❌768²+ |
| SD-Turbo | 1.1B | ~2 GB | ~2.5 GB | 1–4 | ★ | ✅ Fast preview, 11s @512² |

> ¹ Total RAM includes diffusion model + text encoder(s) + VAE.

**Image generation — potential future upgrades:**

| Model | Params | GGUF Size | Total RAM¹ | Notes |
|-------|:------:|:---------:|:----------:|-------|
| FLUX.2-klein-9B Q4_0 | 9B | ~5.6 GB | ~11 GB | Needs Qwen3-8B encoder, borderline VRAM |
| SD3.5-medium | 2.5B | ~2.5 GB | ~6 GB | Needs clip_g + T5 |

**Video generation — future capability:**

| Model | Params | GGUF Size | Total RAM¹ | Notes |
|-------|:------:|:---------:|:----------:|-------|
| WAN 2.1 T2V 1.3B | 1.3B | ~1.5 GB | ~5 GB | Text→video, lightweight, 33 frames feasible |
| WAN 2.2 TI2V 5B | 5B | ~5 GB | ~9 GB | Text/image→video. Borderline — might work with tiling |

**Image editing — Kontext:**

| Model | Notes |
|-------|-------|
| FLUX.2-klein-4B | Supports Kontext-style editing via `-r` reference image |

> Kontext reuses the same FLUX.2 diffusion model + a reference image. No additional model downloads needed beyond what's already on disk.
> ```bash
> # Edit an existing image:
> sd-cli --diffusion-model models/flux2/flux-2-klein-4b-Q4_0.gguf \
>   --vae models/flux2/flux2-vae.safetensors --llm models/flux2/qwen3-4b-Q4_K_M.gguf \
>   -r input.png -p "change the sky to sunset" --cfg-scale 1.0 --steps 4 \
>   --sampling-method euler --offload-to-cpu --diffusion-fa -o output.png
> ```

### 6.3 Signal integration — synchronous pipeline

SD and Ollama can't run simultaneously (shared 16 GB VRAM). queue-runner handles this synchronously — no worker scripts, no delays:

```
  "draw a cyberpunk cat"
    +-> queue-runner intercepts EXEC(generate-and-send "...")
         +-> stop Ollama -> run sd-cli -> send image via Signal -> restart Ollama
              +-> image arrives (~25s total with FLUX.2-klein)
```

The pipeline is triggered when the LLM emits an `EXEC()` call matching the SD script path. queue-runner stops Ollama first (freeing ~12 GB VRAM), generates the image with FLUX.2-klein-4B, sends it as a Signal attachment, then restarts Ollama. Total downtime ~25–40s (vs ~60–90s with FLUX.1-schnell).

> ⚠️ **GFX1013 bug:** sd-cli hangs after writing the output image (Vulkan cleanup). queue-runner polls for the file, then kills the process.

---

# `PART III` — Monitoring & Intelligence

## 7. Netscan Ecosystem

A comprehensive research, monitoring, and intelligence system with **337 autonomous jobs** running on a GPU-constrained single-board computer. Dashboard at `http://<LAN_IP>:8888` — 29 main pages + 101 per-host detail pages.

### 7.1 Architecture — queue-runner v7

The BC-250 has 16 GB GTT shared with the CPU — only **one LLM job can run at a time**. `queue-runner.py` (systemd service) orchestrates all 337 jobs in a continuous loop, with Signal chat between every job:

```
queue-runner v7 -- Continuous Loop + Signal Chat

Cycle N:
  337 jobs sequential, ordered by category:
  scrape -> infra -> lore -> academic -> repo -> company -> career
         -> think -> meta -> market -> report
  HA observations interleaved every 50 jobs
  Signal inbox checked between EVERY job
  Chat processed with LLM (EXEC tool use + image gen)
  Crash recovery: resumes from last completed job

Cycle N+1:
  Immediately starts -- no pause, no idle windows
  No nightly/daytime distinction
```

**Key design decisions (v5 → v7):**

| v5 (OpenClaw era) | v7 (current) |
|--------------------|--------------|
| Nightly batch + daytime fill | Continuous loop, no distinction |
| 354 jobs (including duplicates) | 337 jobs (deduped, expanded) |
| LLM jobs routed through `openclaw cron run` | All jobs run as direct subprocesses |
| Signal via OpenClaw gateway (~700 MB) | signal-cli standalone (~100 MB) |
| Chat only when gateway available | Chat between every job |
| Async SD pipeline (worker scripts, 45s delay) | Synchronous SD (stop Ollama → generate → restart) |
| GPU idle detection for user chat preemption | No preemption needed — chat is interleaved |

**All jobs run as direct subprocesses** — `subprocess.Popen` for Python/bash scripts, no LLM agent routing. This is 3–10× faster than the old `openclaw cron run` path and eliminates the gateway dependency entirely.

### 7.1.1 Queue ordering

The queue prioritizes **data diversity** — all dashboard tabs get fresh data even if the cycle is interrupted:

```
 SCRAPE (data gathering, no LLM) ----------- career-scan, salary, patents, events, repos, lore
 INFRA (6 jobs, ~0.6h) --------------------- leak-monitor, netscan, watchdog
 LORE-ANALYSIS (12 jobs) ------------------- lkml, soc, jetson, libcamera, dri, usb, riscv, dt
 ACADEMIC (17 jobs) ------------------------ publications, dissertations, patents
 REPO-THINK (22 jobs) ---------------------- LLM analysis of repo changes
 OTHER (11 jobs) --------------------------- car-tracker, city-watch, csi-sensor
 COMPANY (46 jobs) ------------------------- company-think per entity
 CAREER (49 jobs) -------------------------- career-think per domain
 THINK (37 jobs, ~2.2h) -------------------- research, trends, crawl, crossfeed
 META (5 jobs) ----------------------------- life-advisor, system-think
 MARKET (21 jobs) -------------------------- market-watch + sector analysis
 REPORT (1 job) ---------------------------- daily-summary -> Signal
   + HA observations interleaved every 50 jobs (ha-correlate, ha-journal)
   + Signal chat checked between EVERY job
```

### 7.1.2 GPU idle detection

GPU idle detection is used for legacy `--daytime` mode and Ollama health checks:

```python
# Three-tier detection:
# 1. Ollama /api/ps → no models loaded → definitely idle
# 2. sysfs pp_dpm_sclk → clock < 1200 MHz → model loaded but not computing
# 3. Ollama expires_at → model about to unload → idle for 3+ min
```

In continuous loop mode (default), GPU detection is only used for pre-flight health checks — not for yielding to user chat, since chat is interleaved between jobs.

### 7.2 Scripts

**GPU jobs** (queue-runner — sequential, one at a time):

| Script | Purpose | Jobs |
|--------|---------|:----:|
| `career-scan.py` | Two-phase career scanner (§8) | 1 |
| `career-think.py` | Per-company career deep analysis | 81 |
| `salary-tracker.py` | Salary intel — NoFluffJobs, career-scan extraction | 1 |
| `company-intel.py` | Deep company intel — GoWork, DDG news, layoffs (13 entities) | 46 |
| `company-think-*` | Focused company deep-dives | 76 |
| `patent-watch.py` | IR/RGB camera patent monitor — Google Patents, Lens.org | 1 |
| `event-scout.py` | Meetup/conference tracker — Łódź, Warsaw, Poland, Europe | 1 |
| `leak-monitor.py` | CTI: 11 OSINT sources — HIBP, Hudson Rock, GitHub dorks, Ahmia dark web, CISA KEV, ransomware, Telegram | 1 |
| `idle-think.sh` | Research brain — 8 task types → JSON notes | 37 |
| `ha-journal.py` | Home Assistant analysis (climate, sensors, anomalies) | 1 |
| `ha-correlate.py` | HA cross-sensor correlation | 1 |
| `city-watch.py` | Łódź/SkyscraperCity construction tracker | 1 |
| `csi-sensor-watch.py` | CSI camera sensor patent/news monitor | 1 |
| `lore-digest.sh` | Kernel mailing list digests (8 feeds) | 12 |
| `repo-watch.sh` | Upstream repos (GStreamer, libcamera, v4l-utils, FFmpeg, LinuxTV) | 8 |
| `repo-think.py` | LLM analysis of repo changes | 22 |
| `market-think.py` | Market sector analysis + synthesis | 21 |
| `life-think.py` | Cross-domain life advisor | 2 |
| `system-think.py` | GPU/security/health system intelligence | 3 |
| `radio-scan.py` | SDR spectrum monitoring | 1 |
| `daily-summary.py` | End-of-cycle summary → Signal | 1 |

**CPU jobs** (system crontab — independent of queue-runner):

| Script | Frequency | Purpose |
|--------|-----------|---------|
| `gpu-monitor.sh` + `.py` | 1 min | GPU utilization sampling (3-state) |
| `presence.sh` | 5 min | Phone presence tracker |
| `syslog.sh` | 5 min | System health logger |
| `watchdog.py` | 30 min (live), 06:00 (full) | Integrity checks — cron, disk, services |
| `scan.sh` + `enumerate.sh` | 04:00 | Network scan + enumeration (nmap) |
| `vulnscan.sh` | Weekly (Sun) | Vulnerability scan |
| `repo-watch.sh` | 08:00, 14:00, 18:00 | Upstream repo data collection |
| `report.sh` | 08:30 | Morning report rebuild |
| `generate-html.py` | After each queue-runner job | Dashboard HTML builder (6900+ lines) |
| `gpu-monitor.py chart` | 22:55 | Daily GPU utilization chart |

### 7.3 Job scheduling — queue-runner v7

All 337 jobs are defined in `~/.openclaw/cron/jobs.json` and scheduled dynamically by `queue-runner.py` (systemd service, `WatchdogSec=14400`). There are **no fixed cron times** — jobs run sequentially as fast as the GPU allows, in a continuous loop.

**Job categories** (auto-classified by name pattern):

| Category | Jobs | Typical GPU time | Examples |
|----------|:----:|:----------------:|---------|
| `scrape` | 35 | 0.1h | career-scan, salary, patents, events, repo-scan (no LLM) |
| `infra` | 6 | 0.6h | leak-monitor, netscan, watchdog, event-scout, radio-scan |
| `lore` | 12 | 0.7h | lore-digest per mailing list feed |
| `academic` | 17 | — | academic-watch per topic |
| `repo-think` | 22 | 0.2h | LLM analysis of repo changes |
| `company` | 46 | 0.4h | company-think per entity |
| `career` | 49 | 1.4h | career-think per domain |
| `think` | 37 | 2.2h | research, trends, crawl, crossfeed |
| `meta` | 5 | — | life-think, system-think |
| `market` | 21 | 1.0h | market-watch + sector analysis |
| `ha` | 2 | 0.5h | ha-correlate, ha-journal (interleaved) |
| `report` | 1 | — | daily-summary → Signal |
| `weekly` | 3 | — | vulnscan, csi-sensor-discover/improve |
| **Total** | **337** | **~11h** | |

**Data flow:**

```
jobs.json (337 jobs)
  |
  v
queue-runner.py
  |
  |-- All jobs -> subprocess.Popen -> python3/bash /opt/netscan/...
  |                                         |
  |       JSON results <--------------------+
  |         |
  |         |-- /opt/netscan/data/{category}/*.json
  |         |
  |         +-- generate-html.py -> /opt/netscan/web/*.html -> nginx :8888
  |
  |-- Signal chat (between every job)
  |     via JSON-RPC http://127.0.0.1:8080/api/v1/rpc
  |
  +-- Signal alerts (career matches, leaks, events, daily summary)
```

### 7.4 System crontab — non-GPU

| Freq | Script |
|------|--------|
| 1 min | `gpu-monitor.sh` + `gpu-monitor.py collect` |
| 5 min | `presence.sh` + `syslog.sh` |
| 30 min | `watchdog.py --live-only` |
| 04:00 | `scan.sh` (nmap) |
| 04:30 | `enumerate.sh` |
| Sun 05:30 | `vulnscan.sh` |
| 06:00 | `watchdog.py` (full) |
| 08:00, 14:00 | `repo-watch.sh --all` |
| 08:30 | `report.sh` |
| 18:00 | `repo-watch.sh --all --notify` |
| 22:55 | `gpu-monitor.py chart` |

### 7.5 Data flow & locations

All paths relative to `/opt/netscan/`:

| Data | Path | Source |
|------|------|--------|
| Research notes | `data/think/note-*.json` + `notes-index.json` | idle-think.sh |
| Career scans | `data/career/scan-*.json` + `latest-scan.json` | career-scan.py |
| Career analysis | `data/career/think-*.json` | career-think.py |
| Salary | `data/salary/salary-*.json` (180-day history) | salary-tracker.py |
| Company intel | `data/intel/intel-*.json` + `company-intel-deep.json` | company-intel.py |
| Patents | `data/patents/patents-*.json` + `patent-db.json` | patent-watch.py |
| Events | `data/events/events-*.json` + `event-db.json` | event-scout.py |
| Leaks / CTI | `data/leaks/leak-intel.json` | leak-monitor.py |
| City watch | `data/city/city-watch-*.json` | city-watch.py |
| CSI sensors | `data/csi-sensors/csi-sensor-*.json` | csi-sensor-watch.py |
| HA correlations | `data/correlate/correlate-*.json` | ha-correlate.py |
| HA journal | `data/ha-journal-*.json` | ha-journal.py |
| Mailing lists | `data/{lkml,soc,jetson,libcamera,dri,usb,riscv,dt}/` | lore-digest.sh |
| Repos | `data/repos/` | repo-watch.sh, repo-think.py |
| Market | `data/market/` | market-think.py |
| Academic | `data/academic/` | academic-watch (LLM) |
| GPU load | `data/gpu-load.tsv` | gpu-monitor.sh |
| System health | `data/syslog/health-*.tsv` (30-day retention) | syslog.sh |
| Network hosts | `data/hosts-db.json` | scan.sh |
| Presence | `data/presence-state.json` | presence.sh |
| Radio | `data/radio/` | radio-scan.py |
| Queue state | `data/queue-runner-state.json` | queue-runner.py |

### 7.6 Dashboard — 29 main pages + 101 host detail pages

Served by nginx at `:8888`, generated by `generate-html.py` (6900+ lines):

| Page | Content | Data source |
|------|---------|-------------|
| `index.html` | Overview — hosts, presence, latest notes, status | aggregated |
| `home.html` | Home Assistant — climate, energy, anomalies | ha-journal, ha-correlate |
| `career.html` | Career intelligence — matches, trends | career-scan, career-think |
| `market.html` | Market analysis — sectors, commodities, crypto | market-think |
| `advisor.html` | Life advisor — cross-domain synthesis | life-think |
| `notes.html` | Research brain — all think notes | idle-think |
| `leaks.html` | CTI / leak monitor | leak-monitor |
| `issues.html` | Upstream issue tracking | repo-think |
| `events.html` | Events calendar — Łódź, Warsaw, Poland | event-scout |
| `lkml.html` | Linux Media mailing list digest | lore-digest (linux-media) |
| `soc.html` | SoC bringup mailing list | lore-digest (soc-bringup) |
| `jetson.html` | Jetson/Tegra mailing list | lore-digest (jetson-tegra) |
| `libcamera.html` | libcamera mailing list | lore-digest (libcamera) |
| `dri.html` | DRI-devel mailing list | lore-digest (dri-devel) |
| `usb.html` | Linux USB mailing list | lore-digest (linux-usb) |
| `riscv.html` | Linux RISC-V mailing list | lore-digest (linux-riscv) |
| `dt.html` | Devicetree mailing list | lore-digest (devicetree) |
| `academic.html` | Academic publications | academic-watch |
| `hosts.html` | Network device inventory | scan.sh |
| `security.html` | Host security scoring | vulnscan.sh |
| `presence.html` | Phone detection timeline | presence.sh |
| `load.html` | GPU utilization heatmap + schedule | gpu-monitor |
| `radio.html` | SDR spectrum monitoring | radio-scan.py |
| `car.html` | Car tracker | car-tracker |
| `weather.html` | Weather forecast + HA sensor correlation | weather-watch.py |
| `news.html` | Tech news aggregation + RSS | news-watch.py |
| `health.html` | System health assessment (services, data freshness, LLM quality) | bc250-extended-health.py |
| `history.html` | Changelog | — |
| `log.html` | Raw scan logs | — |
| `host/*.html` | Per-host detail pages (101 hosts) | scan.sh, enumerate.sh |

> **Mailing list feeds** are configured in `digest-feeds.json` — 8 feeds from `lore.kernel.org`, each with relevance scoring keywords.

### 7.7 GPU monitoring — 3-state

Per-minute sampling via `pp_dpm_sclk`:

| State | Clock | Temp | Meaning |
|-------|:-----:|:----:|---------|
| `generating` | 2000 MHz | ~77°C | Active LLM inference |
| `loaded` | 1000 MHz | ~56°C | Model in VRAM, idle |
| `idle` | 1000 MHz | <50°C | No model loaded |

### 7.8 Configuration & state files

| File | Purpose |
|------|---------|
| `profile.json` | Public interests — tracked repos, keywords, technologies |
| `profile-private.json` | Career context — target companies, salary expectations *(gitignored)* |
| `watchlist.json` | Auto-evolving interest tracker |
| `digest-feeds.json` | Mailing list feed URLs (8 feeds from lore.kernel.org) |
| `repo-feeds.json` | Repository API endpoints |
| `sensor-watchlist.json` | CSI camera sensor tracking list |
| `queue-runner-state.json` | Cycle count, resume index *(in data/)* |
| `~/.openclaw/cron/jobs.json` | All 337 job definitions *(legacy path, may be migrated)* |

### 7.9 Resilience

| Mechanism | Details |
|-----------|---------|
| **Systemd watchdog** | `WatchdogSec=14400` (4h) — queue-runner pings every 30s during job execution |
| **Crash recovery** | State file records nightly batch progress; on restart, resumes from last completed job |
| **Midnight crossing** | Resume index valid for both today and yesterday's date (batch starts 23:00 day N, may crash after midnight day N+1) |
| **Atomic state writes** | Write to `.tmp` file, `fsync()`, then `rename()` — survives SIGABRT/power loss |
| **Ollama health checks** | Pre-flight check before each job; exponential backoff wait if unhealthy |
| **Network down** | Detects network loss, waits with backoff up to 10min |
| **GPU deadlock protection** | If GPU busy for > 60min continuously, breaks and moves on |
| **OOM protection** | Ollama `OOMScoreAdjust=-1000`, 16 GB NVMe swap, zram limited to 2 GB |
| **Signal delivery** | `--best-effort-deliver` flag — delivery failures don't mark job as failed |

---

## 8. Career Intelligence

Automated career opportunity scanner with a two-phase anti-hallucination architecture.

### 8.1 Two-phase design

```
  HTML page
    +-> Phase 1: extract jobs (NO candidate profile) -> raw job list
                                                            |
  Candidate Profile + single job ---------------------------+
    +-> Phase 2: score match -> repeat per job
                                   +-> aggregate -> JSON + Signal alerts
```

**Phase 1** extracts jobs from raw HTML without seeing the candidate profile — prevents the LLM from inventing matching jobs. **Phase 2** scores each job individually against the profile.

### 8.2 Alert thresholds

| Category | Score | Alert? |
|----------|:-----:|:------:|
| ⚡ Hot match | ≥70% | ✅ (up to 5/scan) |
| 🌍 Worth checking | 55–69% + remote | ✅ (up to 2/scan) |
| Good / Weak | <55% | Dashboard only |

> Software houses (SII, GlobalLogic, Sysgo…) appear on the dashboard but **never trigger alerts**.

### 8.3 Salary tracker · `salary-tracker.py`

Nightly at 01:30. Sources: career-scan extraction, NoFluffJobs API, JustJoinIT, Bulldogjob. Tracks embedded Linux / camera driver compensation in Poland. 180-day rolling history.

### 8.4 Company intelligence · `company-intel.py`

Nightly at 01:50. Deep-dives into 13 tracked companies across 7 sources: GoWork.pl reviews, DuckDuckGo news, Layoffs.fyi, company pages, 4programmers.net, Reddit, SemiWiki. LLM-scored sentiment (-5 to +5) with cross-company synthesis.

> **GoWork.pl:** New Next.js SPA breaks scrapers. Scanner uses the old `/opinie_czytaj,{entity_id}` URLs (still server-rendered).

### 8.5 Patent watch · `patent-watch.py`

Nightly at 02:10. Monitors 6 search queries (MIPI CSI, IR/RGB dual camera, ISP pipeline, automotive ADAS, sensor fusion, V4L2/libcamera) across Google Patents and Lens.org. Scored by relevance keywords × watched assignee bonus.

### 8.6 Event scout · `event-scout.py`

Nightly at 02:30. Discovers tech events with geographic scoring (Łódź 10, Warsaw 8, Poland 5, Europe 3, Online 9). Sources: Crossweb.pl, Konfeo, Meetup, Eventbrite, DDG, 9 known conference sites.

---

# `PART IV` — Reference

## 9. Repository Structure

<details>
<summary>▸ Full tree</summary>

```
bc250/
├── README.md                       ← you are here
├── netscan/                        → /opt/netscan/
│   ├── queue-runner.py             # v7 — continuous loop + Signal chat (337 jobs)
│   ├── career-scan.py              # Two-phase career scanner
│   ├── career-think.py             # Per-company career analysis
│   ├── salary-tracker.py           # Salary intelligence
│   ├── company-intel.py            # Company deep-dive
│   ├── company-think.py            # Per-entity company analysis
│   ├── patent-watch.py             # Patent monitor
│   ├── event-scout.py              # Event tracker
│   ├── city-watch.py               # SkyscraperCity Łódź construction monitor
│   ├── leak-monitor.py             # CTI: 11 OSINT sources + Ahmia dark web
│   ├── ha-journal.py               # Home Assistant journal
│   ├── ha-correlate.py             # HA cross-sensor correlation
│   ├── ha-observe.py               # Quick HA queries
│   ├── csi-sensor-watch.py         # CSI camera sensor patent/news
│   ├── radio-scan.py               # SDR spectrum monitoring
│   ├── market-think.py             # Market sector analysis
│   ├── life-think.py               # Cross-domain life advisor
│   ├── system-think.py             # GPU/security/health system intelligence
│   ├── daily-summary.py            # End-of-cycle Signal summary
│   ├── repo-think.py               # LLM analysis of repo changes
│   ├── academic-watch.py           # Academic publication monitor
│   ├── news-watch.py               # Tech news aggregation + RSS feeds
│   ├── book-watch.py               # Book/publication tracker
│   ├── weather-watch.py            # Weather forecast + HA sensor correlation
│   ├── car-tracker.py              # GPS car tracker (SinoTrack API, trip/stop detection)
│   ├── bc250-extended-health.py    # System health assessment (services, data freshness, LLM quality)
│   ├── llm_sanitize.py             # LLM output sanitizer (thinking tags, JSON repair)
│   ├── generate-html.py            # Dashboard builder (6900+ lines, 29 main + 101 host pages)
│   ├── gpu-monitor.py              # GPU data collector
│   ├── idle-think.sh               # Research brain (8 task types)
│   ├── repo-watch.sh               # Upstream repo monitor
│   ├── lore-digest.sh              # Mailing list digests (8 feeds)
│   ├── bc250-health-check.sh       # Quick health check (systemd timer, triggers extended health)
│   ├── gpu-monitor.sh              # Per-minute GPU sampler
│   ├── scan.sh / enumerate.sh      # Network scanning
│   ├── vulnscan.sh                 # Weekly vulnerability scan
│   ├── presence.sh                 # Phone presence detection
│   ├── syslog.sh                   # System health logger
│   ├── watchdog.py                 # Integrity checker
│   ├── report.sh                   # Morning report rebuild
│   ├── profile.json                # Public interests + Signal config
│   ├── profile-private.json        # Career context (gitignored)
│   ├── watchlist.json              # Auto-evolving interest tracker
│   ├── digest-feeds.json           # Feed URLs (8 mailing lists)
│   ├── repo-feeds.json             # Repository endpoints
│   └── sensor-watchlist.json       # CSI sensor tracking list
├── openclaw/                       # ARCHIVED — see Appendix A
│   └── (historical OpenClaw config, no longer deployed)
├── systemd/
│   ├── queue-runner.service        # v7 — continuous loop + Signal chat
│   ├── queue-runner-nightly.service # Nightly batch trigger
│   ├── queue-runner-nightly.timer
│   ├── signal-cli.service          # Standalone JSON-RPC daemon
│   ├── bc250-health.service        # Health check timer
│   ├── bc250-health.timer
│   ├── ollama.service
│   ├── ollama-watchdog.service     # Ollama restart watchdog
│   ├── ollama-watchdog.timer
│   ├── ollama-proxy.service        # LAN proxy for Ollama API
│   └── ollama.service.d/
│       └── override.conf           # Vulkan + memory settings
├── scripts/
│   ├── generate-and-send.sh        # SD image generation pipeline
│   └── generate.sh                 # SD generation wrapper
├── generate-and-send.sh            → /opt/stable-diffusion.cpp/
└── generate-and-send-worker.sh     → /opt/stable-diffusion.cpp/
```

</details>

### Deployment

| Local | → bc250 |
|-------|---------|
| `netscan/*` | `/opt/netscan/` |
| `systemd/queue-runner.service` | `/etc/systemd/system/queue-runner.service` |
| `systemd/signal-cli.service` | `/etc/systemd/system/signal-cli.service` |
| `systemd/ollama.*` | `/etc/systemd/system/ollama.*` |
| `generate-and-send*.sh` | `/opt/stable-diffusion.cpp/` |

```bash
# Typical deploy workflow
scp netscan/queue-runner.py bc250:/tmp/
ssh bc250 'sudo cp /tmp/queue-runner.py /opt/netscan/ && sudo systemctl restart queue-runner'
```

---

## 10. Troubleshooting

<details>
<summary><b>▸ ROCm crashes in Ollama logs</b></summary>

Expected — Ollama tries ROCm, it crashes on GFX1013, falls back to Vulkan. No action needed.

</details>

<details>
<summary><b>▸ Only 7.9 GiB GPU memory instead of 14 GiB</b></summary>

GTT tuning not applied. Check: `cat /proc/cmdline | grep gttsize`

</details>

<details>
<summary><b>▸ 14B model loads but inference returns HTTP 500</b></summary>

TTM pages_limit bottleneck. Fix: `echo 4194304 | sudo tee /sys/module/ttm/parameters/pages_limit` (see §3.3).

</details>

<details>
<summary><b>▸ Model loads on CPU instead of GPU</b></summary>

Check `OLLAMA_VULKAN=1`: `sudo systemctl show ollama | grep Environment`

</details>

<details>
<summary><b>▸ Context window OOM kills (the biggest gotcha on 16 GB)</b></summary>

Ollama allocates KV cache based on `num_ctx`. Many models default to 32K–40K context, which on a 14B Q4_K model means 14–16 GB *just for the model* — leaving nothing for the OS.

**Symptoms:** Gateway gets OOM-killed, Ollama journal shows 500 errors, `dmesg` shows `oom-kill`.

**Root cause:** The abliterated Qwen3 14B declares `num_ctx 40960` → 16 GB total model memory.

**Fix:** Create a custom model with context baked in:
```bash
cat > /tmp/Modelfile.16k << 'EOF'
FROM huihui_ai/qwen3-abliterated:14b
PARAMETER num_ctx 16384
EOF
ollama create qwen3-14b-16k -f /tmp/Modelfile.16k
```

This drops memory from ~16 GB → ~11.1 GB. Do **not** rely on `OLLAMA_CONTEXT_LENGTH` — it doesn't reliably override API requests from the gateway.

</details>

<details>
<summary><b>▸ signal-cli not responding on port 8080</b></summary>

Check the service: `systemctl status signal-cli`. If it crashed, restart: `sudo systemctl restart signal-cli`. Verify JSON-RPC:
```bash
curl -s http://127.0.0.1:8080/api/v1/rpc \
  -d '{"jsonrpc":"2.0","method":"listAccounts","id":"1"}'
```

</details>

<details>
<summary><b>▸ zram competing with model for physical RAM</b></summary>

Fedora defaults to ~8 GB zram. zram compresses pages but stores them in *physical* RAM — directly competing with the model. On 16 GB systems running 14B models, disable or limit zram and use NVMe file swap instead:
```bash
sudo mkdir -p /etc/systemd/zram-generator.conf.d
echo -e '[zram0]\nzram-size = 2048' | sudo tee /etc/systemd/zram-generator.conf.d/small.conf
```

</details>

<details>
<summary><b>▸ Python cron scripts produce no output</b></summary>

Stdout is fully buffered under cron (no TTY). Add at script start:
```python
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)
```

</details>

<details>
<summary><b>▸ Signal delivery from signal-cli</b></summary>

Signal JSON-RPC API at `http://127.0.0.1:8080/api/v1/rpc`:
```bash
curl -X POST http://127.0.0.1:8080/api/v1/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"send","params":{
    "account":"+<BOT>","recipient":["+<YOU>"],
    "message":"test"
  },"id":"1"}'
```

</details>

---

## 11. Known Limitations & TODO

### ⚠ Limitations

| Issue | Impact |
|-------|--------|
| Shared VRAM | Image gen requires stopping Ollama. Bot offline ~25–40s (FLUX.2-klein) or ~60–90s (FLUX.1-schnell). |
| MoE context limit | 35B-A3B MoE tops out at 16K context (weights = 10.3 GiB, KV fills rest). Use 9B for >16K. |
| Signal latency | Messages queue during job execution (typical job 2–15 min). Chat checked between every job. |
| sd-cli hangs on GFX1013 | Vulkan cleanup bug → poll + kill workaround. |
| Cold start latency | 30–60s after Ollama restart (model loading). |
| Chinese thinking leak | Qwen3 occasionally outputs Chinese reasoning. Cosmetic. |
| Prefill rate degrades with context | 128 tok/s at 1.3K → 70 tok/s at 10K tokens (UMA bandwidth + attention scaling). |
| Gen speed degrades with context fill | 27 tok/s empty → 13 tok/s at 30K tokens. Partial model offload at KV limit causes cliff drop. |
| Ollama caps KV auto-size at ~40K (Q4_0) | `num_ctx` > 40960 accepted but silently truncated. Actual limit = VRAM ÷ per-token KV size. |

### ☐ TODO

- [x] Fix OOM kills — custom 16K context model + NVMe swap + OOM score protection
- [x] Fix gateway orphan processes — KillMode=control-group
- [x] Scale from 38 → 56 → 58 → 354 → 309 → **337** jobs/cycle (deduped, +frost-guard)
- [x] Add best-effort-deliver to all announce jobs
- [x] Queue-runner v2 → v3 → v4 → v5 → v6 → **v7** — continuous loop, Signal chat, synchronous SD
- [x] Fix nightly resume across midnight (batch_date accepts today or yesterday)
- [x] Dense daytime GPU mode → replaced by continuous loop (v7)
- [x] Leak-monitor: added Ahmia dark web, GitHub dorks, Hudson Rock retry, model fix
- [x] Dashboard audit — XSS fixes, dead code removal, queue-runner references
- [x] 29 main + 101 host detail dashboard pages including 8 mailing list feeds, academic, market, advisor, radio, car, weather, news, health
- [x] Replace OpenClaw gateway with standalone signal-cli + direct Ollama API calls
- [x] Signal chat between every job (no separate gateway process)
- [x] Synchronous SD image generation in queue-runner (no async worker scripts)
- [x] Bump GTT from 12 → 14 GiB, TTM pages_limit 3M → 4M
- [x] Disable 7 unnecessary services (~113 MB freed)
- [x] System prompt with cynical personality + full data directory map
- [x] Signal notification dedup — sent-items tracker (career, book, news, radio), cooldown+hash (weather, ha-correlate), daily flag (city-watch)
- [x] Extended health monitoring — automated hourly via bc250-health-check.sh
- [x] report.sh midnight-crossing fallback — uses yesterday's scan if today's missing
- [x] Try FLUX at 768×768 — **works with VAE tiling (91s schnell, dev fails at 768+)**
- [ ] Weekly career summary digest via Signal
- [ ] Migrate jobs.json away from ~/.openclaw/ path
- [x] Evaluate zram — already effectively disabled (device exists but disksize=0, NVMe swap handles everything)

### ☐ Action points — verified corrections & upgrades

**Memory tuning — GTT deprecation (kernel 6.12+):**

- [x] Test removing `amdgpu.gttsize=14336` — **done (March 14).** GTT grew 14→16 GiB, Vulkan 14.0→16.5 GiB. The deprecated param was limiting allocation.
- [x] Verify Vulkan heap sizes — host-visible grew from 4.17→8.17 GiB, device-local unchanged
- [x] Remove gttsize from grubby and documentation — removed, docs updated

**Image generation — model upgrades:**

- [x] Update sd.cpp from master-504 to **master-525-d6dd6d7** (adds FLUX.2, Anima, Chroma-Radiance, spectrum caching)
- [x] Test Chroma Q4_0 — **85s @512² (4 steps), 130s (8 steps). Reuses T5+VAE. Good quality but slower than FLUX.2-klein.**
- [x] Test FLUX.2-klein-4B — **20s @512², 30s @768², 63s @1024². Replaced FLUX.1-schnell as default. Uses Qwen3-4B encoder, 40% less VRAM.**
- [x] Test 768×768 resolution with FLUX.1-schnell — **91s with VAE tiling, 1024² = 146s**
- [ ] Test WAN 2.1 T2V 1.3B for short text-to-video clips (4s @ 8fps) — first video generation on BC-250
- [x] Add `--fa`, `--vae-tiling`, `--offload-to-cpu` to generation pipeline — done, smoke tested (37.7s vs ~48s)
- [x] Update `generate-and-send-worker.sh` with new flags

**LLM model upgrades (March 14 — Qwen3.5 era):**

- [x] Upgrade Ollama 0.16.1 → 0.18.0 (required for Qwen3.5 architecture)
- [x] Test qwen3.5:9b — **32 tok/s, 65K context, multimodal (vision+tools+thinking)**
- [x] Test qwen3.5-35b-a3b MoE at IQ2_M — **38 tok/s, 16K context. SMARTEST MODEL ON BC-250.**
- [x] Test qwen3.5-27b dense at IQ2_M — **FAILED. 0 tokens in 5 min. No matrix cores kills dense 27B.**
- [x] Switch production from qwen3:14b to qwen3.5-35b-a3b-iq2m as primary
- [x] Update OLLAMA_CONTEXT_LENGTH from 24576 to 16384 (MoE limit)

**Image generation — pipeline improvements:**

- [x] Add `--offload-to-cpu` to sd-cli command — done (queue-runner + worker script)
- [ ] Implement video generation path in queue-runner (vid_gen mode, mp4 → Signal attachment)
- [ ] Add ESRGAN upscale option in pipeline (512→1024 or 768→1536) using sd-cli `--upscale-model`

**Power monitoring:**

- [x] Add `power_w` column to `gpu-monitor.sh` TSV — reads `/sys/class/drm/card1/device/hwmon/hwmon2/power1_average`. Values: inference 130-155W, model-loaded 55-65W, true-idle 30-40W

---

## Appendix A — OpenClaw Archive

<details>
<summary><b>▸ Historical: OpenClaw gateway configuration (replaced in v7)</b></summary>

OpenClaw v2026.2.26 was used as the Signal ↔ Ollama gateway from project inception through queue-runner v6. It was a Node.js daemon that managed signal-cli as a child process, routed messages to the LLM, and provided an agent framework with tool dispatch.

**Why it was replaced:**
- ~700 MB RSS on a 16 GB system (4.4% of total RAM)
- 15+ second overhead per agent turn (system prompt injection, tool resolution)
- Unreliable fallback chains caused "fetch failed" timeout cascades
- Could not run scripts as direct subprocesses — everything went through the LLM agent
- signal-cli children survived gateway OOM kills, holding port 8080 as orphans
- 9.6K system prompt that couldn't be reduced below ~4K without breaking tools

**What replaced it:**
- signal-cli runs as standalone systemd service (JSON-RPC on :8080)
- queue-runner.py talks to Ollama `/api/chat` directly
- System prompt is a Python string in queue-runner.py (~3K tokens)
- All 337 jobs run as `subprocess.Popen` — no agent routing
- SD image generation handled synchronously by queue-runner

### A.1 Installation (historical)

```bash
sudo dnf install -y nodejs npm
sudo npm install -g openclaw@latest

openclaw onboard \
  --non-interactive --accept-risk --auth-choice skip \
  --install-daemon --skip-channels --skip-skills --skip-ui --skip-health \
  --daemon-runtime node --gateway-bind loopback
```

### A.2 Model configuration (historical)

`~/.openclaw/openclaw.json`:

```json
{
  "models": {
    "providers": {
      "ollama": {
        "baseUrl": "http://127.0.0.1:11434",
        "apiKey": "ollama-local",
        "api": "ollama",
        "models": [{
          "id": "qwen3-14b-16k",
          "name": "Qwen 3 14B (16K ctx)",
          "contextWindow": 16384,
          "maxTokens": 8192,
          "reasoning": true
        }]
      }
    }
  },
  "agents": {
    "defaults": {
      "model": {
        "primary": "ollama/qwen3-14b-16k",
        "fallbacks": ["ollama/qwen3-14b-abl-nothink:latest", "ollama/mistral-nemo:12b"]
      },
      "thinkingDefault": "high",
      "timeoutSeconds": 1800
    }
  }
}
```

### A.3 Tool optimization (historical)

```json
{
  "tools": {
    "profile": "coding",
    "alsoAllow": ["message", "group:messaging"],
    "deny": ["browser", "canvas", "nodes", "cron", "gateway"]
  },
  "skills": { "allowBundled": [] }
}
```

### A.4 Agent identity (historical)

Personality lived in workspace markdown files (`~/.openclaw/workspace/`):

| File | Purpose | Size |
|------|---------|:----:|
| `SOUL.md` | Core personality | 1.0 KB |
| `IDENTITY.md` | Name/emoji | 550 B |
| `USER.md` | Human info | 1.7 KB |
| `TOOLS.md` | Tool commands | 2.1 KB |
| `AGENTS.md` | Grounding rules | 1.4 KB |
| `WORKFLOW_AUTO.md` | Cron bypass rules | 730 B |

### A.5 Signal channel (historical)

```json
{
  "channels": {
    "signal": {
      "enabled": true,
      "account": "+<BOT_PHONE>",
      "cliPath": "/usr/local/bin/signal-cli",
      "dmPolicy": "pairing",
      "allowFrom": ["+<YOUR_PHONE>"],
      "sendReadReceipts": true,
      "textChunkLimit": 4000
    }
  }
}
```

### A.6 Service management (historical)

```bash
systemctl --user status openclaw-gateway   # status
openclaw logs --follow                     # live logs
openclaw doctor                            # diagnostics
openclaw channels status --probe           # signal health
```

The gateway service (`openclaw-gateway.service`) ran as a user-level systemd unit. It has been disabled and masked:

```bash
systemctl --user disable --now openclaw-gateway
systemctl --user mask openclaw-gateway
```

</details>

---

<div align="center">

`bc250` · AMD Cyan Skillfish · 337 autonomous jobs · *hack the planet* 🦞

</div>
