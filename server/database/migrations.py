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

SCHEMA_VERSION = 1

# (version, name, apply(conn))
Migration = Tuple[int, str, Callable[[Any], Awaitable[None]]]


async def _table_columns(conn: Any, table: str) -> List[str]:
    try:
        result = await conn.exec_driver_sql(f"PRAGMA table_info({table})")
        return [row[1] for row in result.fetchall()]
    except Exception:  # pragma: no cover - non-SQLite backends
        return []


async def _ensure_column(conn: Any, table: str, column: str, ddl: str) -> bool:
    """Add ``column`` to ``table`` when it is missing. Returns True if added."""
    columns = await _table_columns(conn, table)
    if not columns or column in columns:
        return False
    try:
        await conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        return True
    except Exception:  # pragma: no cover - racing migration or read-only DB
        logger.warning("Could not add %s.%s", table, column, exc_info=True)
        return False


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


MIGRATIONS: Sequence[Migration] = (
    (1, "owner-auth-tables", _migration_owner_auth),
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
