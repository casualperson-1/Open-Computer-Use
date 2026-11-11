from datetime import datetime
from typing import Optional, List
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import String, Integer, Float, Boolean, DateTime, Text, ForeignKey

# Every row that belongs to a person carries this column. The value 0 is a
# "no owner yet" sentinel used while an old single-user database is migrated:
# startup claims those rows for the owner account (see security/accounts.py).
UNOWNED = 0


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="user")  # admin | user
    disabled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    run_limit_per_hour: Mapped[int] = mapped_column(Integer, default=30)
    usage_cap_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    @property
    def is_admin(self) -> bool:
        return (self.role or "user") == "admin"


class AuthSession(Base):
    """A server-side session; only the SHA-256 hash of the cookie token is stored."""

    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class ApiKey(Base):
    __tablename__ = "api_keys"
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, default=UNOWNED)
    provider: Mapped[str] = mapped_column(String(50), primary_key=True)  # gemini, openai, anthropic, deepseek
    encrypted_key: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=UNOWNED)
    name: Mapped[str] = mapped_column(String(100))
    goal: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(50), default="gemini")
    model: Mapped[str] = mapped_column(String(100), default="gemini-1.5-flash")
    rrule: Mapped[Optional[str]] = mapped_column(String(200), nullable=True) # iCal RRULE or None for once
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    runs: Mapped[List["TaskRun"]] = relationship(back_populates="task", cascade="all, delete-orphan")

class TaskRun(Base):
    __tablename__ = "task_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=UNOWNED)
    task_id: Mapped[Optional[int]] = mapped_column(ForeignKey("scheduled_tasks.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="running") # running, completed, failed, paused
    goal: Mapped[str] = mapped_column(Text)
    steps_count: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    logs: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    task: Mapped[Optional["ScheduledTask"]] = relationship(back_populates="runs")
    deals: Mapped[List["DealRecord"]] = relationship(back_populates="run", cascade="all, delete-orphan")

class DealRecord(Base):
    __tablename__ = "deals"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=UNOWNED)
    run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("task_runs.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(255))
    price: Mapped[float] = mapped_column(Float)
    original_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    discount_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    seller: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    url: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    run: Mapped[Optional["TaskRun"]] = relationship(back_populates="deals")


class NodeRecord(Base):
    """An enrolled desktop node, owned by exactly one user. Only the hash is stored."""

    __tablename__ = "nodes"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=UNOWNED)
    name: Mapped[str] = mapped_column(String(80), default="node")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    backend: Mapped[str] = mapped_column(String(20), default="desktop")
    platform: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class AuditEvent(Base):
    """One security-relevant event, on the server side of the wire.

    Node commands, node enrolment changes and approval decisions land here. The
    detail column holds the redacted command summary only (never typed text).
    """

    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=UNOWNED)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(80), default="owner")
    action: Mapped[str] = mapped_column(String(80), index=True)
    target: Mapped[str] = mapped_column(String(120), default="")
    outcome: Mapped[str] = mapped_column(String(20), default="ok")
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ProviderSetting(Base):
    """BYOK credentials and endpoint overrides for one provider and one user."""

    __tablename__ = "provider_settings"
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, default=UNOWNED)
    provider: Mapped[str] = mapped_column(String(50), primary_key=True)
    encrypted_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    base_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class McpServer(Base):
    """A Model Context Protocol server the agent can pull tools from."""

    __tablename__ = "mcp_servers"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=UNOWNED)
    name: Mapped[str] = mapped_column(String(80))
    transport: Mapped[str] = mapped_column(String(20), default="stdio")  # stdio | http
    command: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    args: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON array
    env: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # encrypted JSON
    url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    headers: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # encrypted JSON
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Chat(Base):
    """A conversation containing one or more task runs."""

    __tablename__ = "cowork_chats"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=UNOWNED)
    title: Mapped[str] = mapped_column(String(200), default="New task")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class ChatRun(Base):
    """Associates a persisted run with the chat it was started from."""

    __tablename__ = "cowork_chat_runs"
    run_uuid: Mapped[str] = mapped_column(String(40), primary_key=True)
    chat_id: Mapped[str] = mapped_column(
        ForeignKey("cowork_chats.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AppSetting(Base):
    """Small key/value store for UI preferences such as the last provider."""

    __tablename__ = "cowork_app_settings"
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, default=UNOWNED)
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class AppPreference(Base):
    """Saved "always use this app" choice for a file kind or URL."""

    __tablename__ = "cowork_app_preferences"
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, default=UNOWNED)
    kind: Mapped[str] = mapped_column(String(80), primary_key=True)
    label: Mapped[str] = mapped_column(String(120), default="")
    app_id: Mapped[str] = mapped_column(String(200))
    app_name: Mapped[str] = mapped_column(String(200))
    command: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class RunRecord(Base):
    """History of Open Computer Use desktop runs."""

    __tablename__ = "cowork_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=UNOWNED)
    run_uuid: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    goal: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(30), default="running")
    steps: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mode: Mapped[str] = mapped_column(String(20), default="desktop")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Artifact(Base):
    """A Canvas document, slide deck or code snippet produced in a chat."""

    __tablename__ = "chat_artifacts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=UNOWNED)
    chat_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("cowork_chats.id", ondelete="CASCADE"), index=True, nullable=True
    )
    run_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    kind: Mapped[str] = mapped_column(String(20), default="doc")  # doc | slides | code
    title: Mapped[str] = mapped_column(String(160))
    content: Mapped[str] = mapped_column(Text)
    language: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
