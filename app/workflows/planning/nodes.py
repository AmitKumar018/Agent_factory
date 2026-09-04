from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from langgraph.types import interrupt
from sqlalchemy import delete, select

from app.config import settings
from app.documents.models import DocumentChunk
from app.hitl.manager import push_to_run
from app.observability.models import AuditLog
from app.patterns.models import Pattern
from app.patterns.seed import CANONICAL_PATTERNS
from app.schemas.planning import (
    ArchitectureOutput, ComponentSpec, CriticOutput, PatternSelectionOutput,
    PlanningRunStatus, ProjectComplexity, ResearchFinding, ResearchSourceKind,
    SelectedPattern, TaskItem, TaskPlanOutput,
)
from app.sse.manager import emit_event
from app.storage.database import AsyncSessionLocal
from app.storage.file_store import FileStore
from app.storage.models import Run
from app.workflows.models import Approval, Task, TaskStatus
from app.workflows.planning.chroma_adapter import PlanningChromaAdapter
from app.workflows.planning.state import PlanningState

log = logging.getLogger(__name__)

STATUS = {
    PlanningRunStatus.RUNNING: "running",
    PlanningRunStatus.FAILED: "failed",
    PlanningRunStatus.COMPLETED: "completed",
    PlanningRunStatus.AWAITING_APPROVAL: "paused_hitl",
}
KEYWORDS = {
    "RAG": ("document", "upload", "search", "chroma", "rag", "retrieval", "brd"),
    "Tool-Use": ("tool", "web", "search", "api", "parse"),
    "Planner-Executor": ("plan", "task", "implementation", "workflow"),
    "Reflection": ("critic", "review", "quality", "approval", "feedback"),
    "Router": ("route", "complex", "simple", "routing"),
    "Hierarchical Agents": ("agent", "multi", "supervisor", "worker"),
    "ReAct": ("iterate", "reason", "tool", "observation"),
    "Map-Reduce / Parallel Workers": ("parallel", "batch", "documents"),
    "Critic-Refine (Reflexion)": ("retry", "revise", "critic", "feedback"),
    "Multi-Agent Debate": ("debate", "consensus", "high-stakes"),
}


def dump(v: Any) -> Any:
    if hasattr(v, "model_dump"):
        return v.model_dump(mode="json")
    if isinstance(v, list):
        return [dump(x) for x in v]
    if isinstance(v, dict):
        return {str(k): dump(x) for k, x in v.items()}
    return v


def short(s: str, n: int = 260) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 3].rstrip() + "..."


def raw_dict(v: Any) -> dict[str, Any]:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return {}
    return v if isinstance(v, dict) else {}


