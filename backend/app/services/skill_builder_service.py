from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import JsonValue
from sqlalchemy import desc, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.private_actor import (
    PrivateResourceActor,
    require_private_actor,
    resolve_private_actor,
)
from app.authz.scoping import private_scope_predicate
from app.authz.skill_builder_scope import (
    private_skill_builder_predicate,
    resolve_skill_builder_actor,
)
from app.error_codes import session_not_found
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.skill import Skill
from app.models.skill_builder_session import SkillBuilderSession
from app.schemas.skill_builder import SkillBuilderMode, SkillBuilderStatus
from app.schemas.skill_builder_scope import SkillBuilderChatAttachment
from app.services.skill_builder_confirmation import confirm_builder_session
from app.services.skill_builder_errors import (
    SkillBuilderConflictError,
    SkillBuilderSourceSkillNotFound,
    SkillBuilderValidationError,
)
from app.services.skill_builder_mutation_scope import (
    SkillBuilderEvaluation,
    SkillBuilderMessage,
    SkillBuilderMutationTarget,
    load_mutation_session,
)
from app.skills import service as skill_service
from app.storage.paths import ensure_relative


async def create_session(
    db: AsyncSession,
    *,
    context: OrgContext,
    user_request: str,
    mode: SkillBuilderMode = SkillBuilderMode.CREATE,
    source_skill_id: uuid.UUID | None = None,
) -> SkillBuilderSession:
    await resolve_private_actor(
        db, PrivateResourceActor(context.user_id, context.org_id, context.tenant_id)
    )
    base_snapshot: dict[str, Any] | None = None
    base_skill_version: str | None = None
    base_content_hash: str | None = None
    if mode is SkillBuilderMode.IMPROVE:
        if source_skill_id is None:
            raise SkillBuilderSourceSkillNotFound("source skill is required")
        source_skill = await _get_owned_skill(db, source_skill_id, context)
        if source_skill is None:
            raise SkillBuilderSourceSkillNotFound("source skill not found")
        base_snapshot = await load_skill_snapshot(source_skill)
        base_skill_version = source_skill.version
        base_content_hash = source_skill.content_hash

    session = SkillBuilderSession(
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
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
    attachment: SkillBuilderChatAttachment,
) -> SkillBuilderSession:
    """v2 시작 플로우 — 빌더 대화/워크스페이스를 붙이고 상태를 ACTIVE로 올린다."""

    context = attachment.context
    session = await get_session(db, attachment.session_id, context)
    if session is None:
        raise session_not_found()
    await resolve_skill_builder_actor(db, session.id, context.user_id)
    conversation_id = await db.scalar(
        select(Conversation.id)
        .join(Agent, Agent.id == Conversation.agent_id)
        .where(
            Conversation.id == attachment.conversation_id,
            private_scope_predicate(Conversation, context),
            private_scope_predicate(Agent, context),
        )
    )
    if conversation_id is None:
        raise session_not_found()
    session.conversation_id = conversation_id
    session.draft_workspace_path = ensure_relative(attachment.draft_workspace_path)
    session.status = SkillBuilderStatus.ACTIVE.value
    session.updated_at = _now()
    await db.flush()
    return session


async def record_tool_consents(
    db: AsyncSession, target: SkillBuilderMutationTarget, tool_names: list[str]
) -> SkillBuilderSession:
    session = await load_mutation_session(db, target)
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
    context: OrgContext,
) -> uuid.UUID | None:
    """빌더 대화의 히든 에이전트 id (대화 미연결/삭제 시 None)."""

    scoped = await get_session(db, session.id, context)
    if scoped is None or scoped.conversation_id is None:
        return None
    result = await db.execute(
        select(Agent.id)
        .join(Conversation, Conversation.agent_id == Agent.id)
        .where(
            Conversation.id == scoped.conversation_id,
            private_scope_predicate(Conversation, context),
            private_scope_predicate(Agent, context),
        )
    )
    return result.scalar_one_or_none()


async def get_session(
    db: AsyncSession,
    session_id: uuid.UUID,
    context: OrgContext,
) -> SkillBuilderSession | None:
    await require_private_actor(db, context)
    result = await db.execute(
        select(SkillBuilderSession)
        .where(
            SkillBuilderSession.id == session_id,
            private_skill_builder_predicate(context),
        )
        .execution_options(populate_existing=True, autoflush=False)
    )
    return result.scalar_one_or_none()


async def list_sessions(
    db: AsyncSession,
    *,
    context: OrgContext,
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

    await require_private_actor(db, context)
    stmt = (
        select(SkillBuilderSession)
        .where(private_skill_builder_predicate(context))
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
    db: AsyncSession, target: SkillBuilderMutationTarget, message: SkillBuilderMessage
) -> SkillBuilderSession:
    session = await load_mutation_session(db, target)
    messages = list(session.messages or [])
    messages.append(
        {
            "role": message.role,
            "content": message.content,
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    session.messages = messages
    session.updated_at = _now()
    await db.flush()
    return session


async def save_draft_package(
    db: AsyncSession, target: SkillBuilderMutationTarget, draft: Mapping[str, JsonValue]
) -> SkillBuilderSession:
    session = await load_mutation_session(db, target)
    session.draft_package = dict(draft)
    session.status = SkillBuilderStatus.REVIEW.value
    session.updated_at = _now()
    await db.flush()
    return session


async def save_validation_result(
    db: AsyncSession, target: SkillBuilderMutationTarget, result: Mapping[str, JsonValue]
) -> SkillBuilderSession:
    session = await load_mutation_session(db, target)
    session.validation_result = dict(result)
    compatibility_result = result.get("compatibility_result")
    if isinstance(compatibility_result, dict):
        session.compatibility_result = compatibility_result
    session.updated_at = _now()
    await db.flush()
    return session


async def save_trigger_eval_result(
    db: AsyncSession, target: SkillBuilderMutationTarget, evaluation: SkillBuilderEvaluation
) -> SkillBuilderSession:
    session = await load_mutation_session(db, target)
    session.trigger_eval_result = dict(evaluation.result)
    session.draft_package = dict(evaluation.draft)
    session.updated_at = _now()
    await db.flush()
    return session


async def claim_for_confirming(
    db: AsyncSession,
    session_id: uuid.UUID,
    context: OrgContext,
) -> bool:
    if await get_session(db, session_id, context) is None:
        return False
    try:
        await resolve_skill_builder_actor(db, session_id, context.user_id)
    except AppError:
        return False
    claimed_id = await db.scalar(
        update(SkillBuilderSession)
        .where(
            SkillBuilderSession.id == session_id,
            private_scope_predicate(SkillBuilderSession, context),
            SkillBuilderSession.status == SkillBuilderStatus.REVIEW.value,
        )
        .values(status=SkillBuilderStatus.CONFIRMING.value, updated_at=_now())
        .returning(SkillBuilderSession.id)
    )
    await db.commit()
    return claimed_id is not None


async def confirm_session(
    db: AsyncSession,
    session: SkillBuilderSession,
    *,
    context: OrgContext,
    zip_from_workspace: bool = False,
) -> Skill:
    return await confirm_builder_session(
        db, session, context=context, zip_from_workspace=zip_from_workspace
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
    context: OrgContext,
) -> Skill | None:
    result = await db.execute(
        select(Skill).where(
            Skill.id == skill_id,
            private_scope_predicate(Skill, context),
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
