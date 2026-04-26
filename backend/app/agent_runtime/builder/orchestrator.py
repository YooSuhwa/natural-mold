"""Builder v2 오케스트레이터 — LangGraph StateGraph 7-phase 파이프라인.

Phase 1: 프로젝트 초기화 (no LLM)
Phase 2: 의도 분석 (서브에이전트)
Phase 3: 도구 추천 (서브에이전트)
Phase 4: 미들웨어 추천 (서브에이전트)
Phase 5: 시스템 프롬프트 생성 (서브에이전트)
Phase 6: 에이전트 설정 저장 (no LLM)
Phase 7: 최종 에이전트 빌드 (no LLM)
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agent_runtime.builder.sub_agents.intent_analyzer import analyze_intent
from app.agent_runtime.builder.sub_agents.middleware_recommender import (
    recommend_middlewares,
)
from app.agent_runtime.builder.sub_agents.prompt_generator import (
    generate_system_prompt,
)
from app.agent_runtime.builder.sub_agents.tool_recommender import recommend_tools
from app.schemas.builder import (
    AgentCreationIntent,
    BuildErrorEvent,
    DraftAgentConfig,
    MiddlewareRecommendation,
    PhaseProgressEvent,
    SubAgentEvent,
    ToolRecommendation,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# BuilderState — LangGraph 내부 상태
# ---------------------------------------------------------------------------


class BuilderState(TypedDict):
    """LangGraph StateGraph의 상태 스키마."""

    user_id: str
    user_request: str
    session_id: str
    project_path: str
    intent: dict[str, Any] | None
    tools: list[dict[str, Any]]
    middlewares: list[dict[str, Any]]
    system_prompt: str
    draft_config: dict[str, Any] | None
    agent_id: str
    current_phase: int
    error: str
    # 오케스트레이터가 DB에서 조회한 카탈로그 (동적 주입, AD-7)
    available_tools_catalog: list[dict[str, Any]]
    available_middlewares_catalog: list[dict[str, Any]]
    # 기본 모델명 (builder_service에서 DB 조회하여 주입, phase6에서 사용)
    default_model_name: str
    # SSE 이벤트 버퍼 — Annotated 없이 overwrite (매 phase마다 새 이벤트)
    sse_events: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Phase 노드 함수
# ---------------------------------------------------------------------------


def phase1_init(state: BuilderState) -> dict:
    """Phase 1: 프로젝트 초기화.

    LLM 불필요. 세션 ID 기반 프로젝트 경로를 생성한다.
    실제 파일 I/O는 ADR-005에 따라 DB JSON 컬럼으로 대체 (PoC 단계).
    """
    session_id = state.get("session_id") or str(uuid.uuid4())
    user_id = state["user_id"]
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    project_path = f"{user_id}/tmp/{timestamp}_{session_id[:8]}"

    return {
        "session_id": session_id,
        "project_path": project_path,
        "current_phase": 1,
        "sse_events": [
            PhaseProgressEvent(
                phase=1, status="completed", message="에이전트 만들 준비가 됐어요"
            ).model_dump(),
        ],
    }


async def phase2_intent(state: BuilderState) -> dict:
    """Phase 2: 의도 분석 — 서브에이전트 호출."""
    events: list[dict] = [
        PhaseProgressEvent(
            phase=2,
            status="started",
            message="어떤 에이전트를 만들면 좋을지 분석 중이에요",
        ).model_dump(),
        SubAgentEvent(phase=2, agent_name="intent_analyzer").model_dump(),
    ]
    try:
        intent = await analyze_intent(state["user_request"])
        events.append(
            SubAgentEvent(
                phase=2,
                agent_name="intent_analyzer",
                result_summary=f"에이전트: {intent.agent_name_ko}",
            ).model_dump()
        )
        events.append(
            PhaseProgressEvent(
                phase=2, status="completed", message="에이전트의 역할을 파악했어요"
            ).model_dump()
        )
        return {
            "intent": intent.model_dump(),
            "current_phase": 2,
            "sse_events": events,
        }
    except Exception as exc:
        logger.exception("Phase 2 failed")
        events.append(
            BuildErrorEvent(
                phase=2, message="의도 분석 중 오류가 발생했습니다.", recoverable=False
            ).model_dump()
        )
        return {
            "error": f"Phase 2 failed: {exc}",
            "current_phase": 2,
            "sse_events": events,
        }


async def phase3_tools(state: BuilderState) -> dict:
    """Phase 3: 도구 추천 — 서브에이전트 호출."""
    if state.get("error"):
        return {"current_phase": 3, "sse_events": []}

    events: list[dict] = [
        PhaseProgressEvent(
            phase=3,
            status="started",
            message="에이전트에 필요한 도구를 고르고 있어요",
        ).model_dump(),
        SubAgentEvent(phase=3, agent_name="tool_recommender").model_dump(),
    ]
    try:
        intent = AgentCreationIntent(**(state["intent"] or {}))  # type: ignore[arg-type]
        catalog = state.get("available_tools_catalog", [])
        tools = await recommend_tools(intent, catalog)

        tool_names = [t.tool_name for t in tools]
        events.append(
            SubAgentEvent(
                phase=3,
                agent_name="tool_recommender",
                result_summary=f"추천 도구: {', '.join(tool_names)}",
            ).model_dump()
        )
        events.append(
            PhaseProgressEvent(
                phase=3, status="completed", message=f"도구 {len(tools)}개를 선정했어요"
            ).model_dump()
        )
        return {
            "tools": [t.model_dump() for t in tools],
            "current_phase": 3,
            "sse_events": events,
        }
    except Exception:
        logger.exception("Phase 3 failed")
        events.append(
            BuildErrorEvent(
                phase=3, message="도구 추천 중 오류가 발생했습니다.", recoverable=True
            ).model_dump()
        )
        events.append(
            PhaseProgressEvent(
                phase=3,
                status="warning",
                message="도구 추천에 문제가 있었지만, 계속 진행할게요",
            ).model_dump()
        )
        return {
            "tools": [],
            "current_phase": 3,
            "sse_events": events,
        }


async def phase4_middlewares(state: BuilderState) -> dict:
    """Phase 4: 미들웨어 추천 — 서브에이전트 호출."""
    if state.get("error"):
        return {"current_phase": 4, "sse_events": []}

    events: list[dict] = [
        PhaseProgressEvent(
            phase=4,
            status="started",
            message="에이전트의 안정성을 높여줄 미들웨어를 고르고 있어요",
        ).model_dump(),
        SubAgentEvent(phase=4, agent_name="middleware_recommender").model_dump(),
    ]
    try:
        intent = AgentCreationIntent(**(state["intent"] or {}))  # type: ignore[arg-type]
        tools = [ToolRecommendation(**t) for t in state.get("tools", [])]
        catalog = state.get("available_middlewares_catalog", [])
        middlewares = await recommend_middlewares(intent, tools, catalog)

        mw_names = [m.middleware_name for m in middlewares]
        events.append(
            SubAgentEvent(
                phase=4,
                agent_name="middleware_recommender",
                result_summary=f"추천 미들웨어: {', '.join(mw_names)}",
            ).model_dump()
        )
        events.append(
            PhaseProgressEvent(
                phase=4,
                status="completed",
                message=f"미들웨어 {len(middlewares)}개를 선정했어요",
            ).model_dump()
        )
        return {
            "middlewares": [m.model_dump() for m in middlewares],
            "current_phase": 4,
            "sse_events": events,
        }
    except Exception:
        logger.exception("Phase 4 failed")
        events.append(
            BuildErrorEvent(
                phase=4, message="미들웨어 추천 중 오류가 발생했습니다.", recoverable=True
            ).model_dump()
        )
        events.append(
            PhaseProgressEvent(
                phase=4,
                status="warning",
                message="미들웨어 추천에 문제가 있었지만, 계속 진행할게요",
            ).model_dump()
        )
        return {
            "middlewares": [],
            "current_phase": 4,
            "sse_events": events,
        }


async def phase5_prompt(state: BuilderState) -> dict:
    """Phase 5: 시스템 프롬프트 생성 — 서브에이전트 호출."""
    if state.get("error"):
        return {"current_phase": 5, "sse_events": []}

    events: list[dict] = [
        PhaseProgressEvent(
            phase=5, status="started", message="에이전트의 성격과 행동 지침을 작성 중이에요"
        ).model_dump(),
        SubAgentEvent(phase=5, agent_name="prompt_generator").model_dump(),
    ]
    try:
        intent = AgentCreationIntent(**(state["intent"] or {}))  # type: ignore[arg-type]
        tools = [ToolRecommendation(**t) for t in state.get("tools", [])]
        middlewares = [MiddlewareRecommendation(**m) for m in state.get("middlewares", [])]
        prompt = await generate_system_prompt(intent, tools, middlewares)

        events.append(
            SubAgentEvent(
                phase=5,
                agent_name="prompt_generator",
                result_summary=f"프롬프트 생성 완료 ({len(prompt)}자)",
            ).model_dump()
        )
        events.append(
            PhaseProgressEvent(
                phase=5, status="completed", message="에이전트의 행동 지침을 완성했어요"
            ).model_dump()
        )
        return {
            "system_prompt": prompt,
            "current_phase": 5,
            "sse_events": events,
        }
    except Exception as exc:
        logger.exception("Phase 5 failed")
        events.append(
            BuildErrorEvent(
                phase=5, message="시스템 프롬프트 생성 중 오류가 발생했습니다.", recoverable=False
            ).model_dump()
        )
        return {
            "error": f"Phase 5 failed: {exc}",
            "current_phase": 5,
            "sse_events": events,
        }


def phase6_config(state: BuilderState) -> dict:
    """Phase 6: 에이전트 설정 저장 — LLM 불필요."""
    if state.get("error"):
        return {"current_phase": 6, "sse_events": []}

    intent_data: dict = state.get("intent") or {}
    tool_names = [t.get("tool_name", "") for t in state.get("tools", [])]
    mw_names = [m.get("middleware_name", "") for m in state.get("middlewares", [])]

    draft = DraftAgentConfig(
        name=intent_data.get("agent_name", "Custom Agent"),
        name_ko=intent_data.get("agent_name_ko", "맞춤 에이전트"),
        description=intent_data.get("agent_description", ""),
        system_prompt=state.get("system_prompt", ""),
        tools=tool_names,
        middlewares=mw_names,
        model_name=state.get("default_model_name", ""),
        primary_task_type=intent_data.get("primary_task_type", ""),
        use_cases=intent_data.get("use_cases", []),
    )

    events = [
        PhaseProgressEvent(
            phase=6, status="completed", message="에이전트 설정을 정리했어요"
        ).model_dump(),
    ]
    return {
        "draft_config": draft.model_dump(),
        "current_phase": 6,
        "sse_events": events,
    }


def phase7_preview(state: BuilderState) -> dict:
    """Phase 7: 빌드 프리뷰 생성.

    실제 Agent DB 레코드 생성은 사용자 confirm 시에 수행한다 (builder_service).
    여기서는 draft_config를 최종 확인하고 preview 상태로 전환한다.
    """
    if state.get("error"):
        return {"current_phase": 7, "sse_events": []}

    events = [
        PhaseProgressEvent(
            phase=7,
            status="completed",
            message="에이전트가 준비됐어요! 설정을 확인해주세요",
        ).model_dump(),
    ]
    return {
        "current_phase": 7,
        "sse_events": events,
    }


# ---------------------------------------------------------------------------
# 에러 라우팅
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 그래프 빌드
# ---------------------------------------------------------------------------


def build_builder_graph() -> Any:
    """Builder 오케스트레이터 StateGraph를 빌드하고 compile한다."""
    graph = StateGraph(BuilderState)

    # 노드 등록
    graph.add_node("phase1", phase1_init)
    graph.add_node("phase2", phase2_intent)
    graph.add_node("phase3", phase3_tools)
    graph.add_node("phase4", phase4_middlewares)
    graph.add_node("phase5", phase5_prompt)
    graph.add_node("phase6", phase6_config)
    graph.add_node("phase7", phase7_preview)

    # 엣지: 순차 파이프라인
    graph.add_edge(START, "phase1")
    graph.add_edge("phase1", "phase2")

    # Phase 2 이후 에러 체크 — 에러면 END, 아니면 phase3
    graph.add_conditional_edges(
        "phase2",
        lambda s: END if s.get("error") else "phase3",
        ["phase3", END],
    )
    graph.add_edge("phase3", "phase4")
    graph.add_edge("phase4", "phase5")

    # Phase 5 이후 에러 체크
    graph.add_conditional_edges(
        "phase5",
        lambda s: END if s.get("error") else "phase6",
        ["phase6", END],
    )
    graph.add_edge("phase6", "phase7")
    graph.add_edge("phase7", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# 실행 헬퍼
# ---------------------------------------------------------------------------


_COMPILED_GRAPH = build_builder_graph()


async def run_builder_pipeline(
    user_id: str,
    user_request: str,
    session_id: str,
    tools_catalog: list[dict[str, Any]],
    middlewares_catalog: list[dict[str, Any]],
    default_model_name: str = "",
) -> AsyncGenerator[dict[str, Any], None]:
    """Builder 파이프라인을 실행하고, phase별 SSE 이벤트를 yield한다.

    Yields:
        dict with keys: phase, events (list), state_update (partial state)
    """
    graph = _COMPILED_GRAPH

    initial_state: BuilderState = {
        "user_id": user_id,
        "user_request": user_request,
        "session_id": session_id,
        "project_path": "",
        "intent": None,
        "tools": [],
        "middlewares": [],
        "system_prompt": "",
        "draft_config": None,
        "agent_id": "",
        "current_phase": 0,
        "error": "",
        "available_tools_catalog": tools_catalog,
        "available_middlewares_catalog": middlewares_catalog,
        "default_model_name": default_model_name,
        "sse_events": [],
    }

    async for event in graph.astream(initial_state, stream_mode="updates"):
        # stream_mode="updates" yields {node_name: state_update}
        for node_name, update in event.items():
            sse_events = update.pop("sse_events", [])
            yield {
                "node": node_name,
                "phase": update.get("current_phase", 0),
                "events": sse_events,
                "state_update": update,
            }
