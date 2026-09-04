# Product Requirements Document

## Overview
Build an AI agent factory that converts business documents into working code.

## Goals
- Automate requirements gathering
- Generate production-quality code
- Support human-in-the-loop approval gates

## Functional Requirements

### Document Ingestion
- Accept PDF, DOCX, PPTX, XLSX, MD, TXT files
- Parse and chunk documents into semantic sections
- Embed chunks into a vector store for retrieval

### Workflow Execution
- Run three sequential agentic workflows
- Stream progress via Server-Sent Events
- Support human approval via WebSocket

## Non-Functional Requirements
- All agent outputs must use structured JSON (Pydantic)
- System must survive process restarts (LangGraph checkpointing)
- Per-run cost must be tracked and capped

## Out of Scope
- Frontend UI
- Multi-user / RBAC
- Cloud deployment
