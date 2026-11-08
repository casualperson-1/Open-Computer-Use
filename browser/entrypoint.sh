#!/bin/sh
set -e

# Clear stale X locks if the container restarted
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99

export DISPLAY=:99
export PYTHONUNBUFFERED=1

# Data directory for the encrypted key store and run history
mkdir -p "${COWORK_DATA_DIR:-/app/data}" 2>/dev/null || true

# Locate the Chromium build installed by Playwright and expose it to supervisord
if [ -z "$CHROME_BIN" ]; then
    CHROME_BIN=$(ls -d /opt/ms-playwright/chromium-*/chrome-linux*/chrome 2>/dev/null | head -n 1 || true)
    export CHROME_BIN
fi

exec "$@"
