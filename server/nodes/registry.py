"""Persistent registry of enrolled desktop nodes.

Only a SHA-256 hash of each node token is stored, so a database leak cannot be
replayed against the node endpoint. Rotating a token replaces the hash, and
revoking marks the row unusable and drops the live connection.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime
from typing import Any, List, Optional

from sqlalchemy import select

from database.models import NodeRecord

TOKEN_BYTES = 32
TOKEN_MIN_LENGTH = 16


def hash_node_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_node_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


class NodeRegistry:
    """CRUD for node records, backed by the server database."""

    def __init__(self, session_factory) -> None:
        self._sessions = session_factory

    async def create(self, name: str, user_id: int = 0) -> tuple[NodeRecord, str]:
        token = new_node_token()
        record = NodeRecord(
            user_id=int(user_id),
            id=uuid.uuid4().hex[:32],
            name=(name or "node").strip()[:80] or "node",
            token_hash=hash_node_token(token),
            created_at=datetime.utcnow(),
        )
        async with self._sessions() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record, token

    async def list_nodes(self, user_id: Optional[int] = None) -> List[NodeRecord]:
        async with self._sessions() as session:
            query = select(NodeRecord)
            if user_id is not None:
                query = query.where(NodeRecord.user_id == int(user_id))
            result = await session.execute(query.order_by(NodeRecord.created_at))
            return list(result.scalars().all())

    async def get(
        self, node_id: str, user_id: Optional[int] = None
    ) -> Optional[NodeRecord]:
        async with self._sessions() as session:
            record = await session.get(NodeRecord, node_id)
            if record is None:
                return None
            if user_id is not None and record.user_id != int(user_id):
                return None
            return record

    async def find_by_token(self, token: str) -> Optional[NodeRecord]:
        """Look a token up by hash; revoked nodes never authenticate."""
        if not token or len(token) < TOKEN_MIN_LENGTH or len(token) > 200:
            return None
        digest = hash_node_token(token)
        async with self._sessions() as session:
            result = await session.execute(
                select(NodeRecord).where(NodeRecord.token_hash == digest)
            )
            record = result.scalars().first()
            if record is None or record.revoked_at is not None:
                return None
            return record

    async def rotate(
        self, node_id: str, user_id: Optional[int] = None
    ) -> Optional[str]:
        """Replace a node's token. The previous token stops working immediately."""
        token = new_node_token()
        async with self._sessions() as session:
            record = await session.get(NodeRecord, node_id)
            if record is None or record.revoked_at is not None:
                return None
            if user_id is not None and record.user_id != int(user_id):
                return None
            record.token_hash = hash_node_token(token)
            await session.commit()
        return token

    async def revoke(self, node_id: str, user_id: Optional[int] = None) -> bool:
        async with self._sessions() as session:
            record = await session.get(NodeRecord, node_id)
            if record is None or record.revoked_at is not None:
                return False
            if user_id is not None and record.user_id != int(user_id):
                return False
            record.revoked_at = datetime.utcnow()
            record.token_hash = hash_node_token(new_node_token())
            await session.commit()
        return True

    async def touch(
        self,
        node_id: str,
        *,
        backend: Optional[str] = None,
        platform: Optional[str] = None,
        seen: bool = False,
    ) -> None:
        async with self._sessions() as session:
            record = await session.get(NodeRecord, node_id)
            if record is None:
                return
            if backend:
                record.backend = backend[:20]
            if platform:
                record.platform = platform[:200]
            if seen:
                record.last_seen_at = datetime.utcnow()
            await session.commit()

    @staticmethod
    def payload(record: NodeRecord) -> dict[str, Any]:
        """Safe JSON for the UI: never includes the token or its hash."""
        return {
            "id": record.id,
            "name": record.name,
            "backend": record.backend,
            "platform": record.platform,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "last_seen_at": record.last_seen_at.isoformat() if record.last_seen_at else None,
            "revoked": record.revoked_at is not None,
        }


_registry: Optional[NodeRegistry] = None


def configure_registry(session_factory) -> NodeRegistry:
    global _registry
    _registry = NodeRegistry(session_factory)
    return _registry


def node_registry() -> NodeRegistry:
    if _registry is None:  # pragma: no cover - always configured by app.py
        raise RuntimeError("The node registry has not been configured.")
    return _registry


__all__ = [
    "NodeRegistry",
    "configure_registry",
    "hash_node_token",
    "new_node_token",
    "node_registry",
]
