# """
# app/workflows/service.py
# ------------------------
# Async service helpers consumed by the workflows router and graph background tasks.
# """

# from __future__ import annotations

# import uuid
# import logging
# from datetime import datetime, timezone
# from typing import Optional, Any

# from fastapi import HTTPException, status
# from sqlalchemy import select
# from sqlalchemy.ext.asyncio import AsyncSession

# from app.storage.models import Run, RunStatus
# from app.workflows.models import Clarification

# logger = logging.getLogger(__name__)


# # ── Helpers ───────────────────────────────────────────────────────────────────

# def _utcnow() -> datetime:
#     return datetime.now(timezone.utc)


# # ── Run CRUD ──────────────────────────────────────────────────────────────────

# async def create_run(
#     db: AsyncSession,
#     *,
#     project_id: str,
#     workflow_type: str = "generic",
#     input_data: Optional[dict[str, Any]] = None,
#     requirements: Optional[list[str]] = None,
# ) -> Run:
#     """Create and persist a new Run record."""
#     run = Run(
#         id=str(uuid.uuid4()),
#         project_id=project_id,
#         workflow_type=workflow_type,
#         status=RunStatus.PENDING,
#         input_data=input_data or {},
#         run_metadata={"requirements": requirements or []},
#         created_at=_utcnow(),
#     )
#     db.add(run)
#     await db.flush()
#     await db.refresh(run)
#     logger.info("run_created", extra={"run_id": run.id, "workflow_type": workflow_type})
#     return run


# async def get_run_or_404(
#     db: AsyncSession,
#     run_id: str,
#     project_id: Optional[str] = None,
# ) -> Run:
#     """Fetch a Run by ID (and optionally project_id); raise 404 if not found."""
#     stmt = select(Run).where(Run.id == run_id)
#     if project_id is not None:
#         stmt = stmt.where(Run.project_id == project_id)

#     result = await db.execute(stmt)
#     run = result.scalar_one_or_none()

#     if run is None:
#         raise HTTPException(
#             status_code=status.HTTP_404_NOT_FOUND,
#             detail=f"Run '{run_id}' not found.",
#         )
#     return run


# # FIX-1: accepts run_id (str) instead of run (Run object)
# # graph.py calls:  await mark_run_running(db, run_id)
# async def mark_run_running(
#     db: AsyncSession,
#     run_id: str,                   # ← was: run: Run
# ) -> Run:
#     """Transition a run to RUNNING status and stamp started_at."""
#     run = await get_run_or_404(db, run_id)
#     run.status = RunStatus.RUNNING
#     run.started_at = _utcnow()
#     await db.flush()
#     logger.info("run_running", extra={"run_id": run.id})
#     return run


# # FIX-2: single definition, accepts run_id (str)
# # The old file had TWO definitions — the stub at the bottom overwrote this one.
# # graph.py calls:  await mark_run_failed(db, run_id, str(e))
# async def mark_run_failed(
#     db: AsyncSession,
#     run_id: str,                   # ← was: run: Run  (and duplicated as stub)
#     error: Optional[str] = None,
# ) -> Run:
#     """Transition a run to FAILED status and record the error message."""
#     run = await get_run_or_404(db, run_id)
#     run.status = RunStatus.FAILED
#     run.finished_at = _utcnow()
#     if error is not None:
#         run.last_error = error      # adjust field name to match your Run model
#     await db.flush()
#     logger.warning("run_failed", extra={"run_id": run.id, "error": error})
#     return run


# # ── Approval ──────────────────────────────────────────────────────────────────

# async def record_approval(
#     db: AsyncSession,
#     run: Run,
#     *,
#     approved: bool,
#     actor: Optional[str] = None,
#     comment: Optional[str] = None,
# ) -> Run:
#     """Record a human-approval decision on a run."""
#     run.status = RunStatus.COMPLETED if approved else RunStatus.CANCELLED
#     run.finished_at = _utcnow()

#     run.run_metadata = run.run_metadata or {}
#     run.run_metadata["approval"] = {
#         "approved": approved,
#         "actor": actor,
#         "comment": comment,
#         "decided_at": _utcnow().isoformat(),
#     }

#     await db.flush()
#     logger.info(
#         "run_approval_recorded",
#         extra={"run_id": run.id, "approved": approved, "actor": actor},
#     )
#     return run


# # ── Run-slot helpers ──────────────────────────────────────────────────────────

# async def acquire_project_run_slot(project_id: str, run_id: str) -> bool:
#     """Acquire an exclusive run slot for a project (stub — always succeeds)."""
#     logger.debug("acquire_project_run_slot: project=%s run=%s", project_id, run_id)
#     return True


