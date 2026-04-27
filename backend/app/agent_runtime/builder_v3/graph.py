"""Builder v3 — StateGraph 컴파일.

8-phase + router. 각 phase에서 사용자에게 묻는 노드는 propose+wait로 분리되어
ToolMessage emit과 interrupt를 별도 노드로 처리한다 (LangGraph 권장 패턴).

토폴로지:
    START
      ↓
    phase1_init
      ↓
    phase2_analyze_intent ↔ phase2_intent_wait (Command goto self/analyze)
      ↓ (intent_confirmed=True)
    phase3_recommend_tools → phase3_approval ↔ phase3_recommend_tools
      ↓
    phase4_recommend_middlewares → phase4_approval ↔ phase4_recommend_middlewares
      ↓
    phase5_generate_prompt → phase5_approval ↔ phase5_generate_prompt
      ↓
    phase6_choice_propose → phase6_choice_wait
      ├→ phase7_save (skip)
      └→ phase6_image_generate → phase6_image_approval
                                 ├→ phase7_save (confirm/skip)
                                 └→ phase6_image_generate (regenerate)
      ↓
    phase7_save
      ↓
    phase8_propose → phase8_build_wait
      ├→ END (approved)
      └→ router → phase2/3/4/5/6 (수정 요청)
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agent_runtime.builder_v3.nodes.phase1_init import phase1_init
from app.agent_runtime.builder_v3.nodes.phase2_intent import (
    phase2_analyze_intent,
    phase2_intent_wait,
)
from app.agent_runtime.builder_v3.nodes.phase3_tools import (
    phase3_approval,
    phase3_recommend_tools,
)
from app.agent_runtime.builder_v3.nodes.phase4_middlewares import (
    phase4_approval,
    phase4_recommend_middlewares,
)
from app.agent_runtime.builder_v3.nodes.phase5_prompt import (
    phase5_approval,
    phase5_generate_prompt,
)
from app.agent_runtime.builder_v3.nodes.phase6_image import (
    phase6_choice_propose,
    phase6_choice_wait,
    phase6_image_approval,
    phase6_image_generate,
)
from app.agent_runtime.builder_v3.nodes.phase7_save import phase7_save
from app.agent_runtime.builder_v3.nodes.phase8_build import phase8_build_wait, phase8_propose
from app.agent_runtime.builder_v3.nodes.router import router
from app.agent_runtime.builder_v3.state import BuilderState

# ---------------------------------------------------------------------------
# Routing functions (conditional_edges) — named for readability
# ---------------------------------------------------------------------------


def _route_after_phase2_analyze(state: BuilderState) -> str:
    """intent_confirmed=True면 phase3로, 아니면 ask_user wait로."""
    if state.get("intent_confirmed") and state.get("intent"):
        return "phase3_recommend_tools"
    return "phase2_intent_wait"


def _route_after_approval(next_phase: str, recommend_node: str):
    """phase 3/4/5 approval 노드의 라우팅 generator.

    last_revision_message가 set되면 recommend로 재진입, 아니면 다음 phase.
    """

    def _route(state: BuilderState) -> str:
        if state.get("last_revision_message"):
            return recommend_node
        return next_phase

    return _route


def _route_after_phase6_choice_propose(state: BuilderState) -> str:
    return "phase7_save" if state.get("image_skipped") else "phase6_choice_wait"


def _route_after_phase6_choice_wait(state: BuilderState) -> str:
    return "phase7_save" if state.get("image_skipped") else "phase6_image_generate"


def _route_after_phase6_image_approval(state: BuilderState) -> str:
    return "phase7_save" if state.get("image_skipped") else "phase6_image_generate"


def _route_after_phase8_build_wait(state: BuilderState) -> str:
    """승인+생성 성공 → END. 에러 발생 → END (사용자에게 error_message 노출). 수정 요청 → router."""
    if state.get("completed"):
        return END
    if state.get("error_message"):
        # confirm 실패 등 — router로 가지 않고 END (frontend가 error 표시)
        return END
    return "router"


def build_graph() -> StateGraph:
    """8-phase StateGraph (uncompiled). 테스트용."""
    g: StateGraph = StateGraph(BuilderState)

    # 모든 노드 dict-only (Command 사용 X). 라우팅은 conditional_edges가 결정.
    g.add_node("phase1_init", phase1_init)
    g.add_node("phase2_analyze_intent", phase2_analyze_intent)
    g.add_node("phase2_intent_wait", phase2_intent_wait)
    g.add_node("phase3_recommend_tools", phase3_recommend_tools)
    g.add_node("phase3_approval", phase3_approval)
    g.add_node("phase4_recommend_middlewares", phase4_recommend_middlewares)
    g.add_node("phase4_approval", phase4_approval)
    g.add_node("phase5_generate_prompt", phase5_generate_prompt)
    g.add_node("phase5_approval", phase5_approval)
    g.add_node("phase6_choice_propose", phase6_choice_propose)
    g.add_node("phase6_choice_wait", phase6_choice_wait)
    g.add_node("phase6_image_generate", phase6_image_generate)
    g.add_node("phase6_image_approval", phase6_image_approval)
    g.add_node("phase7_save", phase7_save)
    g.add_node("phase8_propose", phase8_propose)
    g.add_node("phase8_build_wait", phase8_build_wait)
    # router는 여전히 Command 사용 (5-way 분기) — destinations 명시
    g.add_node(
        "router",
        router,
        destinations=(
            "phase2_analyze_intent",
            "phase3_recommend_tools",
            "phase4_recommend_middlewares",
            "phase5_generate_prompt",
            "phase6_choice_propose",
        ),
    )

    # Fixed edges
    g.add_edge(START, "phase1_init")
    g.add_edge("phase1_init", "phase2_analyze_intent")

    # Phase 2
    g.add_conditional_edges(
        "phase2_analyze_intent",
        _route_after_phase2_analyze,
        ["phase2_intent_wait", "phase3_recommend_tools"],
    )
    g.add_edge("phase2_intent_wait", "phase2_analyze_intent")

    # Phase 3/4/5: approval은 같은 패턴 (last_revision_message로 재진입 or 다음 phase)
    g.add_edge("phase3_recommend_tools", "phase3_approval")
    g.add_conditional_edges(
        "phase3_approval",
        _route_after_approval("phase4_recommend_middlewares", "phase3_recommend_tools"),
        ["phase3_recommend_tools", "phase4_recommend_middlewares"],
    )

    g.add_edge("phase4_recommend_middlewares", "phase4_approval")
    g.add_conditional_edges(
        "phase4_approval",
        _route_after_approval("phase5_generate_prompt", "phase4_recommend_middlewares"),
        ["phase4_recommend_middlewares", "phase5_generate_prompt"],
    )

    g.add_edge("phase5_generate_prompt", "phase5_approval")
    g.add_conditional_edges(
        "phase5_approval",
        _route_after_approval("phase6_choice_propose", "phase5_generate_prompt"),
        ["phase5_generate_prompt", "phase6_choice_propose"],
    )

    # Phase 6
    g.add_conditional_edges(
        "phase6_choice_propose",
        _route_after_phase6_choice_propose,
        ["phase7_save", "phase6_choice_wait"],
    )
    g.add_conditional_edges(
        "phase6_choice_wait",
        _route_after_phase6_choice_wait,
        ["phase7_save", "phase6_image_generate"],
    )
    g.add_edge("phase6_image_generate", "phase6_image_approval")
    g.add_conditional_edges(
        "phase6_image_approval",
        _route_after_phase6_image_approval,
        ["phase7_save", "phase6_image_generate"],
    )

    g.add_edge("phase7_save", "phase8_propose")
    g.add_edge("phase8_propose", "phase8_build_wait")
    # Phase 8: completed=True or error → END, 수정 요청 → router
    g.add_conditional_edges(
        "phase8_build_wait",
        _route_after_phase8_build_wait,
        [END, "router"],
    )

    return g


def compile_graph(checkpointer: Any | None = None) -> Any:
    """그래프를 컴파일한다. checkpointer가 None이면 인메모리 (테스트용)."""
    g = build_graph()
    return g.compile(checkpointer=checkpointer)


def get_node_targets() -> dict[str, set[str]]:
    """그래프 토폴로지 검증용: 각 노드의 가능한 next 노드 집합.

    pytest 그래프 도달성 테스트에서 사용.
    """
    return {
        "phase1_init": {"phase2_analyze_intent"},
        "phase2_analyze_intent": {"phase2_intent_wait", "phase3_recommend_tools"},
        "phase2_intent_wait": {"phase2_analyze_intent"},
        "phase3_recommend_tools": {"phase3_approval"},
        "phase3_approval": {"phase3_recommend_tools", "phase4_recommend_middlewares"},
        "phase4_recommend_middlewares": {"phase4_approval"},
        "phase4_approval": {"phase4_recommend_middlewares", "phase5_generate_prompt"},
        "phase5_generate_prompt": {"phase5_approval"},
        "phase5_approval": {"phase5_generate_prompt", "phase6_choice_propose"},
        "phase6_choice_propose": {"phase6_choice_wait", "phase7_save"},
        "phase6_choice_wait": {"phase6_image_generate", "phase7_save"},
        "phase6_image_generate": {"phase6_image_approval"},
        "phase6_image_approval": {"phase6_image_generate", "phase7_save"},
        "phase7_save": {"phase8_propose"},
        "phase8_propose": {"phase8_build_wait"},
        "phase8_build_wait": {"router", END},
        "router": {
            "phase2_analyze_intent",
            "phase3_recommend_tools",
            "phase4_recommend_middlewares",
            "phase5_generate_prompt",
            "phase6_choice_propose",
        },
    }
