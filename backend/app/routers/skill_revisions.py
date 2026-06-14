from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.error_codes import skill_not_found, skill_revision_not_found
from app.models.skill import Skill
from app.schemas.skill import SkillResponse
from app.schemas.skill_revision import (
    SkillRevisionDetail,
    SkillRevisionSummary,
    SkillRollbackResponse,
)
from app.services import audit_service, skill_revision_audit, skill_revision_service
from app.skills import service as skill_service

router = APIRouter(prefix="/api/skills/{skill_id}/revisions", tags=["skill-revisions"])


@router.get("", response_model=list[SkillRevisionSummary])
async def list_skill_revisions(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[SkillRevisionSummary]:
    skill = await _load_skill_or_404(db, skill_id=skill_id, user=user)
    revisions = await skill_revision_service.list_revisions(db, skill=skill, user_id=user.id)
    return [SkillRevisionSummary.model_validate(revision) for revision in revisions]


@router.get("/{revision_id}", response_model=SkillRevisionDetail)
async def get_skill_revision(
    skill_id: uuid.UUID,
    revision_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> SkillRevisionDetail:
    skill = await _load_skill_or_404(db, skill_id=skill_id, user=user)
    revision = await skill_revision_service.get_revision(
        db,
        skill=skill,
        user_id=user.id,
        revision_id=revision_id,
    )
    if revision is None:
        raise skill_revision_not_found()
    return SkillRevisionDetail.model_validate(revision)


@router.post("/{revision_id}/rollback", response_model=SkillRollbackResponse)
async def rollback_skill_revision(
    skill_id: uuid.UUID,
    revision_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> SkillRollbackResponse:
    skill = await _load_skill_or_404(db, skill_id=skill_id, user=user)
    revision = await skill_revision_service.get_revision(
        db,
        skill=skill,
        user_id=user.id,
        revision_id=revision_id,
    )
    if revision is None:
        raise skill_revision_not_found()
    restored = await skill_revision_service.rollback_to_revision(
        db,
        skill=skill,
        user_id=user.id,
        revision=revision,
        changelog_summary=f"Rolled back to revision {revision.revision_number}.",
    )
    await skill_revision_audit.record_revision_create_audit(
        db,
        user=user,
        request=request,
        revision=restored,
    )
    await _record_revision_rollback_audit(
        db,
        user=user,
        request=request,
        skill_id=skill.id,
        restored_revision_id=revision.id,
        new_revision_id=restored.id,
        old_hash=revision.content_hash,
        new_hash=skill.content_hash,
    )
    await db.commit()
    await db.refresh(skill)
    await db.refresh(restored)
    return SkillRollbackResponse(
        skill=SkillResponse.model_validate(skill),
        revision=SkillRevisionSummary.model_validate(restored),
    )


async def _load_skill_or_404(
    db: AsyncSession,
    *,
    skill_id: uuid.UUID,
    user: CurrentUser,
) -> Skill:
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if skill is None:
        raise skill_not_found()
    return skill


async def _record_revision_rollback_audit(
    db: AsyncSession,
    *,
    user: CurrentUser,
    request: Request,
    skill_id: uuid.UUID,
    restored_revision_id: uuid.UUID,
    new_revision_id: uuid.UUID,
    old_hash: str | None,
    new_hash: str | None,
) -> None:
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action="skill_revision.rollback",
        target_type="skill",
        target_id=skill_id,
        target_owner_user_id=user.id,
        outcome="success",
        request=request,
        metadata={
            "restored_revision_id": str(restored_revision_id),
            "new_revision_id": str(new_revision_id),
            "old_hash": old_hash,
            "new_hash": new_hash,
        },
    )