def req_payload(out: dict[str, Any]) -> tuple[dict[str, Any], str, dict[str, str]]:
    paths = out.get("artifact_paths") or {}
    req = out.get("requirements") if isinstance(out.get("requirements"), dict) else out
    md = out.get("markdown") or out.get("content") or ""
    js_path = paths.get("requirements_json")
    if js_path and os.path.exists(js_path):
        try:
            with open(js_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            req = loaded.get("requirements") if isinstance(loaded.get("requirements"), dict) else loaded
        except Exception:
            pass
    md_path = paths.get("requirements_md")
    if md_path and not md and os.path.exists(md_path):
        with open(md_path, "r", encoding="utf-8") as f:
            md = f.read()
    return req if isinstance(req, dict) else {}, md, paths


def req_items(req: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key in ("functional_requirements", "non_functional_requirements", "requirements"):
        vals = req.get(key) or []
        if isinstance(vals, list):
            for x in vals:
                if isinstance(x, dict):
                    items.append({**x, "id": x.get("id") or f"REQ-{len(items)+1:03d}"})
                elif x:
                    items.append({"id": f"REQ-{len(items)+1:03d}", "title": "Requirement", "description": str(x)})
    return items


def req_ids(req: dict[str, Any]) -> list[str]:
    ids = [str(x.get("id")) for x in req_items(req) if x.get("id")]
    return ids or ["REQ-001"]


def req_text(req: dict[str, Any], md: str = "") -> str:
    parts = []
    for x in req_items(req):
        parts.append(" ".join(str(x.get(k, "")) for k in ("id", "title", "description", "category")))
    for key in ("goals", "constraints", "open_questions"):
        val = req.get(key) or []
        parts.extend(str(x) for x in val) if isinstance(val, list) else parts.append(str(val))
    parts.append(md[:2000])
    return "\n".join(parts).lower()


def goal(req: dict[str, Any], md: str) -> str:
    title = req.get("project_title") or req.get("title") or "approved requirements"
    goals = req.get("goals") or []
    if isinstance(goals, list) and goals:
        summary = "; ".join(map(str, goals[:3]))
    else:
        summary = "; ".join(short(str(x.get("description") or x.get("title") or ""), 110) for x in req_items(req)[:3])
    return f"Plan implementation for {title}: {summary or short(md, 220) or 'Build the approved system.'}"


async def set_run(run_id: str, status: str, node: str, error: str = "") -> None:
    try:
        async with AsyncSessionLocal() as db:
            run = await db.get(Run, run_id)
            if not run:
                return
            run.status = STATUS.get(status, status)
            if error:
                run.error = error[:500]
            meta = dict(run.run_metadata or {})
            meta["current_node"] = node
            meta["updated_at"] = datetime.now(timezone.utc).isoformat()
            run.run_metadata = meta
            if run.status == "running" and run.started_at is None:
                run.started_at = datetime.now(timezone.utc)
            await db.commit()
    except Exception as exc:
        log.warning("planning status update failed: %s", exc)


async def ev(state: PlanningState, node: str, kind: str, msg: str, data: Any | None = None) -> None:
    await emit_event(kind, data or {}, run_id=state.run_id, project_id=state.project_id, node_name=node, message=msg)


async def load_requirements_node(state: PlanningState) -> dict[str, Any]:
    await set_run(state.run_id, PlanningRunStatus.RUNNING, "load_requirements")
    await ev(state, "load_requirements", "node_started", "Loading approved requirements")
    async with AsyncSessionLocal() as db:
        req_run = await db.get(Run, state.requirements_run_id)
    if not req_run:
        err = f"Requirements run {state.requirements_run_id} not found"
        await set_run(state.run_id, PlanningRunStatus.FAILED, "load_requirements", err)
        return {"current_node": "load_requirements", "errors": [err]}
    out = raw_dict(req_run.output_data)
    req, md, paths = req_payload(out)
    if out.get("approved") is False:
        err = "Requirements run has not been approved"
        await set_run(state.run_id, PlanningRunStatus.FAILED, "load_requirements", err)
        return {"current_node": "load_requirements", "errors": [err]}
    await ev(state, "load_requirements", "node_completed", "Approved requirements loaded", {"requirement_count": len(req_items(req)), "artifact_paths": paths})
    return {"current_node": "load_requirements", "requirements_json": req, "requirements_md": md, "planning_goal": goal(req, md)}


async def catalog() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Pattern).order_by(Pattern.name.asc()))
            for p in result.scalars().all():
                rows.append({"pattern_id": p.id, "name": p.name, "intent": p.intent, "structure": p.structure, "when_to_use": p.when_to_use, "when_not_to_use": p.when_not_to_use, "prerequisites": p.prerequisites, "references": p.references or "", "tags": p.tags, "source": p.source or "db"})
    except Exception:
        pass
    names = {p["name"] for p in rows}
    for p in CANONICAL_PATTERNS:
        if p["name"] not in names:
            rows.append({"pattern_id": f"seed:{p['name']}", "source": "seed_fallback", **p})
    return rows


