"""Builder session CRUD, claim, catalog and v3 streaming (split from ``builder_service``)."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.middleware_registry import get_middleware_registry
from app.agent_runtime.streaming import format_sse
from app.authz.builder_scope import resolve_builder_actor
from app.authz.org_context import OrgContext
from app.authz.private_actor import (
    PrivateResourceActor,
    require_private_actor,
    resolve_private_actor,
)
from app.authz.scoping import private_scope_predicate, private_scoped
from app.database import async_session as async_session_factory
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.builder_session import BuilderSession
from app.schemas.builder import BuilderStatus
from app.schemas.conversation import Decision
from app.services.tool_service import get_tools_catalog

logger = logging.getLogger("app.services.builder_service")


# ---------------------------------------------------------------------------
# Resume wire 어댑터 (router-only) — ADR-012
# ---------------------------------------------------------------------------


def decisions_to_builder_response(decisions: list[Decision]) -> Any:
    """표준 ``Decision[]`` → builder graph 가 기대하는 native shape.

    builder v3 wait 노드들은 dict|str 응답을 처리한다
    (``parse_approval_response``, phase6 ``image_choice``/``image_approval``).
    frontend 는 표준 ``Decision[]`` wire 만 유지하고, builder native shape 으로
    의 변환 책임은 router 경계의 본 helper 가 단일 책임으로 흡수한다.

    Mapping:
    - ``approve`` → ``{"approved": True}``
    - ``reject`` → ``{"approved": False, "revision_message": message or ""}``
    - ``respond`` → ``message or ""`` (string)
    - ``edit`` → ``{"approved": True}`` (builder 는 edit args 를 사용하지 않음)
    - empty list → ``None``
    """
    if not decisions:
        return None
    first = decisions[0]
    if first.type == "approve":
        return {"approved": True}
    if first.type == "reject":
        return {"approved": False, "revision_message": first.message or ""}
    if first.type == "respond":
        return first.message or ""
    if first.type == "edit":
        # builder edit 미사용 — approve fallback (graph 행동은 동일)
        return {"approved": True}
    return None


# ---------------------------------------------------------------------------
# 세션 CRUD
# ---------------------------------------------------------------------------


async def create_session(
    db: AsyncSession, context: OrgContext, user_request: str
) -> BuilderSession:
    """빌드 세션을 생성한다."""
    await resolve_private_actor(
        db, PrivateResourceActor(context.user_id, context.org_id, context.tenant_id)
    )
    session = BuilderSession(
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        user_request=user_request,
        status=BuilderStatus.BUILDING,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def get_session(
    db: AsyncSession, session_id: uuid.UUID, context: OrgContext
) -> BuilderSession | None:
    await require_private_actor(db, context)
    result = await db.execute(
        private_scoped(
            select(BuilderSession).where(BuilderSession.id == session_id), BuilderSession, context
        )
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# 원자적 상태 전환 (재진입 방지)
# ---------------------------------------------------------------------------


async def claim_for_confirming(
    db: AsyncSession, session_id: uuid.UUID, context: OrgContext
) -> bool:
    """PREVIEW → CONFIRMING 원자적 전환. 성공하면 True.

    동시 confirm 요청이 오더라도 하나만 성공한다.
    """
    if await get_session(db, session_id, context) is None:
        return False
    try:
        await resolve_builder_actor(db, session_id, context.user_id)
    except AppError:
        return False
    claimed_id = await db.scalar(
        update(BuilderSession)
        .where(
            BuilderSession.id == session_id,
            private_scope_predicate(BuilderSession, context),
            BuilderSession.status == BuilderStatus.PREVIEW,
        )
        .values(status=BuilderStatus.CONFIRMING)
        .returning(BuilderSession.id)
    )
    await db.commit()
    return claimed_id is not None


async def get_agent_by_id(
    db: AsyncSession, agent_id: uuid.UUID, context: OrgContext
) -> Agent | None:
    """Agent를 ID로 조회한다 (멱등 confirm 반환용)."""
    await require_private_actor(db, context)
    result = await db.execute(
        private_scoped(select(Agent).where(Agent.id == agent_id), Agent, context)
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# 카탈로그 조회 (서브에이전트 동적 주입용, AD-7)
# ---------------------------------------------------------------------------


def _get_middlewares_catalog() -> list[dict[str, Any]]:
    """사용 가능한 미들웨어 카탈로그를 조회한다.

    deepagents가 자동 추가하는 빌트인 미들웨어는 제외하여
    중복 추가로 인한 오류를 방지한다.
    """
    return get_middleware_registry(exclude_builtin=True)


# ---------------------------------------------------------------------------
# 모델 조회 (v3 노드 동적 주입용)
# ---------------------------------------------------------------------------


async def _get_default_model_name(db: AsyncSession) -> str:
    """에이전트에 할당할 기본 모델의 provider:model_name을 조회한다.

    우선순위:
    1. 환경변수 DEFAULT_AGENT_MODEL (설정된 경우)
    2. DB에서 is_default=True인 모델
    3. DB의 첫 번째 모델
    4. 빈 문자열 (phase6에서 fallback 처리)
    """
    from app.config import settings
    from app.models.model import Model

    # 1. 환경변수
    if settings.default_agent_model:
        return settings.default_agent_model

    # 2. DB default — Model 테이블에는 is_default unique 제약이 없으므로
    #    is_default=true row 가 여러 개여도 raise 하지 않도록 결정적 first 행 선택
    result = await db.execute(
        select(Model).where(Model.is_default.is_(True)).order_by(Model.created_at.asc()).limit(1)
    )
    model = result.scalars().first()
    if model:
        return f"{model.provider}:{model.model_name}"

    # 3. 아무 모델 — 마찬가지로 결정적 순서 보장
    result = await db.execute(select(Model).order_by(Model.created_at.asc()).limit(1))
    model = result.scalars().first()
    if model:
        return f"{model.provider}:{model.model_name}"

    return ""


# ---------------------------------------------------------------------------
# Builder v3 — StateGraph 기반 메시지 스트리밍
# ---------------------------------------------------------------------------


async def run_v3_message_stream(
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    content: str,
) -> AsyncGenerator[str, None]:
    """Builder v3 StateGraph로 메시지를 스트리밍한다.

    - 첫 메시지: 카탈로그/모델/세션 정보를 inject한 full state로 시작
    - 후속 메시지: messages만 추가 (state는 checkpoint에서 복원)
    """
    from langchain_core.messages import HumanMessage

    from app.agent_runtime.builder_v3.graph import compile_graph
    from app.agent_runtime.builder_v3.state import initial_todos
    from app.agent_runtime.checkpointer import get_checkpointer
    from app.agent_runtime.streaming import stream_agent_response

    async with async_session_factory() as db:
        context = await resolve_builder_actor(db, session_id, user_id)
        tools_catalog = await get_tools_catalog(db, context)
        default_model_name = await _get_default_model_name(db)
        middlewares_catalog = _get_middlewares_catalog()

    checkpointer = get_checkpointer()
    graph_compiled = compile_graph(checkpointer)
    config: dict[str, Any] = {"configurable": {"thread_id": str(session_id)}}

    state_snapshot = await graph_compiled.aget_state(config)
    is_first = not state_snapshot.values

    if is_first:
        graph_input: Any = {
            "messages": [HumanMessage(content=content)],
            "user_id": str(user_id),
            "session_id": str(session_id),
            "org_id": str(context.org_id),
            "tenant_id": str(context.tenant_id),
            "user_request": content,
            "tools_catalog": tools_catalog,
            "middlewares_catalog": middlewares_catalog,
            "default_model_name": default_model_name,
            "current_phase": 1,
            "todos": initial_todos(),
        }
    else:
        graph_input = [HumanMessage(content=content)]

    async for chunk in stream_agent_response(graph_compiled, graph_input, config):
        yield chunk


class StaleInterruptError(Exception):
    """resume이 현재 paused interrupt와 매칭되지 않음 (stale 카드 클릭)."""


async def run_v3_resume_stream(
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    response: Any,
    interrupt_id: str | None = None,
) -> AsyncGenerator[str, None]:
    """interrupt 응답을 받아 Command(resume=...)로 그래프를 재개한다.

    interrupt_id가 제공되면 현재 paused interrupt의 ns와 비교하여 stale 카드로 인한
    오용을 차단한다. None이면 검증 skip (backward compatibility).
    """
    from langgraph.types import Command

    from app.agent_runtime.builder_v3.graph import compile_graph
    from app.agent_runtime.checkpointer import get_checkpointer
    from app.agent_runtime.streaming import stream_agent_response

    async with async_session_factory() as db:
        await resolve_builder_actor(db, session_id, user_id)

    checkpointer = get_checkpointer()
    graph_compiled = compile_graph(checkpointer)
    config: dict[str, Any] = {"configurable": {"thread_id": str(session_id)}}

    # interrupt_id stale 검증
    if interrupt_id:
        try:
            state = await graph_compiled.aget_state(config)
            current_ids: list[str] = []
            for task in state.tasks or []:
                for intr in task.interrupts or []:
                    current_ids.append(str(getattr(intr, "ns", "")))
            if current_ids and interrupt_id not in current_ids:
                # stale interrupt — 사용자에게 알리고 graph는 재개하지 않음
                logger.warning(
                    "Stale interrupt resume rejected. expected=%s got=%s",
                    current_ids,
                    interrupt_id,
                )
                yield format_sse("message_start", {"id": "stale", "role": "assistant"})
                yield format_sse(
                    "error",
                    {
                        "message": (
                            "이 카드는 이미 처리되었거나 만료되었습니다. "
                            "최신 카드에서 응답해주세요."
                        )
                    },
                )
                yield format_sse("message_end", {"usage": {}, "content": ""})
                return
        except Exception:  # pragma: no cover
            logger.warning("interrupt_id validation failed", exc_info=True)

    async for chunk in stream_agent_response(graph_compiled, Command(resume=response), config):
        yield chunk
