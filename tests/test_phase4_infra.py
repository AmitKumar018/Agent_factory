import uuid
from datetime import datetime, timezone

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from sqlalchemy import delete, select

from app.auth.models import User
from app.observability.models import RunEvent
from app.projects.models import Project
from app.sse.manager import emit_event, list_events
from app.storage.database import AsyncSessionLocal, init_db
from app.storage.models import Run, RunStatus
from app.workflows.codegen.nodes import request_approval
from app.workflows.codegen.state import OrchestratorState
from app.workflows.models import Approval


@pytest.mark.asyncio
async def test_run_events_are_persisted_and_replayable():
    await init_db()
    project_id = f"proj-phase4-{uuid.uuid4()}"
    run_id = f"run-phase4-{uuid.uuid4()}"

    await emit_event("node_started", {"node": "alpha"}, run_id=run_id, project_id=project_id)
    await emit_event("node_completed", {"node": "alpha"}, run_id=run_id, project_id=project_id)

    events = await list_events(project_id=project_id, run_id=run_id, after=0)
    assert [event.event_type for event in events] == ["node_started", "node_completed"]
    assert [event.sequence for event in events] == [1, 2]

    replay = await list_events(project_id=project_id, run_id=run_id, after=1)
    assert [event.event_type for event in replay] == ["node_completed"]

    async with AsyncSessionLocal() as db:
        await db.execute(delete(RunEvent).where(RunEvent.run_id == run_id))
        await db.commit()


@pytest.mark.asyncio
async def test_codegen_approval_interrupt_resumes_with_command():
    await init_db()
    user_id = f"user-phase4-{uuid.uuid4()}"
    project_id = f"proj-phase4-{uuid.uuid4()}"
    run_id = f"run-phase4-{uuid.uuid4()}"

    async with AsyncSessionLocal() as db:
        db.add(User(id=user_id, username=user_id, hashed_password="x"))
        db.add(Project(id=project_id, owner_id=user_id, name="Phase 4"))
        db.add(
            Run(
                id=run_id,
                project_id=project_id,
                workflow_type="codegen",
                status=RunStatus.RUNNING,
                input_data={},
                output_data={},
                run_metadata={},
                started_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    graph = StateGraph(OrchestratorState)
    graph.add_node("request_approval", request_approval)
    graph.add_edge(START, "request_approval")
    graph.add_edge("request_approval", END)

    async with AsyncSqliteSaver.from_conn_string(":memory:") as checkpointer:
        app = graph.compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": run_id}}
        state = {
            "run_id": run_id,
            "project_id": project_id,
            "architecture_run_id": "arch-1",
            "tasks": [{"id": "task-1", "title": "Task", "status": "completed"}],
            "completed_task_ids": ["task-1"],
            "failed_task_ids": [],
            "bundle_path": "bundle.zip",
            "manifest": {"all_files": ["app.py"]},
            "hitl_request_id": None,
            "error": None,
        }

        first = await app.ainvoke(state, config=config)
        snapshot = await app.aget_state(config)
        assert snapshot.next == ("request_approval",)
        assert first["hitl_request_id"] is None

        async with AsyncSessionLocal() as db:
            run = await db.get(Run, run_id)
            assert run.status.value == "paused_hitl"
            request_id = run.pending_hitl_request_id
            assert request_id

        resumed = await app.ainvoke(
            Command(resume={"approved": True, "feedback": None}),
            config=config,
        )
        assert resumed["approval_decision"] == "approve"
        assert resumed["hitl_request_id"] is None

    async with AsyncSessionLocal() as db:
        approval = (
            await db.execute(select(Approval).where(Approval.run_id == run_id))
        ).scalar_one()
        assert approval.status == "approved"
        await db.execute(delete(RunEvent).where(RunEvent.run_id == run_id))
        await db.execute(delete(Approval).where(Approval.run_id == run_id))
        await db.execute(delete(Run).where(Run.id == run_id))
        await db.execute(delete(Project).where(Project.id == project_id))
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()