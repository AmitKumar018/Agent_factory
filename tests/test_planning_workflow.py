import asyncio
import json
import os
import shutil
import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from sqlalchemy import delete, select

from app.auth.models import User
from app.documents.models import Document, DocumentChunk, DocumentKind, ParseStatus
from app.main import app
from app.observability.models import AuditLog, RunEvent
from app.projects.models import Project
from app.storage.database import AsyncSessionLocal, init_db
from app.storage.file_store import FileStore
from app.storage.models import Run, RunStatus
from app.workflows.models import Approval, Task
from app.workflows.planning.graph import build_planning_graph
from app.workflows.planning.state import PlanningState

REQ_DOC = {
    "goals": ["Build a document-driven agent factory that plans implementation from approved requirements."],
    "functional_requirements": [
        {"id": "FR-001", "title": "Document ingestion", "description": "The system shall use uploaded BRD chunks as project-scoped RAG context.", "priority": "high"},
        {"id": "FR-002", "title": "Pattern selection", "description": "The system shall select global agentic design patterns for planning.", "priority": "high"},
        {"id": "FR-003", "title": "Architecture generation", "description": "The system shall generate architecture artifacts for implementation.", "priority": "high"},
        {"id": "FR-004", "title": "Task planning", "description": "The system shall persist ordered implementation tasks with acceptance criteria.", "priority": "high"},
        {"id": "FR-005", "title": "Human approval", "description": "The planning workflow shall pause for HITL approval and resume using Command resume.", "priority": "high"},
        {"id": "FR-006", "title": "Progress events", "description": "The workflow shall emit replayable SSE progress events.", "priority": "medium"},
    ],
    "non_functional_requirements": [
        {"id": "NFR-001", "title": "Project isolation", "description": "Document retrieval must be filtered by project_id.", "priority": "high"}
    ],
    "constraints": ["External web search may run through a stub in offline environments."],
}

DOC_TEXT = """
Business Requirements Document
The platform must parse uploaded BRD documents, use project-scoped Chroma retrieval,
select agentic design patterns, generate architecture, create implementation tasks,
and pause for human approval before persisting the plan.
"""


async def register_and_login(client: AsyncClient, username: str) -> str:
    password = "phase6_password_123"
    response = await client.post("/auth/register", json={"username": username, "password": password})
    if response.status_code == 201:
        return response.json()["access_token"]
    response = await client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


async def create_project(client: AsyncClient, headers: dict, name: str) -> str:
    response = await client.post("/projects", headers=headers, json={"name": name, "description": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def poll_planning(client: AsyncClient, headers: dict, project_id: str, run_id: str, expected: str) -> dict:
    for _ in range(50):
        response = await client.get(f"/projects/{project_id}/runs/{run_id}/planning", headers=headers)
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] == expected:
            return payload
        await asyncio.sleep(0.1)
    raise AssertionError(f"planning run did not reach {expected}")


async def cleanup(project_id: str, user_id: str | None = None):
    async with AsyncSessionLocal() as db:
        run_ids = [row[0] for row in (await db.execute(select(Run.id).where(Run.project_id == project_id))).all()]
        for run_id in run_ids:
            await db.execute(delete(RunEvent).where(RunEvent.run_id == run_id))
            await db.execute(delete(AuditLog).where(AuditLog.run_id == run_id))
            await db.execute(delete(Approval).where(Approval.run_id == run_id))
            await db.execute(delete(Task).where(Task.run_id == run_id))
        await db.execute(delete(DocumentChunk).where(DocumentChunk.project_id == project_id))
        await db.execute(delete(Document).where(Document.project_id == project_id))
        await db.execute(delete(Run).where(Run.project_id == project_id))
        await db.execute(delete(Project).where(Project.id == project_id))
        if user_id:
            await db.execute(delete(User).where(User.id == user_id))
        await db.commit()
    path = FileStore().project_root(project_id)
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


async def seed_project_with_requirements(project_id: str, user_id: str, req_run_id: str, planning_run_id: str | None = None):
    doc_id = f"doc-phase6-{uuid.uuid4()}"
    async with AsyncSessionLocal() as db:
        db.add(User(id=user_id, username=user_id, hashed_password="x"))
        db.add(Project(id=project_id, owner_id=user_id, name="Phase 6"))
        db.add(Document(id=doc_id, project_id=project_id, filename="brd.txt", extension="txt", mime_type="text/plain", file_size=len(DOC_TEXT), content_hash=uuid.uuid4().hex, file_path="unused.txt", kind=DocumentKind.BRD, status=ParseStatus.READY, section_count=1, chunk_count=1))
        db.add(DocumentChunk(id=f"{doc_id}:0", document_id=doc_id, project_id=project_id, section_id="section0", section_title="Planning Requirements", text=DOC_TEXT, page=0, chunk_index=0, kind="BRD"))
        db.add(Run(id=req_run_id, project_id=project_id, workflow_type="requirements", status=RunStatus.COMPLETED, input_data={"document_ids": [doc_id]}, output_data={"approved": True, "requirements": REQ_DOC, "artifact_paths": {}}, run_metadata={}, started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc)))
        if planning_run_id:
            db.add(Run(id=planning_run_id, project_id=project_id, workflow_type="planning", status=RunStatus.RUNNING, input_data={"requirements_run_id": req_run_id}, output_data={}, run_metadata={}, started_at=datetime.now(timezone.utc)))
        await db.commit()


