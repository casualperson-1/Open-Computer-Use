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
not echoed). This is the administrator; everyone else can sign up from the login
page. The command refuses to run once an owner exists.

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

### 5. Let people sign up

Sign-up is **open by default**: anyone who can reach the server opens the login
page, clicks **Create account**, and picks a username and a passphrase of at
least 12 characters. No invite is needed. Each account saves its own model
provider key and has its own run limit, so a public instance does not spend
your key.

The owner account is still the administrator. To turn signups off, set
`SIGNUP_MODE=closed` and restart the server. There are no invite codes: an old
`SIGNUP_MODE=invite` is treated as `open`. On a public Funnel, `open` means the
whole internet can register — that is the intended public-service mode.

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

With Tailscale on the server, one command gives a public HTTPS URL:

```bash
sudo tailscale funnel --bg 8765      # no-Docker server
sudo tailscale funnel --bg 8080      # with the compose proxy
```

Set `PUBLIC_BASE_URL=https://<machine>.<tailnet>.ts.net` and
`TRUSTED_PROXIES=127.0.0.1/32`, then open that URL from anywhere. Funnel
terminates TLS for you, so keep the app bound to loopback. Cloudflare Tunnel,
Cloudflare Access and Twingate are covered in
[docs/remote-access.md](docs/remote-access.md).

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

## Python packages

Everything the server and the node import is listed in
[`server/requirements.txt`](server/requirements.txt); CI resolves exact versions
into `server/requirements.lock.txt`. Do not install them one by one:

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r server/requirements.txt   # server + node + browser backend
.venv/bin/pip install -e server                    # only for the no-Docker install
.venv/bin/playwright install chromium              # browser backend only
```

| Package | Used for |
| --- | --- |
| `fastapi`, `uvicorn[standard]`, `python-multipart` | HTTP API, web UI, WebSocket server |
| `pydantic`, `pydantic-settings` | request models, configuration |
| `httpx` | provider calls and MCP over HTTP |
| `click` | `server.cli` and `node.cli` |
| `sqlalchemy[asyncio]`, `aiosqlite` | SQLite storage |
| `cryptography`, `argon2-cffi` | Fernet encryption for saved keys, argon2id passwords |
| `websockets` | node → server `/ws/node` client |
| `pyautogui`, `mss`, `pyperclip`, `pillow` | desktop node input and screenshots |
| `playwright` | browser backend (Chromium tab) |
| `apscheduler` | unwired scheduler module (see [docs/dead-code.md](docs/dead-code.md)) |
| `pytest`, `pytest-asyncio` | test suite |

The node uses the same file because the drivers are shared. On Arch, install the
system libraries first: [docs/install-arch.md](docs/install-arch.md).

## Prompts for AI agents

Both prompts below are meant to be pasted whole into a coding agent that has
shell access to the machine being set up — opencode, Claude Code, Cursor,
Codex, or similar. The agent inspects the OS, installs the packages, configures
the app and verifies the result. Read the commands it proposes, and expect it
to ask for the few things only you have: a model provider key, a node token, a
public URL, and permission to expose the server.

### Prompt: install and configure Open Computer Use

```text
You are installing Open Computer Use from
https://github.com/casualperson-1/Open-Computer-Use on this machine.

Work step by step, verify every step, and never claim success without evidence.
Ask me only when a step needs something only I have (a provider key, a node
token, a domain, permission to expose the server), or before rebooting,
changing firewall/DNS/Tailscale settings, or deleting data.

Step 0 — inspect first, change nothing:
- distro, version and package manager (apt/dnf/pacman/zypper), architecture
- Python >= 3.10, git, systemd, and Docker + compose if present
- whether this machine has a graphical login (X11/Xorg or XWayland) and is
  meant to be the server, the controlled desktop (node), or both
- whether an install already exists (server/, .env, data/); if it does, follow
  the upgrade notes instead of starting over
Print a one-paragraph plan with the commands you intend to run.

Step 1 — install the system packages (tk is required everywhere):
- Debian/Ubuntu:
  sudo apt update && sudo apt install -y git python3 python3-venv python3-pip \
    build-essential tk libx11-6 libxext6 libxrender1 libxtst6 libxi6 \
    xclip scrot libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 libgbm1 libasound2 \
    libpango-1.0-0 libcairo2 libxshmfence1 fonts-liberation
