# Open Computer Use

A self-hosted desktop agent for a homelab. Give it a goal in plain English; it
takes screenshots, asks a model of your choice what to do, and drives the mouse
and keyboard on **your own computer** through a small outbound node agent.
Multiple people can each own their own computer; the server never controls the
host's desktop.

```
┌──────────────┐        HTTPS / WSS         ┌──────────────────────────┐
│ browser UI   │ ◀────────────────────────▶ │ server                   │
│ (members,    │   session cookie + CSRF    │  FastAPI · SQLite        │
│  admin)      │                            │  auth · runs · providers │
└──────────────┘                            │  node hub · audit        │
                                            └────────────┬─────────────┘
                                                         │  outbound
                                                         │  wss /ws/node
                                                         │  (node dials in)
                                            ┌────────────▼─────────────┐
                                            │ node on the user's own   │
                                            │ computer                 │
                                            │  mouse · keyboard · apps │
                                            │  Playwright browser      │
                                            └──────────────────────────┘
```

The node dials **out** to the server, so the controlled machine needs no
inbound port, no port forwarding and no public DNS name. A run is routed only
to the requesting user's node; the server's own Xvfb desktop is
administrator-only.

## Quickstart (Docker, recommended)

Requires Docker with Compose v2.

```bash
./deploy/generate-secrets.sh
docker compose run --rm -it server create-owner
docker compose --profile proxy up -d
```

Open <http://127.0.0.1:8080>, sign in as the owner, then:

1. **Admin → Invites** → create a single-use link for each person.
2. Each person opens the link, creates an account, and goes to **My computers**.
3. **Add my computer** shows a token once and the exact `connect` command for
   Linux, macOS or Windows (see [docs/node.md](docs/node.md)).
4. On that computer, install the node (`pip install -r server/requirements.txt`
   plus `python -m node.cli connect --server … --token … --auto-start`) and start
   it. It appears live in **My computers**.
5. Ask for something in the chat. With no live node the run refuses with
   “Connect your computer first.”

To expose it beyond the host, put a tunnel in front: see
[docs/remote-access.md](docs/remote-access.md). Keep Caddy's port on loopback
unless you deliberately want LAN access.

### Local development

```bash
git clone https://github.com/casualperson-1/Open-Computer-Use.git
cd open-computer-use
python3 -m venv .venv
.venv/bin/pip install -r server/requirements.txt
.venv/bin/pip install -e server
.venv/bin/python -m server.cli create-owner
.venv/bin/python -m server.cli serve          # http://127.0.0.1:8765
```

Run the node from a graphical session:

```bash
python -m node.cli connect --server http://127.0.0.1:8765 --token <token>
python -m node.cli run
```

Linux needs X11/XWayland; native Wayland is not supported. macOS needs
Accessibility and Screen Recording permissions. Windows is supported by the
node but UAC prompts must be approved locally.

## Configuration

All settings are environment variables or `.env` (local) / compose `.env`
(stack). The most important ones:

| Variable | Default | Purpose |
| --- | --- | --- |
| `SIGNUP_MODE` | `invite` | `invite`, `open` (private network only) or `closed`. |
| `REMOTE_NODE_MODE` | `required` | Desktop runs need the user's own node. |
| `PUBLIC_BASE_URL` | unset | Exact public origin; drives Origin checks and Secure cookies. |
| `TRUSTED_PROXIES` | empty (`172.28.0.0/24` in compose) | CIDRs whose forwarded headers are believed. |
| `USER_RUN_LIMIT_PER_HOUR` | `30` | Per-member run limit; admin unlimited. |
| `USER_USAGE_CAP_USD` | unset | Optional per-member daily spend cap. |
| `NODE_APPROVAL_MODE` | `off` | `risky` asks before URLs/apps/key combos. |
| `DEFAULT_PROVIDER` / `DEFAULT_MODEL` | `anthropic` / provider default | Model defaults. |
| `TUNNEL_SIM_PORT` | `8090` | Test-only tunnel simulator port. |

The **complete** reference for every server, node and compose variable, plus
the secret files, is [docs/configuration.md](docs/configuration.md).

## Security model

- **Accounts**: the owner is the administrator; everyone else signs up through
  a single-use, expiring invite (or `open` only on a private network). Passwords
  are argon2id; sessions are 256-bit tokens stored hashed in HttpOnly,
  SameSite=Strict cookies; mutations require a session-bound CSRF header.
- **Isolation**: chats, runs, artifacts, schedules, provider keys, audit rows and
  nodes are scoped by `user_id`. A user can never read another user's rows or
  reach another user's node by guessing an ID. The server's simulated desktop is
  administrator-only.
- **Nodes**: per-node random tokens are shown once, stored hashed, and can be
  rotated/revoked per user. A revoked token closes the socket immediately.
