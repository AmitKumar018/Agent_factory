# Implementation Task Plan

Total tasks: 6

Tasks cover all approved requirements through shared requirement_refs and target architecture components.

## 1. Persist planning artifacts and run state

Wire architecture, selected patterns, research log, and task plan artifacts into FileStore and Run.output_data.

Target files: app/workflows/planning/nodes.py, app/workflows/planning/service.py
Requirement refs: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013, FR-014, FR-015, FR-016, NFR-001, NFR-002, NFR-003, NFR-004
Pattern refs: Planner-Executor
Acceptance criteria:
- Run output contains artifact_paths for architecture, patterns, research, and task plan.
- Task rows are inserted with title, description, order, and pattern references.

## 2. Ground planning with project documents and Pattern KB

Query project-scoped document chunks plus the global Pattern KB, and record cited research findings.

Target files: app/workflows/planning/chroma_adapter.py, app/workflows/planning/nodes.py
Requirement refs: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013, FR-014, FR-015, FR-016, NFR-001, NFR-002, NFR-003, NFR-004
Pattern refs: RAG, Tool-Use
Acceptance criteria:
- Document findings never cross project boundaries.
- Pattern KB findings are present or deterministic fallbacks are recorded.

## 3. Generate implementation architecture

Create architecture JSON and markdown that maps components, tools, data flow, topology, risks, and selected patterns.

Target files: app/workflows/planning/nodes.py
Requirement refs: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013, FR-014, FR-015, FR-016, NFR-001, NFR-002, NFR-003, NFR-004
Pattern refs: Planner-Executor
Acceptance criteria:
- architecture.md and architecture.json are persisted.
- Architecture references selected patterns and research.

## 4. Produce ordered implementation tasks

Convert architecture and requirements into atomic, dependency-aware implementation tasks with acceptance criteria.

Target files: app/workflows/planning/nodes.py, app/workflows/models.py
Requirement refs: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013, FR-014, FR-015, FR-016, NFR-001, NFR-002, NFR-003, NFR-004
Pattern refs: Planner-Executor
Acceptance criteria:
- Tasks have contiguous order values.
- Each task has at least one acceptance criterion and requirement reference.

## 5. Review and revise the plan

Run a critic pass that validates requirements coverage, dependency ordering, pattern fidelity, and task atomicity.

Target files: app/workflows/planning/nodes.py, app/workflows/planning/graph.py
Requirement refs: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013, FR-014, FR-015, FR-016, NFR-001, NFR-002, NFR-003, NFR-004
Pattern refs: Reflection, Critic-Refine (Reflexion)
Acceptance criteria:
- Critic output contains score, dimensions, feedback, and pass/fail.
- Rejected plans route back to planner with feedback.

## 6. Expose HITL approval and progress

Pause the graph for approval, resume using Command(resume=...), emit SSE events, and push WebSocket HITL messages.

Target files: app/workflows/planning/graph.py, app/routers/planning.py
Requirement refs: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013, FR-014, FR-015, FR-016, NFR-001, NFR-002, NFR-003, NFR-004
Pattern refs: Tool-Use, Router
Acceptance criteria:
- A paused run has pending_hitl_request_id and pending_hitl_type=approval.
- Approving the run completes planning and persists tasks.