def ranked_patterns(rows: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    scored = []
    for p in rows:
        name = p.get("name", "")
        score = sum(3 for k in KEYWORDS.get(name, ()) if k in text)
        hay = " ".join(str(p.get(k, "")) for k in ("name", "intent", "structure", "when_to_use", "tags")).lower()
        score += sum(1 for tok in set(text.split()) if len(tok) > 4 and tok in hay)
        if name in {"Planner-Executor", "RAG", "Reflection"}:
            score += 2
        scored.append((score, p))
    return [p for _, p in sorted(scored, key=lambda x: (x[0], x[1].get("name", "")), reverse=True)]


async def pattern_selector_node(state: PlanningState) -> dict[str, Any]:
    await set_run(state.run_id, PlanningRunStatus.RUNNING, "pattern_selector")
    rows = await catalog()
    selected = ranked_patterns(rows, req_text(state.requirements_json, state.requirements_md))[:5]
    by_name = {p.get("name"): p for p in rows}
    for name in ("Planner-Executor", "RAG", "Reflection"):
        if name in by_name and all(p.get("name") != name for p in selected):
            selected.append(by_name[name])
    selected = selected[:6]
    ids = req_ids(state.requirements_json)
    items = [SelectedPattern(pattern_id=str(p.get("pattern_id")), pattern_name=str(p.get("name")), rationale=short(str(p.get("when_to_use") or p.get("intent")), 220), addresses_requirements=ids, source_citation=f"[kb:{p.get('name')}]") for p in selected]
    complexity = ProjectComplexity.COMPLEX if len(ids) > 5 or len(items) > 3 else ProjectComplexity.SIMPLE
    output = PatternSelectionOutput(selected_patterns=items, overall_rationale="Patterns cover retrieval, planning, routing, quality review, and human approval.", complexity_assessment=complexity, requirement_count=len(ids), patterns_deliberately_excluded=["Multi-Agent Debate: not required for this implementation plan."])
    await ev(state, "pattern_selector", "node_completed", "Patterns selected", {"patterns": [x.pattern_name for x in items], "complexity": complexity.value})
    return {"current_node": "pattern_selector", "pattern_selection": output, "snapshotted_patterns": dump(selected)}


async def complexity_router_node(state: PlanningState) -> dict[str, Any]:
    await set_run(state.run_id, PlanningRunStatus.RUNNING, "complexity_router")
    text = req_text(state.requirements_json, state.requirements_md)
    is_complex = len(req_items(state.requirements_json)) > 5 or len(state.snapshotted_patterns) > 3 or any(k in text for k in ("agent", "rag", "document", "approval", "workflow", "architecture", "task", "chroma"))
    complexity = ProjectComplexity.COMPLEX if is_complex else ProjectComplexity.SIMPLE
    await ev(state, "complexity_router", "node_completed", "Complexity route selected", {"complexity": complexity.value})
    return {"current_node": "complexity_router", "complexity": complexity}


async def doc_research(state: PlanningState) -> tuple[list[ResearchFinding], str]:
    findings = []
    rows = await PlanningChromaAdapter().query_documents(state.planning_goal, state.project_id, n_results=6)
    for r in rows[:4]:
        meta = r.get("metadata", {})
        sid = meta.get("section_id") or "unknown"
        findings.append(ResearchFinding(content=short(r.get("document", ""), 360), source_kind=ResearchSourceKind.DOC, source_id=f"doc:{sid}", relevance_score=max(0.0, min(1.0, float(r.get("relevance", 0.65)))), citation_tag=f"[doc:{sid}]"))
    if not findings:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(DocumentChunk).where(DocumentChunk.project_id == state.project_id).order_by(DocumentChunk.document_id.asc(), DocumentChunk.chunk_index.asc()).limit(6))
            for c in result.scalars().all():
                findings.append(ResearchFinding(content=short(c.text, 360), source_kind=ResearchSourceKind.DOC, source_id=f"doc:{c.section_id}", relevance_score=0.7, citation_tag=f"[doc:{c.section_id}]"))
    synth = "Document research found implementation drivers in " + ", ".join(f.citation_tag for f in findings[:4]) + "." if findings else "No project document chunks were available for RAG."
    return findings, synth


