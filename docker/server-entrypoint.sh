#!/bin/sh
# Container entrypoint.
#
#   docker compose run --rm -it server create-owner   -> python -m server.cli create-owner
#   docker compose run --rm server backup             -> python -m server.cli backup
#   docker compose up -d server                       -> serve (with the owner guard)
#
# The guard itself lives in server.cli/serve and in the app lifespan. This
# wrapper only adds a container-friendly message; it is not a bypass.
set -eu

command="${1:-serve}"

case "$command" in
    serve)
        if ! output=$(python -m server.cli owner-status 2>&1); then
            case "$output" in
                *"no owner configured"*)
                    cat >&2 <<'MSG'
error: this container has no owner account yet, so it will not start.

Create the owner first. It uses the same data volume as the server:

    docker compose run --rm -it server create-owner

The bind guard stays in place: there is no way to skip this check.
MSG
                    ;;
                *)
                    # Configuration problem (missing/unreadable secret, bad DB).
                    # Print it verbatim instead of pretending there is no owner.
                    printf '%s\n' "$output" >&2
                    ;;
            esac
            exit 78
        fi
        exec python -m server.cli serve
        ;;
    create-owner|reset-owner-password|backup|restore|migrate|owner-status)
        exec python -m server.cli "$@"
        ;;
    *)
        # `sh`, `python ...`, debugging, or an explicit command from compose.
        exec "$@"
        ;;
esac
