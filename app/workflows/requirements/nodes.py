from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Iterable, List

from langgraph.types import interrupt
from sqlalchemy import select

from app.config import settings
from app.documents.models import Document, DocumentChunk
from app.hitl.manager import push_to_run
from app.hooks.node_hooks import node_hook, record_usage
from app.observability.models import AuditLog
from app.sse.manager import emit_event
from app.storage.file_store import FileStore
from app.storage.models import Run, RunStatus
from app.workflows.models import Approval, Clarification
from app.workflows.requirements.state import (
    ClarificationQA,
    Gap,
    RequirementItem,
    RequirementsDocument,
    RequirementsState,
)

logger = logging.getLogger(__name__)

FUNCTIONAL_KEYWORDS = (
    "shall", "must", "should", "allow", "allows", "enable", "enables",
    "support", "supports", "provide", "provides", "create", "generate",
    "upload", "parse", "search", "approve", "resume", "authenticate", "integrate",
)

NON_FUNCTIONAL_KEYWORDS = (
    "performance", "latency", "seconds", "minutes", "availability", "scalable",
    "concurrent", "security", "encrypt", "audit", "compliance", "cost", "token",
    "rate limit", "retry", "error", "authorized",
)

OPEN_QUESTION_MARKERS = (
    "tbd", "to be decided", "to be defined", "unknown", "clarify",
    "needs clarification", "open question",
)


async def _load_document_chunks(db, project_id: str, document_ids: list[str]) -> dict[str, list[DocumentChunk]]:
    chunks_by_doc: dict[str, list[DocumentChunk]] = {doc_id: [] for doc_id in document_ids}
    if not document_ids:
        return chunks_by_doc

    result = await db.execute(
        select(DocumentChunk)
        .where(DocumentChunk.project_id == project_id, DocumentChunk.document_id.in_(document_ids))
        .order_by(DocumentChunk.document_id.asc(), DocumentChunk.chunk_index.asc())
    )
    for chunk in result.scalars().all():
        chunks_by_doc.setdefault(chunk.document_id, []).append(chunk)

    missing = [doc_id for doc_id, chunks in chunks_by_doc.items() if not chunks]
    if missing:
        docs_result = await db.execute(
            select(Document).where(Document.project_id == project_id, Document.id.in_(missing))
        )
        for doc in docs_result.scalars().all():
            try:
                text = await asyncio.to_thread(_read_text_file, doc.file_path)
            except Exception as exc:
                logger.warning("requirements_file_fallback_failed", extra={"document_id": doc.id, "error": str(exc)})
                continue
            chunks_by_doc[doc.id] = [
                DocumentChunk(
                    id=f"{doc.id}:file",
                    document_id=doc.id,
                    project_id=project_id,
                    section_id=f"{doc.id}:file",
                    section_title=doc.filename,
                    text=text,
                    page=0,
                    chunk_index=0,
                    kind=getattr(doc.kind, "value", str(doc.kind)),
                )
            ]

    return chunks_by_doc


def _read_text_file(path: str) -> str:
    with open(path, "rb") as handle:
        data = handle.read()
    return data.decode("utf-8", errors="ignore")


def _clean_line(line: str) -> str:
    line = re.sub(r"^\s*[-*#>\d.)\]]+\s*", "", line.strip())
    line = re.sub(r"\s+", " ", line)
    return line.strip(" -\t")


def _sentences(text: str) -> list[str]:
    lines = [_clean_line(line) for line in text.splitlines()]
    candidates: list[str] = []
    for line in lines:
        if not line:
            continue
        if len(line) > 240:
            candidates.extend(part.strip() for part in re.split(r"(?<=[.!?])\s+", line) if part.strip())
        else:
            candidates.append(line)
    return candidates


def _unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        cleaned = _clean_line(item)
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            output.append(cleaned)
    return output


def _section_bucket(title: str, line: str) -> str:
    combined = f"{title} {line}".lower()
    if any(word in combined for word in ("out of scope", "not in scope", "excluded")):
        return "out_of_scope"
    if any(word in combined for word in ("constraint", "assumption", "dependency", "must not", "limited to")):
        return "constraints"
    if any(word in combined for word in ("persona", "stakeholder", "user role", "actor")):
        return "personas"
    if any(word in combined for word in ("non-functional", "nfr", "quality attribute", "performance", "security")):
        return "non_functional"
    if any(word in combined for word in ("goal", "objective", "business need", "success criteria")):
        return "goals"
    return "general"


