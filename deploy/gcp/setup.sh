#!/bin/sh
set -e

echo "=== Open Computer Use virtual desktop setup (GCP e2-micro friendly) ==="

# 1. Install Docker if it is missing
if ! command -v docker >/dev/null 2>&1; then
    echo "Installing Docker..."
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update && apt-get install -y docker.io docker-compose-v2 curl
        systemctl enable --now docker
    elif command -v pacman >/dev/null 2>&1; then
        pacman -Sy --noconfirm docker docker-compose
        systemctl enable --now docker
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y docker docker-compose-plugin
        systemctl enable --now docker
    else
        echo "Unsupported package manager. Please install Docker manually." >&2
        exit 1
    fi
fi

# 2. Create swap (the virtual desktop + Chromium are memory hungry on 1 GB)
if [ ! -f /swapfile ]; then
    echo "Creating 4GB swapfile..."
    fallocate -l 4G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=4096
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "Swap created successfully."
else
    echo "Swapfile already present."
fi

sysctl vm.swappiness=60

# 3. Start the Open Computer Use stack (dashboard + virtual desktop)
echo "Starting Open Computer Use..."
docker compose up -d --build

echo "=== Setup complete ==="
echo "Open Computer Use dashboard: http://<instance-ip>:8765"
echo "Virtual desktop:  http://<instance-ip>:6080/vnc.html"
echo "Note: ports are bound to 127.0.0.1 in docker-compose.yml; use an SSH"
echo "tunnel or a reverse proxy with authentication. Do not expose noVNC"
echo "without protection."
