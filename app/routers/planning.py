from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from app.auth.dependencies import get_current_user
from app.schemas.planning import (
    TaskListResponse,
    TaskUpdateRequest,
    TriggerPlanningRequest,
    TriggerPlanningResponse,
)
from app.workflows.planning.service import PlanningService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/projects/{project_id}",
    tags=["Planning Workflow"],
)

def get_planning_service() -> PlanningService:
    """FastAPI dependency — returns a fresh stateless PlanningService."""
    return PlanningService()




class ApprovalRequest(BaseModel):
    feedback: Optional[str] = Field(
        None,
        description="Human feedback. Required on reject, optional on approve.",
    )


class PlanningRunStatusResponse(BaseModel):
    run_id: str
    project_id: str
    status: str
    current_node: str
    critic_iterations: int
    critic_passed: bool
    tokens_used: int
    cost_usd: float
    pending_hitl_request_id: Optional[str] = None
    last_error: Optional[str] = None
    created_at: Optional[str] = None
    finished_at: Optional[str] = None


class ResearchResponse(BaseModel):
    run_id: str
    research_available: bool
    synthesis: str
    doc_synthesis: str
    kb_synthesis: str
    web_synthesis: str
    total_findings: int


class TaskUpdateResponse(BaseModel):
    task_id: str
    updated_fields: Dict[str, Any]
    message: str


class HITLActionResponse(BaseModel):
    run_id: str
    project_id: str
    decision: str
    message: str
    new_status: str




def _handle_service_error(e: Exception) -> None:
    """
    Maps ValueError (not found / bad state) → HTTP 404 or 400.
    All other exceptions propagate as 500 via the global handler.
    """
    msg = str(e)
    if "not found" in msg.lower():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg)
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)




@router.post(
    "/workflows/planning",
    response_model=TriggerPlanningResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger Workflow 2 — Combined Project & Code Planning",
    description=(
        "Creates a new planning run linked to a completed Workflow 1 "
        "(`requirements_run_id`). Returns immediately with `run_id`; "
        "graph execution proceeds in the background. "
        "Poll `GET /projects/{project_id}/runs/{run_id}/planning` for status."
    ),
)
async def trigger_planning(
    project_id: str,
    request: TriggerPlanningRequest,
    background_tasks: BackgroundTasks,
    service: PlanningService = Depends(get_planning_service),
    _current_user: Any = Depends(get_current_user),
) -> TriggerPlanningResponse:
    """
    Workflow 2 entry point.

    - Validates that `requirements_run_id` exists and is in `completed` status.
    - Creates a new Run row (status = `pending`).
    - Enqueues `service.execute()` as a FastAPI BackgroundTask.
    - Returns 202 with `run_id` immediately.

    **Idempotency:** pass an `idempotency_key` to prevent duplicate runs
    (handled in PlanningService.trigger).
    """
    try:
        response = await service.trigger(project_id, request)
    except ValueError as e:
        _handle_service_error(e)

    # Fire-and-forget: graph runs in background
    background_tasks.add_task(
        service.execute,
        project_id=project_id,
        run_id=response.run_id,
        requirements_run_id=request.requirements_run_id,
    )

    logger.info(
        "[router][trigger_planning] project=%s run=%s → background started",
        project_id, response.run_id,
    )

    return response

@router.get(
    "/runs/{run_id}/planning",
    response_model=PlanningRunStatusResponse,
    summary="Get planning run status",
    description=(
        "Returns the current status of a planning run including current node, "
        "critic iteration count, token usage and any pending HITL request ID."
    ),
)
async def get_planning_status(
    project_id: str,
    run_id: str,
    service: PlanningService = Depends(get_planning_service),
    _current_user: Any = Depends(get_current_user),
) -> PlanningRunStatusResponse:
    try:
        data = await service.get_run_status(run_id)
    except ValueError as e:
        _handle_service_error(e)

    # Guard: run must belong to this project
    if data.get("project_id") != project_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found in project {project_id}",
        )

    return PlanningRunStatusResponse(**data)

@router.get(
    "/runs/{run_id}/tasks",
    response_model=TaskListResponse,
    summary="Get generated task list",
    description=(
        "Returns the ordered task list produced by the planner agent. "
        "Only available after the run reaches `completed` status. "
        "Returns an empty list if planning is still in progress."
    ),
)
async def get_tasks(
    project_id: str,
    run_id: str,
    service: PlanningService = Depends(get_planning_service),
    _current_user: Any = Depends(get_current_user),
) -> TaskListResponse:
    # Verify run belongs to project
    try:
        run_status = await service.get_run_status(run_id)
    except ValueError as e:
        _handle_service_error(e)

    if run_status.get("project_id") != project_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found in project {project_id}",
        )

    try:
        tasks = await service.get_tasks(run_id)
    except ValueError as e:
        _handle_service_error(e)

    return TaskListResponse(
        run_id=run_id,
        project_id=project_id,
        tasks=tasks,
        total=len(tasks),
    )

