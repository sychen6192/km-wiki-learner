#!/usr/bin/env bash
# km-wiki daily loop — the deterministic exoskeleton around the LLM librarian.
#
#   preflight:  git pull → vault scan
#   agent:      opencode run --command daily   (or `learn` when KM_TOPIC is set)
#   postflight: stats → lint (one repair pass on failure) → git commit → push
#
# Environment knobs:
#   KM_MODEL      opencode model override, e.g. anthropic/claude-sonnet-4-5
#   KM_MAX_ITEMS  work-item budget per run (default 3)
#   KM_TOPIC      run an on-demand deep dive on this topic instead of the daily loop
#   KM_TIMEOUT    seconds before the agent run is killed (default 3600)
#   KM_SKIP_AGENT set to 1 to run scan/lint/stats/commit only (dry run)
#   KM_NO_PULL / KM_NO_PUSH  skip the corresponding git step
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
DATE="$(date +%F)"
mkdir -p loop/logs loop/state loop/local
LOG="loop/logs/$DATE.log"

exec 9>"loop/state/.lock"
if ! flock -n 9; then
    echo "km-wiki: another loop is already running, exiting" >&2
    exit 0
fi

log() { printf '[%s] %s\n' "$(date +%T)" "$*" | tee -a "$LOG"; }

export KM_MAX_ITEMS="${KM_MAX_ITEMS:-3}"
MODEL_ARGS=()
[[ -n "${KM_MODEL:-}" ]] && MODEL_ARGS=(--model "$KM_MODEL")

log "km-wiki loop starting in $REPO"

# --- preflight ---------------------------------------------------------------
if [[ -z "${KM_NO_PULL:-}" ]] && git remote get-url origin >/dev/null 2>&1; then
    git pull --rebase --autostash 2>&1 | tee -a "$LOG" || log "WARN: pull failed, continuing with local state"
fi
python3 tools/vault.py scan --out loop/state/scan.json | tee -a "$LOG"

# --- agent -------------------------------------------------------------------
if [[ -z "${KM_SKIP_AGENT:-}" ]]; then
    if ! command -v opencode >/dev/null 2>&1; then
        log "ERROR: opencode not found. Install: curl -fsSL https://opencode.ai/install | bash"
        exit 1
    fi
    CMD="daily"
    CMD_ARGS=()
    if [[ -n "${KM_TOPIC:-}" ]]; then
        CMD="learn"
        CMD_ARGS=("$KM_TOPIC")
        log "on-demand mode: learn '$KM_TOPIC'"
    fi
    log "running: opencode run --command $CMD --auto ${MODEL_ARGS[*]:-}"
    timeout "${KM_TIMEOUT:-3600}" \
        opencode run --command "$CMD" --auto --title "km-wiki $CMD $DATE" \
        "${MODEL_ARGS[@]}" "${CMD_ARGS[@]}" 2>&1 | tee -a "$LOG" \
        || log "WARN: agent run exited non-zero; postflight will validate what it left behind"
else
    log "KM_SKIP_AGENT set — skipping agent run"
fi

# --- postflight --------------------------------------------------------------
python3 tools/vault.py stats | tee -a "$LOG" || true
if ! python3 tools/vault.py lint 2>&1 | tee -a "$LOG"; then
    log "lint failed — attempting one repair pass"
    if [[ -z "${KM_SKIP_AGENT:-}" ]]; then
        timeout 900 opencode run --command garden --auto "${MODEL_ARGS[@]}" 2>&1 | tee -a "$LOG" \
            || log "WARN: repair pass exited non-zero"
    fi
    python3 tools/vault.py stats >/dev/null 2>&1 || true
    python3 tools/vault.py lint 2>&1 | tee -a "$LOG" \
        || log "WARN: lint still failing — committing anyway so a human can review the diff"
fi

# --- commit & push -----------------------------------------------------------
git add -A vault
if git diff --cached --quiet; then
    log "no vault changes today — done"
    exit 0
fi
SUMMARY="$(sed -n 's/^> 一句話：//p' "vault/Daily/$DATE.md" 2>/dev/null | head -1 || true)"
git commit -m "wiki(daily): $DATE${SUMMARY:+ — $SUMMARY}" 2>&1 | tee -a "$LOG"

if [[ -z "${KM_NO_PUSH:-}" ]] && git remote get-url origin >/dev/null 2>&1; then
    BRANCH="$(git rev-parse --abbrev-ref HEAD)"
    pushed=""
    for delay in 2 4 8 16; do
        if git push -u origin "$BRANCH" 2>&1 | tee -a "$LOG"; then
            pushed=1
            break
        fi
        log "push failed, retrying in ${delay}s"
        sleep "$delay"
    done
    [[ -n "$pushed" ]] || git push -u origin "$BRANCH" 2>&1 | tee -a "$LOG" || log "WARN: push failed after retries — commit is safe locally"
fi
log "km-wiki loop finished"
