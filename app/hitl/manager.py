

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, Dict

logger = logging.getLogger(__name__)




class _RunHITLState:
    """
    Holds all in-memory HITL coordination state for a single run.

    outbound_queue:
        asyncio.Queue of dicts — messages the graph wants to send to the
        client. The WebSocket handler is the sole consumer.

    response_event:
        asyncio.Event that is set() when the client delivers a response.
        The graph task awaits this event via wait_for_response().

    pending_response:
        The actual response payload stored by deliver_response() before
        setting the event. Cleared after wait_for_response() reads it.

    websocket:
        The active FastAPI WebSocket object. None if no client is connected.
    """

    def __init__(self) -> None:
        self.outbound_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self.response_event: asyncio.Event = asyncio.Event()
        self.pending_response: Optional[Dict[str, Any]] = None
        self.websocket: Optional[Any] = None  # starlette.websockets.WebSocket




class HITLManager:
    """
    Process-singleton that manages HITL state for all active runs.

    Keyed by run_id (str). Entries are created lazily on first access
    and cleaned up explicitly via clear_run().
    """

    def __init__(self) -> None:
        self._runs: Dict[str, _RunHITLState] = {}

    

    def _get_or_create(self, run_id: str) -> _RunHITLState:
        if run_id not in self._runs:
            self._runs[run_id] = _RunHITLState()
            logger.debug("hitl_state_created", extra={"run_id": run_id})
        return self._runs[run_id]

    

    def register_connection(self, run_id: str, websocket: Any) -> None:
        """
        Register an active WebSocket connection for a run.

        If a previous connection object exists it is simply overwritten —
        the caller (websocket.py) is responsible for closing the old socket
        before calling this (enforces single-subscriber rule).
        """
        state = self._get_or_create(run_id)
        state.websocket = websocket
        logger.info("hitl_connection_registered", extra={"run_id": run_id})

    def unregister_connection(self, run_id: str) -> None:
        """
        Remove the WebSocket reference for a run.

        Called on disconnect. The run state (queue, event) is preserved
        so the graph can continue to enqueue messages — they will be
        delivered when the client reconnects.
        """
        if run_id in self._runs:
            self._runs[run_id].websocket = None
            logger.info("hitl_connection_unregistered", extra={"run_id": run_id})

    def get_connection(self, run_id: str) -> Optional[Any]:
        """Return the active WebSocket for a run, or None if disconnected."""
        state = self._runs.get(run_id)
        return state.websocket if state else None

    def has_connection(self, run_id: str) -> bool:
        """True if a WebSocket client is currently connected for this run."""
        return self.get_connection(run_id) is not None

    

    def get_queue(self, run_id: str) -> asyncio.Queue:
        """
        Return the outbound message queue for a run (creates if absent).

        The WebSocket handler calls this once on connect and then loops:
            while True:
                msg = await manager.get_queue(run_id).get()
                await websocket.send_json(msg)
        """
        return self._get_or_create(run_id).outbound_queue

    async def push_to_run(
        self,
        run_id: str,
        event_type: str,
        data: Optional[Any] = None,
    ) -> None:
        """
        Enqueue an outbound message for delivery to the WebSocket client.

        Called by graph nodes (via the module-level helper below) when they
        need to send a clarification_request, approval_request, error, or
        run_completed message to the human.

        If no client is currently connected the message sits in the queue
        and is delivered when the client reconnects — the queue is bounded
        only by memory (acceptable for assignment scope; production would
        cap queue depth).
        """
        state = self._get_or_create(run_id)
        message = {"type": event_type, "data": data or {}}
        await state.outbound_queue.put(message)
        logger.debug(
            "hitl_message_enqueued",
            extra={"run_id": run_id, "event_type": event_type},
        )

    

    async def wait_for_response(
        self,
        run_id: str,
        timeout: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Block until the WebSocket client delivers a response, then return it.

        Called by the graph background task after pushing a request:

            await manager.push_to_run(run_id, "clarification_request", payload)
            response = await manager.wait_for_response(run_id, timeout=300)
            if response is None:
                # timeout — fail the run

        The event is cleared after reading so the next wait_for_response()
        call on the same run_id starts fresh.

        Returns the response dict, or None on timeout.
        """
        state = self._get_or_create(run_id)

        # Clear any leftover event state from a previous round
        state.response_event.clear()
        state.pending_response = None

        try:
            await asyncio.wait_for(state.response_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "hitl_response_timeout",
                extra={"run_id": run_id, "timeout_s": timeout},
            )
            return None

        response = state.pending_response
        state.pending_response = None
        state.response_event.clear()

        logger.debug(
            "hitl_response_received",
            extra={"run_id": run_id, "response_type": (response or {}).get("type")},
        )
        return response

    async def deliver_response(
        self,
        run_id: str,
        response: Dict[str, Any],
    ) -> None:
        """
        Store a client response and wake the waiting graph task.

        Called by the WebSocket handler when it receives a
        clarification_response or approval_response message from the client.

        If no graph task is waiting (wait_for_response was not called yet),
        the response is stored and the event is set — when the graph task
        eventually calls wait_for_response() it will clear the event,
        reset pending_response=None, then re-await. This means a response
        delivered before wait_for_response() is called will be lost.

        In practice this race cannot occur because the graph always calls
        push_to_run() before wait_for_response(), and the client cannot
        respond before receiving the request — so the event.wait() is
        always registered before deliver_response() fires.
        """
        state = self._get_or_create(run_id)
        state.pending_response = response
        state.response_event.set()
        logger.info(
            "hitl_response_delivered",
            extra={"run_id": run_id, "response_type": response.get("type")},
        )

    

    def clear_run(self, run_id: str) -> None:
        """
        Remove all HITL state for a completed or failed run.

        Called by the graph background task in the finally block after
        the run ends (success, failure, or cancellation). This prevents
        unbounded memory growth in long-running processes.
        """
        if run_id in self._runs:
            del self._runs[run_id]
            logger.info("hitl_state_cleared", extra={"run_id": run_id})

    def active_run_ids(self) -> list[str]:
        """Return run_ids currently tracked (useful for health checks / debug)."""
        return list(self._runs.keys())




hitl_manager = HITLManager()



async def push_to_run(
    run_id: str,
    event_type: str,
    data: Optional[Any] = None,
) -> None:
    """Push an outbound HITL message to the client queue for a run."""
    await hitl_manager.push_to_run(run_id, event_type, data)


async def wait_for_response(
    run_id: str,
    timeout: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Block until the client delivers a response for this run."""
    return await hitl_manager.wait_for_response(run_id, timeout=timeout)


async def deliver_response(
    run_id: str,
    response: Dict[str, Any],
) -> None:
    """Deliver a client response to the waiting graph task."""
    await hitl_manager.deliver_response(run_id, response)


async def clear_run(run_id: str) -> None:
    """Clean up all HITL state for a finished run."""
    hitl_manager.clear_run(run_id)


def get_queue(run_id: str) -> asyncio.Queue:
    """Return the outbound queue for a run (used by the WebSocket handler)."""
    return hitl_manager.get_queue(run_id)


def register_connection(run_id: str, websocket: Any) -> None:
    """Register an active WebSocket connection."""
    hitl_manager.register_connection(run_id, websocket)


def unregister_connection(run_id: str) -> None:
    """Unregister the WebSocket connection on disconnect."""
    hitl_manager.unregister_connection(run_id)


def get_connection(run_id: str) -> Optional[Any]:
    """Return the active WebSocket for a run, or None."""
    return hitl_manager.get_connection(run_id)
