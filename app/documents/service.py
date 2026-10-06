import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.documents.models import Document, DocumentChunk, ParseStatus, DocumentKind
from app.documents.parser import parse_document, get_extension_from_mime
from app.storage.file_store import file_store
from app.storage.vector_store import document_store
from app.projects.models import Project, ProjectStatus
from app.logging_utils import get_logger

log = get_logger(__name__)



# Upload handling                                                       


async def handle_upload(
    db: AsyncSession,
    project_id: str,
    filename: str,
    mime_type: str,
    file_bytes: bytes,
    kind: DocumentKind = DocumentKind.OTHER,
) -> Document:
    """
    Step 1 of the pipeline: validate, hash-check, save file, create DB row.

    This runs synchronously in the request handler and returns immediately
    with status=pending. The actual parsing is triggered as a background task.
    """
    # Derive extension from MIME type
    extension = get_extension_from_mime(mime_type)
    if not extension:
        raise ValueError(f"Unsupported MIME type: {mime_type}")

    # Compute content hash for deduplication
    content_hash = file_store.compute_hash(file_bytes)

    # --- Idempotency check: same file already uploaded to this project? ---
    existing = await get_document_by_hash(db, project_id, content_hash, filename, kind)
    if existing:
        # Return the existing document instead of creating a duplicate
        log.info("Duplicate upload detected, returning existing document",
                 document_id=existing.id, project_id=project_id)
        return existing

    # Generate a new document ID
    document_id = str(uuid.uuid4())

    # Save raw file to disk: ./data/projects/{project_id}/uploads/{document_id}.{ext}
    file_path = await file_store.save_upload(
        project_id=project_id,
        document_id=document_id,
        extension=extension,
        content=file_bytes,
    )

    # Create the Document row in the DB with status=pending
    document = Document(
        id=document_id,
        project_id=project_id,
        filename=filename,
        extension=extension,
        mime_type=mime_type,
        file_size=len(file_bytes),
        content_hash=content_hash,
        file_path=file_path,
        kind=kind,
        status=ParseStatus.PENDING,
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)

    log.info("Document uploaded", document_id=document_id,
             project_id=project_id, filename=filename)
    return document


async def get_document_by_hash(
    db: AsyncSession,
    project_id: str,
    content_hash: str,
    filename: str | None = None,
    kind: DocumentKind | None = None,
) -> Optional[Document]:
    """Find an idempotent duplicate upload within a project."""
    conditions = [
        Document.project_id == project_id,
        Document.content_hash == content_hash,
    ]
    if kind is not None:
        conditions.append(Document.kind == kind)
    if kind is not None and kind != DocumentKind.OTHER and filename is not None:
        conditions.append(Document.filename == filename)

    result = await db.execute(select(Document).where(*conditions))
    return result.scalar_one_or_none()


# Background parse job                                                  


