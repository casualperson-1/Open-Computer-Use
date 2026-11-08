# Arch Linux

## Local app (controls your real desktop)

```bash
sudo pacman -S --needed python tk xdotool libx11 libxtst
git clone <your-repo> cowork && cd cowork

python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
.venv/bin/pip install -e backend
.venv/bin/cowork serve
```

Open <http://127.0.0.1:8765>. `cowork doctor` reports screen access and provider
configuration.

PyAutoGUI needs X11. Use an Xorg session (or XWayland) — a native Wayland
session has no input path.

## Docker virtual desktop

```bash
cp .env.example .env
docker compose up -d --build
```

- Cowork: <http://127.0.0.1:8765>
- Virtual desktop: <http://127.0.0.1:6080/vnc.html>
