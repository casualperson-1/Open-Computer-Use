# Fedora / RHEL Quickstart (Rootless Podman & SELinux)

### 1. Install Podman & Compose
```bash
sudo dnf install -y podman podman-compose
```

### 2. SELinux & Volume Sharing
SELinux requires `:Z` or `:z` volume flags for container write access.
When launching with podman-compose:
```bash
cp .env.example .env
podman-compose up -d
```
All persistent volumes in `docker-compose.yml` are configured for SELinux cgroup v2 compatibility.
