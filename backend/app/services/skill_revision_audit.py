from __future__ import annotations

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.models.skill_revision import SkillRevision
from app.services import audit_service


async def record_revision_create_audit(
    db: AsyncSession,
    *,
    user: CurrentUser,
    request: Request,
    revision: SkillRevision,
) -> None:
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action="skill_revision.create",
        target_type="skill_revision",
        target_id=revision.id,
        target_owner_user_id=user.id,
        outcome="success",
        request=request,
        metadata={
            "skill_id": str(revision.skill_id),
            "revision_number": revision.revision_number,
            "operation": revision.operation,
            "content_hash": revision.content_hash,
            "file_count": revision.file_count,
        },
    )
