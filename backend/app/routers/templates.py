from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.exceptions import NotFoundError
from app.schemas.template import TemplateResponse
from app.services import template_service

router = APIRouter(prefix="/api/templates", tags=["templates"])


@router.get("", response_model=list[TemplateResponse])
async def list_templates(
    category: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    return await template_service.list_templates(db, category)


@router.get("/{template_id}", response_model=TemplateResponse)
async def get_template(template_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    template = await template_service.get_template(db, template_id)
    if not template:
        raise NotFoundError("TEMPLATE_NOT_FOUND", "템플릿을 찾을 수 없습니다")
    return template
