from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import check_project_membership, get_current_user
from app.observability.models import AuditLog, RunEvent, UsageRecord
from app.projects.models import Project
from app.sse.manager import event_to_dict
from app.storage.database import get_db
from app.storage.models import Run
from app.workflows.models import Approval, Clarification, Task

router = APIRouter(prefix="/projects", tags=["Observability"])


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _status(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


async def _project_or_404(project_id: str, db: AsyncSession, current_user: Any) -> Project:
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    await check_project_membership(project=project, user=current_user, db=db)
    return project


async def _run_or_404(project_id: str, run_id: str, db: AsyncSession) -> Run:
    run = (
        await db.execute(
            select(Run).where(Run.project_id == project_id, Run.id == run_id)
        )
    ).scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run


def _run_dict(run: Run) -> dict[str, Any]:
    return {
        "id": run.id,
        "project_id": run.project_id,
        "workflow_type": run.workflow_type,
        "status": _status(run.status),
        "pending_hitl_request_id": run.pending_hitl_request_id,
        "pending_hitl_type": run.pending_hitl_type,
        "input_data": run.input_data or {},
        "output_data": run.output_data or {},
        "error": run.error,
        "run_metadata": run.run_metadata or {},
        "created_at": _dt(run.created_at),
        "started_at": _dt(run.started_at),
        "finished_at": _dt(run.finished_at),
    }


def _usage_dict(record: UsageRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "project_id": record.project_id,
        "run_id": record.run_id,
        "node_name": record.node_name,
        "model": record.model,
        "prompt_tokens": record.prompt_tokens,
        "completion_tokens": record.completion_tokens,
        "total_tokens": record.total_tokens,
        "cost_usd": record.cost_usd,
        "metadata": record.metadata_json or {},
        "created_at": _dt(record.created_at),
    }


def _audit_dict(record: AuditLog) -> dict[str, Any]:
    return {
        "id": record.id,
        "project_id": record.project_id,
        "run_id": record.run_id,
        "actor_sub": record.actor_sub,
        "action": record.action,
        "detail": record.detail or {},
        "created_at": _dt(record.created_at),
    }


def _task_dict(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "run_id": task.run_id,
        "name": task.name,
        "title": task.title,
        "description": task.description,
        "order_index": task.order_index,
        "pattern_refs": task.pattern_refs or [],
        "status": _status(task.status),
        "retry_count": task.retry_count,
        "generated_files": task.generated_files or [],
        "feedback": task.feedback,
        "input_data": task.input_data or {},
        "output_data": task.output_data or {},
        "error": task.error,
        "created_at": _dt(task.created_at),
        "started_at": _dt(task.started_at),
        "finished_at": _dt(task.finished_at),
    }


def _approval_dict(record: Approval) -> dict[str, Any]:
    return {
        "id": record.id,
        "project_id": record.project_id,
        "run_id": record.run_id,
        "workflow_type": record.workflow_type,
        "request_id": record.request_id,
        "status": record.status,
        "decision": record.decision,
        "approved": record.approved,
        "feedback": record.feedback,
        "comment": record.comment,
        "artifact_urls": record.artifact_urls or [],
        "payload": record.payload or {},
        "created_at": _dt(record.created_at),
        "decided_at": _dt(record.decided_at),
        "resolved_at": _dt(record.resolved_at),
    }


def _clarification_dict(record: Clarification) -> dict[str, Any]:
    return {
        "id": record.id,
        "project_id": record.project_id,
        "run_id": record.run_id,
        "workflow_type": record.workflow_type,
        "request_id": record.request_id,
        "round_number": record.round_number,
        "status": record.status,
        "question": record.question,
        "answer": record.answer,
        "questions": record.questions or [],
        "answers": record.answers or [],
        "payload": record.payload or {},
        "created_at": _dt(record.created_at),
        "answered_at": _dt(record.answered_at),
        "resolved_at": _dt(record.resolved_at),
    }


def _usage_summary(records: list[UsageRecord]) -> dict[str, Any]:
    totals = {
        "prompt_tokens": sum(r.prompt_tokens or 0 for r in records),
        "completion_tokens": sum(r.completion_tokens or 0 for r in records),
        "total_tokens": sum(r.total_tokens or 0 for r in records),
        "cost_usd": round(sum(r.cost_usd or 0.0 for r in records), 6),
        "record_count": len(records),
    }
    by_node: dict[str, dict[str, Any]] = {}
    by_model: dict[str, dict[str, Any]] = {}
    for record in records:
        for bucket, key in ((by_node, record.node_name or "unknown"), (by_model, record.model or "unknown")):
            item = bucket.setdefault(
                key,
                {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                    "record_count": 0,
                },
            )
            item["prompt_tokens"] += record.prompt_tokens or 0
            item["completion_tokens"] += record.completion_tokens or 0
            item["total_tokens"] += record.total_tokens or 0
            item["cost_usd"] = round(item["cost_usd"] + (record.cost_usd or 0.0), 6)
            item["record_count"] += 1
    return {"totals": totals, "by_node": by_node, "by_model": by_model}


async def _count(db: AsyncSession, model, *where) -> int:
    return int(await db.scalar(select(func.count()).select_from(model).where(*where)) or 0)


async def _run_history_summary(db: AsyncSession, run: Run) -> dict[str, Any]:
    event_count = await _count(db, RunEvent, RunEvent.run_id == run.id)
    usage_count = await _count(db, UsageRecord, UsageRecord.run_id == run.id)
    audit_count = await _count(db, AuditLog, AuditLog.run_id == run.id)
    task_count = await _count(db, Task, Task.run_id == run.id)
    token_total = int(
        await db.scalar(
            select(func.coalesce(func.sum(UsageRecord.total_tokens), 0)).where(UsageRecord.run_id == run.id)
        )
        or 0
    )
    cost_total = float(
        await db.scalar(
            select(func.coalesce(func.sum(UsageRecord.cost_usd), 0.0)).where(UsageRecord.run_id == run.id)
        )
        or 0.0
    )
    return {
        **_run_dict(run),
        "event_count": event_count,
        "usage_record_count": usage_count,
        "audit_record_count": audit_count,
        "task_count": task_count,
        "total_tokens": token_total,
        "cost_usd": round(cost_total, 6),
    }


@router.get("/{project_id}/observability/usage", summary="Project-level usage summary")
async def project_usage(
    project_id: str,
    workflow_type: Optional[str] = Query(default=None),
    run_id: Optional[str] = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    await _project_or_404(project_id, db, current_user)
    if run_id:
        await _run_or_404(project_id, run_id, db)

    query = select(UsageRecord).where(UsageRecord.project_id == project_id)
    if run_id:
        query = query.where(UsageRecord.run_id == run_id)
    if workflow_type:
        query = query.join(Run, Run.id == UsageRecord.run_id).where(Run.workflow_type == workflow_type)

    records = list((await db.execute(query.order_by(UsageRecord.created_at.desc()).limit(limit))).scalars().all())
    return {
        "project_id": project_id,
        "run_id": run_id,
        "workflow_type": workflow_type,
        **_usage_summary(records),
        "records": [_usage_dict(record) for record in records],
    }


@router.get("/{project_id}/runs/{run_id}/usage", summary="Run-level token and cost usage")
async def run_usage(
    project_id: str,
    run_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    await _project_or_404(project_id, db, current_user)
    await _run_or_404(project_id, run_id, db)
    records = list(
        (
            await db.execute(
                select(UsageRecord)
                .where(UsageRecord.run_id == run_id)
                .order_by(UsageRecord.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return {"project_id": project_id, "run_id": run_id, **_usage_summary(records), "records": [_usage_dict(r) for r in records]}


@router.get("/{project_id}/runs/history", summary="Run history for a project")
async def project_run_history(
    project_id: str,
    workflow_type: Optional[str] = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    await _project_or_404(project_id, db, current_user)
    query = select(Run).where(Run.project_id == project_id)
    if workflow_type:
        query = query.where(Run.workflow_type == workflow_type)
    if status_filter:
        query = query.where(Run.status == status_filter)
    runs = list((await db.execute(query.order_by(Run.created_at.desc()).offset(offset).limit(limit))).scalars().all())
    return {
        "project_id": project_id,
        "limit": limit,
        "offset": offset,
        "items": [await _run_history_summary(db, run) for run in runs],
    }


@router.get("/{project_id}/runs/{run_id}/history", summary="Full persisted run history")
async def run_history(
    project_id: str,
    run_id: str,
    event_limit: int = Query(default=500, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    await _project_or_404(project_id, db, current_user)
    run = await _run_or_404(project_id, run_id, db)
    events = list(
        (
            await db.execute(
                select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.sequence.asc()).limit(event_limit)
            )
        )
        .scalars()
        .all()
    )
    usage_records = list((await db.execute(select(UsageRecord).where(UsageRecord.run_id == run_id))).scalars().all())
    audits = list((await db.execute(select(AuditLog).where(AuditLog.run_id == run_id).order_by(AuditLog.created_at.asc()))).scalars().all())
    tasks = list((await db.execute(select(Task).where(Task.run_id == run_id).order_by(Task.order_index.asc()))).scalars().all())
    approvals = list((await db.execute(select(Approval).where(Approval.run_id == run_id).order_by(Approval.created_at.asc()))).scalars().all())
    clarifications = list((await db.execute(select(Clarification).where(Clarification.run_id == run_id).order_by(Clarification.created_at.asc()))).scalars().all())
    return {
        "project_id": project_id,
        "run_id": run_id,
        "run": _run_dict(run),
        "usage": _usage_summary(usage_records),
        "events": [event_to_dict(event) for event in events],
        "audit": [_audit_dict(record) for record in audits],
        "tasks": [_task_dict(task) for task in tasks],
        "approvals": [_approval_dict(record) for record in approvals],
        "clarifications": [_clarification_dict(record) for record in clarifications],
    }


@router.get("/{project_id}/runs/{run_id}/trace", summary="Chronological run trace")
@router.get("/{project_id}/runs/{run_id}/traceability", summary="Chronological run traceability view")
async def run_traceability(
    project_id: str,
    run_id: str,
    limit: int = Query(default=1000, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    await _project_or_404(project_id, db, current_user)
    run = await _run_or_404(project_id, run_id, db)
    events = list((await db.execute(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.sequence.asc()).limit(limit))).scalars().all())
    usage_records = list((await db.execute(select(UsageRecord).where(UsageRecord.run_id == run_id).order_by(UsageRecord.created_at.asc()).limit(limit))).scalars().all())
    audits = list((await db.execute(select(AuditLog).where(AuditLog.run_id == run_id).order_by(AuditLog.created_at.asc()).limit(limit))).scalars().all())
    tasks = list((await db.execute(select(Task).where(Task.run_id == run_id).order_by(Task.order_index.asc()))).scalars().all())

    timeline: list[dict[str, Any]] = []
    for event in events:
        timeline.append({"kind": "event", "at": _dt(event.created_at), **event_to_dict(event)})
    for record in usage_records:
        timeline.append({"kind": "usage", "at": _dt(record.created_at), **_usage_dict(record)})
    for record in audits:
        timeline.append({"kind": "audit", "at": _dt(record.created_at), **_audit_dict(record)})
    timeline.sort(key=lambda item: item.get("at") or "")

    generated_files = []
    for task in tasks:
        generated_files.extend(task.generated_files or [])

    return {
        "project_id": project_id,
        "run_id": run_id,
        "workflow_type": run.workflow_type,
        "status": _status(run.status),
        "lineage": {
            "input_data": run.input_data or {},
            "output_data": run.output_data or {},
            "generated_files": sorted(set(generated_files)),
            "tasks": [_task_dict(task) for task in tasks],
        },
        "usage": _usage_summary(usage_records),
        "timeline": timeline[:limit],
    }


@router.get("/{project_id}/runs/{run_id}/audit", summary="List audit entries for a run")
async def run_audit(
    project_id: str,
    run_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    await _project_or_404(project_id, db, current_user)
    await _run_or_404(project_id, run_id, db)
    records = list(
        (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.run_id == run_id)
                .order_by(AuditLog.created_at.asc())
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return {"project_id": project_id, "run_id": run_id, "items": [_audit_dict(record) for record in records]}


@router.get("/{project_id}/runs/{run_id}/audit/{audit_id}", summary="Get a single audit entry")
async def audit_detail(
    project_id: str,
    run_id: str,
    audit_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    await _project_or_404(project_id, db, current_user)
    await _run_or_404(project_id, run_id, db)
    record = (
        await db.execute(
            select(AuditLog).where(AuditLog.id == audit_id, AuditLog.run_id == run_id)
        )
    ).scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit entry not found")
    return _audit_dict(record)