async def kb_research(state: PlanningState) -> tuple[list[ResearchFinding], str]:
    findings = []
    rows = await PlanningChromaAdapter().query_patterns(state.planning_goal, n_results=6)
    for r in rows[:5]:
        meta = r.get("metadata", {})
        name = meta.get("name") or meta.get("pattern_id") or "Pattern"
        findings.append(ResearchFinding(content=short(r.get("document", ""), 360), source_kind=ResearchSourceKind.KB, source_id=f"kb:{name}", relevance_score=max(0.0, min(1.0, float(r.get("relevance", 0.65)))), citation_tag=f"[kb:{name}]"))
    if not findings:
        for p in state.snapshotted_patterns[:5]:
            name = str(p.get("name") or "Pattern")
            txt = " ".join(str(p.get(k, "")) for k in ("intent", "when_to_use", "structure"))
            findings.append(ResearchFinding(content=short(txt, 360) or f"{name} selected for this run.", source_kind=ResearchSourceKind.KB, source_id=f"kb:{name}", relevance_score=0.72, citation_tag=f"[kb:{name}]"))
    synth = "Pattern KB research supports " + ", ".join(f.citation_tag for f in findings[:5]) + "." if findings else "No Pattern KB findings were available."
    return findings, synth


async def researcher_node(state: PlanningState) -> dict[str, Any]:
    await set_run(state.run_id, PlanningRunStatus.RUNNING, "researcher")
    await ev(state, "researcher", "node_started", "Running document, Pattern KB, and web-stub research")
    docs, doc_s = await doc_research(state)
    kbs, kb_s = await kb_research(state)
    web = ResearchFinding(content="External web search is stubbed in this environment; no network call was made.", source_kind=ResearchSourceKind.WEB, source_id="web:stub", relevance_score=0.5, citation_tag="[web:stub]")
    web_s = "Web-search path executed as a deterministic stub [web:stub]."
    findings = docs + kbs + [web]
    synth = " ".join([doc_s, kb_s, web_s])
    async with AsyncSessionLocal() as db:
        run = await db.get(Run, state.run_id)
        if run:
            meta = dict(run.run_metadata or {})
            meta.update({"current_node": "researcher", "research_synthesis": synth, "doc_synthesis": doc_s, "kb_synthesis": kb_s, "web_synthesis": web_s, "total_findings": len(findings)})
            run.run_metadata = meta
            await db.commit()
    await ev(state, "researcher", "node_completed", "Research completed", {"total_findings": len(findings)})
    return {"current_node": "researcher", "research_findings": findings, "doc_synthesis": doc_s, "kb_synthesis": kb_s, "web_synthesis": web_s, "research_synthesis": synth}


def pattern_names(state: PlanningState) -> list[str]:
    if state.pattern_selection:
        return [x.pattern_name for x in state.pattern_selection.selected_patterns]
    return [str(p.get("name")) for p in state.snapshotted_patterns if p.get("name")]

async def architect_node(state: PlanningState) -> dict[str, Any]:
    await set_run(state.run_id, PlanningRunStatus.RUNNING, "architect")
    patterns = pattern_names(state)
    comps = [
        ComponentSpec(name="Authenticated Planning API", role="Receives approved requirements and exposes status, tasks, and research.", pattern_refs=["Router"], tools=["FastAPI", "SQLAlchemy"], interacts_with=["Planning Graph", "Artifact Store"]),
        ComponentSpec(name="Document RAG Research", role="Retrieves project-scoped document chunks and cites sections.", pattern_refs=["RAG"], tools=["ChromaDB", "DocumentChunk"], interacts_with=["Pattern KB Research", "Architecture Generator"]),
        ComponentSpec(name="Pattern KB Research", role="Looks up global agentic patterns and snapshots selected guidance.", pattern_refs=["RAG", "Tool-Use"], tools=["Pattern DB", "ChromaDB"], interacts_with=["Pattern Selector", "Planner"]),
        ComponentSpec(name="Planning Graph", role="Builds architecture and ordered tasks with a critic loop.", pattern_refs=["Planner-Executor", "Reflection"], tools=["LangGraph"], interacts_with=["HITL Approval", "Task Store"]),
        ComponentSpec(name="HITL Approval and SSE", role="Pauses for approve/reject, resumes through Command(resume=...), and emits replayable events.", pattern_refs=["Tool-Use", "Reflection"], tools=["WebSocket", "SSE", "SQLite checkpointer"], interacts_with=["Planning Graph", "Run Event Log"]),
    ]
    arch = ArchitectureOutput(
        summary="A document-grounded planning workflow that turns approved requirements into an approved architecture and executable task plan.",
        components=comps,
        data_flow="Approved requirements load from Workflow 1, selected patterns are snapshotted, document and Pattern KB findings ground architecture, planner emits ordered tasks, critic validates coverage, and HITL approval persists artifacts.",
        agent_topology="Router plus planner-executor with reflection; complex inputs add RAG/KB/web-stub research before architecture and planning.",
        tool_inventory=["FastAPI", "LangGraph", "SQLite", "ChromaDB", "FileStore", "SSE", "WebSocket HITL"],
        deployment_notes="Runs persist to SQLite and local artifacts under the project run directory; web search remains a replaceable stub until external access is configured.",
        risks=["Generated plans depend on document parse quality.", "The in-memory HITL queue is single-process only.", "Web research is stubbed without network-enabled tooling."],
        pattern_refs=patterns,
    )
    lines = ["# Implementation Architecture", "", arch.summary, "", "## Agent Topology", arch.agent_topology, "", "## Data Flow", arch.data_flow, "", "## Components"]
    lines += [f"- **{c.name}**: {c.role}" for c in arch.components]
    lines += ["", "## Pattern References", ", ".join(patterns) if patterns else "None", "", "## Risks"]
    lines += [f"- {r}" for r in arch.risks]
    await ev(state, "architect", "node_completed", "Architecture generated", {"components": len(comps)})
    return {"current_node": "architect", "architecture": arch, "architecture_md": "\n".join(lines) + "\n"}


