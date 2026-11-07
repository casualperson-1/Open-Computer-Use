# Ubuntu / Debian Quickstart

### 1. Install Docker & Compose
```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
```

### 2. Run Browser Agent
```bash
cp .env.example .env
docker compose up -d
```
