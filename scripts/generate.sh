#!/bin/bash
# Image generation wrapper for OpenClaw
# Usage: generate.sh PROMPT...
# All arguments are joined as the prompt text
# Deployed to: /opt/stable-diffusion.cpp/generate.sh
set -e

OUTPUT="/tmp/sd-output.png"
WIDTH=512
HEIGHT=512
PROMPT_PARTS=()

while [[ $# -gt 0 ]]; do
    case $1 in
        --output|-o) OUTPUT="$2"; shift 2 ;;
        --width|-W) WIDTH="$2"; shift 2 ;;
        --height|-H) HEIGHT="$2"; shift 2 ;;
        --prompt|-p) shift ;;
        --seed|-s) shift 2 ;;
        sudo) shift ;;
        *) PROMPT_PARTS+=("$1"); shift ;;
    esac
done

PROMPT="${PROMPT_PARTS[*]}"
if [ -z "$PROMPT" ]; then
    echo "Usage: generate.sh PROMPT..."
    echo "Example: generate.sh a red elephant"
    exit 1
fi

SD_CLI="/opt/stable-diffusion.cpp/build/bin/sd-cli"
MODEL="/opt/stable-diffusion.cpp/models/sd-turbo.safetensors"

echo "Step 1: Unloading Ollama models to free GPU memory..."
for m in llama3.1:8b qwen2.5:7b qwen2.5-coder:7b qwen3:8b; do
    curl -sf http://localhost:11434/api/generate -d "{\"model\":\"$m\",\"keep_alive\":0}" > /dev/null 2>&1 || true
done
sleep 3

echo "Step 2: Generating image ${WIDTH}x${HEIGHT}..."
echo "Prompt: $PROMPT"

"$SD_CLI" -m "$MODEL" -p "$PROMPT" --steps 4 --cfg-scale 1.0 -W "$WIDTH" -H "$HEIGHT" -o "$OUTPUT" 2>&1

if [ -f "$OUTPUT" ]; then
    echo "SUCCESS: Image saved to $OUTPUT"
    ls -lh "$OUTPUT"
else
    echo "ERROR: Image generation failed"
    exit 1
fi