# # FIX-3: removed run_id parameter — graph.py calls release_project_run_slot(project_id)
# async def release_project_run_slot(project_id: str) -> None:  # ← was: (project_id, run_id)
#     """Release the run slot for a project (stub)."""
#     logger.debug("release_project_run_slot: project=%s", project_id)



























# """
# app/workflows/service.py
# ------------------------
# Async service helpers consumed by the workflows router and graph background tasks.
# """

# from __future__ import annotations

# import uuid
# import logging
# from datetime import datetime, timezone
# from typing import Optional, Any

# from fastapi import HTTPException, status
# from sqlalchemy import select
# from sqlalchemy.ext.asyncio import AsyncSession

# from app.storage.models import Run, RunStatus
# from app.workflows.models import Clarification

# logger = logging.getLogger(__name__)


# # ── Helpers ───────────────────────────────────────────────────────────────────

# def _utcnow() -> datetime:
#     return datetime.now(timezone.utc)


# # ── Run CRUD ──────────────────────────────────────────────────────────────────

# async def create_run(
#     db: AsyncSession,
#     *,
#     project_id: str,
#     workflow_type: str = "generic",
#     input_data: Optional[dict[str, Any]] = None,
#     requirements: Optional[list[str]] = None,
# ) -> Run:
#     """Create and persist a new Run record."""
#     run = Run(
#         id=str(uuid.uuid4()),
#         project_id=project_id,
#         workflow_type=workflow_type,
#         status=RunStatus.PENDING,
#         input_data=input_data or {},
#         run_metadata={"requirements": requirements or []},
#         created_at=_utcnow(),
#     )
#     db.add(run)
#     await db.flush()
#     await db.commit()          # persist immediately — router and background task both rely on this
#     await db.refresh(run)
#     logger.info("run_created", extra={"run_id": run.id, "workflow_type": workflow_type})
#     return run


# async def get_run_or_404(
#     db: AsyncSession,
#     run_id: str,
#     project_id: Optional[str] = None,
# ) -> Run:
#     """Fetch a Run by ID (and optionally project_id); raise 404 if not found."""
#     stmt = select(Run).where(Run.id == run_id)
#     if project_id is not None:
#         stmt = stmt.where(Run.project_id == project_id)

#     result = await db.execute(stmt)
#     run = result.scalar_one_or_none()

#     if run is None:
#         raise HTTPException(
#             status_code=status.HTTP_404_NOT_FOUND,
#             detail=f"Run '{run_id}' not found.",
#         )
#     return run


# async def mark_run_running(
#     db: AsyncSession,
#     run_id: str,
# ) -> Run:
#     """
#     Transition a run to RUNNING status and stamp started_at.

#     FIX-1: Added await db.commit() after flush().
#     flush() alone only stages the change in the current transaction.
#     Without commit(), when the background task's AsyncSessionLocal context
#     exits (normally or on exception), SQLAlchemy issues a ROLLBACK and the
#     status update is lost — run stays PENDING forever in the DB.
#     """
#     run = await get_run_or_404(db, run_id)
#     run.status = RunStatus.RUNNING
#     run.started_at = _utcnow()
#     await db.flush()
#     await db.commit()          # FIX-1: was missing — caused ROLLBACK in logs
#     await db.refresh(run)
#     logger.info("run_running", extra={"run_id": run.id})
#     return run


# async def mark_run_failed(
#     db: AsyncSession,
#     run_id: str,
#     error: Optional[str] = None,
# ) -> Run:
#     """
#     Transition a run to FAILED status and record the error message.

#     FIX-2: run.last_error → run.error  (DB column name confirmed from INSERT log)
#     FIX-3: Added await db.commit() after flush() — same reason as mark_run_running.
#            Without this, the FAILED status rolls back and the run appears stuck.
#     """
#     run = await get_run_or_404(db, run_id)
#     run.status = RunStatus.FAILED
#     run.finished_at = _utcnow()
#     if error is not None:
#         run.error = error      # FIX-2: was run.last_error — AttributeError, column is 'error'
#     await db.flush()
#     await db.commit()          # FIX-3: was missing — caused silent ROLLBACK in logs
#     await db.refresh(run)
#     logger.warning("run_failed", extra={"run_id": run.id, "error": error})
#     return run


# async def mark_run_completed(
#     db: AsyncSession,
#     run_id: str,
#     output_data: Optional[dict[str, Any]] = None,
# ) -> Run:
#     """
#     Transition a run to COMPLETED status.
#     Called by the complete_run node at the end of the happy path.
#     """
#     run = await get_run_or_404(db, run_id)
#     run.status = RunStatus.COMPLETED
#     run.finished_at = _utcnow()
#     if output_data is not None:
#         run.output_data = output_data
#     await db.flush()
#     await db.commit()
#     await db.refresh(run)
#     logger.info("run_completed", extra={"run_id": run.id})
#     return run


# # ── Approval ──────────────────────────────────────────────────────────────────

