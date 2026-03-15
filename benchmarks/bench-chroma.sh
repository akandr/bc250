#!/bin/bash
# Benchmark Chroma vs FLUX.1-schnell on BC-250
# Chroma reuses existing T5-XXL and VAE from FLUX.1

SDCLI="/opt/stable-diffusion.cpp/build/bin/sd-cli"
MODELS="/opt/stable-diffusion.cpp/models/flux"
RESULTS="/opt/netscan/tmp/bench-chroma-results.txt"
OUTDIR="/opt/netscan/tmp/bench-chroma-images"

mkdir -p "$OUTDIR"
echo "Chroma vs FLUX.1-schnell Benchmark — $(date)" > "$RESULTS"
echo "==========================================" >> "$RESULTS"

run_test() {
    local label="$1"
    local args="$2"
    local output="$OUTDIR/${label}.png"
    
    echo "" >> "$RESULTS"
    echo "TEST: $label" >> "$RESULTS"
    echo "CMD: $SDCLI $args" >> "$RESULTS"
    
    rm -f "$output"
    local start=$(date +%s)
    
    # Run in background, poll for output (GFX1013 hang workaround)
    $SDCLI $args -o "$output" 2>> "$RESULTS" &
    local pid=$!
    
    # Poll for output file (max 600s)
    local elapsed=0
    while [ $elapsed -lt 600 ]; do
        sleep 5
        elapsed=$(( $(date +%s) - start ))
        if [ -f "$output" ] && [ $(stat -c%s "$output" 2>/dev/null || echo 0) -gt 1000 ]; then
            sleep 2  # Let it finish writing
            kill $pid 2>/dev/null
            wait $pid 2>/dev/null
            echo "TIME: ${elapsed}s" >> "$RESULTS"
            echo "SIZE: $(ls -lh "$output" | awk '{print $5}')" >> "$RESULTS"
            echo "  $label: ${elapsed}s ✓"
            return 0
        fi
    done
    
    kill $pid 2>/dev/null
    wait $pid 2>/dev/null
    echo "TIME: TIMEOUT (${elapsed}s)" >> "$RESULTS"
    echo "  $label: TIMEOUT ✗"
    return 1
}

PROMPT="a cyberpunk city at night with neon signs reflecting in rain puddles, highly detailed"

# Test 1: Chroma flash 512x512 (8 steps with heun, cfg 1.0)
echo "--- Chroma flash tests ---"
echo "" >> "$RESULTS"
echo "=== CHROMA FLASH ===" >> "$RESULTS"

run_test "chroma-flash-512-s8" \
    "--diffusion-model $MODELS/chroma-unlocked-v47-flash-Q4_0.gguf --vae $MODELS/ae.safetensors --t5xxl $MODELS/t5-v1_1-xxl-encoder-Q4_K_M.gguf -p \"$PROMPT\" --cfg-scale 1.0 --sampling-method heun --steps 8 -W 512 -H 512 --diffusion-fa --offload-to-cpu --vae-tiling --chroma-disable-dit-mask --clip-on-cpu -v"

# Test 2: Chroma flash 512x512 (4 steps)
run_test "chroma-flash-512-s4" \
    "--diffusion-model $MODELS/chroma-unlocked-v47-flash-Q4_0.gguf --vae $MODELS/ae.safetensors --t5xxl $MODELS/t5-v1_1-xxl-encoder-Q4_K_M.gguf -p \"$PROMPT\" --cfg-scale 1.0 --sampling-method heun --steps 4 -W 512 -H 512 --diffusion-fa --offload-to-cpu --vae-tiling --chroma-disable-dit-mask --clip-on-cpu -v"

# Test 3: Chroma flash 768x768
run_test "chroma-flash-768-s8" \
    "--diffusion-model $MODELS/chroma-unlocked-v47-flash-Q4_0.gguf --vae $MODELS/ae.safetensors --t5xxl $MODELS/t5-v1_1-xxl-encoder-Q4_K_M.gguf -p \"$PROMPT\" --cfg-scale 1.0 --sampling-method heun --steps 8 -W 768 -H 768 --diffusion-fa --offload-to-cpu --vae-tiling --chroma-disable-dit-mask --clip-on-cpu -v"

# Test 4: FLUX.1-schnell 512x512 for comparison
echo "" >> "$RESULTS"
echo "=== FLUX.1-SCHNELL (comparison) ===" >> "$RESULTS"

run_test "schnell-512-s4" \
    "--diffusion-model $MODELS/flux1-schnell-q4_k.gguf --vae $MODELS/ae.safetensors --clip_l $MODELS/clip_l.safetensors --t5xxl $MODELS/t5-v1_1-xxl-encoder-Q4_K_M.gguf -p \"$PROMPT\" --cfg-scale 1.0 --steps 4 -W 512 -H 512 --diffusion-fa --offload-to-cpu --vae-tiling --clip-on-cpu -v"

echo "" >> "$RESULTS"
echo "DONE: $(date)" >> "$RESULTS"

echo ""
echo "All tests complete. Results in $RESULTS"
cat "$RESULTS"
