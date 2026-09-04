# app/workflows/router.py
# Active workflow and run routes.

import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import (
    APIRouter, BackgroundTasks, Depends,
    HTTPException, Request, Response, status,
)
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.workflows.codegen.router import router as codegen_router
from app.storage.database import get_db
from app.dependencies import get_current_user
from app.config import get_settings
# from app.workflows.codegen.router import router as codegen_router
settings = get_settings()

# Project model (from your existing milestone-1 code)
from app.projects.models import Project

# Run models + schemas
from app.workflows.models import (
    Run, RunStatus, WorkflowType,
    Clarification, Approval,
)
from app.workflows.schemas import (
    TriggerRequirementsRequest, TriggerResponse,
    RunDetail, RunListItem,
    SubmitClarificationRequest, ApproveRequest, RejectRequest,
)
from app.workflows.service import (
    create_run, get_run_or_404, mark_run_running,
    mark_run_failed, record_approval,
)

# FIX: deliver_client_response does not exist in manager.py â€” the correct
# name is deliver_response. Neither function is called directly in this
# router (resume goes via Command(resume=...)), so both are removed from
# the import to avoid the ImportError entirely and keep imports honest.
from app.hitl.manager import push_to_run   # kept in case future endpoints need it

from app.observability.models import AuditLog

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Workflows & Runs"])


