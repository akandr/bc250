#!/bin/bash
# generate-and-send.sh — generate image + send via Signal JSON-RPC
set -euo pipefail

PROMPT="${*:?Usage: generate-and-send.sh <prompt>}"
OUTPUT="/tmp/sd-output.png"
SD_CLI="/opt/stable-diffusion.cpp/build/bin/sd-cli"
MODEL="/opt/stable-diffusion.cpp/models/sd-turbo.safetensors"
SIGNAL_RPC="http://127.0.0.1:8080/api/v1/rpc"
RECIPIENT="+1YOURPHONENUMBER"
ACCOUNT="+1BOTPHONENUMBER"

# Step 1: unload Ollama models to free VRAM
for m in $(curl -sf http://127.0.0.1:11434/api/ps 2>/dev/null | grep -oP '"name":"[^"]+"' | cut -d'"' -f4); do
  curl -sf http://127.0.0.1:11434/api/generate -d "{\"model\":\"$m\",\"keep_alive\":0}" > /dev/null 2>&1 || true
done

# Step 2: generate image
echo "Generating image for: $PROMPT"
"$SD_CLI" -m "$MODEL" -p "$PROMPT" -o "$OUTPUT" \
  --steps 4 -W 512 -H 512 --cfg-scale 1.0 2>&1 | tail -5

if [ ! -f "$OUTPUT" ]; then
  echo "ERROR: image generation failed"
  exit 1
fi
echo "Image generated: $OUTPUT ($(du -h "$OUTPUT" | cut -f1))"

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

echo "Sending via Signal JSON-RPC..."
RESPONSE=$(curl -sf -X POST "$SIGNAL_RPC" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" 2>&1) || { echo "ERROR: curl failed: $RESPONSE"; exit 1; }

echo "Signal response: $RESPONSE"

if echo "$RESPONSE" | grep -q '"error"'; then
  echo "ERROR: Signal RPC returned error"
  exit 1
fi

echo "Image sent successfully to $RECIPIENT"
