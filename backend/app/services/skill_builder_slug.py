from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import Skill
from app.skills import service as skill_service


async def unique_skill_slug(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    requested: str,
    exclude_skill_id: uuid.UUID | None = None,
) -> str:
    base = skill_service.slugify(requested)
    existing = await _matching_slugs(
        db,
        user_id=user_id,
        base=base,
        exclude_skill_id=exclude_skill_id,
    )
    if base not in existing:
        return base
    suffix = 2
    while f"{base}-{suffix}" in existing:
        suffix += 1
    return f"{base}-{suffix}"


async def _matching_slugs(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    base: str,
    exclude_skill_id: uuid.UUID | None,
) -> set[str]:
    stmt = select(Skill.slug).where(
        Skill.user_id == user_id,
        or_(Skill.slug == base, Skill.slug.like(f"{base}-%")),
    )
    if exclude_skill_id is not None:
        stmt = stmt.where(Skill.id != exclude_skill_id)
    result = await db.execute(stmt)
    return set(result.scalars().all())
