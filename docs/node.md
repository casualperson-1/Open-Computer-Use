# Connect your computer

Open Computer Use never reaches into a machine from the server. Each person runs
a small node agent **on their own computer**; it dials out to the server, and
the agent can only control that one machine. Your chats, runs and provider keys
belong to your account, and your tasks only ever run on your own node.

The server's own desktop is off limits to members. If your account has no
computer connected, a desktop task stops with **“Connect your computer
first.”**

## 1. Add the computer to your account

Sign in, open **My computers** in the sidebar, and press **Add my computer**.
The page shows a token **once** together with the exact command. Copy both; the
token is stored only as a hash and cannot be shown again (rotate it if lost).

## 2. Install the node

Install this on the **computer you want the agent to control** — not on the
server. You need Python 3.10 or newer and a graphical login session.

```bash
python3 --version                     # must be 3.10 or newer
git clone https://github.com/casualperson-1/Open-Computer-Use.git
cd Open-Computer-Use
python3 -m venv .venv
.venv/bin/pip install -r server/requirements.txt
```

Run every command below from the repository root, so `python -m node.cli` can
import the shared protocol modules. On Windows use `.venv\Scripts\python` in
place of `.venv/bin/python`.

Link this computer with the token from **My computers**:

```bash
.venv/bin/python -m node.cli connect \
  --server https://agent.example.com \
  --token <token-from-my-computers>
```

`--server` is the origin you open in a browser. With a local Docker install
that is `http://127.0.0.1:8080` (the Caddy proxy); with the no-Docker install it
is `http://127.0.0.1:8765`. Another computer must use a hostname it can reach,
such as your LAN address or tunnel hostname.

`connect` writes `node-data/config.json` (mode 600), so `run` needs no
arguments afterwards. If you prefer environment variables, the same values work
as `OCC_NODE_SERVER` and `OCC_NODE_TOKEN`. Then start it:

```bash
.venv/bin/python -m node.cli run
```

Check it with `.venv/bin/python -m node.cli status`; the node appears as
**live** in My computers within a few seconds.

### Browser backend (optional)

If this node should drive a Playwright browser instead of the desktop, add
`--backend browser` to `connect` and install Chromium once:

```bash
.venv/bin/playwright install chromium
```

### Upgrading the node

```bash
git pull
.venv/bin/pip install -r server/requirements.txt
.venv/bin/python -m node.cli run
```

The token and `node-data/` survive an upgrade; re-run `connect` only if you
rotated the token.

### Start automatically at login

```bash
.venv/bin/python -m node.cli connect --server … --token … --auto-start
```

- **Linux** writes `~/.config/systemd/user/occ-node.service`, then prints the
  `systemctl --user enable --now occ-node` command. Logs:
  `journalctl --user -u occ-node -f`.
- **macOS** writes `~/Library/LaunchAgents/com.opencomputeruse.node.plist`, then
  prints `launchctl load -w …`.
- **Windows** prints the exact `schtasks` command; creating the task in Task
  Scheduler with “At log on” works the same way.

## 3. Platform notes and limits

### Linux

- **X11** works fully (mouse, keyboard, screenshots, app launching).
- **Wayland with XWayland**: X11 applications can be controlled. Native Wayland
  windows cannot be read or driven, and a focused native Wayland window can
  swallow global hotkeys. Use the control page or `python -m node.cli stop` as a
  reliable kill switch.
- **Wayland without XWayland** is not supported. Log into an X11/XWayland
  session, or run the optional virtual-desktop container on a machine you own.
- Global hotkeys need `pynput`; install `pip install pynput` if you want them.
- The indicator overlay needs a compositor that shows always-on-top windows.
- Arch Linux package list (X11, XFCE, Chromium libraries):
  [docs/install-arch.md](install-arch.md).

### macOS

- Grant **Screen Recording** and **Accessibility** permission to the terminal or
  Python binary the first time; without them screenshots are black and clicks
  do nothing.
- Screen Recording permission must be re-granted after some Python upgrades.
- The node must run inside your logged-in desktop session (not over SSH).

### Windows

- Run the node from your own interactive session, not as a service in session 0.
- Allow the app through SmartScreen/Defender the first time.
- UAC prompts cannot be clicked by the agent; approve them yourself.

## 4. Recommended safety setting

For personal installations set the server to ask before risky actions:

```bash
NODE_APPROVAL_MODE=risky
```

That prompts you in the chat before things like opening items, visiting a URL or
pressing key combinations. `NODE_APPROVAL_MODE=off` runs those without asking;
`all` asks for every command. The node also has its own independent allow lists
(`OCC_NODE_ALLOWED_COMMANDS`, `OCC_NODE_DENIED_DOMAINS`, …) that always win.

## 5. If the server is behind Cloudflare Access

A node connects to `/ws/node`, which may be protected by a Cloudflare Access
service token. Put the two values in files (mode 600) and point the node at
them; do not pass them on the command line:

```bash
install -d -m 700 ~/.config/occ
printf '%s' '<client-id>'     | install -m 600 /dev/stdin ~/.config/occ/access-id
printf '%s' '<client-secret>' | install -m 600 /dev/stdin ~/.config/occ/access-secret
export OCC_NODE_CF_ACCESS_CLIENT_ID_FILE=~/.config/occ/access-id
export OCC_NODE_CF_ACCESS_CLIENT_SECRET_FILE=~/.config/occ/access-secret
python -m node.cli run
```

If you installed auto-start with `connect --auto-start`, systemd/launchd do not
inherit those shell exports. Add the two paths (not the secret values) to the
unit, e.g. under `[Service]`:

```ini
Environment=OCC_NODE_CF_ACCESS_CLIENT_ID_FILE=%h/.config/occ/access-id
Environment=OCC_NODE_CF_ACCESS_CLIENT_SECRET_FILE=%h/.config/occ/access-secret
```

The node sends `CF-Access-Client-Id` / `CF-Access-Client-Secret` on the
handshake. They are never logged, never shown in `status`, and never written to
the audit file. The node token is still required inside the `hello` frame;
Access only decides whether the connection reaches the server. Both halves must
be set, and they are not saved by `connect` (use environment variables or files
each time). Many-user installations usually skip Access on the node hostname
and rely on the node token plus Cloudflare rate limiting instead — the
trade-off is explained in [remote-access.md](remote-access.md#2-cloudflare-access).

## 6. Rotate or revoke

In **My computers**:

- **Rotate** replaces the token and disconnects the node immediately. Re-run
  `connect` on that machine with the new token.
- **Revoke** kills the token permanently and disconnects it. Delete the
  `node-data` directory on that machine afterwards.

An administrator can disable or delete an account, which also drops every node
that account owns.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Node never appears live | `python -m node.cli status`; server URL must be reachable over `wss://` (or `ws://` on localhost) |
| “Connect your computer first” | The token belongs to a different account, or the node is offline |
| Screenshots are black (macOS) | Screen Recording permission |
| Clicks do nothing (macOS) | Accessibility permission |
| Hotkeys do nothing (Wayland) | Expected for native Wayland windows; use the control page |
| `401`/`unauthorized` on connect | Token was rotated or revoked; create a new one |
| `ModuleNotFoundError: shared` | Run the command from the repository root, not from `node/` |
| `Connect your computer first` right after starting | Wait a few seconds for the websocket; check `node.cli status` and that `--server` is the URL the server is reachable at |
| `/ws/node` closes with 4401 | Token belongs to another server or was revoked; create and connect a new one |
