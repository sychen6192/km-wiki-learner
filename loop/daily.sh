#!/usr/bin/env bash
# km-wiki daily loop — the deterministic exoskeleton around the LLM librarian.
#
#   preflight:  git pull → extract Raw/ to text → vault scan
#   agent:      render prompts/daily.md → hand the text to the agent CLI
#   postflight: stats → lint (one repair pass on failure) → git commit → push
#
# The prompt is plain text and the agent is whatever `KM_AGENT_CMD` names, so
# nothing here is tied to one vendor's config format.
#
# Environment knobs:
#   KM_AGENT_CMD  agent CLI to pipe the prompt into (default: opencode run)
#   KM_MODEL      model override, e.g. anthropic/claude-sonnet-4-5
#   KM_MAX_ITEMS  work-item budget per run (default 3)
#   KM_TOPIC      deep-dive this topic instead of running the daily loop
#   KM_TIMEOUT    seconds before the agent run is killed (default 3600)
#   KM_SKIP_AGENT set to 1 for a dry run (scan/lint/stats/commit only)
#   KM_NO_PULL / KM_NO_PUSH  skip the corresponding git step
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
DATE="$(date +%F)"
mkdir -p loop/logs loop/state loop/local
LOG="loop/logs/$DATE.log"

# Single-instance lock. mkdir is atomic everywhere, unlike flock which macOS
# does not ship — and a lock whose absence silently blocks every run is worse
# than no lock at all. A lock left behind by a killed run is reclaimed once its
# owner is gone.
LOCK="loop/state/lock"
take_lock() {
    if ! mkdir "$LOCK" 2>/dev/null; then
        local owner
        owner="$(cat "$LOCK/pid" 2>/dev/null || true)"
        if [[ -n "$owner" ]] && kill -0 "$owner" 2>/dev/null; then
            return 1
        fi
        echo "km-wiki: reclaiming stale lock from pid ${owner:-unknown}" >&2
        rm -rf "$LOCK"
        mkdir "$LOCK" 2>/dev/null || return 1
    fi
    echo $$ > "$LOCK/pid"
    trap 'rm -rf "$LOCK"' EXIT INT TERM
    return 0
}
if ! take_lock; then
    echo "km-wiki: another loop is already running, exiting" >&2
    exit 0
fi

log() { printf '[%s] %s\n' "$(date +%T)" "$*" | tee -a "$LOG"; }

# `timeout` is GNU coreutils; macOS has it only as gtimeout, if at all. Running
# without a watchdog beats refusing to run.
TIMEOUT_CMD=""
for candidate in timeout gtimeout; do
    if command -v "$candidate" >/dev/null 2>&1; then
        TIMEOUT_CMD="$candidate"
        break
    fi
done

export KM_MAX_ITEMS="${KM_MAX_ITEMS:-3}"

# Run the agent on a rendered prompt file. Everything vendor-specific lives here.
run_agent() {
    local template="$1" timeout_s="$2"
    shift 2
    local rendered="loop/state/prompt-$DATE.md"
    python3 tools/render.py --raw "$template" "$@" > "$rendered"
    log "prompt rendered to $rendered ($(wc -c < "$rendered") bytes)"

    local -a agent_cmd
    read -r -a agent_cmd <<< "${KM_AGENT_CMD:-opencode run}"
    [[ -n "${KM_MODEL:-}" ]] && agent_cmd+=(--model "$KM_MODEL")

    log "running: ${agent_cmd[*]}"
    if [[ -n "$TIMEOUT_CMD" ]]; then
        "$TIMEOUT_CMD" "$timeout_s" "${agent_cmd[@]}" "$(cat "$rendered")" 2>&1 | tee -a "$LOG"
    else
        log "note: no timeout command found (brew install coreutils for gtimeout) — running unbounded"
        "${agent_cmd[@]}" "$(cat "$rendered")" 2>&1 | tee -a "$LOG"
    fi
}

log "km-wiki loop starting in $REPO"

# --- preflight ---------------------------------------------------------------
if [[ -z "${KM_NO_PULL:-}" ]] && git remote get-url origin >/dev/null 2>&1; then
    git pull --rebase --autostash 2>&1 | tee -a "$LOG" || log "WARN: pull failed, continuing with local state"
fi
python3 tools/extract.py 2>&1 | tee -a "$LOG" || log "WARN: extraction had problems; the report lists them"
python3 tools/vault.py scan --out loop/state/scan.json | tee -a "$LOG"

# --- agent -------------------------------------------------------------------
if [[ -z "${KM_SKIP_AGENT:-}" ]]; then
    if [[ -n "${KM_TOPIC:-}" ]]; then
        log "on-demand mode: learn '$KM_TOPIC'"
        run_agent prompts/learn.md "${KM_TIMEOUT:-3600}" "$KM_TOPIC" \
            || log "WARN: agent run exited non-zero; postflight will validate what it left behind"
    else
        run_agent prompts/daily.md "${KM_TIMEOUT:-3600}" \
            || log "WARN: agent run exited non-zero; postflight will validate what it left behind"
    fi
else
    log "KM_SKIP_AGENT set — skipping agent run"
fi

# --- postflight --------------------------------------------------------------
python3 tools/vault.py stats | tee -a "$LOG" || true
if ! python3 tools/vault.py lint 2>&1 | tee -a "$LOG"; then
    log "lint failed — attempting one repair pass"
    if [[ -z "${KM_SKIP_AGENT:-}" ]]; then
        run_agent prompts/garden.md 900 || log "WARN: repair pass exited non-zero"
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
    for delay in 2 4 8 16 0; do
        git push -u origin "$BRANCH" 2>&1 | tee -a "$LOG" && break
        (( delay == 0 )) && { log "WARN: push failed after retries — commit is safe locally"; break; }
        log "push failed, retrying in ${delay}s"
        sleep "$delay"
    done
fi
log "km-wiki loop finished"
