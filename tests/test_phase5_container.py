"""Phase 5: container assets stay hardened and secret files are read safely."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from config import Settings

ROOT = Path(__file__).resolve().parents[1]


def test_secret_key_can_be_loaded_from_a_mounted_file(tmp_path):
    path = tmp_path / "secret_key"
    path.write_text("a" * 64 + "\n", encoding="utf-8")
    settings = Settings(SECRET_KEY="", SECRET_KEY_FILE=str(path), _env_file=None)
    assert settings.SECRET_KEY == "a" * 64


def test_missing_or_short_secret_file_fails_loudly(tmp_path):
    with pytest.raises(ValidationError) as missing:
        Settings(SECRET_KEY="", SECRET_KEY_FILE=str(tmp_path / "nope"), _env_file=None)
    assert "does not exist" in str(missing.value)

    short = tmp_path / "short"
    short.write_text("too-short", encoding="utf-8")
    with pytest.raises(ValidationError) as bad:
        Settings(SECRET_KEY="", SECRET_KEY_FILE=str(short), _env_file=None)
    assert "at least 32 characters" in str(bad.value)


def test_explicit_secret_key_wins_over_the_file(tmp_path):
    path = tmp_path / "secret_key"
    path.write_text("b" * 64, encoding="utf-8")
    settings = Settings(SECRET_KEY="c" * 64, SECRET_KEY_FILE=str(path), _env_file=None)
    assert settings.SECRET_KEY == "c" * 64


def test_owner_status_command(tmp_path, monkeypatch):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import cli as server_cli
    from security.owner import create_owner, prepare_database

    url = f"sqlite+aiosqlite:///{tmp_path}/cli.db"
    monkeypatch.setattr(server_cli.settings, "DATABASE_URL", url)
    monkeypatch.setattr(server_cli.settings, "ENCRYPTION_KEY", None)
    monkeypatch.setattr(server_cli.settings, "ENCRYPTION_KEY_FILE", None)

    assert server_cli.main(["owner-status"]) == 1

    async def seed() -> None:
        engine = create_async_engine(url)
        try:
            await prepare_database(engine)
            await create_owner(
                async_sessionmaker(engine, expire_on_commit=False),
                "owner",
                "Correct-Horse-Battery-Staple-9",
            )
        finally:
            await engine.dispose()

    asyncio.run(seed())
    assert server_cli.main(["owner-status"]) == 0


def test_compose_hardens_the_server_and_publishes_nothing():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    server = services["server"]

    assert "ports" not in server
    assert server["restart"] == "unless-stopped"
    assert server["read_only"] is True
    assert server["cap_drop"] == ["ALL"]
    assert any("no-new-privileges" in item for item in server["security_opt"])
    assert server["mem_limit"] and server["cpus"] and server["pids_limit"]
    assert server["user"] == "10001:10001"

    env = server["environment"]
    assert env["HOST"] == "0.0.0.0"
    assert env["ALLOW_REMOTE_BIND"] == "true"
    assert env["ENCRYPTION_KEY_FILE"] == "/run/secrets/fernet_key"
    assert env["SECRET_KEY_FILE"] == "/run/secrets/secret_key"
    # No literal secrets in the compose file.
    assert "SECRET_KEY" not in env
    assert "ENCRYPTION_KEY" not in env
    assert set(server["secrets"]) == {"fernet_key", "secret_key"}
    assert set(server["volumes"]) == {"ocu-data:/data", "ocu-backups:/backups"}

    assert compose["networks"]["internal"]["internal"] is True

    # Caddy is the only published surface, loopback by default.
    caddy = services["caddy"]
    assert caddy["profiles"] == ["proxy"]
    assert len(caddy["ports"]) == 1
    assert "127.0.0.1" in caddy["ports"][0]

    # The Xvfb/noVNC stack is not part of the default profile.
    desktop = services["desktop"]
    assert desktop["profiles"] == ["desktop"]
    assert all(port.startswith("127.0.0.1") for port in desktop["ports"])


def test_secrets_are_file_based_and_gitignored():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    for name in ("fernet_key", "secret_key"):
        assert compose["secrets"][name]["file"].startswith("./deploy/secrets/")
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "deploy/secrets/" in ignored


def test_dockerfile_is_multi_stage_non_root_and_healthchecked():
    text = (ROOT / "server" / "Dockerfile").read_text(encoding="utf-8")
    assert "AS builder" in text and "AS runtime" in text
    assert "requirements.lock.txt" in text
    assert "USER 10001:10001" in text
    assert "HEALTHCHECK" in text and "/api/health" in text
    assert "--no-cache-dir" not in text or "PIP_NO_CACHE_DIR=1" in text


def test_dockerignore_keeps_state_and_secrets_out_of_builds():
    entries = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    for required in (
        ".git",
        "tests/",
        "data/",
        "node-data/",
        "backups/",
        "deploy/secrets/",
        ".env",
        "**/*.key",
    ):
        assert required in entries


def test_entrypoint_has_an_owner_guard_without_a_bypass():
    text = (ROOT / "docker" / "server-entrypoint.sh").read_text(encoding="utf-8")
    assert "owner-status" in text
    assert "docker compose run --rm -it server create-owner" in text
    assert "exit 78" in text
    # The wrapper must not relax the guard by setting these itself.
    assert "ALLOW_REMOTE_BIND=" not in text