def task_id(run_id: str, order: int, title: str) -> str:
    return "task_" + uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}:{order}:{title}").hex[:8]


def build_tasks(state: PlanningState, ids: list[str], patterns: list[str]) -> list[TaskItem]:
    specs = [
        ("Persist planning artifacts and run state", "Wire architecture, selected patterns, research log, and task plan artifacts into FileStore and Run.output_data.", ["app/workflows/planning/nodes.py", "app/workflows/planning/service.py"], ["Run output contains artifact_paths for architecture, patterns, research, and task plan.", "Task rows are inserted with title, description, order, and pattern references."], ["Planner-Executor"]),
        ("Ground planning with project documents and Pattern KB", "Query project-scoped document chunks plus the global Pattern KB, and record cited research findings.", ["app/workflows/planning/chroma_adapter.py", "app/workflows/planning/nodes.py"], ["Document findings never cross project boundaries.", "Pattern KB findings are present or deterministic fallbacks are recorded."], ["RAG", "Tool-Use"]),
        ("Generate implementation architecture", "Create architecture JSON and markdown that maps components, tools, data flow, topology, risks, and selected patterns.", ["app/workflows/planning/nodes.py"], ["architecture.md and architecture.json are persisted.", "Architecture references selected patterns and research."], ["Planner-Executor"]),
        ("Produce ordered implementation tasks", "Convert architecture and requirements into atomic, dependency-aware implementation tasks with acceptance criteria.", ["app/workflows/planning/nodes.py", "app/workflows/models.py"], ["Tasks have contiguous order values.", "Each task has at least one acceptance criterion and requirement reference."], ["Planner-Executor"]),
        ("Review and revise the plan", "Run a critic pass that validates requirements coverage, dependency ordering, pattern fidelity, and task atomicity.", ["app/workflows/planning/nodes.py", "app/workflows/planning/graph.py"], ["Critic output contains score, dimensions, feedback, and pass/fail.", "Rejected plans route back to planner with feedback."], ["Reflection", "Critic-Refine (Reflexion)"]),
        ("Expose HITL approval and progress", "Pause the graph for approval, resume using Command(resume=...), emit SSE events, and push WebSocket HITL messages.", ["app/workflows/planning/graph.py", "app/routers/planning.py"], ["A paused run has pending_hitl_request_id and pending_hitl_type=approval.", "Approving the run completes planning and persists tasks."], ["Tool-Use", "Router"]),
    ]
    tasks = []
    for order, (title, desc, files, criteria, pats) in enumerate(specs, start=1):
        deps = [tasks[-1].task_id] if tasks else []
        refs = [p for p in pats if p in patterns or p in KEYWORDS] or patterns[:2]
        tasks.append(TaskItem(task_id=task_id(state.run_id, order, title), title=title, description=desc, target_files=files, acceptance_criteria=criteria, dependencies=deps, pattern_refs=refs, requirement_refs=ids, order=order, complexity="high" if order in {2, 5, 6} else "medium"))
    return tasks