def _title_from_text(text: str, fallback: str) -> str:
    words = re.sub(r"[^A-Za-z0-9 ]+", "", text).split()
    if not words:
        return fallback
    title = " ".join(words[:8]).strip()
    return title[:80] or fallback


def _priority(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ("critical", "must", "shall", "required", "mandatory")):
        return "high"
    if any(word in lowered for word in ("may", "optional", "nice to have", "could")):
        return "low"
    return "medium"


def _is_non_functional(text: str, bucket: str) -> bool:
    lowered = text.lower()
    return bucket == "non_functional" or any(word in lowered for word in NON_FUNCTIONAL_KEYWORDS)


def _looks_requirement(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in FUNCTIONAL_KEYWORDS + NON_FUNCTIONAL_KEYWORDS)


def _looks_open_question(text: str) -> bool:
    lowered = text.lower()
    return text.endswith("?") or any(marker in lowered for marker in OPEN_QUESTION_MARKERS)


def _persona_from_line(line: str) -> dict[str, str]:
    cleaned = _clean_line(line)
    match = re.match(r"(?P<name>[^:,-]{2,60})[:,-]\s*(?P<needs>.+)", cleaned)
    if match:
        return {"name": match.group("name").strip(), "role": match.group("name").strip(), "needs": match.group("needs").strip()}
    return {"name": cleaned[:60], "role": cleaned[:60], "needs": cleaned}


def _extract_from_chunks(project_id: str, chunks_by_doc: dict[str, list[DocumentChunk]]) -> tuple[RequirementsDocument, dict[str, str]]:
    goals: list[str] = []
    personas: list[dict[str, str]] = []
    constraints: list[str] = []
    out_of_scope: list[str] = []
    open_questions: list[str] = []
    functional: list[RequirementItem] = []
    non_functional: list[RequirementItem] = []
    raw_extracts: dict[str, str] = {}
    full_text: list[str] = []
    fr_idx = 1
    nfr_idx = 1

    for doc_id, chunks in chunks_by_doc.items():
        char_count = sum(len(chunk.text or "") for chunk in chunks)
        raw_extracts[doc_id] = f"{len(chunks)} chunks, {char_count} characters"
        for chunk in chunks:
            source = f"{doc_id}:{chunk.section_id or chunk.chunk_index}"
            title = chunk.section_title or ""
            for line in _sentences(chunk.text or ""):
                full_text.append(line)
                bucket = _section_bucket(title, line)
                if _looks_open_question(line):
                    open_questions.append(line.rstrip("?") + "?")
                    continue
                if bucket == "out_of_scope":
                    out_of_scope.append(line)
                    continue
                if bucket == "constraints":
                    constraints.append(line)
                if bucket == "personas":
                    personas.append(_persona_from_line(line))
                    continue
                if bucket == "goals" and not _looks_requirement(line):
                    goals.append(line)
                    continue
                if not _looks_requirement(line):
                    continue

                if _is_non_functional(line, bucket):
                    non_functional.append(
                        RequirementItem(
                            id=f"NFR-{nfr_idx:03d}",
                            category="non_functional",
                            title=_title_from_text(line, f"Non-functional Requirement {nfr_idx}"),
                            description=line,
                            source_section=source,
                            priority=_priority(line),
                        )
                    )
                    nfr_idx += 1
                else:
                    functional.append(
                        RequirementItem(
                            id=f"FR-{fr_idx:03d}",
                            category="functional",
                            title=_title_from_text(line, f"Functional Requirement {fr_idx}"),
                            description=line,
                            source_section=source,
                            priority=_priority(line),
                        )
                    )
                    fr_idx += 1

    if not goals and full_text:
        goals = [full_text[0]]
    if not functional and full_text:
        functional.append(
            RequirementItem(
                id="FR-001",
                category="functional",
                title="Process Business Requirements",
                description=full_text[0],
                source_section=f"{project_id}:fallback",
                priority="medium",
            )
        )

    doc = RequirementsDocument(
        goals=_unique(goals),
        personas=personas or [{"name": "Business user", "role": "Stakeholder", "needs": "Use the delivered system to satisfy the uploaded business requirements."}],
        functional_requirements=functional,
        non_functional_requirements=non_functional,
        constraints=_unique(constraints),
        out_of_scope=_unique(out_of_scope),
        open_questions=_unique(open_questions),
    )
    return doc, raw_extracts


