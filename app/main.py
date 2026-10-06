import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.auth.router import router as auth_router
from app.config import get_settings
from app.documents.router import router as documents_router
from app.hitl.websocket import router as hitl_router
from app.logging_utils import configure_logging, get_logger
from app.observability.router import router as observability_router
from app.patterns.router import router as patterns_router
from app.patterns.seed import seed_patterns
from app.projects.router import router as projects_router
from app.routers.planning import router as planning_router
from app.sse.router import router as sse_router
from app.storage.database import AsyncSessionLocal, close_db, init_db
from app.workflows.router import router as workflows_router

settings = get_settings()
configure_logging()
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create runtime directories, initialize SQLite, and seed the Pattern KB."""
    log.info("Starting Agent Factory API", env=settings.APP_ENV)

    os.makedirs(settings.DATA_ROOT, exist_ok=True)
    os.makedirs(os.path.dirname(settings.SQLITE_DB_PATH) or ".", exist_ok=True)
    os.makedirs(os.path.join(settings.DATA_ROOT, "projects"), exist_ok=True)
    os.makedirs(os.path.join(settings.DATA_ROOT, "graphs"), exist_ok=True)
    os.makedirs(os.path.join(settings.DATA_ROOT, "checkpoints"), exist_ok=True)

    checkpoint_dir = os.path.dirname(settings.CHECKPOINT_DB_PATH)
    if checkpoint_dir:
        os.makedirs(checkpoint_dir, exist_ok=True)

    await init_db()
    async with AsyncSessionLocal() as db:
        await seed_patterns(db)

    log.info("Agent Factory API ready", checkpoint_path=settings.CHECKPOINT_DB_PATH)
    try:
        yield
    finally:
        from app.workflows.codegen.runner import close_orchestrator
        from app.workflows.requirements.graph import close_checkpointer

        await close_orchestrator()
        await close_checkpointer()
        await close_db()
        log.info("Shutting down Agent Factory API")


app = FastAPI(
    title="AI Agent Factory",
    description=(
        "Backend-only AI agent factory that turns BRD/PRD/TRD documents into "
        
    ),
    version="1.1.0",
    lifespan=lifespan,
    swagger_ui_init_oauth={},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.APP_ENV == "development" else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log.error("Unhandled exception", path=request.url.path, error=str(exc))
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": str(exc),
                "details": None,
            }
        },
    )


@app.get("/healthz", tags=["Health"], summary="Health check")
async def health_check():
    """Check SQLite, checkpoint SQLite, vector-store fallback, and filesystem access."""
    from sqlalchemy import text

    from app.storage.database import engine

    checks: dict[str, str] = {}

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["sqlite"] = "ok"
    except Exception as exc:
        checks["sqlite"] = f"error: {exc}"

    try:
        import aiosqlite

        checkpoint_dir = os.path.dirname(settings.CHECKPOINT_DB_PATH)
        if checkpoint_dir:
            os.makedirs(checkpoint_dir, exist_ok=True)
        async with aiosqlite.connect(settings.CHECKPOINT_DB_PATH) as conn:
            await conn.execute("SELECT 1")
        checks["sqlite_checkpoints"] = "ok"
    except Exception as exc:
        checks["sqlite_checkpoints"] = f"error: {exc}"

    try:
        from app.storage.vector_store import get_patterns_collection

        collection = get_patterns_collection()
        count = getattr(collection, "count", None)
        if callable(count):
            count()
        checks["vector_store"] = "ok"
    except Exception as exc:
        checks["vector_store"] = f"error: {exc}"

    try:
        os.makedirs(settings.DATA_ROOT, exist_ok=True)
        checks["filesystem"] = "ok"
    except Exception as exc:
        checks["filesystem"] = f"error: {exc}"

    all_ok = all(value == "ok" for value in checks.values())
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={"status": "healthy" if all_ok else "degraded", "checks": checks},
    )


app.include_router(auth_router)
app.include_router(projects_router)
app.include_router(documents_router)
app.include_router(patterns_router)
app.include_router(observability_router)
app.include_router(workflows_router)
app.include_router(sse_router)
app.include_router(hitl_router)
app.include_router(planning_router)