# async def record_approval(
#     db: AsyncSession,
#     run: Run,
#     *,
#     approved: bool,
#     actor: Optional[str] = None,
#     comment: Optional[str] = None,
# ) -> Run:
#     """
#     Record a human-approval decision on a run.

#     FIX-4: Added await db.commit() after flush().
#     Without this, approval decisions made via REST or WebSocket are
#     rolled back when the request session closes — HITL loop breaks.
#     """
#     run.status = RunStatus.COMPLETED if approved else RunStatus.CANCELLED
#     run.finished_at = _utcnow()

#     run.run_metadata = run.run_metadata or {}
#     run.run_metadata["approval"] = {
#         "approved": approved,
#         "actor": actor,
#         "comment": comment,
#         "decided_at": _utcnow().isoformat(),
#     }

#     await db.flush()
#     await db.commit()          # FIX-4: was missing — approval silently lost on session close
#     await db.refresh(run)
#     logger.info(
#         "run_approval_recorded",
#         extra={"run_id": run.id, "approved": approved, "actor": actor},
#     )
#     return run


# # ── Run-slot helpers ──────────────────────────────────────────────────────────

# async def acquire_project_run_slot(project_id: str, run_id: str) -> bool:
#     """Acquire an exclusive run slot for a project (stub — always succeeds)."""
#     logger.debug("acquire_project_run_slot: project=%s run=%s", project_id, run_id)
#     return True


# async def release_project_run_slot(project_id: str) -> None:
#     """Release the run slot for a project (stub)."""
#     logger.debug("release_project_run_slot: project=%s", project_id)





















# Code Generated by Sidekick is for learning and experimentation purposes only.
"""
app/workflows/service.py
------------------------
Async service helpers consumed by the workflows router and graph background tasks.
"""

from __future__ import annotations

import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, Any

from fastapi import HTTPException, status
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.storage.models import Run, RunStatus
from app.workflows.models import Clarification

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Run CRUD ──────────────────────────────────────────────────────────────────

async def create_run(
    db: AsyncSession,
    *,
    project_id: str,
    workflow_type: str = "generic",
    input_data: Optional[dict[str, Any]] = None,
    requirements: Optional[list[str]] = None,
) -> Run:
    """Create and persist a new Run record."""
    run = Run(
        id=str(uuid.uuid4()),
        project_id=project_id,
        workflow_type=workflow_type,
        status=RunStatus.PENDING,
        input_data=input_data or {},
        run_metadata={"requirements": requirements or []},
        created_at=_utcnow(),
    )
    db.add(run)
    await db.flush()
    await db.commit()
    await db.refresh(run)
    logger.info("run_created", extra={"run_id": run.id, "workflow_type": workflow_type})
    return run


async def get_run_or_404(
    db: AsyncSession,
    run_id: str,
    project_id: Optional[str] = None,
) -> Run:
    """
    Fetch a Run by ID (and optionally project_id); raise 404 if not found.

    scalar_one_or_none() is safe here — Run.id is a primary key,
    so at most one row can ever match. The project_id filter is an
    authorization check: if the run exists but belongs to a different
    project, we return 404 (never 403) to avoid leaking existence.
    """
    stmt = select(Run).where(Run.id == run_id)
    if project_id is not None:
        stmt = stmt.where(Run.project_id == project_id)

    result = await db.execute(stmt)
    run = result.scalar_one_or_none()

    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found.",
        )
    return run


async def get_active_run(
    db: AsyncSession,
    project_id: str,
) -> Optional[Run]:
    """
    Return the most recent RUNNING or PAUSED_HITL run for a project, or None.

    FIX for Bug 2 + Bug 6:
    - Uses scalars().first() instead of scalar_one_or_none().
      scalar_one_or_none() raises MultipleResultsFound if the DB has
      leftover duplicate active runs from failed test attempts.
      scalars().first() is always safe — returns the first row or None.
    - Checks both RUNNING and PAUSED_HITL statuses because a run that
      hit an interrupt() is paused but still "active" — a second trigger
      must still return 409.
    - Orders by created_at DESC so we get the most recent active run
      if somehow duplicates exist (defensive, not a normal state).
    """
    stmt = (
        select(Run)
        .where(
            Run.project_id == project_id,
            or_(
                Run.status == RunStatus.RUNNING,
                Run.status == RunStatus.PAUSED_HITL,
            ),
        )
        .order_by(Run.created_at.desc())
    )
    result = await db.execute(stmt)
    return result.scalars().first()  # safe even if multiple rows exist