@node_hook("retrieve_and_extract")
async def retrieve_and_extract(state: RequirementsState, config=None, **kwargs) -> dict:
    db = kwargs["db"]
    project_id = kwargs["project_id"]
    chunks_by_doc = await _load_document_chunks(db, project_id, state.document_ids)
    draft, raw_extracts = _extract_from_chunks(project_id, chunks_by_doc)
    await record_usage("retrieve_and_extract", run_id=state.run_id, project_id=project_id, model="local-heuristic", metadata={"documents": len(state.document_ids), "functional": len(draft.functional_requirements)})
    return {"raw_extracts": raw_extracts, "draft_requirements": draft}


@node_hook("reflect_and_find_gaps")
async def reflect_and_find_gaps(state: RequirementsState, config=None, **kwargs) -> dict:
    draft = state.draft_requirements
    gaps: list[Gap] = []
    if draft is None:
        gaps.append(Gap(id="GAP-001", description="No requirements draft could be extracted from the selected documents.", related_requirement_area="Source documents", suggested_question="Which source document or section should be used as the requirements baseline?"))
    else:
        if not draft.functional_requirements:
            gaps.append(Gap(id="GAP-001", description="No functional requirements were found.", related_requirement_area="Functional requirements", suggested_question="What core user/system behaviors must the solution support?"))
        for idx, question in enumerate(draft.open_questions[:3], start=len(gaps) + 1):
            gaps.append(Gap(id=f"GAP-{idx:03d}", description=question, related_requirement_area="Open questions", suggested_question=question))

    new_iteration = state.reflection_iterations + 1
    reflection_passed = not gaps or new_iteration >= settings.MAX_REFLECTION_ITERATIONS
    await record_usage("reflect_and_find_gaps", run_id=state.run_id, project_id=state.project_id, model="local-heuristic", metadata={"gaps": len(gaps), "iteration": new_iteration})
    return {"gaps": gaps, "reflection_iterations": new_iteration, "reflection_passed": reflection_passed}


@node_hook("request_clarification")
async def request_clarification(state: RequirementsState, config=None, **kwargs) -> dict:
    db = kwargs["db"]
    run_id = kwargs["run_id"]
    project_id = kwargs["project_id"]

    if state.clarification_rounds_used >= settings.MAX_CLARIFICATION_ROUNDS:
        await push_to_run(run_id, "clarification_failed", {"message": "Maximum clarification rounds exceeded."})
        return {"clarification_capped": True, "error": "Max clarification rounds exceeded"}

    asked = {qa.question for qa in state.clarification_qa}
    pending = [gap for gap in state.gaps if gap.suggested_question not in asked][:3]
    if not pending:
        return {"reflection_passed": True}

    request_id = str(uuid.uuid4())
    questions = [{"id": gap.id, "question": gap.suggested_question, "context": gap.description} for gap in pending]

    run = await db.get(Run, run_id)
    if run:
        run.status = RunStatus.PAUSED_HITL
        run.pending_hitl_request_id = request_id
        run.pending_hitl_type = "clarification"

    db.add(Clarification(run_id=run_id, project_id=project_id, workflow_type="requirements", request_id=request_id, round_number=state.clarification_rounds_used + 1, questions=questions, status="pending"))
    await db.commit()

    await emit_event("clarification_requested", {"request_id": request_id, "questions": questions}, run_id=run_id, project_id=project_id, node_name="request_clarification", message="Clarification requested")
    await push_to_run(run_id, "clarification_request", {"request_id": request_id, "questions": questions})

    answers_payload = interrupt({"type": "clarification_request", "request_id": request_id, "questions": questions}) or {}
    raw_answers = answers_payload.get("answers", [])

    clar = (await db.execute(select(Clarification).where(Clarification.request_id == request_id))).scalar_one_or_none()
    if clar:
        clar.answers = raw_answers
        clar.status = "answered"
        clar.answered_at = datetime.now(timezone.utc)
    run = await db.get(Run, run_id)
    if run:
        run.status = RunStatus.RUNNING
        run.pending_hitl_request_id = None
        run.pending_hitl_type = None
    await db.commit()

    q_map = {q["id"]: q for q in questions}
    new_qa: list[ClarificationQA] = []
    for answer in raw_answers:
        qid = answer.get("id") or answer.get("question_id") or ""
        q_data = q_map.get(qid, {})
        new_qa.append(ClarificationQA(question_id=qid, question=q_data.get("question", ""), context=q_data.get("context", ""), answer=answer.get("answer", "")))

    return {"clarification_round_boundaries": [len(state.clarification_qa)], "clarification_qa": new_qa, "clarification_rounds_used": state.clarification_rounds_used + 1}


