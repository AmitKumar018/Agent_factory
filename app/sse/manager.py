"""
Persisted run-event publisher used by SSE, HITL, and node hooks.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.observability.models import RunEvent
from app.storage.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

_subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
_subscribers_lock = asyncio.Lock()


@asynccontextmanager
async def _session_scope(db: AsyncSession | None = None) -> AsyncIterator[AsyncSession]:
    if db is not None:
        yield db
        return

    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(key): _json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_json_safe(item) for item in value]
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)


def event_to_dict(event: RunEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "sequence": event.sequence,
        "project_id": event.project_id,
        "run_id": event.run_id,
        "event_type": event.event_type,
        "node_name": event.node_name,
        "data": event.data or {},
        "message": event.message,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


async def emit_event(
    event_type: str,
    data: Any = None,
    run_id: Optional[str] = None,
    project_id: Optional[str] = None,
    node_name: Optional[str] = None,
    message: Optional[str] = None,
    db: AsyncSession | None = None,
) -> RunEvent | None:
    """Persist and fan out a replayable run event."""
    if not run_id:
        logger.debug("sse_event_without_run", extra={"event_type": event_type, "data": data})
        return None

    try:
        async with _session_scope(db) as session:
            max_sequence = await session.scalar(
                select(func.max(RunEvent.sequence)).where(RunEvent.run_id == run_id)
            )
            event = RunEvent(
                project_id=project_id,
                run_id=run_id,
                event_type=event_type,
                node_name=node_name,
                sequence=(max_sequence or 0) + 1,
                data=_json_safe(data or {}),
                message=message,
            )
            session.add(event)
            await session.flush()
            payload = event_to_dict(event)
    except Exception as exc:
        logger.warning("run_event_persist_failed", extra={"run_id": run_id, "event_type": event_type, "error": str(exc)})
        payload = {
            "id": None,
            "sequence": 0,
            "project_id": project_id,
            "run_id": run_id,
            "event_type": event_type,
            "node_name": node_name,
            "data": _json_safe(data or {}),
            "message": message,
            "created_at": None,
        }
        event = None

    await publish_live(run_id, payload)
    return event


async def publish_live(run_id: str, payload: dict[str, Any]) -> None:
    async with _subscribers_lock:
        queues = list(_subscribers.get(run_id, set()))
    for queue in queues:
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            logger.warning("sse_subscriber_queue_full", extra={"run_id": run_id})


async def list_events(
    *,
    project_id: str,
    run_id: str,
    after: int = 0,
    limit: int = 500,
    db: AsyncSession | None = None,
) -> list[RunEvent]:
    async with _session_scope(db) as session:
        result = await session.execute(
            select(RunEvent)
            .where(
                RunEvent.project_id == project_id,
                RunEvent.run_id == run_id,
                RunEvent.sequence > after,
            )
            .order_by(RunEvent.sequence.asc())
            .limit(limit)
        )
        return list(result.scalars().all())


@asynccontextmanager
async def subscribe(run_id: str, maxsize: int = 100) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=maxsize)
    async with _subscribers_lock:
        _subscribers.setdefault(run_id, set()).add(queue)
    try:
        yield queue
    finally:
        async with _subscribers_lock:
            subscribers = _subscribers.get(run_id)
            if subscribers:
                subscribers.discard(queue)
                if not subscribers:
                    _subscribers.pop(run_id, None)


class SSEManager:
    async def emit(self, event_type: str, data: Any = None, **kwargs: Any) -> RunEvent | None:
        return await emit_event(event_type, data, **kwargs)


sse_manager = SSEManager()