async def run_parse_job(document_id: str, project_id: str):
    """
    Step 2 of the pipeline: parse the file, chunk it, embed it.
    This runs in the background (via FastAPI BackgroundTasks).

    Flow:
      1. Load file bytes from disk
      2. Parse into chunks using parser.py
      3. Save chunks to DocumentChunk table in DB
      4. Embed chunks and upsert into ChromaDB
      5. Update Document status → ready (or failed on error)
    """
    # We need a fresh DB session since this runs outside the request context
    from app.storage.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        # Fetch the document row
        result = await db.execute(select(Document).where(Document.id == document_id))
        document = result.scalar_one_or_none()

        if not document:
            log.error("Document not found in parse job", document_id=document_id)
            return

        # Mark as parsing so the user can see progress when polling
        document.status = ParseStatus.PARSING
        document.updated_at = datetime.now(timezone.utc)
        await db.commit()

        try:
            log.info("Starting parse job", document_id=document_id,
                     filename=document.filename)

            # 1. Read the raw file from disk
            file_bytes = await file_store.read_bytes(document.file_path)

            # 2. Parse the file into chunks
            raw_chunks = parse_document(file_bytes, document.extension)

            if not raw_chunks:
                raise ValueError("Parser returned no chunks — file may be empty or unreadable")

            # 3. Save each chunk to the DocumentChunk table
            db_chunks = []
            for chunk in raw_chunks:
                db_chunk = DocumentChunk(
                    id=str(uuid.uuid4()),
                    document_id=document_id,
                    project_id=project_id,
                    section_id=chunk["section_id"],
                    section_title=chunk["section_title"],
                    text=chunk["text"],
                    page=chunk["page"],
                    chunk_index=chunk["chunk_index"],
                    kind=document.kind.value,
                )
                db.add(db_chunk)
                db_chunks.append(db_chunk)

            await db.flush()  # write chunks to DB but don't commit yet

            # 4. Embed chunks and upsert into ChromaDB
            # Add the 'kind' field from the document to each chunk dict
            chroma_chunks = [
                {**c, "kind": document.kind.value}
                for c in raw_chunks
            ]
            document_store.upsert_chunks(
                project_id=project_id,
                document_id=document_id,
                chunks=chroma_chunks,
            )

            # 5. Update document stats and mark as ready
            document.status = ParseStatus.READY
            document.section_count = len(set(c["section_id"].split("_chunk")[0]
                                            for c in raw_chunks))
            document.chunk_count = len(raw_chunks)
            document.updated_at = datetime.now(timezone.utc)

            await db.commit()

            # Update the project status to 'ready' if it was still 'draft'
            await _maybe_mark_project_ready(db, project_id)

            log.info("Parse job complete", document_id=document_id,
                     chunk_count=document.chunk_count,
                     section_count=document.section_count)

        except Exception as e:
            # Save the error message so the user can see what went wrong
            await db.rollback()

            document.status = ParseStatus.FAILED
            document.error_message = str(e)
            document.updated_at = datetime.now(timezone.utc)
            await db.commit()

            log.error("Parse job failed", document_id=document_id, error=str(e))


async def _maybe_mark_project_ready(db: AsyncSession, project_id: str):
    """
    If the project is still in DRAFT status and now has at least one
    successfully parsed document, promote it to READY.
    This unlocks workflow triggers.
    """
    result = await db.execute(
        select(Project).where(Project.id == project_id)
    )
    project = result.scalar_one_or_none()

    if project and project.status == ProjectStatus.DRAFT:
        project.status = ProjectStatus.READY
        project.updated_at = datetime.now(timezone.utc)
        await db.commit()
        log.info("Project promoted to READY", project_id=project_id)


# ------------------------------------------------------------------ #
# Read operations                                                       #
# ------------------------------------------------------------------ #

async def get_document(
    db: AsyncSession,
    document_id: str,
    project_id: str,
) -> Optional[Document]:
    """
    Fetch a document scoped to a project.
    Returns None if not found OR if it belongs to a different project → 404.
    """
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.project_id == project_id,
        )
    )
    return result.scalar_one_or_none()


async def list_documents(
    db: AsyncSession,
    project_id: str,
) -> tuple[list[Document], int]:
    """Return all documents for a project, with total count."""
    query = select(Document).where(Document.project_id == project_id).order_by(
        Document.created_at.desc()
    )
    count_query = select(func.count()).select_from(Document).where(
        Document.project_id == project_id
    )

    results = await db.execute(query)
    count_result = await db.execute(count_query)

    return results.scalars().all(), count_result.scalar_one()


async def get_document_sections(
    db: AsyncSession,
    document_id: str,
    project_id: str,
) -> list[DocumentChunk]:
    """
    Return all chunks for a document, ordered by their position.
    Used to show the structured outline to the user.
    """
    result = await db.execute(
        select(DocumentChunk).where(
            DocumentChunk.document_id == document_id,
            DocumentChunk.project_id == project_id,
        ).order_by(DocumentChunk.chunk_index)
    )
    return result.scalars().all()
