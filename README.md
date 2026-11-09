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

## Before you begin

| Requirement | Minimum | Check |
| --- | --- | --- |
| Docker Engine with Compose v2 (Docker install) | Docker 24, Compose 2.20 | `docker compose version` |
| Python (no-Docker install) | 3.10 | `python3 --version` |
| The computer you want to control | graphical login session | — |
| A model provider key | Anthropic, OpenAI, Gemini, DeepSeek, or a local Ollama | — |

The node dials out, so the controlled computer needs no open ports, no port
forwarding and no public DNS name. Linux desktops must run X11 or XWayland;
native Wayland windows cannot be driven. macOS needs Screen Recording and
Accessibility permission. On Windows, approve UAC prompts yourself.

## Install with Docker (recommended)

### 1. Get the code

```bash
git clone https://github.com/casualperson-1/Open-Computer-Use.git
cd Open-Computer-Use
```

### 2. Create the two server secrets

```bash
./deploy/generate-secrets.sh
# wrote deploy/secrets/fernet.key and deploy/secrets/secret_key
```

`fernet.key` encrypts saved provider keys; `secret_key` signs sessions. Both
are written read-only into a mode-700 directory that git ignores. Do not
regenerate them later: a new `fernet.key` cannot decrypt the existing database.

The script refuses to overwrite existing files. If it stops with
`error: secrets already exist`, keep the current key and go to step 3.

### 3. Create the owner account

```bash
docker compose run --rm -it server create-owner
```

You are prompted for a username (default `owner`) and a password (typed twice,
not echoed). This is the administrator; everybody else joins by invite. The
command refuses to run once an owner exists.

### 4. Start the server and the proxy

```bash
docker compose --profile proxy up -d
docker compose ps            # server should become "healthy"
```

Open <http://127.0.0.1:8080> and sign in as the owner. The server container
publishes no port; Caddy is the only front door and binds loopback only. If
`docker compose ps` shows no proxy, you started the stack without
`--profile proxy`.

Then save a model key under **Settings → Providers**. No task can run without
one.

### 5. Invite the other people

1. **Admin → Invites** → create a single-use link per person.
2. They open the link, set a password, and land in their own workspace.

Switch to `SIGNUP_MODE=closed` once everyone has joined. `open` lets anyone who
can reach the server create an account — use it only on a private network.

### 6. Connect a computer

1. **My computers** → **Add my computer**. The token is shown once, together
   with the exact command. Copy both; the token cannot be displayed again.
2. On the computer to be controlled, clone this repository and install the
   node's dependencies once:

   ```bash
   git clone https://github.com/casualperson-1/Open-Computer-Use.git
   cd Open-Computer-Use
   python3 -m venv .venv
   .venv/bin/pip install -r server/requirements.txt
   ```

   On Windows use `.venv\Scripts\python` for every command below.
3. Link and start it:

   ```bash
   .venv/bin/python -m node.cli connect \
     --server http://127.0.0.1:8080 \
     --token <token-from-my-computers> \
     --auto-start
   .venv/bin/python -m node.cli run
   ```

   `--server` must be the URL that computer can reach: the proxy origin for a
   remote machine, `http://127.0.0.1:8765` for the no-Docker install. `connect`
   saves it in `node-data/config.json`, so `run` needs no arguments.
4. The node shows **live** in My computers within a few seconds. Ask for
   something in a chat; without a live node a desktop run stops with
   “Connect your computer first.”

Per-platform permissions, browser backend and auto-start details:
[docs/node.md](docs/node.md).

### Phones

A phone browser is served the phone UI automatically, and `/m` opens it
explicitly. It supports signing in, chats, run control, approvals and computer
pairing, and deliberately has no MCP settings — configure MCP from a desktop
browser. `/?desktop=1` forces the desktop UI on a phone.

### Exposing it beyond this computer

Put a tunnel in front: see [docs/remote-access.md](docs/remote-access.md) for
Cloudflare Tunnel, Cloudflare Access and Twingate. Keep Caddy's port on
loopback unless you deliberately want LAN access.

## Install without Docker

```bash
git clone https://github.com/casualperson-1/Open-Computer-Use.git
cd Open-Computer-Use
python3 -m venv .venv
.venv/bin/pip install -r server/requirements.txt
.venv/bin/pip install -e server
.venv/bin/python -m server.cli create-owner
.venv/bin/python -m server.cli serve          # http://127.0.0.1:8765
```

`serve` binds loopback only. Keep it running in its own terminal, then connect
the node from a graphical session exactly as in step 6, using the local URL:

```bash
.venv/bin/python -m node.cli connect --server http://127.0.0.1:8765 --token <token>
.venv/bin/python -m node.cli run
```

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

## Secrets and resetting an install

These paths are git-ignored and hold live secrets. Never add them to a commit,
an archive you share, or a support ticket:

| Path | Holds |
| --- | --- |
| `data/cowork.db` | accounts, hashed sessions, chats and encrypted provider keys |
| `data/.fernet.key` | key that decrypts provider keys (no-Docker installs) |
| `deploy/secrets/` | compose Fernet key, session key, tunnel tokens |
| `backups/` | archives containing the database **and** the key |
| `node-data/` | this node's token, control token and audit log |

To reset an install from scratch (this deletes all accounts, chats and saved
keys), stop the stack and remove those paths, then repeat steps 2–4:

```bash
docker compose down -v      # also removes the data, backup and Caddy volumes
rm -rf deploy/secrets node-data backups data
```

On a no-Docker install, stop the server and delete `data/` and `backups/`.
The next start creates a new `data/.fernet.key`, and you create the owner again.

## Upgrade

1. Back up first: `docker compose run --rm -T server backup`.
2. Fetch/checkout the new release (`git pull` on the branch you deploy).
3. `docker compose build server && docker compose --profile proxy up -d`.
   Database migrations run at startup and are idempotent.
4. Verify `/api/health` and sign in. If you must roll back, check out the
   previous revision, rebuild, and restore the matching backup archive.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `error: this container has no owner account yet` | `docker compose run --rm -it server create-owner`. |
| No password prompt / no echo while typing | Expected: `create-owner` hides the password. Type it and press Enter. |
| `http://127.0.0.1:8080` refuses to connect | Start the proxy profile: `docker compose --profile proxy up -d`; check `docker compose ps`. |
| `error: secrets already exist in deploy/secrets` | Keep the existing key — regenerating it makes saved provider keys unreadable. |
| `pip install -r server/requirements.txt` fails on `pyautogui` | A graphical session and X11 libs are needed on the node; install `python3-tk`/`python3-xlib` (Debian/Ubuntu) or `python3-tk` (Fedora) and retry. |
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
server/        FastAPI app, accounts, runs, providers, node hub, UI (desktop + phone)
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
