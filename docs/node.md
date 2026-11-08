# Run a desktop node

A node is the machine the agent actually controls: it moves the real mouse,
types on the real keyboard, takes screenshots and drives the Playwright browser
profile. It connects **out** to the server, so it needs no inbound port.

## Requirements

- **Linux session: X11 or XWayland.** Native Wayland without XWayland is not
  supported, because the drivers use X11 APIs.
  - Check with `echo $XDG_SESSION_TYPE` and `echo $DISPLAY`.
  - A Wayland session with XWayland reports `XDG_SESSION_TYPE=wayland` **and**
    a `DISPLAY` value (for example `:0` or `:1`). That works.
  - Wayland with no `DISPLAY` does not. Log into an X11/XWayland session, or
    use the optional virtual-desktop container.
- Python packages from `server/requirements.txt` (`pyautogui`, `mss`,
  `pyperclip`, `pillow`, `websockets`) plus `playwright` for browser mode.
- Screenshot/input permission on macOS is out of scope for now; the node is
  documented for Linux.

## 1. Create a token on the server

Node enrolment has an API but no dashboard panel yet. From a signed-in browser
session:

```bash
# the CSRF token comes from /api/auth/status
CSRF=$(curl -s -b cookies.txt http://localhost:8765/api/auth/status \
  | python -c 'import json,sys; print(json.load(sys.stdin)["csrf_token"])')

curl -s -b cookies.txt -X POST http://localhost:8765/api/nodes \
  -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF" \
  -d '{"name":"desk"}'
# {"node": {...}, "token": "<shown once>"}
```

The token is shown **once**; only its hash is stored. Other endpoints:
`GET /api/nodes` (list, plus live connection state), `POST /api/nodes/{id}/rotate`
(new token, drops the live socket), `DELETE /api/nodes/{id}` (revoke),
`POST /api/nodes/{id}/pause|resume|stop` (remote control).

## 2. Start the node

From the repository root on the machine being controlled:

```bash
.venv/bin/python -m node.cli run \
  --server https://agent.example.com \
  --token-file ~/.config/occ/node.token
```

or with environment variables:

```bash
export OCC_NODE_SERVER=https://agent.example.com
export OCC_NODE_TOKEN=...        # or use --token-file
export OCC_NODE_NAME=desk
.venv/bin/python -m node.cli run
```

Check the machine first with:

```bash
.venv/bin/python -m node.cli doctor --server https://agent.example.com
```

Useful flags: `--backend browser` (Playwright tab instead of the desktop),
`--headless`, `--data-dir`, `--control-port`, `--no-control-page`,
`--no-hotkeys`.

State (control token, browser profile, node id) lives in `./node-data` by
default and is ignored by git.

## 3. Watch and stop it

The node prints a loopback control page URL when it starts:

```
http://127.0.0.1:8791/?token=…
```

Three independent kill switches:

| Method | How |
| --- | --- |
| Control page | Pause, resume or stop from the printed loopback URL. |
| CLI | `python -m node.cli stop` (uses the control token, falls back to `SIGTERM` on the pid file). |
| Hotkeys | `Ctrl+Alt+Shift+Q` stop, `Ctrl+Alt+Shift+P` pause/resume (requires `pynput`). |

**Under Wayland the hotkeys are best effort**: a focused native Wayland window can
swallow global hotkeys, so treat the CLI and the control page as the reliable
kill paths and the hotkey as a convenience. `python -m node.cli doctor` says so
explicitly when it detects a Wayland session.

`python -m node.cli status` reports connection, backend and command count.

Stopping is sticky: the node refuses commands until it is restarted, so nothing
remote can undo a local kill. The server can also pause/resume/stop a node over
the socket via `POST /api/nodes/{id}/pause|resume|stop`.

## Policy: what this machine allows

The node is the hard boundary: it decides what may run no matter what the
server asks. Configure it with `--policy`, `OCC_NODE_POLICY` or environment
variables.

```json
{
  "allowed_commands": ["mouse_move", "click", "scroll", "type", "screenshot", "open_url"],
  "denied_domains": ["*.bank.example"],
  "allowed_apps": ["firefox", "org.gnome.Nautilus"],
  "rate_limit_per_minute": 120,
  "audit": true
}
```

