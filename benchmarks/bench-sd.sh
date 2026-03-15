#!/bin/bash
# BC-250 Image Generation Benchmark Suite — March 2026
# Simpler version: run each test, poll for output file, kill hung process

SD_CLI="/opt/stable-diffusion.cpp/build/bin/sd-cli"
M="/opt/stable-diffusion.cpp/models"
OUT="/opt/netscan/tmp/bench-sd-images"
LOG="/opt/netscan/tmp/bench-sd-results.txt"

mkdir -p "$OUT"

echo "================================================================" > "$LOG"
echo "BC-250 Image Gen Benchmark — $(date '+%Y-%m-%d %H:%M')" >> "$LOG"
echo "sd.cpp master-525 · Vulkan · GFX1013 · --offload-to-cpu --fa" >> "$LOG"
echo "================================================================" >> "$LOG"

sudo systemctl stop ollama 2>/dev/null
sleep 3

PROMPT="A photorealistic cyberpunk cityscape at night with neon lights reflecting on wet streets, detailed architecture, volumetric lighting"

run_test() {
    local label="$1"; shift
    local outfile="$OUT/${label}.png"
    rm -f "$outfile"

    printf "%-45s " "$label" | tee -a "$LOG"

    local t0=$SECONDS
    timeout 600 $SD_CLI "$@" --offload-to-cpu --fa -p "$PROMPT" --seed 42 -o "$outfile" >/dev/null 2>&1 &
    local pid=$!

    # Poll for file (GFX1013 hangs after write)
    local i=0
    while [ $i -lt 120 ]; do
        sleep 5
        i=$((i+1))
        if [ -f "$outfile" ]; then
            local sz
            sz=$(stat -c%s "$outfile" 2>/dev/null || echo 0)
            if [ "$sz" -gt 1000 ]; then
                sleep 1
                local elapsed=$((SECONDS - t0))
                local fsize=$(du -h "$outfile" | cut -f1)
                kill "$pid" 2>/dev/null
                wait "$pid" 2>/dev/null
                pkill -f sd-cli 2>/dev/null
                sleep 2
                echo "OK  ${elapsed}s  ${fsize}" | tee -a "$LOG"
                return 0
            fi
        fi
        # Check if process exited without file
        if ! kill -0 "$pid" 2>/dev/null; then
            if [ -f "$outfile" ]; then
                local elapsed=$((SECONDS - t0))
                local fsize=$(du -h "$outfile" | cut -f1)
                echo "OK  ${elapsed}s  ${fsize}" | tee -a "$LOG"
                return 0
            else
                echo "FAIL" | tee -a "$LOG"
                return 1
            fi
        fi
    done
    kill "$pid" 2>/dev/null
    wait "$pid" 2>/dev/null
    pkill -f sd-cli 2>/dev/null
    echo "TIMEOUT" | tee -a "$LOG"
    return 1
}

echo "" | tee -a "$LOG"
echo "--- FLUX.1-schnell Q4_K (production) ---" | tee -a "$LOG"

run_test "schnell_512x512_s4" \
  --diffusion-model "$M/flux/flux1-schnell-q4_k.gguf" \
  --vae "$M/flux/ae.safetensors" --clip_l "$M/flux/clip_l.safetensors" \
  --t5xxl "$M/flux/t5-v1_1-xxl-encoder-Q4_K_M.gguf" \
  --cfg-scale 1.0 --sampling-method euler --clip-on-cpu \
  -W 512 -H 512 --steps 4

run_test "schnell_768x768_s4_tiling" \
  --diffusion-model "$M/flux/flux1-schnell-q4_k.gguf" \
  --vae "$M/flux/ae.safetensors" --clip_l "$M/flux/clip_l.safetensors" \
  --t5xxl "$M/flux/t5-v1_1-xxl-encoder-Q4_K_M.gguf" \
  --cfg-scale 1.0 --sampling-method euler --clip-on-cpu --vae-tiling \
  -W 768 -H 768 --steps 4

run_test "schnell_1024x1024_s4_tiling" \
  --diffusion-model "$M/flux/flux1-schnell-q4_k.gguf" \
  --vae "$M/flux/ae.safetensors" --clip_l "$M/flux/clip_l.safetensors" \
  --t5xxl "$M/flux/t5-v1_1-xxl-encoder-Q4_K_M.gguf" \
  --cfg-scale 1.0 --sampling-method euler --clip-on-cpu --vae-tiling \
  -W 1024 -H 1024 --steps 4

