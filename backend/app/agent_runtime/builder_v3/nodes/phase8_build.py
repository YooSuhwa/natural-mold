"""Phase 8 — 최종 승인 + 빌드 (propose + wait 2-노드 패턴).

phase8_propose: draft_approval ToolMessage emit + dict 반환
phase8_build_wait: interrupt → 승인 시 Agent 생성 후 END, 수정 시 router로
"""

from __future__ import annotations

import logging
import uuid

from langchain_core.messages import AIMessage
from langgraph.types import interrupt
from sqlalchemy import select

from app.agent_runtime.builder_v3.nodes._helpers import (
    build_phase_complete,
    close_pending_tool_card,
    ensure_todos,
    make_pending_tool_card,
)
from app.agent_runtime.builder_v3.state import BuilderState
from app.database import async_session as async_session_factory
from app.models.builder_session import BuilderSession
from app.schemas.builder import BuilderStatus

logger = logging.getLogger(__name__)


async def _confirm_and_create_agent(state: BuilderState) -> tuple[str | None, str | None]:
    """builder_session.status=PREVIEW에서 Agent 생성까지 처리.

    Returns:
        (agent_id, error_message). 성공 시 agent_id만, 실패 시 error만 채워짐.
    """
    session_id_str = state.get("session_id", "")
    if not session_id_str:
        return None, "session_id 없음"
    try:
        sid = uuid.UUID(session_id_str)
    except ValueError:
        return None, "잘못된 session_id"

    from app.services.builder_service import claim_for_confirming, confirm_build

    try:
        async with async_session_factory() as db:
            stmt = select(BuilderSession).where(BuilderSession.id == sid)
            session = (await db.execute(stmt)).scalar_one_or_none()
            if not session:
                return None, "세션을 찾을 수 없음"

            if session.status == BuilderStatus.COMPLETED and session.agent_id:
                return str(session.agent_id), None

            if session.status != BuilderStatus.PREVIEW:
                return None, f"세션 상태가 PREVIEW가 아닙니다: {session.status}"

            claimed = await claim_for_confirming(db, sid, session.user_id)
            if not claimed:
                return None, "다른 요청이 처리 중입니다."

            session = (await db.execute(stmt)).scalar_one_or_none()
            if not session:
                return None, "세션이 사라졌습니다."
            agent = await confirm_build(db, session)
            if not agent:
                return None, "에이전트 생성 실패"
            return str(agent.id), None
    except Exception as exc:  # pragma: no cover
        logger.exception("Phase 8 confirm failed")
        return None, str(exc)


async def _persist_error(state: BuilderState, error_message: str) -> None:
    """builder_session.error_message + status=FAILED 기록 (frontend가 표시할 수 있도록)."""
    session_id_str = state.get("session_id", "")
    if not session_id_str:
        return
    try:
        sid = uuid.UUID(session_id_str)
    except ValueError:
        return
    try:
        async with async_session_factory() as db:
            stmt = select(BuilderSession).where(BuilderSession.id == sid)
            row = (await db.execute(stmt)).scalar_one_or_none()
            if row:
                row.error_message = error_message
                row.status = BuilderStatus.FAILED
                await db.commit()
    except Exception:  # pragma: no cover
        logger.warning("Phase 8 error persist failed", exc_info=True)


# ---------------------------------------------------------------------------
# Node: phase8_propose
# ---------------------------------------------------------------------------


async def phase8_propose(state: BuilderState) -> dict:
    """draft_approval ToolMessage emit + dict 반환 (interrupt 없음)."""
    draft = state.get("draft_config") or {}
    image_url = state.get("image_url") or draft.get("image_url")

    msgs, tool_call_id = make_pending_tool_card(
        "draft_approval",
        {
            "phase": 8,
            "title": "최종 확인",
            "draft": draft,
            "image_url": image_url,
            "summary": "이 설정으로 에이전트를 생성하시겠습니까?",
        },
        intro_text="아래 설정을 확인하고 '승인' 또는 '수정요청'을 선택해주세요.",
    )

    return {"messages": msgs, "pending_tool_call_id": tool_call_id}


# ---------------------------------------------------------------------------
# Node: phase8_build_wait
# ---------------------------------------------------------------------------


async def phase8_build_wait(state: BuilderState) -> dict:
    """interrupt → 승인 시 Agent 생성, 수정 시 last_revision_message만 set. 라우팅은 graph."""
    response = interrupt(
        {
            "type": "approval",
            "phase": 8,
            "kind": "final",
            "draft": state.get("draft_config") or {},
        }
    )

    approved = False
    revision = ""
    if isinstance(response, dict):
        approved = bool(response.get("approved"))
        revision = response.get("revision_message") or response.get("message") or ""
    elif isinstance(response, str):
        revision = response

    pending_tc_id = state.get("pending_tool_call_id")

    if approved:
        agent_id, error = await _confirm_and_create_agent(state)
        if agent_id:
            close_msgs = close_pending_tool_card(pending_tc_id, "draft_approval", "승인됨")
            complete_msgs = build_phase_complete(
                8,
                ensure_todos(state),
                "[Phase 8 완료] 에이전트가 생성되었습니다! 잠시 후 페이지로 이동합니다.",
            )
            return {
                "messages": [*close_msgs, *complete_msgs],
                "completed": True,
                "agent_id": agent_id,
                "pending_tool_call_id": None,
            }
        # 생성 실패 — 사용자에게 명시적으로 노출
        err_text = error or "에이전트 생성에 실패했습니다."
        await _persist_error(state, err_text)
        close_msgs = close_pending_tool_card(
            pending_tc_id, "draft_approval", f"실패: {err_text}"
        )
        return {
            "messages": [
                *close_msgs,
                AIMessage(
                    content=(
                        f"⚠️ 에이전트 생성에 실패했습니다.\n\n"
                        f"**원인**: {err_text}\n\n"
                        "관리자에게 문의하거나 모델 설정을 확인 후 다시 시도해주세요."
                    )
                ),
            ],
            "error_message": err_text,
            "pending_tool_call_id": None,
        }

    # 수정요청
    revision_text = revision or "수정 요청"
    close_msgs = close_pending_tool_card(
        pending_tc_id, "draft_approval", f"수정 요청: {revision_text}"
    )
    return {
        "messages": close_msgs,
        "last_revision_message": revision_text,
        "pending_tool_call_id": None,
    }
