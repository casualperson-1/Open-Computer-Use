"""Small, ordered, idempotent schema migrations.

The app used to rely on ``Base.metadata.create_all`` alone, which adds tables
but never changes existing ones. Migrations here are additive-only and tracked
twice: in a ``schema_migrations`` table (for auditing) and in SQLite's
``PRAGMA user_version`` (for a cheap check).

A copy of a real database is used in tests to prove the existing rows and
tables survive.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Awaitable, Callable, List, Sequence, Tuple

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2

# (version, name, apply(conn))
Migration = Tuple[int, str, Callable[[Any], Awaitable[None]]]

# Tables that only need a plain user_id column.
USER_ID_TABLES = (
    "scheduled_tasks",
    "task_runs",
    "deals",
    "nodes",
    "audit_events",
    "mcp_servers",
    "cowork_chats",
    "cowork_runs",
    "chat_artifacts",
)


async def _table_columns(conn: Any, table: str) -> List[str]:
    try:
        result = await conn.exec_driver_sql(f"PRAGMA table_info({table})")
        return [row[1] for row in result.fetchall()]
    except Exception:  # pragma: no cover - non-SQLite backends
        return []


async def _pk_columns(conn: Any, table: str) -> List[str]:
    try:
        result = await conn.exec_driver_sql(f"PRAGMA table_info({table})")
        rows = [row for row in result.fetchall() if int(row[5] or 0) > 0]
        return [row[1] for row in sorted(rows, key=lambda row: int(row[5]))]
    except Exception:  # pragma: no cover - non-SQLite backends
        return []


async def _table_exists(conn: Any, table: str) -> bool:
    result = await conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return result.first() is not None


async def _ensure_column(conn: Any, table: str, column: str, ddl: str) -> bool:
    """Add ``column`` to ``table`` when it is missing. Returns True if added."""
    if not await _table_exists(conn, table):
        return False
    columns = await _table_columns(conn, table)
    if not columns or column in columns:
        return False
    try:
        await conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        return True
    except Exception:  # pragma: no cover - racing migration or read-only DB
        logger.warning("Could not add %s.%s", table, column, exc_info=True)
        return False


async def _rebuild_table(
    conn: Any, table: str, ddl: str, columns: Sequence[str]
) -> None:
    """Recreate ``table`` with a new primary key, copying the listed columns."""
    if not await _table_exists(conn, table):
        return
    temporary = f"{table}__multiuser"
    await conn.exec_driver_sql(f"DROP TABLE IF EXISTS {temporary}")
    await conn.exec_driver_sql(ddl.replace("{table}", temporary))
    existing = set(await _table_columns(conn, table))
    shared = [name for name in columns if name in existing]
    if shared:
        listed = ", ".join(shared)
        await conn.exec_driver_sql(
            f"INSERT INTO {temporary} ({listed}) SELECT {listed} FROM {table}"
        )
    await conn.exec_driver_sql(f"DROP TABLE {table}")
    await conn.exec_driver_sql(f"ALTER TABLE {temporary} RENAME TO {table}")


async def _migration_owner_auth(conn: Any) -> None:
    """Owner and session tables, including very old databases."""
    await conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username VARCHAR(50) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            created_at DATETIME
        )
        """
    )
    await conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS auth_sessions (
            id VARCHAR(64) PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at DATETIME,
            expires_at DATETIME NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    await conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_auth_sessions_user_id ON auth_sessions (user_id)"
    )
    await conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_auth_sessions_expires_at ON auth_sessions (expires_at)"
    )
    await conn.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users (username)"
    )
    # Backfill columns that pre-date this migration on old installs.
    await _ensure_column(conn, "users", "created_at", "DATETIME")
    await _ensure_column(conn, "auth_sessions", "expires_at", "DATETIME")


