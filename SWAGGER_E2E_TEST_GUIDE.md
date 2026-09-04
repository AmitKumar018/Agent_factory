# Swagger End-to-End Test Guide

This guide walks through testing the backend from zero to the final downloadable code bundle using Swagger UI.

The target outcome is:

- A user can authenticate.
- A project can be created.
- A BRD document can be uploaded and parsed.
- Workflow 1 produces approved requirements artifacts.
- Workflow 2 produces an approved implementation plan and tasks.
- Workflow 3 produces a code bundle, receives final approval, and exposes downloadable artifacts.
- Observability, run history, audit, traceability, and graph PNG endpoints can be verified.

## 0. Prerequisites

Run these from the repository root:

```powershell
.\.venv\Scripts\Activate.ps1
```

Set local environment variables. For a real end-to-end codegen run, `GEMINI_API_KEY` must be a real key because Workflow 3 calls the Gemini API for developer/reviewer agents.

```powershell
$env:APP_ENV="development"
$env:JWT_SECRET="dev-jwt-secret-change-me"
$env:GEMINI_API_KEY="paste-real-gemini-key-here"
$env:GOOGLE_API_KEY="paste-real-gemini-key-here"
$env:DEFAULT_MODEL="gemini-2.5-flash"
$env:SQLITE_DB_PATH="./data/app.sqlite"
$env:CHECKPOINT_DB_PATH="./data/checkpoints.sqlite"
$env:CHROMA_PERSIST_DIR="./data/chromadb"
$env:DATA_ROOT="./data/projects"
```

Start the API:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open Swagger UI:

```text
http://127.0.0.1:8000/docs
```

Also keep a scratch pad open for IDs:

```text
ACCESS_TOKEN=
PROJECT_ID=
DOCUMENT_ID=
REQ_RUN_ID=
PLAN_RUN_ID=
CODEGEN_RUN_ID=
HITL_REQUEST_ID=
AUDIT_ID=
```

## 1. Create A Sample BRD File

Create a local file named `swagger-demo-brd.txt` with this content:

```text
Business Requirements Document

Overview
Build an AI Agent Factory backend that accepts project documents and produces approved requirements, an implementation plan, and a generated code bundle.

Functional Requirements
1. Users shall register, log in, and access only their own projects.
2. Users shall create projects and upload BRD, PRD, or TRD documents.
3. The system shall parse uploaded documents into sections and searchable chunks.
4. The requirements workflow shall extract functional and non-functional requirements.
5. The requirements workflow shall pause for human approval before planning.
6. The planning workflow shall select agentic patterns, create architecture, and create implementation tasks.
7. The planning workflow shall pause for human approval before code generation.
8. The code generation workflow shall execute tasks, run reviewer agents, package a zip bundle, and pause for final approval.
9. The approved codegen result shall expose a downloadable bundle and MANIFEST.json.
10. The system shall expose usage, traceability, audit, run history, SSE progress, and graph PNG endpoints.

Non-Functional Requirements
1. All project resources must be project-scoped.
2. Workflow events must be persisted and replayable.
3. Human decisions must be auditable.
4. Artifacts must be downloadable from backend endpoints.
5. The backend must be demoable without a frontend.

Acceptance Criteria
1. A user can approve requirements and retrieve requirements.md and requirements.json.
2. A user can approve an implementation plan and retrieve the generated tasks.
3. A user can approve codegen and download bundle.zip and MANIFEST.json.
4. Observability endpoints show run history, usage, audit, and traceability.
```

This sample is intentionally detailed so Workflow 1 should go directly to approval instead of asking for clarifications.

## 2. Verify The API Is Running

In Swagger, open the `Health` section.

### GET `/healthz`

Click:

1. `GET /healthz`
2. `Try it out`
3. `Execute`

Expected response:

```json
{
  "status": "healthy",
  "checks": {
    "sqlite": "ok",
    "sqlite_checkpoints": "ok",
    "vector_store": "ok",
    "filesystem": "ok"
  }
}
```

If `vector_store` is `ok` with log warnings about Chroma being unavailable, that is acceptable for local testing because the app falls back to the in-memory vector store.

## 3. Register A User

In Swagger, open `Authentication`.

### POST `/auth/register`

Use this body:

