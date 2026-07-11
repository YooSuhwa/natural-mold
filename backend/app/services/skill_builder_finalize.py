"""빌더 챗 finalize 오케스트레이션 (M5, 스펙 AD-3).

``finalize_skill`` 도구가 승인 후 호출한다. v1 confirm 플로우를 최대 재사용:
워크스페이스 → ``SkillDraftPackage`` → ``save_draft_package``(REVIEW) →
``claim_for_confirming`` → ``confirm_builder_session``(검증 재실행 + secret
scan + 생성/개선 + 리비전 + eval 수거). 감사 어휘도 v1 계승
(``skill_builder.confirm_create``/``apply_improvement``/``apply_conflict``/
``secret_scan_blocked`` + ``skill_revision.create``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
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
from app.skills.packager import PackageError

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

    # REST /confirm과 동일한 상태 게이트(R2) — save_draft_package가 상태를
    # 무조건 REVIEW로 리셋하므로, 게이트 없이 동시 finalize B의 save가 A의
    # CONFIRMING claim을 되돌려 이중 confirm이 가능해진다 (run 뮤텍스로
    # 완화되나 도구 경로 자체도 닫는다).
    if session.status == SkillBuilderStatus.CONFIRMING.value:
        return _error("SESSION_CONFIRMING", "another finalize is already in progress")

    # 바이너리 asset은 confirm 단계의 디스크 기반 zip(build_workspace_zip_bytes)이
    # 그대로 싣는다 (Phase 1.5) — draft_package(text 어댑터)는 검증/메타데이터용.
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
        # zip_from_workspace=True — 빌더 챗 경로는 워크스페이스 디스크가 source of
        # truth라 바이너리 asset을 포함한 zip을 만든다 (Phase 1.5). REST /confirm은
        # 게시된 draft_package 계약을 유지하므로 이 플래그를 켜지 않는다.
        skill = await skill_builder_service.confirm_session(
            db, session, user_id=user_id, zip_from_workspace=True
        )
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
        # claim이 CONFIRMING을 독립 커밋한 뒤의 실패 — conflict/validation 경로와
        # 대칭으로 REVIEW로 되돌려야 재시도가 self-heal된다(rollback만으로는 이미
        # 커밋된 CONFIRMING을 못 되돌려 CONFIRMING 게이트가 재시도를 영구 차단).
        await db.rollback()
        await _release_confirming_claim(db, session_id, user_id)
        return _error("SOURCE_SKILL_NOT_FOUND", "source skill not found")
    except PackageError as exc:
        # zip 추출 가드(크기/파일 수/경로 방어) 실패 — 바이너리 asset이 실리며
        # 패키지 상한 초과가 현실화됐다(Phase 1.5). 위 경로들과 대칭으로 claim을
        # 풀어 에이전트가 파일을 줄인 뒤 재시도할 수 있게 사유를 그대로 전한다.
        await db.rollback()
        await _release_confirming_claim(db, session_id, user_id)
        return _error("PACKAGE_INVALID", str(exc))
    except asyncio.CancelledError:
        # 런 취소(stop)는 BaseException이라 아래 Exception 캐치와 도구 경로의
        # 광역 catch를 모두 통과한다 — claim 해제만은 shield로 완료시키고
        # 재전파해 CONFIRMING 잠금 잔존을 막는다 (자체 세션이라 요청 세션
        # teardown과 무관하게 끝까지 커밋된다).
        with contextlib.suppress(Exception):
            await asyncio.shield(_release_confirming_claim(db, session_id, user_id))
        raise
    except Exception:
        # 예기치 못한 실패(transient DB 오류 등)도 claim을 해제해 세션이
        # 거짓 "다른 finalize 진행 중" 상태에 갇히지 않게 한다.
        await db.rollback()
        await _release_confirming_claim(db, session_id, user_id)
        raise

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


async def _release_confirming_claim(
    _db: AsyncSession, session_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    """post-claim 실패 시 CONFIRMING → REVIEW 복귀 (best-effort self-heal).

    자체 세션을 연다 — 취소(shield) 경로에서 요청 세션이 teardown돼도 복귀
    커밋이 완료되고, 조건부 UPDATE(claim과 대칭)라 원자적이다. 호출자 세션은
    rollback 상태 그대로 둔다.
    """

    try:
        async with async_session() as fresh:
            await fresh.execute(
                update(SkillBuilderSession)
                .where(
                    SkillBuilderSession.id == session_id,
                    SkillBuilderSession.user_id == user_id,
                    SkillBuilderSession.status == SkillBuilderStatus.CONFIRMING.value,
                )
                .values(status=SkillBuilderStatus.REVIEW.value)
            )
            await fresh.commit()
    except Exception:
        logger.exception("failed to release confirming claim session=%s", session_id)


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
    await audit_service.record_self_event(
        db,
        actor,
        action=action,
        target_type="skill_builder_session",
        target_id=session.id,
        outcome=outcome,
        metadata={
            "session_id": str(session.id),
            "mode": session.mode,
            "source_skill_id": (str(session.source_skill_id) if session.source_skill_id else None),
            **metadata,
        },
    )
