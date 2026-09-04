import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.database import Base
from app.storage.models import Run, RunStatus, _enum_values  # noqa: F401


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"

    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str):
            normalized = value.lower()
            if normalized == "running":
                return cls.RUNNING
            for member in cls:
                if member.value == normalized:
                    return member
        return None


class WorkflowType(str, enum.Enum):
    REQUIREMENTS = "requirements"
    PLANNING = "planning"
    ARCHITECTURE = "architecture"
    CODEGEN = "codegen"
    GENERIC = "generic"

    @classmethod
    def _missing_(cls, value):
        if value == "requiremets":
            return cls.REQUIREMENTS
        if isinstance(value, str):
            normalized = value.lower()
            for member in cls:
                if member.value == normalized:
                    return member
        return None


class Task(Base):
    """A persisted task or node-level work item for a run."""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String, ForeignKey("runs.id", ondelete="CASCADE"), nullable=True, index=True)

    name: Mapped[str | None] = mapped_column(String, nullable=True, default="task")
    title: Mapped[str] = mapped_column(String, nullable=False, default="Task")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_index: Mapped[int] = mapped_column(default=0, nullable=False)
    pattern_refs: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, default=list)

    status: Mapped[TaskStatus] = mapped_column(
        SAEnum(TaskStatus, values_callable=_enum_values, native_enum=False, validate_strings=False),
        default=TaskStatus.PENDING,
        nullable=False,
    )
    retry_count: Mapped[int] = mapped_column(default=0, nullable=False)
    generated_files: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, default=list)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)

    input_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    output_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __init__(self, **kwargs):
        if "title" not in kwargs and "name" in kwargs:
            kwargs["title"] = kwargs["name"] or "Task"
        if "name" not in kwargs and "title" in kwargs:
            kwargs["name"] = kwargs["title"] or "Task"
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        status = self.status.value if hasattr(self.status, "value") else self.status
        return f"<Task id={self.id!r} title={self.title!r} status={status!r}>"


class Approval(Base):
    """Human-in-the-loop approval decision linked to a run."""

    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String, ForeignKey("runs.id", ondelete="CASCADE"), nullable=True, index=True)
    workflow_type: Mapped[str | None] = mapped_column(String, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    decision: Mapped[str | None] = mapped_column(String, nullable=True)
    approved: Mapped[bool] = mapped_column(default=False, nullable=False)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_urls: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, default=list)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<Approval id={self.id!r} run_id={self.run_id!r} status={self.status!r}>"


class Clarification(Base):
    """Human-in-the-loop clarification request and response."""

    __tablename__ = "clarifications"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String, ForeignKey("runs.id", ondelete="CASCADE"), nullable=True, index=True)
    workflow_type: Mapped[str | None] = mapped_column(String, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    round_number: Mapped[int] = mapped_column(default=1, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    questions: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True, default=list)
    answers: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True, default=list)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<Clarification id={self.id!r} run_id={self.run_id!r} status={self.status!r}>"