```json
{
  "username": "swagger_demo_user",
  "password": "SwaggerDemo123!"
}
```

Expected response status: `201`

Copy `access_token` into your scratch pad as `ACCESS_TOKEN`.

If you get `409 USERNAME_TAKEN`, use `/auth/login` instead.

## 4. Log In

### POST `/auth/login`

Swagger may show this as form fields, not JSON. Fill:

```text
username: swagger_demo_user
password: SwaggerDemo123!
```

Expected response status: `200`

Copy `access_token` again. This confirms login works.

## 5. Authorize Swagger

Click the `Authorize` button near the top-right of Swagger.

You may see one or two auth entries:

1. `HTTPBearer` or similar bearer auth:
   - Paste the raw token value from `access_token`.
   - If your Swagger UI asks for a full header value, paste `Bearer <ACCESS_TOKEN>`.

2. `OAuth2PasswordBearer`:
   - Either enter username/password and authorize, or paste the token if your UI allows token input.

Click `Authorize`, then `Close`.

From this point onward, Swagger should send the bearer token automatically.

## 6. Create A Project

Open `Projects`.

### POST `/projects`

Use this body:

```json
{
  "name": "Swagger E2E Demo",
  "description": "End-to-end backend-only Swagger test"
}
```

Expected response status: `201`

Copy:

```text
id -> PROJECT_ID
```

Example:

```json
{
  "id": "abc123...",
  "owner_id": "user-id...",
  "name": "Swagger E2E Demo",
  "description": "End-to-end backend-only Swagger test",
  "status": "draft"
}
```

## 7. Upload The BRD Document

Open `Documents`.

### POST `/projects/{project_id}/documents`

Fill:

```text
project_id: PROJECT_ID
kind: BRD
file: choose swagger-demo-brd.txt
```

Important: `kind` values are uppercase for `BRD`, `PRD`, and `TRD`; `other` is lowercase.

Expected response status: `202`

Copy:

```text
document_id -> DOCUMENT_ID
```

Example response:

```json
{
  "document_id": "doc-id...",
  "filename": "swagger-demo-brd.txt",
  "status": "pending",
  "message": "File uploaded successfully. Parsing started in the background."
}
```

## 8. Wait For Document Parsing

### GET `/projects/{project_id}/documents/{document_id}`

Fill:

```text
project_id: PROJECT_ID
document_id: DOCUMENT_ID
```

Click `Execute`.

If `status` is `pending` or `parsing`, wait a few seconds and execute again.

Continue until:

```json
{
  "status": "ready"
}
```

If `status` is `failed`, read `error_message`, fix the input file or environment, and upload again.

## 9. Verify Parsed Sections

### GET `/projects/{project_id}/documents/{document_id}/sections`

Fill:

```text
project_id: PROJECT_ID
document_id: DOCUMENT_ID
```

Expected response status: `200`

Expected result:

- `sections` is not empty.
- `total_sections` is greater than `0`.
- You can see previews of the BRD content.

This confirms document parsing is working before running workflows.

## 10. Trigger Workflow 1: Requirements Gathering

Open `Workflows & Runs`.

### POST `/projects/{project_id}/workflows/requirements`

Fill:

```text
project_id: PROJECT_ID
```

Use this body:

```json
{
  "workflow_type": "requirements",
  "input": {},
  "document_ids": ["DOCUMENT_ID"],
  "idempotency_key": "swagger-req-001"
}
```

Replace `DOCUMENT_ID` with the real ID.

Expected response status: `202`

Copy:

```text
run_id -> REQ_RUN_ID
```

Expected response shape:

```json
{
  "run_id": "requirements-run-id...",
  "project_id": "project-id...",
  "workflow_type": "requirements",
  "status": "pending",
  "location": "/projects/.../runs/..."
}
```

## 11. Poll Requirements Run Status

### GET `/projects/{project_id}/runs/{run_id}`

Fill:

```text
project_id: PROJECT_ID
run_id: REQ_RUN_ID
```

Execute repeatedly until `status` is one of:

```text
paused_hitl
completed
failed
```

Expected normal path:

```json
{
  "status": "paused_hitl",
  "pending_hitl_type": "approval",
  "output_data": {
    "approved": false,
    "artifact_paths": {
      "requirements_md": "...",
      "requirements_json": "..."
    }
  }
}
```

