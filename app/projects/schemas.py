
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
from app.projects.models import ProjectStatus


# --- Request bodies ---

class ProjectCreate(BaseModel):
    """Body for POST /projects"""
    name: str = Field(..., min_length=1, max_length=255, description="Human-readable project name")
    description: Optional[str] = Field(None, max_length=2000)


class ProjectUpdate(BaseModel):
    """Body for PATCH /projects/{project_id} — all fields optional"""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)
    status: Optional[ProjectStatus] = None


# --- Response bodies ---

class ProjectResponse(BaseModel):
    """Returned for every project read/create/update operation"""
    id: str
    owner_id: str
    name: str
    description: Optional[str]
    status: ProjectStatus
    created_at: datetime
    updated_at: datetime

    # Tell Pydantic to read from SQLAlchemy ORM objects directly
    model_config = {"from_attributes": True}


class ProjectListResponse(BaseModel):
    """Paginated list of projects"""
    items: list[ProjectResponse]
    total: int
