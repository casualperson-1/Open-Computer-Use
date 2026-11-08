"""Server administration CLI.

    python -m server.cli create-owner
    python -m server.cli reset-owner-password
    python -m server.cli backup [--output backups/ocu-backup-....tar.gz]
    python -m server.cli restore backups/ocu-backup-....tar.gz
    python -m server.cli serve [--host 127.0.0.1] [--port 8765]
    python -m server.cli migrate

``create-owner`` prompts for the password with no echo. Automated installs can
pass it through ``--password-env`` or ``--password-file`` instead.

``backup`` and ``restore`` always handle the SQLite database and the Fernet key
together: a database restored without its key cannot read stored provider keys.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

SERVER_DIR = Path(__file__).resolve().parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))
REPO_ROOT = SERVER_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from config import settings  # noqa: E402
from database.migrations import apply_migrations  # noqa: E402
from security.crypto import vault  # noqa: E402
from security.network import BindRefused, assert_bind_allowed  # noqa: E402
from security.owner import (  # noqa: E402
    OwnerExistsError,
    OwnerMissingError,
    create_owner,
    prepare_database,
    set_owner_password,
)
from security.accounts import (  # noqa: E402
    create_invite,
    delete_user,
    list_users,
    owner_id,
    set_disabled,
)

MANIFEST_NAME = "manifest.json"
DB_NAME = "cowork.db"
KEY_NAME = "fernet.key"
KEY_FILE_MODE = 0o600


class CliError(RuntimeError):
    """A user-facing error: printed without a traceback."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _engine():
    return create_async_engine(settings.database_url, echo=False)