# â”€â”€ Helper: verify project ownership â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def _get_project(
    project_id:   str,
    db:           AsyncSession,
    current_user,
) -> Project:
    """
    Load project and verify it belongs to the JWT subject.
    Returns 404 (never 403) to avoid existence leakage.
    """
    project = (
        await db.execute(
            select(Project).where(
                Project.id       == project_id,
                Project.owner_id == current_user.id,
            )
        )
    ).scalar_one_or_none()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# TRIGGER â€” POST /projects/{project_id}/workflows/requirements
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.post(
    "/projects/{project_id}/workflows/requirements",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TriggerResponse,
    summary="Trigger Workflow 1 â€” Requirements Gathering",
    responses={
        202: {"description": "Run accepted and started in background"},
        404: {"description": "Project not found"},
        409: {"description": "A run is already active for this project"},
        415: {"description": "Unsupported document type in document_ids"},
    },
)
async def trigger_requirements_workflow(
    project_id:       str,
    body:             TriggerRequirementsRequest,
    background_tasks: BackgroundTasks,
    request:          Request,
    db:               AsyncSession = Depends(get_db),
    current_user                   = Depends(get_current_user),
):
    # â”€â”€ Authorise â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    await _get_project(project_id, db, current_user)
    document_ids = body.document_ids or (body.input or {}).get("document_ids") or []

    # â”€â”€ Validate document IDs belong to this project â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    from app.documents.models import Document   # import here to avoid circular
    for doc_id in document_ids:
        doc = (
            await db.execute(
                select(Document).where(
                    Document.id         == doc_id,
                    Document.project_id == project_id,
                )
            )
        ).scalar_one_or_none()
        if not doc:
            raise HTTPException(
                status_code=404,
                detail=f"Document {doc_id} not found in project {project_id}",
            )
        if doc.status.value != "ready":
            raise HTTPException(
                status_code=409,
                detail=f"Document {doc_id} is not ready (status: {doc.status.value}). "
                       f"Wait for parsing to complete before triggering a workflow.",
            )

    # â”€â”€ Idempotency-Key from header â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    idempotency_key = request.headers.get("Idempotency-Key") or body.idempotency_key

    # â”€â”€ Create run record â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    try:
        run = await create_run(
            db=db,
            project_id=project_id,
            workflow_type=WorkflowType.REQUIREMENTS.value,
            input_data={"document_ids": document_ids},
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    # â”€â”€ Launch background task â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    from app.workflows.requirements.graph import run_requirements_workflow

    background_tasks.add_task(
        run_requirements_workflow,
        run_id=run.id,
        project_id=project_id,
        document_ids=document_ids,
    )

    location = f"/projects/{project_id}/runs/{run.id}"

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=TriggerResponse(
            run_id=run.id,
            project_id=project_id,
            workflow_type="requirements",
            status=RunStatus.PENDING.value,
            location=location,
        ).model_dump(),
        headers={"Location": location},
    )


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# GET /projects/{project_id}/runs/{run_id}
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get(
    "/projects/{project_id}/runs/{run_id}",
    response_model=RunDetail,
    summary="Get run status and metadata",
)
async def get_run(
    project_id:  str,
    run_id:      str,
    db:          AsyncSession = Depends(get_db),
    current_user              = Depends(get_current_user),
):
    await _get_project(project_id, db, current_user)
    run = await get_run_or_404(db, run_id, project_id)
    return RunDetail.model_validate(run)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# GET /projects/{project_id}/runs  (filtered list)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get(
    "/projects/{project_id}/runs",
    response_model=List[RunListItem],
    summary="List all runs for a project",
)
async def list_runs(
    project_id:    str,
    status_filter: Optional[str] = None,   # ?status=running
    db:            AsyncSession  = Depends(get_db),
    current_user                 = Depends(get_current_user),
):
    await _get_project(project_id, db, current_user)

    query = select(Run).where(Run.project_id == project_id)
    if status_filter:
        try:
            query = query.where(Run.status == RunStatus(status_filter))
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid status filter: {status_filter}",
            )

    result = await db.execute(query.order_by(Run.created_at.desc()))
    runs   = result.scalars().all()
    return [RunListItem.model_validate(r) for r in runs]


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# REST HITL FALLBACK â€” POST /projects/{project_id}/runs/{run_id}/clarifications
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.post(
    "/projects/{project_id}/runs/{run_id}/clarifications",
    status_code=status.HTTP_200_OK,
    summary="REST fallback: submit clarification answers",
    responses={
        200: {"description": "Answers injected into graph state"},
        404: {"description": "Run or pending clarification not found"},
        409: {"description": "Run is not waiting for clarification"},
    },
)
async def submit_clarification(
    project_id:  str,
    run_id:      str,
    body:        SubmitClarificationRequest,
    db:          AsyncSession = Depends(get_db),
    current_user              = Depends(get_current_user),
):
    await _get_project(project_id, db, current_user)
    run = await get_run_or_404(db, run_id, project_id)

    if run.status != RunStatus.PAUSED_HITL or run.pending_hitl_type != "clarification":
        raise HTTPException(
            status_code=409,
            detail="Run is not currently waiting for a clarification response.",
        )

    if run.pending_hitl_request_id != body.request_id:
        raise HTTPException(
            status_code=409,
            detail=f"request_id mismatch. Expected {run.pending_hitl_request_id}",
        )

    clar = (
        await db.execute(select(Clarification).where(Clarification.request_id == body.request_id))
    ).scalar_one_or_none()
    if not clar:
        raise HTTPException(status_code=404, detail="Clarification record not found")

    answers = [a.model_dump() for a in body.answers]
    clar.answers = answers
    clar.status = "answered"
    clar.answered_at = datetime.now(timezone.utc)
    db.add(
        AuditLog(
            project_id=project_id,
            run_id=run_id,
            actor_sub=current_user.id,
            action="clarification_answered",
            detail={"request_id": body.request_id, "via": "REST"},
        )
    )
    await db.commit()

    from app.workflows.requirements.graph import resume_requirements_workflow
    import asyncio

    resume_payload = {
        "type": "clarification_response",
        "request_id": body.request_id,
        "answers": answers,
    }
    asyncio.create_task(resume_requirements_workflow(run_id, project_id, resume_payload))
    return {"detail": "Clarification submitted. Run resuming."}


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# REST HITL FALLBACK â€” POST /projects/{project_id}/runs/{run_id}/approve
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.post(
    "/projects/{project_id}/runs/{run_id}/approve",
    status_code=status.HTTP_200_OK,
    summary="REST fallback: approve the run output",
)
async def approve_run(
    project_id:  str,
    run_id:      str,
    db:          AsyncSession = Depends(get_db),
    current_user              = Depends(get_current_user),
):
    await _get_project(project_id, db, current_user)
    run = await get_run_or_404(db, run_id, project_id)

    if run.status != RunStatus.PAUSED_HITL or run.pending_hitl_type != "approval":
        raise HTTPException(
            status_code=409,
            detail="Run is not currently waiting for approval.",
        )

    await _resume_with_approval(
        db=db,
        run=run,
        run_id=run_id,
        project_id=project_id,
        decision="approve",
        feedback=None,
        actor_sub=current_user.id,
    )

    return {"detail": "Run approved. Resuming workflow."}


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# REST HITL FALLBACK â€” POST /projects/{project_id}/runs/{run_id}/reject
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.post(
    "/projects/{project_id}/runs/{run_id}/reject",
    status_code=status.HTTP_200_OK,
    summary="REST fallback: reject the run output with feedback",
)
async def reject_run(
    project_id:  str,
    run_id:      str,
    body:        RejectRequest,
    db:          AsyncSession = Depends(get_db),
    current_user              = Depends(get_current_user),
):
    await _get_project(project_id, db, current_user)
    run = await get_run_or_404(db, run_id, project_id)

    if run.status != RunStatus.PAUSED_HITL or run.pending_hitl_type != "approval":
        raise HTTPException(
            status_code=409,
            detail="Run is not currently waiting for approval.",
        )

    await _resume_with_approval(
        db=db,
        run=run,
        run_id=run_id,
        project_id=project_id,
        decision="reject",
        feedback=body.feedback,
        actor_sub=current_user.id,
    )

    return {"detail": "Run rejected. Feedback injected. Resuming revision cycle."}


