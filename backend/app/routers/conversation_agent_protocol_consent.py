"""HITL 세션 동의 처리 (스킬 빌더 챗, 스펙 AD-4).

프론트가 ``input.respond`` decision에 실어 보내는 확장 필드 ``scope:"session"``
("이 세션에서 계속 허용")을 여기서 소비한다:

1. 세션 row(``skill_builder_sessions.tool_consents``)에 동의를 기록하고,
2. decision에서 ``scope`` 키를 **제거**해 미들웨어에는 표준
   approve/reject/edit만 내려보낸다 — 비표준 decision 필드는 langchain
   ``HumanInTheLoopMiddleware`` 검증을 깨뜨린다.

경계 (AD-4): 동의 대상은 ``SESSION_CONSENT_ELIGIBLE_TOOLS`` 뿐이고
(``finalize_skill`` 은 항상 카드), 드래프트가 ``requires_network`` 면 동의를
기록하지 않는다 — 이번 승인만 1회 유효.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.skill_builder.tools import SESSION_CONSENT_ELIGIBLE_TOOLS
from app.models.skill_builder_session import SkillBuilderSession
from app.routers.conversation_agent_protocol_interrupts import ThreadInterrupt
from app.routers.conversation_agent_protocol_resume import ResumePayload
from app.routers.conversation_agent_protocol_resume_redaction import _action_requests
from app.services import skill_builder_service, skill_draft_workspace

logger = logging.getLogger(__name__)


async def apply_session_consent_decisions(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    resume: ResumePayload,
    pending_interrupts: list[ThreadInterrupt],
) -> list[str]:
    """``scope:"session"`` 동의를 기록하고 decision에서 scope 키를 벗겨낸다.

    decision dict를 **in-place** 로 수정한다 (``resume.input_payload`` 와
    ``resume.submitted`` 가 같은 dict를 참조). 기록된 도구명 목록을 반환한다
    (동의 불가 조건이면 빈 목록 — 이번 승인은 표준 approve로 1회 진행).
    """

    actions_by_interrupt = {
        interrupt["id"]: _action_requests(interrupt.get("value"))
        for interrupt in pending_interrupts
    }
    requested: list[str] = []
    for submitted in resume.submitted:
        response = submitted.response
        if not isinstance(response, Mapping):
            continue
        decisions = response.get("decisions")
        if not isinstance(decisions, list):
            continue
        actions = actions_by_interrupt.get(submitted.interrupt_id, [])
        for index, decision in enumerate(decisions):
            if not isinstance(decision, dict) or "scope" not in decision:
                continue
            # 비표준 키는 대상 여부와 무관하게 항상 제거한다.
            scope = decision.pop("scope")
            if scope != "session" or decision.get("type") != "approve":
                continue
            action = actions[index] if index < len(actions) else None
            name = action.get("name") if isinstance(action, Mapping) else None
            if isinstance(name, str) and name in SESSION_CONSENT_ELIGIBLE_TOOLS:
                requested.append(name)

    if not requested:
        return []

    result = await db.execute(
        select(SkillBuilderSession).where(
            SkillBuilderSession.conversation_id == conversation_id,
            SkillBuilderSession.user_id == user_id,
        )
    )
    session = result.scalar_one_or_none()
    if session is None:
        # 빌더 대화가 아니면 동의는 무의미 — scope 제거만으로 충분.
        return []
    if session.draft_workspace_path and skill_draft_workspace.draft_requires_network(
        session.draft_workspace_path
    ):
        logger.info("session consent skipped — draft requires network (session=%s)", session.id)
        return []

    tool_names = sorted(set(requested))
    await skill_builder_service.record_tool_consents(db, session, tool_names=tool_names)
    return tool_names


__all__ = ["apply_session_consent_decisions"]
