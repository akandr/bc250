#!/bin/bash
# generate-and-send.sh — async wrapper for image generation
# Returns immediately so OpenClaw exec tool does not time out.
# Actual work runs in background worker to avoid OOM (SD + Ollama cant coexist).
set -euo pipefail

PROMPT="${*:?Usage: generate-and-send.sh <prompt>}"

nohup /opt/stable-diffusion.cpp/generate-and-send-worker.sh "$PROMPT" &>/tmp/sd-worker.log &
WORKER_PID=$!

echo "Image generation started (pid $WORKER_PID)."
echo "The image will be sent via Signal in about 60 seconds."
echo "Prompt: $PROMPT"
