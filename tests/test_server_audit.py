"""Server audit rows: the sqlite3 writer and the ORM reading them back."""

import asyncio
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import AuditEvent, Base
from security.audit import sqlite_path_for, write_audit_row


def test_sqlite_path_for_only_understands_sqlite_urls(tmp_path):
    assert sqlite_path_for(f"sqlite+aiosqlite:///{tmp_path}/cowork.db") == tmp_path / "cowork.db"
    assert sqlite_path_for("postgresql+asyncpg://user@host/db") is None
    assert sqlite_path_for("") is None
    assert sqlite_path_for("sqlite+aiosqlite://") is None


async def test_sqlite3_rows_are_readable_by_the_orm(tmp_path):
    db = tmp_path / "cowork.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    await asyncio.to_thread(
        write_audit_row,
        db,
        {
            "actor": "node",
            "action": "click",
            "target": "node-1",
            "outcome": "ok",
            "detail": '{"command": "click", "x": 1, "y": 2}',
        },
    )

    async with factory() as session:
        rows = (await session.execute(select(AuditEvent))).scalars().all()
    await engine.dispose()

    assert len(rows) == 1
    row = rows[0]
    # The timestamp format must be one SQLAlchemy's SQLite DateTime can parse.
    assert isinstance(row.created_at, datetime)
    assert (row.actor, row.action, row.target, row.outcome) == (
        "node",
        "click",
        "node-1",
        "ok",
    )
    assert '"command": "click"' in (row.detail or "")


def test_writer_raises_when_the_table_is_missing(tmp_path):
    with pytest.raises(Exception):
        write_audit_row(tmp_path / "empty.db", {"action": "click"})