async def assert_no_active_run(
    db: AsyncSession,
    project_id: str,
) -> None:
    """
    Raise HTTP 409 if an active run already exists for this project.

    Call this before create_run() in every workflow trigger endpoint.
    This is the enforcement point for the spec requirement:
    'only one workflow may be active per project at a time'.

    Returns cleanly if no active run exists.
    """
    existing = await get_active_run(db, project_id)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "RUN_ALREADY_ACTIVE",
                    "message": (
                        f"Project '{project_id}' already has an active run "
                        f"(run_id='{existing.id}', status='{existing.status.value}'). "
                        "Approve, reject, or cancel it before starting a new one."
                    ),
                    "details": {
                        "active_run_id": existing.id,
                        "active_run_status": existing.status.value,
                    },
                }
            },
        )


# ── Status transitions ────────────────────────────────────────────────────────

async def mark_run_running(
    db: AsyncSession,
    run_id: str,
) -> Run:
    """Transition a run to RUNNING status and stamp started_at."""
    run = await get_run_or_404(db, run_id)
    run.status = RunStatus.RUNNING
    run.started_at = _utcnow()
    await db.flush()
    await db.commit()
    await db.refresh(run)
    logger.info("run_running", extra={"run_id": run.id})
    return run


async def mark_run_paused_hitl(
    db: AsyncSession,
    run_id: str,
    *,
    pending_request_id: str,
    pending_hitl_type: str,
) -> Run:
    """
    Transition a run to PAUSED_HITL and record the pending HITL request.

    This is called by the graph background task immediately after LangGraph
    hits an interrupt(). Storing pending_hitl_request_id and pending_hitl_type
    on the run row allows the WebSocket handler to re-push the correct pending
    request on reconnect (idempotent by request_id).

    pending_hitl_type values: 'clarification' | 'approval'
    """
    run = await get_run_or_404(db, run_id)
    run.status = RunStatus.PAUSED_HITL
    run.pending_hitl_request_id = pending_request_id
    run.pending_hitl_type = pending_hitl_type
    await db.flush()
    await db.commit()
    await db.refresh(run)
    logger.info(
        "run_paused_hitl",
        extra={
            "run_id": run.id,
            "request_id": pending_request_id,
            "hitl_type": pending_hitl_type,
        },
    )
    return run


async def mark_run_failed(
    db: AsyncSession,
    run_id: str,
    error: Optional[str] = None,
) -> Run:
    """Transition a run to FAILED status and record the error message."""
    run = await get_run_or_404(db, run_id)
    run.status = RunStatus.FAILED
    run.finished_at = _utcnow()
    if error is not None:
        run.error = error
    await db.flush()
    await db.commit()
    await db.refresh(run)
    logger.warning("run_failed", extra={"run_id": run.id, "error": error})
    return run


async def mark_run_completed(
    db: AsyncSession,
    run_id: str,
    output_data: Optional[dict[str, Any]] = None,
) -> Run:
    """Transition a run to COMPLETED status."""
    run = await get_run_or_404(db, run_id)
    run.status = RunStatus.COMPLETED
    run.finished_at = _utcnow()
    if output_data is not None:
        run.output_data = output_data
    await db.flush()
    await db.commit()
    await db.refresh(run)
    logger.info("run_completed", extra={"run_id": run.id})
    return run


# ── Approval ──────────────────────────────────────────────────────────────────

async def record_approval(
    db: AsyncSession,
    run: Run,
    *,
    approved: bool,
    actor: Optional[str] = None,
    comment: Optional[str] = None,
) -> Run:
    """Record a human-approval decision on a run."""
    run.status = RunStatus.COMPLETED if approved else RunStatus.CANCELLED
    run.finished_at = _utcnow()

    run.run_metadata = run.run_metadata or {}
    run.run_metadata["approval"] = {
        "approved": approved,
        "actor": actor,
        "comment": comment,
        "decided_at": _utcnow().isoformat(),
    }

    await db.flush()
    await db.commit()
    await db.refresh(run)
    logger.info(
        "run_approval_recorded",
        extra={"run_id": run.id, "approved": approved, "actor": actor},
    )
    return run


# ── Run-slot helpers ──────────────────────────────────────────────────────────

async def acquire_project_run_slot(project_id: str, run_id: str) -> bool:
    """
    In-memory slot acquisition (stub — always succeeds at this layer).

    The real enforcement is done by assert_no_active_run() at the DB level
    before create_run() is called. This stub exists as a hook point for
    future distributed locking (e.g., Redis SETNX) if the system scales
    beyond a single process.

    Trade-off documented: in single-process mode, assert_no_active_run()
    provides sufficient protection. In multi-process/multi-worker deployments,
    a race condition exists between the check and the insert — a distributed
    lock would be required.
    """
    logger.debug("acquire_project_run_slot: project=%s run=%s", project_id, run_id)
    return True


async def release_project_run_slot(project_id: str) -> None:
    """Release the run slot for a project (stub — paired with acquire above)."""
    logger.debug("release_project_run_slot: project=%s", project_id)