```bash
python -m node.cli run --policy ~/.config/occ/policy.json --server https://agent.example.com
# or, without a file
OCC_NODE_ALLOWED_APPS=firefox OCC_NODE_RATE_LIMIT=60 python -m node.cli run ...
```

Rules:

- An empty allow list allows everything of that kind; a deny entry always wins.
- `describe`, `pause`, `resume` and `stop` are always accepted, so the kill
  switch cannot be locked out by policy.
- App rules are globs matched against the app id **and** its display name.
- Host rules are globs; `*.example.com` also matches `example.com` itself.
- Environment variables override the file.

Available variables: `OCC_NODE_ALLOWED_COMMANDS`, `OCC_NODE_DENIED_COMMANDS`,
`OCC_NODE_ALLOWED_DOMAINS`, `OCC_NODE_DENIED_DOMAINS`, `OCC_NODE_ALLOWED_APPS`,
`OCC_NODE_DENIED_APPS`, `OCC_NODE_RATE_LIMIT`, `OCC_NODE_AUDIT`.

The node only ever tells the server an app's `app_id` and display name.
Desktop entries, launch commands and file paths stay on this machine, and the
allowlist decides which apps are even listed.

## Audit log

Every command is appended to `node-data/audit.jsonl` (mode `600`, rotated to
`.1` at 5 MB) as one JSON object: the command, its redacted arguments, the
outcome (`ok`, `denied`, `failed`), the error code and the duration. Typed text
is never stored — only its length — and URL query strings are dropped.

Read it locally:

```bash
curl -H "X-Node-Token: $(cat node-data/control.token)" \
  'http://127.0.0.1:8791/api/audit?limit=50'
```

The server keeps its own audit trail (`GET /api/audit`), including node
enrolment, token rotation/revocation and connection changes.

## On-screen indicator

While the node runs it draws a small always-on-top strip in the top-right
corner: green `Agent controlling this machine` when connected, amber when
paused, red when stopped, grey while idle. State changes also raise a desktop
notification when `notify-send` exists.

Turn it off with `--no-indicator` or `OCC_NODE_INDICATOR=false`. Without Tk or a
display the node still runs; the indicator is never required for control.

## 4. Keep it running (systemd user unit)

```ini
# ~/.config/systemd/user/occ-node.service
[Unit]
Description=Open Computer Use node
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory=%h/browser-agent
Environment=OCC_NODE_SERVER=https://agent.example.com
Environment=OCC_NODE_NAME=desk
Environment=OCC_NODE_DATA_DIR=%h/.local/share/occ-node
ExecStart=%h/browser-agent/.venv/bin/python -m node.cli run --token-file %h/.config/occ/node.token
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload && systemctl --user enable --now occ-node
```

The process must run **inside** your graphical session so `DISPLAY` is set.
Enable lingering only if you understand that the node then runs without a
logged-in desktop, where screenshots will fail.

## Browser mode

```bash
python -m node.cli run --backend browser --server https://agent.example.com
```

The node keeps a persistent Chromium profile in
`node-data/browser_profile` (git-ignored). This profile is what lets the agent
stay logged into sites, and it lives on the node — never on the server.

On a Wayland desktop the node launches Chromium with `--ozone-platform=x11`, so
the window is an XWayland client that PyAutoGUI and `mss` can see and click. A
native Wayland Chromium window would be invisible to the screenshot and input
tools. `python -m node.cli doctor --backend browser` reports which mode will be
used and whether Playwright's Chromium is installed.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `No DISPLAY` / desktop unavailable | Started outside a graphical session, or native Wayland. |
| Node exits immediately with "rejected this node's token" | Token rotated/revoked, or wrong server URL. Create a new token. |
| `The node control page is not reachable` | Node not running, or `--no-control-page` was used. |
| Screenshots are black under Xvfb | Missing `shm_size`/`--disable-gpu` in the container; use the demo profile. |
| Hotkey does nothing | Wayland focus; use `python -m node.cli stop` or the control page. |
| An installed app is missing from `list_apps` | `allowed_apps` on the node excludes it, or it is a `NoDisplay` entry. |
| `not allowed by this node's policy` | The node's own allowlist refused the command; the server's policy is a second, separate layer. |
