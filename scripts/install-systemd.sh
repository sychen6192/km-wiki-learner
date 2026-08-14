#!/usr/bin/env bash
# Install the daily loop as a systemd user timer (better than cron on laptops:
# Persistent=true catches up runs missed while the machine was asleep).
# Usage: ./scripts/install-systemd.sh [HH:MM]   (default 05:30, local time)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TIME="${1:-05:30}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$UNIT_DIR"

sed "s|{{REPO}}|$REPO|g" "$REPO/systemd/km-wiki.service" > "$UNIT_DIR/km-wiki.service"
sed -e "s|{{REPO}}|$REPO|g" -e "s|{{TIME}}|$TIME|g" \
    "$REPO/systemd/km-wiki.timer" > "$UNIT_DIR/km-wiki.timer"

systemctl --user daemon-reload
systemctl --user enable --now km-wiki.timer
echo "installed: km-wiki.timer at $TIME daily"
systemctl --user list-timers km-wiki.timer --no-pager || true
echo "logs:   journalctl --user -u km-wiki.service"
echo "run now: systemctl --user start km-wiki.service"