- **Secrets**: provider keys are encrypted with a Fernet key that lives outside
  the database; the admin cannot read plaintext back through the API.
- **Transport**: bind is loopback by default. Non-loopback requires both an
  owner and `ALLOW_REMOTE_BIND=true`. `PUBLIC_BASE_URL` plus trusted-proxy
  handling make cookies `Secure` and Origin checks correct behind Caddy,
  Cloudflare Tunnel or Twingate. `CF-Connecting-IP` is honoured only from a
  trusted peer.
- **Prompt injection**: screen content is untrusted. The prompt tells the model
  to ignore instructions on screen; treat that as a mitigation, not a guarantee.
- **Audit**: server-side `audit_events` and node-side `audit.jsonl` record node
  commands, connections and rotations with redacted parameters.

## Backup and restore

The database and the Fernet key must travel together:

```bash
docker compose run --rm -T server backup
# -> /backups/ocu-backup-YYYYMMDD-HHMMSS.tar.gz (DB + key)
docker compose run --rm -T server restore /backups/ocu-backup-…tar.gz
```

`restore` verifies that the mounted key can decrypt stored secrets before
replacing the live database. A restore drill into a scratch volume, scheduled
backups and off-box copies are in [docs/operator.md](docs/operator.md).

## Upgrade

1. Back up first: `docker compose run --rm -T server backup`.
2. Fetch/checkout the new release (this repository has no configured remote by
   default; use your own copy).
3. `docker compose build server && docker compose --profile proxy up -d`.
   Database migrations run at startup and are idempotent.
4. Verify `/api/health` and sign in. If you must roll back, check out the
   previous revision, rebuild, and restore the matching backup archive.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `error: this container has no owner account yet` | `docker compose run --rm -it server create-owner`. |
| Login works but the cookie is not `Secure` | `PUBLIC_BASE_URL` must be the exact https origin and the request must arrive through the trusted proxy. |
| “Connect your computer first” | The node is offline, or its token belongs to another account. |
| `/ws/node` closes with 4401 | Token rotated/revoked; create a new one in My computers. |
| Node reconnects in a loop | `python -m node.cli status`, server URL reachability, token, and clock skew. |
| Chrome/Chromium control fails | `playwright install chromium` on the node. |
| `Refusing to bind 0.0.0.0` | An owner must exist and `ALLOW_REMOTE_BIND=true` must be set. |
| Health returns `503 degraded` | Database unreachable or encryption key missing; details stay only in the server logs. |
| Invite link says expired/used | Create a new invite; invites are single-use. |

More: [docs/docker.md](docs/docker.md) (stack), [docs/operator.md](docs/operator.md)
(runbook), [docs/remote-access.md](docs/remote-access.md) (tunnels),
[docs/node.md](docs/node.md) (node install).

## Repository layout

```
server/        FastAPI app, accounts, runs, providers, node hub, UI
node/          Desktop node agent (WebSocket client, drivers, control page)
shared/        Wire protocol, Computer abstraction, policy matcher
deploy/        Caddy, tunnel simulator, secret generation
docs/          Architecture, configuration, operator, node, remote access
tests/         Unit/integration suite + opt-in compose end-to-end test
browser/       Optional legacy Xvfb + noVNC desktop image
```

## Tests and CI

```bash
.venv/bin/python -m pytest -q                     # unit/integration
OCU_COMPOSE_SMOKE=1 .venv/bin/python -m pytest tests/test_compose_e2e.py -q
.venv/bin/python -m ruff check server node shared tests
```

The compose end-to-end test starts its own project on port 18080, creates the
owner, invites two users, enrols a fake node for one of them, dispatches a
screenshot command, and proves the other user cannot reach that node.

CI (`.github/workflows/ci.yml`) lints, runs the suite and builds the server
image. It does not push or deploy.

## Limitations and unfinished work

- The scheduler and deal-finder modules are **unwired** (no route or startup
  hook) and intentionally retained for a later scheduler/deals feature; see
  [docs/dead-code.md](docs/dead-code.md).
- Native Wayland is unsupported; XWayland works. The desktop driver controls
  the primary monitor only.
- The legacy `browser/` Xvfb + noVNC profile is maintained for demos, not the
  primary path.
- The nodes dashboard is functional but still minimal (no per-node history or
  latency chart).
- Vision-capable models are strongly recommended; text-only models cannot
  verify the screen.
- Cloudflare/Twingate integrations are configuration + local simulation only;
  live accounts, TLS cookies and real-browser/indicator behaviour must be
  verified by the operator (see the checklist in `docs/remote-access.md`).

## License

Use it, fork it, ship it. No warranty — an AI with mouse and keyboard access can
do real damage, so keep the safety rails on.
