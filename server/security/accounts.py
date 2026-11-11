"""Multi-user accounts: signup, admin actions and ownership backfill.

The owner account is still created out of band with
``python -m server.cli create-owner`` and is the administrator. Everyone else
creates their own account from the sign-in page (``SIGNUP_MODE=open``, the
default); ``SIGNUP_MODE=closed`` disables signup.

Rows created by the old single-user build have ``user_id = 0``. Once an owner
exists, ``claim_orphan_rows`` hands them to the owner so upgrading loses
nothing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

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
    TaskRun,
    UNOWNED,
    User,
)
from security.auth import hash_password, normalize_username, validate_password

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


async def create_user(
    session_factory: async_sessionmaker[AsyncSession],
    username: str,
    password: str,
    *,
    role: str = "user",
    run_limit_per_hour: int = 30,
    usage_cap_usd: Optional[float] = None,
) -> User:
    """Create an account. The caller decides whether signup is allowed at all."""
    clean = normalize_username(username)
    validate_password(password, clean)
    if role not in ("admin", "user"):
        raise ValueError("Unknown role.")

    async with session_factory() as session:
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
        await session.commit()
        await session.refresh(user)
        return user


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
    "claim_orphan_rows",
    "create_user",
    "delete_user",
    "list_users",
    "owner_id",
    "set_disabled",
    "set_quota",
]
