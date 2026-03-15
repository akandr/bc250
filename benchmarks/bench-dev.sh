#!/bin/bash
# Benchmark FLUX.1-dev at multiple resolutions
cd /opt/stable-diffusion.cpp
M="models/flux"
DEV="$M/flux1-dev-Q4_K_S.gguf"
COMMON="--vae $M/ae.safetensors --clip_l $M/clip_l.safetensors --t5xxl $M/t5-v1_1-xxl-encoder-Q4_K_M.gguf --cfg-scale 3.5 --sampling-method euler --clip-on-cpu --offload-to-cpu --fa"

OUT="/opt/netscan/tmp/bench-dev-results.txt"
echo "FLUX.1-dev Q4_K_S Benchmark — $(date)" > "$OUT"

# Test 768x768 s20
echo -n "dev 768x768 s20: " >> "$OUT"
rm -f /tmp/test-dev-768.png
timeout 600 build/bin/sd-cli --diffusion-model "$DEV" $COMMON --vae-tiling -p "cyberpunk city" -W 768 -H 768 --steps 20 --seed 42 -o /tmp/test-dev-768.png > /tmp/sd-dev-768.log 2>&1 &
PID=$!
for i in $(seq 1 120); do
    sleep 5
    if [ -f /tmp/test-dev-768.png ] && [ "$(stat -c%s /tmp/test-dev-768.png 2>/dev/null || echo 0)" -gt 1000 ]; then
        sleep 1; kill $PID 2>/dev/null; wait $PID 2>/dev/null; pkill -f sd-cli 2>/dev/null
        grep "generate_image completed" /tmp/sd-dev-768.log >> "$OUT"
        break
    fi
    if ! kill -0 $PID 2>/dev/null; then
        echo "FAIL" >> "$OUT"
        break
    fi
done
sleep 3

# Test 1024x1024 s20 tiling
echo -n "dev 1024x1024 s20: " >> "$OUT"
rm -f /tmp/test-dev-1024.png
timeout 600 build/bin/sd-cli --diffusion-model "$DEV" $COMMON --vae-tiling -p "cyberpunk city" -W 1024 -H 1024 --steps 20 --seed 42 -o /tmp/test-dev-1024.png > /tmp/sd-dev-1024.log 2>&1 &
PID=$!
for i in $(seq 1 120); do
    sleep 5
    if [ -f /tmp/test-dev-1024.png ] && [ "$(stat -c%s /tmp/test-dev-1024.png 2>/dev/null || echo 0)" -gt 1000 ]; then
        sleep 1; kill $PID 2>/dev/null; wait $PID 2>/dev/null; pkill -f sd-cli 2>/dev/null
        grep "generate_image completed" /tmp/sd-dev-1024.log >> "$OUT"
        break
    fi
    if ! kill -0 $PID 2>/dev/null; then
        echo "FAIL" >> "$OUT"
        break
    fi
done

echo "DONE" >> "$OUT"
cat "$OUT"

# Restart ollama
sudo systemctl start ollama