- Fedora:
  sudo dnf install -y git python3 python3-pip python3-devel gcc gcc-c++ make tk \
    libX11 libXext libXrender libXtst libXi xclip scrot nss atk at-spi2-atk \
    cups-libs libdrm libxkbcommon libXcomposite libXdamage libXrandr mesa-libGL \
    alsa-lib pango cairo libxshmfence liberation-fonts
- Arch:
  sudo pacman -Syu --needed base-devel git python python-pip tk xorg-server \
    xorg-xinit xfce4 xfce4-goodies lightdm lightdm-gtk-greeter xclip scrot \
    libx11 libxext libxrender libxtst libxi nss atk at-spi2-atk cups libdrm \
    libxkbcommon libxcomposite libxdamage libxrandr mesa alsa-lib pango cairo \
    libxshmfence ttf-liberation
- Other distros: install the equivalents. docs/install-ubuntu.md,
  docs/install-fedora.md and docs/install-arch.md name every package.
On Ubuntu 24.04 some libraries carry a t64 suffix (libasound2t64,
libatk1.0-0t64); resolve the real package names instead of dropping them.
The X11, clipboard, screenshot and Chromium libraries are needed when this
machine also runs the node; tk is needed wherever PyAutoGUI is imported.
Never pip install into the system Python (PEP 668) — use the venv below.

Step 2 — get the code and Python dependencies:
  git clone https://github.com/casualperson-1/Open-Computer-Use.git
  cd Open-Computer-Use
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r server/requirements.txt
  .venv/bin/pip install -e server
If the node or the browser backend runs here, also:
  .venv/bin/playwright install chromium
(`playwright install --with-deps chromium` needs sudo; ask me first.)

Step 3 — configuration:
  cp .env.example .env        # only if .env does not exist
Set SIGNUP_MODE=open (or closed if I asked for a private service).
Set PUBLIC_BASE_URL only when a public origin exists; leave it blank
otherwise. Set TRUSTED_PROXIES only for a proxy on this host:
127.0.0.1/32 for Tailscale Funnel, the compose subnet for the bundled Caddy
(leave its default there). Do not set ALLOW_REMOTE_BIND unless I explicitly
ask: keep the app on loopback and put a proxy/tunnel in front of it.

Step 4 — create the owner (interactive, so I can type the passphrase):
  .venv/bin/python -m server.cli create-owner
For a Docker install instead:
  ./deploy/generate-secrets.sh
  docker compose run --rm -it server create-owner
  docker compose --profile proxy up -d      # UI on http://127.0.0.1:8080
Never regenerate deploy/secrets/ if it already exists — saved provider keys
become unreadable.

Step 5 — keep it running:
- venv install: write a systemd unit (system or --user) with
  WorkingDirectory=<repo>, ExecStart=<repo>/.venv/bin/python -m server.cli serve
  and Restart=always; enable it and confirm it comes back after a restart.
- Docker install: `docker compose ps` must show the server healthy and the
  proxy running.

Step 6 — public URL, only if I asked for one:
  sudo tailscale funnel --bg 8765      # venv server
  sudo tailscale funnel --bg 8080      # compose proxy
Then PUBLIC_BASE_URL=https://<machine>.<tailnet>.ts.net and
TRUSTED_PROXIES=127.0.0.1/32, restart, and verify from outside this machine.
Funnel is public: with SIGNUP_MODE=open anyone with the URL can register.
Confirm that is what I want, otherwise switch to closed.

Step 7 — node, only on a graphical machine:
Run this inside the logged-in desktop session, as that user (never root):
  .venv/bin/python -m node.cli connect --server <URL> --token <token> --auto-start
  .venv/bin/python -m node.cli run
Ask me for the token from My computers → Add my computer; it is shown once.
Over SSH, export DISPLAY=:0 and XAUTHORITY=$HOME/.Xauthority first. Native
Wayland windows cannot be driven; X11 and XWayland are supported.

