from __future__ import annotations

import base64
import functools
from contextlib import asynccontextmanager
import logging
import os
from typing import Literal, Optional

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.config import settings
from app.workflows.graph_png import render_workflow_graph_png
from app.workflows.requirements.nodes import (
    approval_gate,
    complete_run,
    fail_run,
    finalise_requirements,
    handle_rejection,
    incorporate_answers,
    reflect_and_find_gaps,
    request_clarification,
    retrieve_and_extract,
)
from app.workflows.requirements.state import RequirementsState

logger = logging.getLogger(__name__)

_CHECKPOINTER_CONTEXT = None
_CHECKPOINTER = None

# Valid 1x1 PNG fallback. Used only when Mermaid rendering dependencies or network are unavailable.
_FALLBACK_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


async def get_checkpointer() -> AsyncSqliteSaver:
    global _CHECKPOINTER_CONTEXT, _CHECKPOINTER
    if _CHECKPOINTER is not None:
        return _CHECKPOINTER

    db_path = settings.CHECKPOINT_DB_PATH
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    context = AsyncSqliteSaver.from_conn_string(db_path)
    saver = await context.__aenter__()
    setup = getattr(saver, "setup", None)
    if setup is not None:
        result = setup()
        if hasattr(result, "__await__"):
            await result
    _CHECKPOINTER_CONTEXT = context
    _CHECKPOINTER = saver
    return saver


async def close_checkpointer() -> None:
    """Close the shared requirements checkpointer on application/test shutdown."""
    global _CHECKPOINTER_CONTEXT, _CHECKPOINTER
    context = _CHECKPOINTER_CONTEXT
    _CHECKPOINTER_CONTEXT = None
    _CHECKPOINTER = None
    if context is not None:
        await context.__aexit__(None, None, None)


@asynccontextmanager
async def open_checkpointer():
    """Open a scoped requirements checkpointer backed by the shared SQLite file."""
    db_path = settings.CHECKPOINT_DB_PATH
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    context = AsyncSqliteSaver.from_conn_string(db_path)
    saver = await context.__aenter__()
    try:
        setup = getattr(saver, "setup", None)
        if setup is not None:
            result = setup()
            if hasattr(result, "__await__"):
                await result
        yield saver
    finally:
        await context.__aexit__(None, None, None)


def route_after_reflection(state: RequirementsState) -> Literal["request_clarification", "finalise_requirements", "fail_run"]:
    if state.clarification_capped:
        return "fail_run"
    if state.reflection_passed:
        return "finalise_requirements"
    if state.clarification_rounds_used >= settings.MAX_CLARIFICATION_ROUNDS:
        return "finalise_requirements"
    return "request_clarification"


def route_after_approval(state: RequirementsState) -> Literal["complete_run", "handle_rejection"]:
    if state.approval_decision == "approve":
        return "complete_run"
    return "handle_rejection"


def _bind(node_fn, **bound_kwargs):
    @functools.wraps(node_fn)
    async def wrapper(state, config=None):
        return await node_fn(state, config, **bound_kwargs)

    return wrapper


def build_requirements_graph(db, run_id: str, project_id: str, checkpointer: Optional[AsyncSqliteSaver] = None):
    ctx = {"db": db, "run_id": run_id, "project_id": project_id}
    builder = StateGraph(RequirementsState)

    builder.add_node("retrieve_and_extract", _bind(retrieve_and_extract, **ctx))
    builder.add_node("reflect_and_find_gaps", _bind(reflect_and_find_gaps, **ctx))
    builder.add_node("request_clarification", _bind(request_clarification, **ctx))
    builder.add_node("incorporate_answers", _bind(incorporate_answers, **ctx))
    builder.add_node("finalise_requirements", _bind(finalise_requirements, **ctx))
    builder.add_node("approval_gate", _bind(approval_gate, **ctx))
    builder.add_node("handle_rejection", _bind(handle_rejection, **ctx))
    builder.add_node("complete_run", _bind(complete_run, **ctx))
    builder.add_node("fail_run", _bind(fail_run, **ctx))

    builder.add_edge(START, "retrieve_and_extract")
    builder.add_edge("retrieve_and_extract", "reflect_and_find_gaps")
    builder.add_conditional_edges(
        "reflect_and_find_gaps",
        route_after_reflection,
        {
            "request_clarification": "request_clarification",
            "finalise_requirements": "finalise_requirements",
            "fail_run": "fail_run",
        },
    )
    builder.add_edge("request_clarification", "incorporate_answers")
    builder.add_edge("incorporate_answers", "reflect_and_find_gaps")
    builder.add_edge("finalise_requirements", "approval_gate")
    builder.add_conditional_edges(
        "approval_gate",
        route_after_approval,
        {"complete_run": "complete_run", "handle_rejection": "handle_rejection"},
    )
    builder.add_edge("handle_rejection", "reflect_and_find_gaps")
    builder.add_edge("complete_run", END)
    builder.add_edge("fail_run", END)
    return builder.compile(checkpointer=checkpointer)


