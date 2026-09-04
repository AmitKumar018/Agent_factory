# AI Agent Factory Backend

Backend-only implementation for the Python GenAI final assignment. The service turns project documents into an approved requirements package, a planning/architecture package, and a downloadable generated-code bundle through three human-in-the-loop workflows.

## What This Repo Demonstrates

- Project-scoped document upload, parsing, section extraction, and vector indexing.
- Workflow 1: requirements gathering with clarification and approval HITL gates.
- Workflow 2: planning and architecture with pattern selection, optional research, critique loops, and approval.
- Workflow 3: code generation with per-task dynamic subgraphs, reviewer agents, revision loops, final approval, and zip bundle download.
- Observability: usage records, event replay, audit logs, traceability timelines, run history, and workflow graph PNGs.
- Backend-only demo flow through REST, SSE, and WebSocket endpoints.

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Create or update `.env` as needed:

```env
GEMINI_API_KEY=your-gemini-api-key
GOOGLE_API_KEY=your-gemini-api-key
JWT_SECRET=dev-jwt-secret-change-me
SQLITE_DB_PATH=./data/app.sqlite
CHECKPOINT_DB_PATH=./data/checkpoints.sqlite
CHROMA_PERSIST_DIR=./data/chromadb
DATA_ROOT=./data/projects
APP_ENV=development
LOG_LEVEL=INFO
MAX_CLARIFICATION_ROUNDS=3
MAX_CRITIC_ITERATIONS=3
MAX_REVIEWER_RETRIES=3
MAX_REFLECTION_ITERATIONS=3
DEFAULT_MODEL=gemini-2.5-flash
```

Run the API:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Health and docs:

```powershell
curl.exe http://127.0.0.1:8000/healthz
# Open http://127.0.0.1:8000/docs
```

## Backend Demo Curl Flow

Set shell variables manually, or replace them inline in the examples.

```powershell
$BASE="http://127.0.0.1:8000"
```

Register and log in:

```powershell
curl.exe -s -X POST "$BASE/auth/register" -H "Content-Type: application/json" -d '{"username":"demo","password":"demo123"}'
curl.exe -s -X POST "$BASE/auth/login" -H "Content-Type: application/json" -d '{"username":"demo","password":"demo123"}'
$TOKEN="paste_access_token_here"
```

Create a project:

```powershell
curl.exe -s -X POST "$BASE/projects" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"name":"Agent Factory Demo","description":"Backend-only milestone demo"}'
$PROJECT_ID="paste_project_id_here"
```

Upload a document:

```powershell
curl.exe -s -X POST "$BASE/projects/$PROJECT_ID/documents?kind=brd" -H "Authorization: Bearer $TOKEN" -F "file=@body.json;type=text/plain"
$DOCUMENT_ID="paste_document_id_here"
curl.exe -s "$BASE/projects/$PROJECT_ID/documents/$DOCUMENT_ID" -H "Authorization: Bearer $TOKEN"
```

Trigger Workflow 1 requirements:

```powershell
curl.exe -s -X POST "$BASE/projects/$PROJECT_ID/workflows/requirements" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"document_ids\":[\"$DOCUMENT_ID\"]}"
$REQ_RUN_ID="paste_requirements_run_id_here"
curl.exe -s "$BASE/projects/$PROJECT_ID/runs/$REQ_RUN_ID" -H "Authorization: Bearer $TOKEN"
```

Trigger Workflow 2 planning after requirements approval/completion:

```powershell
curl.exe -s -X POST "$BASE/projects/$PROJECT_ID/workflows/planning" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"requirements_run_id\":\"$REQ_RUN_ID\",\"idempotency_key\":\"plan-demo-1\"}"
$PLAN_RUN_ID="paste_planning_run_id_here"
curl.exe -s "$BASE/projects/$PROJECT_ID/runs/$PLAN_RUN_ID/planning" -H "Authorization: Bearer $TOKEN"
curl.exe -s "$BASE/projects/$PROJECT_ID/runs/$PLAN_RUN_ID/tasks" -H "Authorization: Bearer $TOKEN"
```

