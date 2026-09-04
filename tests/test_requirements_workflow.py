import asyncio
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
from app.observability.models import RunEvent
from app.projects.models import Project
from app.storage.database import AsyncSessionLocal, init_db
from app.storage.file_store import FileStore
from app.storage.models import Run, RunStatus
from app.workflows.models import Approval, Clarification
from app.workflows.requirements.graph import build_requirements_graph, get_graph_png_bytes
from app.workflows.requirements.state import RequirementsState

BRD_TEXT = b"""
Business Requirements Document

Overview
The platform shall process uploaded business documents and extract requirements.
All processing must complete within 30 seconds per document.

Functional Requirements
The system shall support TXT and Markdown BRD uploads.
Users shall be able to approve generated requirements before planning begins.
The system shall save requirements.md and requirements.json artifacts.

Non-Functional Requirements
The workflow must emit replayable progress events.
The workflow must resume from a human approval decision.
"""


async def _register_and_login(client: AsyncClient, username: str) -> str:
    password = "phase5_password_123"
    response = await client.post("/auth/register", json={"username": username, "password": password})
    if response.status_code == 201:
        return response.json()["access_token"]
    if response.status_code == 409:
        response = await client.post("/auth/login", json={"username": username, "password": password})
        assert response.status_code == 200, response.text
        return response.json()["access_token"]
    raise AssertionError(response.text)


async def _create_project(client: AsyncClient, headers: dict, name: str) -> str:
    response = await client.post("/projects", headers=headers, json={"name": name, "description": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _poll_document_ready(client: AsyncClient, headers: dict, project_id: str, document_id: str) -> dict:
    for _ in range(30):
        response = await client.get(f"/projects/{project_id}/documents/{document_id}", headers=headers)
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] in ("ready", "failed"):
            return payload
        await asyncio.sleep(0.1)
    raise AssertionError("document did not finish parsing")


async def _poll_run_status(client: AsyncClient, headers: dict, project_id: str, run_id: str, expected: str) -> dict:
    for _ in range(40):
        response = await client.get(f"/projects/{project_id}/runs/{run_id}", headers=headers)
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] == expected:
            return payload
        await asyncio.sleep(0.1)
    raise AssertionError(f"run did not reach {expected}")


async def _cleanup(project_id: str, run_id: str | None = None, user_id: str | None = None, document_id: str | None = None):
    async with AsyncSessionLocal() as db:
        if run_id:
            await db.execute(delete(RunEvent).where(RunEvent.run_id == run_id))
            await db.execute(delete(Approval).where(Approval.run_id == run_id))
            await db.execute(delete(Clarification).where(Clarification.run_id == run_id))
            await db.execute(delete(Run).where(Run.id == run_id))
        if document_id:
            await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
            await db.execute(delete(Document).where(Document.id == document_id))
        await db.execute(delete(Project).where(Project.id == project_id))
        if user_id:
            await db.execute(delete(User).where(User.id == user_id))
        await db.commit()

    path = FileStore().project_root(project_id)
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


