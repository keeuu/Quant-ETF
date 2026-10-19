#!/bin/bash
# Install the daily dashboard job for the current user (launchd, gui domain).
set -euo pipefail
LABEL="com.quant-etf.dashboard"
SRC="$(cd "$(dirname "$0")" && pwd)/${LABEL}.plist"
DST="$HOME/Library/LaunchAgents/${LABEL}.plist"
mkdir -p "$HOME/Library/LaunchAgents" "/Users/zhangzeyuan/Projects/quant-ETF/logs"
plutil -lint "$SRC"
# replace a previously loaded copy, ignore "not loaded" errors
launchctl bootout "gui/$UID/${LABEL}" 2>/dev/null || true
cp "$SRC" "$DST"
launchctl bootstrap "gui/$UID" "$DST"
launchctl enable "gui/$UID/${LABEL}"
echo "Installed ${LABEL}: runs daily at 06:30 and 16:00 (local time). Test now with:"
echo "  launchctl kickstart -k gui/$UID/${LABEL}"
