import os

hitl_content = (
    '"""\n'
    'app/hitl/manager.py\n'
    'Human-in-the-Loop manager stub.\n'
    '"""\n\n'
    'from __future__ import annotations\n'
    'import logging\n'
    'from typing import Any, Optional, Dict\n\n'
    'logger = logging.getLogger(__name__)\n\n\n'
    'async def push_to_run(\n'
    '    run_id: str,\n'
    '    event_type: str,\n'
    '    data: Optional[Any] = None,\n'
    ') -> None:\n'
    '    """Pushes a HITL event to the run queue (stub)."""\n'
    '    logger.debug(f"hitl push_to_run: {event_type}", extra={"run_id": run_id, "data": data})\n\n\n'
    'async def deliver_client_response(\n'
    '    run_id: str,\n'
    '    response: Optional[Dict[str, Any]] = None,\n'
    ') -> None:\n'
    '    """Delivers a client HITL response back to a waiting run (stub)."""\n'
    '    logger.debug(f"hitl deliver_client_response", extra={"run_id": run_id, "response": response})\n\n\n'
    'async def request_clarification(\n'
    '    run_id: str,\n'
    '    questions: Optional[Any] = None,\n'
    ') -> None:\n'
    '    """Sends a clarification request to the client (stub)."""\n'
    '    logger.debug(f"hitl request_clarification", extra={"run_id": run_id, "questions": questions})\n\n\n'
    'async def request_approval(\n'
    '    run_id: str,\n'
    '    payload: Optional[Any] = None,\n'
    ') -> None:\n'
    '    """Sends an approval request to the client (stub)."""\n'
    '    logger.debug(f"hitl request_approval", extra={"run_id": run_id, "payload": payload})\n\n\n'
    'class HITLManager:\n'
    '    """HITL Manager stub."""\n\n'
    '    async def push(self, run_id: str, event_type: str, data: Any = None) -> None:\n'
    '        await push_to_run(run_id, event_type, data)\n\n'
    '    async def deliver(self, run_id: str, response: Any = None) -> None:\n'
    '        await deliver_client_response(run_id, response)\n\n\n'
    'hitl_manager = HITLManager()\n'
)

os.makedirs("app/hitl", exist_ok=True)
with open("app/hitl/__init__.py", "a", encoding="utf-8") as f:
    pass
with open("app/hitl/manager.py", "w", encoding="utf-8") as f:
    f.write(hitl_content)
print("Done - app/hitl/manager.py updated with all stubs")