async def planner_node(state: PlanningState) -> dict[str, Any]:
    await set_run(state.run_id, PlanningRunStatus.RUNNING, f"planner_iter_{state.critic_iteration}")
    ids = req_ids(state.requirements_json)
    patterns = pattern_names(state)
    tasks = build_tasks(state, ids, patterns)
    if state.approval_feedback:
        order = len(tasks) + 1
        tasks.append(TaskItem(task_id=task_id(state.run_id, order, "Address planning approval feedback"), title="Address planning approval feedback", description=f"Revise the implementation plan based on reviewer feedback: {state.approval_feedback.strip()}", target_files=["app/workflows/planning/nodes.py", "data/<project>/runs/<run>/artifacts/task_plan.md"], acceptance_criteria=["Reviewer feedback is represented in the task plan.", "The revised plan can be re-submitted for approval."], dependencies=[tasks[-1].task_id], pattern_refs=["Reflection"], requirement_refs=ids, order=order, complexity="medium"))
    plan = TaskPlanOutput(tasks=tasks, total_tasks=len(tasks), estimated_complexity=state.complexity or ProjectComplexity.COMPLEX, coverage_notes="Tasks cover all approved requirements through shared requirement_refs and target architecture components.")
    await ev(state, "planner", "node_completed", "Task plan generated", {"total_tasks": len(tasks)})
    return {"current_node": "planner", "task_plan": plan}


async def critic_node(state: PlanningState) -> dict[str, Any]:
    await set_run(state.run_id, PlanningRunStatus.RUNNING, f"critic_iter_{state.critic_iteration}")
    if not state.task_plan:
        result = CriticOutput(coverage_score=0, ordering_score=0, pattern_fidelity_score=0, atomicity_score=0, score=0, passed=False, feedback="No task plan was generated.", suggested_revisions=["Generate a task plan."], iteration=state.critic_iteration + 1)
        return {"current_node": "critic", "critic_result": result, "critic_passed": False, "critic_iteration": state.critic_iteration + 1}
    issues = []
    orders = [t.order for t in state.task_plan.tasks]
    if orders != list(range(1, len(orders) + 1)):
        issues.append("Task order must be contiguous and 1-based.")
    known = {t.task_id: t.order for t in state.task_plan.tasks}
    for t in state.task_plan.tasks:
        if not t.acceptance_criteria:
            issues.append(f"{t.task_id} has no acceptance criteria.")
        if not t.requirement_refs:
            issues.append(f"{t.task_id} has no requirement references.")
        for dep in t.dependencies:
            if known.get(dep, 9999) >= t.order:
                issues.append(f"{t.task_id} depends on a task that does not have a lower order.")
    missing = set(req_ids(state.requirements_json)) - {r for t in state.task_plan.tasks for r in t.requirement_refs}
    if missing:
        issues.append("Missing requirement coverage: " + ", ".join(sorted(missing)))
    passed = not issues
    result = CriticOutput(coverage_score=0.25 if not missing else 0.12, ordering_score=0.25 if orders == list(range(1, len(orders)+1)) else 0.1, pattern_fidelity_score=0.22 if state.pattern_selection else 0.12, atomicity_score=0.20 if passed else 0.15, score=0.92 if passed else 0.62, passed=passed, feedback="Plan passes coverage, ordering, pattern fidelity, and atomicity checks." if passed else " ".join(issues), suggested_revisions=[] if passed else issues, iteration=state.critic_iteration + 1)
    await ev(state, "critic", "node_completed", "Plan critic completed", {"score": result.score, "passed": result.passed})
    return {"current_node": "critic", "critic_result": result, "critic_passed": result.passed, "critic_iteration": state.critic_iteration + 1}


