"""Per-user 히든 스킬 빌더 에이전트 lazy-seed (스펙 AD-1).

``runtime_profile='skill_builder'`` Agent row를 첫 빌더 진입 시 생성한다.
사용자-노출 표면(목록/요약/네비게이터/일일 집계 등)은 ``runtime_profile ==
'standard'`` 필터로 이 row를 숨기고, PUT/DELETE는 enumeration-safe 404.

``model_id``는 FK 충족용 seed 시점 참조값일 뿐이다 — 런타임 분기는 항상
``resolve_system_model(db, 'text_primary')``로 재해석한다 (ADR-019, M3).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import AGENT_RUNTIME_PROFILE_SKILL_BUILDER, Agent
from app.models.model import Model
from app.services.system_credential_resolver import (
    SystemModelNotConfiguredError,
    resolve_system_model,
)

SKILL_BUILDER_AGENT_NAME = "스킬 빌더"

# 런타임이 skill_builder 프로필 분기에서 전용 prompt.md로 교체하므로(M3)
# row의 system_prompt는 실행에 쓰이지 않는 자리표시자다.
_PLACEHOLDER_PROMPT = (
    "Moldy hidden skill-builder agent. The runtime replaces this prompt "
    "for runtime_profile='skill_builder'; this stored value is never used."
)


async def get_or_create_skill_builder_agent(
    db: AsyncSession, user_id: uuid.UUID
) -> Agent:
    """사용자의 히든 빌더 에이전트를 반환한다 (없으면 생성, flush까지만)."""

    result = await db.execute(
        select(Agent)
        .where(
            Agent.user_id == user_id,
            Agent.runtime_profile == AGENT_RUNTIME_PROFILE_SKILL_BUILDER,
        )
        # 동시 시드로 중복이 생겨도 항상 같은 row를 고르도록 결정적 정렬.
        .order_by(Agent.created_at.asc(), Agent.id.asc())
        .limit(1)
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing

    resolved = await resolve_system_model(db, "text_primary")
    agent = Agent(
        user_id=user_id,
        name=SKILL_BUILDER_AGENT_NAME,
        description="스킬 빌더 챗 전용 히든 에이전트",
        system_prompt=_PLACEHOLDER_PROMPT,
        model_id=await _seed_model_id(db, resolved.model_name),
        runtime_profile=AGENT_RUNTIME_PROFILE_SKILL_BUILDER,
    )
    db.add(agent)
    await db.flush()
    return agent


async def _seed_model_id(db: AsyncSession, model_name: str) -> uuid.UUID:
    """FK 충족용 모델 id — 시스템 모델과 같은 ``model_name`` 우선, 없으면 카탈로그 첫 행."""

    result = await db.execute(
        select(Model.id).where(Model.model_name == model_name).limit(1)
    )
    model_id = result.scalar_one_or_none()
    if model_id is not None:
        return model_id
    result = await db.execute(
        select(Model.id).order_by(Model.created_at.asc(), Model.id.asc()).limit(1)
    )
    model_id = result.scalar_one_or_none()
    if model_id is None:
        # 모델 카탈로그가 비어 있으면 시스템 LLM 셋업이 실질적으로 미완 —
        # 기존 SYSTEM_LLM_NOT_CONFIGURED 계약으로 수렴시킨다.
        raise SystemModelNotConfiguredError("text_primary")
    return model_id
