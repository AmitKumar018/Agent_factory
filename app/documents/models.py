
import enum
from datetime import datetime, timezone
from sqlalchemy import String, Integer, DateTime, Enum as SAEnum, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.storage.database import Base


class ParseStatus(str, enum.Enum):
    """
    Tracks where a document is in the parse pipeline.

    pending   → file saved, parse job not started yet
    parsing   → background job is actively parsing
    ready     → parsed, chunked, embedded — ready for RAG
    failed    → parse job threw an error
    """
    PENDING  = "pending"
    PARSING  = "parsing"
    READY    = "ready"
    FAILED   = "failed"


class DocumentKind(str, enum.Enum):
    """
    What type of document this is.
    Stored in chunk metadata so agents can filter by document type.
    e.g., 'give me only requirements from the BRD'
    """
    BRD   = "BRD"
    PRD   = "PRD"
    TRD   = "TRD"
    OTHER = "other"


class Document(Base):
    """
    Represents one uploaded file.
    Each document belongs to exactly one project (non-null project_id FK).
    """
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String, primary_key=True)

    # Every document is scoped to a project — cross-project access returns 404
    project_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Original filename the user uploaded (e.g., "requirements.pdf")
    filename: Mapped[str] = mapped_column(String(255), nullable=False)

    # File extension without the dot (e.g., "pdf", "docx")
    extension: Mapped[str] = mapped_column(String(10), nullable=False)

    # MIME type (e.g., "application/pdf")
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)

    # File size in bytes — used to enforce upload size limits
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)

    # SHA-256 hash of the raw file content — used for idempotent uploads
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Absolute path to the raw file on disk
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)

    # What kind of document this is (BRD / PRD / TRD / other)
    kind: Mapped[DocumentKind] = mapped_column(
        SAEnum(DocumentKind),
        default=DocumentKind.OTHER,
        nullable=False,
    )

    # Current state of the parse pipeline
    status: Mapped[ParseStatus] = mapped_column(
        SAEnum(ParseStatus),
        default=ParseStatus.PENDING,
        nullable=False,
    )

    # How many logical sections were extracted (filled after parsing)
    section_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # How many chunks were embedded into ChromaDB (filled after embedding)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # If status=failed, the error message is stored here
    error_message: Mapped[str] = mapped_column(Text, nullable=True)

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

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="documents")  # noqa: F821
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        "DocumentChunk",
        back_populates="document",
        cascade="all, delete-orphan",
    )


class DocumentChunk(Base):
    """
    One logical chunk of a parsed document.

    Why store chunks in SQL AND ChromaDB?
    - SQL: structured metadata, section outline, fast lookup by document_id
    - ChromaDB: vector embeddings for semantic similarity search

    They are kept in sync — a chunk row in SQL has a matching vector in ChromaDB
    with the same section_id.
    """
    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(String, primary_key=True)

    document_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # project_id is denormalized here for fast scoped queries
    project_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    # Stable ID for this chunk — used as the ChromaDB vector ID
    # Format: {document_id}_{section_index}
    section_id: Mapped[str] = mapped_column(String(100), nullable=False)

    # Heading or title of this section (empty string if not detected)
    section_title: Mapped[str] = mapped_column(String(500), nullable=False, default="")

    # The actual text content of this chunk
    text: Mapped[str] = mapped_column(Text, nullable=False)

    # Page number where this chunk starts (0 for non-paginated formats like MD/TXT)
    page: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Position of this chunk within the document (for ordering)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)

    # Document kind copied from parent document (for ChromaDB filter convenience)
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="other")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationship back to the parent document
    document: Mapped["Document"] = relationship("Document", back_populates="chunks")
