```
 ██████╗  ██████╗       ██████╗ ███████╗ ██████╗
 ██╔══██╗██╔════╝       ╚════██╗██╔════╝██╔═████╗
 ██████╔╝██║      █████╗ █████╔╝███████╗██║██╔██║
 ██╔══██╗██║      ╚════╝██╔═══╝ ╚════██║████╔╝██║
 ██████╔╝╚██████╗       ███████╗███████║╚██████╔╝
 ╚═════╝  ╚═════╝       ╚══════╝╚══════╝ ╚═════╝
```

<div align="center">

**GPU-accelerated AI home server on repurposed crypto-mining hardware**

`Zen 2 · RDNA 1 · 16 GB unified · Vulkan · 14B @ 27 tok/s · 38 autonomous tasks/day`

</div>

> A complete guide to running a personal AI stack on the AMD BC-250 — an obscure APU (Zen 2 + RDNA 1) from Samsung mining appliances. Covers Vulkan-based LLM inference, a Signal chat bot, image generation, and an autonomous monitoring ecosystem.
>
> **February 2026** · Hardware-specific driver workarounds, memory tuning discoveries, and real-world benchmarks that aren't documented anywhere else.

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
| [5](#5-openclaw-signal-bot) | OpenClaw Signal Bot | Bot builders | Model config, Signal channel, tools, skills |
| [6](#6-image-generation) | Image Generation | Creative users | FLUX.1-schnell + SD-Turbo, async pipeline |
| | **`PART III ─ MONITORING & INTEL`** | | |
| [7](#7-netscan-ecosystem) | Netscan Ecosystem | Home lab admins | 20+ scripts, dashboard, cron schedule |
| [8](#8-career-intelligence) | Career Intelligence | Job seekers | Two-phase scanner, salary, patents |
| | **`PART IV ─ REFERENCE`** | | |
| [9](#9-repository-structure) | Repository Structure | Contributors | File layout, deployment paths |
| [10](#10-troubleshooting) | Troubleshooting | Everyone | Common issues and fixes |
| [11](#11-known-limitations--todo) | Known Limitations & TODO | Maintainers | What's broken, what's planned |

---

# `PART I` — Hardware & Setup

## 1. Hardware Overview

The AMD BC-250 is a custom APU originally designed for Samsung crypto-mining appliances, repurposed as a hobbyist compute board.

| Component | Details |
|-----------|---------|
| **CPU** | Zen 2 — 6c/12t @ 2.0 GHz |
| **GPU** | Cyan Skillfish — RDNA 1, `GFX1013`, 24 CUs (1536 SPs) |
| **Memory** | **16 GB unified** (16 × 1 GB on-package), shared CPU/GPU |
| **VRAM** | 512 MB dedicated framebuffer |
| **GTT** | **12 GiB** (tuned, default 7.4 GiB) — `amdgpu.gttsize=12288` |
| **Vulkan total** | **12.5 GiB** after tuning |
| **Storage** | 475 GB NVMe |
| **OS** | Fedora 43, kernel 6.18.9, headless |
| **TDP** | 220W board (idle: 35–45W) |
| **IP** | `192.168.3.151` |

### Unified memory is your friend (but needs tuning)

CPU and GPU share the same 16 GB pool. Only 512 MB is carved out as VRAM — the rest is accessible as **GTT (Graphics Translation Table)**.

**Two bottlenecks must be fixed:**

1. **GTT cap** — `amdgpu` driver defaults to 50% of RAM (~7.4 GiB). Fix: `amdgpu.gttsize=12288` in kernel cmdline → GPU gets 12 GiB GTT.
2. **TTM pages_limit** — kernel TTM memory manager independently caps allocations at ~7.4 GiB. Fix: `ttm.pages_limit=3145728` (12 GiB in 4K pages).

After both fixes: Vulkan sees **12.5 GiB** — enough for **14B parameter models at 100% GPU**.

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
cat /sys/class/drm/card1/device/mem_info_gtt_total    # → 12884901888 (12 GiB)
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
Environment=OLLAMA_LOAD_TIMEOUT=15m
EOF
sudo systemctl daemon-reload && sudo systemctl restart ollama
```

> ROCm will crash during startup — expected and harmless. Ollama catches it and uses Vulkan.

### 3.2 Tune GTT size

```bash
sudo grubby --update-kernel=ALL --args="amdgpu.gttsize=12288"
# Reboot required. Verify:
cat /sys/class/drm/card1/device/mem_info_gtt_total  # → 12884901888 (12 GiB)
```

### 3.3 Tune TTM pages_limit ← *unlocks 14B models*

This was the breakthrough. Without this fix, 14B models load fine but produce HTTP 500 during inference.

```bash
# Runtime (immediate)
echo 3145728 | sudo tee /sys/module/ttm/parameters/pages_limit
echo 3145728 | sudo tee /sys/module/ttm/parameters/page_pool_size

# Persistent
echo "options ttm pages_limit=3145728 page_pool_size=3145728" | \
  sudo tee /etc/modprobe.d/ttm-gpu-memory.conf
printf "w /sys/module/ttm/parameters/pages_limit - - - - 3145728\n\
w /sys/module/ttm/parameters/page_pool_size - - - - 3145728\n" | \
  sudo tee /etc/tmpfiles.d/gpu-ttm-memory.conf
sudo dracut -f
```

### 3.4 Verify

```bash
sudo journalctl -u ollama -n 20 | grep total
# → total="12.5 GiB" available="12.5 GiB"
```

### 3.5 Disable GUI (saves ~1 GB)

```bash
sudo systemctl set-default multi-user.target && sudo reboot
```

### Memory layout after tuning

```
┌──────────────────────────────────────────────────┐
│              16 GB Unified Memory                │
├──────────────────────────────────────────────────┤
│  VRAM carveout ········· 512 MB                  │
│  GTT ··················· 12 GiB  (tuned ▲)       │
│  TTM pages_limit ······· 12 GiB  (tuned ▲)       │
├──────────────────────────────────────────────────┤
│  Vulkan device-local ··· 8.33 GiB                │
│  Vulkan host-visible ··· 4.17 GiB                │
│  Total ················· 12.5 GiB                │
│  → 14B models fit ····· 100% GPU, zero-copy      │
└──────────────────────────────────────────────────┘
```

---

## 4. Models & Benchmarks

### 4.1 Compatibility table

> Ollama 0.16.1 · Vulkan · RADV Mesa 25.3.4

| Model | VRAM | GPU | tok/s | Notes |
|-------|:----:|:---:|:-----:|-------|
| qwen2.5:3b | 2.4 GB | 100% | **101** | Fast, lightweight |
| qwen2.5:7b | 4.9 GB | 100% | **59** | Great quality/speed |
| llama3.1:8b | 5.5 GB | 100% | **75** | Fastest 8B |
| qwen3:8b | 5.9 GB | 100% | **44** | Thinking mode |
| **qwen3-abliterated:14b** | **11 GB** | **100%** | **27.7** | **← primary** |
| qwen3:14b | 12 GB | 100% | **27** | Largest that fits |
| mistral-nemo:12b | 10 GB | 100% | **34** | Good 12B alt |
| gemma2:9b | 8.1 GB | 91% | 26 | Spills to CPU |

> ⚠️ 14B models require both GTT (§3.2) and TTM (§3.3) tuning.

### 4.2 Memory budget

```
┌─────────────────────────────────────┐
│  14B model loaded · headless server │
├─────────────────────────────────────┤
│  OS + services ····· ~0.8 GB        │
│  Ollama process ···· ~0.5 GB        │
│  Model (GPU) ······· ~11 GB         │
│  Free RAM ·········· ~1.8–3 GB      │
│  Swap ·············· 8 GB (unused)  │
│  Status ············ tight ✓ stable │
└─────────────────────────────────────┘
```

### 4.3 Abliterated models

"Abliterated" models have refusal mechanisms removed — identical intelligence, zero quality loss, no safety refusals. The abliterated 14B is the primary model for all tasks.

```bash
ollama pull huihui_ai/qwen3-abliterated:14b
```

---

# `PART II` — AI Stack

## 5. OpenClaw Signal Bot

OpenClaw turns the BC-250 into a personal AI assistant accessible via Signal messenger.

```
  📱 Signal ──→ signal-cli ──→ OpenClaw Gateway ──→ Ollama ──→ GPU (Vulkan)
```

> **Software:** OpenClaw v2026.2.17 · Node.js 22 · signal-cli v0.13.24 (native) · Ollama 0.16.1

### 5.1 Installation

```bash
sudo dnf install -y nodejs npm
sudo npm install -g openclaw@latest

openclaw onboard \
  --non-interactive --accept-risk --auth-choice skip \
  --install-daemon --skip-channels --skip-skills --skip-ui --skip-health \
  --daemon-runtime node --gateway-bind loopback
```

<details>
<summary>▸ Install signal-cli</summary>

```bash
VERSION=$(curl -Ls -o /dev/null -w %{url_effective} \
  https://github.com/AsamK/signal-cli/releases/latest | sed -e 's/^.*\/v//')
curl -L -O "https://github.com/AsamK/signal-cli/releases/download/v${VERSION}/signal-cli-${VERSION}-Linux-native.tar.gz"
sudo tar xf "signal-cli-${VERSION}-Linux-native.tar.gz" -C /opt
sudo ln -sf /opt/signal-cli /usr/local/bin/signal-cli
```

</details>

### 5.2 Model configuration

`~/.openclaw/openclaw.json`:

```json
{
  "models": {
    "providers": {
      "ollama": {
        "baseUrl": "http://127.0.0.1:11434",
        "apiKey": "ollama-local",
        "api": "ollama",
        "models": [
          {
            "id": "huihui_ai/qwen3-abliterated:14b",
            "name": "Qwen 3 14B Abliterated",
            "contextWindow": 24576,
            "maxTokens": 12288,
            "reasoning": true
          }
        ]
      }
    }
  },
  "agents": {
    "defaults": {
      "model": { "primary": "ollama/huihui_ai/qwen3-abliterated:14b" },
      "thinkingDefault": "high",
      "timeoutSeconds": 1800
    }
  }
}
```

**Key settings:**
- `reasoning: true` — enables native thinking support
- `thinkingDefault: "high"` — deep reasoning on interactive messages
- `contextWindow: 24576` — at 24k, KV cache is ~3.8 GB. Beyond 32k wastes VRAM.
- `timeoutSeconds: 1800` — generous timeout for complex agent turns
- Single model, no fallbacks. `MAX_LOADED_MODELS=1` keeps it hot.

### 5.3 Tool optimization

Cut system prompt from ~11k to ~4k tokens:

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

> **Important:** Use `alsoAllow` (additive), not `allow` (restrictive whitelist).

### 5.4 Agent identity

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

Personality lives in workspace markdown files (`~/.openclaw/workspace/`):

| File | What | Size |
|------|------|:----:|
| `WORKFLOW_AUTO.md` | Cron bypass rules, session start grounding | 730 B |
| `SOUL.md` | Core personality — direct, no corporate speak | 1.0 KB |
| `IDENTITY.md` | Name, creature type, emoji | 550 B |
| `USER.md` | Human info — timezone, preferences | 1.7 KB |
| `TOOLS.md` | Explicit tool commands (image, web, diagnostics) | 2.1 KB |
| `AGENTS.md` | Grounding — "only report facts you can verify" | 1.4 KB |

> **Context budget:** All root `.md` files are injected into the system prompt. Total ~7.5 KB. Larger reference docs live in `docs/` subdirectory to avoid bloating cron context.

### 5.5 Signal channel setup

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

Register a **separate** phone number for the bot, then pair:

```bash
systemctl --user restart openclaw-gateway
# Send any message from your phone → bot replies with pairing code
openclaw pairing approve signal <CODE>
```

### 5.6 Service management

```bash
systemctl --user status openclaw-gateway   # status
openclaw logs --follow                     # live logs
openclaw doctor                            # diagnostics
openclaw channels status --probe           # signal health
```

### 5.7 Response times

| Scenario | Latency |
|----------|:-------:|
| Cold start (first msg) | 60–90s |
| Warm, simple query | 10–30s |
| Warm, complex reasoning | 30–90s |
| Image generation (FLUX) | ~48s |

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

### 6.3 Signal integration — async pipeline

SD and Ollama can't run simultaneously (shared VRAM). The bot uses an async two-script architecture:

```
  "draw a cyberpunk cat"
  ╰─→ exec wrapper (returns instantly) → "Generating..."
       ╰─→ worker: wait 45s → stop Ollama → run SD → send image → restart Ollama
            ╰─→ 📱 image arrives (~100s total)
```

The 45s delay ensures Ollama finishes its response before the worker takes the GPU. Bot is offline during generation (~50s).

> ⚠️ **GFX1013 bug:** sd-cli hangs after writing the output image (Vulkan cleanup). Worker polls for the file, then kills the process.

---

# `PART III` — Monitoring & Intelligence

## 7. Netscan Ecosystem

A comprehensive research, monitoring, and intelligence system. Dashboard at `http://192.168.3.151:8888`.

### 7.1 Architecture

```
  ┌─────────────────────────────────────────────────────────────┐
  │  openclaw cron (38 jobs/day)                                │
  │    → Clawd agent turns → shell tools → scripts → Ollama    │
  │    → JSON data → generate-html.py → Dashboard (nginx)      │
  │    → Signal alerts (9 jobs: leaks, career, salary, ...)    │
  ├─────────────────────────────────────────────────────────────┤
  │  Signal (phone) → gateway (24/7) → agent turn              │
  │    → queued if cron job running                             │
  └─────────────────────────────────────────────────────────────┘
```

The system runs **autonomously** — 38 GPU tasks/day, all routed through Clawd. The gateway runs 24/7. Signal messages queue until the current task completes.

### 7.2 Scripts

| Script | Purpose | GPU |
|--------|---------|:---:|
| `career-scan.py` | Two-phase career scanner (§8) | ● |
| `salary-tracker.py` | Salary intelligence — NoFluffJobs, career-scan extraction | ● |
| `company-intel.py` | Deep company intel — GoWork, DDG news, layoffs (13 entities) | ● |
| `patent-watch.py` | IR/RGB camera patent monitor — Google Patents, Lens.org | ● |
| `event-scout.py` | Meetup/conference tracker — Łódź, Warsaw, Poland, Europe | ● |
| `leak-monitor.py` | CTI: ransomware, HIBP, Hudson Rock, GitHub, Telegram, CISA KEV | ● |
| `idle-think.sh` | Research brain — 8 task types → JSON notes | ● |
| `ha-journal.py` | Home Assistant analysis (climate, sensors, anomalies) | ● |
| `ha-correlate.py` | HA cross-sensor correlation | ● |
| `lore-digest.sh` | Kernel mailing list digests | ● |
| `repo-watch.sh` | Upstream repos (GStreamer, libcamera, v4l-utils, FFmpeg, LinuxTV) | ○ |
| `scan.sh` / `enumerate.sh` | Network scan + enumeration | ○ |
| `vulnscan.sh` | Weekly vulnerability scan | ○ |
| `presence.sh` | Phone presence tracker | ○ |
| `gpu-monitor.sh` / `.py` | Per-minute GPU utilization (3-state) | ○ |
| `syslog.sh` | System health logger | ○ |
| `watchdog.py` | Integrity checks — cron health, disk, services | ○ |
| `generate-html.py` | Dashboard builder | ○ |

`●` GPU (openclaw cron) · `○` CPU-only (system cron)

### 7.3 Cron schedule — 38 GPU jobs

All GPU tasks use `[cron]` directive prefix (no startup rituals), `thinking: off` (scripts handle their own Ollama calls). Dashboard reads live config from `~/.openclaw/cron/jobs.json`.

<details>
<summary>▸ Night batch — 23:00–07:59 — 24 jobs, ~20 min spacing</summary>

| Time | Job | Timeout |
|:----:|-----|:-------:|
| 23:00 | leak-monitor-night | 30 min |
| 23:20 | think-trends-n1 | 60 min |
| 23:40 | think-research-n1 | 60 min |
| 00:00 | ha-journal-n1 | 30 min |
| 00:20 | career-scan | 120 min |
| 01:30 | salary-tracker | 30 min |
| 01:50 | company-intel | 30 min |
| 02:10 | patent-watch | 30 min |
| 02:30 | event-scout | 30 min |
| 02:50 | think-crossfeed-n1 | 60 min |
| 03:10 | think-career-n1 | 60 min |
| 03:30 | think-crawl-n1 | 60 min |
| 03:50 | think-learn | 60 min |
| 04:10 | think-weekly | 60 min |
| 04:30 | lore-digest | 60 min |
| 05:00 | think-research-n2 | 60 min |
| 05:20 | think-trends-n2 | 60 min |
| 05:40 | ha-correlate | 60 min |
| 06:00 | think-crossfeed-n2 | 60 min |
| 06:20 | think-research-n3 | 60 min |
| 06:40 | ha-journal-n2 | 30 min |
| 07:00 | think-crawl-n2 | 60 min |
| 07:20 | think-research-n4 | 60 min |
| 07:40 | leak-monitor-morning | 30 min |

</details>

<details>
<summary>▸ Daytime — 08:00–22:59 — 14 jobs, hourly</summary>

| Time | Job | Timeout |
|:----:|-----|:-------:|
| 09:00 | ha-journal-d1 | 30 min |
| 10:00 | think-research-d1 | 60 min |
| 11:00 | leak-monitor-midday | 30 min |
| 12:00 | ha-journal-d2 | 30 min |
| 13:00 | think-trends-d1 | 60 min |
| 14:00 | think-crossfeed-d1 | 60 min |
| 15:00 | ha-journal-d3 | 30 min |
| 16:00 | think-crawl-d1 | 60 min |
| 17:00 | think-career-d1 | 60 min |
| 18:00 | ha-journal-d4 | 30 min |
| 19:00 | think-signal 📱 | 60 min |
| 20:00 | think-research-d2 | 60 min |
| 21:00 | ha-journal-d5 | 30 min |
| 22:00 | think-research-d3 | 60 min |

</details>

**Signal delivery:** 9 jobs announce to Signal (best-effort) — leak-monitor ×3, career-scan, salary-tracker, company-intel, patent-watch, event-scout, lore-digest. The other 29 write silently to files.

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

### 7.5 Data locations

All paths relative to `/opt/netscan/`:

| Data | Path |
|------|------|
| Research notes | `data/think/note-*.json` + `notes-index.json` |
| Career scans | `data/career/scan-*.json` + `latest-scan.json` |
| Salary | `data/salary/salary-*.json` (180-day history) |
| Company intel | `data/intel/intel-*.json` + `company-intel-deep.json` |
| Patents | `data/patents/patents-*.json` + `patent-db.json` |
| Events | `data/events/events-*.json` + `event-db.json` |
| Leaks / CTI | `data/leaks/leak-intel.json` |
| Correlations | `data/correlate/correlate-*.json` |
| GPU load | `data/gpu-load.tsv` |
| System health | `data/syslog/health-*.tsv` (30-day retention) |
| Network hosts | `data/hosts-db.json` |
| Presence | `data/presence-state.json` |

### 7.6 Dashboard pages

Served by nginx at `:8888`:

| Page | Content |
|------|---------|
| `index.html` | Overview — host count, presence, latest notes |
| `hosts.html` | Network device inventory |
| `presence.html` | Phone detection timeline |
| `security.html` | Host security scoring |
| `career.html` | Career scan results |
| `leaks.html` | CTI / leak monitor |
| `notes.html` | Research notes |
| `load.html` | GPU utilization heatmap + dynamic cron schedule |
| `issues.html` | Repo issue tracking |
| `lkml.html` | Mailing list / repo digests |
| `history.html` | Changelog |
| `log.html` | Raw scan logs |

### 7.7 GPU monitoring — 3-state

Per-minute sampling via `pp_dpm_sclk`:

| State | Clock | Temp | Meaning |
|-------|:-----:|:----:|---------|
| `generating` | 2000 MHz | ~77°C | Active LLM inference |
| `loaded` | 1000 MHz | ~56°C | Model in VRAM, idle |
| `idle` | 1000 MHz | <50°C | No model loaded |

### 7.8 Configuration files

| File | Purpose |
|------|---------|
| `profile.json` | Public interests — tracked repos, keywords, technologies |
| `profile-private.json` | Career context — target companies, salary expectations *(gitignored)* |
| `watchlist.json` | Auto-evolving interest tracker |
| `digest-feeds.json` | Mailing list feed URLs |
| `repo-feeds.json` | Repository API endpoints |

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
│   ├── career-scan.py              # Two-phase career scanner
│   ├── salary-tracker.py           # Salary intelligence
│   ├── company-intel.py            # Company deep-dive
│   ├── patent-watch.py             # Patent monitor
│   ├── event-scout.py              # Event tracker
│   ├── leak-monitor.py             # CTI: 8 breach/leak sources
│   ├── ha-journal.py               # Home Assistant journal
│   ├── ha-correlate.py             # HA cross-sensor correlation
│   ├── ha-observe.py               # Quick HA queries
│   ├── generate-html.py            # Dashboard builder
│   ├── gpu-monitor.py              # GPU data collector
│   ├── idle-think.sh               # Research brain (8 task types)
│   ├── repo-watch.sh               # Upstream repo monitor
│   ├── lore-digest.sh              # Mailing list digests
│   ├── gpu-monitor.sh              # Per-minute GPU sampler
│   ├── scan.sh / enumerate.sh      # Network scanning
│   ├── vulnscan.sh                 # Weekly vulnerability scan
│   ├── presence.sh                 # Phone presence detection
│   ├── syslog.sh                   # System health logger
│   ├── watchdog.py                 # Integrity checker
│   ├── report.sh                   # Morning report rebuild
│   ├── profile.json                # Public interests
│   ├── profile-private.json        # Career context (gitignored)
│   ├── watchlist.json              # Auto-evolving interest tracker
│   ├── digest-feeds.json           # Feed URLs
│   └── repo-feeds.json             # Repository endpoints
├── openclaw/
│   ├── workspace/
│   │   ├── WORKFLOW_AUTO.md        # Slim cron-aware behavior rules
│   │   ├── AGENTS.md               # Grounding rules
│   │   ├── SOUL.md                 # Personality
│   │   ├── IDENTITY.md             # Name/emoji
│   │   └── docs/                   # Reference docs (not in system prompt)
│   │       ├── ECOSYSTEM.md
│   │       ├── HEARTBEAT.md
│   │       └── WORKFLOW_AUTO_FULL.md
│   ├── TOOLS.md                    → ~/.openclaw/workspace/TOOLS.md
│   └── skills/
│       ├── sd-image/SKILL.md
│       └── web-search/SKILL.md
├── openclaw.json                   → ~/.openclaw/openclaw.json
├── systemd/
│   ├── ollama.service
│   └── ollama.service.d/
│       └── override.conf           # Vulkan + memory settings
├── generate-and-send.sh            → /opt/stable-diffusion.cpp/
├── generate-and-send-worker.sh     → /opt/stable-diffusion.cpp/
└── ollama-proxy.py                 # DEPRECATED
```

</details>

### Deployment

| Local | → bc250 |
|-------|---------|
| `netscan/*` | `/opt/netscan/` |
| `openclaw.json` | `~/.openclaw/openclaw.json` |
| `openclaw/workspace/*` | `~/.openclaw/workspace/` |
| `openclaw/TOOLS.md` | `~/.openclaw/workspace/TOOLS.md` |
| `openclaw/skills/*` | `~/.openclaw/workspace/skills/` |
| `systemd/ollama.*` | `/etc/systemd/system/ollama.*` |
| `generate-and-send*.sh` | `/opt/stable-diffusion.cpp/` |

---

## 10. Troubleshooting

<details>
<summary><b>▸ ROCm crashes in Ollama logs</b></summary>

Expected — Ollama tries ROCm, it crashes on GFX1013, falls back to Vulkan. No action needed.

</details>

<details>
<summary><b>▸ Only 7.9 GiB GPU memory instead of 12.5 GiB</b></summary>

GTT tuning not applied. Check: `cat /proc/cmdline | grep gttsize`

</details>

<details>
<summary><b>▸ 14B model loads but inference returns HTTP 500</b></summary>

TTM pages_limit bottleneck. Fix: `echo 3145728 | sudo tee /sys/module/ttm/parameters/pages_limit` (see §3.3).

</details>

<details>
<summary><b>▸ Model loads on CPU instead of GPU</b></summary>

Check `OLLAMA_VULKAN=1`: `sudo systemctl show ollama | grep Environment`

</details>

<details>
<summary><b>▸ Context window OOM kills</b></summary>

Don't use 128k context on 16 GB. Cap at 24576 via `OLLAMA_CONTEXT_LENGTH` in Ollama's systemd override.

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
| Shared VRAM | Image gen requires stopping Ollama. Bot offline ~50s. |
| 14B memory pressure | ~2 GB free when loaded. Tight but stable. |
| Signal preemption | Messages queue during cron (2–5 min typical, 120 min max at 00:20). |
| sd-cli hangs on GFX1013 | Vulkan cleanup bug → background kill workaround. |
| Cold start latency | 30–60s after Ollama restart (model loading). |
| Chinese thinking leak | Qwen3 occasionally outputs Chinese reasoning. Cosmetic. |
| KV cache quantization | `q8_0`/`q4_0` no-op on Vulkan (CUDA/Metal only). |
| Night GPU utilization | ~22–33% — room for more tasks. |

### ☐ TODO

- [ ] Increase night GPU utilization beyond 30%
- [ ] Try FLUX at 768×768
- [ ] Weekly career summary digest via Signal
- [ ] Reduce OpenClaw system prompt overhead (~9.6K chars)

---

<div align="center">

`bc250` · AMD Cyan Skillfish · *hack the planet* 🦞

</div>
