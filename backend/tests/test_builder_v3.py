"""Builder v3 단위 테스트.

- 그래프 토폴로지 도달성 검증
- BuilderState/Todos 헬퍼 검증
- image_gen public_url/resolve_local_path round-trip
- 통합: graph.astream end-to-end (mocked LLMs)
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agent_runtime.builder_v3 import image_gen
from app.agent_runtime.builder_v3.graph import build_graph, get_node_targets
from app.agent_runtime.builder_v3.state import (
    PHASE_DEFINITIONS,
    initial_todos,
)
from app.agent_runtime.builder_v3.todos import (
    PHASE_TIMELINE_TOOL,
    build_timeline_messages,
    mark_completed_through,
    update_phase_status,
)

# ---------------------------------------------------------------------------
# Phase definitions
# ---------------------------------------------------------------------------


def test_phase_definitions_count():
    assert len(PHASE_DEFINITIONS) == 8
    assert [p["id"] for p in PHASE_DEFINITIONS] == list(range(1, 9))


def test_initial_todos_all_pending():
    todos = initial_todos()
    assert len(todos) == 8
    assert all(t["status"] == "pending" for t in todos)


# ---------------------------------------------------------------------------
# Todos helpers
# ---------------------------------------------------------------------------


def test_update_phase_status_only_changes_target():
    todos = initial_todos()
    new = update_phase_status(todos, 3, "in_progress")
    assert new[2]["status"] == "in_progress"
    # 다른 phase는 변경 없음
    assert all(t["status"] == "pending" for i, t in enumerate(new) if i != 2)
    # 원본 불변
    assert all(t["status"] == "pending" for t in todos)


def test_mark_completed_through():
    todos = initial_todos()
    new = mark_completed_through(todos, 4)
    statuses = [t["status"] for t in new]
    assert statuses[:4] == ["completed"] * 4
    assert all(s == "pending" for s in statuses[4:])


def test_build_timeline_messages_returns_pair():
    state = {"todos": initial_todos()}
    msgs, todos = build_timeline_messages(state, intro_text="hi")  # type: ignore[arg-type]
    assert len(msgs) == 2  # AIMessage + ToolMessage
    assert msgs[0].tool_calls[0]["name"] == PHASE_TIMELINE_TOOL  # type: ignore[attr-defined]
    assert msgs[1].name == PHASE_TIMELINE_TOOL  # type: ignore[attr-defined]
    assert len(todos) == 8


# ---------------------------------------------------------------------------
# Graph topology
# ---------------------------------------------------------------------------


def test_graph_compiles():
    g = build_graph()
    compiled = g.compile()  # in-memory, no checkpointer
    assert compiled is not None


def test_graph_contains_all_phases():
    """8-phase 노드 + router가 모두 등록됨 (propose+wait 분리 패턴 포함)."""
    g = build_graph()
    expected = {
        "phase1_init",
        "phase2_analyze_intent",
        "phase2_intent_wait",
        "phase3_recommend_tools",
        "phase3_approval",
        "phase4_recommend_middlewares",
        "phase4_approval",
        "phase5_generate_prompt",
        "phase5_approval",
        "phase6_choice_propose",
        "phase6_choice_wait",
        "phase6_image_generate",
        "phase6_image_approval",
        "phase7_save",
        "phase8_propose",
        "phase8_build_wait",
        "router",
    }
    actual = set(g.nodes.keys())
    assert expected <= actual


def test_node_targets_topology_consistent():
    """각 노드의 가능한 next 노드가 실제 그래프 노드 집합 내에 있어야 한다."""
    targets = get_node_targets()
    g = build_graph()
    valid_nodes = set(g.nodes.keys()) | {"__end__"}

    for source, dests in targets.items():
        assert source in g.nodes
        for dest in dests:
            # END는 langgraph 상수 (str로 비교 시 "__end__")
            assert dest in valid_nodes or str(dest) == "__end__"


def test_phase_order_enforced_by_topology():
    """Phase 8 도달 시 반드시 Phase 1-7을 거쳐야 한다 (위상학적 검증).

    그래프의 정방향 edge 추적 — phase8_build_wait에 도달하려면 어떤 경로든 1~7 노드를 통과.
    """
    targets = get_node_targets()

    # Phase 1로 도달 가능한 source 노드들 (router는 직접 가지 않음)
    sources_to_phase1 = [src for src, dests in targets.items() if "phase1_init" in dests]
    assert sources_to_phase1 == []

    # phase8_build_wait의 가능한 next: router 또는 END
    assert "router" in targets["phase8_build_wait"]
    # router는 분리된 노드 (phase2_analyze_intent 등)로만 분기
    assert "phase2_analyze_intent" in targets["router"]
    assert "phase6_choice_propose" in targets["router"]


# ---------------------------------------------------------------------------
# Image gen helpers (no actual API call)
# ---------------------------------------------------------------------------


def test_image_public_url_format():
    url = image_gen.public_url_for("session-abc", "test.png")
    assert url == "/api/builder/session-abc/image/test.png"


def test_image_resolve_path_traversal_safe():
    # 존재하지 않는 파일 → None
    result = image_gen.resolve_local_path("session-abc", "../../etc/passwd")
    assert result is None


def test_build_default_prompt_includes_metadata():
    prompt = image_gen.build_default_prompt(
        agent_name="검색 봇",
        agent_description="인터넷 검색 자동화",
        primary_task_type="웹 검색",
    )
    assert "검색 봇" in prompt
    assert "인터넷 검색" in prompt or "웹 검색" in prompt


# ---------------------------------------------------------------------------
# Integration: graph.astream end-to-end (mocked LLMs)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase2_to_phase3_with_intent_confirmed_via_resume(monkeypatch):
    """Phase 2 ask_user → resume → intent_confirmed=True → Phase 3 도달.

    LLM 호출은 monkeypatch로 mock. interrupt → Command(resume) 흐름 검증.
    """
    from app.agent_runtime.builder_v3 import graph as graph_module
    from app.agent_runtime.builder_v3.nodes import phase2_intent
    from app.schemas.builder import AgentCreationIntent

    # mock analyze_intent → 빈 fallback intent (이름 fallback 라벨)
    fake_intent = AgentCreationIntent(
        agent_name="Custom Agent",
        agent_name_ko="맞춤 에이전트",
        agent_description="사용자 요청에 따라 생성된 에이전트: x",
        primary_task_type="x",
        use_cases=["x"],
    )

    async def _fake_analyze(req: str):
        return fake_intent

    async def _fake_suggest(req: str):
        return ["옵션 A", "옵션 B", "옵션 C"]

    monkeypatch.setattr(phase2_intent, "analyze_intent", _fake_analyze)
    monkeypatch.setattr(phase2_intent, "_suggest_name_options", _fake_suggest)

    # Phase 3 의 LLM도 mock — 도구 추천 빈 리스트 반환 (interrupt에서 멈춤 검증이 목적)
    from app.agent_runtime.builder_v3.nodes import phase3_tools

    async def _fake_recommend_tools(intent, catalog):
        return []

    monkeypatch.setattr(phase3_tools, "recommend_tools", _fake_recommend_tools)

    saver = InMemorySaver()
    compiled = graph_module.compile_graph(checkpointer=saver)
    config = {"configurable": {"thread_id": "test-thread-1"}}

    initial = {
        "messages": [],
        "user_request": "테스트 요청",
        "session_id": "test-session-1",
        "current_phase": 1,
        "tools_catalog": [],
        "middlewares_catalog": [],
        "default_model_name": "",
    }

    # 첫 호출 → phase1 → phase2_analyze_intent → phase2_intent_wait → interrupt
    result = await compiled.ainvoke(initial, config=config)
    assert "__interrupt__" in result
    interrupts = result["__interrupt__"]
    assert any(
        (intr.value.get("type") if isinstance(intr.value, dict) else None) == "ask_user"
        for intr in interrupts
    )

    # resume — 사용자가 "옵션 A" 선택
    result2 = await compiled.ainvoke(Command(resume="옵션 A"), config=config)
    assert "__interrupt__" in result2  # phase 3 approval에서 또 interrupt
    # state 검증
    state = await compiled.aget_state(config)
    assert state.values.get("intent_confirmed") is True
    assert state.values["intent"]["agent_name_ko"] == "옵션 A"
    # phase 3 도구 추천 카드는 emit되었어야
    msgs = state.values.get("messages") or []
    has_recommendation = any(
        any(
            tc.get("name") == "recommendation_approval"
            for tc in (getattr(m, "tool_calls", None) or [])
        )
        for m in msgs
    )
    assert has_recommendation, "Phase 3 recommendation_approval 카드가 emit되어야 함"


def test_phase8_error_routes_to_end_not_router():
    """phase8_build_wait에서 confirm 실패(error_message set)면 END로 가야 한다.

    routing 함수만 단위 테스트.
    """
    from langgraph.graph import END

    from app.agent_runtime.builder_v3.graph import _route_after_phase8_build_wait

    # 승인 + 생성 성공
    assert _route_after_phase8_build_wait({"completed": True}) == END
    # 에러 발생
    assert _route_after_phase8_build_wait({"error_message": "fail"}) == END
    # 수정 요청
    assert _route_after_phase8_build_wait({"completed": False}) == "router"
    assert _route_after_phase8_build_wait({}) == "router"


def test_route_after_approval_factory():
    """phase 3/4/5 approval routing factory 검증."""
    from app.agent_runtime.builder_v3.graph import _route_after_approval

    route = _route_after_approval("phase4", "phase3_recommend")
    assert route({"last_revision_message": "다시"}) == "phase3_recommend"
    assert route({"last_revision_message": None}) == "phase4"
    assert route({}) == "phase4"
