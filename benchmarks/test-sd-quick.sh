#!/bin/bash
# Quick sd-cli test
set -x
cd /opt/stable-diffusion.cpp
build/bin/sd-cli \
  -m models/flux/flux1-schnell-q4_k.gguf \
  --vae models/flux/ae.safetensors \
  --clip_l models/flux/clip_l.safetensors \
  --t5xxl models/flux/t5-v1_1-xxl-encoder-Q4_K_M.gguf \
  --cfg-scale 1.0 --sampling-method euler \
  --clip-on-cpu \
  -p "a cat" -W 512 -H 512 --steps 4 --seed 42 \
  -o /tmp/test-sd.png 2>&1 | tail -40
echo "EXIT: $?"
ls -la /tmp/test-sd.png 2>/dev/null
