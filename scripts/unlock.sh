#!/usr/bin/env bash
# Clear a lock left behind by an interrupted run — and refuse to clear a live one.
#
# Liveness is the heartbeat the holder keeps touching, the same signal
# loop/daily.sh uses. Reading the pid and asking whether it is alive does not
# work here: two Git Bash instances started from different places have separate
# MSYS pid namespaces, so each sees the other's running loop as dead.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
# shellcheck source=scripts/python.sh
source "$REPO/scripts/python.sh"

LOCK="loop/state/lock"
STALE="${KM_LOCK_STALE_SEC:-120}"

if [[ ! -d "$LOCK" ]]; then
    echo "沒有鎖，不用清"
    exit 0
fi

owner="$(cat "$LOCK/pid" 2>/dev/null || echo unknown)"
idle="$("$KM_PYTHON" -c 'import os,sys,time;print(int(time.time()-os.path.getmtime(sys.argv[1])))' \
        "$LOCK/heartbeat" 2>/dev/null || echo 999999)"

if (( idle < STALE )); then
    echo "還在跑（心跳 ${idle}s 前，持有者 $owner）。真的要停，去中斷那一圈（Ctrl-C）"
    exit 1
fi

rm -rf "$LOCK"
echo "已清除停擺的鎖（心跳停了 ${idle}s，原持有者 $owner）"
