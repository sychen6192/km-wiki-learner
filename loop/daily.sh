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
#   KM_AGENT_CMD  agent CLI to hand the prompt to (default: opencode run --auto;
#                 a headless agent must be allowed to approve its own writes)
#   KM_MODEL      model override, e.g. anthropic/claude-sonnet-4-5
#   KM_PYTHON     interpreter for the toolkit (default: first of python3/python/py
#                 that actually runs — `python3` is a stub on Windows)
#   KM_MAX_ITEMS  work-item budget per run (default 3)
#   KM_TOPIC      deep-dive this topic instead of running the daily loop
#   KM_TIMEOUT    seconds before the agent run is killed (default 3600)
#   KM_SKIP_AGENT set to 1 for a dry run (scan/lint/stats/commit only)
#   KM_NO_PULL / KM_NO_PUSH  skip the corresponding git step
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

# Resolved once here and exported, so the tools, the prompt templates and the
# agent all use the same interpreter. Why it cannot just be `python3`: see
# scripts/python.sh.
# shellcheck source=scripts/python.sh
source "$REPO/scripts/python.sh"

DATE="$(date +%F)"
mkdir -p loop/logs loop/state loop/local
LOG="loop/logs/$DATE.log"

# Single-instance lock. mkdir is atomic everywhere, unlike flock which macOS
# does not ship.
#
# Liveness is a heartbeat, not a pid check. "Is pid N still alive?" is the
# obvious design and it is wrong here: two Git Bash instances started from
# different places get separate MSYS pid namespaces, so each sees the other's
# running loop as dead, steals the lock, and both run at once — which is how
# two loops ended up writing the same vault. Pids are recyclable besides. A
# file the holder keeps touching answers the question that actually matters:
# is anyone still working?
LOCK="loop/state/lock"
BEAT="$LOCK/heartbeat"
BEAT_EVERY="${KM_HEARTBEAT_SEC:-30}"
BEAT_STALE="${KM_LOCK_STALE_SEC:-120}"
BEAT_PID=""
CHILD_PID=""


# Seconds since the lock last showed a sign of life. The directory's own mtime
# counts too, so a lock claimed microseconds ago is not mistaken for abandoned
# in the window before its first heartbeat lands.
lock_idle_seconds() {
    local newest=0 stamp
    for target in "$BEAT" "$LOCK"; do
        # Not `date -r`: GNU reads it as a path, BSD/macOS as epoch seconds, so
        # on macOS both lookups fail, newest stays 0, and every live lock reads
        # as abandoned — reinstating the concurrent-run bug this lock exists to
        # prevent. Python is already a hard requirement and answers the same
        # question identically everywhere.
        stamp="$("$KM_PYTHON" -c 'import os,sys;print(int(os.path.getmtime(sys.argv[1])))' \
                 "$target" 2>/dev/null || echo 0)"
        (( stamp > newest )) && newest="$stamp"
    done
    (( newest == 0 )) && { echo 999999; return; }
    echo $(( $(date +%s) - newest ))
}

# Every descendant of a pid, deepest first. Walking this by hand looks like
# reinventing `pkill -P`, and on Windows it is the only thing that works:
# MSYS keeps a POSIX process tree that Windows knows nothing about, so
# `timeout.exe` and the agent are not children of the wrapping subshell as far
# as `taskkill /T` is concerned — it ends the subshell and reports success
# while the agent carries on writing to the vault.
descendants_deepest_first() {
    local kid
    for kid in $(ps -ef 2>/dev/null | awk -v parent="$1" 'NR > 1 && $3 == parent {print $2}'); do
        descendants_deepest_first "$kid"
        printf '%s\n' "$kid"
    done
}