run_test "schnell_512x512_s8" \
  --diffusion-model "$M/flux/flux1-schnell-q4_k.gguf" \
  --vae "$M/flux/ae.safetensors" --clip_l "$M/flux/clip_l.safetensors" \
  --t5xxl "$M/flux/t5-v1_1-xxl-encoder-Q4_K_M.gguf" \
  --cfg-scale 1.0 --sampling-method euler --clip-on-cpu \
  -W 512 -H 512 --steps 8

run_test "schnell_768x512_s4" \
  --diffusion-model "$M/flux/flux1-schnell-q4_k.gguf" \
  --vae "$M/flux/ae.safetensors" --clip_l "$M/flux/clip_l.safetensors" \
  --t5xxl "$M/flux/t5-v1_1-xxl-encoder-Q4_K_M.gguf" \
  --cfg-scale 1.0 --sampling-method euler --clip-on-cpu \
  -W 768 -H 512 --steps 4

run_test "schnell_1024x576_s4_tiling" \
  --diffusion-model "$M/flux/flux1-schnell-q4_k.gguf" \
  --vae "$M/flux/ae.safetensors" --clip_l "$M/flux/clip_l.safetensors" \
  --t5xxl "$M/flux/t5-v1_1-xxl-encoder-Q4_K_M.gguf" \
  --cfg-scale 1.0 --sampling-method euler --clip-on-cpu --vae-tiling \
  -W 1024 -H 576 --steps 4

echo "" | tee -a "$LOG"
echo "--- SD-Turbo (fast baseline) ---" | tee -a "$LOG"

run_test "sdturbo_512x512_s1" \
  -m "$M/sd-turbo.safetensors" --clip-on-cpu \
  -W 512 -H 512 --steps 1

run_test "sdturbo_512x512_s4" \
  -m "$M/sd-turbo.safetensors" --clip-on-cpu \
  -W 512 -H 512 --steps 4

run_test "sdturbo_768x768_s4" \
  -m "$M/sd-turbo.safetensors" --clip-on-cpu \
  -W 768 -H 768 --steps 4

echo "" | tee -a "$LOG"
echo "--- FLUX.1-dev Q4_K (download if needed) ---" | tee -a "$LOG"

FLUX_DEV="$M/flux/flux1-dev-q4_k.gguf"
if [ ! -f "$FLUX_DEV" ]; then
    echo "Downloading flux1-dev-q4_k.gguf (6.5 GB)..." | tee -a "$LOG"
    curl -L -o "$FLUX_DEV" \
      "https://huggingface.co/second-state/FLUX.1-dev-GGUF/resolve/main/flux1-dev-q4_k.gguf"
fi

if [ -f "$FLUX_DEV" ]; then
    run_test "dev_512x512_s20" \
      --diffusion-model "$FLUX_DEV" \
      --vae "$M/flux/ae.safetensors" --clip_l "$M/flux/clip_l.safetensors" \
      --t5xxl "$M/flux/t5-v1_1-xxl-encoder-Q4_K_M.gguf" \
      --cfg-scale 1.0 --sampling-method euler --clip-on-cpu \
      -W 512 -H 512 --steps 20

    run_test "dev_768x768_s20_tiling" \
      --diffusion-model "$FLUX_DEV" \
      --vae "$M/flux/ae.safetensors" --clip_l "$M/flux/clip_l.safetensors" \
      --t5xxl "$M/flux/t5-v1_1-xxl-encoder-Q4_K_M.gguf" \
      --cfg-scale 1.0 --sampling-method euler --clip-on-cpu --vae-tiling \
      -W 768 -H 768 --steps 20

    run_test "dev_1024x1024_s20_tiling" \
      --diffusion-model "$FLUX_DEV" \
      --vae "$M/flux/ae.safetensors" --clip_l "$M/flux/clip_l.safetensors" \
      --t5xxl "$M/flux/t5-v1_1-xxl-encoder-Q4_K_M.gguf" \
      --cfg-scale 1.0 --sampling-method euler --clip-on-cpu --vae-tiling \
      -W 1024 -H 1024 --steps 20

    run_test "dev_512x512_s50" \
      --diffusion-model "$FLUX_DEV" \
      --vae "$M/flux/ae.safetensors" --clip_l "$M/flux/clip_l.safetensors" \
      --t5xxl "$M/flux/t5-v1_1-xxl-encoder-Q4_K_M.gguf" \
      --cfg-scale 1.0 --sampling-method euler --clip-on-cpu \
      -W 512 -H 512 --steps 50
fi

echo "" | tee -a "$LOG"
echo "=== ALL DONE ===" | tee -a "$LOG"

sudo systemctl start ollama
echo "Ollama restarted." | tee -a "$LOG"
echo "Results: $LOG"
ls -lh "$OUT/"
