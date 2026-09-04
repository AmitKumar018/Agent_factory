import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.database import Base


class UsageRecord(Base):
    """Token/cost usage captured per workflow node or LLM call."""

    __tablename__ = "usage_records"
    __table_args__ = {"extend_existing": True}

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String, ForeignKey("runs.id", ondelete="CASCADE"), nullable=True, index=True)
    node_name: Mapped[str | None] = mapped_column(String, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class AuditLog(Base):
    """Immutable audit trail entry for workflow actions."""

    __tablename__ = "audit_logs"
    __table_args__ = {"extend_existing": True}

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    actor_sub: Mapped[str | None] = mapped_column(String, nullable=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class RunEvent(Base):
    """Persisted run event used by SSE replay and traceability."""

    __tablename__ = "run_events"
    __table_args__ = {"extend_existing": True}

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String, ForeignKey("runs.id", ondelete="CASCADE"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False, default="event")
    node_name: Mapped[str | None] = mapped_column(String, nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)