@node_hook("incorporate_answers")
async def incorporate_answers(state: RequirementsState, config=None, **kwargs) -> dict:
    if state.draft_requirements is None:
        return {}
    draft = state.draft_requirements.model_copy(deep=True)
    start = state.clarification_round_boundaries[-1] if state.clarification_round_boundaries else 0
    latest_answers = state.clarification_qa[start:]
    next_id = len(draft.functional_requirements) + 1
    answered_questions = {qa.question for qa in latest_answers}
    draft.open_questions = [q for q in draft.open_questions if q not in answered_questions]
    for qa in latest_answers:
        if not qa.answer.strip():
            continue
        draft.functional_requirements.append(RequirementItem(id=f"FR-{next_id:03d}", category="functional", title=_title_from_text(qa.question, f"Clarified Requirement {next_id}"), description=qa.answer.strip(), source_section=f"clarification:{qa.question_id}", priority="medium"))
        next_id += 1
    await record_usage("incorporate_answers", run_id=state.run_id, project_id=state.project_id, model="local-heuristic", metadata={"answers": len(latest_answers)})
    return {"draft_requirements": draft}


@node_hook("finalise_requirements")
async def finalise_requirements(state: RequirementsState, config=None, **kwargs) -> dict:
    db = kwargs["db"]
    run_id = kwargs["run_id"]
    project_id = kwargs["project_id"]
    final_doc = state.draft_requirements or RequirementsDocument()
    markdown = _generate_fallback_markdown(final_doc, project_id)
    fs = FileStore(settings.DATA_ROOT)
    json_path = await fs.save_artifact(project_id=project_id, run_id=run_id, filename="requirements.json", content=final_doc.model_dump_json(indent=2))
    md_path = await fs.save_artifact(project_id=project_id, run_id=run_id, filename="requirements.md", content=markdown)
    run = await db.get(Run, run_id)
    if run:
        run.output_data = {"approved": False, "requirements": final_doc.model_dump(mode="json"), "artifact_paths": {"requirements_md": md_path, "requirements_json": json_path}}
    await db.commit()
    await emit_event("artifacts_generated", {"requirements_md": md_path, "requirements_json": json_path}, run_id=run_id, project_id=project_id, node_name="finalise_requirements", message="Requirements artifacts generated")
    return {"final_requirements": final_doc, "requirements_json_path": json_path, "requirements_md_path": md_path}


@node_hook("approval_gate")
async def approval_gate(state: RequirementsState, config=None, **kwargs) -> dict:
    db = kwargs["db"]
    run_id = kwargs["run_id"]
    project_id = kwargs["project_id"]
    request_id = str(uuid.uuid4())
    artifact = {"requirements_md_url": f"/projects/{project_id}/runs/{run_id}/artifacts/requirements.md", "requirements_json_url": f"/projects/{project_id}/runs/{run_id}/artifacts/requirements.json"}
    run = await db.get(Run, run_id)
    if run:
        run.status = RunStatus.PAUSED_HITL
        run.pending_hitl_request_id = request_id
        run.pending_hitl_type = "approval"
    db.add(Approval(run_id=run_id, project_id=project_id, workflow_type="requirements", request_id=request_id, artifact_urls=artifact, status="pending", approved=False))
    await db.commit()
    await emit_event("approval_requested", {"request_id": request_id, "artifact": artifact}, run_id=run_id, project_id=project_id, node_name="approval_gate", message="Requirements approval requested")
    await push_to_run(run_id, "approval_request", {"request_id": request_id, "artifact": artifact})

    decision_payload = interrupt({"type": "approval_request", "request_id": request_id, "artifact": artifact}) or {}
    decision = str(decision_payload.get("decision", "approve")).lower()
    if decision == "approved":
        decision = "approve"
    feedback = decision_payload.get("feedback") or decision_payload.get("comment")

    approval = (await db.execute(select(Approval).where(Approval.request_id == request_id))).scalar_one_or_none()
    if approval:
        approval.decision = decision
        approval.feedback = feedback
        approval.status = "decided"
        approval.approved = decision == "approve"
        approval.decided_at = datetime.now(timezone.utc)
    run = await db.get(Run, run_id)
    if run:
        run.status = RunStatus.RUNNING
        run.pending_hitl_request_id = None
        run.pending_hitl_type = None
    db.add(AuditLog(project_id=project_id, run_id=run_id, actor_sub="system", action="run_approved" if decision == "approve" else "run_rejected", detail={"feedback": feedback}))
    await db.commit()
    return {"approval_decision": decision, "approval_feedback": feedback, "approval_request_id": request_id}


