# Architecture: server and desktop node

Open Computer Use runs as two parts that talk over one outbound WebSocket.

```
        trusted network / tunnel                    your desk
┌───────────────────────────────┐        ┌───────────────────────────────┐
│ server                        │        │ node                          │
│  • web UI, auth, sessions     │  wss   │  • mouse, keyboard, screens   │
│  • LLM providers, agent loop  │◀───────│  • Playwright browser profile │
│  • SQLite state, MCP servers  │  only  │  • installed-app launching    │
│  • node registry + hub        │  this  │  • loopback control page      │
└───────────────────────────────┘  way   └───────────────────────────────┘
```

The arrow matters: **the node dials the server**. The server never opens a
connection to the node, so the controlled machine needs no inbound port, no
port forwarding and no public DNS name. It works behind NAT.

## Repository layout

| Path | Purpose |
| --- | --- |
| `server/` | FastAPI app: UI, auth, run manager, providers, MCP, node registry and hub. |
| `node/` | Desktop node agent: WebSocket client, drivers, safety state, control page. |
| `shared/` | Protocol models, the `Computer` abstraction and Bezier math used by both sides. |
| `deploy/` | Reverse-proxy and host setup files. |
| `docs/` | Architecture, node setup and install guides. |
| `browser/` | Optional Xvfb + noVNC virtual desktop (demo/CI). |

## Wire protocol (`shared/protocol.py`)

Every frame is JSON with a `type`. Protocol version `1.0` uses a major-version
compatibility check at handshake time.

1. Node connects to `wss://<server>/ws/node`.
2. Node sends `hello` with its token, name, platform, backend and capabilities.
3. Server verifies the token hash, registers the connection and replies
   `welcome` (or an `error` frame plus close code `4401`).
4. Server sends `command` frames with a request id; the node answers with a
   matching `result` frame (`ok`, `data`, `error`, `error_code`, `duration_ms`).
5. Both sides exchange `heartbeat` / `heartbeat_ack`. A node that misses
   heartbeats for 60 s is dropped.

Commands (`CommandName`): `mouse_move`, `click`, `drag`, `scroll`, `type`,
`key_combo`, `screenshot`, `open_url`, `browser_action`, `get_page_text`,
`list_apps`, `open_item`, `describe`, `pause`, `resume`, `stop`.

`list_apps` answers with `{app_id, name}` only, and `open_item` accepts an
`app_id` that the node resolves to a desktop entry locally. Launch commands and
file paths never cross the wire; an id the node does not know is rejected with
`unknown_app` rather than guessed at.

Each command has a pydantic parameter model, so both sides reject bad input
before anything touches the mouse or keyboard: coordinates must be finite and
in range, text is length-capped, key combos must be printable ASCII, and
`open_url` only accepts `http`/`https` URLs without embedded credentials.

### Failure handling

- **Timeouts** – every command carries a timeout; the server additionally
  bounds the wait and raises `NodeTimeout`.
- **Reconnect** – the node retries with exponential backoff (1 s doubling to a
  60 s cap, ±20 % jitter). A rejected token is *not* retried: the node exits
  with a clear message so it cannot hammer the server.
- **Disconnects** – pending commands fail with `NodeUnavailable` instead of
  hanging, and the UI receives a `node_status` event.

## Policy, approval and audit

Two independent layers, so a mistake in one is not enough to drive a machine:

| Layer | Code | Enforces |
| --- | --- | --- |
| Node policy | `node/policy.py` | Command allow/deny, host allow/deny, app allow/deny, per-minute rate limit. Cannot be overridden by the server. |
| Server policy | `server/security/policy.py` | The same command/host/app lists, a per-run rate limit, and approval prompts. |

Both use `shared/policy.py` for matching, so a rule means the same thing on
either side. Empty allow lists allow everything of that kind; deny entries win;
and the node control commands are always accepted so a policy cannot lock out
the kill switch.

### Approval

`NODE_APPROVAL_MODE` decides whether the owner is asked before a command is
sent:

| Value | Meaning |
| --- | --- |
| `off` (default) | Never ask. |
| `risky` | Ask for `NODE_APPROVAL_COMMANDS` (default: `open_item`, `open_url`, `browser_action`, `key_combo`). |
| `all` | Ask before every command. |

A prompt appears in the run's event stream as `approval_request` with
`source: "node"`, and is answered through the same approve endpoint as a tool
call. It times out (default 120 s) to a refusal. A run already in `confirm` mode
asks per tool call, so the extra prompt is skipped to avoid asking twice, and
the ad-hoc `/api/screen` preview has no run to ask through — restrict those
paths with the allowlists instead.

### Audit

- **Node**: `node-data/audit.jsonl`, one JSON object per command, readable via
  the loopback control page (`/api/audit`) and rotated at 5 MB.
- **Server**: the `audit_events` table, read with `GET /api/audit`, covering
  node commands plus enrolment, rotation, revocation and connect/disconnect.

Both record a redacted summary from `describe_command`: typed text becomes a
character count, URL query strings and fragments are dropped. Neither log is a
keylogger.

## Authentication

- Node tokens are 32 random bytes, shown **once** when created via
  `POST /api/nodes` or **My computers** in the dashboard.
- The server stores only `sha256(token)`; a database leak cannot be replayed.
- Rotating a token replaces the hash and drops the live socket; revoking marks
  the row unusable and drops the socket.
- Tokens are independent of the owner session: revoking a node does not affect
  the browser session, and logging out does not disconnect nodes.

The web UI has multi-user accounts: the owner is the administrator, members
sign up (open by default) or join by invite, and every row and node is scoped by `user_id`. Password
hashes are argon2id, session ids are hashed in `HttpOnly`, `SameSite=Strict`
cookies, and unsafe requests need a CSRF token.

## Node behaviour

- **Drivers** live in `node/drivers/`: `desktop.py` (PyAutoGUI + mss, smooth
  Bezier mouse movement), `browser.py` (Playwright persistent profile) and
  `apps.py` (freedesktop app discovery/launching).
- **Safety state** (`node/safety.py`) is shared by the socket, the control page
  and the CLI. `stop` is sticky until the process restarts, so a remote command
  cannot undo a local kill.
- **Control page** binds to `127.0.0.1` only, requires a random per-run token,
  and sets a strict CSP. The token is stored in `node-data/control.token`
  (mode `600`) so `python -m node.cli stop` works without the page.
- **Hotkeys** are optional (`pynput`): `Ctrl+Alt+Shift+Q` stops the node,
  `Ctrl+Alt+Shift+P` pauses/resumes.

Server modes for a desktop run (`REMOTE_NODE_MODE`):

| Value | Behaviour |
| --- | --- |
| `required` (default) | Refuse to start a desktop run when the user's own node is not connected. |
| `auto` | Use the connected node; the administrator may fall back to the simulated desktop. |
| `never` | Always use the simulated desktop (administrator only). |

## State and backups

- SQLite database: `<data dir>/cowork.db`.
- Fernet key used to encrypt provider keys and MCP secrets:
  `<data dir>/.fernet.key` (or `ENCRYPTION_KEY`).

**Back them up together.** A database without its Fernet key cannot decrypt the
saved provider credentials, and a key without the database is useless. Neither
file belongs in git or in an image build context.