# End one process, whatever kind it is. A native Windows program has no POSIX
# signals for MSYS to deliver, so it needs taskkill; everything else needs the
# signal. Doing both costs nothing and means this works on either platform.
end_process() {
    local pid="$1" winpid
    winpid="$(cat "/proc/$pid/winpid" 2>/dev/null || true)"
    if [[ -n "$winpid" ]] && command -v taskkill >/dev/null 2>&1; then
        MSYS2_ARG_CONV_EXCL='*' taskkill /PID "$winpid" /F >/dev/null 2>&1 || true
    fi
    kill -KILL "$pid" 2>/dev/null || true
}

kill_child_tree() {
    [[ -n "$CHILD_PID" ]] || return 0
    local pid="$CHILD_PID" victim
    CHILD_PID=""
    printf 'km-wiki: 中止 agent（pid %s 及其子孫）\n' "$pid" | tee -a "$LOG" >&2
    while read -r victim; do
        [[ -n "$victim" ]] && end_process "$victim"
    done < <(descendants_deepest_first "$pid")
    kill -TERM "-$pid" 2>/dev/null || true      # the group, for non-Windows
    end_process "$pid"
}

release() {
    kill_child_tree
    [[ -n "$BEAT_PID" ]] && kill "$BEAT_PID" 2>/dev/null
    BEAT_PID=""
    rm -rf "$LOCK"
}

take_lock() {
    if ! mkdir "$LOCK" 2>/dev/null; then
        local idle
        idle="$(lock_idle_seconds)"
        (( idle < BEAT_STALE )) && return 1
        printf 'km-wiki: 回收停擺的鎖（心跳停了 %ss，原持有者 %s）\n' \
            "$idle" "$(cat "$LOCK/pid" 2>/dev/null || echo unknown)" | tee -a "$LOG" >&2
        rm -rf "$LOCK"
        mkdir "$LOCK" 2>/dev/null || return 1
    fi
    printf 'msys=%s win=%s since=%s\n' "$$" \
        "$(cat "/proc/$$/winpid" 2>/dev/null || echo '?')" "$(date +%T)" > "$LOCK/pid"
    touch "$BEAT"
    # Stops on its own once the lock is gone, so it can never outlive the run.
    ( while touch "$BEAT" 2>/dev/null; do sleep "$BEAT_EVERY"; done ) &
    BEAT_PID=$!

    # Ctrl-C must actually stop the loop. A bare INT handler would clean up and
    # then let the script carry on into postflight, quietly committing work the
    # user just asked to abandon — so interrupts exit, leaving partial writes in
    # the working tree for a human to inspect.
    trap 'release' EXIT
    trap 'release; echo "km-wiki: interrupted — partial work left uncommitted" >&2; exit 130' INT
    trap 'release; exit 143' TERM
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
    "$KM_PYTHON" tools/render.py --raw "$template" "$@" > "$rendered"
    log "prompt rendered to $rendered ($(wc -c < "$rendered") bytes)"

    # --auto is what makes opencode approve its own file writes when no human is
    # watching; without it every edit is auto-rejected and the run does nothing.
    # It only approves what is not explicitly denied, so the deny rules in the
    # user's global config still hold.
    local -a agent_cmd
    read -r -a agent_cmd <<< "${KM_AGENT_CMD:-opencode run --auto}"
    [[ -n "${KM_MODEL:-}" ]] && agent_cmd+=(--model "$KM_MODEL")

    log "running: ${agent_cmd[*]}"
    local prompt status=0
    prompt="$(cat "$rendered")"
    [[ -n "$TIMEOUT_CMD" ]] || log "note: no timeout command found (brew install coreutils for gtimeout) — running unbounded"

    # Backgrounded and waited on, rather than run in the foreground: bash defers
    # a trap until the current command returns, and the agent can run for an
    # hour. `wait` is interruptible, so Ctrl-C is acted on when it is pressed.
    #
    # `set -m` for this launch only, which puts the agent in its own process
    # group. That looks backwards — it stops a terminal's Ctrl-C from reaching
    # the agent at all — but it is the point: a group-wide SIGINT kills the
    # wrapping subshell first and orphans the native agent underneath it, and a
    # cancelled run then leaves a model still writing to the vault. Shielded,
    # the agent stays intact until the trap below takes the whole tree down on
    # purpose.
    set -m
    if [[ -n "$TIMEOUT_CMD" ]]; then
        ( "$TIMEOUT_CMD" "$timeout_s" "${agent_cmd[@]}" "$prompt" 2>&1 | tee -a "$LOG" ) &
    else
        ( "${agent_cmd[@]}" "$prompt" 2>&1 | tee -a "$LOG" ) &
    fi
    CHILD_PID=$!

    set +m

    wait "$CHILD_PID" || status=$?
    CHILD_PID=""

    return "$status"
}

