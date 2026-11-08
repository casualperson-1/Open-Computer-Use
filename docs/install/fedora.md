# Fedora

## Local app (controls your real desktop)

```bash
sudo dnf install -y python3 python3-pip python3-tkinter xdotool libX11 libXtst
git clone <your-repo> open-computer-use && cd open-computer-use

python3 -m venv .venv
.venv/bin/pip install -r server/requirements.txt
.venv/bin/pip install -e server
.venv/bin/open-computer-use serve
```

Open <http://127.0.0.1:8765>. Run `.venv/bin/open-computer-use doctor` to verify
display access.

Open Computer Use uses X11 input. On a Wayland-only Fedora desktop, log into an X11 session
or use the Docker virtual desktop.

## Docker virtual desktop

```bash
cp .env.example .env
docker compose up -d --build
```

- Open Computer Use: <http://127.0.0.1:8765>
- Virtual desktop: <http://127.0.0.1:6080/vnc.html>
