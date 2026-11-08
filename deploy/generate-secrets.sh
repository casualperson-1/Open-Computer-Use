#!/usr/bin/env bash
# Generate the two secrets docker-compose.yml mounts read-only:
#
#   deploy/secrets/fernet.key  Fernet key for provider credentials at rest
#   deploy/secrets/secret_key  32-byte hex key for CSRF/session tokens
#
# Both are written read-only (0444) inside a mode-700 directory. The directory
# is the access boundary: only its owner can traverse it, while the container's
# non-root uid (10001) can still read the files through the bind mount.
#
# This script refuses to overwrite anything: changing the Fernet key makes
# existing provider keys and every existing backup unreadable.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
target="${1:-${script_dir}/secrets}"

umask 077
mkdir -p "$target"
chmod 700 "$target"

if [ -s "$target/fernet.key" ] || [ -s "$target/secret_key" ]; then
    echo "error: secrets already exist in $target" >&2
    echo "move them aside first; a new Fernet key cannot decrypt the current database or backups." >&2
    exit 1
fi

python3 - "$target" <<'PY'
import base64
import os
import secrets
import sys
from pathlib import Path

target = Path(sys.argv[1])
# Fernet keys are 32 random bytes, urlsafe-base64 encoded (44 characters).
(target / "fernet.key").write_bytes(base64.urlsafe_b64encode(secrets.token_bytes(32)))
(target / "secret_key").write_text(secrets.token_hex(32), encoding="ascii")
for name in ("fernet.key", "secret_key"):
    os.chmod(target / name, 0o444)
PY

echo "wrote $target/fernet.key and $target/secret_key (read-only, in a mode-700 directory)"
echo "back both up with the database: python -m server.cli backup stores the DB and key together."

# Optional remote-access tokens. They are only written when supplied, and an
# existing file is never overwritten. Prefer reading the token with `read -rs`
# rather than putting it in a shell history or a .env file.
write_token() {
    name="$1"
    value="$2"
    [ -n "$value" ] || return 0
    if [ -s "$target/$name" ]; then
        echo "keeping existing $target/$name"
        return 0
    fi
    printf '%s' "$value" > "$target/$name"
    chmod 0444 "$target/$name"
    echo "wrote $target/$name"
}

write_token cf_tunnel_token "${CF_TUNNEL_TOKEN:-}"
write_token twingate_access_token "${TWINGATE_ACCESS_TOKEN:-}"
write_token twingate_refresh_token "${TWINGATE_REFRESH_TOKEN:-}"
if [ -n "${CF_TUNNEL_TOKEN:-}${TWINGATE_ACCESS_TOKEN:-}${TWINGATE_REFRESH_TOKEN:-}" ]; then
    echo "remote-access tokens are in $target; unset CF_TUNNEL_TOKEN/TWINGATE_* now."
fi