If `pending_hitl_type` is `clarification`, follow the optional clarification step below.

If `status` is `failed`, check `error` and the run traceability endpoint later in this guide.

## 12. Optional: Answer Requirements Clarifications

Only do this if the requirements run pauses with:

```json
"pending_hitl_type": "clarification"
```

Look at the run detail response:

```text
pending_hitl_request_id -> HITL_REQUEST_ID
```

### POST `/projects/{project_id}/runs/{run_id}/clarifications`

Use this body:

```json
{
  "request_id": "HITL_REQUEST_ID",
  "answers": [
    {
      "question_id": "q1",
      "answer": "Use SQLite, FastAPI, persisted events, and backend-only artifact downloads for the demo."
    }
  ]
}
```

Then poll `GET /projects/{project_id}/runs/{run_id}` again until it pauses for approval.

## 13. Approve Workflow 1 Requirements

### POST `/projects/{project_id}/runs/{run_id}/approve`

Fill:

```text
project_id: PROJECT_ID
run_id: REQ_RUN_ID
```

This endpoint has no required body.

Expected response status: `200`

Then poll:

### GET `/projects/{project_id}/runs/{run_id}`

Expected final status:

```json
{
  "status": "completed",
  "output_data": {
    "approved": true
  }
}
```

## 14. Download Requirements Artifacts

### GET `/projects/{project_id}/runs/{run_id}/artifacts/{filename}`

Download `requirements.md`.

Fill:

```text
project_id: PROJECT_ID
run_id: REQ_RUN_ID
filename: requirements.md
```

Expected response status: `200`

Then download `requirements.json`.

Fill:

```text
filename: requirements.json
```

Expected response status: `200`

Swagger may display the artifact inline. That is fine. You are verifying that the artifact endpoint returns content.

## 15. Trigger Workflow 2: Combined Planning

Open `Planning Workflow`.

### POST `/projects/{project_id}/workflows/planning`

Fill:

```text
project_id: PROJECT_ID
```

Use this body:

```json
{
  "requirements_run_id": "REQ_RUN_ID",
  "idempotency_key": "swagger-plan-001"
}
```

Replace `REQ_RUN_ID`.

Expected response status: `202`

Copy:

```text
run_id -> PLAN_RUN_ID
```

## 16. Poll Planning Status

### GET `/projects/{project_id}/runs/{run_id}/planning`

Fill:

```text
project_id: PROJECT_ID
run_id: PLAN_RUN_ID
```

Execute repeatedly until `status` becomes:

```text
paused_hitl
```

Expected normal path:

```json
{
  "status": "paused_hitl",
  "current_node": "approval_gate",
  "pending_hitl_request_id": "..."
}
```

If `status` becomes `failed`, inspect `last_error`, then use traceability/audit endpoints later in this guide.

## 17. Check Planning Task Availability

### GET `/projects/{project_id}/runs/{run_id}/tasks`

Fill:

```text
project_id: PROJECT_ID
run_id: PLAN_RUN_ID
```

Important: this endpoint can return an empty list while the planning run is still paused at the approval gate. If you see this, it is not automatically an error.

If the response is:

```json
{
  "tasks": [],
  "total": 0
}
```

then continue to Step 20 and approve the planning run. After the planning run reaches `completed`, come back to this endpoint and run it again.

After approval/completion, expected response:

- `tasks` contains one or more planned implementation tasks.
- `total` is greater than `0`.

These tasks are what Workflow 3 will execute.

## 18. Optional: Inspect Planning Research

### GET `/projects/{project_id}/runs/{run_id}/research`

Fill:

```text
project_id: PROJECT_ID
run_id: PLAN_RUN_ID
```

Expected response status: `200`

If the planning route was simple, research may be empty. If it was complex, expect findings and synthesis fields.

## 19. Optional: Update A Planning Task Before Approval

Use this only if you want to manually edit a generated task.

### PATCH `/projects/{project_id}/runs/{run_id}/tasks/{task_id}`

Fill:

```text
project_id: PROJECT_ID
run_id: PLAN_RUN_ID
task_id: copy one task_id from the task list
```

Example body:

