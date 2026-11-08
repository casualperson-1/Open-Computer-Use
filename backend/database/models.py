from datetime import datetime
from typing import Optional, List
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import String, Integer, Float, Boolean, DateTime, Text, ForeignKey

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(50), unique=True) # gemini, openai, anthropic, deepseek
    encrypted_key: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
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
    run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("task_runs.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(255))
    price: Mapped[float] = mapped_column(Float)
    original_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    discount_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    seller: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    url: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    run: Mapped[Optional["TaskRun"]] = relationship(back_populates="deals")


class ProviderSetting(Base):
    """BYOK credentials and endpoint overrides for one provider."""

    __tablename__ = "provider_settings"
    provider: Mapped[str] = mapped_column(String(50), primary_key=True)
    encrypted_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    base_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Chat(Base):
    """A conversation containing one or more task runs."""

    __tablename__ = "cowork_chats"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
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
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class RunRecord(Base):
    """History of Open Computer Use desktop runs."""

    __tablename__ = "cowork_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
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
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