@pytest.mark.asyncio
async def test_planning_graph_pauses_resumes_and_persists_artifacts():
    await init_db()
    user_id = f"user-phase6-{uuid.uuid4()}"
    project_id = f"proj-phase6-{uuid.uuid4()}"
    req_run_id = f"req-phase6-{uuid.uuid4()}"
    planning_run_id = f"plan-phase6-{uuid.uuid4()}"
    await seed_project_with_requirements(project_id, user_id, req_run_id, planning_run_id)

    async with AsyncSqliteSaver.from_conn_string(":memory:") as checkpointer:
        graph = build_planning_graph(checkpointer)
        config = {"configurable": {"thread_id": planning_run_id}}
        state = PlanningState(project_id=project_id, run_id=planning_run_id, requirements_run_id=req_run_id)
        await graph.ainvoke(state, config=config)
        snapshot = await graph.aget_state(config)
        assert snapshot.next == ("approval_gate",)

        async with AsyncSessionLocal() as db:
            run = await db.get(Run, planning_run_id)
            assert run.status == RunStatus.PAUSED_HITL
            assert run.pending_hitl_type == "approval"
            assert run.pending_hitl_request_id
            assert (await db.execute(select(Approval).where(Approval.run_id == planning_run_id))).scalar_one().status == "pending"

        await graph.ainvoke(Command(resume={"decision": "approve", "feedback": "Looks good"}), config=config)

    async with AsyncSessionLocal() as db:
        run = await db.get(Run, planning_run_id)
        assert run.status == RunStatus.COMPLETED
        assert run.output_data["approved"] is True
        assert run.output_data["architecture"]["components"]
        assert run.output_data["selected_patterns"]
        assert run.output_data["research"]["findings"]
        assert len(run.output_data["tasks"]) >= 6
        for path in run.output_data["artifact_paths"].values():
            assert os.path.exists(path)
        task_rows = (await db.execute(select(Task).where(Task.run_id == planning_run_id))).scalars().all()
        assert len(task_rows) == len(run.output_data["tasks"])
        assert all(task.title != "Task" for task in task_rows)
        events = (await db.execute(select(RunEvent).where(RunEvent.run_id == planning_run_id))).scalars().all()
        assert any(event.event_type == "approval_requested" for event in events)
        assert any(event.event_type == "run_completed" for event in events)

    await cleanup(project_id, user_id=user_id)