log "km-wiki loop starting in $REPO"

# --- preflight ---------------------------------------------------------------
if [[ -z "${KM_NO_PULL:-}" ]] && git remote get-url origin >/dev/null 2>&1; then
    git pull --rebase --autostash 2>&1 | tee -a "$LOG" || log "WARN: pull failed, continuing with local state"
fi
"$KM_PYTHON" tools/extract.py 2>&1 | tee -a "$LOG" || log "WARN: extraction had problems; the report lists them"
"$KM_PYTHON" tools/vault.py scan --out loop/state/scan.json | tee -a "$LOG"

# --- agent -------------------------------------------------------------------
AGENT_FAILED=""
if [[ -z "${KM_SKIP_AGENT:-}" ]]; then
    if [[ -n "${KM_TOPIC:-}" ]]; then
        log "on-demand mode: learn '$KM_TOPIC'"
        run_agent prompts/learn.md "${KM_TIMEOUT:-3600}" "$KM_TOPIC" || AGENT_FAILED=1
    else
        run_agent prompts/daily.md "${KM_TIMEOUT:-3600}" || AGENT_FAILED=1
    fi
    # `${x:-}` rather than `$x`: under `set -u` a bare read of an unset name
    # aborts the run at the point where it is about to report what went wrong,
    # replacing a useful warning with "unbound variable". The default costs
    # nothing and cannot misfire.
    if [[ -n "${AGENT_FAILED:-}" ]]; then
        log "WARN: agent run exited non-zero — whatever it left behind is still validated"
        log "WARN: and committed, but this run's commit reflects little or no agent work"
    fi
    if [[ ! -f "vault/Daily/$DATE.md" ]]; then
        log "WARN: no daily report written — the agent did not finish its work"
        AGENT_FAILED=1
    fi
else
    log "KM_SKIP_AGENT set — skipping agent run"
fi

# --- postflight --------------------------------------------------------------
"$KM_PYTHON" tools/vault.py stats | tee -a "$LOG" || true
if ! "$KM_PYTHON" tools/vault.py lint 2>&1 | tee -a "$LOG"; then
    log "lint failed — attempting one repair pass"
    if [[ -z "${KM_SKIP_AGENT:-}" ]]; then
        run_agent prompts/garden.md 900 || log "WARN: repair pass exited non-zero"
    fi
    "$KM_PYTHON" tools/vault.py stats >/dev/null 2>&1 || true
    "$KM_PYTHON" tools/vault.py lint 2>&1 | tee -a "$LOG" \
        || log "WARN: lint still failing — committing anyway so a human can review the diff"
fi

# --- commit & push -----------------------------------------------------------
git add -A vault
if git diff --cached --quiet; then
    log "no vault changes today — done"
    exit 0
fi
# A failed run still commits — losing the agent's partial work would be worse —
# but the message must not read like a successful one.
SUMMARY="$(sed -n 's/^> 一句話：//p' "vault/Daily/$DATE.md" 2>/dev/null | head -1 || true)"
git commit -m "wiki(daily): $DATE${AGENT_FAILED:+ [agent 未完成]}${SUMMARY:+ — $SUMMARY}" 2>&1 | tee -a "$LOG"

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