Step 8 — verify and report:
- curl -fsS http://127.0.0.1:8765/api/health (or :8080) returns 200
- the login page loads and the owner can sign in
- with SIGNUP_MODE=open, a private-window signup succeeds without an invite
- the node shows live and one screenshot task returns a screenshot
- through a tunnel, the same checks pass on the https URL
Report the repo path, service name, listening ports, installed package list,
public URL, and anything left for me to do. Never print passwords, tokens or
keys, and do not paste .env or data/ into chat.
```

### Prompt: install and configure an MCP server

```text
You are adding an MCP (Model Context Protocol) server to an existing Open
Computer Use install on this machine.

Ask me only for: what the MCP server should give the agent access to (a
directory, a service, an API), and whether it may be reachable over the
network. Then work autonomously and verify each step.

Step 0 — inspect the install:
- Is Open Computer Use running from a venv/systemd unit or Docker?
  (docker compose ps)
- Where does its server process run, and what PATH and filesystem does it see?
- MCP stdio servers are child processes of the app server, so they run there,
  not on the node or the phone. For Docker, the stock image has no Node.js:
  use HTTP transport unless you also build a custom image.
Print the transport you chose and why.

Step 1 — install the runtime packages on the server host:
- Node.js + npm (for npx-based servers):
  sudo apt install -y nodejs npm | sudo dnf install -y nodejs npm |
  sudo pacman -S --needed nodejs npm
- Python MCP servers: install uv (or a dedicated venv) with the distro package
  manager, not pip --user.
- HTTP MCP servers: nothing extra here; you need their URL and token.
Verify with node --version / npx --version / uv --version before continuing.

Step 2 — install and scope the server:
- Filesystem example:
    sudo install -d -o <app-user> -g <app-user> /srv/agent-files
    npx -y @modelcontextprotocol/server-filesystem /srv/agent-files
- Give it only the stated directory — never /, my home directory or a shell,
  unless I explicitly asked for that and understand the risk.
- For HTTP: deploy or locate the server, confirm TLS, and obtain a token.

Step 3 — prove it works by hand:
Run the exact command and environment once and check the MCP handshake lists
tools (initialize + tools/list; use `npx @modelcontextprotocol/inspector` or a
small JSON-RPC call). Record the command, args, URL and tool names.

Step 4 — register it in Open Computer Use:
Settings → MCP (desktop browser; the phone UI has no MCP page) → add:
name, transport (stdio/http), command + space-separated args + env JSON, or
URL + headers JSON → Test → enable.
If you can drive a browser, do it and show me the Test output. Otherwise give
me the exact field values and stop before saving.
Env and header values are encrypted at rest and never shown again; do not
paste secrets into chat, logs or the transcript.

Step 5 — verify end to end:
Start a short run that calls one tool. The model sees tools as
mcp__<server>__<tool>; a server that fails to start appears as an MCP:
observation without aborting the run. Confirm the tool result in the run.

Report the transport, exact command or URL, scoped path or service, tool
names, and anything I must approve or open in a firewall. Never print tokens.
```

## Configuration

All settings are environment variables or `.env` (local) / compose `.env`
(stack). The most important ones:

| Variable | Default | Purpose |
| --- | --- | --- |
| `SIGNUP_MODE` | `open` | `open` (anyone can register) or `closed`. Legacy `invite` means open. |
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

- **Accounts**: the owner is the administrator. Sign-up is open by default, so
  anyone who can reach the server can create an account (`SIGNUP_MODE=closed`
  turns signup off). Passwords are argon2id; sessions are 256-bit tokens stored
  hashed in HttpOnly, SameSite=Strict cookies, remembered for 90 days and
  renewed while the browser is used; mutations require a session-bound CSRF
  header.
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

More: [docs/docker.md](docs/docker.md) (stack), [docs/operator.md](docs/operator.md)
(runbook), [docs/remote-access.md](docs/remote-access.md) (tunnels, including
Tailscale Funnel), [docs/node.md](docs/node.md) (node install),
[docs/mcp.md](docs/mcp.md) (MCP tools),
[docs/install-arch.md](docs/install-arch.md) (Arch + XFCE packages).

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
owner, signs up two members, enrols a fake node for one of them, dispatches a
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