async def approval_gate_node(state: PlanningState) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        run = await db.get(Run, state.run_id)
        stored_request_id = run.pending_hitl_request_id if run else None
    request_id = state.pending_hitl_request_id or stored_request_id or str(uuid.uuid4())
    payload = {"request_id": request_id, "run_id": state.run_id, "project_id": state.project_id, "artifact": {"architecture_summary": state.architecture.summary if state.architecture else "", "task_count": len(state.task_plan.tasks) if state.task_plan else 0, "patterns": pattern_names(state), "critic_score": round(state.critic_result.score, 3) if state.critic_result else None, "critic_passed": state.critic_passed, "critic_feedback": state.critic_result.feedback if state.critic_result else ""}}
    should_emit_request = False
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Approval).where(Approval.request_id == request_id))
        approval = result.scalar_one_or_none()
        if not approval:
            db.add(Approval(id=str(uuid.uuid4()), project_id=state.project_id, run_id=state.run_id, workflow_type="planning", request_id=request_id, status="pending", payload=payload, artifact_urls=[]))
            should_emit_request = True
        else:
            approval.status = "pending"
            approval.payload = payload
        run = await db.get(Run, state.run_id)
        if run:
            run.status = "paused_hitl"
            run.pending_hitl_request_id = request_id
            run.pending_hitl_type = "approval"
            meta = dict(run.run_metadata or {})
            meta["current_node"] = "approval_gate"
            run.run_metadata = meta
        await db.commit()
    if should_emit_request:
        await emit_event("approval_requested", payload, run_id=state.run_id, project_id=state.project_id, node_name="approval_gate", message="Planning approval requested")
        await push_to_run(state.run_id, "approval_request", payload)
    decision = interrupt(payload)
    approval_value = decision.get("decision", "approve") if isinstance(decision, dict) else "approve"
    feedback = decision.get("feedback", "") if isinstance(decision, dict) else ""
    approval_value = approval_value.lower().strip() or "approve"
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Approval).where(Approval.request_id == request_id))
        record = result.scalar_one_or_none()
        if record:
            record.decision = approval_value
            record.feedback = feedback
            record.approved = approval_value == "approve"
            record.status = "decided"
            record.decided_at = datetime.now(timezone.utc)
            record.resolved_at = datetime.now(timezone.utc)
        db.add(AuditLog(project_id=state.project_id, run_id=state.run_id, action="planning_approval_decision", detail={"request_id": request_id, "decision": approval_value, "feedback": feedback}))
        run = await db.get(Run, state.run_id)
        if run:
            run.status = "running"
            run.pending_hitl_request_id = None
            run.pending_hitl_type = None
            meta = dict(run.run_metadata or {})
            meta["current_node"] = "approval_gate"
            meta["approval_decision"] = approval_value
            run.run_metadata = meta
        await db.commit()
    await emit_event("approval_decided", {"request_id": request_id, "decision": approval_value}, run_id=state.run_id, project_id=state.project_id, node_name="approval_gate", message="Planning approval decision received")
    return {"current_node": "approval_gate", "pending_hitl_request_id": None, "approval_decision": approval_value, "approval_feedback": feedback or None}


def plan_md(plan: TaskPlanOutput) -> str:
    lines = ["# Implementation Task Plan", "", f"Total tasks: {plan.total_tasks}", "", plan.coverage_notes, ""]
    for t in sorted(plan.tasks, key=lambda x: x.order):
        lines += [f"## {t.order}. {t.title}", "", t.description, "", "Target files: " + ", ".join(t.target_files), "Requirement refs: " + ", ".join(t.requirement_refs), "Pattern refs: " + ", ".join(t.pattern_refs), "Acceptance criteria:"]
        lines += [f"- {c}" for c in t.acceptance_criteria] + [""]
    return "\n".join(lines)


