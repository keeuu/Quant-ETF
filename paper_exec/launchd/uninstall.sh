#!/bin/bash
# Remove the paper-exec job.
set -euo pipefail
LABEL="com.quant-etf.paper-exec"
DST="$HOME/Library/LaunchAgents/${LABEL}.plist"
launchctl bootout "gui/$UID/${LABEL}" 2>/dev/null || true
rm -f "$DST"
echo "Uninstalled ${LABEL}."