Trigger Workflow 3 code generation after an approved/completed architecture plan:

```powershell
curl.exe -s -X POST "$BASE/projects/$PROJECT_ID/codegen/runs" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"architecture_run_id\":\"$PLAN_RUN_ID\",\"max_retries\":3,\"idempotency_key\":\"codegen-demo-1\"}"
$CODEGEN_RUN_ID="paste_codegen_run_id_here"
curl.exe -s "$BASE/projects/$PROJECT_ID/codegen/runs/$CODEGEN_RUN_ID" -H "Authorization: Bearer $TOKEN"
```

Approve the generated bundle and download artifacts:

```powershell
curl.exe -s -X POST "$BASE/projects/$PROJECT_ID/codegen/runs/$CODEGEN_RUN_ID/approve" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"approved":true}'
curl.exe -L "$BASE/projects/$PROJECT_ID/codegen/runs/$CODEGEN_RUN_ID/bundle/download" -H "Authorization: Bearer $TOKEN" -o codegen_bundle.zip
curl.exe -L "$BASE/projects/$PROJECT_ID/codegen/runs/$CODEGEN_RUN_ID/artifacts/MANIFEST.json" -H "Authorization: Bearer $TOKEN"
```

## HITL WebSocket Protocol

Endpoint:

```text
WS /projects/{project_id}/runs/{run_id}/hitl?token=<JWT>
```

Server messages:

```json
{"type":"connected","data":{"run_id":"...","project_id":"..."}}
{"type":"clarification_request","data":{"request_id":"...","questions":[...]}}
{"type":"approval_request","data":{"request_id":"...","bundle_path":"...","summary":{}}}
```

Client clarification response:

```json
{
  "type": "clarification_response",
  "data": {
    "request_id": "request-id-from-server",
    "answers": [{"question_id": "q1", "answer": "Use SQLite for the demo."}]
  }
}
```

Client final codegen approval response:

```json
{
  "type": "approval_response",
  "data": {
    "request_id": "request-id-from-server",
    "approved": true,
    "feedback": null
  }
}
```

Rejecting codegen requires feedback:

```json
{"type":"approval_response","data":{"request_id":"...","approved":false,"feedback":"Regenerate tool docs."}}
```

## Observability And Traceability

Run-level usage:

```powershell
curl.exe -s "$BASE/projects/$PROJECT_ID/runs/$CODEGEN_RUN_ID/usage" -H "Authorization: Bearer $TOKEN"
```

Project usage rollup:

```powershell
curl.exe -s "$BASE/projects/$PROJECT_ID/observability/usage" -H "Authorization: Bearer $TOKEN"
```

Traceability timeline:

```powershell
curl.exe -s "$BASE/projects/$PROJECT_ID/runs/$CODEGEN_RUN_ID/traceability" -H "Authorization: Bearer $TOKEN"
```

Audit list and detail:

```powershell
curl.exe -s "$BASE/projects/$PROJECT_ID/runs/$CODEGEN_RUN_ID/audit" -H "Authorization: Bearer $TOKEN"
curl.exe -s "$BASE/projects/$PROJECT_ID/runs/$CODEGEN_RUN_ID/audit/$AUDIT_ID" -H "Authorization: Bearer $TOKEN"
```

Run history:

```powershell
curl.exe -s "$BASE/projects/$PROJECT_ID/runs/history" -H "Authorization: Bearer $TOKEN"
curl.exe -s "$BASE/projects/$PROJECT_ID/runs/$CODEGEN_RUN_ID/history" -H "Authorization: Bearer $TOKEN"
```

SSE event replay:

```powershell
curl.exe -N "$BASE/projects/$PROJECT_ID/runs/$CODEGEN_RUN_ID/events" -H "Authorization: Bearer $TOKEN"
```

Workflow graph PNGs:

```powershell
curl.exe -L "$BASE/workflows/requirements/graph.png" -o requirements.png
curl.exe -L "$BASE/workflows/planning/graph.png" -o planning.png
curl.exe -L "$BASE/workflows/codegen/graph.png" -o codegen.png
```

## Architecture Notes

The service is intentionally backend-only. FastAPI routers expose workflow triggers, status, HITL decisions, artifacts, observability, and graph diagrams. SQLite stores projects, users, runs, tasks, approvals, clarifications, audit logs, usage records, and replayable run events. ChromaDB stores parsed document embeddings. Files under `DATA_ROOT` store uploads, generated artifacts, manifests, workspaces, and zip bundles.

Workflow 1 reads uploaded documents, extracts requirements, runs reflection, asks clarifying questions when needed, and pauses for final approval. Workflow 2 consumes the approved requirements run, selects patterns, optionally researches, generates architecture/task artifacts, critiques them, and pauses for approval. Workflow 3 consumes the approved architecture run, creates a real codegen run id up front, executes dynamic per-task subgraphs, runs reviewer subagents, applies retry loops, writes `MANIFEST.json`, packages `bundle.zip`, and records final approval state.

Run events are persisted through `emit_event` and are also fanned out live through SSE/WebSocket managers. Token and cost records are written through `record_usage`. Audit records capture explicit human decisions and workflow actions.

## File And Function Mapping

| Area | File | Key functions/classes |
| --- | --- | --- |
| App bootstrap | `app/main.py` | `lifespan`, router registration, `health_check` |
| Auth | `app/auth/router.py`, `app/auth/service.py` | `register`, `login`, `create_access_token` |
| Projects | `app/projects/router.py`, `app/projects/service.py` | `create_project`, `list_projects`, `get_project` |
| Documents | `app/documents/router.py`, `app/documents/service.py` | `upload_document`, `run_parse_job`, `handle_upload` |
| Requirements graph | `app/workflows/requirements/graph.py` | `build_requirements_graph`, `run_requirements_workflow`, `resume_requirements_workflow`, `get_graph_png_bytes` |
| Planning graph | `app/workflows/planning/graph.py`, `app/workflows/planning/service.py` | `build_planning_graph`, `get_planning_graph`, `PlanningService.trigger`, `PlanningService.execute` |
| Codegen routing | `app/workflows/codegen/router.py` | `trigger_codegen`, `approve_codegen`, `download_bundle`, `download_codegen_artifact` |
| Codegen runner | `app/workflows/codegen/runner.py` | `create_codegen_run`, `execute_codegen_run`, `resume_codegen_run` |
| Codegen graph | `app/workflows/codegen/graph.py`, `app/workflows/codegen/subgraph.py` | `build_orchestrator_graph`, `execute_task_node`, `build_task_subgraph`, `get_graph_png_bytes` |
| Codegen nodes | `app/workflows/codegen/nodes.py` | `developer_node`, `tool_syntax_check_node`, reviewer nodes, `bundle_workspace`, `request_approval` |
| HITL WebSocket | `app/hitl/websocket.py`, `app/hitl/manager.py` | `run_hitl_socket`, `push_to_run`, `deliver_response` |
| Observability | `app/observability/router.py`, `app/hooks/node_hooks.py`, `app/sse/manager.py` | `run_usage`, `run_traceability`, `run_history`, `record_usage`, `emit_event` |
| Storage | `app/storage/database.py`, `app/storage/file_store.py`, `app/storage/vector_store.py` | `init_db`, `FileStore`, `get_chroma_client` |

## Testing

Run the full suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Useful focused suites:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_requirements_workflow.py -q
.\.venv\Scripts\python.exe -m pytest tests\test_planning_workflow.py -q
.\.venv\Scripts\python.exe -m pytest tests\workflows\codegen\test_router.py -q
```

The tests mock LLM/vector paths where available. For manual backend demos with real LLM calls, set a real `GEMINI_API_KEY`; otherwise keep route-level tests and graph PNG checks mocked/local.