```json
{
  "title": "Implement authenticated artifact download",
  "description": "Add project-scoped download checks for requirements and codegen artifacts.",
  "acceptance_criteria": [
    "Returns 404 for artifacts outside the project",
    "Returns the requested artifact with a safe filename",
    "Preserves existing auth behavior"
  ],
  "order": 1
}
```

Expected response status: `200`

## 20. Approve Workflow 2 Planning

### POST `/projects/{project_id}/runs/{run_id}/planning/approve`

Fill:

```text
project_id: PROJECT_ID
run_id: PLAN_RUN_ID
```

Use this body:

```json
{
  "feedback": "Approved from Swagger E2E test."
}
```

Expected response status: `200`

Then poll:

### GET `/projects/{project_id}/runs/{run_id}/planning`

Expected final status:

```text
completed
```

Important: Workflow 3 needs this planning run to be completed and to contain tasks in `output_data["tasks"]`.

## 21. Trigger Workflow 3: Code Generation

Open `codegen`.

### POST `/projects/{project_id}/codegen/runs`

Fill:

```text
project_id: PROJECT_ID
```

Use this body:

```json
{
  "architecture_run_id": "PLAN_RUN_ID",
  "document_ids": ["DOCUMENT_ID"],
  "max_retries": 3,
  "idempotency_key": "swagger-codegen-001"
}
```

Replace `PLAN_RUN_ID` and `DOCUMENT_ID`.

Expected response status: `202`

Copy:

```text
run_id -> CODEGEN_RUN_ID
```

If this returns `400` saying the architecture run is not completed, go back to Workflow 2 and confirm the planning run status is `completed`.

If this returns `400` about missing tasks, confirm Workflow 2 generated tasks and completed successfully.

If the background run later fails with an OpenAI error, confirm `OPENAI_API_KEY` is set to a real key before starting the server.

## 22. Poll Codegen Run Detail

### GET `/projects/{project_id}/codegen/runs/{run_id}`

Fill:

```text
project_id: PROJECT_ID
run_id: CODEGEN_RUN_ID
```

Execute repeatedly until `status` is one of:

```text
paused_hitl
completed
failed
```

Expected normal path:

```json
{
  "run_id": "CODEGEN_RUN_ID",
  "status": "paused_hitl",
  "pending_approval": true,
  "hitl_request_id": "...",
  "bundle_path": "...",
  "manifest": {
    "run_id": "...",
    "files": []
  }
}
```

Verify:

- `total_task_count` is greater than `0`.
- `completed_task_count` is greater than `0`, unless a task intentionally failed.
- `bundle_path` is not null.
- `pending_approval` is true.

If `status` is `failed`, inspect:

- `error`
- each task's `error`
- each task's `feedback`
- run traceability endpoint

## 23. Download The Bundle Before Final Approval

The bundle is available once codegen reaches `paused_hitl`.

### GET `/projects/{project_id}/codegen/runs/{run_id}/bundle/download`

Fill:

```text
project_id: PROJECT_ID
run_id: CODEGEN_RUN_ID
```

Expected response status: `200`

Swagger may show a binary response. If the browser downloads a file, save it. If it only displays binary text, this still confirms the endpoint is returning a ZIP stream. For a nicer download, use the same endpoint in a browser tab or curl.

## 24. Approve Workflow 3 Final Codegen Bundle

### POST `/projects/{project_id}/codegen/runs/{run_id}/approve`

Fill:

```text
project_id: PROJECT_ID
run_id: CODEGEN_RUN_ID
```

Use this body:

```json
{
  "approved": true,
  "feedback": "Approved from Swagger E2E test."
}
```

Expected response status: `200`

Expected response:

```json
{
  "run_id": "CODEGEN_RUN_ID",
  "status": "completed",
  "message": "Codegen run approved and completed."
}
```

Then poll:

### GET `/projects/{project_id}/codegen/runs/{run_id}`

Expected final state:

```json
{
  "status": "completed",
  "final_approval": {
    "status": "approved",
    "approved": true,
    "decision": "approve"
  }
}
```

This is the final approval state required by the milestone.

## 25. Download Final Codegen Artifacts

### GET `/projects/{project_id}/codegen/runs/{run_id}/bundle/download`

Fill:

```text
project_id: PROJECT_ID
run_id: CODEGEN_RUN_ID
```

Expected response status: `200`

This is the final generated ZIP bundle.

