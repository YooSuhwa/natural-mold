from __future__ import annotations

import uuid

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.error_codes import session_not_found, system_llm_not_configured
from app.models.skill import Skill
from app.models.skill_builder_session import SkillBuilderSession
from app.routers.skill_builder_audit import secret_scan_audit_metadata
from app.schemas.skill_builder import SkillBuilderStatus
from app.services import audit_service, skill_builder_service
from app.services.system_credential_resolver import (
    SystemModelNotConfiguredError,
    resolve_system_model,
)
from app.skills import service as skill_service


async def get_session_or_404(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    user: CurrentUser,
) -> SkillBuilderSession:
    session = await skill_builder_service.get_session(db, session_id, user.id)
    if session is None:
        raise session_not_found()
    return session


async def completed_skill(
    db: AsyncSession,
    *,
    session: SkillBuilderSession,
    user: CurrentUser,
) -> Skill | None:
    if session.status != SkillBuilderStatus.COMPLETED.value or session.finalized_skill_id is None:
        return None
    return await skill_service.get_skill(db, session.finalized_skill_id, user.id)


async def require_system_llm(
    db: AsyncSession,
    *,
    user: CurrentUser,
    request: Request,
) -> None:
    try:
        await resolve_system_model(db, "text_primary")
    except SystemModelNotConfiguredError as exc:
        await record_builder_audit(
            db,
            user=user,
            request=request,
            action="skill_builder.system_model_missing",
            mode="unknown",
            outcome="denied",
            reason_code="SYSTEM_LLM_NOT_CONFIGURED",
        )
        await db.commit()
        raise system_llm_not_configured() from exc


async def record_builder_audit(
    db: AsyncSession,
    *,
    user: CurrentUser,
    request: Request,
    action: str,
    outcome: str = "success",
    session_id: uuid.UUID | None = None,
    mode: str | None = None,
    source_skill_id: uuid.UUID | None = None,
    **metadata: object,
) -> None:
    await audit_service.record_self_event(
        db,
        user,
        action=action,
        target_type="skill_builder_session",
        target_id=session_id,
        outcome=outcome,
        request=request,
        metadata={
            "session_id": str(session_id) if session_id else None,
            "mode": mode,
            "source_skill_id": str(source_skill_id) if source_skill_id else None,
            **metadata,
        },
    )


async def record_secret_scan_blocked_if_needed(
    db: AsyncSession,
    *,
    user: CurrentUser,
    request: Request,
    session: SkillBuilderSession,
    validation_result: dict[str, object],
) -> None:
    secret_metadata = secret_scan_audit_metadata(validation_result)
    if secret_metadata is None:
        return
    await record_builder_audit(
        db,
        user=user,
        request=request,
        action="skill_builder.secret_scan_blocked",
        session_id=session.id,
        mode=session.mode,
        source_skill_id=session.source_skill_id,
        outcome="denied",
        **secret_metadata,
    )
