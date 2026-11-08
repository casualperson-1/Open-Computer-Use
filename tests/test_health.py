"""Operational assets: health checks, log rotation, pinned base images."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

import app as app_module

ROOT = Path(__file__).resolve().parents[1]


def test_health_reports_database_and_key_status_without_secrets():
    with TestClient(app_module.app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["encryption_key"] in {"ok", "missing"}
    serialised = json.dumps(body).lower()
    for leak in ("fernet", "secret", "password", ".key", "sqlite", "traceback"):
        assert leak not in serialised


def test_health_degrades_without_leaking_the_database_error(monkeypatch):
    class BrokenSession:
        async def __aenter__(self):
            raise RuntimeError("database is on fire at /data/cowork.db")

        async def __aexit__(self, *exc_info):
            return False

    with TestClient(app_module.app) as client:
        monkeypatch.setattr(app_module, "SessionLocal", BrokenSession)
        response = client.get("/api/health")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["database"] == "error"
    assert "on fire" not in response.text
    assert "/data" not in response.text


def test_compose_rotates_logs_and_pins_local_base_images():
    import yaml

    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    for name, service in compose["services"].items():
        logging = service.get("logging")
        assert logging, f"{name} has no log rotation"
        assert logging["driver"] == "json-file"
        assert logging["options"]["max-size"]
        assert logging["options"]["max-file"]

    for name in ("caddy", "tunnel-sim"):
        assert "@sha256:" in compose["services"][name]["image"], name

    server_dockerfile = (ROOT / "server" / "Dockerfile").read_text(encoding="utf-8")
    assert server_dockerfile.count("python:3.12-slim@sha256:") == 2
