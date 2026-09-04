import logging
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

sqlite_dir = os.path.dirname(settings.SQLITE_DB_PATH)
if sqlite_dir:
    os.makedirs(sqlite_dir, exist_ok=True)

engine = create_async_engine(
    f"sqlite+aiosqlite:///{settings.SQLITE_DB_PATH}",
    echo=(settings.APP_ENV == "development"),
    connect_args={"check_same_thread": False},
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base class for SQLAlchemy ORM models."""


RECREATE_IF_MISSING: dict[str, list[str]] = {
    "patterns": [
        "id",
        "name",
        "intent",
        "structure",
        "when_to_use",
        "when_not_to_use",
        "prerequisites",
        "references",
        "tags",
        "source",
        "created_at",
        "updated_at",
    ],
}

ADD_COLUMNS: dict[str, dict[str, str]] = {
    "users": {
        "email": "VARCHAR",
        "is_active": "BOOLEAN DEFAULT 1",
    },
    "runs": {
        "project_id": "VARCHAR",
        "workflow_type": "VARCHAR DEFAULT 'generic'",
        "status": "VARCHAR DEFAULT 'pending'",
        "pending_hitl_request_id": "VARCHAR",
        "pending_hitl_type": "VARCHAR",
        "input_data": "JSON",
        "output_data": "JSON",
        "error": "TEXT",
        "run_metadata": "JSON",
        "created_at": "DATETIME",
        "started_at": "DATETIME",
        "finished_at": "DATETIME",
    },
    "tasks": {
        "run_id": "VARCHAR",
        "name": "VARCHAR DEFAULT 'task'",
        "title": "VARCHAR DEFAULT 'Task'",
        "description": "TEXT",
        "order_index": "INTEGER DEFAULT 0",
        "pattern_refs": "JSON",
        "status": "VARCHAR DEFAULT 'pending'",
        "retry_count": "INTEGER DEFAULT 0",
        "generated_files": "JSON",
        "feedback": "TEXT",
        "input_data": "JSON",
        "output_data": "JSON",
        "error": "TEXT",
        "created_at": "DATETIME",
        "started_at": "DATETIME",
        "finished_at": "DATETIME",
    },
    "approvals": {
        "project_id": "VARCHAR",
        "run_id": "VARCHAR",
        "workflow_type": "VARCHAR",
        "request_id": "VARCHAR",
        "status": "VARCHAR DEFAULT 'pending'",
        "decision": "VARCHAR",
        "approved": "BOOLEAN DEFAULT 0",
        "feedback": "TEXT",
        "comment": "TEXT",
        "artifact_urls": "JSON",
        "payload": "JSON",
        "created_at": "DATETIME",
        "decided_at": "DATETIME",
        "resolved_at": "DATETIME",
    },
    "clarifications": {
        "project_id": "VARCHAR",
        "run_id": "VARCHAR",
        "workflow_type": "VARCHAR",
        "request_id": "VARCHAR",
        "round_number": "INTEGER DEFAULT 1",
        "status": "VARCHAR DEFAULT 'pending'",
        "question": "TEXT",
        "answer": "TEXT",
        "questions": "JSON",
        "answers": "JSON",
        "payload": "JSON",
        "created_at": "DATETIME",
        "answered_at": "DATETIME",
        "resolved_at": "DATETIME",
    },
    "usage_records": {
        "project_id": "VARCHAR",
        "run_id": "VARCHAR",
        "node_name": "VARCHAR",
        "model": "VARCHAR",
        "prompt_tokens": "INTEGER DEFAULT 0",
        "completion_tokens": "INTEGER DEFAULT 0",
        "total_tokens": "INTEGER DEFAULT 0",
        "cost_usd": "FLOAT DEFAULT 0",
        "metadata_json": "JSON",
        "created_at": "DATETIME",
    },
    "run_events": {
        "project_id": "VARCHAR",
        "run_id": "VARCHAR",
        "event_type": "VARCHAR DEFAULT 'event'",
        "node_name": "VARCHAR",
        "sequence": "INTEGER DEFAULT 0",
        "data": "JSON",
        "message": "TEXT",
        "created_at": "DATETIME",
    },
    "audit_logs": {
        "project_id": "VARCHAR",
        "run_id": "VARCHAR",
        "actor_sub": "VARCHAR",
        "action": "VARCHAR DEFAULT 'unknown'",
        "detail": "JSON",
        "created_at": "DATETIME",
    },
}


def _columns(sync_conn, table_name: str) -> set[str]:
    result = sync_conn.execute(text(f"PRAGMA table_info({table_name})"))
    return {row[1] for row in result.fetchall()}


def _recreate_known_stale_tables(sync_conn) -> None:
    for table_name, required_cols in RECREATE_IF_MISSING.items():
        existing = _columns(sync_conn, table_name)
        if not existing:
            logger.info("Table '%s' does not exist yet; create_all will handle it.", table_name)
            continue

        missing = [column for column in required_cols if column not in existing]
        if not missing:
            logger.info("Table '%s' schema is up to date.", table_name)
            continue

        logger.warning("Table '%s' is missing columns %s. Recreating it.", table_name, missing)
        sync_conn.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
        if table_name in Base.metadata.tables:
            Base.metadata.tables[table_name].create(sync_conn)


def _add_missing_columns(sync_conn) -> None:
    for table_name, column_specs in ADD_COLUMNS.items():
        existing = _columns(sync_conn, table_name)
        if not existing:
            continue

        for column_name, ddl in column_specs.items():
            if column_name in existing:
                continue
            logger.info("Adding missing column %s.%s", table_name, column_name)
            sync_conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}"))


def _normalize_enum_values(sync_conn) -> None:
    run_statuses = {
        "PENDING": "pending",
        "RUNNING": "running",
        "PAUSED_HITL": "paused_hitl",
        "COMPLETED": "completed",
        "APPROVED": "approved",
        "FAILED": "failed",
        "CANCELLED": "cancelled",
    }
    task_statuses = {
        "PENDING": "pending",
        "IN_PROGRESS": "in_progress",
        "RUNNING": "running",
        "COMPLETED": "completed",
        "FAILED": "failed",
        "SKIPPED": "skipped",
    }

    if _columns(sync_conn, "runs"):
        for old, new in run_statuses.items():
            sync_conn.execute(text("UPDATE runs SET status = :new WHERE status = :old"), {"new": new, "old": old})
        sync_conn.execute(text("UPDATE runs SET workflow_type = 'requirements' WHERE workflow_type = 'requiremets'"))

    if _columns(sync_conn, "tasks"):
        for old, new in task_statuses.items():
            sync_conn.execute(text("UPDATE tasks SET status = :new WHERE status = :old"), {"new": new, "old": old})
        sync_conn.execute(text("UPDATE tasks SET title = COALESCE(title, name, 'Task') WHERE title IS NULL"))
        sync_conn.execute(text("UPDATE tasks SET name = COALESCE(name, title, 'Task') WHERE name IS NULL"))


def _migrate_schema(sync_conn) -> None:
    _recreate_known_stale_tables(sync_conn)
    _add_missing_columns(sync_conn)
    _normalize_enum_values(sync_conn)


async def close_db() -> None:
    """Dispose the shared async SQLAlchemy engine on shutdown."""
    await engine.dispose()


async def init_db() -> None:
    """Import ORM models, create tables, and apply lightweight SQLite migrations."""
    from app.auth.models import User  # noqa: F401
    from app.projects.models import Project  # noqa: F401
    from app.documents.models import Document, DocumentChunk  # noqa: F401
    from app.patterns.models import Pattern  # noqa: F401
    from app.storage.models import Run  # noqa: F401
    from app.workflows.models import Approval, Clarification, Task  # noqa: F401
    from app.observability.models import AuditLog, RunEvent, UsageRecord  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(_migrate_schema)
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_schema)

    logger.info("Database initialized successfully.")


async def get_db():
    """Yield an async DB session per request."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


get_async_db = get_db