@pytest.mark.asyncio
async def test_planning_graph_rejects_replans_and_persists_feedback_task():
    await init_db()
    user_id = f"user-phase6-reject-{uuid.uuid4()}"
    project_id = f"proj-phase6-reject-{uuid.uuid4()}"
    req_run_id = f"req-phase6-reject-{uuid.uuid4()}"
    planning_run_id = f"plan-phase6-reject-{uuid.uuid4()}"
    feedback = "Add a task that explicitly covers audit event replay."
    await seed_project_with_requirements(project_id, user_id, req_run_id, planning_run_id)

    async with AsyncSqliteSaver.from_conn_string(":memory:") as checkpointer:
        graph = build_planning_graph(checkpointer)
        config = {"configurable": {"thread_id": planning_run_id}}
        state = PlanningState(project_id=project_id, run_id=planning_run_id, requirements_run_id=req_run_id)
        await graph.ainvoke(state, config=config)

        async with AsyncSessionLocal() as db:
            run = await db.get(Run, planning_run_id)
            first_request_id = run.pending_hitl_request_id
            assert first_request_id

        await graph.ainvoke(Command(resume={"decision": "reject", "feedback": feedback}), config=config)
        snapshot = await graph.aget_state(config)
        assert snapshot.next == ("approval_gate",)

        async with AsyncSessionLocal() as db:
            run = await db.get(Run, planning_run_id)
            second_request_id = run.pending_hitl_request_id
            assert run.status == RunStatus.PAUSED_HITL
            assert second_request_id
            assert second_request_id != first_request_id
            approvals = (await db.execute(select(Approval).where(Approval.run_id == planning_run_id))).scalars().all()
            assert any(a.request_id == first_request_id and a.decision == "reject" and a.feedback == feedback for a in approvals)
            assert any(a.request_id == second_request_id and a.status == "pending" for a in approvals)

        await graph.ainvoke(Command(resume={"decision": "approve", "feedback": "Revised plan approved"}), config=config)

    async with AsyncSessionLocal() as db:
        run = await db.get(Run, planning_run_id)
        assert run.status == RunStatus.COMPLETED
        assert run.output_data["implementation_plan_approved"] is True
        assert run.output_data["critic_passed"] is True
        assert run.output_data["snapshotted_patterns"]
        assert "pattern_snapshots_json" in run.output_data["artifact_paths"]
        assert os.path.exists(run.output_data["artifact_paths"]["pattern_snapshots_json"])
        with open(run.output_data["artifact_paths"]["selected_patterns_json"], "r", encoding="utf-8") as f:
            selected_patterns_artifact = json.load(f)
        assert selected_patterns_artifact["snapshots"] == run.output_data["snapshotted_patterns"]
        assert any(task["title"] == "Address planning approval feedback" and feedback in task["description"] for task in run.output_data["tasks"])
        events = (await db.execute(select(RunEvent).where(RunEvent.run_id == planning_run_id))).scalars().all()
        assert sum(1 for event in events if event.event_type == "approval_requested") == 2

    await cleanup(project_id, user_id=user_id)


@pytest.mark.asyncio
async def test_planning_api_approved_requirements_to_approved_plan():
    await init_db()
    username = f"phase6_api_{uuid.uuid4().hex}"
    req_run_id = f"req-phase6-api-{uuid.uuid4()}"
    project_id = None
    user_id = None

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await register_and_login(client, username)
        headers = {"Authorization": f"Bearer {token}"}
        project_id = await create_project(client, headers, "Phase 6 API")
        async with AsyncSessionLocal() as db:
            user = (await db.execute(select(User).where(User.username == username))).scalar_one()
            user_id = user.id
            doc_id = f"doc-phase6-api-{uuid.uuid4()}"
            db.add(Document(id=doc_id, project_id=project_id, filename="brd.txt", extension="txt", mime_type="text/plain", file_size=len(DOC_TEXT), content_hash=uuid.uuid4().hex, file_path="unused.txt", kind=DocumentKind.BRD, status=ParseStatus.READY, section_count=1, chunk_count=1))
            db.add(DocumentChunk(id=f"{doc_id}:0", document_id=doc_id, project_id=project_id, section_id="section0", section_title="Planning Requirements", text=DOC_TEXT, page=0, chunk_index=0, kind="BRD"))
            db.add(Run(id=req_run_id, project_id=project_id, workflow_type="requirements", status=RunStatus.COMPLETED, input_data={"document_ids": [doc_id]}, output_data={"approved": True, "requirements": REQ_DOC, "artifact_paths": {}}, run_metadata={}))
            await db.commit()

        trigger = await client.post(f"/projects/{project_id}/workflows/planning", headers=headers, json={"requirements_run_id": req_run_id})
        assert trigger.status_code == 202, trigger.text
        planning_run_id = trigger.json()["run_id"]
        paused = await poll_planning(client, headers, project_id, planning_run_id, "paused_hitl")
        assert paused["pending_hitl_request_id"]

        approval = await client.post(f"/projects/{project_id}/runs/{planning_run_id}/planning/approve", headers=headers, json={})
        assert approval.status_code == 200, approval.text
        await poll_planning(client, headers, project_id, planning_run_id, "completed")

        tasks = await client.get(f"/projects/{project_id}/runs/{planning_run_id}/tasks", headers=headers)
        assert tasks.status_code == 200, tasks.text
        assert tasks.json()["total"] >= 6
        research = await client.get(f"/projects/{project_id}/runs/{planning_run_id}/research", headers=headers)
        assert research.status_code == 200, research.text
        assert research.json()["research_available"] is True
        assert research.json()["total_findings"] >= 1

    if project_id:
        await cleanup(project_id, user_id=user_id)
