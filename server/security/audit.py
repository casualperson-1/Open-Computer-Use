"""Audit rows written with the stdlib sqlite3 driver in a worker thread.

Why not the async engine? Audit writes happen on websocket teardown as well as
in request handlers. An ``aiosqlite`` write that is cancelled while the app is
shutting down can wedge the event loop's cancellation, which hangs the socket
close. A plain sqlite3 write executed via ``asyncio.to_thread`` is cancellable
from the loop's point of view, so a stuck disk never blocks a teardown.

The ORM still *reads* these rows (``GET /api/audit``), so the timestamp format
matches what SQLAlchemy's SQLite DATETIME type expects.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

INSERT_SQL = (
    "INSERT INTO audit_events "
    "(user_id, created_at, actor, action, target, outcome, detail) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)


def sqlite_path_for(database_url: str) -> Optional[Path]:
    """The database file for a ``sqlite``/``sqlite+aiosqlite`` URL, else None."""
    url = (database_url or "").strip()
    if not url.startswith("sqlite"):
        return None
    marker = "///"
    if marker not in url:
        return None
    path = url.split(marker, 1)[1]
    return Path(path).expanduser() if path else None


def write_audit_row(
    path: Path,
    row: Dict[str, Any],
    *,
    timeout: float = 5.0,
) -> None:
    """Append one audit row. Raises ``sqlite3.Error``/``OSError`` on failure."""
    created = row.get("created_at")
    if isinstance(created, datetime):
        stamp = created.strftime("%Y-%m-%d %H:%M:%S.%f")
    else:
        stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")

    connection = sqlite3.connect(str(path), timeout=timeout)
    try:
        connection.execute(
            INSERT_SQL,
            (
                int(row.get("user_id") or 0),
                stamp,
                str(row.get("actor") or "owner")[:80],
                str(row.get("action") or "event")[:80],
                str(row.get("target") or "")[:120],
                str(row.get("outcome") or "ok")[:20],
                row.get("detail"),
            ),
        )
        connection.commit()
    finally:
        connection.close()


__all__ = ["INSERT_SQL", "sqlite_path_for", "write_audit_row"]
