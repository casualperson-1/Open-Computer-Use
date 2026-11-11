# Configuration reference

Every variable the server, the node and the compose stack read. Defaults are
the values in `server/config.py` / `node/config.py` unless stated otherwise.
Compose reads `.env` in the repository root; put values there or pass them in
the environment.

## Secrets (files, not variables)

| File | Used by | Notes |
| --- | --- | --- |
| `deploy/secrets/fernet.key` | server | Fernet key for provider keys and MCP secrets. Losing it makes stored credentials unreadable. |
| `deploy/secrets/secret_key` | server | 32-byte hex key for CSRF/session hashing; changing it signs everyone out. |
| `deploy/secrets/cf_tunnel_token` | cloudflared profile | Cloudflare Tunnel token. |
| `deploy/secrets/twingate_access_token` | twingate profile | Connector access token. |
| `deploy/secrets/twingate_refresh_token` | twingate profile | Connector refresh token. |

`deploy/generate-secrets.sh` creates the first two (and optional tunnel tokens
if `CF_TUNNEL_TOKEN` / `TWINGATE_*` are set once). Files are `0444` inside a
`0700` directory and are git-ignored.

## Server

| Variable | Default | Meaning |
| --- | --- | --- |
| `APP_ENV` | `production` | Label only; does not disable checks. |
| `HOST` | `127.0.0.1` | Bind address. Non-loopback requires an owner **and** `ALLOW_REMOTE_BIND=true`. |
| `PORT` | `8765` | Listen port. |
| `ALLOW_REMOTE_BIND` | `false` | Explicit opt-in for non-loopback binds. |
| `PUBLIC_BASE_URL` | unset | Exact public origin (`https://agent.example.com`). Used for Origin checks, Secure-cookie decisions and CLI hints. If unset, `PROXY_PORT` then `PORT` build the hint. |
| `TRUSTED_PROXIES` | empty | Comma-separated CIDRs whose `X-Forwarded-*` and `CF-Connecting-IP` headers are believed. Compose default: `172.28.0.0/24`. Never include public ranges. |
| `SECRET_KEY` | generated | Session/CSRF secret. Prefer `SECRET_KEY_FILE`. Changing it logs everyone out. |
| `SECRET_KEY_FILE` | unset | Mounted file with the secret (compose uses `/run/secrets/secret_key`). |
| `ENCRYPTION_KEY` | unset | Fernet key inline. Prefer `ENCRYPTION_KEY_FILE`. |
| `ENCRYPTION_KEY_FILE` | unset | Mounted Fernet key file (compose uses `/run/secrets/fernet_key`). |
| `DATA_DIR` | `./data` | SQLite database, `.fernet.key` fallback and browser profile. |
| `OPEN_COMPUTER_USE_DATA_DIR` | unset | Overrides `DATA_DIR` when set (used by tests/backup tooling). |
| `DATABASE_URL` | unset | Full SQLAlchemy URL; defaults to SQLite at `DATA_DIR/cowork.db`. |
| `BACKUP_DIR` | `./backups` | Where `server.cli backup` writes archives. |
| `LOGIN_MAX_FAILURES` | `5` | Failures per window before exponential lockout. |
| `LOGIN_WINDOW_SECONDS` | `300` | Throttle window. |
| `LOGIN_LOCKOUT_BASE_SECONDS` | `5` | First lockout duration. |
| `LOGIN_LOCKOUT_MAX_SECONDS` | `900` | Lockout cap. |
| `SESSION_TTL_HOURS` | `2160` | Session lifetime (90 days), renewed while the browser is used. |
| `SIGNUP_MODE` | `open` | `open` (anyone can register) or `closed`. A legacy `invite` value is treated as `open`. |
| `USER_RUN_LIMIT_PER_HOUR` | `30` | Per-member run limit; admin unlimited. Empty means 30. |
| `USER_USAGE_CAP_USD` | unset | Optional per-member daily spend cap; empty means no cap. |
| `REMOTE_NODE_MODE` | `required` | `required` (desktop runs need the user's node), `auto` (admin fallback to simulated), `never`. |
| `NODE_COMMAND_TIMEOUT` | `45` | Seconds to wait for a node command. |
| `NODE_ALLOWED_COMMANDS` / `NODE_DENIED_COMMANDS` | empty | Comma-separated glob allow/deny lists enforced server-side; deny wins. |
| `NODE_ALLOWED_DOMAINS` / `NODE_DENIED_DOMAINS` | empty | URL host allow/deny lists. |
| `NODE_ALLOWED_APPS` / `NODE_DENIED_APPS` | empty | App-id allow/deny lists. |
| `NODE_RATE_LIMIT` | `240` | Commands per minute per desktop run (0 disables). |
| `NODE_AUDIT` | `true` | Write `audit_events` rows for node commands. |
| `NODE_APPROVAL_MODE` | `off` | `off`, `risky` (ask for `NODE_APPROVAL_COMMANDS`) or `all`. |
| `NODE_APPROVAL_COMMANDS` | `open_item,open_url,browser_action,key_combo` | Commands that trigger a prompt in `risky` mode. |
| `NODE_APPROVAL_TIMEOUT` | `120` | Seconds before an approval prompt is refused. |
| `COMPUTER` | `desktop` | Default backend for admin/simulated runs; members never fall back. |
| `SCREEN_MAX_WIDTH` | `1280` | Screenshot downscale width. |
| `LIVE_VIEW_FPS` | `2` | Live screen frames per second while the panel is open (0.5-6). |
| `LIVE_VIEW_QUALITY` | `62` | JPEG quality for live frames (30-85). |
| `HUMAN_MOUSE` | `true` | Human-like cursor animation. |
| `MAX_STEPS` | `30` | Default per-run step cap. |
| `MAX_COST` | `1.0` | Default per-run spend cap (USD). |
| `APPROVAL_MODE` | `auto` | `auto` or `confirm` (approve every tool call). |
| `VNC_URL` | unset | Optional noVNC link shown by the legacy desktop profile. |
| `DEFAULT_PROVIDER` | `anthropic` | Provider when a chat has none. |
| `DEFAULT_MODEL` | empty | Model when a chat has none; provider default otherwise. |
| `MAX_TOKENS` | `4096` | Per-response token cap. |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint (compose uses `host.docker.internal`). |
| `OPENAI_BASE_URL` | unset | OpenAI endpoint override. |
| `OPENAI_COMPATIBLE_BASE_URL` | unset | Any OpenAI-compatible endpoint. |
| `OPENAI_COMPATIBLE_VISION` | `true` | Whether that endpoint accepts images. |
| `ANTHROPIC_BASE_URL`, `GEMINI_BASE_URL`, `DEEPSEEK_BASE_URL` | unset | Endpoint overrides. Use a full URL (for example `https://generativelanguage.googleapis.com/v1beta`); values without a scheme get https, and a bare word is ignored. |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, `OPENAI_COMPATIBLE_API_KEY` | unset | Optional environment fallback keys; UI-saved keys win. |
| `VIEWPORT_WIDTH`, `VIEWPORT_HEIGHT` | `1280`, `720` | Legacy browser loop viewport. |
| `BROWSER_HEADLESS`, `BROWSER_FAST_MODE`, `BROWSER_REAL_MOUSE` | `false` | Legacy browser flags. |
| `BROWSER_USER_DATA_DIR` | `./data/browser_profile` | Legacy/Playwright profile directory. |
| `PROXY_PORT` | `8765` in app, `8080` in compose | Used only for the sign-in hint when `PUBLIC_BASE_URL` is empty. |

The four `BROWSER_*` and `VIEWPORT_*` variables belong to the legacy browser
stack listed in `docs/dead-code.md`.

## Node (`node.config` / `node.cli`)

| Variable | Default | Meaning |
| --- | --- | --- |
| `OCC_NODE_SERVER` | none | Server URL (`https://…`, `wss://…` or a host). |
| `OCC_NODE_TOKEN` | none | Per-node token (or `--token` / `--token-file`). |
| `OCC_NODE_NAME` | hostname | Name in My computers. |
| `OCC_NODE_BACKEND` | `desktop` | `desktop` or `browser`. |
| `OCC_NODE_DATA_DIR` | `./node-data` | Config, audit, pid, browser profile. |
| `OCC_NODE_CONTROL_PORT` | `8791` | Loopback control page port. |
| `OCC_NODE_HEARTBEAT` | `15` | Seconds between heartbeats (server advertises 15). |
| `OCC_NODE_RECONNECT_MAX` | `60` | Max reconnect backoff seconds. |
| `OCC_NODE_MAX_WIDTH` | `1280` | Screenshot width. |
| `OCC_NODE_HUMAN_MOUSE` | `true` | Human-like cursor movement. |
| `OCC_NODE_BROWSER_PROFILE` | `<data>/browser_profile` | Playwright profile path. |
| `OCC_NODE_POLICY` | none | JSON policy file. |
| `OCC_NODE_INDICATOR` | `true` | Corner overlay while controlled. |
| `OCC_NODE_CF_ACCESS_CLIENT_ID` / `_FILE` | none | Cloudflare Access service-token client id (file or value). |
| `OCC_NODE_CF_ACCESS_CLIENT_SECRET` / `_FILE` | none | Access client secret. Both halves are required together. |
| `OCC_NODE_ALLOWED_COMMANDS`, `OCC_NODE_DENIED_COMMANDS` | empty | Node-side command lists. |
| `OCC_NODE_ALLOWED_DOMAINS`, `OCC_NODE_DENIED_DOMAINS` | empty | Node-side domain lists. |
| `OCC_NODE_ALLOWED_APPS`, `OCC_NODE_DENIED_APPS` | empty | Node-side app lists. |
| `OCC_NODE_RATE_LIMIT` | `120` | Node-side commands per minute. |
| `OCC_NODE_AUDIT` | `true` | Write `node-data/audit.jsonl`. |

## Compose (`.env`)

| Variable | Default | Meaning |
| --- | --- | --- |
| `PROXY_BIND` | `127.0.0.1` | Caddy published address. |
| `PROXY_PORT` | `8080` | Caddy published port. |
| `PUBLIC_BASE_URL` | empty | Passed to the server; see above. |
| `TRUSTED_PROXIES` | `172.28.0.0/24` | Must match the internal compose subnet. |
| `SIGNUP_MODE` | `open` | Passed to the server. |
| `REMOTE_NODE_MODE` | `required` | Passed to the server. |
| `USER_RUN_LIMIT_PER_HOUR` | `30` | Passed to the server. |
| `USER_USAGE_CAP_USD` | empty | Passed to the server. |
| `DEFAULT_PROVIDER`, `DEFAULT_MODEL` | `anthropic`, empty | Passed to the server/desktop profile. |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | Ollama from inside the container. |
| `BACKUP_INTERVAL` | `86400` | Backup sidecar sleep, seconds. |
| `TUNNEL_SIM_PORT` | `8090` | Loopback port of the test-only tunnel simulator. |
| `TWINGATE_NETWORK` | empty | Twingate network slug (not secret). |
| `TWINGATE_LOG_LEVEL` | `3` | Connector log level. |
| `DESKTOP_WEB_PORT`, `DESKTOP_VNC_PORT` | `8765`, `6080` | Legacy desktop profile loopback ports. |
