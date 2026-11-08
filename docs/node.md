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

You need Python 3.10 or newer on the computer being controlled, plus a graphical
session for desktop control.

```bash
# one-time: get the code and its dependencies
git clone https://github.com/your-org/open-computer-use.git
cd open-computer-use
python3 -m venv .venv
.venv/bin/pip install -r server/requirements.txt

# link this computer (paste the token from My computers)
.venv/bin/python -m node.cli connect \
  --server https://agent.example.com \
  --token <token-from-my-computers>

# start it
.venv/bin/python -m node.cli run
```

`connect` writes `node-data/config.json` (mode 600), so `run` needs no
arguments afterwards. If you prefer environment variables, the same values work
as `OCC_NODE_SERVER` and `OCC_NODE_TOKEN`.

Check it with `python -m node.cli status`; the node appears as **live** in My
computers within a few seconds.

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

## 5. Rotate or revoke

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
