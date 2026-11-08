# Docker deployment

The compose stack runs the server in a hardened, unprivileged container and
never publishes a port by default. Caddy is an optional front door; the legacy
Xvfb/noVNC desktop is a separate optional profile.

```
docker/
  server-entrypoint.sh     dispatches `server.cli` commands, enforces the owner guard
server/Dockerfile          multi-stage image, non-root (uid 10001), healthcheck
server/requirements.lock.txt  pinned runtime dependencies
deploy/generate-secrets.sh    creates the two secret files
deploy/Caddyfile.compose      proxy config with WebSocket-friendly timeouts
```

## 1. Generate secrets

```bash
./deploy/generate-secrets.sh
# writes deploy/secrets/fernet.key and deploy/secrets/secret_key (read-only, in a mode-700 directory)
```

`docker-compose.yml` mounts them read-only as `/run/secrets/fernet_key` and
`/run/secrets/secret_key`; `ENCRYPTION_KEY_FILE` and `SECRET_KEY_FILE` point at
those paths. Nothing secret is stored in the compose file or in `environment:`.

Back the Fernet key up. `server.cli backup` archives the database **and** the
key together, but if you lose both, saved provider keys are unrecoverable.
`deploy/secrets/` is git-ignored.

The key lives in a read-only mounted secret rather than a named volume, because
Compose file secrets are bind mounts and cannot point at a volume. It is still
handled together with the database by `backup`/`restore`: restoring onto a host
whose mounted key is read-only succeeds only when that key already matches the
archive, and refuses otherwise instead of swapping in an unreadable database.

## 2. Create the owner (before the first start)

```bash
docker compose run --rm -it server create-owner
```

That command uses the same `ocu-data` volume and secret files as the server.
Automated installs can avoid the prompt:

```bash
docker compose run --rm -e OCU_OWNER_PASSWORD \
  server create-owner --password-env OCU_OWNER_PASSWORD
```

There is no signup endpoint and no bypass. Until an owner exists the container
exits with status 78 and prints exactly that instruction; the app's own bind
guard (`server/security/network.py`) also refuses a non-loopback bind.

## 3. Start the server

```bash
docker compose up -d server
docker compose ps                 # health: healthy
docker compose logs -f server
```

The server listens on `0.0.0.0:8765` **inside** the private `internal` network
and is not reachable from the host. That is why `HOST=0.0.0.0` and
`ALLOW_REMOTE_BIND=true` are set there: both conditions are satisfied only
because an owner exists and the network has no published ports.

Networks:

| Network | Purpose |
| --- | --- |
| `internal` (172.28.0.0/24, `internal: true`) | Caddy ↔ server; no internet route |
| `egress` (bridge) | outbound model API calls only |
| `edge` (bridge) | host-published side of Caddy only (Docker cannot publish ports from an internal network); the server is not attached |

## 4. Put the proxy in front

```bash
docker compose --profile proxy up -d caddy
curl -s http://127.0.0.1:8080/api/health
```

Set these in `.env` for your deployment:

```dotenv
PUBLIC_BASE_URL=https://agent.example.com
TRUSTED_PROXIES=172.28.0.0/24
PROXY_BIND=127.0.0.1
PROXY_PORT=8080
```

- `TRUSTED_PROXIES` must be the compose subnet, because Caddy is the proxy that
  supplies `X-Forwarded-For`/`X-Forwarded-Proto`. With the default
  `172.28.0.0/24`, forwarded headers from anywhere else are ignored and the
  socket peer is used instead.
- `PUBLIC_BASE_URL` is the public https origin. It is accepted by the Origin
  check and enables `Secure` session cookies when TLS terminates at Caddy or a
  tunnel in front of it.
- To expose it beyond this host, change `PROXY_BIND` to `0.0.0.0` deliberately,
  and terminate TLS (Caddyfile TLS block, Cloudflare Tunnel, or Twingate).

### WebSockets through Caddy

`deploy/Caddyfile.compose` reverse-proxies everything to `server:8765` with:

- transparent WebSocket upgrade (Caddy handles `Upgrade: websocket`),
- `read_timeout 24h` / `write_timeout 24h` so idle `/ws` and `/ws/node`
  connections are not cut,
- TCP `keepalive 30s`, plus the protocol heartbeat (`HEARTBEAT_INTERVAL_MS =
  15000`) exchanged between node and server for application-level liveness.

Verify the node channel through the proxy by enrolling a node and running the
node agent against the proxy URL:

```bash
# with an owner session cookie and X-CSRF-Token from /api/auth/login:
curl -X POST http://127.0.0.1:8080/api/nodes \
  -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
  -d '{"name":"desk"}'
# -> {"node": {...}, "token": "..."}   (shown once)

OCC_NODE_SERVER=http://127.0.0.1:8080 OCC_NODE_TOKEN=<token> \
  python -m node.cli run --no-control-page --no-hotkeys --no-indicator
```

`/ws/node` authenticates only with that token: it does not use the browser
session and does not require an `Origin` header, so a node behind a tunnel
works the same way.

### Remote access through a tunnel

For Cloudflare Tunnel, Cloudflare Access and Twingate, including the
recommended `cloudflared → server` topology, the trust chain, the local tunnel
simulator and a security checklist, see
[docs/remote-access.md](remote-access.md). The short version: keep Caddy's port
host-local or stop the `proxy` profile, store the tunnel token as
`deploy/secrets/cf_tunnel_token`, and point both the UI and node hostnames at
`http://server:8765`.

## 5. Backups

One-off, through the same container and volumes:

```bash
docker compose run --rm server backup
docker compose run --rm -T server restore /backups/ocu-backup-20260101-120000.tar.gz
```

Scheduled with host cron:

