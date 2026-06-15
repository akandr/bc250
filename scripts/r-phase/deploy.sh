#!/usr/bin/env bash
# deploy.sh — workstation-side. scp hardened harness + r-phase scaffolding
# to bc250, kick off model fetch, then orchestrate.
#
# Usage:
#   bash scripts/r-phase/deploy.sh                # full deploy + launch
#   DRY_RUN=1 bash scripts/r-phase/deploy.sh      # show what would happen
#   SSH_HOST=bc250r bash scripts/r-phase/deploy.sh
set -eu
SSH_HOST=${SSH_HOST:-bc250}
REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
DEST=phase-c-out
DRY=${DRY_RUN:-0}

say() { echo "[deploy] $*"; }
run() {
    if [[ "$DRY" == "1" ]]; then echo "DRY: $*"; else eval "$*"; fi
}

# 1. Reachability probe.
if ! ssh -o ConnectTimeout=10 -o BatchMode=yes "$SSH_HOST" 'echo reachable' 2>/dev/null; then
    say "FAIL: $SSH_HOST unreachable (kex closed or network down). Aborting."
    exit 2
fi

# 2. Sync scripts and harness.
say "syncing harness + r-phase scripts to $SSH_HOST:~/$DEST/"
run "ssh '$SSH_HOST' 'mkdir -p ~/$DEST/results'"
run "scp '$REPO_ROOT/benchmarks/bench-phase-c.py' '$SSH_HOST:~/$DEST/bench-phase-c.py'"
run "scp '$REPO_ROOT/scripts/r-phase/fetch-models.sh' '$SSH_HOST:~/$DEST/fetch-models.sh'"
run "scp '$REPO_ROOT/scripts/r-phase/bench-status.sh' '$SSH_HOST:~/$DEST/bench-status.sh'"
run "scp '$REPO_ROOT/scripts/r-phase/orchestrate.sh' '$SSH_HOST:~/$DEST/orchestrate.sh'"
run "scp '$REPO_ROOT/scripts/r-phase/gpu-overlay-sampler.sh' '$SSH_HOST:/tmp/gpu-overlay-sampler.sh'"
run "ssh '$SSH_HOST' 'chmod +x ~/$DEST/*.sh ~/$DEST/bench-phase-c.py /tmp/gpu-overlay-sampler.sh'"

# 3. Sampler smoke (2 sec) to confirm it writes lines.
say "sampler healthcheck"
run "ssh '$SSH_HOST' 'bash /tmp/gpu-overlay-sampler.sh /tmp/sampler-test.csv & sp=\$!; sleep 2; kill \$sp 2>/dev/null; wc -l /tmp/sampler-test.csv; head -3 /tmp/sampler-test.csv; rm -f /tmp/sampler-test.csv'"

# 4. Kick off model fetch (foreground first run prints to terminal; thereafter
#    it's a no-op so re-deploy is cheap).
say "running fetch-models.sh on $SSH_HOST (idempotent)"
run "ssh '$SSH_HOST' 'bash ~/$DEST/fetch-models.sh'" || say "fetch-models returned non-zero (some pulls may have failed; continuing)"

# 5. Pre-flight smoke: env-snapshot + 1-cell sanity on the smallest model.
say "pre-flight env-snapshot + smoke"
run "ssh '$SSH_HOST' 'python3 ~/$DEST/bench-phase-c.py --step env-snapshot && python3 ~/$DEST/bench-phase-c.py --step smoke 2>&1 | tail -20'"

# 6. Launch the orchestrator under nohup.
say "launching orchestrate.sh under nohup on $SSH_HOST"
run "ssh '$SSH_HOST' 'cd ~/$DEST && nohup bash ./orchestrate.sh > orchestrate.nohup.out 2>&1 & disown; echo launched pid=\$!; sleep 3; pgrep -af orchestrate.sh | head -3'"

say "deploy complete. Check progress with:"
say "  ssh $SSH_HOST 'bash ~/$DEST/bench-status.sh && cat ~/$DEST/STATUS.md'"
