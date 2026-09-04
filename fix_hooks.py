import os

# ── app/hooks/node_hooks.py ───────────────────────────────────────────────────
hooks_content = (
    '"""\n'
    'app/hooks/node_hooks.py\n'
    'Node hook decorator and usage recorder stubs.\n'
    '"""\n\n'
    'from __future__ import annotations\n'
    'import logging\n'
    'import functools\n'
    'from typing import Any, Callable, Optional\n\n'
    'logger = logging.getLogger(__name__)\n\n\n'
    'def node_hook(name: str) -> Callable:\n'
    '    """Decorator factory for node observability (SSE / cost tracking).\n'
    '    Wraps the node function and passes through unchanged in this stub.\n'
    '    """\n'
    '    def decorator(fn: Callable) -> Callable:\n'
    '        @functools.wraps(fn)\n'
    '        async def wrapper(*args: Any, **kwargs: Any) -> Any:\n'
    '            logger.debug(f"node_hook enter: {name}")\n'
    '            result = await fn(*args, **kwargs)\n'
    '            logger.debug(f"node_hook exit: {name}")\n'
    '            return result\n'
    '        return wrapper\n'
    '    return decorator\n\n\n'
    'def record_usage(\n'
    '    node_name: str,\n'
    '    usage: Optional[Any] = None,\n'
    '    run_id: Optional[str] = None,\n'
    ') -> None:\n'
    '    """Records token / cost usage for a node (stub — logs only)."""\n'
    '    logger.debug(\n'
    '        f"record_usage: {node_name}",\n'
    '        extra={"run_id": run_id, "usage": usage},\n'
    '    )\n'
)

os.makedirs("app/hooks", exist_ok=True)
with open("app/hooks/__init__.py", "a", encoding="utf-8") as f:
    pass
with open("app/hooks/node_hooks.py", "w", encoding="utf-8") as f:
    f.write(hooks_content)
print("Done - app/hooks/node_hooks.py written")

# ── app/hitl/manager.py ───────────────────────────────────────────────────────
hitl_content = (
    '"""\n'
    'app/hitl/manager.py\n'
    'Human-in-the-Loop manager stub.\n'
    '"""\n\n'
    'from __future__ import annotations\n'
    'import logging\n'
    'from typing import Any, Optional\n\n'
    'logger = logging.getLogger(__name__)\n\n\n'
    'async def push_to_run(\n'
    '    run_id: str,\n'
    '    event_type: str,\n'
    '    data: Optional[Any] = None,\n'
    ') -> None:\n'
    '    """Pushes a HITL event to the run queue (stub — logs only)."""\n'
    '    logger.debug(\n'
    '        f"hitl push_to_run: {event_type}",\n'
    '        extra={"run_id": run_id, "data": data},\n'
    '    )\n\n\n'
    'class HITLManager:\n'
    '    """HITL Manager stub."""\n\n'
    '    async def push(self, run_id: str, event_type: str, data: Any = None) -> None:\n'
    '        await push_to_run(run_id, event_type, data)\n\n\n'
    'hitl_manager = HITLManager()\n'
)

os.makedirs("app/hitl", exist_ok=True)
with open("app/hitl/__init__.py", "a", encoding="utf-8") as f:
    pass
with open("app/hitl/manager.py", "w", encoding="utf-8") as f:
    f.write(hitl_content)
print("Done - app/hitl/manager.py written")