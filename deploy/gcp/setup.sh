#!/bin/sh
set -e

echo "=== Google Cloud e2-micro VM Setup for Browser Agent ==="

# 1. Detect package manager & install Docker if missing
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

# 2. Configure 4GB Swap file (Critical for e2-micro 1GB RAM)
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

# 3. Configure vm.swappiness and low-memory limits
sysctl vm.swappiness=60

# 4. Start compose stack with low-memory profile
echo "Starting stack in low-memory profile..."
docker compose --profile low-memory up -d

echo "=== Setup complete! Service is accessible on port 80 ==="
