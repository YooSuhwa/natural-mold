"""빌더 챗 finalize 오케스트레이션 (M5, 스펙 AD-3).

``finalize_skill`` 도구가 승인 후 호출한다. v1 confirm 플로우를 최대 재사용:
워크스페이스 → ``SkillDraftPackage`` → ``save_draft_package``(REVIEW) →
``claim_for_confirming`` → ``confirm_builder_session``(검증 재실행 + secret
scan + 생성/개선 + 리비전 + eval 수거). 감사 어휘도 v1 계승
(``skill_builder.confirm_create``/``apply_improvement``/``apply_conflict``/
``secret_scan_blocked`` + ``skill_revision.create``).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.models.skill_builder_session import SkillBuilderSession
from app.models.user import User
from app.routers.skill_builder_audit import (
    confirm_audit_metadata,
    secret_scan_audit_metadata,
)
from app.schemas.skill_builder import SkillBuilderMode, SkillBuilderStatus
from app.services import audit_service, skill_builder_service
from app.services import skill_draft_workspace as workspace
from app.services.skill_builder_errors import (
    SkillBuilderConflictError,
    SkillBuilderSourceSkillNotFound,
    SkillBuilderValidationError,
)
from app.services.skill_revision_audit import record_revision_create_audit

logger = logging.getLogger(__name__)

SKILL_DETAIL_DEEPLINK = "/skills?detailId={skill_id}"


async def finalize_draft_session(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    """finalize 전체 플로우 실행 — 도구 결과로 쓸 dict 반환 (커밋 포함).

    성공: ``{skill_id, slug, name, content_hash, deeplink, validation_result}``.
    실패: ``{error_code, message, ...}`` — 에이전트가 사용자에게 설명한다.
    """

    session = await skill_builder_service.get_session(db, session_id, user_id)
    if session is None:
        return _error("SESSION_NOT_FOUND", "builder session not found")

    # 멱등: 이미 확정된 세션이면 기존 skill 정보를 그대로 반환.
    if (
        session.status == SkillBuilderStatus.COMPLETED.value
        and session.finalized_skill_id is not None
    ):
        from app.skills import service as skill_service

        existing = await skill_service.get_skill(db, session.finalized_skill_id, user_id)
        if existing is not None:
            return _success(session, existing)

    if not session.draft_workspace_path:
        return _error("DRAFT_WORKSPACE_MISSING", "draft workspace is not attached")

    binaries = workspace.binary_package_files(session.draft_workspace_path)
    if binaries:
        # text-only 어댑터가 zip 소스라 바이너리는 조용히 누락된다 — improve
        # 시드 원본의 asset 손실을 막기 위해 fail-closed (Phase 1.5 백로그).
        return _error(
            "BINARY_FILES_UNSUPPORTED",
            "binary package files are not supported by builder-chat finalize yet: "
            + ", ".join(binaries[:10]),
        )

    draft = workspace.build_draft_package(session.draft_workspace_path)
    await skill_builder_service.save_draft_package(db, session, draft=draft.model_dump(mode="json"))
    await db.commit()

    claimed = await skill_builder_service.claim_for_confirming(db, session.id, user_id)
    if not claimed:
        return _error("SESSION_CONFIRMING", "another finalize is already in progress")

    session = await skill_builder_service.get_session(db, session_id, user_id)
    if session is None:
        return _error("SESSION_NOT_FOUND", "builder session not found")

    actor = await _actor_for(db, user_id)
    try:
        skill = await skill_builder_service.confirm_session(db, session, user_id=user_id)
    except SkillBuilderConflictError as exc:
        await _record_audit(
            db,
            actor=actor,
            action="skill_builder.apply_conflict",
            session=session,
            outcome="denied",
            metadata={
                "old_hash": exc.base_content_hash,
                "new_hash": exc.current_content_hash,
            },
        )
        await db.commit()
        return _error(
            "SOURCE_SKILL_CHANGED",
            "the source skill changed while this session was open; "
            "start a new improve session from the latest version",
        )
    except SkillBuilderValidationError as exc:
        if secret_scan_audit_metadata(exc.result) is not None:
            await _record_audit(
                db,
                actor=actor,
                action="skill_builder.secret_scan_blocked",
                session=session,
                outcome="denied",
                metadata=dict(secret_scan_audit_metadata(exc.result) or {}),
            )
        await db.commit()
        return {
            "error_code": "VALIDATION_FAILED",
            "message": "draft validation failed; fix the reported issues and retry",
            "session_id": str(session.id),
            "validation_result": exc.result,
        }
    except SkillBuilderSourceSkillNotFound:
        await db.rollback()
        return _error("SOURCE_SKILL_NOT_FOUND", "source skill not found")

    await _record_audit(
        db,
        actor=actor,
        action=(
            "skill_builder.apply_improvement"
            if session.mode == SkillBuilderMode.IMPROVE.value
            else "skill_builder.confirm_create"
        ),
        session=session,
        outcome="success",
        metadata=dict(confirm_audit_metadata(session, skill)),
    )
    if skill.current_revision_id is not None:
        from app.models.skill_revision import SkillRevision

        revision = await db.get(SkillRevision, skill.current_revision_id)
        if revision is not None:
            await record_revision_create_audit(
                db,
                user=actor,
                request=None,  # type: ignore[arg-type] — 도구 경로: HTTP request 없음
                revision=revision,
            )
    await db.commit()
    await db.refresh(skill)
    return _success(session, skill)


def _success(session: SkillBuilderSession, skill: Any) -> dict[str, Any]:
    return {
        "session_id": str(session.id),
        "skill_id": str(skill.id),
        "slug": skill.slug,
        "name": skill.name,
        "content_hash": skill.content_hash,
        "version": skill.version,
        "mode": session.mode,
        # 완료 카드 딥링크 (스펙 5.1 — Phase 2에서 스튜디오 라우트로 승격).
        "deeplink": SKILL_DETAIL_DEEPLINK.format(skill_id=skill.id),
        "validation_result": session.validation_result,
    }


def _error(code: str, message: str) -> dict[str, Any]:
    return {"error_code": code, "message": message}


async def _actor_for(db: AsyncSession, user_id: uuid.UUID) -> CurrentUser:
    user = await db.get(User, user_id)
    email = user.email if user is not None else ""
    name = user.name if user is not None else ""
    return CurrentUser(id=user_id, email=email, name=name)


async def _record_audit(
    db: AsyncSession,
    *,
    actor: CurrentUser,
    action: str,
    session: SkillBuilderSession,
    outcome: str,
    metadata: dict[str, Any],
) -> None:
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=actor.id,
        actor_email_snapshot=actor.email,
        owner_user_id=actor.id,
        owner_email_snapshot=actor.email,
        action=action,
        target_type="skill_builder_session",
        target_id=session.id,
        target_owner_user_id=actor.id,
        outcome=outcome,
        request=None,
        metadata={
            "session_id": str(session.id),
            "mode": session.mode,
            "source_skill_id": (str(session.source_skill_id) if session.source_skill_id else None),
            **metadata,
        },
    )
