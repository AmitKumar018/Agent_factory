import enum
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Enum as SAEnum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.storage.database import Base


class ProjectStatus(str, enum.Enum):
    """
    Lifecycle states for a project.
    draft    → created, documents not yet uploaded
    ready    → at least one document parsed and embedded
    running  → a workflow is currently active
    archived → user has archived the project (read-only)
    """
    DRAFT    = "draft"
    READY    = "ready"
    RUNNING  = "running"
    ARCHIVED = "archived"


class Project(Base):
    """
    Root aggregate. Every document, run, task, and artifact
    belongs to exactly one project via a non-null project_id FK.
    """
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(2000), nullable=True)
    status: Mapped[ProjectStatus] = mapped_column(
        SAEnum(ProjectStatus),
        default=ProjectStatus.DRAFT,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships (lazy loaded — we join explicitly when needed)
    documents: Mapped[list] = relationship(
        "Document", back_populates="project", cascade="all, delete-orphan"
    )
    runs: Mapped[list] = relationship(
        "Run", back_populates="project", cascade="all, delete-orphan"
    )