async def save_result_node(state: PlanningState) -> dict[str, Any]:
    await set_run(state.run_id, PlanningRunStatus.RUNNING, "save_result")
    if not state.task_plan:
        err = "save_result_node: no task_plan to save"
        await set_run(state.run_id, PlanningRunStatus.FAILED, "save_result", err)
        return {"current_node": "save_result", "errors": [err]}
    store = FileStore(settings.DATA_ROOT)
    pattern_selection = dump(state.pattern_selection) if state.pattern_selection else {"selected_patterns": []}
    pattern_snapshot = dump(state.snapshotted_patterns)
    research = {"findings": dump(state.research_findings), "doc_synthesis": state.doc_synthesis, "kb_synthesis": state.kb_synthesis, "web_synthesis": state.web_synthesis, "synthesis": state.research_synthesis}
    arch = dump(state.architecture) if state.architecture else {}
    plan = dump(state.task_plan)
    paths = {
        "architecture_md": await store.save_artifact(state.project_id, state.run_id, "architecture.md", state.architecture_md or "# Implementation Architecture\n"),
        "architecture_json": await store.save_artifact(state.project_id, state.run_id, "architecture.json", json.dumps(arch, indent=2)),
        "selected_patterns_json": await store.save_artifact(state.project_id, state.run_id, "selected_patterns.json", json.dumps({**pattern_selection, "snapshots": pattern_snapshot}, indent=2)),
        "pattern_snapshots_json": await store.save_artifact(state.project_id, state.run_id, "pattern_snapshots.json", json.dumps(pattern_snapshot, indent=2)),
        "research_log_json": await store.save_artifact(state.project_id, state.run_id, "research_log.json", json.dumps(research, indent=2)),
        "task_plan_json": await store.save_artifact(state.project_id, state.run_id, "task_plan.json", json.dumps(plan, indent=2)),
        "task_plan_md": await store.save_artifact(state.project_id, state.run_id, "task_plan.md", plan_md(state.task_plan)),
    }
    output = {"approved": True, "implementation_plan_approved": True, "critic_passed": state.critic_passed, "requirements_run_id": state.requirements_run_id, "architecture": arch, "architecture_md": state.architecture_md, "selected_patterns": pattern_selection.get("selected_patterns", []), "pattern_selection": pattern_selection, "snapshotted_patterns": pattern_snapshot, "research": research, "task_plan": plan, "tasks": plan.get("tasks", []), "artifact_paths": paths}
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Task).where(Task.run_id == state.run_id))
        for item in state.task_plan.tasks:
            db.add(Task(id=item.task_id, run_id=state.run_id, name=item.title, title=item.title, description=item.description, order_index=item.order, pattern_refs=item.pattern_refs, status=TaskStatus.PENDING, generated_files=[], input_data={"requirement_refs": item.requirement_refs, "target_files": item.target_files}, output_data={"acceptance_criteria": item.acceptance_criteria, "dependencies": item.dependencies, "complexity": item.complexity}))
        run = await db.get(Run, state.run_id)
        if run:
            run.status = "completed"
            run.output_data = output
            meta = dict(run.run_metadata or {})
            meta.update({"current_node": "save_result", "tokens_used": state.tokens_used, "cost_usd": state.cost_usd, "critic_iterations": state.critic_iteration, "critic_passed": state.critic_passed, "research_synthesis": state.research_synthesis, "doc_synthesis": state.doc_synthesis, "kb_synthesis": state.kb_synthesis, "web_synthesis": state.web_synthesis, "total_findings": len(state.research_findings), "artifact_paths": paths})
            run.run_metadata = meta
            run.finished_at = datetime.now(timezone.utc)
            run.pending_hitl_request_id = None
            run.pending_hitl_type = None
        await db.commit()
    await emit_event("artifacts_generated", paths, run_id=state.run_id, project_id=state.project_id, node_name="save_result", message="Planning artifacts generated")
    await emit_event("run_completed", {"artifact_paths": paths, "task_count": len(state.task_plan.tasks)}, run_id=state.run_id, project_id=state.project_id, node_name="save_result", message="Planning run completed")
    await push_to_run(state.run_id, "run_completed", {"run_id": state.run_id, "artifact_paths": paths})
    return {"current_node": "save_result"}


def route_complexity(state: PlanningState) -> str:
    return "researcher" if state.complexity == ProjectComplexity.COMPLEX else "architect"


def route_critic(state: PlanningState) -> str:
    if state.critic_passed:
        return "approval_gate"
    return "planner" if state.critic_iteration < state.max_critic_iterations else "approval_gate"


def route_approval(state: PlanningState) -> str:
    return "save_result" if (state.approval_decision or "approve").lower() == "approve" else "planner"
