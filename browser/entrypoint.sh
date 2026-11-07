#!/bin/sh
set -e

# Clear stale X locks if container restarted
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99

exec "$@"
