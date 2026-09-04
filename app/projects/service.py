
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.projects.models import Project, ProjectStatus
from app.projects.schemas import ProjectCreate, ProjectUpdate


async def create_project(
    db: AsyncSession,
    owner_id: str,
    data: ProjectCreate,
) -> Project:
    """Create a new project owned by the given user."""
    project = Project(
        id=str(uuid.uuid4()),
        owner_id=owner_id,
        name=data.name,
        description=data.description,
        status=ProjectStatus.DRAFT,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project


async def get_project(
    db: AsyncSession,
    project_id: str,
    owner_id: str,
) -> Optional[Project]:
    """
    Fetch a project by ID, scoped to the owner.
    Returns None (→ 404) if not found OR if it belongs to a different user.
    We never return 403 to avoid leaking existence.
    """
    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.owner_id == owner_id,
        )
    )
    return result.scalar_one_or_none()


async def list_projects(
    db: AsyncSession,
    owner_id: str,
    status: Optional[ProjectStatus] = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[Project], int]:
    """
    Return a paginated list of projects for the given owner,
    optionally filtered by status. Returns (items, total_count).
    """
    query = select(Project).where(Project.owner_id == owner_id)
    count_query = select(func.count()).select_from(Project).where(
        Project.owner_id == owner_id
    )

    if status:
        query = query.where(Project.status == status)
        count_query = count_query.where(Project.status == status)

    query = query.order_by(Project.created_at.desc()).offset(offset).limit(limit)

    results = await db.execute(query)
    count_result = await db.execute(count_query)

    return results.scalars().all(), count_result.scalar_one()


async def update_project(
    db: AsyncSession,
    project: Project,
    data: ProjectUpdate,
) -> Project:
    """Apply partial updates to a project. Only updates fields that are provided."""
    if data.name is not None:
        project.name = data.name
    if data.description is not None:
        project.description = data.description
    if data.status is not None:
        project.status = data.status

    project.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(project)
    return project


async def set_project_status(
    db: AsyncSession,
    project: Project,
    status: ProjectStatus,
) -> Project:
    """Convenience helper to change only the project status."""
    project.status = status
    project.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(project)
    return project
