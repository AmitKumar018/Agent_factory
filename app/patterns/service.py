from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.logging_utils import get_logger
from app.patterns.models import Pattern
from app.patterns.schemas import (
    PatternCreate,
    PatternResponse,
    PatternSearchHit,
    PatternSearchResponse,
    PatternUpdate,
)
from app.storage.vector_store import _embed_texts, get_patterns_collection

settings = get_settings()
logger = get_logger(__name__)


def _embed_text(pattern: Pattern) -> str:
    return "\n\n".join(
        [
            f"Intent: {pattern.intent}",
            f"When to use: {pattern.when_to_use}",
            f"Structure: {pattern.structure}",
        ]
    )


def _upsert_to_chroma(pattern: Pattern) -> None:
    """Best-effort mirror into ChromaDB; DB remains source of truth."""
    try:
        collection = get_patterns_collection()
        collection.upsert(
            ids=[pattern.id],
            documents=[_embed_text(pattern)],
            embeddings=_embed_texts([_embed_text(pattern)]),
            metadatas=[
                {
                    "pattern_id": pattern.id,
                    "name": pattern.name,
                    "tags": ",".join(pattern.tags),
                    "source": pattern.source or "",
                }
            ],
        )
    except Exception as exc:
        logger.warning("pattern_vector_upsert_skipped", pattern_id=pattern.id, error=str(exc))


def _delete_from_chroma(pattern_id: str) -> None:
    try:
        get_patterns_collection().delete(ids=[pattern_id])
    except Exception as exc:
        logger.warning("pattern_vector_delete_skipped", pattern_id=pattern_id, error=str(exc))


async def create_pattern(db: AsyncSession, data: PatternCreate, source: str = "user") -> Pattern:
    pattern = Pattern(
        id=str(uuid.uuid4()),
        name=data.name,
        intent=data.intent,
        structure=data.structure,
        when_to_use=data.when_to_use,
        when_not_to_use=data.when_not_to_use,
        prerequisites=data.prerequisites,
        references=data.references,
        source=source,
    )
    pattern.tags = data.tags

    db.add(pattern)
    await db.commit()
    await db.refresh(pattern)

    _upsert_to_chroma(pattern)
    return pattern


async def get_pattern_by_id(db: AsyncSession, pattern_id: str) -> Optional[Pattern]:
    result = await db.execute(select(Pattern).where(Pattern.id == pattern_id))
    return result.scalar_one_or_none()


async def get_pattern_by_name(db: AsyncSession, name: str) -> Optional[Pattern]:
    result = await db.execute(select(Pattern).where(Pattern.name == name))
    return result.scalar_one_or_none()


async def list_patterns(
    db: AsyncSession,
    tags: list[str] | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[Pattern], int]:
    query = select(Pattern)
    if tags:
        for tag in tags:
            query = query.where(Pattern._tags.contains(tag))

    total_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(total_q)).scalar_one()

    result = await db.execute(query.offset(offset).limit(limit).order_by(Pattern.created_at.desc()))
    return result.scalars().all(), total


async def update_pattern(db: AsyncSession, pattern: Pattern, data: PatternUpdate) -> Pattern:
    for field, value in data.model_dump(exclude_none=True).items():
        if field == "tags":
            pattern.tags = value
        else:
            setattr(pattern, field, value)

    pattern.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(pattern)

    _upsert_to_chroma(pattern)
    return pattern


async def delete_pattern(db: AsyncSession, pattern: Pattern) -> None:
    _delete_from_chroma(pattern.id)
    await db.delete(pattern)
    await db.commit()


async def _keyword_search_patterns(
    db: AsyncSession,
    query: str,
    tags: list[str],
    top_k: int,
) -> PatternSearchResponse:
    """SQL/source-of-truth search used when vector search is empty or unavailable."""
    result = await db.execute(select(Pattern).order_by(Pattern.created_at.desc()))
    query_tokens = {token.strip().lower() for token in query.replace("-", " ").split() if token.strip()}

    scored: list[tuple[float, Pattern]] = []
    for pattern in result.scalars().all():
        if tags and not any(tag in pattern.tags for tag in tags):
            continue
        haystack = " ".join(
            [
                pattern.name or "",
                pattern.intent or "",
                pattern.structure or "",
                pattern.when_to_use or "",
                pattern.when_not_to_use or "",
                " ".join(pattern.tags),
            ]
        ).lower()
        matched = sum(1 for token in query_tokens if token in haystack)
        if matched == 0 and query_tokens:
            continue
        score = matched / max(len(query_tokens), 1)
        scored.append((score, pattern))

    scored.sort(key=lambda item: item[0], reverse=True)
    hits = [
        PatternSearchHit(
            pattern=PatternResponse.from_orm_model(pattern),
            similarity_score=round(score, 4),
            matched_fields=["keyword_fallback"],
        )
        for score, pattern in scored[:top_k]
    ]
    return PatternSearchResponse(query=query, results=hits, total=len(hits))


async def search_patterns(
    db: AsyncSession,
    query: str,
    tags: list[str],
    top_k: int,
) -> PatternSearchResponse:
    try:
        collection = get_patterns_collection()
    except RuntimeError as exc:
        logger.warning("pattern_chroma_search_skipped", error=str(exc))
        return await _keyword_search_patterns(db, query, tags, top_k)

    vector_count = collection.count()
    if vector_count == 0:
        return await _keyword_search_patterns(db, query, tags, top_k)

    # Chroma metadata filters do not support list containment portably, so tag
    # filtering is applied against the SQL row, which is the source of truth.
    fetch_k = max(top_k * 4, top_k)
    chroma_kwargs = {
        "query_embeddings": _embed_texts([query]),
        "n_results": min(fetch_k, vector_count),
        "include": ["documents", "metadatas", "distances"],
    }

    results = collection.query(**chroma_kwargs)

    hits: list[PatternSearchHit] = []
    ids = results["ids"][0]
    distances = results["distances"][0]
    metadatas = results["metadatas"][0]

    for chroma_id, distance, _meta in zip(ids, distances, metadatas):
        pattern = await get_pattern_by_id(db, chroma_id)
        if not pattern:
            continue
        if tags and not any(tag in pattern.tags for tag in tags):
            continue

        hits.append(
            PatternSearchHit(
                pattern=PatternResponse.from_orm_model(pattern),
                similarity_score=max(0.0, round(1 - distance, 4)),
                matched_fields=["intent", "when_to_use", "structure"],
            )
        )
        if len(hits) >= top_k:
            break

    return PatternSearchResponse(query=query, results=hits, total=len(hits))