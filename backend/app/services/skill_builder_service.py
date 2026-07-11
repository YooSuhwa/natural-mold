from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.skill import Skill
from app.models.skill_builder_session import SkillBuilderSession
from app.schemas.skill_builder import SkillBuilderMode, SkillBuilderStatus
from app.services.skill_builder_confirmation import confirm_builder_session
from app.services.skill_builder_errors import (
    SkillBuilderConflictError,
    SkillBuilderSourceSkillNotFound,
    SkillBuilderValidationError,
)
from app.skills import service as skill_service
from app.storage.paths import ensure_relative


async def create_session(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    user_request: str,
    mode: SkillBuilderMode = SkillBuilderMode.CREATE,
    source_skill_id: uuid.UUID | None = None,
) -> SkillBuilderSession:
    base_snapshot: dict[str, Any] | None = None
    base_skill_version: str | None = None
    base_content_hash: str | None = None
    if mode is SkillBuilderMode.IMPROVE:
        if source_skill_id is None:
            raise SkillBuilderSourceSkillNotFound("source skill is required")
        source_skill = await _get_owned_skill(db, source_skill_id, user_id)
        if source_skill is None:
            raise SkillBuilderSourceSkillNotFound("source skill not found")
        base_snapshot = await load_skill_snapshot(source_skill)
        base_skill_version = source_skill.version
        base_content_hash = source_skill.content_hash

    session = SkillBuilderSession(
        user_id=user_id,
        user_request=user_request,
        mode=mode.value,
        source_skill_id=source_skill_id if mode is SkillBuilderMode.IMPROVE else None,
        base_skill_version=base_skill_version,
        base_content_hash=base_content_hash,
        base_snapshot=base_snapshot,
        status=SkillBuilderStatus.COLLECTING.value,
    )
    db.add(session)
    await db.flush()
    return session


async def attach_chat_runtime(
    db: AsyncSession,
    session: SkillBuilderSession,
    *,
    conversation_id: uuid.UUID,
    draft_workspace_path: str,
) -> SkillBuilderSession:
    """v2 시작 플로우 — 빌더 대화/워크스페이스를 붙이고 상태를 ACTIVE로 올린다."""

    session.conversation_id = conversation_id
    session.draft_workspace_path = ensure_relative(draft_workspace_path)
    session.status = SkillBuilderStatus.ACTIVE.value
    session.updated_at = _now()
    await db.flush()
    return session


async def record_tool_consents(
    db: AsyncSession,
    session: SkillBuilderSession,
    *,
    tool_names: list[str],
) -> SkillBuilderSession:
    """AD-4 스코프드 동의 기록 — 도구명 → 동의 메타데이터 (세션 단위)."""

    consents = dict(session.tool_consents or {})
    granted_at = datetime.now(UTC).isoformat()
    for name in tool_names:
        consents[name] = {"scope": "session", "granted_at": granted_at}
    session.tool_consents = consents
    session.updated_at = _now()
    await db.flush()
    return session


async def resolve_session_agent_id(
    db: AsyncSession,
    session: SkillBuilderSession,
) -> uuid.UUID | None:
    """빌더 대화의 히든 에이전트 id (대화 미연결/삭제 시 None)."""

    if session.conversation_id is None:
        return None
    result = await db.execute(
        select(Conversation.agent_id).where(Conversation.id == session.conversation_id)
    )
    return result.scalar_one_or_none()


