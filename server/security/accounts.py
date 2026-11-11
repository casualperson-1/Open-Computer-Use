"""Multi-user accounts: invites, signup, admin actions and ownership backfill.

The owner account is still created out of band with
``python -m server.cli create-owner`` and is the administrator. Everyone else
joins through the open signup form (``SIGNUP_MODE=open``, the default), an
admin-issued invite (``SIGNUP_MODE=invite``) or not at all
(``SIGNUP_MODE=closed``).

Rows created by the old single-user build have ``user_id = 0``. Once an owner
exists, ``claim_orphan_rows`` hands them to the owner so upgrading loses
nothing.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from database.models import (
    AppPreference,
    AppSetting,
    ApiKey,
    Artifact,
    AuthSession,
    Chat,
    ChatRun,
    DealRecord,
    McpServer,
    NodeRecord,
    ProviderSetting,
    RunRecord,
    ScheduledTask,
    SignupInvite,
    TaskRun,
    UNOWNED,
    User,
)
from security.auth import hash_password, normalize_username, validate_password

SIGNUP_MODES = ("invite", "open", "closed")
INVITE_CODE_BYTES = 24


class InviteError(ValueError):
    """The invite is unknown, already used, revoked or expired."""


def hash_invite_code(code: str) -> str:
    return hashlib.sha256((code or "").strip().encode("utf-8")).hexdigest()


async def owner_id(session_factory: async_sessionmaker[AsyncSession]) -> Optional[int]:
    """The administrator account's id (lowest id when several exist)."""
    async with session_factory() as session:
        admin = (
            await session.execute(
                select(User).where(User.role == "admin").order_by(User.id).limit(1)
            )
        ).scalars().first()
        if admin is not None:
            return admin.id
        first = (await session.execute(select(User).order_by(User.id).limit(1))).scalars().first()
        return first.id if first else None


async def create_invite(
    session_factory: async_sessionmaker[AsyncSession],
    created_by: int,
    *,
    ttl_hours: int = 72,
    label: str = "",
) -> Tuple[SignupInvite, str]:
    """Create a single-use invite and return it with the plaintext code once."""
    code = secrets.token_urlsafe(INVITE_CODE_BYTES)
    row = SignupInvite(
        id=uuid.uuid4().hex[:16],
        code_hash=hash_invite_code(code),
        label=(label or "").strip()[:120],
        created_by=int(created_by),
        created_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(hours=max(1, min(24 * 365, ttl_hours))),
    )
    async with session_factory() as session:
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row, code


async def find_valid_invite(
    session_factory: async_sessionmaker[AsyncSession], code: str
) -> Optional[SignupInvite]:
    if not code or len(code) > 200:
        return None
    digest = hash_invite_code(code)
    async with session_factory() as session:
        row = (
            await session.execute(
                select(SignupInvite).where(SignupInvite.code_hash == digest)
            )
        ).scalars().first()
        if row is None:
            return None
        if row.revoked_at is not None or row.used_at is not None:
            return None
        if row.expires_at <= datetime.utcnow():
            return None
        return row


async def create_user(
    session_factory: async_sessionmaker[AsyncSession],
    username: str,
    password: str,
    *,
    role: str = "user",
    invite_code: Optional[str] = None,
    require_invite: bool = True,
    run_limit_per_hour: int = 30,
    usage_cap_usd: Optional[float] = None,
) -> User:
    """Create an account. Invite validation and consumption are atomic."""
    clean = normalize_username(username)
    validate_password(password, clean)
    if role not in ("admin", "user"):
        raise ValueError("Unknown role.")

    async with session_factory() as session:
        invite: Optional[SignupInvite] = None
        if require_invite:
            digest = hash_invite_code(invite_code or "")
            invite = (
                await session.execute(
                    select(SignupInvite).where(SignupInvite.code_hash == digest)
                )
            ).scalars().first()
            if (
                invite is None
                or invite.revoked_at is not None
                or invite.used_at is not None
                or invite.expires_at <= datetime.utcnow()
            ):
                raise InviteError("That invite is invalid, already used or expired.")
        existing = (
            await session.execute(select(User).where(User.username == clean))
        ).scalars().first()
        if existing is not None:
            raise ValueError("That username is already taken.")
        user = User(
            username=clean,
            password_hash=hash_password(password),
            role=role,
            run_limit_per_hour=max(0, int(run_limit_per_hour)),
            usage_cap_usd=usage_cap_usd,
            created_at=datetime.utcnow(),
        )
        session.add(user)
        await session.flush()
        if invite is not None:
            invite.used_at = datetime.utcnow()
            invite.used_by = user.id
        await session.commit()
        await session.refresh(user)
        return user


def invite_payload(row: SignupInvite) -> Dict[str, Any]:
    return {
        "id": row.id,
        "label": row.label,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "used_at": row.used_at.isoformat() if row.used_at else None,
        "used_by": row.used_by,
        "revoked": row.revoked_at is not None,
        "expired": bool(row.expires_at and row.expires_at <= datetime.utcnow()),
    }


async def list_invites(
    session_factory: async_sessionmaker[AsyncSession], limit: int = 100
) -> List[SignupInvite]:
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(SignupInvite)
                    .order_by(SignupInvite.created_at.desc())
                    .limit(max(1, min(500, limit)))
                )
            ).scalars().all()
        )


