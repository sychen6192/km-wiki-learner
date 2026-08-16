#!/usr/bin/env bash
# Resolve a Python interpreter that actually runs, as KM_PYTHON.
#
# Being on PATH is not the same as working: Windows ships a `python3` that is a
# Microsoft Store shortcut — it prints nothing, exits 49, and satisfies every
# `command -v` check you throw at it. The only honest test is to run something.
#
#   source scripts/python.sh   → exports KM_PYTHON, exits 1 if there is none
#   ./scripts/python.sh        → prints the interpreter name
#
# Set KM_PYTHON yourself to pin a specific one; this leaves it alone.

if [[ -z "${KM_PYTHON:-}" ]]; then
    for _km_candidate in python3 python py; do
        if command -v "$_km_candidate" >/dev/null 2>&1 && "$_km_candidate" -c '' >/dev/null 2>&1; then
            KM_PYTHON="$_km_candidate"
            break
        fi
    done
    unset _km_candidate
fi

if [[ -z "${KM_PYTHON:-}" ]]; then
    echo "km-wiki: 找不到可用的 Python（試過 python3／python／py）。裝好之後再跑，或設 KM_PYTHON=..." >&2
    # `return` when sourced, `exit` when executed — either way the caller stops.
    return 1 2>/dev/null || exit 1
fi

export KM_PYTHON
# Executed directly: print it, so `$(shell ./scripts/python.sh)` works in make.
# Sourced: say nothing, the export is the whole point.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    echo "$KM_PYTHON"
fi
