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

`Zen 2 · RDNA 1 · 16 GB unified · Vulkan · 14B @ 27 tok/s · 336 autonomous jobs/cycle · 130 dashboard pages`

</div>

> A complete guide to running a 14B-parameter LLM, image generation, and 336 autonomous jobs on the AMD BC-250 — an obscure APU (Zen 2 CPU + Cyan Skillfish RDNA 1 GPU) found in Samsung's blockchain/distributed-ledger rack appliances. Not a "crypto mining GPU," not a PS5 prototype — it's a custom SoC that Samsung used for private DLT infrastructure, repurposed here as a headless AI server with a community-patched BIOS.
>
> **March 2026** · Hardware-specific driver workarounds, memory tuning discoveries, context window experiments, and real-world benchmarks that aren't documented anywhere else.

> **What makes this unique:** The BC-250's Cyan Skillfish GPU (`GFX1013`) is possibly the only RDNA 1 silicon running production LLM inference. ROCm doesn't support it. OpenCL doesn't expose it. The only viable compute path is **Vulkan** — and even that required discovering two hidden kernel memory bottlenecks (GTT cap + TTM pages_limit) before 14B models would run.

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
| [6](#6-image-generation) | Image Generation | Creative users | FLUX.1-schnell, synchronous pipeline |
| | **`PART III ─ MONITORING & INTEL`** | | |
| [7](#7-netscan-ecosystem) | Netscan Ecosystem | Home lab admins | 336 jobs, queue-runner v7, 130-page dashboard |
| [8](#8-career-intelligence) | Career Intelligence | Job seekers | Two-phase scanner, salary, patents |
| | **`PART IV ─ REFERENCE`** | | |
| [9](#9-repository-structure) | Repository Structure | Contributors | File layout, deployment paths |
| [10](#10-troubleshooting) | Troubleshooting | Everyone | Common issues and fixes |
| [11](#11-known-limitations--todo) | Known Limitations & TODO | Maintainers | What's broken, what's planned |
| [A](#appendix-a-openclaw-archive) | OpenClaw Archive | Historical | Original architecture, why we ditched it |

---

# `PART I` — Hardware & Setup

## 1. Hardware Overview

The AMD BC-250 is a custom APU originally designed for Samsung's blockchain/distributed-ledger rack appliances (not a traditional "mining GPU"). It's a full SoC — Zen 2 CPU and Cyan Skillfish RDNA 1 GPU on a single package, with 16 GB of on-package unified memory. Samsung deployed these in rack-mount enclosures for private DLT workloads; decommissioned boards now sell for ~$100–150 on the secondhand market, making them possibly the cheapest way to run 14B LLMs on dedicated hardware.

> **Not a PlayStation 5.** Despite superficial similarities (both use Zen 2 + 16 GB memory), the BC-250 has nothing to do with the PS5. The PS5's Oberon SoC is **RDNA 2** (GFX10.3, gfx1030+) with hardware ray tracing; the BC-250's Cyan Skillfish is **RDNA 1** (GFX10.1, gfx1013) — one full GPU architecture generation earlier, no RT hardware. LLVM's AMDGPU processor table lists GFX1013 as product "TBA" under GFX10.1, confirming it was never a retail part. Samsung also licensed RDNA 2 for mobile (Exynos 2200 / Xclipse 920) — that's a completely separate deal.

> **BIOS and CPU governor are not stock.** The board ships with a minimal Samsung BIOS meant for rack operation. A community-patched BIOS (from [Miyconst's YouTube tutorial](https://www.youtube.com/watch?v=YLO3fYyCo2s)) enables standard UEFI features (boot menu, NVMe boot, fan control). The CPU `performance` governor is set explicitly — the stock `schedutil` governor causes latency spikes during LLM inference.

| Component | Details |
|-----------|---------|
| **CPU** | Zen 2 — 6c/12t @ 2.0 GHz |
| **GPU** | Cyan Skillfish — RDNA 1, `GFX1013`, 24 CUs (1536 SPs) |
| **Memory** | **16 GB unified** (16 × 1 GB on-package), shared CPU/GPU |
| **VRAM** | 512 MB dedicated framebuffer |
| **GTT** | **14 GiB** (tuned, default 7.4 GiB) — `amdgpu.gttsize=14336` |
| **Vulkan total** | **14.5 GiB** after tuning |
| **Storage** | 475 GB NVMe |
| **OS** | Fedora 43, kernel 6.18.9, headless |
| **TDP** | 220W board (idle: 35–45W) |
| **BIOS** | Community-patched UEFI (not Samsung stock) — [Miyconst tutorial](https://www.youtube.com/watch?v=YLO3fYyCo2s) |
| **CPU governor** | `performance` (stock `schedutil` causes LLM latency spikes) |
| **IP** | `192.168.3.151` |

### Unified memory is your friend (but needs tuning)

CPU and GPU share the same 16 GB pool. Only 512 MB is carved out as VRAM — the rest is accessible as **GTT (Graphics Translation Table)**.

**Two bottlenecks must be fixed:**

1. **GTT cap** — `amdgpu` driver defaults to 50% of RAM (~7.4 GiB). Fix: `amdgpu.gttsize=14336` in kernel cmdline → GPU gets 14 GiB GTT.
2. **TTM pages_limit** — kernel TTM memory manager independently caps allocations at ~7.4 GiB. Fix: `ttm.pages_limit=4194304` (16 GiB in 4K pages).

After both fixes: Vulkan sees **14.5 GiB** — enough for **14B parameter models at 16K context, 100% GPU**.

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
Environment=OLLAMA_CONTEXT_LENGTH=16384
OOMScoreAdjust=-1000
EOF
sudo systemctl daemon-reload && sudo systemctl restart ollama
```

> `OOMScoreAdjust=-1000` protects Ollama from the OOM killer — the model process must survive at all costs (see §3.4).

> ROCm will crash during startup — expected and harmless. Ollama catches it and uses Vulkan.

### 3.2 Tune GTT size

```bash
sudo grubby --update-kernel=ALL --args="amdgpu.gttsize=14336"
# Reboot required. Verify:
cat /sys/class/drm/card1/device/mem_info_gtt_total  # → 15032385536 (14 GiB)
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

### 3.4 Context window — the silent OOM killer

Ollama allocates KV cache based on the model's declared context window. The default `qwen3-abliterated:14b` declares `num_ctx 40960` — that's **16 GB** of KV cache + weights, exceeding available RAM and triggering OOM kills.

**Fix:** Bake a hard context cap into a custom Ollama model:

```bash
cat > /tmp/Modelfile.16k << 'EOF'
FROM huihui_ai/qwen3-abliterated:14b
PARAMETER num_ctx 16384
EOF

ollama create qwen3-14b-16k -f /tmp/Modelfile.16k
```

This reduces total memory from **~16 GB** (40960 ctx) to **~11.1 GB** (16384 ctx). The model name `qwen3-14b-16k` is then used everywhere in OpenClaw config.

> ⚠️ Setting `OLLAMA_CONTEXT_LENGTH` in the environment is not reliable — some API calls bypass it. The Modelfile approach is the only guaranteed fix.

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
| VRAM carveout | 512 MB | Dedicated framebuffer |
| GTT | **14 GiB** | Tuned ▲ (default 7.4 GiB) — `amdgpu.gttsize=14336` |
| TTM pages_limit | **16 GiB** | Tuned ▲ (default ~7.4 GiB) — `ttm.pages_limit=4194304` |

| Vulkan heap | Size |
|-------------|------|
| Device-local | 8.33 GiB |
| Host-visible | 4.17 GiB |
| **Total** | **12.5 GiB** → 14B models fit, 100% GPU, zero-copy |

| Consumer | Usage | Notes |
|----------|-------|-------|
| Model (qwen3-14b-16k) | ~11.1 GiB | GPU memory |
| signal-cli + queue-runner | ~1.0 GiB | System RAM |
| OS + services | ~1.5 GiB | System RAM |
| NVMe swap | 16 GiB | Safety net |
| zram | 2 GiB | Boot-limited ▼ |
| **Status** | **Stable** | 16K context, swap headroom ~14 GB |

---

## 4. Models & Benchmarks

### 4.1 Compatibility table

> Ollama 0.16.1 · Vulkan · RADV Mesa 25.3.4 · March 2026

| Model | Params | Quant | VRAM | GPU | tok/s | Status |
|-------|:------:|:-----:|:----:|:---:|:-----:|--------|
| qwen2.5:3b | 3B | Q4_K_M | 2.4 GB | 100% | **101** | ✅ Fast, lightweight |
| qwen2.5:7b | 7B | Q4_K_M | 4.7 GB | 100% | **59** | ✅ Great quality/speed |
| qwen2.5-coder:7b | 7B | Q4_K_M | 4.7 GB | 100% | **57** | ✅ Code-focused |
| llama3.1:8b | 8B | Q4_K_M | 4.9 GB | 100% | **75** | ✅ Fastest 8B |
| mannix/llama3.1-8b-lexi | 8B | Q4_K_M | 4.7 GB | 100% | **73** | ✅ Uncensored 8B |
| huihui_ai/seed-coder-abliterate | 8B | Q4_K_M | 5.1 GB | 100% | **52** | ✅ Code gen, uncensored |
| qwen3:8b | 8B | Q4_K_M | 5.2 GB | 100% | **44** | ✅ Thinking mode |
| gemma2:9b | 9B | Q4_K_M | 5.4 GB | 91% | **26** | ⚠️ Spills to CPU |
| mistral-nemo:12b | 12B | Q4_K_M | 7.1 GB | 100% | **34** | ✅ Good 12B alternative |
| phi4:14b | 14B | Q4_K_M | 9.1 GB | 100% | **25** | ✅ Strong reasoning |
| **qwen3:14b** | **14B** | **Q4_K_M** | **9.3 GB** | **100%** | **27** | **✅ Primary model** |
| huihui_ai/qwen3-abliterated:14b | 14B | Q4_K_M | 9.0 GB | 100% | **27.5** | ✅ Uncensored variant |
| Qwen3-30B-A3B (Q2_K) | 30B | Q2_K | 11 GB | 100% | **~12** | ⚠️ Fits but slow, heavy quant |

> ⚠️ 14B models require both GTT (§3.2) and TTM (§3.3) tuning. 30B MoE fits only at Q2_K (heavy quality loss).

### 4.2 Benchmark visualization

```
  Token generation speed (tok/s) — higher is better
  ──────────────────────────────────────────────────────────────────────

  qwen2.5:3b         ████████████████████████████████████████████████████ 101
  llama3.1:8b         ██████████████████████████████████████░░░░░░░░░░░░░  75
  lexi-8b             █████████████████████████████████████░░░░░░░░░░░░░░  73
  qwen2.5:7b          ██████████████████████████████░░░░░░░░░░░░░░░░░░░░░  59
  qwen2.5-coder:7b    █████████████████████████████░░░░░░░░░░░░░░░░░░░░░░  57
  seed-coder:8b       ██████████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░  52
  qwen3:8b            ██████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  44
  mistral-nemo:12b    █████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  34
  qwen3:14b ← prod    █████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  27
  gemma2:9b            █████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  26
  phi4:14b             ████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  25
  Qwen3-30B-A3B Q2_K   ██████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  12

  ──────────────────────────────────────────────────────────────────────
  VRAM usage (GB) — lower is better (14.5 GB max)
  ──────────────────────────────────────────────────────────────────────

  qwen2.5:3b        ██░░░░░░░░░░░░░  2.4 GB
  qwen2.5:7b        ███░░░░░░░░░░░░  4.7 GB
  llama3.1:8b       ████░░░░░░░░░░░  4.9 GB
  qwen3:8b          ████░░░░░░░░░░░  5.2 GB
  gemma2:9b         ████░░░░░░░░░░░  5.4 GB
  mistral-nemo:12b  █████░░░░░░░░░░  7.1 GB
  phi4:14b          ██████░░░░░░░░░  9.1 GB
  qwen3:14b         ███████░░░░░░░░  9.3 GB ← production
  Qwen3-30B-A3B     ████████░░░░░░░ 11.0 GB ← barely fits
                    ▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔
                    0    5   10  14.5 GB (Vulkan max)
```

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
             phi4: 25 tok/s, good reasoning but slower than qwen3.
             30B MoE: fits at Q2_K (11 GB) but ~12 tok/s, heavy quality loss.
             seed-coder: decent for code, 52 tok/s, but not general-purpose.
             Decision: keep qwen3:14b as primary. ✅
```

### 4.4 Context window experiments

The context window directly controls KV cache size, and on 16 GB unified memory, every megabyte counts:

```
  Context Window vs Memory (qwen3:14b Q4_K_M)
  ─────────────────────────────────────────────────────────────────
  ctx=8192   │████████░░░░░░░░│ ~9.5 GB total  │ 6.5 GB free  │ ✅ safe
  ctx=12288  │██████████░░░░░░│ ~10.3 GB total  │ 5.7 GB free  │ ✅ conservative
  ctx=16384  │████████████░░░░│ ~11.1 GB total  │ 4.9 GB free  │ ✅ production ←
  ctx=24576  │██████████████░░│ ~12.3 GB total  │ 3.7 GB free  │ ❌ deadlocks
  ctx=32768  │████████████████│ ~13.5 GB total  │ 2.5 GB free  │ ❌ OOM kills
  ctx=40960  │██████████████████ ~16.0 GB total │ OOM           │ 💀 instant death
             └────────────────┘
              0 GB        16 GB

  Lesson: 16K is the sweet spot — enough for complex reasoning,
  leaves ~4.9 GB for OS/services. 24K worked initially but failed
  under sustained load (8+ hours of continuous inference).
```

> **Flash attention** (`OLLAMA_FLASH_ATTENTION=1`) reduces KV cache memory ~30% but on Vulkan/GFX1013 the savings aren't enough to safely run 24K. The 16K→24K experiment was the costliest failure: 8 hours of silent 500 errors before discovery.

### 4.2 Memory budget

**qwen3-14b-16k loaded · headless server**

| Component | Memory | Notes |
|-----------|--------|-------|
| OS + system | ~0.9 GB | Headless Fedora 43 |
| Ollama + model (GPU) | ~11.1 GB | GTT allocation |
| signal-cli | ~0.1 GB | JSON-RPC daemon |
| queue-runner | ~0.05 GB | Python process |
| **Free RAM** | **~1.5–3.8 GB** | Fluctuates with inference |
| NVMe swap | 16 GB (1.4 used) | Safety net |
| zram | 2 GB | Boot limit |
| GTT (VRAM) | 14 GB | See §3.2 |
| **Status** | **Stable ✓** | OOM protection: Ollama=-1000 |

> **Why NVMe swap matters:** During inference peaks, the kernel pages out inactive memory (signal-cli heap, queue-runner) to swap. With 16 GB NVMe swap at ~500 MB/s, this is transparent. Without it, the OOM killer terminates services. Removing OpenClaw freed ~700 MB of RAM — see [Appendix A](#appendix-a--openclaw-archive).

### 4.3 Abliterated models

"Abliterated" models have refusal mechanisms removed — identical intelligence, zero quality loss, no safety refusals. The abliterated 14B is the primary model for all tasks.

```bash
ollama pull huihui_ai/qwen3-abliterated:14b

# Create context-capped variant (required for 16 GB systems — see §3.4)
cat > /tmp/Modelfile.16k << 'EOF'
FROM huihui_ai/qwen3-abliterated:14b
PARAMETER num_ctx 16384
EOF
ollama create qwen3-14b-16k -f /tmp/Modelfile.16k
```

---

# `PART II` — AI Stack

## 5. Signal Chat Bot

The BC-250 runs a personal AI assistant accessible via Signal messenger — no gateway, no middleware. signal-cli runs as a standalone systemd service exposing a JSON-RPC API, and queue-runner handles all LLM interaction directly.

```
  📱 Signal ──→ signal-cli (JSON-RPC :8080) ──→ queue-runner ──→ Ollama ──→ GPU (Vulkan)
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
  ┌────────────────────────────────────────────────────────────────┐
  │   queue-runner v7 — continuous loop                            │
  │                                                                │
  │   ┌──────────┐  ┌──────────────┐  ┌──────────┐  ┌──────────┐  │
  │   │  job N   │→ │ check Signal │→ │  chat    │→ │ job N+1  │  │
  │   └──────────┘  │  inbox       │  │ (if msg) │  └──────────┘  │
  │                 └──────┬───────┘  └────┬─────┘               │
  │                        │               │                      │
  │                        ▼               ▼                      │
  │                 journalctl -u   Ollama /api/chat              │
  │                 signal-cli      (16K ctx, /think)             │
  │                        │               │                      │
  │                        │          ┌────┴─────┐                │
  │                        │          │ EXEC cmd │  ← tool use    │
  │                        │          └────┬─────┘                │
  │                        │               │                      │
  │                        ▼               ▼                      │
  │                 signal-cli       signal-cli                   │
  │                 JSON-RPC :8080   send reply                   │
  └────────────────────────────────────────────────────────────────┘
```

**Key parameters:**

| Setting | Value | Purpose |
|---------|:-----:|---------|
| `SIGNAL_CHAT_CTX` | 16384 | Full 16K context window for reasoning |
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
2. Run sd-cli with FLUX.1-schnell (4 steps, 512×512, ~48s)
3. Send image as Signal attachment
4. Restart Ollama

Bot is offline during generation (~60s total including model reload).

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
| Image generation (FLUX) | ~60s |
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

**FLUX.1-schnell** — recommended, 12B flow-matching, Apache 2.0:

```bash
mkdir -p /opt/stable-diffusion.cpp/models/flux && cd /opt/stable-diffusion.cpp/models/flux
curl -L -O "https://huggingface.co/second-state/FLUX.1-schnell-GGUF/resolve/main/flux1-schnell-q4_k.gguf"
curl -L -O "https://huggingface.co/second-state/FLUX.1-schnell-GGUF/resolve/main/ae.safetensors"
curl -L -O "https://huggingface.co/second-state/FLUX.1-schnell-GGUF/resolve/main/clip_l.safetensors"
curl -L -O "https://huggingface.co/city96/t5-v1_1-xxl-encoder-gguf/resolve/main/t5-v1_1-xxl-encoder-Q4_K_M.gguf"
```

> Memory: 6.5 GB VRAM (diffusion) + 2.9 GB RAM (T5-XXL Q4_K_M) = ~10 GB total.

**SD-Turbo** — fallback, faster but lower quality:

```bash
cd /opt/stable-diffusion.cpp/models
curl -L -o sd-turbo.safetensors \
  "https://huggingface.co/stabilityai/sd-turbo/resolve/main/sd_turbo.safetensors"
```

### 6.2 Performance

| Model | Res | Steps | Time | Quality |
|-------|:---:|:-----:|:----:|:-------:|
| **FLUX.1-schnell Q4_K** | 512² | 4 | **~48s** | ★★★★★ |
| SD-Turbo | 512² | 1 | **~3s** | ★★☆☆☆ |

### 6.3 Signal integration — synchronous pipeline

SD and Ollama can't run simultaneously (shared 16 GB VRAM). queue-runner handles this synchronously — no worker scripts, no delays:

```
  "draw a cyberpunk cat"
  ╰─→ queue-runner intercepts EXEC(generate-and-send "...")
       ╰─→ stop Ollama → run sd-cli → send image via Signal → restart Ollama
            ╰─→ 📱 image arrives (~60s total)
```

The pipeline is triggered when the LLM emits an `EXEC()` call matching the SD script path. queue-runner stops Ollama first (freeing ~11 GB VRAM), generates the image, sends it as a Signal attachment, then restarts Ollama. Total downtime ~60s.

> ⚠️ **GFX1013 bug:** sd-cli hangs after writing the output image (Vulkan cleanup). queue-runner polls for the file, then kills the process.

---

# `PART III` — Monitoring & Intelligence

## 7. Netscan Ecosystem

A comprehensive research, monitoring, and intelligence system with **336 autonomous jobs** running on a GPU-constrained single-board computer. Dashboard at `http://192.168.3.151:8888` — 29 main pages + 101 per-host detail pages.

### 7.1 Architecture — queue-runner v7

The BC-250 has 14 GB GTT shared with the CPU — only **one LLM job can run at a time**. `queue-runner.py` (systemd service) orchestrates all 336 jobs in a continuous loop, with Signal chat between every job:

```
  ┌─────────────────────────────────────────────────────────────────┐
  │  queue-runner v7 — Continuous Loop + Signal Chat                │
  │                                                                 │
  │  Cycle N:                                                       │
  │    336 jobs sequential, ordered by category:                    │
  │    scrape → infra → lore → academic → repo → company → career  │
  │           → think → meta → market → report                     │
  │    HA observations interleaved every 50 jobs                    │
  │    Signal inbox checked between EVERY job                       │
  │    Chat processed with LLM (EXEC tool use + image gen)          │
  │    Crash recovery: resumes from last completed job              │
  │                                                                 │
  │  Cycle N+1:                                                     │
  │    Immediately starts — no pause, no idle windows               │
  │    No nightly/daytime distinction                               │
  └─────────────────────────────────────────────────────────────────┘
```

**Key design decisions (v5 → v7):**

| v5 (OpenClaw era) | v7 (current) |
|--------------------|--------------|
| Nightly batch + daytime fill | Continuous loop, no distinction |
| 354 jobs (including duplicates) | 336 jobs (deduped, expanded) |
| LLM jobs routed through `openclaw cron run` | All jobs run as direct subprocesses |
| Signal via OpenClaw gateway (~700 MB) | signal-cli standalone (~100 MB) |
| Chat only when gateway available | Chat between every job |
| Async SD pipeline (worker scripts, 45s delay) | Synchronous SD (stop Ollama → generate → restart) |
| GPU idle detection for user chat preemption | No preemption needed — chat is interleaved |

**All jobs run as direct subprocesses** — `subprocess.Popen` for Python/bash scripts, no LLM agent routing. This is 3–10× faster than the old `openclaw cron run` path and eliminates the gateway dependency entirely.

### 7.1.1 Queue ordering

The queue prioritizes **data diversity** — all dashboard tabs get fresh data even if the cycle is interrupted:

```
 ┌── SCRAPE (data gathering, no LLM) ───────── career-scan, salary, patents, events, repos, lore
 │── INFRA (6 jobs, ~0.6h) ──────────────────── leak-monitor, netscan, watchdog
 │── LORE-ANALYSIS (12 jobs) ────────────────── lkml, soc, jetson, libcamera, dri, usb, riscv, dt
 │── ACADEMIC (17 jobs) ─────────────────────── publications, dissertations, patents
 │── REPO-THINK (22 jobs) ──────────────────── LLM analysis of repo changes
 │── OTHER (11 jobs) ────────────────────────── car-tracker, city-watch, csi-sensor
 │── COMPANY (46 jobs) ──────────────────────── company-think per entity
 │── CAREER (49 jobs) ──────────────────────── career-think per domain
 │── THINK (37 jobs, ~2.2h) ────────────────── research, trends, crawl, crossfeed
 │── META (5 jobs) ──────────────────────────── life-advisor, system-think
 │── MARKET (21 jobs) ──────────────────────── market-watch + sector analysis
 └── REPORT (1 job) ─────────────────────────── daily-summary → Signal
     ↕ HA observations interleaved every 50 jobs (ha-correlate, ha-journal)
     ↕ Signal chat checked between EVERY job
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

All 336 jobs are defined in `~/.openclaw/cron/jobs.json` and scheduled dynamically by `queue-runner.py` (systemd service, `WatchdogSec=14400`). There are **no fixed cron times** — jobs run sequentially as fast as the GPU allows, in a continuous loop.

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
| **Total** | **336** | **~11h** | |

**Data flow:**

```
  jobs.json (336 jobs)
       │
  queue-runner.py reads ──╯
    │
    ├─ All jobs → subprocess.Popen → python3/bash /opt/netscan/...
    │                                      │
    │      JSON results ←──────────────────╯
    │         │
    │         ├─→ /opt/netscan/data/{category}/*.json
    │         │
    │         └─→ generate-html.py → /opt/netscan/web/*.html → nginx :8888
    │
    ├─→ Signal chat (between every job)
    │    via JSON-RPC http://127.0.0.1:8080/api/v1/rpc
    │
    └─→ Signal alerts (career matches, leaks, events, daily summary)
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
| `~/.openclaw/cron/jobs.json` | All 336 job definitions *(legacy path, may be migrated)* |

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
   ╰─→ Phase 1: extract jobs (NO candidate profile) → raw job list
                                                           │
  Candidate Profile + single job ──────────────────────────╯
   ╰─→ Phase 2: score match → repeat per job
                                  ╰─→ aggregate → JSON + Signal alerts
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
│   ├── queue-runner.py             # v7 — continuous loop + Signal chat (336 jobs)
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
│   ├── car-tracker.py              # Car listing tracker
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
| Shared VRAM | Image gen requires stopping Ollama. Bot offline ~60s. |
| 14B memory pressure | ~1.5–3.8 GB free when loaded. NVMe swap essential. |
| Context cap at 16K | Hard limit to stay within 11.1 GB. Can't use 32K+ context. |
| Signal latency | Messages queue during job execution (typical job 2–15 min). Chat checked between every job. |
| sd-cli hangs on GFX1013 | Vulkan cleanup bug → poll + kill workaround. |
| Cold start latency | 30–60s after Ollama restart (model loading). |
| Chinese thinking leak | Qwen3 occasionally outputs Chinese reasoning. Cosmetic. |
| KV cache quantization | `q8_0`/`q4_0` no-op on Vulkan (CUDA/Metal only). |

### ☐ TODO

- [x] Fix OOM kills — custom 16K context model + NVMe swap + OOM score protection
- [x] Fix gateway orphan processes — KillMode=control-group
- [x] Scale from 38 → 56 → 58 → 354 → 309 → **336** jobs/cycle (deduped)
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
- [ ] Try FLUX at 768×768
- [ ] Weekly career summary digest via Signal
- [ ] Migrate jobs.json away from ~/.openclaw/ path
- [ ] Evaluate if zram can be fully disabled (currently 2 GB boot limit)

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
- All 336 jobs run as `subprocess.Popen` — no agent routing
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

`bc250` · AMD Cyan Skillfish · 336 autonomous jobs · *hack the planet* 🦞

</div>