async def _migration_multiuser(conn: Any) -> None:
    """Give every row an owner and make user-scoped tables per-user.

    Old single-user installs have rows with no owner. They get the sentinel
    ``user_id = 0`` here and are claimed by the owner account at startup, which
    keeps this migration safe to run before ``create-owner`` exists.
    """
    await _ensure_column(conn, "users", "role", "VARCHAR(20) DEFAULT 'user'")
    await _ensure_column(conn, "users", "disabled_at", "DATETIME")
    await _ensure_column(conn, "users", "run_limit_per_hour", "INTEGER DEFAULT 30")
    await _ensure_column(conn, "users", "usage_cap_usd", "FLOAT")
    await _ensure_column(conn, "users", "last_login_at", "DATETIME")
    # The first account created by create-owner is the administrator.
    await conn.exec_driver_sql(
        "UPDATE users SET role = 'admin' WHERE id = (SELECT MIN(id) FROM users)"
    )

    for table in USER_ID_TABLES:
        if await _ensure_column(conn, table, "user_id", "INTEGER NOT NULL DEFAULT 0"):
            logger.info("Added user_id to %s", table)

    # These four tables had a global primary key that is now per user.
    await _rebuild_table(
        conn,
        "provider_settings",
        """
        CREATE TABLE {table} (
            user_id INTEGER NOT NULL DEFAULT 0,
            provider VARCHAR(50) NOT NULL,
            encrypted_key TEXT,
            base_url TEXT,
            model VARCHAR(120),
            updated_at DATETIME,
            PRIMARY KEY (user_id, provider)
        )
        """,
        ("user_id", "provider", "encrypted_key", "base_url", "model", "updated_at"),
    )
    await _rebuild_table(
        conn,
        "cowork_app_settings",
        """
        CREATE TABLE {table} (
            user_id INTEGER NOT NULL DEFAULT 0,
            key VARCHAR(80) NOT NULL,
            value TEXT,
            updated_at DATETIME,
            PRIMARY KEY (user_id, key)
        )
        """,
        ("user_id", "key", "value", "updated_at"),
    )
    await _rebuild_table(
        conn,
        "cowork_app_preferences",
        """
        CREATE TABLE {table} (
            user_id INTEGER NOT NULL DEFAULT 0,
            kind VARCHAR(80) NOT NULL,
            label VARCHAR(120) NOT NULL DEFAULT '',
            app_id VARCHAR(200) NOT NULL,
            app_name VARCHAR(200) NOT NULL,
            command TEXT NOT NULL,
            updated_at DATETIME,
            PRIMARY KEY (user_id, kind)
        )
        """,
        ("user_id", "kind", "label", "app_id", "app_name", "command", "updated_at"),
    )
    await _rebuild_table(
        conn,
        "api_keys",
        """
        CREATE TABLE {table} (
            user_id INTEGER NOT NULL DEFAULT 0,
            provider VARCHAR(50) NOT NULL,
            encrypted_key TEXT NOT NULL,
            created_at DATETIME,
            PRIMARY KEY (user_id, provider)
        )
        """,
        ("user_id", "provider", "encrypted_key", "created_at"),
    )

    await conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS signup_invites (
            id VARCHAR(40) PRIMARY KEY,
            code_hash VARCHAR(64) NOT NULL UNIQUE,
            label VARCHAR(120) NOT NULL DEFAULT '',
            created_by INTEGER NOT NULL,
            created_at DATETIME,
            expires_at DATETIME NOT NULL,
            used_at DATETIME,
            used_by INTEGER,
            revoked_at DATETIME
        )
        """
    )
    await conn.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_signup_invites_code_hash "
        "ON signup_invites (code_hash)"
    )
    await conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_signup_invites_expires_at "
        "ON signup_invites (expires_at)"
    )

    for table in USER_ID_TABLES + (
        "provider_settings",
        "cowork_app_settings",
        "cowork_app_preferences",
        "api_keys",
    ):
        if not await _table_exists(conn, table):
            continue
        await conn.exec_driver_sql(
            f"CREATE INDEX IF NOT EXISTS ix_{table}_user_id ON {table} (user_id)"
        )


MIGRATIONS: Sequence[Migration] = (
    (1, "owner-auth-tables", _migration_owner_auth),
    (2, "multiuser-accounts", _migration_multiuser),
)


async def apply_migrations(conn: Any) -> List[str]:
    """Apply pending migrations in order and return their names."""
    await conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    result = await conn.exec_driver_sql("SELECT version FROM schema_migrations")
    done = {row[0] for row in result.fetchall()}

    applied: List[str] = []
    for version, name, migrate in MIGRATIONS:
        if version in done:
            continue
        await migrate(conn)
        await conn.exec_driver_sql(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
            (version, name, datetime.utcnow().isoformat(timespec="seconds")),
        )
        try:
            await conn.exec_driver_sql(f"PRAGMA user_version = {int(version)}")
        except Exception:  # pragma: no cover - non-SQLite backends
            pass
        logger.info("Applied database migration %s (%s)", version, name)
        applied.append(name)
    return applied


__all__ = ["MIGRATIONS", "SCHEMA_VERSION", "apply_migrations"]
