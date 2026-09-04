import pytest
from sqlalchemy import select

from app.patterns.models import Pattern
from app.patterns.seed import CANONICAL_PATTERNS, seed_patterns
from app.patterns import service as pattern_service
from app.storage.database import AsyncSessionLocal, init_db


@pytest.mark.asyncio
async def test_seed_patterns_is_idempotent_and_searchable():
    await init_db()
    async with AsyncSessionLocal() as db:
        await seed_patterns(db)
        await seed_patterns(db)

        result = await db.execute(select(Pattern).where(Pattern.source == "seed"))
        seeded = result.scalars().all()
        seeded_names = {pattern.name for pattern in seeded}
        canonical_names = {entry["name"] for entry in CANONICAL_PATTERNS}

        assert canonical_names.issubset(seeded_names)
        assert len([pattern for pattern in seeded if pattern.name == "Tool-Use"]) == 1

        response = await pattern_service.search_patterns(
            db,
            query="function calls external tools api execution",
            tags=[],
            top_k=5,
        )

        assert response.total > 0
        assert any(hit.pattern.name == "Tool-Use" for hit in response.results)


@pytest.mark.asyncio
async def test_pattern_search_filters_by_tags_from_sql_source_of_truth():
    await init_db()
    async with AsyncSessionLocal() as db:
        await seed_patterns(db)

        response = await pattern_service.search_patterns(
            db,
            query="self critique revise quality iteration",
            tags=["self-critique"],
            top_k=10,
        )

        assert response.total > 0
        assert all("self-critique" in hit.pattern.tags for hit in response.results)
        assert any(hit.pattern.name == "Reflection" for hit in response.results)