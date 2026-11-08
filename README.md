# Open Computer Use

A self-hosted, bring-your-own-key desktop agent. Give it a goal in plain English;
it takes screenshots, looks at your screen, and drives the mouse and keyboard
until the task is done.

It is a small, hackable take on computer-use agents: local-first, one owner
account per installation, no subscription, and you choose the model. Works with
Anthropic, OpenAI, Google Gemini, DeepSeek, local Ollama, and any
OpenAI-compatible endpoint.

```
Goal ──▶ screenshot ──▶ model (your key) ──▶ tool call ──▶ mouse / keyboard
             ▲                                                   │
             └──────────────── new screenshot ◀──────────────────┘
```

## What it does

- **Real computer control**: move/click/drag/scroll with PyAutoGUI, type text and
  keyboard shortcuts, take screenshots with mss. Works on Windows, macOS and
  Linux/X11 (including XWayland).
- **Native tool calling** for every provider, not JSON-in-prose parsing.
- **BYOK with encrypted local storage**: provider keys are encrypted with a local
  Fernet key and never leave the machine except in requests to that provider.
- **OpenAI-compatible anything**: point it at OpenRouter, Groq, Together, vLLM,
  LM Studio, llama.cpp or your own gateway with a base URL, key and model id.
- **Safety rails**: per-action approval mode, pause/stop, ask-the-human handoff,
  step and cost limits, and an emergency stop (move the mouse into a screen
  corner).
- **Live dashboard**: activity feed, screenshots, token/cost counters and a
  connectable noVNC virtual desktop in Docker mode.
- **Safe simulated desktop** (`--dry-run` / the "Simulated desktop" option) so
  you can try tasks or run CI without letting a model touch your real mouse.

## Quickstart (local, real desktop)

Requirements: Python 3.10+ and a graphical session.

```bash
git clone <your-repo> open-computer-use && cd open-computer-use
python3 -m venv .venv
.venv/bin/pip install -r server/requirements.txt
.venv/bin/pip install -e server
.venv/bin/open-computer-use serve
```

Open <http://127.0.0.1:8765>. On first launch you create the owner account; do
this from the same machine, because first-run setup is local-only by default.
Then pick a provider, paste a key, save, and describe a task.
`open-computer-use doctor` checks screen access and key configuration.

Linux needs X11 (or XWayland) and a reachable `DISPLAY`. On macOS, grant your
terminal/Python **Accessibility** and **Screen Recording** permissions. On
Wayland-only sessions, use the Docker virtual desktop below.

### Docker virtual desktop (recommended for a first run)

This runs Open Computer Use inside a throwaway X desktop with Chromium and noVNC, so the
agent never touches your real mouse.

```bash
cp .env.example .env
docker compose up -d --build
```

- App: <http://127.0.0.1:8765>
- Live virtual desktop / manual takeover: <http://127.0.0.1:6080/vnc.html>

The agent starts on a virtual 1280×720 desktop. Screenshots stream into the app;
noVNC lets you take over for logins, CAPTCHAs or sensitive steps. Ports are bound
to localhost by default.

## Accounts and security

Open Computer Use is a single-owner installation. The account can control the
same desktop as the app, so open registration is intentionally not supported.
The owner account is created from the server's terminal, never from the browser:

```bash
python -m server.cli create-owner            # no-echo password prompt
python -m server.cli create-owner --password-env OCU_OWNER_PASSWORD
python -m server.cli reset-owner-password    # signs out every session
```

There is no signup endpoint and no self-service password recovery.

- **Passwords** are hashed with **argon2id** (64 MiB, 3 passes, 32-byte salt).
  Older scrypt hashes still verify and are transparently upgraded on the next
  sign-in. Minimum length is 12 characters.
- **Sessions** use a random 256-bit token in an HttpOnly, SameSite=Strict cookie;
  only a SHA-256 hash of the token is stored. Sessions expire after 7 days, and
  signing in rotates the token and revokes other sessions.
- **CSRF**: authenticated mutations require an `X-CSRF-Token` header bound to the
  session. Cross-site origins and `Sec-Fetch-Site: cross-site` are rejected, and
  browser WebSockets check `Origin` too.
