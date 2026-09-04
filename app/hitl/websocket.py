from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.auth.service import JWTError, decode_token, get_user_by_id
from app.dependencies import check_project_membership
from app.hitl.manager import (
    deliver_response,
    get_connection,
    get_queue,
    register_connection,
    unregister_connection,
)
from app.projects.models import Project
from app.sse.manager import emit_event
from app.storage.database import AsyncSessionLocal
from app.storage.models import Run
from app.workflows.codegen.runner import resume_codegen_run
from app.workflows.models import Approval, Clarification

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/projects", tags=["HITL WebSocket"])


def _status_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


async def _authenticate(websocket: WebSocket):
    auth_header = websocket.headers.get("authorization", "")
    token = websocket.query_params.get("token")
    if auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1]
    if not token:
        await websocket.close(code=1008, reason="Missing bearer token")
        return None

    try:
        token_data = decode_token(token)
    except JWTError:
        await websocket.close(code=1008, reason="Invalid bearer token")
        return None

    async with AsyncSessionLocal() as db:
        user = await get_user_by_id(db, token_data.sub)
    if not user:
        await websocket.close(code=1008, reason="User not found")
        return None
    return user


async def _verify_access(project_id: str, run_id: str, user: Any) -> Optional[Run]:
    async with AsyncSessionLocal() as db:
        project = await db.get(Project, project_id)
        if not project:
            return None
        await check_project_membership(project=project, user=user, db=db)
        run = (
            await db.execute(
                select(Run).where(Run.project_id == project_id, Run.id == run_id)
            )
        ).scalar_one_or_none()
        return run


async def _pending_hitl_message(run: Run) -> Optional[dict[str, Any]]:
    if _status_value(run.status) != "paused_hitl" or not run.pending_hitl_request_id:
        return None

    async with AsyncSessionLocal() as db:
        if run.pending_hitl_type == "approval":
            approval = (
                await db.execute(
                    select(Approval).where(Approval.request_id == run.pending_hitl_request_id)
                )
            ).scalar_one_or_none()
            payload = approval.payload if approval else None
            return {"type": "approval_request", "data": payload or {"request_id": run.pending_hitl_request_id}}

        if run.pending_hitl_type == "clarification":
            clarification = (
                await db.execute(
                    select(Clarification).where(Clarification.request_id == run.pending_hitl_request_id)
                )
            ).scalar_one_or_none()
            payload = clarification.payload if clarification else None
            return {"type": "clarification_request", "data": payload or {"request_id": run.pending_hitl_request_id}}

    return None


async def _sender(websocket: WebSocket, run_id: str) -> None:
    queue = get_queue(run_id)
    while True:
        message = await queue.get()
        await websocket.send_json(message)


async def _handle_client_message(project_id: str, run_id: str, message: dict[str, Any]) -> dict[str, Any]:
    msg_type = message.get("type")
    data = message.get("data") if isinstance(message.get("data"), dict) else message

    if msg_type not in {"approval_response", "clarification_response"}:
        return {"type": "error", "data": {"message": f"Unsupported HITL message type: {msg_type}"}}

    await deliver_response(run_id, message)
    await emit_event(
        msg_type,
        data,
        run_id=run_id,
        project_id=project_id,
        message="HITL response received over WebSocket",
    )

    if msg_type == "approval_response":
        async with AsyncSessionLocal() as db:
            run = (
                await db.execute(
                    select(Run).where(Run.project_id == project_id, Run.id == run_id)
                )
            ).scalar_one_or_none()

        if not run:
            return {"type": "error", "data": {"message": "Run not found"}}
        if run.workflow_type != "codegen":
            return {"type": "error", "data": {"message": "Final WebSocket approval is available for codegen runs."}}
        if _status_value(run.status) != "paused_hitl" or run.pending_hitl_type != "approval":
            return {"type": "error", "data": {"message": "Run is not awaiting final approval."}}

        request_id = data.get("request_id")
        if request_id and request_id != run.pending_hitl_request_id:
            return {"type": "error", "data": {"message": "approval request_id mismatch"}}

        approved = bool(data.get("approved") if "approved" in data else data.get("decision") == "approve")
        feedback = data.get("feedback")
        if not approved and not feedback:
            return {"type": "error", "data": {"message": "feedback is required when rejecting codegen output"}}

        result = await resume_codegen_run(
            run_id=run_id,
            project_id=project_id,
            approved=approved,
            feedback=feedback,
        )
        return {"type": "approval_ack", "data": result}

    return {"type": "clarification_ack", "data": {"run_id": run_id, "status": "received"}}


@router.websocket("/{project_id}/runs/{run_id}/hitl")
async def run_hitl_socket(websocket: WebSocket, project_id: str, run_id: str):
    user = await _authenticate(websocket)
    if user is None:
        return

    try:
        run = await _verify_access(project_id, run_id, user)
    except Exception:
        await websocket.close(code=1008, reason="Project access denied")
        return

    if not run:
        await websocket.close(code=1008, reason="Run not found")
        return

    previous = get_connection(run_id)
    if previous is not None and previous is not websocket:
        try:
            await previous.close(code=4000, reason="Superseded by a new HITL connection")
        except Exception:
            pass

    await websocket.accept()
    register_connection(run_id, websocket)
    await websocket.send_json({"type": "connected", "data": {"run_id": run_id, "project_id": project_id}})

    pending = await _pending_hitl_message(run)
    if pending:
        await websocket.send_json(pending)

    sender_task = asyncio.create_task(_sender(websocket, run_id))
    try:
        while True:
            message = await websocket.receive_json()
            response = await _handle_client_message(project_id, run_id, message)
            await websocket.send_json(response)
    except WebSocketDisconnect:
        logger.info("hitl_websocket_disconnected", extra={"run_id": run_id})
    finally:
        sender_task.cancel()
        unregister_connection(run_id)