async def get_session(
    db: AsyncSession,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
) -> SkillBuilderSession | None:
    result = await db.execute(
        select(SkillBuilderSession).where(
            SkillBuilderSession.id == session_id,
            SkillBuilderSession.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def list_sessions(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    skill_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = 20,
) -> list[SkillBuilderSession]:
    """사용자의 빌더 세션 목록 (스튜디오 빌더 탭/인덱스, Phase 2).

    ``skill_id``는 improve 원본(``source_skill_id``)과 create 산출물
    (``finalized_skill_id``) 양쪽에 매칭한다 — "이 스킬의 빌더 이력"을
    한 질의로 잡기 위함(인덱스 양쪽 존재). 상태 필터가 없으면 GC 대상인
    ``abandoned``를 기본 제외한다 — 대화가 SET NULL로 끊겨 재개 불가한
    세션을 클릭 가능한 행으로 노출하지 않기 위함(명시 status로는 조회 가능).
    """

    stmt = (
        select(SkillBuilderSession)
        .where(SkillBuilderSession.user_id == user_id)
        # updated_at은 트랜잭션/초 단위로 동률이 나므로 id 보조 정렬로 절단
        # 경계 행의 플랩을 막는다 (R5).
        .order_by(desc(SkillBuilderSession.updated_at), desc(SkillBuilderSession.id))
        .limit(limit)
    )
    if skill_id is not None:
        stmt = stmt.where(
            or_(
                SkillBuilderSession.source_skill_id == skill_id,
                SkillBuilderSession.finalized_skill_id == skill_id,
            )
        )
    if status is not None:
        stmt = stmt.where(SkillBuilderSession.status == status)
    else:
        stmt = stmt.where(SkillBuilderSession.status != SkillBuilderStatus.ABANDONED.value)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def append_message(
    db: AsyncSession,
    session: SkillBuilderSession,
    *,
    role: str,
    content: str,
) -> SkillBuilderSession:
    messages = list(session.messages or [])
    messages.append(
        {
            "role": role,
            "content": content,
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    session.messages = messages
    session.updated_at = _now()
    await db.flush()
    return session


async def save_draft_package(
    db: AsyncSession,
    session: SkillBuilderSession,
    *,
    draft: dict[str, Any],
) -> SkillBuilderSession:
    session.draft_package = draft
    session.status = SkillBuilderStatus.REVIEW.value
    session.updated_at = _now()
    await db.flush()
    return session


async def save_validation_result(
    db: AsyncSession,
    session: SkillBuilderSession,
    *,
    result: dict[str, Any],
) -> SkillBuilderSession:
    session.validation_result = result
    compatibility_result = result.get("compatibility_result")
    if isinstance(compatibility_result, dict):
        session.compatibility_result = compatibility_result
    session.updated_at = _now()
    await db.flush()
    return session


async def save_trigger_eval_result(
    db: AsyncSession,
    session: SkillBuilderSession,
    *,
    result: dict[str, Any],
    draft: dict[str, Any],
) -> SkillBuilderSession:
    session.trigger_eval_result = result
    session.draft_package = draft
    session.updated_at = _now()
    await db.flush()
    return session


async def claim_for_confirming(
    db: AsyncSession,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
) -> bool:
    result = await db.execute(
        update(SkillBuilderSession)
        .where(
            SkillBuilderSession.id == session_id,
            SkillBuilderSession.user_id == user_id,
            SkillBuilderSession.status == SkillBuilderStatus.REVIEW.value,
        )
        .values(status=SkillBuilderStatus.CONFIRMING.value, updated_at=_now())
    )
    await db.commit()
    return result.rowcount == 1


async def confirm_session(
    db: AsyncSession,
    session: SkillBuilderSession,
    *,
    user_id: uuid.UUID,
    zip_from_workspace: bool = False,
) -> Skill:
    return await confirm_builder_session(
        db, session, user_id=user_id, zip_from_workspace=zip_from_workspace
    )


async def load_skill_snapshot(skill: Skill) -> dict[str, Any]:
    files: list[dict[str, Any]]
    if skill.kind == "text":
        content = await skill_service.read_text_content(skill)
        files = [{"path": "SKILL.md", "content": content, "role": "skill"}]
    else:
        files = []
        for file_info in skill_service.get_skill_files(skill):
            if file_info.is_dir:
                continue
            raw = skill_service.get_file_bytes(skill, file_info.path)
            files.append(
                {
                    "path": file_info.path,
                    "content": raw.decode("utf-8", errors="replace"),
                    "role": _role_for_path(file_info.path),
                }
            )
    return {
        "skill_id": str(skill.id),
        "kind": skill.kind,
        "name": skill.name,
        "slug": skill.slug,
        "description": skill.description,
        "version": skill.version,
        "content_hash": skill.content_hash,
        "credential_requirements": skill.credential_requirements or [],
        "execution_profile": skill.execution_profile or {},
        "files": files,
    }


async def _get_owned_skill(
    db: AsyncSession,
    skill_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Skill | None:
    result = await db.execute(
        select(Skill).where(
            Skill.id == skill_id,
            Skill.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


def _role_for_path(path: str) -> str:
    # 정본은 skill_draft_workspace.role_for_path — 드래프트 어댑터와 스냅샷
    # 로더가 같은 role 규칙을 쓰도록 위임한다 (지연 import: 모듈 로드 순환 방지).
    from app.services.skill_draft_workspace import role_for_path

    return role_for_path(path)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


__all__ = [
    "SkillBuilderConflictError",
    "SkillBuilderSourceSkillNotFound",
    "SkillBuilderValidationError",
    "append_message",
    "claim_for_confirming",
    "confirm_session",
    "create_session",
    "get_session",
    "list_sessions",
    "load_skill_snapshot",
    "save_draft_package",
    "save_trigger_eval_result",
    "save_validation_result",
]
