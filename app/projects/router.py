
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.storage.database import get_db
from app.dependencies import get_current_user
from app.auth.models import User
from app.projects import service as project_service
from app.projects.models import ProjectStatus
from app.projects.schemas import (
    ProjectCreate,
    ProjectUpdate,
    ProjectResponse,
    ProjectListResponse,
)

router = APIRouter(prefix="/projects", tags=["Projects"])


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new project",
)
async def create_project(
    body: ProjectCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Creates a new project owned by the authenticated user.
    Each project is an isolated workspace with its own documents, runs, and artifacts.
    """
    project = await project_service.create_project(db, current_user.id, body)
    return ProjectResponse.model_validate(project)


@router.get(
    "",
    response_model=ProjectListResponse,
    summary="List all projects for the current user",
)
async def list_projects(
    status: Optional[ProjectStatus] = Query(None, description="Filter by project status"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return a paginated list of the user's projects, optionally filtered by status."""
    items, total = await project_service.list_projects(
        db, current_user.id, status=status, offset=offset, limit=limit
    )
    return ProjectListResponse(
        items=[ProjectResponse.model_validate(p) for p in items],
        total=total,
    )


@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    summary="Get a single project",
)
async def get_project(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns full project details.
    Returns 404 if the project doesn't exist OR belongs to another user.
    """
    project = await project_service.get_project(db, project_id, current_user.id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PROJECT_NOT_FOUND", "message": "Project not found"},
        )
    return ProjectResponse.model_validate(project)


@router.patch(
    "/{project_id}",
    response_model=ProjectResponse,
    summary="Update a project (rename / archive)",
)
async def update_project(
    project_id: str,
    body: ProjectUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Partially updates a project. All fields are optional.
    Archived projects can still be renamed but not re-activated via this endpoint.
    """
    project = await project_service.get_project(db, project_id, current_user.id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PROJECT_NOT_FOUND", "message": "Project not found"},
        )

    updated = await project_service.update_project(db, project, body)
    return ProjectResponse.model_validate(updated)
