#!/bin/bash
# Benchmark Chroma vs FLUX.1-schnell on BC-250

SDCLI="/opt/stable-diffusion.cpp/build/bin/sd-cli"
MODELS="/opt/stable-diffusion.cpp/models/flux"
RESULTS="/opt/netscan/tmp/bench-chroma-results.txt"
OUTDIR="/opt/netscan/tmp/bench-chroma-images"
PROMPT="a cyberpunk city at night with neon signs reflecting in rain puddles, highly detailed"

mkdir -p "$OUTDIR"
echo "Chroma vs FLUX.1-schnell Benchmark — $(date)" > "$RESULTS"
echo "==========================================" >> "$RESULTS"

run_sd() {
    local label="$1"
    shift
    local output="$OUTDIR/${label}.png"
    
    echo "" >> "$RESULTS"
    echo "TEST: $label" >> "$RESULTS"
    
    rm -f "$output"
    local start=$(date +%s)
    
    "$SDCLI" "$@" -o "$output" >> "$RESULTS" 2>&1 &
    local pid=$!
    
    local elapsed=0
    while [ $elapsed -lt 600 ]; do
        sleep 5
        elapsed=$(( $(date +%s) - start ))
        if [ -f "$output" ] && [ $(stat -c%s "$output" 2>/dev/null || echo 0) -gt 1000 ]; then
            sleep 2
            kill $pid 2>/dev/null
            wait $pid 2>/dev/null
            echo "TIME: ${elapsed}s" >> "$RESULTS"
            echo "  $label: ${elapsed}s"
            return 0
        fi
    done
    
    kill $pid 2>/dev/null
    wait $pid 2>/dev/null
    echo "TIME: TIMEOUT" >> "$RESULTS"
    echo "  $label: TIMEOUT"
    return 1
}

echo "--- Chroma flash tests ---"
echo "" >> "$RESULTS"
echo "=== CHROMA FLASH ===" >> "$RESULTS"

# Test 1: Chroma 512x512 8 steps
run_sd "chroma-512-s8" \
    --diffusion-model "$MODELS/chroma-unlocked-v47-flash-Q4_0.gguf" \
    --vae "$MODELS/ae.safetensors" \
    --t5xxl "$MODELS/t5-v1_1-xxl-encoder-Q4_K_M.gguf" \
    -p "$PROMPT" \
    --cfg-scale 1.0 --sampling-method heun --steps 8 \
    -W 512 -H 512 \
    --diffusion-fa --offload-to-cpu --vae-tiling \
    --chroma-disable-dit-mask --clip-on-cpu -v

# Test 2: Chroma 512x512 4 steps
run_sd "chroma-512-s4" \
    --diffusion-model "$MODELS/chroma-unlocked-v47-flash-Q4_0.gguf" \
    --vae "$MODELS/ae.safetensors" \
    --t5xxl "$MODELS/t5-v1_1-xxl-encoder-Q4_K_M.gguf" \
    -p "$PROMPT" \
    --cfg-scale 1.0 --sampling-method heun --steps 4 \
    -W 512 -H 512 \
    --diffusion-fa --offload-to-cpu --vae-tiling \
    --chroma-disable-dit-mask --clip-on-cpu -v

# Test 3: Chroma 768x768 8 steps 
run_sd "chroma-768-s8" \
    --diffusion-model "$MODELS/chroma-unlocked-v47-flash-Q4_0.gguf" \
    --vae "$MODELS/ae.safetensors" \
    --t5xxl "$MODELS/t5-v1_1-xxl-encoder-Q4_K_M.gguf" \
    -p "$PROMPT" \
    --cfg-scale 1.0 --sampling-method heun --steps 8 \
    -W 768 -H 768 \
    --diffusion-fa --offload-to-cpu --vae-tiling \
    --chroma-disable-dit-mask --clip-on-cpu -v

# Test 4: FLUX.1-schnell comparison
echo "" >> "$RESULTS"
echo "=== FLUX.1-SCHNELL ===" >> "$RESULTS"

run_sd "schnell-512-s4" \
    --diffusion-model "$MODELS/flux1-schnell-q4_k.gguf" \
    --vae "$MODELS/ae.safetensors" \
    --clip_l "$MODELS/clip_l.safetensors" \
    --t5xxl "$MODELS/t5-v1_1-xxl-encoder-Q4_K_M.gguf" \
    -p "$PROMPT" \
    --cfg-scale 1.0 --steps 4 \
    -W 512 -H 512 \
    --diffusion-fa --offload-to-cpu --vae-tiling --clip-on-cpu -v

echo "" >> "$RESULTS"
echo "DONE: $(date)" >> "$RESULTS"
echo ""
echo "All tests complete."
cat "$RESULTS" | grep -E "^TEST:|^TIME:|DONE:"
