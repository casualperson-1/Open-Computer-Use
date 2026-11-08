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

In the web UI open **Nodes → Add node**, name it, and copy the token. It is
shown once; only its hash is stored on the server.

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

`python -m node.cli status` reports connection, backend and command count.

Stopping is sticky: the node refuses commands until it is restarted, so nothing
remote can undo a local kill. The server can also pause/resume/stop a node from
**Nodes** in the UI.

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

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `No DISPLAY` / desktop unavailable | Started outside a graphical session, or native Wayland. |
| Node exits immediately with "rejected this node's token" | Token rotated/revoked, or wrong server URL. Create a new token. |
| `The node control page is not reachable` | Node not running, or `--no-control-page` was used. |
| Screenshots are black under Xvfb | Missing `shm_size`/`--disable-gpu` in the container; use the demo profile. |