# â”€â”€ Shared approval resume helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def _resume_with_approval(
    db, run, run_id, project_id,
    decision: str, feedback: Optional[str], actor_sub: str,
):
    """Record the REST decision and resume the checkpointed requirements graph."""
    import asyncio
    from app.workflows.requirements.graph import resume_requirements_workflow

    request_id = run.pending_hitl_request_id
    appr = (
        await db.execute(select(Approval).where(Approval.request_id == request_id))
    ).scalar_one_or_none()
    if appr:
        appr.decision = decision
        appr.feedback = feedback
        appr.status = "decided"
        appr.approved = decision == "approve"
        appr.decided_at = datetime.now(timezone.utc)

    db.add(
        AuditLog(
            project_id=project_id,
            run_id=run_id,
            actor_sub=actor_sub,
            action="run_approved" if decision == "approve" else "run_rejected",
            detail={"feedback": feedback, "via": "REST"},
        )
    )
    await db.commit()

    resume_payload = {
        "type": "approval_response",
        "request_id": request_id,
        "decision": decision,
        "feedback": feedback,
    }
    asyncio.create_task(resume_requirements_workflow(run_id, project_id, resume_payload))


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# RESUME â€” POST /projects/{project_id}/runs/{run_id}/resume
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.post(
    "/projects/{project_id}/runs/{run_id}/resume",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Resume a failed or interrupted run from its last checkpoint",
)
async def resume_run(
    project_id:  str,
    run_id:      str,
    db:          AsyncSession = Depends(get_db),
    current_user              = Depends(get_current_user),
):
    await _get_project(project_id, db, current_user)
    run = await get_run_or_404(db, run_id, project_id)

    if run.status not in (RunStatus.FAILED,):
        raise HTTPException(
            status_code=409,
            detail=f"Only 'failed' runs can be manually resumed. Current status: {run.status}",
        )

    from app.workflows.requirements.graph import run_requirements_workflow
    import asyncio

    db.add(AuditLog(
        project_id=project_id,
        run_id=run_id,
        actor_sub=current_user.id,
        action="run_resumed",
        detail={"via": "REST"},
    ))
    await db.commit()

    document_ids = (run.input_data or {}).get("document_ids") or []
    asyncio.create_task(run_requirements_workflow(run_id, project_id, document_ids))

    return {"detail": "Run resumed from last checkpoint.", "run_id": run_id}

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# ARTIFACT DOWNLOAD â€” GET /projects/{project_id}/runs/{run_id}/artifacts/{filename}
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get(
    "/projects/{project_id}/runs/{run_id}/artifacts/{filename}",
    summary="Download a run artifact (requirements.md, requirements.json, bundle.zip, etc.)",
)
async def download_artifact(
    project_id:  str,
    run_id:      str,
    filename:    str,
    db:          AsyncSession = Depends(get_db),
    current_user              = Depends(get_current_user),
):
    await _get_project(project_id, db, current_user)
    await get_run_or_404(db, run_id, project_id)

    from app.storage.file_store import FileStore
    fs = FileStore(settings.DATA_ROOT)

    artifact_dir = fs.artifacts_dir(project_id, run_id)
    file_path    = os.path.realpath(os.path.join(artifact_dir, filename))

    # Path traversal guard
    if not file_path.startswith(os.path.realpath(artifact_dir)):
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not os.path.isfile(file_path):
        raise HTTPException(
            status_code=404,
            detail=f"Artifact '{filename}' not found for run {run_id}. "
                   f"Expected path: {file_path}",
        )

    media_type = "application/octet-stream"
    if filename.endswith(".md"):
        media_type = "text/markdown"
    elif filename.endswith(".json"):
        media_type = "application/json"
    elif filename.endswith(".zip"):
        media_type = "application/zip"

    return FileResponse(path=file_path, media_type=media_type, filename=filename)


