from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.dependencies import check_project_membership
from app.projects.models import Project
from app.sse.manager import event_to_dict, list_events, subscribe
from app.storage.database import get_db
from app.storage.models import Run

router = APIRouter(prefix="/projects", tags=["SSE"])


def _format_sse(event: dict) -> str:
    payload = json.dumps(event, default=str)
    return (
        f"id: {event.get('sequence', '')}\n"
        f"event: {event.get('event_type', 'event')}\n"
        f"data: {payload}\n\n"
    )


async def _verify_run_access(
    project_id: str,
    run_id: str,
    db: AsyncSession,
    user: User,
) -> Run:
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")
    await check_project_membership(project=project, user=user, db=db)

    run = (
        await db.execute(
            select(Run).where(Run.project_id == project_id, Run.id == run_id)
        )
    ).scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    return run


@router.get("/{project_id}/runs/{run_id}/events")
async def stream_run_events(
    request: Request,
    project_id: str = Path(...),
    run_id: str = Path(...),
    after: int = Query(0, ge=0, description="Replay events after this sequence."),
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _verify_run_access(project_id, run_id, db, current_user)

    if last_event_id and last_event_id.isdigit():
        after = max(after, int(last_event_id))

    async def event_generator() -> AsyncIterator[str]:
        replay = await list_events(project_id=project_id, run_id=run_id, after=after)
        for event in replay:
            if await request.is_disconnected():
                return
            yield _format_sse(event_to_dict(event))

        async with subscribe(run_id) as queue:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield _format_sse(event)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )