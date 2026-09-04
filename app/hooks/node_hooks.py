"""
Node hook decorator and usage recorder for workflow observability.
"""

from __future__ import annotations

import functools
import inspect
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from app.observability.models import UsageRecord
from app.sse.manager import emit_event
from app.storage.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

_MODEL_PRICES_PER_1K = {
    "gemini-2.5-flash": {"prompt": 0.00015, "completion": 0.0006},
    "gemini-1.5-flash": {"prompt": 0.000075, "completion": 0.0003},
    "gemini-2.5-pro": {"prompt": 0.00125, "completion": 0.005},
    "gemini-1.5-pro": {"prompt": 0.00125, "completion": 0.005},
    "gpt-4o": {"prompt": 0.005, "completion": 0.015},
    "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
}


def _state_from_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    state = args[0] if args else kwargs.get("state")
    if isinstance(state, dict):
        return state
    if hasattr(state, "model_dump"):
        try:
            return state.model_dump()
        except Exception:
            return {}
    return {}


def _run_context(state: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    run_id = state.get("run_id")
    project_id = state.get("project_id")
    task = state.get("task")
    if not project_id and isinstance(task, dict):
        project_id = state.get("project_id")
    return run_id, project_id


def _summarize_result(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"result_type": type(result).__name__}
    keys = [
        key for key in (
            "current_task_index",
            "final_status",
            "error",
            "hitl_request_id",
            "bundle_path",
        )
        if key in result
    ]
    return {key: result.get(key) for key in keys}


def node_hook(name: str) -> Callable:
    """Wrap a LangGraph node with persisted pre/post/error events."""

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            state = _state_from_args(args, kwargs)
            run_id, project_id = _run_context(state)
            started = datetime.now(timezone.utc)
            await emit_event(
                "node_started",
                {"node": name, "started_at": started.isoformat()},
                run_id=run_id,
                project_id=project_id,
                node_name=name,
                message=f"Started {name}",
            )
            try:
                result = fn(*args, **kwargs)
                if inspect.isawaitable(result):
                    result = await result
            except Exception as exc:
                await emit_event(
                    "node_error",
                    {"node": name, "error": str(exc)},
                    run_id=run_id,
                    project_id=project_id,
                    node_name=name,
                    message=f"{name} failed",
                )
                raise

            elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            await emit_event(
                "node_completed",
                {"node": name, "elapsed_ms": elapsed_ms, **_summarize_result(result)},
                run_id=run_id,
                project_id=project_id,
                node_name=name,
                message=f"Completed {name}",
            )
            return result

        return wrapper

    return decorator


def _usage_value(usage: Any, key: str) -> int:
    if usage is None:
        return 0
    alt_key = "input_tokens" if key == "prompt_tokens" else ("output_tokens" if key == "completion_tokens" else key)
    if isinstance(usage, dict):
        val = usage.get(key) if key in usage else usage.get(alt_key, 0)
        return int(val or 0)
    val = getattr(usage, key, None)
    if val is None:
        val = getattr(usage, alt_key, 0)
    return int(val or 0)


def estimate_cost_usd(model: Optional[str], prompt_tokens: int, completion_tokens: int) -> float:
    prices = _MODEL_PRICES_PER_1K.get(model or "", {"prompt": 0.0, "completion": 0.0})
    return round(
        (prompt_tokens / 1000.0) * prices["prompt"]
        + (completion_tokens / 1000.0) * prices["completion"],
        6,
    )


async def record_usage(
    node_name: str,
    usage: Optional[Any] = None,
    run_id: Optional[str] = None,
    project_id: Optional[str] = None,
    model: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> UsageRecord | None:
    """Persist token/cost usage and emit a matching progress event."""
    if not run_id:
        logger.debug("usage_without_run", extra={"node_name": node_name, "usage": usage})
        return None

    prompt_tokens = _usage_value(usage, "prompt_tokens")
    completion_tokens = _usage_value(usage, "completion_tokens")
    total_tokens = _usage_value(usage, "total_tokens") or prompt_tokens + completion_tokens
    cost_usd = estimate_cost_usd(model, prompt_tokens, completion_tokens)

    record = None
    try:
        async with AsyncSessionLocal() as db:
            record = UsageRecord(
                project_id=project_id,
                run_id=run_id,
                node_name=node_name,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost_usd=cost_usd,
                metadata_json=metadata or {},
            )
            db.add(record)
            await db.commit()
            await db.refresh(record)
    except Exception as exc:
        logger.warning("usage_record_persist_failed", extra={"run_id": run_id, "node_name": node_name, "error": str(exc)})

    await emit_event(
        "usage_recorded",
        {
            "node": node_name,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost_usd": cost_usd,
        },
        run_id=run_id,
        project_id=project_id,
        node_name=node_name,
        message=f"Usage recorded for {node_name}",
    )
    return record