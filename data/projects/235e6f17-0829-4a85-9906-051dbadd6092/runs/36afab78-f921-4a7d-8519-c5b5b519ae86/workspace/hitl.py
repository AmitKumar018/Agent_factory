import asyncio
import re
from typing import Dict, Any
from fastapi import WebSocket
from sse_starlette.sse import EventSourceResponse
from langgraph import LangGraph
from langgraph.prebuilt import ToolNode
from tools import ToolException
import logging

logger = logging.getLogger(__name__)

UUID_REGEX = re.compile(
    r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$', re.IGNORECASE)

class HITLApproval:
    def __init__(self, graph: LangGraph):
        self.graph = graph
        self.approval_queue = asyncio.Queue()

    def validate_task_id(self, task_id: str) -> None:
        """Validate the task ID format."""
        if not task_id or not UUID_REGEX.match(task_id):
            raise ToolException("Invalid task ID format. Expected a UUID.")

    async def pause_for_approval(self, task_id: str) -> Dict[str, Any]:
        """Pause the graph execution for human approval."""
        try:
            self.validate_task_id(task_id)
            await self.graph.pause(task_id)
            logger.info(f"Graph paused for task {task_id} awaiting approval.")
            return {"status": "paused", "task_id": task_id}
        except ToolException as te:
            logger.error("Validation error occurred during task pause.")
            raise
        except Exception:
            logger.error("Failed to pause graph due to an internal error.")
            raise ToolException("Failed to pause graph due to an internal error.")

    async def resume_graph(self, task_id: str) -> Dict[str, Any]:
        """Resume the graph execution after approval."""
        try:
            self.validate_task_id(task_id)
            await self.graph.resume(task_id)
            logger.info(f"Graph resumed for task {task_id}.")
            return {"status": "resumed", "task_id": task_id}
        except ToolException as te:
            logger.error("Validation error occurred during task resume.")
            raise
        except Exception:
            logger.error("Failed to resume graph due to an internal error.")
            raise ToolException("Failed to resume graph due to an internal error.")

    async def send_sse_events(self, request) -> EventSourceResponse:
        """Emit Server-Sent Events for HITL progress."""
        async def event_generator():
            while True:
                # Simulate event emission
                await asyncio.sleep(1)
                yield {"event": "progress", "data": "HITL event data"}
        
        return EventSourceResponse(event_generator())

    async def websocket_handler(self, websocket: WebSocket) -> None:
        """Handle WebSocket connections for HITL messages."""
        await websocket.accept()
        try:
            while True:
                data = await websocket.receive_text()
                # Process incoming data
                await websocket.send_text(f"Received: {data}")
        except Exception:
            logger.error("WebSocket error occurred.")
        finally:
            await websocket.close()

# Example tool registration
tools = [
    HITLApproval.pause_for_approval,
    HITLApproval.resume_graph
]

# Instantiate ToolNode with the registered tools
tool_node = ToolNode(tools=tools)