"""Phase 7 — 에이전트 설정 저장 (자동, LLM 불필요).

draft_config 조립 → DB(builder_session)에 저장 → status=PREVIEW.
DraftConfigCard ToolMessage emit + Phase 8로 자동 진행.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select

from app.agent_runtime.builder_v3.nodes._helpers import (
    build_phase_complete,
    ensure_todos,
    make_tool_card,
)
from app.agent_runtime.builder_v3.state import BuilderState
from app.database import async_session as async_session_factory
from app.models.builder_session import BuilderSession
from app.schemas.builder import (
    AgentCreationIntent,
    BuilderStatus,
    DraftAgentConfig,
    MiddlewareRecommendation,
    ToolRecommendation,
)

logger = logging.getLogger(__name__)


def _build_draft(state: BuilderState) -> DraftAgentConfig:
    intent_dict = state.get("intent") or {}
    intent = AgentCreationIntent(**intent_dict)
    tools = [ToolRecommendation(**t) for t in state.get("tools") or []]
    mws = [MiddlewareRecommendation(**m) for m in state.get("middlewares") or []]
    return DraftAgentConfig(
        name=intent.agent_name,
        name_ko=intent.agent_name_ko,
        description=intent.agent_description,
        system_prompt=state.get("system_prompt") or "",
        tools=[t.tool_name for t in tools],
        middlewares=[m.middleware_name for m in mws],
        model_name=state.get("default_model_name", ""),
        primary_task_type=intent.primary_task_type,
        use_cases=list(intent.use_cases),
    )


async def _persist_session(
    session_id: str,
    draft: DraftAgentConfig,
    image_url: str | None,
    current_phase: int,
    tools: list[dict[str, Any]],
    middlewares: list[dict[str, Any]],
) -> None:
    """builder_session에 phase 결과를 저장하고 status=PREVIEW로 전환."""
    if not session_id:
        return
    try:
        sid = uuid.UUID(session_id)
    except (TypeError, ValueError):
        return

    try:
        async with async_session_factory() as db:
            stmt = select(BuilderSession).where(BuilderSession.id == sid)
            row = (await db.execute(stmt)).scalar_one_or_none()
            if not row:
                return
            payload: dict[str, Any] = draft.model_dump(mode="json")
            # image_url은 항상 명시적으로 set — None은 사용자가 phase6에서 skip한 의미.
            payload["image_url"] = image_url
            row.draft_config = payload
            row.system_prompt = draft.system_prompt
            # ToolRecommendation/MiddlewareRecommendation 전체 객체 저장
            # (BuilderSessionResponse 스키마가 description/reason 필수)
            row.tools_result = list(tools)
            row.middlewares_result = list(middlewares)
            row.current_phase = current_phase
            row.status = BuilderStatus.PREVIEW
            await db.commit()
    except Exception:  # pragma: no cover
        logger.warning("Phase 7 persist failed", exc_info=True)


async def phase7_save(state: BuilderState) -> dict:
    draft = _build_draft(state)
    draft_dict: dict[str, Any] = draft.model_dump(mode="json")
    # image_url은 항상 명시적으로 set — None이면 사용자가 phase6에서 skip한 의미.
    draft_dict["image_url"] = state.get("image_url")

    await _persist_session(
        state.get("session_id", ""),
        draft,
        state.get("image_url"),
        7,
        list(state.get("tools") or []),
        list(state.get("middlewares") or []),
    )

    msgs, _ = make_tool_card(
        "draft_config_card",
        {
            "phase": 7,
            "title": "에이전트 설정 미리보기",
            "draft": draft_dict,
            "image_url": state.get("image_url"),
        },
        intro_text="모든 정보를 종합하여 에이전트 설정을 준비했습니다.",
    )

    complete_msgs = build_phase_complete(
        7,
        ensure_todos(state),
        "[Phase 7 완료] 설정 저장 완료. 마지막으로 최종 확인 후 빌드합니다.",
    )

    return {
        "messages": list(msgs) + list(complete_msgs),
        "draft_config": draft_dict,
        "current_phase": 8,
    }
