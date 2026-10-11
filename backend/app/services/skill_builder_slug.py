from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.models.skill import Skill
from app.skills import service as skill_service


async def unique_skill_slug(
    db: AsyncSession,
    *,
    context: OrgContext,
    requested: str,
    exclude_skill_id: uuid.UUID | None = None,
) -> str:
    base = skill_service.slugify(requested)
    existing = await _matching_slugs(
        db,
        context=context,
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
    context: OrgContext,
    base: str,
    exclude_skill_id: uuid.UUID | None,
) -> set[str]:
    stmt = select(Skill.slug).where(
        private_scope_predicate(Skill, context),
        or_(Skill.slug == base, Skill.slug.like(f"{base}-%")),
    )
    if exclude_skill_id is not None:
        stmt = stmt.where(Skill.id != exclude_skill_id)
    result = await db.execute(stmt)
    return set(result.scalars().all())