```cron
15 3 * * * cd /opt/open-computer-use && /usr/bin/docker compose run --rm -T server backup >> /var/log/ocu-backup.log 2>&1
```

Or with the compose sidecar:

```bash
# every 24h by default; BACKUP_INTERVAL is in seconds
docker compose --profile backup up -d backup
```

Archives land in the `ocu-backups` volume (`/backups` in the container). Copy
them off-host; they contain the database and the Fernet key.

## 6. Optional: legacy desktop stack

The all-in-one Xvfb + noVNC image is not part of the default stack:

```bash
docker compose --profile desktop up -d
# dashboard:   http://127.0.0.1:8765   (create its owner first, see below)
# virtual desktop: http://127.0.0.1:6080/vnc.html
docker compose run --rm -it desktop python -m server.cli create-owner
```

It publishes only on `127.0.0.1` and keeps its own data volume
(`ocu-desktop-data`). For real desktop control prefer a node on the machine you
want to drive; the server container deliberately has no display.

## Hardening summary

| Control | Value |
| --- | --- |
| User | `10001:10001`, no shell, no home |
| Root filesystem | `read_only: true`, `tmpfs` for `/tmp` |
| Capabilities | `cap_drop: ALL`; Caddy re-adds `NET_BIND_SERVICE` only |
| Privilege escalation | `no-new-privileges:true` |
| Resources | 2 CPU / 2 GiB / 256 pids (server), 256 MiB (Caddy) |
| Ports | none on `server`; Caddy binds `127.0.0.1:${PROXY_PORT:-8080}` |
| Secrets | read-only files under `/run/secrets`, git-ignored |
| Health | `GET /api/health` every 30 s, 20 s start period |

## Troubleshooting

- **`error: this container has no owner account yet`** — run
  `docker compose run --rm -it server create-owner`.
- **`Refusing to bind 0.0.0.0`** — the owner exists but `ALLOW_REMOTE_BIND` is
  unset; the compose file sets it, so this means the container was started
  outside compose.
- **`SECRET_KEY_FILE points to ... which does not exist`** — run
  `deploy/generate-secrets.sh` before any compose command.
- **Login works but cookies are not `Secure`** — `PUBLIC_BASE_URL` must be the
  https origin and the request must arrive through the trusted proxy.
- **`/ws/node` closes with 4401** — the token was revoked, rotated, or belongs
  to another server; enrol a new node.

## Multi-user accounts in the container

The first account is still the owner and administrator. Create it before the
first start (step 2 above). After that, members sign up from the login screen:

```bash
# invite-only (default): print a single-use link the admin can send
docker compose run --rm server create-invite --expires-hours 72 --label "Sam"
# or from Admin → Invites in the UI
```

`SIGNUP_MODE` controls this: `invite` (default), `open` (anyone who can reach the
server — **only on a private network**) or `closed`. Every member gets argon2id
passwords, the same login throttling and CSRF rules as the owner.

Account administration:

```bash
docker compose run --rm server list-users
docker compose run --rm server disable-user sam
docker compose run --rm server enable-user sam
docker compose run --rm server delete-user sam     # deletes their chats, runs and nodes
```

Nobody, including members, can drive the server's own desktop: desktop runs only
use the node that the signed-in user added under **My computers**, and
`REMOTE_NODE_MODE=required` is the default. The legacy `desktop` profile is not
part of the default stack and must never be exposed to non-admin accounts.

Member limits live on the account: `USER_RUN_LIMIT_PER_HOUR` (default 30) and an
optional `USER_USAGE_CAP_USD` daily cap. The administrator is unlimited and can
adjust a member in Admin → Members.

## Moving an existing single-user install into the stack

The old install has `data/cowork.db` and `data/.fernet.key`. The key must become
the stack's `fernet_key` secret, because provider keys in the database are
encrypted with it.

```bash
# 0. Back up the old install first (writes into /tmp; data/ is only read).
OPEN_COMPUTER_USE_DATA_DIR=./data BACKUP_DIR=/tmp/ocu-pre-migration \
  python -m server.cli backup

# 1. Use the existing key as the compose secret. Never edit data/ in place.
install -d -m 700 deploy/secrets
install -m 444 data/.fernet.key deploy/secrets/fernet.key
./deploy/generate-secrets.sh --force-secret-key-only   # or create secret_key by hand

# 2. Put the archive into the backups volume and restore it.
docker compose run --rm -T server sh -c 'ls /backups'     # start the service once if empty
docker compose cp /tmp/ocu-pre-migration/ocu-backup-*.tar.gz \
  server:/backups/          # compose cp needs a running container; see note below
docker compose run --rm -T server restore /backups/ocu-backup-YYYYMMDD-HHMMSS.tar.gz

# 3. Create the owner. Startup then assigns every old row to that account.
docker compose run --rm -it server create-owner

# 4. Start and sign in at the URL printed by create-owner.
docker compose up -d server
```

`docker compose cp` needs a created container; the reliable alternative is a
one-shot helper that writes through the volume:

```bash
docker compose run --rm -T -v /tmp/ocu-pre-migration:/src:ro server \
  sh -c 'cp /src/ocu-backup-*.tar.gz /backups/'
```

Restore happens **before** `create-owner` because the old database has no users.
The database migration adds `user_id` with a `0` sentinel; the next startup (and
`create-owner`) hands every such row to the owner, so chats, runs, provider
settings and nodes keep working with no data loss. Restore refuses to swap in a
database whose key does not match the mounted secret, and it never touches the
original `data/` directory.

## Upgrading to multi-user

`docker compose up -d server` applies the schema migration automatically. Rows
from the single-user era are claimed by the owner account on the same start; the
log line names each table and the number of rows claimed. Existing sessions are
still valid, and the owner keeps every old chat and provider key.