- **Brute force**: failed logins are throttled per IP, per username and globally
  with an exponential lockout (5 failures in 5 minutes starts at 5 s and doubles
  up to 15 minutes). Errors stay generic and `429` carries `Retry-After`.
- **Transport**: the cookie is marked `Secure` when the request is HTTPS, or when
  a trusted proxy says so via `X-Forwarded-Proto`. Use HTTPS (Caddy/TLS or a
  tunnel) whenever the dashboard is reachable beyond localhost.
- Interactive API docs (`/docs`, `/openapi.json`) are disabled, and responses
  carry CSP, frame, MIME-sniffing and referrer protections.

Binding beyond loopback is deliberately two-step: an owner must exist **and**
`ALLOW_REMOTE_BIND=true` must be set, otherwise startup refuses with a clear
error. A fresh install with no owner also answers `503` to non-loopback API
requests. `PUBLIC_BASE_URL` and `TRUSTED_PROXIES` make `X-Forwarded-*` safe
behind Caddy, Cloudflare Tunnel or Twingate.

Chats, provider keys and desktop access are installation-wide; accounts are not
tenant-isolated.

## BYOK providers

| Provider | ID | Default model | Sees screen? | Key env var |
| --- | --- | --- | --- | --- |
| Anthropic | `anthropic` | `claude-sonnet-4-5` | yes | `ANTHROPIC_API_KEY` |
| OpenAI | `openai` | `gpt-4o` | yes | `OPENAI_API_KEY` |
| Google Gemini | `gemini` | `gemini-2.5-flash` | yes | `GEMINI_API_KEY` |
| DeepSeek | `deepseek` | `deepseek-chat` | **no** | `DEEPSEEK_API_KEY` |
| Ollama | `ollama` | `qwen2.5vl:7b` | model-dependent | none |
| OpenAI-compatible | `openai-compatible` | your choice | configurable | `OPENAI_COMPATIBLE_API_KEY` |

Model ids are editable, so new models work without a code change. Use the
**Test** button in the UI to validate a key and list available models.

Sensible defaults:

- **Anthropic / OpenAI / Gemini**: best results; they can see the screenshot and
  call tools reliably.
- **Ollama**: `ollama pull qwen2.5vl:7b` (vision) or another tool-capable model.
- **DeepSeek**: text-only. It cannot see screenshots, so it is suitable for
  keyboard-driven goals only (open an app, type a known shortcut) and will ask
  you when it needs visual confirmation.
- **OpenAI-compatible**: set the endpoint, key and a vision-capable model. Turn
  off "vision" with `OPENAI_COMPATIBLE_VISION=false` for text-only gateways.

Keys saved in the web UI are encrypted in the local SQLite database. Keys are
resolved in this order: saved key → environment variable. Remove a stored key
from the UI at any time.

## CLI

```bash
open-computer-use serve                         # web app on 127.0.0.1:8765
open-computer-use doctor                        # screen access, platform, provider keys

python -m server.cli create-owner               # first run: create the owner
python -m server.cli reset-owner-password       # change it (revokes sessions)
python -m server.cli backup                     # database + Fernet key, together
python -m server.cli restore backups/ocu-backup-20260101-120000.tar.gz
python -m server.cli serve --host 0.0.0.0       # still needs ALLOW_REMOTE_BIND=true

open-computer-use run "Open the Settings app and enable dark mode" \
  --provider anthropic --model claude-sonnet-4-5

open-computer-use run "Type hello into the notes window" --dry-run        # simulated desktop
open-computer-use run "Sort my Downloads folder" --confirm --screenshots ./shots
```

Useful flags: `--confirm` (approve each action), `--max-steps`, `--max-cost`,
`--base-url`, `--api-key`, `--fast` (no human-like cursor animation).

## Docker

```bash
./deploy/generate-secrets.sh                    # Fernet key + session secret (mode 400)
docker compose run --rm -it server create-owner # no owner = the container refuses to start
docker compose up -d server                     # no published ports
docker compose --profile proxy up -d caddy      # the only published port (127.0.0.1:8080)
```

