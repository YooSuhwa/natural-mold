"""Builder v2 서비스 — 세션 관리, 파이프라인 실행, confirm 로직."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.builder.orchestrator import run_builder_pipeline
from app.agent_runtime.middleware_registry import get_middleware_registry
from app.agent_runtime.streaming import format_sse
from app.database import async_session as async_session_factory
from app.models.agent import Agent
from app.models.builder_session import BuilderSession
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


async def claim_for_streaming(db: AsyncSession, session_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """BUILDING → STREAMING 원자적 전환. 성공하면 True.

    동시 GET /stream 요청이 오더라도 하나만 성공한다.
    STREAMING 상태의 re-claim은 허용하지 않는다 — 2개 SSE 소비자가
    동시에 파이프라인을 실행하는 것을 방지한다.
    이전 SSE 연결이 끊어진 경우: finally 롤백이 STREAMING → BUILDING으로
    되돌려주므로, 클라이언트는 재시도 시 BUILDING에서 새로 claim 가능하다.
    """
    result = await db.execute(
        update(BuilderSession)
        .where(
            BuilderSession.id == session_id,
            BuilderSession.user_id == user_id,
            BuilderSession.status == BuilderStatus.BUILDING,
        )
        .values(status=BuilderStatus.STREAMING)
    )
    await db.commit()
    return result.rowcount == 1  # type: ignore[return-value]


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
    """사용 가능한 미들웨어 카탈로그를 조회한다."""
    return get_middleware_registry()


# ---------------------------------------------------------------------------
# 파이프라인 실행 (SSE 스트리밍)
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


async def _save_phase_result(
    session_id: uuid.UUID,
    state: dict[str, Any],
) -> None:
    """완료된 phase의 중간 결과를 DB에 점진적으로 저장한다.

    SSE 스트림 중단 시에도 이미 완료된 phase 결과가 유실되지 않도록 한다.
    """
    phase = state.get("current_phase", 0)
    try:
        async with async_session_factory() as db:
            result = await db.execute(select(BuilderSession).where(BuilderSession.id == session_id))
            session = result.scalar_one_or_none()
            if not session:
                return

            session.current_phase = phase
            if "project_path" in state:
                session.project_path = state["project_path"]
            if "intent" in state:
                session.intent = state["intent"]
            if "tools" in state:
                session.tools_result = state["tools"]
            if "middlewares" in state:
                session.middlewares_result = state["middlewares"]
            if "system_prompt" in state:
                session.system_prompt = state["system_prompt"]
            if "draft_config" in state:
                session.draft_config = state["draft_config"]

            await db.commit()
    except Exception:
        logger.warning(
            "Failed to save phase %d result for session %s",
            phase,
            session_id,
            exc_info=True,
        )


def _has_phase_completed(events: list[dict]) -> bool:
    """SSE 이벤트 목록에 phase 완료 이벤트가 있는지 확인한다."""
    for event in events:
        event_type = event.get("event_type", "")
        if not event_type:
            # event_type 없으면 구조 기반 추론
            if "phase" in event and event.get("status") == "completed":
                return True
        elif event_type == "phase_progress" and event.get("status") == "completed":
            return True
    return False


async def run_build_stream(
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    user_request: str,
) -> AsyncGenerator[str, None]:
    """Builder 파이프라인을 실행하고 SSE 이벤트를 스트리밍한다.

    SSE 스트리밍은 응답이 반환된 뒤에도 계속 실행되므로,
    라우터의 Depends(get_db) 세션에 의존하면 세션이 닫힐 수 있다.
    따라서 내부에서 자체 DB 세션을 생성하여 라이프사이클을 관리한다.

    각 phase 완료 시 결과를 DB에 점진적으로 저장하여,
    SSE 중단 시에도 완료된 phase 결과가 유실되지 않도록 한다.
    """
    async with async_session_factory() as db:
        tools_catalog = await get_tools_catalog(db, user_id)
        middlewares_catalog = _get_middlewares_catalog()
        default_model_name = await _get_default_model_name(db)

    final_state: dict[str, Any] = {}
    _stream_finished = False

    try:
        try:
            async for update in run_builder_pipeline(
                user_id=str(user_id),
                user_request=user_request,
                session_id=str(session_id),
                tools_catalog=tools_catalog,
                middlewares_catalog=middlewares_catalog,
                default_model_name=default_model_name,
            ):
                # SSE 이벤트 전송
                events = update.get("events", [])
                for event in events:
                    event_type = _detect_event_type(event)
                    yield format_sse(event_type, event)

                # 중간 상태 업데이트 수집
                state_update = update.get("state_update", {})
                final_state.update(state_update)

                # phase 완료 이벤트가 있으면 해당 phase 결과를 DB에 점진적 저장
                if _has_phase_completed(events):
                    await _save_phase_result(session_id, final_state)
        except Exception as exc:
            logger.exception("Builder pipeline error in session %s", session_id)
            yield format_sse(
                "error",
                {
                    "message": "빌드 파이프라인에서 오류가 발생했습니다. 다시 시도해주세요.",
                    "recoverable": False,
                },
            )
            # 세션 상태를 failed로 업데이트
            async with async_session_factory() as db:
                result = await db.execute(
                    select(BuilderSession).where(BuilderSession.id == session_id)
                )
                session = result.scalar_one_or_none()
                if session:
                    session.status = BuilderStatus.FAILED
                    session.error_message = str(exc)
                    await db.commit()
            _stream_finished = True
            yield format_sse("stream_end", {})
            return

        # 파이프라인 완료 — fresh DB 세션으로 업데이트
        async with async_session_factory() as db:
            result = await db.execute(select(BuilderSession).where(BuilderSession.id == session_id))
            session = result.scalar_one_or_none()
            if not session:
                _stream_finished = True
                yield format_sse("error", {"message": "세션을 찾을 수 없습니다."})
                yield format_sse("stream_end", {})
                return

            error = final_state.get("error", "")
            if error:
                session.status = BuilderStatus.FAILED
                session.error_message = error
            else:
                session.status = BuilderStatus.PREVIEW

            session.current_phase = final_state.get("current_phase", 0)
            session.project_path = final_state.get("project_path", "")
            session.intent = final_state.get("intent")
            session.tools_result = final_state.get("tools")
            session.middlewares_result = final_state.get("middlewares")
            session.system_prompt = final_state.get("system_prompt")
            session.draft_config = final_state.get("draft_config")

            await db.commit()

        # 최종 이벤트
        _stream_finished = True
        if error:
            yield format_sse("build_failed", {"message": error})
        else:
            yield format_sse(
                "build_preview",
                {"draft_config": final_state.get("draft_config") or {}},
            )
        yield format_sse("stream_end", {})
    finally:
        # 안전망: SSE 연결이 중간에 끊기면 (클라이언트 disconnect, 네트워크 오류)
        # 세션이 STREAMING에 영구 고착되는 것을 방지한다.
        # 정상 완료/실패 경로에서는 이미 다른 상태로 전환되었으므로
        # WHERE status=STREAMING 조건에 걸리지 않는다.
        # 중간 결과는 이미 phase 완료 시점에 DB에 저장되어 있다.
        if not _stream_finished:
            try:
                async with async_session_factory() as db:
                    await db.execute(
                        update(BuilderSession)
                        .where(
                            BuilderSession.id == session_id,
                            BuilderSession.status == BuilderStatus.STREAMING,
                        )
                        .values(status=BuilderStatus.BUILDING)
                    )
                    await db.commit()
            except Exception:
                logger.warning(
                    "Failed to rollback STREAMING→BUILDING for session %s",
                    session_id,
                    exc_info=True,
                )


def _detect_event_type(event: dict) -> str:
    """SSE 이벤트 딕셔너리에서 이벤트 타입을 결정한다.

    event_type 필드가 있으면 그 값을 우선 사용한다.
    없으면 구조 기반으로 추론한다 (하위 호환성).
    """
    if "event_type" in event:
        return event["event_type"]
    if "phase" in event and "status" in event:
        return "phase_progress"
    if "agent_name" in event and "result_summary" in event:
        return "sub_agent_end"
    if "agent_name" in event:
        return "sub_agent_start"
    if "recoverable" in event:
        return "error"
    if "draft_config" in event:
        return "build_preview"
    return "info"


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
        return agent
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
