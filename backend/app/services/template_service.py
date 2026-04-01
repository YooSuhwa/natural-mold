from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.template import Template


async def list_templates(db: AsyncSession, category: str | None = None) -> list[Template]:
    query = select(Template).order_by(Template.category, Template.name)
    if category:
        query = query.where(Template.category == category)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_template(db: AsyncSession, template_id: uuid.UUID) -> Template | None:
    result = await db.execute(select(Template).where(Template.id == template_id))
    return result.scalar_one_or_none()