The server image is multi-stage, runs as uid 10001 on a read-only root filesystem
with `cap_drop: ALL` and `no-new-privileges`, and reads its secrets from mounted
files. The Xvfb/noVNC desktop is the separate `desktop` profile. Full details,
including backups, `TRUSTED_PROXIES` and `/ws/node` through Caddy, are in
[docs/docker.md](docs/docker.md).

## Safety model

- The server binds to `127.0.0.1` by default. Authentication is built in, but
  use HTTPS when exposing the dashboard; the included Caddyfile is a starting
  point and needs TLS for any non-local access.
- The owner account is created only with `python -m server.cli create-owner`;
  the web app has no signup. Non-loopback binds require both an owner and
  `ALLOW_REMOTE_BIND=true`, so a fresh install cannot be exposed by accident.
- All chats, provider keys and desktop access belong to the single owner
  account. Do not treat this as a multi-user service.
- **Confirm every action** makes the agent stop before each tool call and wait for
  Allow/Deny.
- The system prompt forbids typing passwords, payment details and 2FA codes; it
  asks the human instead. It also asks before destructive actions such as
  deleting files, sending messages or making purchases.
- Emergency stop: move the physical cursor into a screen corner. PyAutoGUI's
  failsafe aborts the run.
- Screen content is untrusted input. The prompt tells the model to ignore
  instructions found on screen (prompt-injection defence). Treat this as a
  mitigation, not a guarantee.
- Screenshots are sent to the model provider you choose. Use a local Ollama model
  if screen content must not leave the machine.
- Docker mode isolates the agent from your real desktop; use it when evaluating
  untrusted tasks or models.

## How it is built

```
server/
  app.py                     FastAPI server, REST + WebSocket, run manager
  config.py                  environment settings
  database/models.py         SQLite: accounts, sessions, runs, artifacts
  security/auth.py           scrypt hashing, sessions, CSRF, throttling
  security/crypto.py         Fernet vault for provider keys
  static/index.html          single-file dashboard (no build step)
  agent/
    open_computer_use/loop.py observe → model → tool → verify loop
    open_computer_use/cli.py  `open-computer-use` command
    computer/desktop.py      PyAutoGUI + mss real desktop driver
    computer/simulated.py    Pillow-rendered safe virtual desktop
    computer/actions.py      tool schemas + coordinate mapping
    providers/               provider-neutral tool-calling adapters
```

The provider layer normalises system/user/assistant/tool messages, images and
tool definitions, then serialises them to OpenAI Chat Completions, Anthropic
Messages, Gemini `generateContent`, and Ollama `/api/chat`. Adding a provider
means implementing `generate()` and optionally `list_models()`.

The loop captures a screenshot every step, sends only the newest image (older
screenshots are pruned to keep token costs down), enforces step/cost limits,
detects emergency stops, and streams every action over the WebSocket.

### API surface

- `GET /api/auth/status`, `POST /api/auth/login|logout` — owner session
- `GET /api/settings`, `PUT /api/settings/providers/{id}` — BYOK config
- `POST /api/providers/{id}/test` — validate a key and list models
- `POST /api/run`, `/api/run/pause`, `/api/run/resume`, `/api/run/stop`
- `POST /api/run/approve`, `/api/run/reply` — human-in-the-loop
- `GET /api/screen`, `GET /api/runs`, `WS /ws` — live view and history

Interactive API docs are disabled; authenticate with the dashboard session
before calling the API.

## Tests

```bash
PYTHONPATH=server .venv/bin/python -m pytest tests/test_open_computer_use_*.py -q
```

The suite covers provider serialization/tool parsing (no network), coordinate
mapping, authentication/session/CSRF behavior, the simulated desktop,
approvals/stop/ask-user, and a complete HTTP run against the simulated desktop. The older browser-agent acceptance tests remain
in `tests/test_m*.py`; they need a headed Chromium and the legacy browser stack.

## Limitations

- Linux desktop control uses X11; native Wayland is not supported.
- The desktop driver controls the primary monitor only.
- Vision-capable models are strongly recommended; DeepSeek and other text-only
  models cannot verify what is on screen.
- Lock screen / secure input fields cannot be automated.
- Long, multi-app workflows are still experimental. Use max steps, approval mode
  and the stop button.

## License

Use it, fork it, ship it. No warranty — an AI with mouse and keyboard access can
do real damage, so keep the safety rails on.
