import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.storage.database import Base


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_cls]


class RunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED_HITL = "paused_hitl"
    COMPLETED = "completed"
    APPROVED = "approved"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str):
            normalized = value.lower()
            if normalized == "paused_hitl":
                return cls.PAUSED_HITL
            for member in cls:
                if member.value == normalized:
                    return member
        return None


class Run(Base):
    """A project-scoped execution of one workflow."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False,
    )
    project_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workflow_type: Mapped[str] = mapped_column(String, nullable=False, default="generic", index=True)
    status: Mapped[RunStatus] = mapped_column(
        SAEnum(
            RunStatus,
            values_callable=_enum_values,
            native_enum=False,
            validate_strings=False,
        ),
        default=RunStatus.PENDING,
        nullable=False,
        index=True,
    )

    pending_hitl_request_id: Mapped[str | None] = mapped_column(String, nullable=True)
    pending_hitl_type: Mapped[str | None] = mapped_column(String, nullable=True)

    input_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    output_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped["Project"] = relationship(  # type: ignore[name-defined]
        "Project",
        back_populates="runs",
    )

    def __repr__(self) -> str:
        status = self.status.value if hasattr(self.status, "value") else self.status
        return f"<Run id={self.id!r} project_id={self.project_id!r} status={status!r}>"
# Register FK target tables when tests import only the storage/workflow models.
try:  # pragma: no cover - import side effects only
    import app.auth.models  # noqa: F401
    import app.projects.models  # noqa: F401
    import app.documents.models  # noqa: F401
except Exception:
    pass