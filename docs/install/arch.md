# Arch Linux Quickstart

### 1. Install Docker & Compose
```bash
sudo pacman -Syu docker docker-compose
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
newgrp docker
```

### 2. Run Browser Agent
```bash
cp .env.example .env
docker compose up -d
```

Access the dashboard at `http://localhost:6080` (or `http://localhost:8080`).