@node_hook("handle_rejection")
async def handle_rejection(state: RequirementsState, config=None, **kwargs) -> dict:
    feedback = state.approval_feedback or "Requirements were rejected without feedback."
    return {"gaps": [Gap(id=f"GAP-REJECT-{uuid.uuid4().hex[:6]}", description=f"Approval feedback: {feedback}", related_requirement_area="Approval feedback", suggested_question=feedback)], "reflection_passed": False, "reflection_iterations": 0, "approval_decision": None}


@node_hook("complete_run")
async def complete_run(state: RequirementsState, config=None, **kwargs) -> dict:
    db = kwargs["db"]
    run_id = kwargs["run_id"]
    project_id = kwargs["project_id"]
    run = await db.get(Run, run_id)
    artifact_paths = {"requirements_md": state.requirements_md_path, "requirements_json": state.requirements_json_path}
    if run:
        existing = dict(run.output_data or {})
        existing["approved"] = True
        existing["artifact_paths"] = artifact_paths
        run.output_data = existing
        run.status = RunStatus.COMPLETED
        run.pending_hitl_request_id = None
        run.pending_hitl_type = None
        run.finished_at = datetime.now(timezone.utc)
    await db.commit()
    await emit_event("run_completed", {"artifact_paths": artifact_paths}, run_id=run_id, project_id=project_id, node_name="complete_run", message="Requirements run completed")
    await push_to_run(run_id, "run_completed", {"run_id": run_id, "artifact_paths": artifact_paths})
    return {}


@node_hook("fail_run")
async def fail_run(state: RequirementsState, config=None, **kwargs) -> dict:
    db = kwargs["db"]
    run_id = kwargs["run_id"]
    project_id = kwargs["project_id"]
    error_msg = state.error or "Requirements workflow failed."
    run = await db.get(Run, run_id)
    if run:
        run.status = RunStatus.FAILED
        run.error = error_msg
        run.finished_at = datetime.now(timezone.utc)
    await db.commit()
    await emit_event("error", {"error": error_msg}, run_id=run_id, project_id=project_id, node_name="fail_run", message=error_msg)
    return {}


def _generate_fallback_markdown(doc: RequirementsDocument, project_id: str) -> str:
    def bullet_list(items: list) -> str:
        if not items:
            return "_None identified._\n"
        return "\n".join(f"- {item}" for item in items) + "\n"

    def persona_list(items: list[dict[str, str]]) -> str:
        if not items:
            return "_None identified._\n"
        return "\n".join(f"- **{item.get('name', 'User')}** ({item.get('role', 'Stakeholder')}): {item.get('needs', '')}" for item in items) + "\n"

    def req_table(items: List[RequirementItem]) -> str:
        if not items:
            return "_None identified._\n"
        rows = [f"| {r.id} | {r.title} | {r.description} | {r.priority} | {r.source_section or ''} |" for r in items]
        header = "| ID | Title | Description | Priority | Source |\n|---|---|---|---|---|"
        return header + "\n" + "\n".join(rows) + "\n"

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""# Requirements Document

**Project:** {project_id}
**Generated:** {generated}

## Goals
{bullet_list(doc.goals)}

## Personas
{persona_list(doc.personas)}

## Functional Requirements
{req_table(doc.functional_requirements)}

## Non-Functional Requirements
{req_table(doc.non_functional_requirements)}

## Constraints
{bullet_list(doc.constraints)}

## Out of Scope
{bullet_list(doc.out_of_scope)}

## Open Questions
{bullet_list(doc.open_questions)}
"""