### GET `/projects/{project_id}/codegen/runs/{run_id}/artifacts/{filename}`

Download `MANIFEST.json`.

Fill:

```text
project_id: PROJECT_ID
run_id: CODEGEN_RUN_ID
filename: MANIFEST.json
```

Expected response status: `200`

Check the manifest response includes:

```json
{
  "final_approval": {
    "status": "approved",
    "approved": true
  }
}
```

## 26. Verify Run History

Open `Observability`.

### GET `/projects/{project_id}/runs/history`

Fill:

```text
project_id: PROJECT_ID
```

Expected response status: `200`

Expected:

- Items include `REQ_RUN_ID`, `PLAN_RUN_ID`, and `CODEGEN_RUN_ID`.
- Each item includes counts for events, audits, usage, and artifacts where available.

### GET `/projects/{project_id}/runs/{run_id}/history`

Run this for each workflow run:

```text
run_id: REQ_RUN_ID
run_id: PLAN_RUN_ID
run_id: CODEGEN_RUN_ID
```

Expected response status: `200`

Expected:

- `run` summary
- `events`
- `audit`
- `usage`
- `artifacts`

## 27. Verify Usage

### GET `/projects/{project_id}/observability/usage`

Fill:

```text
project_id: PROJECT_ID
```

Expected response status: `200`

Expected:

- `total_tokens`
- `total_cost_usd`
- `records`

For local heuristic nodes, token totals may be `0`, but records should still show node metadata.

### GET `/projects/{project_id}/runs/{run_id}/usage`

Run this for:

```text
REQ_RUN_ID
PLAN_RUN_ID
CODEGEN_RUN_ID
```

Expected response status: `200`

Codegen should show usage if OpenAI responses include usage metadata.

## 28. Verify Traceability

### GET `/projects/{project_id}/runs/{run_id}/traceability`

Fill:

```text
project_id: PROJECT_ID
run_id: CODEGEN_RUN_ID
```

Expected response status: `200`

Expected:

- `timeline` contains event and usage entries.
- `artifacts` includes `bundle.zip` and `MANIFEST.json` if generated.
- `audit` includes final approval entries.

You can also test the alias:

### GET `/projects/{project_id}/runs/{run_id}/trace`

Expected response status: `200`

## 29. Verify Audit

### GET `/projects/{project_id}/runs/{run_id}/audit`

Fill:

```text
project_id: PROJECT_ID
run_id: CODEGEN_RUN_ID
```

Expected response status: `200`

Copy one audit entry `id` as:

```text
AUDIT_ID=
```

### GET `/projects/{project_id}/runs/{run_id}/audit/{audit_id}`

Fill:

```text
project_id: PROJECT_ID
run_id: CODEGEN_RUN_ID
audit_id: AUDIT_ID
```

Expected response status: `200`

Expected:

- one audit record
- includes `action`
- includes `detail`
- belongs to the same run and project

## 30. Verify Workflow Graph PNGs

Open `Workflow Graphs` or `Workflows & Runs`.

### GET `/workflows/requirements/graph.png`

Expected response status: `200`

Expected content type:

```text
image/png
```

Repeat for:

```text
GET /workflows/planning/graph.png
GET /workflows/codegen/graph.png
```

Swagger may display the image or a download link depending on the browser.

## 31. Optional: Test SSE Progress

Swagger can call this endpoint, but SSE streams are long-lived and may not finish by themselves.

### GET `/projects/{project_id}/runs/{run_id}/events`

Fill:

```text
project_id: PROJECT_ID
run_id: CODEGEN_RUN_ID
```

Expected:

- Swagger may keep waiting because SSE is a stream.
- This is normal.
- Cancel the request after you see data or after confirming it connects.

For a better SSE test, use a browser tab or curl:

```powershell
curl.exe -N "http://127.0.0.1:8000/projects/PROJECT_ID/runs/CODEGEN_RUN_ID/events" -H "Authorization: Bearer ACCESS_TOKEN"
```

## 32. Optional: Test WebSocket HITL

Swagger UI does not support WebSocket testing.

The WebSocket endpoint is:

```text
ws://127.0.0.1:8000/projects/{project_id}/runs/{run_id}/hitl?token=<ACCESS_TOKEN>
```

Use this with a WebSocket client such as Postman, Hoppscotch, or a browser console.

