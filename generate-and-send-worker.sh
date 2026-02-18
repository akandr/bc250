#!/bin/bash
# generate-and-send-worker.sh — background image generation worker
# Called by generate-and-send.sh wrapper. Runs in background.
# Stops Ollama entirely to free VRAM, generates image, sends via Signal,
# then restarts Ollama. BC-250 has 16GB unified memory — SD and Ollama
# cannot coexist.
set -euo pipefail

PROMPT="${*:?}"
OUTPUT="/tmp/sd-output.png"
SD_CLI="/opt/stable-diffusion.cpp/build/bin/sd-cli"
MODELS_DIR="/opt/stable-diffusion.cpp/models"
SIGNAL_RPC="http://127.0.0.1:8080/api/v1/rpc"
RECIPIENT="+48503326388"
ACCOUNT="+48532825716"
FLUX_DIR="$MODELS_DIR/flux"

log() { echo "[$(date +%H:%M:%S)] $*"; }

log "Worker started. Prompt: $PROMPT"

# Step 1: Wait for OpenClaw to complete its model response.
# The wrapper returned immediately. OpenClaw will call Ollama to generate
# a response ("your image is being generated"). The model is already loaded
# from the first call, so this takes 10-20s. We wait 45s to be safe.
log "Waiting 45s for model response to complete..."
sleep 45

# Step 2: Stop Ollama service entirely to guarantee VRAM is freed.
# Using systemctl stop (not just keep_alive:0) so nothing can trigger
# a model reload while SD is using the GPU.
log "Stopping Ollama service..."
sudo systemctl stop ollama
sleep 3
log "Ollama stopped."

# Step 3: Auto-detect model
T5XXL=""
EXTRA_FLAGS=""
if [ -f "$FLUX_DIR/flux1-schnell-q4_k.gguf" ] && \
   [ -f "$FLUX_DIR/ae.safetensors" ] && \
   [ -f "$FLUX_DIR/clip_l.safetensors" ]; then
  if [ -f "$FLUX_DIR/t5-v1_1-xxl-encoder-Q4_K_M.gguf" ]; then
    T5XXL="$FLUX_DIR/t5-v1_1-xxl-encoder-Q4_K_M.gguf"
  elif [ -f "$FLUX_DIR/t5-v1_1-xxl-encoder-Q8_0.gguf" ]; then
    T5XXL="$FLUX_DIR/t5-v1_1-xxl-encoder-Q8_0.gguf"
    EXTRA_FLAGS="--mmap"
  fi
fi

if [ -z "$T5XXL" ]; then
  log "ERROR: No FLUX model found"
  sudo systemctl start ollama
  exit 1
fi
log "Using FLUX.1-schnell with $(basename "$T5XXL")"

# Step 4: Generate image
log "Generating image..."
rm -f "$OUTPUT"
START=$(date +%s)

"$SD_CLI" \
  --diffusion-model "$FLUX_DIR/flux1-schnell-q4_k.gguf" \
  --vae "$FLUX_DIR/ae.safetensors" \
  --clip_l "$FLUX_DIR/clip_l.safetensors" \
  --t5xxl "$T5XXL" \
  --clip-on-cpu $EXTRA_FLAGS \
  -p "$PROMPT" -o "$OUTPUT" \
  --steps 4 -W 512 -H 512 --cfg-scale 1.0 \
  --sampling-method euler 2>&1 &
SD_PID=$!

# Wait for image file to appear (max 5 min)
WAITED=0
while [ $WAITED -lt 300 ]; do
  if [ -f "$OUTPUT" ] && [ "$(stat -c%s "$OUTPUT" 2>/dev/null || echo 0)" -gt 1000 ]; then
    ELAPSED=$(( $(date +%s) - START ))
    log "Image ready after ${ELAPSED}s, killing sd-cli..."
    sleep 2
    kill $SD_PID 2>/dev/null || true
    killall -9 sd-cli 2>/dev/null || true
    wait $SD_PID 2>/dev/null || true
    break
  fi
  sleep 3
  WAITED=$((WAITED + 3))
done

if [ ! -f "$OUTPUT" ]; then
  kill $SD_PID 2>/dev/null || true
  killall -9 sd-cli 2>/dev/null || true
  log "ERROR: Image generation timed out"
  sudo systemctl start ollama
  exit 1
fi

SIZE=$(du -h "$OUTPUT" | cut -f1)
ELAPSED=$(( $(date +%s) - START ))
log "Image: $OUTPUT ($SIZE) in ${ELAPSED}s"

# Step 5: Restart Ollama BEFORE sending (so it is loading while we send)
log "Restarting Ollama service..."
sudo systemctl start ollama
log "Ollama restart triggered."

# Step 6: Send via Signal
PAYLOAD=$(cat <<EOF
{
  "jsonrpc": "2.0",
  "method": "send",
  "params": {
    "account": "$ACCOUNT",
    "recipient": ["$RECIPIENT"],
    "message": "$PROMPT",
    "attachments": ["$OUTPUT"]
  },
  "id": "img-$(date +%s)"
}
EOF
)

log "Sending via Signal..."
RESPONSE=$(curl -sf -X POST "$SIGNAL_RPC" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" 2>&1) || { log "ERROR: curl failed: $RESPONSE"; exit 1; }

log "Signal response: $RESPONSE"

if echo "$RESPONSE" | grep -q '"error"'; then
  log "ERROR: Signal RPC returned error"
  exit 1
fi

log "Done! Image sent to $RECIPIENT"
