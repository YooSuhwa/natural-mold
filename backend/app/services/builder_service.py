"""Builder 서비스 — 세션 관리, v3 메시지 스트리밍, confirm 로직."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent_runtime.middleware_registry import get_middleware_registry
from app.agent_runtime.streaming import format_sse
from app.database import async_session as async_session_factory
from app.models.agent import Agent
from app.models.builder_session import BuilderSession
from app.models.skill import AgentSkillLink
from app.models.tool import AgentToolLink, Tool
from app.schemas.builder import BuilderStatus
from app.services.model_service import resolve_model
from app.services.tool_service import get_tools_catalog

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 세션 CRUD
# ---------------------------------------------------------------------------


async def create_session(db: AsyncSession, user_id: uuid.UUID, user_request: str) -> BuilderSession:
    """빌드 세션을 생성한다."""
    session = BuilderSession(
        user_id=user_id,
        user_request=user_request,
        status=BuilderStatus.BUILDING,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def get_session(
    db: AsyncSession, session_id: uuid.UUID, user_id: uuid.UUID
) -> BuilderSession | None:
    result = await db.execute(
        select(BuilderSession).where(
            BuilderSession.id == session_id,
            BuilderSession.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# 원자적 상태 전환 (재진입 방지)
# ---------------------------------------------------------------------------


async def claim_for_confirming(db: AsyncSession, session_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """PREVIEW → CONFIRMING 원자적 전환. 성공하면 True.

    동시 confirm 요청이 오더라도 하나만 성공한다.
    """
    result = await db.execute(
        update(BuilderSession)
        .where(
            BuilderSession.id == session_id,
            BuilderSession.user_id == user_id,
            BuilderSession.status == BuilderStatus.PREVIEW,
        )
        .values(status=BuilderStatus.CONFIRMING)
    )
    await db.commit()
    return result.rowcount == 1  # type: ignore[return-value]


async def get_agent_by_id(db: AsyncSession, agent_id: uuid.UUID) -> Agent | None:
    """Agent를 ID로 조회한다 (멱등 confirm 반환용)."""
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
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

    # 2. DB default
    result = await db.execute(select(Model).where(Model.is_default.is_(True)))
    model = result.scalar_one_or_none()
    if model:
        return f"{model.provider}:{model.model_name}"

    # 3. 아무 모델
    result = await db.execute(select(Model).limit(1))
    model = result.scalar_one_or_none()
    if model:
        return f"{model.provider}:{model.model_name}"

    return ""


# ---------------------------------------------------------------------------
# 빌드 확인 (confirm) — confirm_creation 로직 재사용
# ---------------------------------------------------------------------------


async def confirm_build(db: AsyncSession, session: BuilderSession) -> Agent | None:
    """빌드 확인: draft_config를 기반으로 실제 Agent를 생성한다.

    agent_creation_service.confirm_creation()의 도구/모델 매칭 로직을 재사용.
    예외 발생 시 세션을 PREVIEW로 롤백하여 CONFIRMING 고착을 방지한다.
    """
    config = session.draft_config
    if not config:
        return None

    try:
        # 모델 매칭 — strict 조회 실패 시 fallback
        model_name = config.get("model_name", "")
        model = await resolve_model(db, model_name, strict=True) if model_name else None
        if not model:
            # strict 실패 → default 모델로 fallback
            model = await resolve_model(db, "", strict=False)
        if not model:
            # default도 없으면 → DB의 아무 모델 사용
            from app.models.model import Model as ModelORM

            any_result = await db.execute(select(ModelORM).limit(1))
            model = any_result.scalar_one_or_none()
        if not model:
            raise ValueError(
                "사용 가능한 모델이 없습니다. 모델 설정 페이지에서 모델을 등록해주세요."
            )

        # 도구 매칭 — 이름으로 DB Tool 레코드 조회
        tools_to_link = await _resolve_tools(db, session.user_id, config.get("tools", []))

        # 에이전트 생성
        agent = Agent(
            user_id=session.user_id,
            name=config.get("name_ko") or config.get("name", "새 에이전트"),
            description=config.get("description", ""),
            system_prompt=config.get("system_prompt", ""),
            model_id=model.id,
            middleware_configs=[
                {"type": mw_name, "params": {}} for mw_name in config.get("middlewares", [])
            ],
        )
        agent.tool_links = [AgentToolLink(tool_id=t.id) for t in tools_to_link]
        db.add(agent)
        await db.flush()  # agent.id 할당을 위해 flush 필요

        session.status = BuilderStatus.COMPLETED
        session.agent_id = agent.id

        await db.commit()
        await db.refresh(agent, ["model", "tool_links"])

        # 이미지 처리: phase7_save가 draft_config["image_url"]을 항상 명시적으로 set.
        #   truthy → 임시 파일을 Agent 디렉토리로 이동
        #   None/falsy → 사용자가 phase6에서 명시적 skip
        image_url = (config or {}).get("image_url")
        if image_url:
            await _transfer_builder_image(session, agent, image_url)
            # agent.image_path 변경을 DB에 반영
            await db.commit()
            await db.refresh(agent)

        # 이미지 생성이 commit하면 관계가 expire되므로 selectinload로 재로드
        result = await db.execute(
            select(Agent)
            .where(Agent.id == agent.id)
            .options(
                selectinload(Agent.model),
                selectinload(Agent.tool_links).selectinload(AgentToolLink.tool),
                selectinload(Agent.skill_links).selectinload(AgentSkillLink.skill),
            )
        )
        return result.scalar_one()
    except Exception:
        # CONFIRMING 고착 방지: 예외 발생 시 PREVIEW로 롤백
        await db.rollback()
        session.status = BuilderStatus.PREVIEW
        await db.commit()
        raise


async def _resolve_tools(
    db: AsyncSession,
    user_id: uuid.UUID,
    tool_names: list[str],
) -> list[Tool]:
    """도구 이름 목록 → Tool DB 레코드 리스트.

    agent_creation_service.confirm_creation()의 패턴 재사용:
    func.lower(Tool.name).in_(lower_names)
    """
    if not tool_names:
        return []
    lower_names = [n.lower() for n in tool_names]
    result = await db.execute(
        select(Tool).where(
            or_(Tool.user_id == user_id, Tool.is_system.is_(True)),
            func.lower(Tool.name).in_(lower_names),
        )
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Builder v3 — StateGraph 기반 메시지 스트리밍
# ---------------------------------------------------------------------------


def _transfer_builder_image_sync(
    session_id: uuid.UUID,
    agent_id: uuid.UUID,
    public_url: str,
) -> str | None:
    """Sync I/O — copy file + cleanup builder temp dir. asyncio.to_thread로 호출.

    Returns: agent.image_path 값 (None이면 실패).
    """
    import shutil
    from pathlib import Path

    from app.agent_runtime.builder_v3.image_gen import resolve_local_path
    from app.config import settings

    filename = Path(public_url).name
    src = resolve_local_path(str(session_id), filename)
    if not src:
        logger.warning("Builder image source not found: %s", public_url)
        return None

    dest_dir = Path(settings.agent_image_dir) / str(agent_id)
    dest = dest_dir / f"avatar{src.suffix}"
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        # 직접 copy — 파일 사라지면 FileNotFoundError로 처리 (TOCTOU 방어)
        shutil.copy(src, dest)
    except FileNotFoundError:
        logger.warning("Builder image disappeared before copy: %s", src)
        return None
    except Exception:
        logger.warning("Builder image transfer failed", exc_info=True)
        return None

    # Cleanup: builder temp dir 정리 (실패해도 무시)
    builder_dir = src.parent
    if builder_dir.name == str(session_id):
        shutil.rmtree(builder_dir, ignore_errors=True)

    return str(dest)


async def _transfer_builder_image(
    session: BuilderSession,
    agent: Agent,
    public_url: str,
) -> None:
    """Phase 6 임시 이미지 → Agent 디렉토리 복사 (async wrapper).

    실패해도 Agent 생성은 유지한다.
    """
    result = await asyncio.to_thread(
        _transfer_builder_image_sync, session.id, agent.id, public_url
    )
    if result:
        agent.image_path = result


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
        # async DB call 두 개 병렬, sync 호출은 직접
        tools_catalog, default_model_name = await asyncio.gather(
            get_tools_catalog(db, user_id),
            _get_default_model_name(db),
        )
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
