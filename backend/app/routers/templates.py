from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db
from app.error_codes import template_not_found
from app.schemas.template import TemplateResponse
from app.services import template_service

# Templates are a global catalog (no per-user ownership) but reads still
# require an authenticated session — anonymous browsing isn't a feature
# and exposing the list publicly leaks our curated set. Mutations don't
# exist on this router today; if they're added later, gate them with
# ``Depends(require_super_user)`` (see ADR-016 §5.1).
router = APIRouter(prefix="/api/templates", tags=["templates"])


@router.get("", response_model=list[TemplateResponse])
async def list_templates(
    category: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    return await template_service.list_templates(db, category)


@router.get("/{template_id}", response_model=TemplateResponse)
async def get_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    template = await template_service.get_template(db, template_id)
    if not template:
        raise template_not_found()
    return template