@router.patch(
    "/runs/{run_id}/tasks/{task_id}",
    response_model=TaskUpdateResponse,
    summary="Update a task (human override)",
    description=(
        "Patch mutable fields of a task: `title`, `description`, "
        "`acceptance_criteria`, `order`. "
        "Typically called after a HITL rejection to correct specific tasks "
        "before re-approval."
    ),
)
async def update_task(
    project_id: str,
    run_id: str,
    task_id: str,
    request: TaskUpdateRequest,
    service: PlanningService = Depends(get_planning_service),
    _current_user: Any = Depends(get_current_user),
) -> TaskUpdateResponse:
    # Verify run belongs to project
    try:
        run_status = await service.get_run_status(run_id)
    except ValueError as e:
        _handle_service_error(e)

    if run_status.get("project_id") != project_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found in project {project_id}",
        )

    # Only allow updates when run is completed or paused_hitl
    allowed_statuses = {"completed", "paused_hitl"}
    if run_status.get("status") not in allowed_statuses:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot update tasks while run is in status "
                f"'{run_status.get('status')}'. "
                "Wait until planning completes or is awaiting approval."
            ),
        )

    # Build patch dict — only include non-None fields
    patch = {
        k: v
        for k, v in request.model_dump().items()
        if v is not None
    }

    if not patch:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No valid fields provided for update.",
        )

    try:
        updated = await service.update_task(run_id, task_id, patch)
    except ValueError as e:
        _handle_service_error(e)

    logger.info(
        "[router][update_task] run=%s task=%s fields=%s",
        run_id, task_id, list(patch.keys()),
    )

    return TaskUpdateResponse(
        task_id=task_id,
        updated_fields=patch,
        message=f"Task {task_id} updated successfully.",
    )


@router.post(
    "/runs/{run_id}/planning/approve",
    response_model=HITLActionResponse,
    summary="Approve planning output (HITL)",
    description=(
        "Approves the generated architecture + task plan. "
        "Resumes the paused planning graph which then calls `save_result_node` "
        "and transitions the run to `completed`. "
        "The run must be in `paused_hitl` (awaiting_approval) status."
    ),
)
async def approve_planning(
    project_id: str,
    run_id: str,
    request: ApprovalRequest,
    background_tasks: BackgroundTasks,
    service: PlanningService = Depends(get_planning_service),
    _current_user: Any = Depends(get_current_user),
) -> HITLActionResponse:
    # Verify run belongs to project
    try:
        run_status = await service.get_run_status(run_id)
    except ValueError as e:
        _handle_service_error(e)

    if run_status.get("project_id") != project_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found in project {project_id}",
        )

    if run_status.get("status") != "paused_hitl":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Run {run_id} is not awaiting approval "
                f"(current status: '{run_status.get('status')}')."
            ),
        )

    # Resume graph in background — save_result_node will mark it completed
    background_tasks.add_task(
        service.resume_after_approval,
        project_id=project_id,
        run_id=run_id,
        decision="approve",
        feedback=request.feedback or "",
    )

    logger.info("[router][approve_planning] run=%s approved", run_id)

    return HITLActionResponse(
        run_id=run_id,
        project_id=project_id,
        decision="approve",
        message="Approval recorded. Graph resuming — run will transition to 'completed'.",
        new_status="running",
    )

@router.post(
    "/runs/{run_id}/planning/reject",
    response_model=HITLActionResponse,
    summary="Reject planning output (HITL)",
    description=(
        "Rejects the generated architecture + task plan and routes the graph "
        "back to `planner_node` with the provided feedback injected as "
        "critic feedback. "
        "`feedback` is **required** on rejection — the planner uses it to "
        "revise the task list. "
        "The run must be in `paused_hitl` status."
    ),
)
async def reject_planning(
    project_id: str,
    run_id: str,
    request: ApprovalRequest,
    background_tasks: BackgroundTasks,
    service: PlanningService = Depends(get_planning_service),
    _current_user: Any = Depends(get_current_user),
) -> HITLActionResponse:
    # feedback is mandatory on reject
    if not request.feedback or not request.feedback.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'feedback' is required when rejecting a planning run. "
                   "Explain what needs to change so the planner can revise.",
        )

    # Verify run belongs to project
    try:
        run_status = await service.get_run_status(run_id)
    except ValueError as e:
        _handle_service_error(e)

    if run_status.get("project_id") != project_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found in project {project_id}",
        )

    if run_status.get("status") != "paused_hitl":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Run {run_id} is not awaiting approval "
                f"(current status: '{run_status.get('status')}')."
            ),
        )

    # Resume graph in background — route_approval() will send back to planner
    background_tasks.add_task(
        service.resume_after_approval,
        project_id=project_id,
        run_id=run_id,
        decision="reject",
        feedback=request.feedback,
    )

    logger.info(
        "[router][reject_planning] run=%s rejected — feedback=%s...",
        run_id, request.feedback[:80],
    )

    return HITLActionResponse(
        run_id=run_id,
        project_id=project_id,
        decision="reject",
        message="Rejection recorded. Graph resuming — planner will revise with your feedback.",
        new_status="running",
    )




@router.get(
    "/runs/{run_id}/research",
    response_model=ResearchResponse,
    summary="Get research findings for a planning run",
    description=(
        "Returns the merged research synthesis and per-branch summaries "
        "(doc / kb / web) produced by the researcher subgraph. "
        "Only populated after the `researcher` node completes "
        "(COMPLEX path only — SIMPLE path returns empty synthesis)."
    ),
)
async def get_research(
    project_id: str,
    run_id: str,
    service: PlanningService = Depends(get_planning_service),
    _current_user: Any = Depends(get_current_user),
) -> ResearchResponse:
    # Verify run belongs to project
    try:
        run_status = await service.get_run_status(run_id)
    except ValueError as e:
        _handle_service_error(e)

    if run_status.get("project_id") != project_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found in project {project_id}",
        )

    try:
        data = await service.get_research_findings(run_id)
    except ValueError as e:
        _handle_service_error(e)

    return ResearchResponse(**data)
