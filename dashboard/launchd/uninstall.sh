#!/bin/bash
# Remove the daily dashboard job.
set -euo pipefail
LABEL="com.quant-etf.dashboard"
DST="$HOME/Library/LaunchAgents/${LABEL}.plist"
launchctl bootout "gui/$UID/${LABEL}" 2>/dev/null || true
rm -f "$DST"
echo "Uninstalled ${LABEL}."
