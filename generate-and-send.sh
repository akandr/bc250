#!/bin/bash
# generate-and-send.sh — generate image + send via Signal JSON-RPC
# FLUX.1-schnell (Q4_K + Q4_K_M t5xxl) on BC-250 (16GB unified memory)
# NOTE: sd-cli hangs after image generation on Vulkan/GFX1013, so we
# run it in background and kill it once the image file appears.
set -euo pipefail

PROMPT="${*:?Usage: generate-and-send.sh <prompt>}"
OUTPUT="/tmp/sd-output.png"
SD_CLI="/opt/stable-diffusion.cpp/build/bin/sd-cli"
MODELS_DIR="/opt/stable-diffusion.cpp/models"
SIGNAL_RPC="http://127.0.0.1:8080/api/v1/rpc"
RECIPIENT="+48503326388"
ACCOUNT="+48532825716"

# Auto-detect best available model
FLUX_DIR="$MODELS_DIR/flux"
T5XXL=""
MODE=""
EXTRA_FLAGS=""

if [ -f "$FLUX_DIR/flux1-schnell-q4_k.gguf" ] && \
   [ -f "$FLUX_DIR/ae.safetensors" ] && \
   [ -f "$FLUX_DIR/clip_l.safetensors" ]; then
  if [ -f "$FLUX_DIR/t5-v1_1-xxl-encoder-Q4_K_M.gguf" ]; then
    T5XXL="$FLUX_DIR/t5-v1_1-xxl-encoder-Q4_K_M.gguf"
    MODE="flux"
  elif [ -f "$FLUX_DIR/t5-v1_1-xxl-encoder-Q8_0.gguf" ]; then
    T5XXL="$FLUX_DIR/t5-v1_1-xxl-encoder-Q8_0.gguf"
    MODE="flux"
    EXTRA_FLAGS="--mmap"
  fi
fi

if [ -z "$MODE" ] && [ -f "$MODELS_DIR/sd-turbo.safetensors" ]; then
  MODE="sd-turbo"
fi

if [ -z "$MODE" ]; then
  echo "ERROR: No supported model found"
  exit 1
fi
echo "Using mode: $MODE (t5xxl: $(basename "${T5XXL:-none}"))"

# Step 1: unload Ollama models to free VRAM
echo "Unloading Ollama models..."
LOADED=$(curl -sf http://127.0.0.1:11434/api/ps 2>/dev/null || echo "{}")
for m in $(echo "$LOADED" | python3 -c "import sys,json; [print(m['name']) for m in json.load(sys.stdin).get('models',[])]" 2>/dev/null); do
  curl -sf -X POST http://127.0.0.1:11434/api/generate \
    -d "{\"model\":\"$m\",\"keep_alive\":0}" > /dev/null 2>&1 || true
done
sleep 2

# Step 2: generate image
echo "Generating image: $PROMPT"
rm -f "$OUTPUT"
START=$(date +%s)

case "$MODE" in
  flux)
    # Run sd-cli in background — it hangs after saving image (Vulkan cleanup bug)
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

    # Wait for image file to appear (max 8 min)
    WAITED=0
    while [ $WAITED -lt 480 ]; do
      if [ -f "$OUTPUT" ] && [ "$(stat -c%s "$OUTPUT" 2>/dev/null || echo 0)" -gt 1000 ]; then
        ELAPSED=$(( $(date +%s) - START ))
        echo "Image ready after ${ELAPSED}s, killing sd-cli..."
        sleep 2
        kill $SD_PID 2>/dev/null || true
        killall -9 sd-cli 2>/dev/null || true
        wait $SD_PID 2>/dev/null || true
        break
      fi
      sleep 3
      WAITED=$((WAITED + 3))
    done

    # Cleanup on timeout
    if [ ! -f "$OUTPUT" ]; then
      kill $SD_PID 2>/dev/null || true
      killall -9 sd-cli 2>/dev/null || true
      echo "ERROR: FLUX timed out after 480s"
      exit 1
    fi
    ;;
  sd-turbo)
    timeout 120 "$SD_CLI" -m "$MODELS_DIR/sd-turbo.safetensors" \
      -p "$PROMPT" -o "$OUTPUT" \
      --steps 4 -W 512 -H 512 --cfg-scale 1.0 2>&1 | tail -5 || true
    ;;
esac

if [ ! -f "$OUTPUT" ]; then
  echo "ERROR: image generation failed"
  exit 1
fi
SIZE=$(du -h "$OUTPUT" | cut -f1)
ELAPSED=$(( $(date +%s) - START ))
echo "Image: $OUTPUT ($SIZE) in ${ELAPSED}s"

# Step 3: send via signal-cli JSON-RPC
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

echo "Sending via Signal..."
RESPONSE=$(curl -sf -X POST "$SIGNAL_RPC" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" 2>&1) || { echo "ERROR: curl failed: $RESPONSE"; exit 1; }

echo "Signal: $RESPONSE"

if echo "$RESPONSE" | grep -q error; then
  echo "ERROR: Signal RPC error"
  exit 1
fi

echo "Done! Image sent to $RECIPIENT"
