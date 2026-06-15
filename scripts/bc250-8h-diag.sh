#!/usr/bin/env bash
# Autonomous on-box diagnostic for the 3 failed models from Phase D phase 1.
# Designed to run on bc250 via nohup for ~8h, no laptop interaction.
# Output: ~/phase-c-out/8h-window/<job>/...
set -uo pipefail

OUT="$HOME/phase-c-out/8h-window"
mkdir -p "$OUT"/{gemma26-timeout,gemma4l-sampler,qwen95-q4km-abi,extra}
LOG="$OUT/run.log"
exec > >(tee -a "$LOG") 2>&1

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*"; }

OLLAMA="http://localhost:11434"
EARLY="OMEGA-4-COBALT-RIVER-17"
PRIMARY="DELTA-7-VIOLET-MOUNTAIN-93"

# Helper: call ollama /api/generate with full options block, dump verbatim response.
gen() {
  # $1 model  $2 ctx  $3 num_predict  $4 outfile  $5 options-json  $6 prompt
  local model="$1" ctx="$2" npred="$3" out="$4" opts="$5" prompt="$6"
  python3 - "$model" "$ctx" "$npred" "$out" "$opts" "$prompt" <<'PYEOF'
import json, sys, time, urllib.request
model, ctx, npred, out, opts, prompt = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], sys.argv[5], sys.argv[6]
body = {
    "model": model,
    "prompt": prompt,
    "stream": False,
    "options": {"num_ctx": ctx, "num_predict": npred, **json.loads(opts)},
    "think": False,
}
t0 = time.time()
req = urllib.request.Request("http://localhost:11434/api/generate",
    data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=1800) as r:
        raw = r.read().decode("utf-8", errors="replace")
        d = json.loads(raw)
        d["_wall_s"] = time.time() - t0
        d["_request"] = body
except Exception as e:
    d = {"_error": str(e), "_wall_s": time.time() - t0, "_request": body}
with open(out, "w") as f:
    json.dump(d, f, indent=2, ensure_ascii=False)
print(f"  -> {out}  wall={d.get('_wall_s',0):.1f}s  err={d.get('_error','')}")
PYEOF
}