def _thread_config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}}


async def _log_updates(graph, input_value, run_id: str) -> None:
    async for update in graph.astream(input_value, config=_thread_config(run_id), stream_mode="updates"):
        node_name = list(update.keys())[0] if update else "unknown"
        logger.info("requirements_node_complete", extra={"run_id": run_id, "node": node_name})


async def run_requirements_workflow(run_id: str, project_id: str, document_ids: list[str]) -> None:
    import traceback

    from app.storage.database import AsyncSessionLocal
    from app.storage.models import RunStatus
    from app.workflows.service import mark_run_failed, mark_run_running

    logger.info("requirements_background_started", extra={"run_id": run_id, "project_id": project_id})
    async with AsyncSessionLocal() as db:
        try:
            await mark_run_running(db, run_id)
            async with open_checkpointer() as checkpointer:
                graph = build_requirements_graph(db, run_id, project_id, checkpointer=checkpointer)
                initial_state = RequirementsState(run_id=run_id, project_id=project_id, document_ids=document_ids)
                await _log_updates(graph, initial_state, run_id)

            run = await db.get(__import__("app.storage.models", fromlist=["Run"]).Run, run_id)
            if run and run.status == RunStatus.PAUSED_HITL:
                logger.info("requirements_background_paused", extra={"run_id": run_id, "hitl_type": run.pending_hitl_type})
            else:
                logger.info("requirements_background_finished", extra={"run_id": run_id})
        except Exception as exc:
            logger.error("requirements_background_crashed | run=%s | error=%s\n%s", run_id, exc, traceback.format_exc())
            try:
                await mark_run_failed(db, run_id, str(exc))
            except Exception:
                logger.exception("requirements_mark_failed_failed", extra={"run_id": run_id})


async def resume_requirements_workflow(run_id: str, project_id: str, resume_payload: dict) -> None:
    import traceback

    from app.storage.database import AsyncSessionLocal
    from app.workflows.service import mark_run_failed

    logger.info("requirements_resume_started", extra={"run_id": run_id, "project_id": project_id})
    async with AsyncSessionLocal() as db:
        try:
            async with open_checkpointer() as checkpointer:
                graph = build_requirements_graph(db, run_id, project_id, checkpointer=checkpointer)
                await _log_updates(graph, Command(resume=resume_payload), run_id)
            logger.info("requirements_resume_finished", extra={"run_id": run_id})
        except Exception as exc:
            logger.error("requirements_resume_crashed | run=%s | error=%s\n%s", run_id, exc, traceback.format_exc())
            try:
                await mark_run_failed(db, run_id, str(exc))
            except Exception:
                logger.exception("requirements_resume_mark_failed_failed", extra={"run_id": run_id})


def get_graph_png_bytes() -> bytes:
    graph = build_requirements_graph(db=None, run_id="preview", project_id="preview", checkpointer=None)
    try:
        return graph.get_graph().draw_mermaid_png()
    except Exception as exc:
        logger.warning("requirements_graph_png_fallback", extra={"error": str(exc)})
        return render_workflow_graph_png(
            "Workflow 1: Requirements",
            [
                "Retrieve and extract project requirements from uploaded documents",
                "Reflect and find gaps, looping through clarifications when needed",
                "Incorporate human answers into the requirements state",
                "Finalize requirements artifacts for approval",
                "Pause at approval gate and resume from human decision",
                "Complete approved run or revise after rejection",
            ],
        )
