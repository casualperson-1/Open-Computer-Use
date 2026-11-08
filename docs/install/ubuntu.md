# Ubuntu / Debian

## Local app (controls your real desktop)

Open Computr Use needs Python 3.10+ and an X11 (or XWayland) session.

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip python3-tk \
  xdotool libx11-6 libxtst6

git clone <your-repo> open-computr-use && cd open-computr-use
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
.venv/bin/pip install -e backend
.venv/bin/open-computr-use serve
```

Open <http://127.0.0.1:8765>, add a provider key, and start a task.

Check access at any time:

```bash
.venv/bin/open-computr-use doctor
```

- `DISPLAY` must point at a running X server. A Wayland-only session has no X11
  input path — use the Docker virtual desktop instead.
- Move the physical mouse into a screen corner for an emergency stop.
- Install optional OCR/clipboard helpers only if you need non-ASCII typing
  (`xclip` or `xsel` for `pyperclip`).

## Docker virtual desktop (safe from the host)

```bash
cp .env.example .env
docker compose up -d --build
```

- Open Computr Use: <http://127.0.0.1:8765>
- Virtual desktop / takeover: <http://127.0.0.1:6080/vnc.html>

Ports are bound to localhost. Add your keys to `.env` or paste them in the UI.
