"""Owner account operations shared by the CLI and the tests.

There is exactly one owner. It is created out of band with
``python -m server.cli create-owner``; the web app never creates it. Password
changing is CLI-only and invalidates every existing session.
"""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from database.migrations import apply_migrations
from database.models import AuthSession, Base, User
from security.auth import hash_password, normalize_username, validate_password


class OwnerExistsError(RuntimeError):
    """An owner account is already present."""


class OwnerMissingError(RuntimeError):
    """No owner account exists yet."""


async def prepare_database(engine: AsyncEngine) -> List[str]:
    """Create missing tables and apply migrations; returns applied names."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        return await apply_migrations(conn)


async def owner_exists(session_factory: async_sessionmaker[AsyncSession]) -> bool:
    async with session_factory() as session:
        return (await session.execute(select(User.id).limit(1))).first() is not None


async def create_owner(
    session_factory: async_sessionmaker[AsyncSession],
    username: str,
    password: str,
) -> User:
    """Create the single owner account. Refuses when one already exists."""
    clean = normalize_username(username)
    validate_password(password, clean)
    async with session_factory() as session:
        existing = (await session.execute(select(User).limit(1))).scalars().first()
        if existing is not None:
            raise OwnerExistsError(
                f"An owner account already exists ({existing.username}). "
                "Use `python -m server.cli reset-owner-password` instead."
            )
        user = User(
            username=clean,
            password_hash=hash_password(password),
            role="admin",
            run_limit_per_hour=0,  # 0 = unlimited for the administrator
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def set_owner_password(
    session_factory: async_sessionmaker[AsyncSession],
    password: str,
    username: Optional[str] = None,
) -> User:
    """Change the owner password and invalidate every session."""
    async with session_factory() as session:
        query = select(User)
        if username:
            query = query.where(User.username == normalize_username(username))
        user = (await session.execute(query.limit(1))).scalars().first()
        if user is None:
            raise OwnerMissingError(
                "No owner account exists yet. Run `python -m server.cli create-owner` first."
            )
        validate_password(password, user.username)
        user.password_hash = hash_password(password)
        user.disabled_at = None
        await session.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
        await session.commit()
        await session.refresh(user)
        return user


__all__ = [
    "OwnerExistsError",
    "OwnerMissingError",
    "create_owner",
    "owner_exists",
    "prepare_database",
    "set_owner_password",
]