# Build a needle-in-haystack prompt. ctx is target context size in tokens (approx).
# We pad with simple repeating filler. EARLY appears at ~10%, PRIMARY at ~50%.
build_needle_prompt() {
  local ctx="$1" out="$2"
  python3 - "$ctx" "$out" <<'PYEOF'
import sys
ctx, out = int(sys.argv[1]), sys.argv[2]
# rough: 1 token ~ 4 chars for english; pad with sentence
sentence = "The library kept thousands of books about distant places, quiet rivers, and old songs. "
# leave room for question + needles + answer
target_chars = max(0, (ctx - 600) * 4)
filler = (sentence * ((target_chars // len(sentence)) + 1))[:target_chars]
n = len(filler)
early_pos = n // 10
prim_pos  = n // 2
early = " The secret code OMEGA-4-COBALT-RIVER-17 was etched on the south wall. "
prim  = " The hidden phrase DELTA-7-VIOLET-MOUNTAIN-93 was carved over the gate. "
body = filler[:early_pos] + early + filler[early_pos:prim_pos] + prim + filler[prim_pos:]
prompt = (
    "Read the passage carefully.\n\n"
    + body
    + "\n\nQUESTION: What is the hidden phrase carved over the gate?\n"
      "Answer with the exact phrase only, nothing else."
)
with open(out, "w") as f:
    f.write(prompt)
print(f"  prompt written: {out}  chars={len(prompt)}  approx_tokens={len(prompt)//4}")
PYEOF
}

# ============================================================
# JOB 1: gemma4-26b-q3 timeout investigation
# Hypothesis: model is real-slow, not stuck. Re-run with 1800s timeout.
# ============================================================
job_gemma26_timeout() {
  log "===== JOB1: gemma4-26b-q3 extended-timeout sweep ====="
  local D="$OUT/gemma26-timeout"
  local OPTS='{"temperature":0.2,"top_k":40,"top_p":0.9,"seed":42,"num_thread":8}'
  for ctx in 4096 8192 16384 32768; do
    log "  ctx=$ctx  short prompt, npred=200"
    gen "gemma4-26b-q3" "$ctx" 200 "$D/short-ctx${ctx}.json" "$OPTS" \
        "Write a single short paragraph about the moon, exactly 5 sentences." \
        || true
    log "  ctx=$ctx  needle prompt, npred=80"
    build_needle_prompt "$ctx" "$D/needle-ctx${ctx}.prompt.txt"
    gen "gemma4-26b-q3" "$ctx" 80 "$D/needle-ctx${ctx}.json" "$OPTS" \
        "$(cat $D/needle-ctx${ctx}.prompt.txt)" \
        || true
  done
  log "===== JOB1 DONE ====="
}

# ============================================================
# JOB 2: gemma4-latest sampler matrix for needle test
# Hypothesis: needle fail depends on sampler/template. Try several.
# ============================================================
job_gemma4l_sampler() {
  log "===== JOB2: gemma4-latest sampler matrix (ctx=4096 needle) ====="
  local D="$OUT/gemma4l-sampler"
  build_needle_prompt 4096 "$D/needle-4k.prompt.txt"
  local PROMPT
  PROMPT="$(cat "$D/needle-4k.prompt.txt")"

  # Variant configs (label -> json)
  declare -a VARIANTS=(
    "default:{}"
    "native:{\"temperature\":1.0,\"top_k\":64,\"top_p\":0.95,\"seed\":42}"
    "greedy:{\"temperature\":0.0,\"seed\":42}"
    "lowtemp:{\"temperature\":0.2,\"top_k\":40,\"top_p\":0.9,\"seed\":42}"
    "native_no_top:{\"temperature\":1.0,\"seed\":42}"
  )
  for v in "${VARIANTS[@]}"; do
    local label="${v%%:*}"
    local opts="${v#*:}"
    log "  variant=$label  opts=$opts"
    gen "gemma4-latest" 4096 80 "$D/needle-${label}.json" "$opts" "$PROMPT" \
        || true
  done

  # Also try /api/chat with explicit system role (different code path)
  log "  variant=chat-with-system"
  python3 - "$D/needle-chat-system.json" "$PROMPT" <<'PYEOF' || true
import json, sys, time, urllib.request
out, prompt = sys.argv[1], sys.argv[2]
body = {
    "model": "gemma4-latest",
    "messages": [
        {"role": "system", "content": "You are a precise retrieval assistant. Answer with exact text only."},
        {"role": "user", "content": prompt},
    ],
    "stream": False,
    "options": {"num_ctx": 4096, "num_predict": 80, "temperature": 1.0, "top_k": 64, "top_p": 0.95, "seed": 42},
    "think": False,
}
t0 = time.time()
req = urllib.request.Request("http://localhost:11434/api/chat",
    data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=900) as r:
        d = json.loads(r.read().decode("utf-8", errors="replace"))
        d["_wall_s"] = time.time() - t0
        d["_request"] = body
except Exception as e:
    d = {"_error": str(e), "_wall_s": time.time() - t0, "_request": body}
with open(out, "w") as f:
    json.dump(d, f, indent=2, ensure_ascii=False)
print(f"  -> {out}  wall={d.get('_wall_s',0):.1f}s")
PYEOF
  log "===== JOB2 DONE ====="
}

# ============================================================
# JOB 3: qwen3.5-9b-q4km GGUF ABI check
# Capture the actual error so we know what to fix (re-quantize? new GGUF?).
# ============================================================
job_qwen95_q4km() {
  log "===== JOB3: qwen3.5-9b-q4km ABI probe ====="
  local D="$OUT/qwen95-q4km-abi"
  ollama show qwen3.5-9b-q4km > "$D/show.txt" 2>&1 || true
  ollama show --modelfile qwen3.5-9b-q4km > "$D/modelfile.txt" 2>&1 || true
  log "  attempting tiny generate via ollama run"
  ( timeout 60 ollama run qwen3.5-9b-q4km "say hi" </dev/null > "$D/run.stdout" 2> "$D/run.stderr" ) || true
  log "  attempting /api/generate"
  gen "qwen3.5-9b-q4km" 4096 50 "$D/api-generate.json" '{"seed":42}' "Say hi briefly." || true
  log "  ollama server logs (tail)"
  journalctl --user-unit ollama --since "5 min ago" --no-pager > "$D/ollama-journal-tail.txt" 2>&1 || \
  sudo -n journalctl -u ollama --since "5 min ago" --no-pager > "$D/ollama-journal-tail.txt" 2>&1 || true
  log "  ollama list"
  ollama list > "$D/ollama-list.txt" 2>&1 || true
  log "===== JOB3 DONE ====="
}

# ============================================================
# JOB 4 (bonus): extended steady-state perf for qwen3.5-9b-ollama
# 30 consecutive runs at ctx=8192 to catch any throttling/drift
# ============================================================
job_extra_steady() {
  log "===== JOB4: 30-run steady-state qwen3.5-9b-ollama @ctx=8192 ====="
  local D="$OUT/extra"
  for i in $(seq 1 30); do
    log "  iteration $i/30"
    gen "qwen3.5-9b-ollama" 8192 150 "$D/steady-${i}.json" \
        '{"temperature":0.3,"seed":42}' \
        "Explain in 3 sentences why the sky is blue." || true
  done
  log "===== JOB4 DONE ====="
}

# ---------- main ----------
log "8h diagnostic START  out=$OUT"
log "  ollama health:"
curl -s --max-time 5 "$OLLAMA/api/tags" >/dev/null && log "  OLLAMA-OK" || log "  OLLAMA-DOWN"
log "  GPU mode:  $(cat /sys/module/amdgpu/parameters/bc250_cc_write_mode 2>/dev/null)"
log "  CU count: $(bash ~/bc250-40cu-unlock/scripts/cu_map.sh 2>&1 | grep -E 'CUs|active' | head -1)"

job_gemma26_timeout
job_gemma4l_sampler
job_qwen95_q4km
job_extra_steady

log "ALL JOBS DONE"