def _factory(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _key_file_path() -> Path:
    if settings.ENCRYPTION_KEY_FILE:
        return Path(settings.ENCRYPTION_KEY_FILE).expanduser()
    return settings.data_path / ".fernet.key"


def _read_password(args: argparse.Namespace, *, confirm: bool) -> str:
    """Password from an env var, a file, or an interactive no-echo prompt."""
    env_name = getattr(args, "password_env", None)
    if env_name:
        value = os.environ.get(env_name, "")
        if not value:
            raise CliError(f"Environment variable {env_name} is empty or not set.")
        return value
    password_file = getattr(args, "password_file", None)
    if password_file:
        path = Path(password_file).expanduser()
        if not path.exists():
            raise CliError(f"Password file {path} does not exist.")
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines or not lines[0]:
            raise CliError(f"Password file {path} is empty.")
        return lines[0]
    first = getpass.getpass("Owner password: ")
    if confirm:
        second = getpass.getpass("Repeat owner password: ")
        if first != second:
            raise CliError("Passwords do not match.")
    if not first:
        raise CliError("Password cannot be empty.")
    return first


def _dir_writable(directory: Path) -> bool:
    """Probe with a real write: os.access() ignores read-only filesystems."""
    probe = directory / ".ocu-write-probe"
    try:
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_snapshot(source: Path, target: Path) -> None:
    """Consistent copy of a live SQLite database (safe during writes)."""
    uri = f"file:{source}?mode=ro"
    with sqlite3.connect(uri, uri=True) as src, sqlite3.connect(target) as dst:
        src.backup(dst)


def _check_database_uses_key(db_path: Path, key: bytes) -> None:
    """Abort when the restored key cannot decrypt the restored provider keys."""
    from cryptography.fernet import Fernet, InvalidToken

    cipher = Fernet(key)
    checked = 0
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        for table, column in (
            ("provider_settings", "encrypted_key"),
            ("api_keys", "encrypted_key"),
        ):
            try:
                rows = conn.execute(
                    f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL"
                ).fetchall()
            except sqlite3.OperationalError:
                continue
            for (value,) in rows:
                if not value:
                    continue
                checked += 1
                try:
                    cipher.decrypt(str(value).encode("utf-8"))
                except (InvalidToken, ValueError) as exc:
                    raise CliError(
                        f"The backup's {KEY_NAME} cannot decrypt {table}.{column}. "
                        "The archive is inconsistent; refusing to restore."
                    ) from exc
    if checked:
        print(f"Verified {checked} stored secret(s) against the backup key.")


async def _migrate_file(path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        async with engine.begin() as conn:
            await apply_migrations(conn)
    finally:
        await engine.dispose()


async def _owner_present() -> bool:
    from security.owner import owner_exists

    engine = _engine()
    try:
        await prepare_database(engine)
        return await owner_exists(_factory(engine))
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _signin_url() -> str:
    """The address a person should actually open.

    Inside the compose stack the process port (8765) is not the published one,
    so PUBLIC_BASE_URL or PROXY_PORT wins over PORT.
    """
    configured = (settings.PUBLIC_BASE_URL or "").strip().rstrip("/")
    if configured:
        return configured + "/"
    port = os.environ.get("PROXY_PORT") or str(settings.PORT)
    return f"http://127.0.0.1:{port}/"


def command_create_owner(args: argparse.Namespace) -> int:
    password = _read_password(args, confirm=True)

    async def run() -> str:
        engine = _engine()
        try:
            await prepare_database(engine)
            user = await create_owner(_factory(engine), args.username, password)
            return user.username
        finally:
            await engine.dispose()

    try:
        username = asyncio.run(run())
    except (OwnerExistsError, OwnerMissingError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Owner account {username!r} created.")
    print(f"Sign in at {_signin_url()} (password not stored anywhere).")
    print("Add members from the Admin panel in the UI, or with `create-invite`.")
    asyncio.run(_claim_after_owner())
    return 0


def command_reset_password(args: argparse.Namespace) -> int:
    password = _read_password(args, confirm=True)

    async def run() -> str:
        engine = _engine()
        try:
            await prepare_database(engine)
            user = await set_owner_password(_factory(engine), password, args.username)
            return user.username
        finally:
            await engine.dispose()

    try:
        username = asyncio.run(run())
    except (OwnerExistsError, OwnerMissingError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Password for {username!r} changed. All existing sessions were signed out.")
    return 0


def command_backup(args: argparse.Namespace) -> int:
    db_path = settings.database_file
    if db_path is None or not db_path.exists():
        raise CliError("backup supports the SQLite database only (no database file found).")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = (
        Path(args.output).expanduser()
        if args.output
        else settings.backup_path / f"ocu-backup-{stamp}.tar.gz"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    started = datetime.utcnow().isoformat(timespec="seconds")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        _sqlite_snapshot(db_path, tmp_dir / DB_NAME)
        key_path = tmp_dir / KEY_NAME
        key_path.write_bytes(vault.key_bytes)
        os.chmod(key_path, KEY_FILE_MODE)
        manifest: Dict[str, Any] = {
            "format": 1,
            "created_at": started,
            "source_database": str(db_path),
            "database": DB_NAME,
            "key": KEY_NAME,
            "database_sha256": _sha256(tmp_dir / DB_NAME),
            "key_sha256": _sha256(key_path),
            "key_source": vault.key_source,
        }
        (tmp_dir / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        with tarfile.open(output, "w:gz") as archive:
            for name in (DB_NAME, KEY_NAME, MANIFEST_NAME):
                archive.add(tmp_dir / name, arcname=name)

    try:
        os.chmod(output, KEY_FILE_MODE)
    except OSError:
        pass
    print(f"Backup written to {output} (database + Fernet key).")
    print("Store it somewhere safe: it contains provider API keys.")
    return 0


def command_restore(args: argparse.Namespace) -> int:
    db_path = settings.database_file
    if db_path is None:
        raise CliError("restore supports the SQLite database only.")
    archive_path = Path(args.input).expanduser()
    if not archive_path.exists():
        raise CliError(f"Backup archive {archive_path} does not exist.")

    wanted = {DB_NAME, KEY_NAME, MANIFEST_NAME}
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive.getmembers():
                name = Path(member.name).name
                if name not in wanted or not member.isfile():
                    continue
                source = archive.extractfile(member)
                if source is None:
                    continue
                (tmp_dir / name).write_bytes(source.read())
        for name in wanted:
            if not (tmp_dir / name).exists():
                raise CliError(f"Backup is missing {name}; refusing to restore.")
        manifest = json.loads((tmp_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
        for name, field in ((DB_NAME, "database_sha256"), (KEY_NAME, "key_sha256")):
            expected = str(manifest.get(field) or "")
            if expected and _sha256(tmp_dir / name) != expected:
                raise CliError(f"Checksum mismatch for {name}; archive is corrupted.")
        key_bytes = (tmp_dir / KEY_NAME).read_bytes().strip()
        try:
            from cryptography.fernet import Fernet

            Fernet(key_bytes)
        except Exception as exc:  # noqa: BLE001 - any parse failure means bad key
            raise CliError(f"Backup {KEY_NAME} is not a valid Fernet key.") from exc
        _check_database_uses_key(tmp_dir / DB_NAME, key_bytes)

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        key_target = _key_file_path()
        # In a container the key is usually a read-only mounted secret. Restoring
        # the database is still safe when that key already matches the archive;
        # refusing early avoids swapping in a database the live key cannot read.
        key_writable = _dir_writable(key_target.parent) and (
            not key_target.exists() or os.access(key_target, os.W_OK)
        )
        existing_key = b""
        if key_target.exists():
            try:
                existing_key = key_target.read_bytes().strip()
            except OSError:
                existing_key = b""
        if not key_writable and existing_key != key_bytes:
            raise CliError(
                f"{key_target} is read-only and does not match this backup. "
                "Replace it with the archive's fernet.key (mode 444) and run restore "
                "again; the database was not changed."
            )

        db_path.parent.mkdir(parents=True, exist_ok=True)
        if db_path.exists():
            safety = db_path.with_name(f"{db_path.name}.pre-restore-{stamp}")
            shutil.copy2(db_path, safety)
            print(f"Current database saved to {safety}")
        if key_writable and key_target.exists():
            safety_key = key_target.with_name(f"{key_target.name}.pre-restore-{stamp}")
            shutil.copy2(key_target, safety_key)
            os.chmod(safety_key, KEY_FILE_MODE)
        staged_db = db_path.with_name(f"{db_path.name}.restore-{stamp}")
        shutil.copy2(tmp_dir / DB_NAME, staged_db)
        os.replace(staged_db, db_path)
        if key_writable:
            key_target.parent.mkdir(parents=True, exist_ok=True)
            key_target.write_bytes(key_bytes)
            os.chmod(key_target, KEY_FILE_MODE)
        else:
            print(
                f"{key_target} is mounted read-only and already matches this backup; "
                "left unchanged."
            )

    asyncio.run(_migrate_file(db_path))
    if settings.ENCRYPTION_KEY:
        print(
            "warning: ENCRYPTION_KEY is set in the environment and overrides the "
            "restored key file. Unset it or restore that key instead.",
            file=sys.stderr,
        )
    print(f"Restored {db_path} and {_key_file_path()} from {archive_path}.")
    return 0


def command_owner_status(_: argparse.Namespace) -> int:
    """Exit 0 when an owner exists, 1 otherwise (used by the container entrypoint)."""
    present = asyncio.run(_owner_present())
    print("owner configured" if present else "no owner configured")
    return 0 if present else 1


async def _claim_after_owner() -> None:
    """Hand single-user-era rows to the account that now exists."""
    from security.accounts import claim_orphan_rows

    engine = _engine()
    try:
        await claim_orphan_rows(_factory(engine))
    finally:
        await engine.dispose()


def command_create_invite(args: argparse.Namespace) -> int:
    async def run():
        engine = _engine()
        try:
            await prepare_database(engine)
            factory = _factory(engine)
            creator = await owner_id(factory)
            if creator is None:
                raise CliError(
                    "No owner account exists yet. Run `python -m server.cli create-owner` first."
                )
            return await create_invite(
                factory,
                creator,
                ttl_hours=int(args.expires_hours),
                label=args.label or "",
            )
        finally:
            await engine.dispose()

    row, code = asyncio.run(run())
    link = f"{_signin_url()}?invite={code}"
    print(f"Invite {row.id} created (expires {row.expires_at:%Y-%m-%d %H:%M} UTC).")
    print(f"Code: {code}")
    print(f"Link: {link}")
    print("Single use: it stops working after the first signup or when it expires.")
    return 0


def command_list_users(_: argparse.Namespace) -> int:
    async def run():
        engine = _engine()
        try:
            await prepare_database(engine)
            return await list_users(_factory(engine))
        finally:
            await engine.dispose()

    users = asyncio.run(run())
    if not users:
        print("No accounts yet. Run `python -m server.cli create-owner` first.")
        return 0
    for user in users:
        status = "disabled" if user.disabled_at else "active"
        print(
            f"{user.username:24s} {user.role:6s} {status:9s} "
            f"runs/hour={user.run_limit_per_hour} cap={user.usage_cap_usd or '-'}"
        )
    return 0


def command_set_user_enabled(args: argparse.Namespace, disabled: bool) -> int:
    async def run() -> Optional[str]:
        from sqlalchemy import select

        from database.models import User

        engine = _engine()
        try:
            await prepare_database(engine)
            factory = _factory(engine)
            async with factory() as session:
                user = (
                    await session.execute(
                        select(User).where(User.username == args.username.strip().lower())
                    )
                ).scalars().first()
                if user is None:
                    raise CliError(f"No account named {args.username!r}.")
                if user.is_admin and disabled:
                    raise CliError("Administrator accounts cannot be disabled.")
                await set_disabled(factory, user.id, disabled)
                return user.username
        finally:
            await engine.dispose()

    try:
        username = asyncio.run(run())
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Account {username!r} {'disabled' if disabled else 'enabled'}.")
    return 0


def command_disable_user(args: argparse.Namespace) -> int:
    return command_set_user_enabled(args, True)


def command_enable_user(args: argparse.Namespace) -> int:
    return command_set_user_enabled(args, False)


def command_delete_user(args: argparse.Namespace) -> int:
    async def run() -> str:
        from sqlalchemy import select

        from database.models import User

        engine = _engine()
        try:
            await prepare_database(engine)
            factory = _factory(engine)
            async with factory() as session:
                user = (
                    await session.execute(
                        select(User).where(User.username == args.username.strip().lower())
                    )
                ).scalars().first()
                if user is None:
                    raise CliError(f"No account named {args.username!r}.")
                if user.is_admin:
                    raise CliError("Administrator accounts cannot be deleted.")
                user_id = user.id
            await delete_user(factory, user_id)
            return user.username
        finally:
            await engine.dispose()

    try:
        username = asyncio.run(run())
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Account {username!r} and its data were deleted.")
    return 0


def command_migrate(_: argparse.Namespace) -> int:
    db_path = settings.database_file
    if db_path is None:
        raise CliError("migrate supports the SQLite database only.")
    asyncio.run(_migrate_file(db_path))
    print(f"Migrations are up to date for {db_path}.")
    return 0


def command_serve(args: argparse.Namespace) -> int:
    host = args.host or settings.HOST
    port = args.port or settings.PORT
    try:
        assert_bind_allowed(
            host,
            owner_exists=asyncio.run(_owner_present()),
            allow_remote_bind=settings.ALLOW_REMOTE_BIND,
        )
    except BindRefused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if str(SERVER_DIR) not in sys.path:
        sys.path.insert(0, str(SERVER_DIR))
    import uvicorn

    print(f"Serving on http://{host}:{port}/")
    uvicorn.run("app:app", host=host, port=port, log_level="info")
    return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m server.cli",
        description="Owner accounts, backups and server startup for Open Computer Use.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-owner", help="create the single owner account")
    create.add_argument("--username", default="owner")
    create.add_argument("--password-env", metavar="VAR", help="read the password from $VAR")
    create.add_argument("--password-file", metavar="PATH", help="read the password from a file")
    create.set_defaults(func=command_create_owner)

    reset = sub.add_parser("reset-owner-password", help="change the owner password")
    reset.add_argument("--username", default=None)
    reset.add_argument("--password-env", metavar="VAR")
    reset.add_argument("--password-file", metavar="PATH")
    reset.set_defaults(func=command_reset_password)

    backup = sub.add_parser("backup", help="archive the database and Fernet key together")
    backup.add_argument("--output", metavar="PATH", default=None)
    backup.set_defaults(func=command_backup)

    restore = sub.add_parser("restore", help="restore a database and its Fernet key")
    restore.add_argument("input", metavar="ARCHIVE")
    restore.set_defaults(func=command_restore)

    serve = sub.add_parser("serve", help="run the server with the bind safety checks")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.set_defaults(func=command_serve)

    migrate = sub.add_parser("migrate", help="apply pending database migrations")
    migrate.set_defaults(func=command_migrate)

    status = sub.add_parser("owner-status", help="exit 0 when an owner exists")
    status.set_defaults(func=command_owner_status)

    invite = sub.add_parser("create-invite", help="create a single-use signup invite")
    invite.add_argument("--expires-hours", type=int, default=72)
    invite.add_argument("--label", default="", help="who or what this invite is for")
    invite.set_defaults(func=command_create_invite)

    users = sub.add_parser("list-users", help="list accounts")
    users.set_defaults(func=command_list_users)

    for name, func, text in (
        ("disable-user", command_disable_user, "disable an account and sign it out"),
        ("enable-user", command_enable_user, "re-enable a disabled account"),
        ("delete-user", command_delete_user, "delete an account and all its data"),
    ):
        user_parser = sub.add_parser(name, help=text)
        user_parser.add_argument("username")
        user_parser.set_defaults(func=func)
    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("aborted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