@pytest.mark.asyncio
async def test_requirements_graph_generates_artifacts_and_resumes_approval():
    await init_db()
    user_id = f"user-phase5-{uuid.uuid4()}"
    project_id = f"proj-phase5-{uuid.uuid4()}"
    document_id = f"doc-phase5-{uuid.uuid4()}"
    run_id = f"run-phase5-{uuid.uuid4()}"

    async with AsyncSessionLocal() as db:
        db.add(User(id=user_id, username=user_id, hashed_password="x"))
        db.add(Project(id=project_id, owner_id=user_id, name="Phase 5"))
        db.add(
            Document(
                id=document_id,
                project_id=project_id,
                filename="brd.txt",
                extension="txt",
                mime_type="text/plain",
                file_size=len(BRD_TEXT),
                content_hash=uuid.uuid4().hex,
                file_path="unused.txt",
                kind=DocumentKind.BRD,
                status=ParseStatus.READY,
                section_count=1,
                chunk_count=1,
            )
        )
        db.add(
            DocumentChunk(
                id=f"{document_id}:0",
                document_id=document_id,
                project_id=project_id,
                section_id="0",
                section_title="Business Requirements",
                text=BRD_TEXT.decode(),
                page=0,
                chunk_index=0,
                kind="BRD",
            )
        )
        db.add(
            Run(
                id=run_id,
                project_id=project_id,
                workflow_type="requirements",
                status=RunStatus.RUNNING,
                input_data={"document_ids": [document_id]},
                output_data={},
                run_metadata={},
                started_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

        async with AsyncSqliteSaver.from_conn_string(":memory:") as checkpointer:
            graph = build_requirements_graph(db, run_id, project_id, checkpointer=checkpointer)
            config = {"configurable": {"thread_id": run_id}}
            state = RequirementsState(run_id=run_id, project_id=project_id, document_ids=[document_id])

            await graph.ainvoke(state, config=config)
            snapshot = await graph.aget_state(config)
            assert snapshot.next == ("approval_gate",)

            run = await db.get(Run, run_id)
            assert run.status == RunStatus.PAUSED_HITL
            assert run.pending_hitl_type == "approval"
            assert run.output_data["artifact_paths"]["requirements_md"]
            assert os.path.exists(run.output_data["artifact_paths"]["requirements_md"])
            assert os.path.exists(run.output_data["artifact_paths"]["requirements_json"])

            await graph.ainvoke(Command(resume={"decision": "approve"}), config=config)
            await db.refresh(run)
            assert run.status == RunStatus.COMPLETED
            assert run.output_data["approved"] is True

    await _cleanup(project_id, run_id=run_id, user_id=user_id, document_id=document_id)


def test_requirements_graph_png_bytes_are_valid_png():
    png = get_graph_png_bytes()
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.asyncio
async def test_graph_png_endpoint_returns_png():
    await init_db()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/workflows/requirements/graph.png")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.asyncio
async def test_brd_upload_can_generate_and_approve_requirements_end_to_end():
    await init_db()
    username = f"phase5_api_{uuid.uuid4().hex}"
    project_id = None
    run_id = None
    document_id = None

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await _register_and_login(client, username)
        headers = {"Authorization": f"Bearer {token}"}
        project_id = await _create_project(client, headers, "Phase 5 API")

        upload = await client.post(
            f"/projects/{project_id}/documents?kind=BRD",
            headers=headers,
            files={"file": ("brd.txt", BRD_TEXT, "text/plain")},
        )
        assert upload.status_code in (200, 202), upload.text
        document_id = upload.json()["document_id"]
        final_doc = await _poll_document_ready(client, headers, project_id, document_id)
        assert final_doc["status"] == "ready", final_doc

        trigger = await client.post(
            f"/projects/{project_id}/workflows/requirements",
            headers=headers,
            json={"document_ids": [document_id]},
        )
        assert trigger.status_code == 202, trigger.text
        run_id = trigger.json()["run_id"]

        paused = await _poll_run_status(client, headers, project_id, run_id, "paused_hitl")
        assert paused["output_data"]["artifact_paths"]["requirements_md"]

        approval = await client.post(f"/projects/{project_id}/runs/{run_id}/approve", headers=headers)
        assert approval.status_code == 200, approval.text
        completed = await _poll_run_status(client, headers, project_id, run_id, "completed")
        assert completed["output_data"]["approved"] is True

        md = await client.get(f"/projects/{project_id}/runs/{run_id}/artifacts/requirements.md", headers=headers)
        js = await client.get(f"/projects/{project_id}/runs/{run_id}/artifacts/requirements.json", headers=headers)
        assert md.status_code == 200, md.text
        assert js.status_code == 200, js.text
        assert "Functional Requirements" in md.text
        assert "functional_requirements" in js.text

    if project_id:
        user = None
        async with AsyncSessionLocal() as db:
            user = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
        await _cleanup(project_id, run_id=run_id, user_id=user.id if user else None, document_id=document_id)