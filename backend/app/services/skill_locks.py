from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import is_postgres
from app.models.skill import Skill


class SkillMutationLockNotFound(RuntimeError):
    pass


async def lock_skill_for_mutation(db: AsyncSession, *, skill: Skill) -> Skill:
    stmt = select(Skill).where(Skill.id == skill.id).execution_options(populate_existing=True)
    if is_postgres(db):
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    locked = result.scalar_one_or_none()
    if locked is None:
        raise SkillMutationLockNotFound("skill not found while acquiring mutation lock")
    return locked
