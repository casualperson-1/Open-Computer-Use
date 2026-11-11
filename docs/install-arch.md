# Arch Linux: server and XFCE desktop packages

Two machines are involved, and they need different things:

| Machine | Needs |
| --- | --- |
| Server (or the same VM if you run both) | Python 3.10+, the app, a public HTTPS URL |
| Desktop VM with XFCE | X11 session, Python, the node agent, desktop/browser libraries |

Both roles install the same `server/requirements.txt`; the pacman packages below
are the system libraries pip cannot provide.

## 1. Base packages (both roles)

```bash
sudo pacman -Syu --needed base-devel git python python-pip tk
```

`tk` matters: PyAutoGUI's import path needs it, so without it `pip install
pyautogui` succeeds but importing the driver aborts.

## 2. Desktop VM (XFCE, X11)

If the VM does not have a desktop yet:

```bash
sudo pacman -S --needed xorg-server xorg-xinit xfce4 xfce4-goodies \
  lightdm lightdm-gtk-greeter
sudo systemctl enable --now lightdm
```

Libraries used by the desktop driver and the browser backend:

```bash
# input, clipboard, screenshots (PyAutoGUI, mss, pyperclip)
sudo pacman -S --needed xclip scrot libx11 libxext libxrender libxtst libxi

# Chromium libraries for `playwright install chromium`
sudo pacman -S --needed nss atk at-spi2-atk cups libdrm libxkbcommon \
  libxcomposite libxdamage libxrandr mesa alsa-lib pango cairo libxshmfence \
  ttf-liberation
```

Log into the **Xorg** XFCE session (not Wayland). Then, as the logged-in user,
from a terminal inside that session:

```bash
git clone https://github.com/casualperson-1/Open-Computer-Use.git
cd Open-Computer-Use
python3 -m venv .venv
.venv/bin/pip install -r server/requirements.txt
.venv/bin/playwright install chromium          # only for --backend browser

.venv/bin/python -m node.cli connect \
  --server https://<machine>.<tailnet>.ts.net \
  --token <token-from-my-computers> \
  --auto-start
.venv/bin/python -m node.cli run
```

Over SSH into the desktop session, export the display first:

```bash
export DISPLAY=:0
export XAUTHORITY="$HOME/.Xauthority"
```

Run the node as the logged-in user, never as root, and not from a TTY without a
session. `--auto-start` installs a systemd user unit; details in
[docs/node.md](node.md#start-automatically-at-login). Native Wayland windows
cannot be driven — see [docs/node.md](node.md#linux).

## 3. Server on Arch

Without Docker:

```bash
git clone https://github.com/casualperson-1/Open-Computer-Use.git
cd Open-Computer-Use
python3 -m venv .venv
.venv/bin/pip install -r server/requirements.txt
.venv/bin/pip install -e server

.venv/bin/python -m server.cli create-owner
.venv/bin/python -m server.cli serve          # http://127.0.0.1:8765
```

Settings come from `.env` in the repository root (see
[docs/configuration.md](configuration.md)):

```dotenv
SIGNUP_MODE=open
PUBLIC_BASE_URL=https://<machine>.<tailnet>.ts.net
TRUSTED_PROXIES=127.0.0.1/32
```

With Docker instead:

```bash
sudo pacman -S --needed docker docker-compose
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"      # log out and back in

./deploy/generate-secrets.sh
docker compose run --rm -it server create-owner
docker compose --profile proxy up -d
```

## 4. Tailscale Funnel

```bash
sudo pacman -S --needed tailscale
sudo systemctl enable --now tailscaled
sudo tailscale up

# Point https://<machine>.<tailnet>.ts.net at the app:
sudo tailscale funnel --bg 8765      # no-Docker server
sudo tailscale funnel --bg 8080      # with the compose proxy

sudo tailscale funnel status
```

Funnel needs MagicDNS, HTTPS certificates for the tailnet and the `funnel` node
attribute; the first `tailscale funnel` run walks you through enabling them.
Only ports 443, 8443 and 10000 are exposed publicly, so leave the app on
`8765`/`8080` and let Funnel terminate TLS. Turn it off with
`sudo tailscale funnel reset`.

Funnel proxies from loopback and sets `X-Forwarded-Proto: https`, so
`TRUSTED_PROXIES=127.0.0.1/32` makes session cookies `Secure`, enables HSTS and
buckets login throttling per real client. Do not also publish Caddy's port.

Funnel is on the public internet: with `SIGNUP_MODE=open` (the default) anyone
with the URL can register. Each account still needs its own provider key and has
its own run limit; use `SIGNUP_MODE=invite` if you want to approve people.

## 5. MCP runtime (optional, server side)

MCP **stdio** servers are children of the server process, so their runtime must
exist where the server runs:

```bash
sudo pacman -S --needed nodejs npm     # npx-based servers
sudo pacman -S --needed uv             # uvx-based servers
```

Inside the Docker image those runtimes are absent; use HTTP transport instead.
The desktop node never needs MCP. Full guide: [docs/mcp.md](mcp.md).

## Prompt: install and configure Open Computer Use

Paste this into an agent with shell access to the Arch machine. Fill in the
placeholders first.

```text
Install and configure Open Computer Use on this Arch Linux machine.

First decide from the machine's state whether it is:
- the server (runs the web app and is reachable from the internet),
- a desktop node (XFCE VM the agent will drive), or both.

Rules:
- Use pacman for system packages and a project-local .venv for Python; never
  pip install into the system Python (Arch blocks it with PEP 668).
- Use https://github.com/casualperson-1/Open-Computer-Use.git.
- Ask before rebooting, enabling a display manager, changing firewall rules or
  enabling Tailscale Funnel.
- Never print tokens or passwords; read them from files or prompt the user.

Server steps:
1. pacman -Syu --needed base-devel git python python-pip tk
2. Clone the repository, create .venv, run
   `.venv/bin/pip install -r server/requirements.txt` and
   `.venv/bin/pip install -e server`.
3. Write .env with SIGNUP_MODE=open,
   PUBLIC_BASE_URL=https://<machine>.<tailnet>.ts.net and
   TRUSTED_PROXIES=127.0.0.1/32. Create the owner interactively with
   `.venv/bin/python -m server.cli create-owner`.
4. Install tailscale, run `tailscale up`, then
   `tailscale funnel --bg 8765` for the venv install (or use the compose proxy
   and funnel 8080).
5. Verify GET /api/health returns 200, the sign-up page loads over the https
   URL, and a browser WebSocket connects. Report the public URL.

Desktop-node steps (XFCE VM):
1. pacman -Syu --needed base-devel git python python-pip tk xfce4
   xfce4-goodies lightdm lightdm-gtk-greeter xclip scrot libx11 libxext
   libxrender libxtst libxi nss atk at-spi2-atk cups libdrm libxkbcommon
   libxcomposite libxdamage libxrandr mesa alsa-lib pango cairo libxshmfence
   ttf-liberation
2. Clone the repository, create .venv, pip install -r server/requirements.txt,
   and run `.venv/bin/playwright install chromium` if the browser backend is
   wanted.
3. Enrol with `.venv/bin/python -m node.cli connect --server <public https URL>
   --token <token> --auto-start`, then run `.venv/bin/python -m node.cli run`
   inside the Xorg XFCE session (over SSH: DISPLAY=:0,
   XAUTHORITY=$HOME/.Xauthority).
4. Confirm the node shows live in My computers and run one screenshot task.

Check your work after each step, keep a short log of commands, and stop with a
report if a step fails instead of guessing.
```
