#!/usr/bin/env bash
# Install (or refresh) the crontab entry for the daily loop.
# Usage: ./scripts/install-cron.sh [HH:MM]   (default 05:30, local time)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TIME="${1:-05:30}"
HOUR="${TIME%%:*}"
MIN="${TIME##*:}"
if ! [[ "$HOUR" =~ ^[0-9]{1,2}$ && "$MIN" =~ ^[0-9]{1,2}$ ]]; then
    echo "usage: $0 [HH:MM]" >&2
    exit 1
fi

MARKER="# km-wiki-daily"
LINE="$MIN $HOUR * * * cd $REPO && PATH=\$HOME/.opencode/bin:\$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin ./loop/daily.sh >> $REPO/loop/logs/cron.log 2>&1 $MARKER"

(crontab -l 2>/dev/null | grep -vF "$MARKER" || true; echo "$LINE") | crontab -
echo "installed: daily loop at $TIME (local time)"
echo "  $LINE"
echo "remove with: crontab -l | grep -vF '$MARKER' | crontab -"
