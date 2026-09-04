
from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


# ── Inbound ───────────────────────────────────────────────────────────────────

class PatternCreate(BaseModel):
    name:            str         = Field(..., min_length=1, max_length=120)
    intent:          str         = Field(..., min_length=1)
    structure:       str         = Field(..., min_length=1)
    when_to_use:     str         = Field(..., min_length=1)
    when_not_to_use: str         = Field(..., min_length=1)
    prerequisites:   str         = Field(..., min_length=1)
    references:      Optional[str] = None
    tags:            list[str]   = Field(default_factory=list)


class PatternUpdate(BaseModel):
    """All fields optional — PATCH semantics."""
    name:            Optional[str] = Field(None, min_length=1, max_length=120)
    intent:          Optional[str] = None
    structure:       Optional[str] = None
    when_to_use:     Optional[str] = None
    when_not_to_use: Optional[str] = None
    prerequisites:   Optional[str] = None
    references:      Optional[str] = None
    tags:            Optional[list[str]] = None


class PatternSearchRequest(BaseModel):
    query:  str            = Field(..., min_length=1)
    tags:   list[str]      = Field(default_factory=list)
    top_k:  int            = Field(default=8, ge=1, le=50)


# ── Outbound ──────────────────────────────────────────────────────────────────

class PatternResponse(BaseModel):
    id:              str
    name:            str
    intent:          str
    structure:       str
    when_to_use:     str
    when_not_to_use: str
    prerequisites:   str
    references:      Optional[str]
    tags:            list[str]
    source:          str
    created_at:      datetime
    updated_at:      datetime

    model_config = {"from_attributes": True}

    # flatten the tags property from the ORM model
    @classmethod
    def from_orm_model(cls, obj) -> "PatternResponse":
        return cls(
            id=obj.id,
            name=obj.name,
            intent=obj.intent,
            structure=obj.structure,
            when_to_use=obj.when_to_use,
            when_not_to_use=obj.when_not_to_use,
            prerequisites=obj.prerequisites,
            references=obj.references,
            tags=obj.tags,          # uses the @property
            source=obj.source,
            created_at=obj.created_at,
            updated_at=obj.updated_at,
        )


class PatternListResponse(BaseModel):
    items: list[PatternResponse]
    total: int


class PatternSearchHit(BaseModel):
    pattern:         PatternResponse
    similarity_score: float          = Field(..., ge=0.0, le=1.0)
    matched_fields:  list[str]       # e.g. ["intent", "when_to_use"]


class PatternSearchResponse(BaseModel):
    query:   str
    results: list[PatternSearchHit]
    total:   int