async def revoke_invite(
    session_factory: async_sessionmaker[AsyncSession], invite_id: str
) -> bool:
    async with session_factory() as session:
        row = await session.get(SignupInvite, invite_id)
        if row is None or row.revoked_at is not None:
            return False
        row.revoked_at = datetime.utcnow()
        await session.commit()
        return True


async def list_users(session_factory: async_sessionmaker[AsyncSession]) -> List[User]:
    async with session_factory() as session:
        return list(
            (await session.execute(select(User).order_by(User.id))).scalars().all()
        )


async def set_disabled(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    disabled: bool,
) -> Optional[User]:
    async with session_factory() as session:
        user = await session.get(User, user_id)
        if user is None:
            return None
        user.disabled_at = datetime.utcnow() if disabled else None
        if disabled:
            await session.execute(delete(AuthSession).where(AuthSession.user_id == user_id))
        await session.commit()
        await session.refresh(user)
        return user


async def set_quota(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    *,
    run_limit_per_hour: Optional[int] = None,
    usage_cap_usd: Optional[float] = None,
) -> Optional[User]:
    async with session_factory() as session:
        user = await session.get(User, user_id)
        if user is None:
            return None
        if run_limit_per_hour is not None:
            user.run_limit_per_hour = max(0, int(run_limit_per_hour))
        user.usage_cap_usd = usage_cap_usd
        await session.commit()
        await session.refresh(user)
        return user


async def _delete_owned_rows(session: AsyncSession, user_id: int) -> None:
    chat_ids = [
        row[0]
        for row in (
            await session.execute(select(Chat.id).where(Chat.user_id == user_id))
        ).fetchall()
    ]
    if chat_ids:
        run_uuids = [
            row[0]
            for row in (
                await session.execute(
                    select(ChatRun.run_uuid).where(ChatRun.chat_id.in_(chat_ids))
                )
            ).fetchall()
        ]
        if run_uuids:
            await session.execute(
                delete(RunRecord).where(RunRecord.run_uuid.in_(run_uuids))
            )
            await session.execute(
                delete(ChatRun).where(ChatRun.run_uuid.in_(run_uuids))
            )
        await session.execute(delete(Artifact).where(Artifact.chat_id.in_(chat_ids)))
        await session.execute(delete(Chat).where(Chat.id.in_(chat_ids)))

    task_ids = [
        row[0]
        for row in (
            await session.execute(
                select(ScheduledTask.id).where(ScheduledTask.user_id == user_id)
            )
        ).fetchall()
    ]
    if task_ids:
        run_ids = [
            row[0]
            for row in (
                await session.execute(
                    select(TaskRun.id).where(TaskRun.task_id.in_(task_ids))
                )
            ).fetchall()
        ]
        if run_ids:
            await session.execute(delete(DealRecord).where(DealRecord.run_id.in_(run_ids)))
            await session.execute(delete(TaskRun).where(TaskRun.id.in_(run_ids)))
        await session.execute(delete(ScheduledTask).where(ScheduledTask.id.in_(task_ids)))

    for model in (
        Artifact,
        RunRecord,
        ProviderSetting,
        ApiKey,
        AppSetting,
        AppPreference,
        McpServer,
        NodeRecord,
        AuthSession,
    ):
        await session.execute(delete(model).where(model.user_id == user_id))


async def delete_user(
    session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> bool:
    """Delete an account and every row it owns. Sessions go with it."""
    async with session_factory() as session:
        user = await session.get(User, user_id)
        if user is None:
            return False
        await _delete_owned_rows(session, user_id)
        await session.execute(delete(SignupInvite).where(SignupInvite.created_by == user_id))
        await session.delete(user)
        await session.commit()
        return True


async def claim_orphan_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> Dict[str, int]:
    """Assign rows from the single-user era to the owner account.

    Idempotent: only rows still at ``user_id = 0`` are touched, and nothing
    happens until an owner exists.
    """
    owner = await owner_id(session_factory)
    if owner is None:
        return {}
    tables = (
        "provider_settings",
        "api_keys",
        "cowork_app_settings",
        "cowork_app_preferences",
        "mcp_servers",
        "cowork_chats",
        "cowork_runs",
        "chat_artifacts",
        "nodes",
        "scheduled_tasks",
        "task_runs",
        "deals",
        "audit_events",
    )
    claimed: Dict[str, int] = {}
    async with session_factory() as session:
        for table in tables:
            try:
                result = await session.execute(
                    __import__("sqlalchemy").text(
                        f"UPDATE {table} SET user_id = :owner WHERE user_id = :unowned"
                    ),
                    {"owner": owner, "unowned": UNOWNED},
                )
            except Exception:  # pragma: no cover - table may predate the app
                continue
            if result.rowcount:
                claimed[table] = int(result.rowcount)
        await session.commit()
    return claimed


__all__ = [
    "InviteError",
    "SIGNUP_MODES",
    "claim_orphan_rows",
    "create_invite",
    "create_user",
    "delete_user",
    "find_valid_invite",
    "hash_invite_code",
    "invite_payload",
    "list_invites",
    "list_users",
    "owner_id",
    "revoke_invite",
    "set_disabled",
    "set_quota",
]
