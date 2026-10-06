
"""
Pydantic schemas for Workflow 2 – Combined Project & Code Planning.

Every agent boundary in this workflow consumes or produces one of these
types via llm.with_structured_output(). Free-text parsing is forbidden.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field



# Enumerations


class ProjectComplexity(str, Enum):
    SIMPLE = "simple"    # ≤5 reqs, all standard patterns
    COMPLEX = "complex"  # >5 reqs OR novel pattern combo OR high ambiguity


class ResearchSourceKind(str, Enum):
    DOC = "doc"
    KB = "kb"
    WEB = "web"
    LLM = "llm"


class ValidationStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"


class PlanningRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    FAILED = "failed"
    FAILED_COST_CEILING = "failed_cost_ceiling"



# Pattern Selection


class SelectedPattern(BaseModel):
    pattern_id: str
    pattern_name: str
    rationale: str = Field(
        ..., description="Why this pattern fits — tied to specific requirements"
    )
    addresses_requirements: List[str] = Field(
        ..., description="Requirement IDs this pattern satisfies"
    )
    source_citation: str = Field(
        ..., description="Citation tag e.g. [kb:ReAct]"
    )


class PatternSelectionOutput(BaseModel):
    selected_patterns: List[SelectedPattern]
    overall_rationale: str = Field(
        ..., description="Why this combination — not just individual patterns"
    )
    complexity_assessment: ProjectComplexity
    requirement_count: int
    patterns_deliberately_excluded: List[str] = Field(
        default_factory=list,
        description="Patterns considered but rejected and why — proves minimal selection",
    )



# Research


class ResearchFinding(BaseModel):
    content: str
    source_kind: ResearchSourceKind
    source_id: str = Field(
        ...,
        description=(
            "doc:<section_id> | kb:<pattern_name> | web:<url> | llm"
        ),
    )
    relevance_score: float = Field(ge=0.0, le=1.0)
    citation_tag: str = Field(
        ..., description="Inline citation e.g. [doc:sec_1], [kb:ReAct], [web:https://...]"
    )


class ResearchOutput(BaseModel):
    """Produced by each researcher branch (doc / kb / web)."""
    findings: List[ResearchFinding]
    synthesis: str = Field(
        ..., description="Branch-level narrative with inline citation tags"
    )
    branch: str = Field(..., description="One of: doc | kb | web")



# Architecture


class ComponentSpec(BaseModel):
    name: str
    role: str
    pattern_refs: List[str] = Field(
        default_factory=list, description="Pattern names that shape this component"
    )
    tools: List[str] = Field(default_factory=list)
    interacts_with: List[str] = Field(
        default_factory=list, description="Names of other components this talks to"
    )


class ArchitectureOutput(BaseModel):
    summary: str
    components: List[ComponentSpec]
    data_flow: str = Field(..., description="Narrative description of data flow")
    agent_topology: str = Field(
        ..., description="How agents relate — supervisor/worker, peer, etc."
    )
    tool_inventory: List[str]
    deployment_notes: str
    risks: List[str]
    pattern_refs: List[str] = Field(
        ..., description="Top-level pattern names used in the architecture"
    )



# Task Planning


class TaskItem(BaseModel):
    task_id: str = Field(default_factory=lambda: f"task_{uuid.uuid4().hex[:8]}")
    title: str
    description: str = Field(
        ..., description="Enough detail for a developer with no other context"
    )
    target_files: List[str] = Field(
        ..., description="Relative file paths to create or modify"
    )
    acceptance_criteria: List[str] = Field(
        ..., min_length=1, description="Verifiable completion criteria"
    )
    dependencies: List[str] = Field(
        default_factory=list,
        description="task_ids this task depends on — must all have lower order numbers",
    )
    pattern_refs: List[str] = Field(
        default_factory=list,
        description="Pattern names that inform implementation of this task",
    )
    requirement_refs: List[str] = Field(
        default_factory=list,
        description="Requirement IDs this task satisfies — at least one required",
    )
    order: int = Field(..., description="Execution order, 1-based, no gaps")
    complexity: str = Field(default="medium", description="low | medium | high")


class TaskPlanOutput(BaseModel):
    tasks: List[TaskItem]
    total_tasks: int
    estimated_complexity: ProjectComplexity
    coverage_notes: str = Field(
        ..., description="How the tasks collectively cover all requirements"
    )



# Validation


class ValidationIssue(BaseModel):
    issue_type: str  # coverage | ordering | pattern_fidelity | atomicity
    task_id: Optional[str] = None
    requirement_id: Optional[str] = None
    description: str
    severity: str = "error"  # error | warning


class PlanValidationOutput(BaseModel):
    status: ValidationStatus
    issues: List[ValidationIssue] = Field(default_factory=list)
    score: float = Field(ge=0.0, le=1.0)
    feedback: str



# Critic  (Evaluator-Optimizer evaluator side)


class CriticOutput(BaseModel):
    """
    Rubric: coverage (25) + ordering (25) + pattern_fidelity (25) + atomicity (25)
    Normalised score = total / 100.  passed = score >= 0.75
    """
    coverage_score: float = Field(ge=0.0, le=0.25)
    ordering_score: float = Field(ge=0.0, le=0.25)
    pattern_fidelity_score: float = Field(ge=0.0, le=0.25)
    atomicity_score: float = Field(ge=0.0, le=0.25)
    score: float = Field(ge=0.0, le=1.0, description="Sum of four dimension scores")
    passed: bool
    feedback: str
    suggested_revisions: List[str] = Field(default_factory=list)
    iteration: int = 0




class ComplexityRouterOutput(BaseModel):
    complexity: ProjectComplexity
    reason: str




class TriggerPlanningRequest(BaseModel):
    requirements_run_id: str = Field(
        ..., description="run_id from Workflow 1 whose output is approved"
    )
    idempotency_key: Optional[str] = None


class TriggerPlanningResponse(BaseModel):
    run_id: str
    project_id: str
    status: PlanningRunStatus
    message: str


class TaskListResponse(BaseModel):
    run_id: str
    project_id: str
    tasks: List[TaskItem]
    total: int


class TaskUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    acceptance_criteria: Optional[List[str]] = None
    order: Optional[int] = None


class PlanningRunResponse(BaseModel):
    run_id: str
    project_id: str
    status: PlanningRunStatus
    current_node: Optional[str] = None
    critic_iteration: int = 0
    tokens_used: int = 0
    cost_usd: float = 0.0
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ResearchLogResponse(BaseModel):
    run_id: str
    findings: List[ResearchFinding]
    synthesis: str
    total_findings: int
