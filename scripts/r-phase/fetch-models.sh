#!/usr/bin/env bash
# fetch-models.sh — idempotent pull of Tier-1+2 models for the R-phase
# re-benchmark. Run on the BC-250 box. Skips any GGUF / ollama tag that
# already exists. Logs to /opt/models/fetch.log so a periodic check can
# tell what was pulled and what's still in progress.
#
# Tier 1 (mandatory):
#   gpt-oss-20b MXFP4  — OpenAI native MXFP4 ggufs (HF)
#   gemma4-26b-a4b     — Q3_K_M or IQ3_M to fit 16 GB
#   qwen3.6-35b-a3b    — IQ2_M, drop-in for the 3.5
# Tier 2 (best effort):
#   qwen3.6-27b dense  — Q4_K_M
#   glm-5.1 small      — Q4_K_M
#   llama-3.4-8b       — Q4_K_M
set -u
MODELS_DIR=/opt/models
LOG=${MODELS_DIR}/fetch.log
mkdir -p "$MODELS_DIR"
exec >> "$LOG" 2>&1
echo "=== fetch-models started $(date -Iseconds) ==="

pull_ggjuf() {
    local out="$1" repo="$2" file="$3"
    if [[ -s "$MODELS_DIR/$out" ]]; then
        echo "[skip] $out already present ($(du -h "$MODELS_DIR/$out" | cut -f1))"
        return 0
    fi
    echo "[pull] $out  <-  $repo / $file"
    huggingface-cli download "$repo" "$file" --local-dir "$MODELS_DIR" --local-dir-use-symlinks False \
        && mv -n "$MODELS_DIR/$file" "$MODELS_DIR/$out" \
        || echo "[FAIL] $out"
}

pull_ollama() {
    local tag="$1"
    if ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$tag"; then
        echo "[skip] ollama tag $tag already present"
        return 0
    fi
    echo "[pull] ollama $tag"
    ollama pull "$tag" || echo "[FAIL] ollama $tag"
}

command -v huggingface-cli >/dev/null 2>&1 || {
    echo "huggingface-cli missing; installing..."
    pip3 install --user --quiet huggingface_hub[cli] || echo "[FAIL] hf-cli install"
}

# Tier 1
# gpt-oss-20b — OpenAI ships MXFP4 directly. Fallback to Unsloth Q4_K_M if MXFP4
# can't be located (the repo name has shifted; pin both attempts).
pull_ggjuf gpt-oss-20b-mxfp4.gguf  openai/gpt-oss-20b-gguf            gpt-oss-20b-MXFP4.gguf
[[ -s "$MODELS_DIR/gpt-oss-20b-mxfp4.gguf" ]] || \
    pull_ggjuf gpt-oss-20b-mxfp4.gguf  unsloth/gpt-oss-20b-GGUF  gpt-oss-20b-Q4_K_M.gguf

pull_ggjuf gemma4-26b-a4b-iq3m.gguf  unsloth/gemma-4-26b-a4b-GGUF  gemma-4-26b-a4b-IQ3_M.gguf
[[ -s "$MODELS_DIR/gemma4-26b-a4b-iq3m.gguf" ]] || \
    pull_ggjuf gemma4-26b-a4b-iq3m.gguf  bartowski/gemma-4-26b-a4b-GGUF  gemma-4-26b-a4b-Q3_K_M.gguf

pull_ggjuf qwen3.6-35b-a3b-iq2m.gguf  unsloth/Qwen3.6-35B-A3B-GGUF  Qwen3.6-35B-A3B-IQ2_M.gguf
[[ -s "$MODELS_DIR/qwen3.6-35b-a3b-iq2m.gguf" ]] || \
    pull_ggjuf qwen3.6-35b-a3b-iq2m.gguf  bartowski/Qwen3.6-35B-A3B-GGUF  Qwen3.6-35B-A3B-IQ3_XXS.gguf

# Ollama mirrors for cross-backend probes
pull_ollama gpt-oss:20b
pull_ollama gemma4-26b-a4b:latest || true   # may not be in ollama registry yet

# Tier 2 (best effort)
pull_ggjuf qwen3.6-27b-q4km.gguf  unsloth/Qwen3.6-27B-GGUF       Qwen3.6-27B-Q4_K_M.gguf
pull_ggjuf glm-5.1-q4km.gguf      bartowski/GLM-5.1-9B-GGUF      GLM-5.1-9B-Q4_K_M.gguf
pull_ggjuf llama-3.4-8b-q4km.gguf bartowski/Meta-Llama-3.4-8B-Instruct-GGUF  Meta-Llama-3.4-8B-Instruct-Q4_K_M.gguf

echo "=== fetch-models done $(date -Iseconds) ==="
echo "=== /opt/models inventory ==="
ls -lah "$MODELS_DIR" | tee -a "$LOG"