Server messages include:

```json
{
  "type": "approval_request",
  "data": {
    "request_id": "...",
    "run_id": "...",
    "project_id": "..."
  }
}
```

Client approval message:

```json
{
  "type": "approval_response",
  "data": {
    "request_id": "request-id-from-server",
    "approved": true,
    "feedback": "Approved from WebSocket test."
  }
}
```

For a Swagger-only test, use the REST approval endpoints described above instead.

## 33. Final Success Checklist

You are done when all of these are true:

- `GET /healthz` returns `200` and `status: healthy`.
- `POST /auth/register` or `POST /auth/login` returns a bearer token.
- `POST /projects` returns `PROJECT_ID`.
- `POST /projects/{project_id}/documents` returns `DOCUMENT_ID`.
- `GET /projects/{project_id}/documents/{document_id}` reaches `status: ready`.
- `POST /projects/{project_id}/workflows/requirements` returns `REQ_RUN_ID`.
- Requirements run reaches `paused_hitl`, then approval changes it to `completed`.
- `requirements.md` and `requirements.json` download successfully.
- `POST /projects/{project_id}/workflows/planning` returns `PLAN_RUN_ID`.
- Planning run reaches `paused_hitl`, then approval changes it to `completed`.
- `GET /projects/{project_id}/runs/{plan_run_id}/tasks` returns at least one task.
- `POST /projects/{project_id}/codegen/runs` returns `CODEGEN_RUN_ID`.
- Codegen run reaches `paused_hitl` with `bundle_path` and `pending_approval: true`.
- `GET /projects/{project_id}/codegen/runs/{codegen_run_id}/bundle/download` returns `200`.
- Codegen approval changes status to `completed`.
- `GET /projects/{project_id}/codegen/runs/{codegen_run_id}/artifacts/MANIFEST.json` returns final approval state.
- Usage, traceability, audit, run history, and graph PNG endpoints return `200`.

## Troubleshooting

### 403 Not Authenticated

Swagger is not sending your bearer token.

Fix:

1. Click `Authorize`.
2. Paste the token again.
3. If there are two auth schemes, authorize both.
4. Retry the endpoint.

### 401 Invalid Token

The token is expired or malformed.

Fix:

1. Call `/auth/login` again.
2. Copy the new `access_token`.
3. Re-authorize Swagger.

### Document Stays Pending Or Parsing

Wait a few seconds and poll again.

If it becomes `failed`, inspect `error_message`.

Common causes:

- Uploaded unsupported file type.
- Empty file.
- Parser error.

### Requirements Run Pauses For Clarification

This is valid HITL behavior.

Fix:

1. Copy `pending_hitl_request_id`.
2. Use `/projects/{project_id}/runs/{run_id}/clarifications`.
3. Poll again until approval is requested.

### Planning Trigger Says Requirements Are Not Approved

Workflow 1 must be approved and completed first.

Fix:

1. Poll `GET /projects/{project_id}/runs/{REQ_RUN_ID}`.
2. Confirm `status` is `completed`.
3. Confirm `output_data.approved` is `true`.

### Codegen Trigger Says Architecture Run Is Not Completed

Workflow 2 must be approved and completed first.

Fix:

1. Poll `GET /projects/{project_id}/runs/{PLAN_RUN_ID}/planning`.
2. Confirm `status` is `completed`.

### Codegen Fails With OpenAI Error

Workflow 3 makes real OpenAI calls during a Swagger run.

Fix:

1. Stop uvicorn.
2. Set a valid `OPENAI_API_KEY`.
3. Restart uvicorn.
4. Trigger a new codegen run.

### Codegen Approval Returns 400

You can approve only when the codegen run is in `paused_hitl`.

Fix:

1. Poll `GET /projects/{project_id}/codegen/runs/{CODEGEN_RUN_ID}`.
2. Wait until `status` is `paused_hitl`.
3. Retry approval.

### Bundle Download Returns 400

The bundle is not created yet.

Fix:

1. Poll codegen detail.
2. Wait for `bundle_path` to become non-null.
3. Retry bundle download.

### Bundle Download Returns 404

The database has a bundle path, but the file is missing from disk.

Fix:

1. Check that `DATA_ROOT` points to the same folder used when uvicorn started.
2. Re-run codegen if local data was cleaned up.