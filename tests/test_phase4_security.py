"""Phase 4: owner auth, hashing, lockout, proxy trust, migrations, backups."""

import asyncio
import base64
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app as app_module
from config import settings
from database.migrations import apply_migrations
from database.models import AuthSession, Base, ProviderSetting, User
from security import auth as auth_module
from security.crypto import EncryptionConfigError, EncryptionVault, startup_check, vault
from security.network import (
    BindRefused,
    assert_bind_allowed,
    origin_allowed,
    parse_networks,
    resolve_client_ip,
    resolve_secure,
)
from security.owner import (
    OwnerExistsError,
    create_owner,
    owner_exists,
    prepare_database,
    set_owner_password,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_DB = REPO_ROOT / "data" / "cowork.db"
PASSWORD = "Correct-Horse-Battery-Staple-9"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _scrypt_hash(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=auth_module.SCRYPT_N,
        r=auth_module.SCRYPT_R,
        p=auth_module.SCRYPT_P,
        dklen=auth_module.SCRYPT_DKLEN,
        maxmem=auth_module.SCRYPT_MAXMEM,
    )
    return (
        f"scrypt${auth_module.SCRYPT_N}${auth_module.SCRYPT_R}"
        f"${auth_module.SCRYPT_P}${_b64(salt)}${_b64(digest)}"
    )


def _counts(path: Path) -> dict:
    with sqlite3.connect(path) as conn:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {
            table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in tables
        }


def _make_archive(path: Path, db_bytes: bytes, key_bytes: bytes) -> None:
    """A backup archive with correct manifest checksums."""
    manifest = {
        "format": 1,
        "database": "cowork.db",
        "key": "fernet.key",
        "database_sha256": hashlib.sha256(db_bytes).hexdigest(),
        "key_sha256": hashlib.sha256(key_bytes).hexdigest(),
    }
    payload = (
        ("cowork.db", db_bytes),
        ("fernet.key", key_bytes),
        ("manifest.json", (json.dumps(manifest) + "\n").encode()),
    )
    with tarfile.open(path, "w:gz") as tar:
        for name, data in payload:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------


def test_passwords_are_argon2id():
    digest = auth_module.hash_password(PASSWORD)
    assert digest.startswith("$argon2id$")
    assert auth_module.verify_password(PASSWORD, digest)
    assert not auth_module.verify_password(PASSWORD + "x", digest)
    assert auth_module.needs_rehash(digest) is False
    assert auth_module.needs_rehash("scrypt$1$2$3$abc$def") is True


def test_legacy_scrypt_hashes_still_verify_and_are_upgraded():
    legacy = _scrypt_hash(PASSWORD)
    assert auth_module.verify_password(PASSWORD, legacy)
    assert not auth_module.verify_password("wrong-password-123", legacy)
    assert auth_module.needs_rehash(legacy) is True
    # The login path rehashes with argon2id on the next sign-in.
    assert auth_module.hash_password(PASSWORD).startswith("$argon2id$")


def test_password_policy_rejects_short_and_username_passwords():
    with pytest.raises(ValueError):
        auth_module.validate_password("short", "owner")
    with pytest.raises(ValueError):
        auth_module.validate_password("owner-is-my-password", "owner")
    with pytest.raises(ValueError):
        auth_module.normalize_username("Not Valid!")


# ---------------------------------------------------------------------------
# Throttling
# ---------------------------------------------------------------------------


def test_login_throttle_locks_with_exponential_backoff():
    throttle = auth_module.LoginThrottle(limit=3, window=60, base_lock=5, max_lock=60)
    assert throttle.locked_for("ip:1.2.3.4") == 0
    assert [throttle.fail("ip:1.2.3.4") for _ in range(3)] == [0, 0, 5]
    assert 0 < throttle.locked_for("ip:1.2.3.4") <= 5
    assert throttle.fail("ip:1.2.3.4") == 10  # doubles
    assert throttle.fail("ip:1.2.3.4") == 20
    assert throttle.fail("ip:1.2.3.4") == 40
    assert throttle.fail("ip:1.2.3.4") == 60  # capped
    throttle.reset("ip:1.2.3.4")
    assert throttle.locked_for("ip:1.2.3.4") == 0


def test_login_throttle_keys_are_independent():
    throttle = auth_module.LoginThrottle(limit=2, window=60, base_lock=5, max_lock=5)
    throttle.fail("user:alice", "ip:203.0.113.1")
    throttle.fail("user:alice", "ip:203.0.113.1")
    assert throttle.locked_for("user:alice") > 0
    assert throttle.locked_for("ip:203.0.113.1") > 0
    assert throttle.locked_for("user:bob") == 0


def test_owner_gate_blocks_remote_requests_when_no_owner(monkeypatch):
    async def no_owner(refresh: bool = False) -> bool:
        return False

    monkeypatch.setattr(app_module, "_owner_exists", no_owner)

    def request(peer):
        from starlette.requests import Request

        return Request(
            {
                "type": "http",
                "method": "GET",
                "scheme": "http",
                "path": "/api/settings",
                "headers": [],
                "client": (peer, 1234),
            }
        )

    remote = request("203.0.113.9")
    assert asyncio.run(app_module._owner_gate_blocks(remote, "/api/settings")) is True
    # Health and status stay reachable so the page can explain the situation.
    assert asyncio.run(app_module._owner_gate_blocks(remote, "/api/health")) is False
    assert asyncio.run(app_module._owner_gate_blocks(remote, "/api/auth/status")) is False
    # Loopback is allowed to prepare the install.
    assert asyncio.run(app_module._owner_gate_blocks(request("127.0.0.1"), "/api/settings")) is False


def test_signup_route_is_mode_gated():
    """Signup is open by default; the operator can require invites or close it."""
    paths = {getattr(route, "path", "") for route in app_module.app.routes}
    assert "/api/auth/signup" in paths
    assert "/api/auth/login" in paths
    assert settings.SIGNUP_MODE in ("invite", "open", "closed")
    assert app_module._signup_mode() == "open"


# ---------------------------------------------------------------------------
# Proxy awareness and bind guard
# ---------------------------------------------------------------------------


def test_forwarded_headers_require_a_trusted_proxy():
    assert resolve_client_ip("203.0.113.5", "127.0.0.1", parse_networks("")) == "203.0.113.5"
    trusted = parse_networks("127.0.0.1/32,::1/128")
    assert resolve_client_ip("127.0.0.1", "198.51.100.7", trusted) == "198.51.100.7"
    assert (
        resolve_client_ip("127.0.0.1", "1.2.3.4, 198.51.100.7", trusted)
        == "198.51.100.7"
    )
    assert resolve_client_ip("127.0.0.1", "127.0.0.1", trusted) == "127.0.0.1"


def test_https_detection_only_believes_trusted_proxies():
    trusted = parse_networks("127.0.0.1/32")
    assert resolve_secure("http", "https", "127.0.0.1", trusted) is True
    assert resolve_secure("http", "https", "203.0.113.5", trusted) is False
    assert resolve_secure("https", None, "203.0.113.5", trusted) is True


def test_origin_check_accepts_the_public_base_url():
    assert origin_allowed(None, "agent.example.com", "") is True
    assert origin_allowed("https://agent.example.com", "agent.example.com", "") is True
    assert origin_allowed("https://evil.example", "agent.example.com", "") is False
    assert (
        origin_allowed(
            "https://agent.example.com", "127.0.0.1:8765", "https://agent.example.com"
        )
        is True
    )


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10"])
def test_non_loopback_bind_requires_owner_and_explicit_opt_in(host):
    with pytest.raises(BindRefused) as no_owner:
        assert_bind_allowed(host, owner_exists=False, allow_remote_bind=True)
    assert "create-owner" in str(no_owner.value)
    with pytest.raises(BindRefused) as not_opted_in:
        assert_bind_allowed(host, owner_exists=True, allow_remote_bind=False)
    assert "ALLOW_REMOTE_BIND" in str(not_opted_in.value)
    assert_bind_allowed(host, owner_exists=True, allow_remote_bind=True)
    assert_bind_allowed("127.0.0.1", owner_exists=False, allow_remote_bind=False)


def _cli_env(tmp_path: Path) -> dict:
    env = dict(os.environ)
    env.update(
        {
            "OPEN_COMPUTER_USE_DATA_DIR": str(tmp_path),
            "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path}/fresh.db",
            "SECRET_KEY": "test-secret-key-" + "0" * 32,
        }
    )
    env.pop("ENCRYPTION_KEY", None)
    env.pop("ENCRYPTION_KEY_FILE", None)
    return env


def test_fresh_install_without_owner_cannot_be_bound_off_loopback(tmp_path):
    env = _cli_env(tmp_path)
    env.update({"HOST": "0.0.0.0", "ALLOW_REMOTE_BIND": "true"})
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app:app",
            "--app-dir",
            str(REPO_ROOT / "server"),
            "--host",
            "127.0.0.1",
            "--port",
            "8871",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    output = (process.stdout + process.stderr).lower()
    assert process.returncode != 0, process.stdout
    assert "no owner account" in output
    assert "create-owner" in output


def test_cli_create_owner_and_serve_guard(tmp_path):
    env = _cli_env(tmp_path)
    env["OCU_TEST_PASSWORD"] = PASSWORD
    create = subprocess.run(
        [
            sys.executable,
            "-m",
            "server.cli",
            "create-owner",
            "--password-env",
            "OCU_TEST_PASSWORD",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert create.returncode == 0, create.stderr
    assert "'owner' created" in create.stdout

    duplicate = subprocess.run(
        [
            sys.executable,
            "-m",
            "server.cli",
            "create-owner",
            "--password-env",
            "OCU_TEST_PASSWORD",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert duplicate.returncode == 1
    assert "already exists" in duplicate.stderr

    env.pop("ALLOW_REMOTE_BIND", None)
    serve = subprocess.run(
        [sys.executable, "-m", "server.cli", "serve", "--host", "0.0.0.0"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert serve.returncode == 2
    assert "ALLOW_REMOTE_BIND" in serve.stderr


# ---------------------------------------------------------------------------
# Owner lifecycle
# ---------------------------------------------------------------------------


def test_owner_is_single_and_password_reset_revokes_sessions(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/owner.db"
    engine = create_async_engine(url)

    async def scenario() -> None:
        await prepare_database(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        assert await owner_exists(factory) is False
        user = await create_owner(factory, "owner", PASSWORD)
        assert user.username == "owner"
        assert await owner_exists(factory) is True
        with pytest.raises(OwnerExistsError):
            await create_owner(factory, "second", PASSWORD)

        async with factory() as session:
            session.add(
                AuthSession(
                    id="stale-session",
                    user_id=user.id,
                    expires_at=datetime.utcnow() + timedelta(hours=1),
                )
            )
            await session.commit()

        await set_owner_password(factory, "Another-Long-Passphrase-42")
        async with factory() as session:
            assert (await session.execute(select(AuthSession))).scalars().all() == []
            row = (await session.execute(select(User))).scalars().one()
            assert auth_module.verify_password(
                "Another-Long-Passphrase-42", row.password_hash
            )
            assert not auth_module.verify_password(PASSWORD, row.password_hash)

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(engine.dispose())


# ---------------------------------------------------------------------------
# Migrations against a copy of the real database
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not REAL_DB.exists(), reason="no real database to copy")
def test_migration_preserves_a_copy_of_the_real_database(tmp_path):
    copy = tmp_path / "cowork.db"
    shutil.copy2(REAL_DB, copy)
    before = _counts(copy)

    engine = create_async_engine(f"sqlite+aiosqlite:///{copy}")

    async def run():
        async with engine.begin() as conn:
            first = await apply_migrations(conn)
        async with engine.begin() as conn:
            second = await apply_migrations(conn)
        return first, second

    try:
        first, second = asyncio.run(run())
    finally:
        asyncio.run(engine.dispose())

    # The copy may come from a database that is already migrated; either way the
    # rows must survive and a second pass must be a no-op.
    assert second == []
    assert set(first) <= {"owner-auth-tables", "multiuser-accounts"}
    after = _counts(copy)
    for table, count in before.items():
        assert after.get(table) == count, f"{table} lost rows during migration"
    assert after["schema_migrations"] == 2
    with sqlite3.connect(copy) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(provider_settings)")
        }
        assert "user_id" in columns
        keys = [row[1] for row in conn.execute("PRAGMA table_info(provider_settings)") if row[5]]
        assert keys == ["user_id", "provider"]
        assert conn.execute(
            "SELECT COUNT(*) FROM provider_settings WHERE user_id = 0"
        ).fetchone()[0] == before.get("provider_settings", 0)


# ---------------------------------------------------------------------------
# Encryption key handling and backups
# ---------------------------------------------------------------------------


def test_missing_key_file_fails_loudly(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", None)
    monkeypatch.setattr(settings, "ENCRYPTION_KEY_FILE", str(tmp_path / "missing.key"))
    with pytest.raises(EncryptionConfigError) as excinfo:
        EncryptionVault()
    assert "does not exist" in str(excinfo.value)


def test_malformed_encryption_key_fails_loudly(monkeypatch):
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", "not-a-fernet-key")
    with pytest.raises(EncryptionConfigError):
        EncryptionVault()


def test_startup_check_rejects_secrets_from_another_key(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/vault.db"
    engine = create_async_engine(url)

    async def scenario() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        foreign = Fernet(Fernet.generate_key()).encrypt(b"sk-foreign").decode()
        async with factory() as session:
            session.add(ProviderSetting(provider="openai", encrypted_key=foreign))
            session.add(
                ProviderSetting(provider="anthropic", encrypted_key=vault.encrypt("sk-mine"))
            )
            await session.commit()
        with pytest.raises(Exception) as excinfo:
            await startup_check(factory)
        assert "cannot decrypt" in str(excinfo.value)

        async with factory() as session:
            await session.execute(
                delete(ProviderSetting).where(ProviderSetting.provider == "openai")
            )
            await session.commit()
        await startup_check(factory)  # our own key still decrypts its rows

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(engine.dispose())


def test_backup_and_restore_keep_database_and_key_together(tmp_path, monkeypatch):
    import cli as server_cli

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db = data_dir / "cowork.db"
    url = f"sqlite+aiosqlite:///{db}"
    monkeypatch.setenv("OPEN_COMPUTER_USE_DATA_DIR", str(data_dir))
    monkeypatch.setattr(server_cli.settings, "DATABASE_URL", url)
    monkeypatch.setattr(server_cli.settings, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(server_cli.settings, "ENCRYPTION_KEY_FILE", None)
    monkeypatch.setattr(server_cli.settings, "ENCRYPTION_KEY", None)

    async def seed() -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                session.add(User(username="owner", password_hash="argon2-placeholder"))
                session.add(
                    ProviderSetting(
                        provider="anthropic",
                        encrypted_key=server_cli.vault.encrypt("sk-live-test"),
                    )
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(seed())
    archive = tmp_path / "backup.tar.gz"
    assert server_cli.main(["backup", "--output", str(archive)]) == 0
    assert archive.exists()

    key_file = data_dir / ".fernet.key"
    db.unlink()
    if key_file.exists():
        key_file.unlink()
    assert server_cli.main(["restore", str(archive)]) == 0

    assert db.exists() and key_file.exists()
    assert key_file.read_bytes().strip() == vault.key_bytes
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM provider_settings").fetchone()[0] == 1
        stored = conn.execute(
            "SELECT encrypted_key FROM provider_settings WHERE provider='anthropic'"
        ).fetchone()[0]
    assert Fernet(vault.key_bytes).decrypt(stored.encode()) == b"sk-live-test"


def test_restore_refuses_a_mismatched_key(tmp_path, monkeypatch):
    import cli as server_cli

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db = data_dir / "cowork.db"
    url = f"sqlite+aiosqlite:///{db}"
    monkeypatch.setenv("OPEN_COMPUTER_USE_DATA_DIR", str(data_dir))
    monkeypatch.setattr(server_cli.settings, "DATABASE_URL", url)
    monkeypatch.setattr(server_cli.settings, "ENCRYPTION_KEY_FILE", None)
    monkeypatch.setattr(server_cli.settings, "ENCRYPTION_KEY", None)

    foreign = Fernet(Fernet.generate_key())
    async def seed() -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                session.add(User(username="owner", password_hash="argon2-placeholder"))
                session.add(
                    ProviderSetting(
                        provider="openai",
                        encrypted_key=foreign.encrypt(b"sk-from-other-install").decode(),
                    )
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(seed())
    before = _counts(db)
    archive = tmp_path / "mismatched.tar.gz"
    # Valid Fernet key, but not the key that encrypted the provider secret.
    wrong_key = Fernet.generate_key()
    _make_archive(archive, db.read_bytes(), wrong_key)

    assert server_cli.main(["restore", str(archive)]) == 1
    assert _counts(db) == before  # the live database was not replaced
