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
> **March 2026** · Qwen3.5-35B MoE at 38 tok/s, FLUX.2-klein-9B at best quality, hardware-specific driver workarounds, memory tuning discoveries, and real-world benchmarks that aren't documented anywhere else.

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
| [6](#6-image-generation) | Image Generation | Creative users | FLUX.2-klein-9B, synchronous pipeline |
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

<details>
<summary><b>▸ Origin story — Samsung, 5G operators, and AliExpress</b></summary>

**What it was built for:** Samsung commissioned these custom AMD SoCs to build rack-mount servers for **private DLT (Distributed Ledger Technology) infrastructure** — not public cryptocurrency mining. The target customers were **South Korean 5G operators** (SK Telecom and others), who were global pioneers in 5G deployment. Private blockchain solved several real problems for 5G telcos:

- **IoT microtransactions:** 5G networks connect millions of smart devices. DLT enables cheap, instant machine-to-machine contract settlement without overloading central databases.
- **Digital identity & security:** Operators used DLT registries for cryptographic customer authentication and digital identity wallets (e.g. Samsung Pay integration).
- **Inter-operator settlement:** Blockchain streamlined real-time roaming fee reconciliation and data exchange between telecom partners.

**Who made the hardware:** The SoC was designed by **AMD** (Zen 2 CPU + RDNA 1.5 GPU). Samsung designed the overall system and wrote the factory BIOS. The physical boards were manufactured by **ASRock Rack** (ASRock's server division) as an OEM contractor — Samsung rack enclosures typically held 12 BC-250 boards each. ASRock Rack is known for producing highly custom designs for large tech companies.

**How they ended up on AliExpress:** Classic corporate e-waste cycle. As 5G infrastructure evolved, entire Korean server racks were decommissioned. Specialized recycling centers (mostly near Shenzhen, China) buy pallets of retired servers in bulk — often by weight. Workers disassemble the racks, test individual boards, and list working BC-250 modules on AliExpress as all-in-one SBC platforms for $100–150.

</details>

> **Not a PlayStation 5.** Despite superficial similarities (both use Zen 2 + 16 GB memory), the BC-250 has nothing to do with the PS5. The PS5's Oberon SoC is **RDNA 2** (GFX10.3, gfx1030+); the BC-250's Cyan Skillfish is **RDNA 1.5** (GFX10.1, gfx1013) — a hybrid architecture: GFX10.1 instruction set (RDNA 1) but with **hardware ray tracing support** (full `VK_KHR_ray_tracing_pipeline`, `VK_KHR_acceleration_structure`, `VK_KHR_ray_query`). LLVM's AMDGPU processor table lists GFX1013 as product "TBA" under GFX10.1, confirming it was never a retail part. Samsung also licensed RDNA 2 for mobile (Exynos 2200 / Xclipse 920) — that's a completely separate deal.
>
> **Why "RDNA 1.5"?** GFX1013 doesn't fit cleanly into AMD's public RDNA generations. It has the RDNA 1 (GFX10.1) ISA and shader compiler target, but includes hardware ray tracing — a feature AMD only shipped publicly with RDNA 2 (GFX10.3). This makes Cyan Skillfish a transitional/custom design, likely built for Samsung's specific workload requirements. We call it "RDNA 1.5" as a practical label.

> **BIOS is not stock.** The board ships with a minimal Samsung BIOS meant for rack operation. A community-patched BIOS (from [AMD BC-250 docs](https://elektricm.github.io/amd-bc250-docs/)) enables standard UEFI features (boot menu, NVMe boot, fan control).

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
| **TDP** | 220W board (inference: 130–155W, between jobs: 55–60W, true idle w/o model: ~35W) |
| **BIOS** | Community-patched UEFI (not Samsung stock) — [AMD BC-250 docs](https://elektricm.github.io/amd-bc250-docs/) |
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
Environment=OLLAMA_KEEP_ALIVE=30m
Environment=OLLAMA_MAX_LOADED_MODELS=1
Environment=OLLAMA_FLASH_ATTENTION=1
Environment=OLLAMA_GPU_OVERHEAD=0
Environment=OLLAMA_CONTEXT_LENGTH=16384
Environment=OLLAMA_MAX_QUEUE=4
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

Ollama allocates KV cache based on the model's declared context window. Without a cap, large models request more KV cache than the BC-250 can handle, causing TTM fragmentation, OOM kills, or deadlocks on this UMA system.

**Fix:** Set `OLLAMA_CONTEXT_LENGTH=16384` in the Ollama systemd override (see §3.3). This caps all inference to 16K context by default — matching the MoE primary model's limit.

> Individual requests can override with `{"options": {"num_ctx": 65536}}` when using `qwen3.5:9b` (which handles 65K). The cap only affects the default allocation.

**History of context tuning:**

| Date | Context Cap | Primary Model | Why |
|------|:-----------:|---------------|-----|
| Feb 2026 | 40960 | qwen3:14b | Default — caused deadlocks (TTM fragmentation) |
| Feb 25 | **24576** | qwen3:14b | Sweet spot: ~27 tok/s, 26K was 10% slower, 28K+ deadlocked |
| Mar 14 | **16384** | qwen3.5-35b-a3b MoE | MoE maxes at 16K (KV cache exceeds VRAM at 24K+). 9B fallback can go to 65K per-request. |

> **Why 24K → 16K?** The 35B MoE's total weight (11 GB GGUF) is larger than qwen3:14b (9.3 GB). At 24K+ context the KV cache can't fit alongside the MoE weights. 16K is the maximum stable context for the MoE with all layers on GPU. See §4.3 for detailed KV cache scaling.

### 3.5 Swap — NVMe-backed safety net

With the model consuming 11+ GB on a 14 GB system, disk swap is essential for surviving inference peaks.

> **NVMe wear concern:** Swap is a *safety net*, not an active paging target. In steady state, swap usage is ~400 MB (OS buffers pushed out to make room for model weights). SMART data after months of 24/7 operation: **3% wear, 25.4 TB total written**. The model runs entirely in RAM — swap catches transient spikes during model load/unload transitions. Consumer NVMe drives rated for 300–600 TBW will last years at this rate.

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

| Model | Params | Quant | tok/s | Prefill | Max Ctx | VRAM @4K | Status |
|-------|:------:|:-----:|:-----:|:-------:|:-------:|:--------:|--------|
| **qwen3.5-35b-a3b-iq2m** | **35B/3B** | **UD-IQ2_M** | **38** | **233** | **16K** | **12.3 GiB** | **🏆 Smartest — MoE** |
| **qwen3.5:9b** | **9.7B** | **Q4_K_M** | **32** | **230** | **65K** | **8.6 GiB** | **🏆 Best context+vision** |
| qwen2.5:3b | 3.1B | Q4_K_M | **104** | **515** | **64K** | 3.4 GiB | ✅ Fast, lightweight |
| qwen2.5:7b | 7.6B | Q4_K_M | **56** | **248** | **64K** | 6.5 GiB | ✅ Great quality/speed |
| qwen2.5-coder:7b | 7.6B | Q4_K_M | **56** | **246** | **64K** | 6.4 GiB | ✅ Code-focused |
| llama3.1:8b | 8.0B | Q4_K_M | **52** | **246** | **48K** | 11.0 GiB | ✅ Fast 8B |
| mannix/llama3.1-8b-lexi | 8.0B | Q4_0 | **51** | **308** | **48K** | 10.6 GiB | ✅ Uncensored 8B |
| huihui_ai/seed-coder-abliterate | 8.3B | Q4_K_M | **52** | **231** | **64K** | 9.1 GiB | ✅ Code gen, uncensored |
| qwen3:8b | 8.2B | Q4_K_M | **44** | **251** | **64K** | 9.8 GiB | ✅ Thinking mode |
| huihui_ai/qwen3-abliterated:8b | 8.2B | Q4_K_M | **46** | **250** | **64K** | 9.7 GiB | ✅ Abliterated 8B |
| gemma2:9b | 9.2B | Q4_0 | **38** | **219** | **48K** | 9.2 GiB | ✅ Fixed! (was 91% before GTT fix) |
| mistral-nemo:12b | 12.2B | Q4_0 | **34** | **137** | **24K** | 10.8 GiB | ⚠️ 32K deadlocks |
| qwen3:14b | 14.8B | Q4_K_M | **27** | **131** | **24K** | 13.5 GiB | ✅ Previous primary |
| huihui_ai/qwen3-abliterated:14b | 14.8B | Q4_K_M | **28** | **137** | **24K** | 11.4 GiB | ✅ Abliterated |
| phi4:14b | 14.7B | Q4_K_M | **29** | **128** | **40K** | 11.8 GiB | 🏆 Best 14B context |
| Qwen3-30B-A3B (Q2_K) | 30.5B | Q2_K | **61** | — | **16K** | 11.5 GiB | ⚠️ MoE fast, heavy quant |
| qwen3.5-27b-iq2m | 26.9B | IQ2_M | **0** | — | — | 13.5 GiB | ❌ Non-functional¹ |

> **All models run 100% on GPU** after GTT tuning (16 GiB). Before the fix, gemma2:9b was only 91% GPU-offloaded (26 tok/s → 38 tok/s after fix).

> ¹ **Why 27B dense fails:** The dense architecture requires all 27B parameters in every forward pass. Without matrix cores (GFX1013 has none), each token requires ~27B multiplications through general-purpose shader cores. Result: 0 tokens generated in 5 minutes. The 35B MoE with only 3B active params per token avoids this entirely — compute is ~9× less per token despite having more total knowledge stored.

> **Prefill column:** Measured at ~400 tokens prompt size (warm model, FP16 KV). Prefill rate depends on prompt length — see §4.5 for detailed sweep. Smaller models (3B) saturate the GPU compute and achieve higher prefill. Larger models (14B) are memory-bandwidth-limited at ~128–137 tok/s. MoE and 9B land between at ~230 tok/s — the MoE benefits from only loading 3B active expert weights per token during prefill. Qwen3-30B-A3B and qwen3.5-27b not measured (deprecated/non-functional).

> **March 14 — Qwen3.5 era:** Ollama upgraded 0.16.1→0.18.0 (required for Qwen3.5). The **qwen3.5-35b-a3b MoE** (35B total, 3B active per token) at IQ2_M quantization is now the smartest model on BC-250: 38 tok/s, 233 tok/s prefill, 16K context, multimodal (vision+tools+thinking). The **qwen3.5:9b** provides 65K context with vision when longer documents are needed. Both are Qwen3.5 architecture — dramatically newer than Qwen3.

> **⚠️ IQ2_M quality tradeoff:** The extreme quantization (~2.5 bits per parameter) is a significant quality compromise — perplexity increases and complex mathematical reasoning degrades compared to higher-precision quantizations. For everyday tasks (summarization, JSON extraction, tool use, chat) the quality is adequate. For tasks requiring precise reasoning, the `qwen3.5:9b` fallback (Q4_K_M, ~4.5 bits) provides substantially better accuracy. This is an informed tradeoff: more knowledge at lower precision vs less knowledge at higher precision.

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
Model            16K  24K  32K  48K  64K
──────────────────────────────────────────
qwen2.5:3b        ✅   ✅   ✅   ✅   ✅
qwen2.5:7b        ✅   ✅   ✅   ✅   ✅
qwen2.5-coder:7b  ✅   ✅   ✅   ✅   ✅
qwen3:8b          ✅   ✅   ✅   ✅   ✅
qwen3-abl:8b      ✅   ✅   ✅   ✅   ✅
seed-coder:8b     ✅   ✅   ✅   ✅   ✅
★ qwen3.5:9b      ✅   ✅   ✅   ✅   ✅
llama3.1:8b       ✅   ✅   ✅   ✅   ❌
lexi-8b           ✅   ✅   ✅   ✅   ❌
gemma2:9b         ✅   ✅   ✅   ✅   —
mistral-nemo:12b  ✅   ✅   ❌   —    —
qwen3:14b         ✅   ✅   ❌   —    —
qwen3-abl:14b     ✅   ✅   ❌   —    —
phi4:14b          ✅   ✅   ✅   —    —
★ 35B-A3B iq2m    ✅   ❌   —    —    —
30B-A3B Q2_K      ✅   ❌   —    —    —
qwen3.5-27b iq2m  ❌   —    —    —    —
```

> 4K and 8K columns omitted — every model passes at those sizes.

> ✅ = works 100% GPU | ❌ = timeout/deadlock | — = not tested (too large)

**Key insight:** Speed is constant across context sizes with FP16 KV (speed only degrades when the context is actually *filled* — see §4.4). The context ceiling is purely a memory constraint: weights + KV cache + compute graph must fit in 16.5 GiB.

**Graphical benchmarks:**

| Generation Speed | Prefill Speed |
|:---:|:---:|
| ![Generation speed](images/charts/generation-speed.png) | ![Prefill speed](images/charts/prefill-speed.png) |

![Generation vs Prefill — all models side by side](images/charts/gen-vs-prefill-all.png)

### 4.3 Context window experiments

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
| **24576** | **~14.4 GB** | **1.5 GB** | **0.9 GB** | **26.7 t/s** | **✅ Max for qwen3:14b** |
| 26624 | ~14.6 GB | 1.3 GB | 1.0 GB | 23.9 t/s | ⚠️ 10% slower |
| 28672 | ~14.2 GB | — | 1.7 GB | timeout | ❌ Deadlocks |
| 32768 | ~15.7 GB | 0.2 GB | 2.1 GB | timeout | ❌ Deadlocks |
| 40960 | ~16.0 GB | 0 | — | — | 💀 TTM fragmentation¹ |

> **24K is the sweet spot** — full speed (~27 tok/s), leaves ~1.5 GB for OS/services with stable swap at 0.9 GB. 26K works but inference drops 10% due to swap pressure. 28K+ deadlocks under Vulkan.
>
> ¹ **Why 40K fails isn't raw OOM.** The math: 9.3 GB weights + 2 GB KV cache + 1 GB OS ≈ 12.3 GB < 16 GB available. The actual failure is **TTM fragmentation** — the kernel's TTM memory manager can't allocate a contiguous block large enough for the KV cache because physical pages are fragmented across GPU and CPU consumers. This is a UMA-specific problem: on discrete GPUs with dedicated VRAM, fragmentation doesn't cross the PCIe boundary.

> **History:** The original 24K experiment (Feb 25) deadlocked because OpenClaw gateway consumed ~700 MB. After v7 removed OpenClaw and bumped GTT to 14 GB (Mar 5), 24K became stable. Flash attention (`OLLAMA_FLASH_ATTENTION=1`) is essential — without it, 24K would not fit.

### 4.4 KV cache quantization — breaking the context ceiling

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

**Not deploying to production.** MoE model (primary) is capped at 16K context — KV quantization provides no benefit (bottleneck is weight size, not KV). Potentially useful for the 9B fallback model at 40K+ context, but not worth the quality risk.
```bash
# If ever needed for 9B model at extreme context:
# Environment=OLLAMA_KV_CACHE_TYPE=q4_0
# in /etc/systemd/system/ollama.service.d/override.conf
```

> **Current production:** FP16 KV (Ollama default). Context capped at 16K for MoE via `OLLAMA_CONTEXT_LENGTH=16384`.

### 4.5 Prefill (prompt evaluation) benchmarks

On UMA, both prefill and generation share memory bandwidth (~51 GB/s DDR4-3200). Prefill is the time the model spends "reading" the prompt before generating the first token.

> **For embedded engineers:** Think of LLM inference as two phases — like a bootloader and a main loop. **Prefill** is the "bootloader": the model processes the entire input prompt in one burst (parallel, compute-bound — like DMA-ing a firmware image into SRAM). **Token generation** is the "main loop": the model produces output tokens one at a time, sequentially (memory-bandwidth-bound — like polling a UART at a fixed baud rate). MoE (Mixture of Experts) is like having 35 specialized ISRs but only routing to 3 of them per interrupt — you get the routing intelligence of knowing all 35, but only pay the execution cost of 3. That's why a 35B-parameter MoE runs faster than a 14B dense model on hardware without matrix cores.

**Prefill rate vs prompt size — production models (FP16 KV cache, warm):**

**qwen3.5-35b-a3b-iq2m (MoE 35B/3B active, UD-IQ2_M):**

| Prompt Size | Tokens | Prefill | Gen tok/s | TTFT (warm) |
|-------------|:------:|--------:|----------:|------------:|
| Tiny | 17 | 53 tok/s | 39.3 | 0.3s |
| Short | 42 | 68 tok/s | 39.6 | 0.6s |
| Medium | 384 | 231 tok/s | 38.5 | 1.7s |
| Long | 1,179 | 228 tok/s | 38.3 | 5.2s |

**qwen3.5:9b (Q4_K_M, dense 9.7B):**

| Prompt Size | Tokens | Prefill | Gen tok/s | TTFT (warm) |
|-------------|:------:|--------:|----------:|------------:|
| Tiny | 17 | 61 tok/s | 33.2 | 0.3s |
| Short | 42 | 118 tok/s | 33.0 | 0.4s |
| Medium | 384 | 229 tok/s | 33.0 | 1.7s |
| Long | 1,179 | 225 tok/s | 32.5 | 5.2s |

> **Observations:** Both production models converge to ~230 tok/s prefill at medium-to-long prompts — the DDR4 bandwidth ceiling. At tiny prompts (<50 tokens), GPU compute overhead dominates and prefill drops to 53–61 tok/s. Generation rate is remarkably stable: MoE holds 38–39 tok/s, 9B holds 32–33 tok/s regardless of prompt size. TTFT scales linearly: at 384 tokens it's ~1.7s, at 1.2K tokens it's ~5.2s. For real-world Signal chat (3K system prompt + conversation), expect TTFT of ~15–20s on cold start, <2s when the model is warm (prompt cached via `OLLAMA_KEEP_ALIVE=30m`).

<details>
<summary><b>Historical: qwen3:14b Q4_K_M (previous primary, 24K context)</b></summary>

| Prompt Size | Tokens | Prefill | Gen tok/s | TTFT (warm) |
|-------------|:------:|--------:|----------:|------------:|
| Tiny | 86 | 88 tok/s | 27.2 | ~1s |
| Short | 353 | 67 tok/s | 27.2 | ~5s |
| Medium | 1,351 | 128 tok/s | 26.1 | ~11s |
| Long | 3,354 | 113 tok/s | 24.6 | ~30s |
| XL | 6,686 | 88 tok/s | 22.5 | ~76s |
| Massive | 10,014 | 70 tok/s | 20.7 | ~143s |

> Generation rate degrades with context: 27.2 tok/s @small → 20.7 tok/s @10K tokens.

</details>

**Graphical: prefill rate and generation rate vs prompt size:**

![Prefill and generation rate vs prompt size](images/charts/prefill-vs-prompt-size.png)

**Model Landscape Bubble Chart** — generation speed × prefill speed × max context (bubble size = context window, unique color per model):

![Model landscape — numbered 3D](images/charts/model-landscape-3d.png)

![Model landscape — bubble chart](images/charts/model-landscape-3d-labeled.png)

### 4.6 Memory budget

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

### 4.7 Model recommendations

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
> The MoE wins over the 9B dense model in generation speed (38 vs 32 tok/s) because only 3B parameters activate per token on hardware without matrix cores — fewer multiplications wins. Both models achieve similar prefill rates (~230 tok/s at ~400 tokens), but the 9B wins in context capacity (65K vs 16K) because its smaller total weight leaves more room for KV cache.

```bash
# Primary model (smartest, 35B MoE) — custom GGUF via Modelfile
# See tmp/Modelfile-qwen35-35b-a3b for setup
ollama create qwen3.5-35b-a3b-iq2m -f Modelfile-qwen35-35b-a3b

# High-context model (vision+65K, official Ollama)
ollama pull qwen3.5:9b

# Context is capped via OLLAMA_CONTEXT_LENGTH=16384 in systemd (see §3.3, §3.4)
# Individual requests can override with {"options": {"num_ctx": 65536}} when using 9b
```

> **Why not a bigger MoE?** Even though only 3B params activate per token, **all 35B params must reside in memory** — the router decides per-token which experts to fire, so every weight must be loaded. At IQ2_M (~2.5 bits per parameter), 35B = 11 GB GGUF. The next MoE up — Qwen3-235B-A22B — would be ~44 GB at IQ2_M (2.7× too large). Mixtral 8×22B (141B) would be ~35 GB. Going below IQ2_M (e.g. IQ1_S at ~1.5 bits) causes quality collapse. The qwen3.5-35b-a3b at IQ2_M is the **largest MoE that fits 16 GB with usable quantization** — the Pareto frontier for this hardware.

---

# `PART II` — AI Stack

## 5. Signal Chat Bot

The BC-250 runs a personal AI assistant accessible via Signal messenger — no gateway, no middleware. signal-cli runs as a standalone systemd service exposing a JSON-RPC API, and queue-runner handles all LLM interaction directly.

```
  Signal --> signal-cli (JSON-RPC :8080) --> queue-runner --> Ollama --> GPU (Vulkan)
```

> **Software:** signal-cli v0.13.24 (native binary) · Ollama 0.18+ · queue-runner v7

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
2. Run sd-cli with FLUX.2-klein-9B (4 steps, 512×512, ~105s)
3. Send image as Signal attachment
4. Restart Ollama

Bot is offline during generation (~2–3 minutes total including model reload).

**Image editing (Kontext):** Send a photo to Signal with an edit instruction ("make it cyberpunk", "add a hat"). The LLM emits `EXEC(/opt/stable-diffusion.cpp/edit-image "instruction")`, queue-runner runs FLUX.1-Kontext-dev with the photo as reference, and sends back the edited image (~5–10 min @512²). Input images are automatically resized to 512×512. See §6.2 for a demo (Sonic → Shadow the Hedgehog).

**Video generation:** Ask for a video/animation. Uses WAN 2.1 T2V 1.3B (~38 min for 17 frames @480×320).

**ESRGAN upscale:** Ask to upscale the last generated image. 4× upscale via RealESRGAN_x4plus (~30s).

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
| Image generation (FLUX.2-klein-9B 512²) | ~105s |
| Image editing (Kontext 512²) | ~5 min |
| Video generation (WAN 2.1 480×320) | ~38 min |
| ESRGAN 4× upscale | ~30s |
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

**FLUX.2-klein-9B** — recommended, best quality, Apache 2.0:

```bash
mkdir -p /opt/stable-diffusion.cpp/models/flux2 && cd /opt/stable-diffusion.cpp/models/flux2
# Diffusion model (9B, Q4_0, 5.3 GB)
curl -L -O "https://huggingface.co/leejet/FLUX.2-klein-9B-GGUF/resolve/main/flux-2-klein-9b-Q4_0.gguf"
# Qwen3-8B text encoder (Q4_K_M, 4.7 GB)
curl -L -o qwen3-8b-Q4_K_M.gguf "https://huggingface.co/unsloth/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf"
# FLUX.2 VAE (321 MB) — different from FLUX.1 VAE!
curl -L -o flux2-vae.safetensors "https://huggingface.co/Comfy-Org/vae-text-encorder-for-flux-klein-4b/resolve/main/split_files/vae/flux2-vae.safetensors"
```

> Memory: 5.3 GB VRAM (diffusion) + 6.2 GB VRAM (Qwen3-8B encoder) + 95 MB (VAE) = ~11.8 GB total. Stresses the 16.5 GB Vulkan pool properly. Best quality of all tested models.

**FLUX.2-klein-4B** — fast alternative, Apache 2.0:

```bash
cd /opt/stable-diffusion.cpp/models/flux2
# Diffusion model (4B, Q4_0, 2.3 GB)
curl -L -O "https://huggingface.co/leejet/FLUX.2-klein-4B-GGUF/resolve/main/flux-2-klein-4b-Q4_0.gguf"
# Qwen3-4B text encoder (Q4_K_M, 2.4 GB)
curl -L -o qwen3-4b-Q4_K_M.gguf "https://huggingface.co/unsloth/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf"
# Reuses same flux2-vae.safetensors from above
```

> Memory: 2.3 GB VRAM (diffusion) + 3.6 GB VRAM (Qwen3-4B encoder) + 95 MB (VAE) = ~6 GB total. 7× faster than 9B but lower quality. Good for quick previews.

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

**🏆 FLUX.2-klein-9B Q4_0 — new default (best quality):**

| Resolution | Steps | Time | s/step | Notes |
|:----------:|:-----:|:----:|:------:|-------|
| 512×512 | 4 | **104s** | 15.4 | Default, ~11.8 GB VRAM total |
| 768×768 | 4 | **129s** | 21.3 | Best balance of quality vs time |

> FLUX.2-klein-9B uses a Qwen3-8B LLM as text encoder — richer prompt understanding and finer detail than the 4B variant. Stresses the 16.5 GB Vulkan pool properly (11.8 GB used). The `--offload-to-cpu` flag is essential (manages UMA allocation pools).

**FLUX.2-klein-4B Q4_0 — fast alternative:**

| Resolution | Steps | Time | s/step | Notes |
|:----------:|:-----:|:----:|:------:|-------|
| 512×512 | 4 | **20s** | 3.95 | Fast preview, ~6 GB VRAM total |
| 512×512 | 8 | **26s** | 2.66 | Better quality, GPU warm |
| 768×768 | 4 | **30s** | 5.43 | Great quality, no tiling |
| 1024×1024 | 4 | **63s** | 10.18 | VAE tiling required |
| 1024×1024 | 4 | ❌ FAIL | — | Without `--vae-tiling` (VAE OOM) |

> 7× faster than 9B but noticeably less detailed. Good for quick previews or batch generation.

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
| **FLUX.2-klein-9B** | **104s** | **129s** | **11.8 GB** | 5.3 GB | Qwen3-8B (4.7 GB) |
| FLUX.2-klein-4B | 20s | 30s | 6 GB | 2.3 GB | Qwen3-4B (2.4 GB) |
| FLUX.1-schnell | 30s | 91s | 10 GB | 6.5 GB | CLIP+T5 (3.4 GB) |
| Chroma flash | 85s | 240s⁸ | 8.4 GB | 5.1 GB | T5 (3.2 GB) |
| FLUX.1-dev | 279s²⁰ | ❌ | 10 GB | 6.8 GB | CLIP+T5 (3.4 GB) |
| SD-Turbo | 11s¹ | 21s | 2 GB | 2 GB | (built-in) |

> FLUX.2-klein-9B is the quality winner — more detail, better text understanding, and it actually stresses the 16.5 GB GPU properly (11.8 GB used vs 6 GB for 4B). The 4B version is 7× faster but leaves 10 GB unused.

**🔬 Quality shootout — same prompt, same seed (42), 512×512 @4 steps:**

All models tested back-to-back on the same prompt: *"a cyberpunk cityscape at sunset with neon lights reflecting on wet streets, highly detailed"*

| Model | Time | s/step | VRAM | File Size | Quality |
|-------|:----:|:------:|:----:|:---------:|:-------:|
| **FLUX.2-klein-9B** | **104s** | 15.4 | 11.8 GB | 709 KB | **★★★★** — finest detail, best reflections |
| FLUX.2-klein-4B | 15s | 2.7 | 6.0 GB | 704 KB | ★★★ — good but less detail |
| FLUX.1-schnell | 31s | 6.5 | 10.1 GB | 609 KB | ★★ — decent, less coherent |
| Chroma flash (8 steps) | 120s | 14.1 | 8.4 GB | 204 KB | ★★ — artistic but softer |

**Example outputs** (same prompt, same seed 42, 512×512):

| FLUX.2-klein-9B (★★★★) | FLUX.2-klein-4B (★★★) |
|:-:|:-:|
| ![9B](images/shootout/shootout-9b-512.png) | ![4B](images/shootout/shootout-4b-512.png) |
| **104s**, 11.8 GB VRAM | **15s**, 6.0 GB VRAM |

| FLUX.1-schnell (★★) | Chroma flash (★★) |
|:-:|:-:|
| ![schnell](images/shootout/shootout-schnell-512.png) | ![chroma](images/shootout/shootout-chroma-512.png) |
| **31s**, 10.1 GB VRAM | **120s**, 8.4 GB VRAM |

> The 9B model produces visibly more detail in fine structures (neon reflections, wet streets, building facades). The 4B is the speed champion but sacrifices detail. Chroma has a distinctive artistic style but outputs smaller, softer images. FLUX.1-schnell sits in the middle.

**Summary: recommended settings for production**

| Use case | Model | Resolution | Steps | Time |
|----------|-------|:----------:|:-----:|:----:|
| **Default (Signal)** | **FLUX.2-klein-9B** | **512×512** | **4** | **~105s** |
| **High quality** | **FLUX.2-klein-9B** | **768×768** | **4** | **~130s** |
| Quick preview | FLUX.2-klein-4B | 512×512 | 4 | ~20s |
| Poster/wallpaper | FLUX.2-klein-4B | 1024×1024 | 4 | ~63s |
| Best quality (slow) | Chroma flash | 512×512 | 8 | ~130s |

```bash
# FLUX.2-klein-9B — recommended production command:
/opt/stable-diffusion.cpp/build/bin/sd-cli \
  --diffusion-model models/flux2/flux-2-klein-9b-Q4_0.gguf \
  --vae models/flux2/flux2-vae.safetensors \
  --llm models/flux2/qwen3-8b-Q4_K_M.gguf \
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
| **FLUX.2-klein-9B Q4_0** | **9B** | **5.3 GB** | **~11.8 GB** | **4** | **★★★★** | **✅ Current default, 104s @512²** |
| FLUX.2-klein-4B Q4_0 | 4B | 2.3 GB | ~6 GB | 4 | ★★★ | ✅ Fast alternative, 20s @512² |
| FLUX.1-schnell Q4_K | 12B | 6.5 GB | ~10 GB | 4 | ★★ | ✅ Previous default, 30s @512² |
| Chroma flash Q4_0 | 12B | 5.1 GB | ~8.4 GB | 4–8 | ★★★ | ✅ Tested — 85s @512², better quality |
| FLUX.1-dev Q4_K_S | 12B | 6.8 GB | ~10 GB | 20 | ★★★★ | ✅ Tested — 279s @512², ❌768²+ |
| SD-Turbo | 1.1B | ~2 GB | ~2.5 GB | 1–4 | ★ | ✅ Fast preview, 11s @512² |
| SD3.5-medium Q4_0 | 2.5B | 1.7 GB | ~6 GB | 28 | ★★★ | ✅ Tested — 49s @512², needs clip_g+clip_l+T5+F16 VAE³ |

> ¹ Total RAM includes diffusion model + text encoder(s) + VAE.
>
> ³ BF16 VAE gotcha — see SD3.5 section below.

**Video generation — tested models:**

| Model | Params | GGUF Size | Total RAM¹ | Frames | Time | Status |
|-------|:------:|:---------:|:----------:|:------:|:----:|--------|
| **WAN 2.1 T2V 1.3B Q4_0** | **1.3B** | **826 MB** | **~5 GB** | **17 @480×320** | **~38 min** | **✅ First video on BC-250!** |

> WAN requires umt5-xxl text encoder (3.5 GB Q4_K_M) + WAN VAE (243 MB). Outputs raw AVI (MJPEG). No matrix cores = slow but works.

**Video generation — tested (OOM):**

| Model | Params | GGUF Size | Total RAM¹ | Notes |
|-------|:------:|:---------:|:----------:|-------|
| WAN 2.2 TI2V 5B Q4_0 | 5B | 2.9 GB | **~9 GB** | **❌ OOM crash at Q4_0.** Model (2.9G) + VAE (1.4G) + T5 (4.7G) = 9 GB — exceeds UMA budget during video denoising. May work with Q2_K model + Q2_K T5 (~6 GB) but untested. |

**Image editing — FLUX.1-Kontext-dev:**

| Model | Params | GGUF Size | Total RAM¹ | Status |
|-------|:------:|:---------:|:----------:|--------|
| FLUX.1-Kontext-dev Q4_0 | 12B | 6.8 GB | ~10 GB | ✅ Tested — 316s @512² (no swap). 1024² causes swap pressure (40+ min). Uses `-r` flag, reuses FLUX.1 T5/CLIP/VAE |

> Kontext is a dedicated image editing model by Black Forest Labs. It takes a reference image via `-r` and a text instruction to produce an edited version. Uses existing FLUX.1 encoders (T5-XXL, CLIP_L) and VAE (ae.safetensors) from `/opt/stable-diffusion.cpp/models/flux/`.
> ```bash
> # Edit an existing image with Kontext:
> sd-cli --diffusion-model models/flux/flux1-kontext-dev-Q4_0.gguf \
>   --vae models/flux/ae.safetensors --clip_l models/flux/clip_l.safetensors \
>   --t5xxl models/flux/t5-v1_1-xxl-encoder-Q4_K_M.gguf --clip-on-cpu \
>   -r input.png -p "change the sky to sunset" --cfg-scale 3.5 --steps 28 \
>   --sampling-method euler --offload-to-cpu --diffusion-fa -o output.png
> ```

**Kontext demo — "turn Sonic into Shadow the Hedgehog":**

| Input (1200×1600 → resized to 512×512) | Output (512×512, 647s) |
|:---:|:---:|
| ![Kontext input](images/kontext/kontext-input.jpg) | ![Kontext output](images/kontext/kontext-output.png) |

#### SD3.5-medium benchmark details

**Timing breakdown (512×512, 28 steps, seed 42):**

| Phase | Time | Notes |
|-------|:----:|-------|
| CLIP + T5 encoding | 3.5s | clip_l + clip_g + t5-v1_1-xxl Q4_K_M |
| Diffusion sampling | 43s | 28 steps × 1.5s/it (mmdit 2.1 GB on Vulkan) |
| VAE decode | 2.3s | F16-converted VAE (94.6 MB) |
| **Total** | **49s** | |

**Model stack on disk:**

| Component | File | Size |
|-----------|------|:----:|
| Diffusion | sd3.5_medium-q4_0.gguf | 1.7 GB |
| CLIP-L | clip_l.safetensors (shared with FLUX) | 246 MB |
| CLIP-G | clip_g.safetensors | 1.3 GB |
| T5-XXL | t5-v1_1-xxl-encoder-Q4_K_M.gguf (shared with FLUX) | 2.9 GB |
| VAE | sd3_vae_f16.safetensors (converted from BF16) | 160 MB |
| **Total on disk** | | **~6.3 GB** |

```bash
# SD3.5-medium generation command:
sd-cli --diffusion-model models/sd3/sd3.5_medium-q4_0.gguf \
  --vae models/sd3/sd3_vae_f16.safetensors \
  --clip_l models/flux/clip_l.safetensors \
  --clip_g models/sd3/clip_g.safetensors \
  --t5xxl models/flux/t5-v1_1-xxl-encoder-Q4_K_M.gguf \
  -p "prompt" --cfg-scale 4.5 --sampling-method euler --steps 28 \
  -W 512 -H 512 --diffusion-fa --offload-to-cpu -o output.png
```

> **⚠ BF16 VAE gotcha:** The upstream SD3 VAE (`diffusion_pytorch_model.safetensors`) uses BF16 tensors. GFX1013 Vulkan has no BF16 support — the output is a solid blue/yellow rectangle. Fix: convert to F16 with `python3 convert_vae_bf16_to_f16.py input.safetensors output.safetensors` (script in `/tmp/`).

#### WAN 2.1 T2V 1.3B benchmark details — first video generation on BC-250

**Timing breakdown (480×320, 17 frames, 50 steps, seed 42):**

| Phase | Time | Notes |
|-------|:----:|-------|
| umt5-xxl encoding | ~4s | 3.5 GB Q4_K_M text encoder |
| Diffusion sampling | ~35 min | 17 frames × 50 steps. No matrix cores → pure scalar Vulkan |
| VAE decode | ~30s | WAN VAE (243 MB), decodes all 17 frames |
| **Total** | **~38 min** | |

**Model stack on disk:**

| Component | File | Size |
|-----------|------|:----:|
| Diffusion | Wan2.1-T2V-1.3B-Q4_0.gguf | 826 MB |
| Text encoder | umt5-xxl-encoder-Q4_K_M.gguf | 3.5 GB |
| VAE | wan_2.1_vae.safetensors | 243 MB |
| **Total on disk** | | **~4.5 GB** |

```bash
# WAN 2.1 text-to-video generation:
sd-cli -M vid_gen \
  --diffusion-model models/wan/Wan2.1-T2V-1.3B-Q4_0.gguf \
  --vae models/wan/wan_2.1_vae.safetensors \
  --t5xxl models/wan/umt5-xxl-encoder-Q4_K_M.gguf \
  -p "A cat walking across a sunny garden" \
  --cfg-scale 6.0 --sampling-method euler \
  -W 480 -H 320 --diffusion-fa --offload-to-cpu \
  --video-frames 17 --flow-shift 3.0 -o output.mp4
```

> **Output format:** sd.cpp produces raw AVI (MJPEG) regardless of the `-o` extension. The 17-frame clip plays at 16 fps (~1 second). Quality is recognizable but noisy — expected at Q4_0 with scalar-only Vulkan compute.
>
> **Why so slow?** Each video frame is a full diffusion pass through the 1.3B model. With 17 frames × 50 steps × no matrix cores, every multiply is scalar. A GPU with tensor/matrix units (RDNA3+, Turing+) would be 5–10× faster.

**WAN 2.1 demo — "A cat walking across a sunny garden":**

<p align="center">
  <img src="images/wan-test.gif" alt="WAN 2.1 T2V — cat in garden" width="480">
</p>

> 17 frames @480×320, 50 steps, Q4_0 quantization, EUR scheduler, cfg-scale 6.0. Generated in **~38 minutes** on GFX1013 scalar Vulkan — no matrix/tensor cores. The BC-250 rendered every frame through pure ALU compute. Noisy but recognizable — a real video from a 1.3B parameter model on a $70 ex-telecom board.

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

The queue prioritizes **data diversity** — all dashboard tabs get fresh data even if the cycle is interrupted. See §7.3 for the full category breakdown with GPU times. HA observations are interleaved every 50 jobs, and Signal chat is checked between every job.

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
| `career-digest.py` | Weekly career digest → Signal (Sunday) | 1 |
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

### 7.4 Data flow & locations

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

### 7.5 Dashboard — 29 main pages + 101 host detail pages

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

### 7.6 GPU monitoring — 3-state

Per-minute sampling via `pp_dpm_sclk`:

| State | Clock | Temp | Meaning |
|-------|:-----:|:----:|---------|
| `generating` | 2000 MHz | ~77°C | Active LLM inference |
| `loaded` | 1000 MHz | ~56°C | Model in VRAM, idle |
| `idle` | 1000 MHz | <50°C | No model loaded |

### 7.7 Configuration & state files

| File | Purpose |
|------|---------|
| `profile.json` | Public interests — tracked repos, keywords, technologies |
| `profile-private.json` | Career context — target companies, salary expectations *(gitignored)* |
| `watchlist.json` | Auto-evolving interest tracker |
| `digest-feeds.json` | Mailing list feed URLs (8 feeds from lore.kernel.org) |
| `repo-feeds.json` | Repository API endpoints |
| `sensor-watchlist.json` | CSI camera sensor tracking list |
| `queue-runner-state.json` | Cycle count, resume index *(in data/)* |
| `/opt/netscan/data/jobs.json` | All 337 job definitions |

### 7.8 Resilience

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
│   ├── career-digest.py            # Weekly career digest → Signal (Sunday)
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
├── generate-and-send.sh            → /opt/stable-diffusion.cpp/ (legacy EXEC pattern, intercepted by queue-runner)
└── generate-and-send-worker.sh     → legacy async worker (unused in v7, kept for EXEC pattern match)
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

GTT tuning not applied. Check: `cat /sys/module/ttm/parameters/pages_limit` (should be 4194304). See §3.3.

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

## 11. Known Limitations & Ideas

### ⚠ Limitations

| Issue | Impact |
|-------|--------|
| Shared VRAM | Image gen requires stopping Ollama. Bot offline ~2–3 min (FLUX.2-klein-9B) or ~1 min (FLUX.2-klein-4B). |
| MoE context limit | 35B-A3B MoE tops out at 16K context (weights = 10.3 GiB, KV fills rest). Use 9B for >16K. |
| Signal latency | Messages queue during job execution (typical job 2–15 min). Chat checked between every job. |
| sd-cli hangs on GFX1013 | Vulkan cleanup bug → poll + kill workaround. |
| Cold start latency | 30–60s after Ollama restart (model loading). |
| Chinese thinking leak | Qwen3 occasionally outputs Chinese reasoning. Cosmetic. |
| Prefill rate degrades with context | 128 tok/s at 1.3K → 70 tok/s at 10K tokens (UMA bandwidth + attention scaling). |
| Gen speed degrades with context fill | 27 tok/s empty → 13 tok/s at 30K tokens. Partial model offload at KV limit causes cliff drop. |
| Ollama caps KV auto-size at ~40K (Q4_0) | `num_ctx` > 40960 accepted but silently truncated. Actual limit = VRAM ÷ per-token KV size. |

### ✅ Tested Ideas — experiment results

| Idea | Result | Details |
|------|--------|---------|
| **80K+ context on $100 hardware** | **Debunked — Ollama caps at ~40K.** | KV Q4_0 halves cache (3.8→1.1 GiB @24K) with zero speed loss (27.3 tok/s). But Ollama's scheduler auto-sizes KV to what fits: 9B weights (8.2 GiB) + max KV (1.8 GiB) = ~40K real ceiling. Requesting `num_ctx=128K` is accepted but silently truncated. Q8_0 spills to CPU at 80K (7 tok/s — unusable). Full data in §4.4. |
| **ESRGAN upscale via Signal** | **Done — on-demand upscale works.** | `EXEC(upscale /path/to/image)` → sd-cli 4× RealESRGAN_x4plus → sends upscaled image via Signal. ~30s for 512²→2048². Deployed in queue-runner, GPU-swap managed (stop Ollama → upscale → restart). |

### 💡 Ideas — untested frontiers

| Idea | What | Why it might work on BC-250 |
|------|------|----------------------------|
| **Speculative decoding** | 3B draft model + 35B MoE verifier | qwen2.5:3b runs at 104 tok/s, MoE at 38 tok/s. Draft proposes N tokens, MoE verifies in one pass. Ollama supports `--draft-model`. Could push effective throughput above 50 tok/s if draft acceptance rate is >60%. MoE's sparse activation makes verification cheap. |
| **Vision analysis via Signal** | Send photo → qwen3.5:9b describes/analyzes it (no sd-cli) | 9B model has native vision (multimodal). No GPU swap needed — just pass the image via Ollama's `/api/chat` with base64 image. Different from Kontext (edit) — this is understanding. "What's in this photo?", "Read this receipt", "Identify this plant." |
| **Auto-upscale pipeline** | Automatically ESRGAN every generated image | On-demand ESRGAN already works (see Tested above). This would chain it after every `generate_and_send_image()` — send both 512² thumbnail and 2048² full-res via Signal. ~30s extra per image. |
| **Smart model routing** | Auto-switch between MoE and 9B based on task | If prompt >8K tokens or contains an image → use 9B (65K context, vision). Otherwise → MoE (faster, smarter). queue-runner already manages both models. Could count tokens before sending and route automatically. |

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

**What replaced it:** See §5 for the current architecture.

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
