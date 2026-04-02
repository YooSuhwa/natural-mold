from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import Skill
from app.schemas.skill import SkillCreate, SkillUpdate


async def list_skills(db: AsyncSession, user_id: uuid.UUID) -> list[Skill]:
    result = await db.execute(
        select(Skill)
        .where(Skill.user_id == user_id)
        .order_by(Skill.updated_at.desc())
    )
    return list(result.scalars().all())


async def get_skill(db: AsyncSession, skill_id: uuid.UUID, user_id: uuid.UUID) -> Skill | None:
    result = await db.execute(
        select(Skill).where(Skill.id == skill_id, Skill.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def create_skill(db: AsyncSession, data: SkillCreate, user_id: uuid.UUID) -> Skill:
    skill = Skill(
        user_id=user_id,
        name=data.name,
        description=data.description,
        content=data.content,
    )
    db.add(skill)
    await db.commit()
    await db.refresh(skill)
    return skill


async def update_skill(db: AsyncSession, skill: Skill, data: SkillUpdate) -> Skill:
    if data.name is not None:
        skill.name = data.name
    if data.description is not None:
        skill.description = data.description
    if data.content is not None:
        skill.content = data.content
    await db.commit()
    await db.refresh(skill)
    return skill


async def delete_skill(db: AsyncSession, skill: Skill) -> None:
    await db.delete(skill)
    await db.commit()