# ---------------------------------------------------------------------------
# GRAPH PNGS - backend-demo workflow topology renders
# ---------------------------------------------------------------------------

async def _workflow_graph_png(workflow_name: str, renderer):
    cache_path = os.path.join(settings.DATA_ROOT, "graphs", f"{workflow_name}.png")
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)

    if not os.path.isfile(cache_path) or os.path.getsize(cache_path) < 512:
        try:
            png_bytes = renderer()
            with open(cache_path, "wb") as file:
                file.write(png_bytes)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Could not render {workflow_name} graph PNG: {exc}. "
                    "Install Mermaid/Graphviz support or use the built-in fallback renderers."
                ),
            )

    with open(cache_path, "rb") as file:
        return Response(content=file.read(), media_type="image/png")


@router.get(
    "/workflows/requirements/graph.png",
    summary="Rendered graph PNG for Workflow 1 - Requirements",
    response_class=Response,
)
async def get_requirements_graph_png():
    from app.workflows.requirements.graph import get_graph_png_bytes

    return await _workflow_graph_png("requirements", get_graph_png_bytes)


@router.get(
    "/workflows/planning/graph.png",
    summary="Rendered graph PNG for Workflow 2 - Planning",
    response_class=Response,
)
async def get_planning_graph_png():
    from app.workflows.planning.graph import get_graph_png_bytes

    return await _workflow_graph_png("planning", get_graph_png_bytes)


@router.get(
    "/workflows/codegen/graph.png",
    summary="Rendered graph PNG for Workflow 3 - Code Generation",
    response_class=Response,
)
async def get_codegen_graph_png():
    from app.workflows.codegen.graph import get_graph_png_bytes

    return await _workflow_graph_png("codegen", get_graph_png_bytes)


def register_codegen_routes(app_router: APIRouter) -> None:
    """
    Call this from wherever your main workflows router is assembled.

    Example â€” in app/workflows/router.py or app/main.py:

        from app.workflows.codegen.router import router as codegen_router
        workflows_router.include_router(
            codegen_router,
            prefix="/projects/{project_id}",
        )

    Or if your parent router already carries the /projects/{project_id}
    prefix, simply:

        workflows_router.include_router(codegen_router)
    """
    app_router.include_router(
        codegen_router,
        prefix="/projects/{project_id}",
    )


register_codegen_routes(router)

# workflows_router.include_router(codegen_router)
