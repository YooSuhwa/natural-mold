"""Phase 6 — 에이전트 이미지 생성 (4-노드 패턴).

phase6_choice_propose: 1차 image_choice ToolMessage emit (또는 provider 없으면 즉시 skip)
phase6_choice_wait: interrupt → skip/generate 분기
phase6_image_generate: 이미지 생성 + 2차 image_approval ToolMessage emit
phase6_image_approval: interrupt → 확정/재생성/skip 분기
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.types import interrupt

from app.agent_runtime.builder_v3.image_gen import (
    ImageGenerationError,
    build_default_prompt,
    generate_agent_image,
    is_image_generation_available,
)
from app.agent_runtime.builder_v3.nodes._helpers import (
    build_phase_complete,
    close_pending_tool_card,
    ensure_todos,
    make_pending_tool_card,
    make_tool_card,
)
from app.agent_runtime.builder_v3.state import BuilderState

logger = logging.getLogger(__name__)


def _get_image_prompt_seed(state: BuilderState) -> str:
    intent = state.get("intent") or {}
    return build_default_prompt(
        agent_name=intent.get("agent_name_ko") or intent.get("agent_name", "Agent"),
        agent_description=intent.get("agent_description", ""),
        primary_task_type=intent.get("primary_task_type", ""),
    )


# ---------------------------------------------------------------------------
# Node: phase6_choice_propose
# ---------------------------------------------------------------------------


async def phase6_choice_propose(state: BuilderState) -> dict:
    """1차: image_choice 카드 emit. provider 없으면 image_skipped=True로 fall-through."""
    if not is_image_generation_available():
        info_msgs, _ = make_tool_card(
            "image_choice",
            {
                "phase": 6,
                "title": "에이전트 이미지 생성",
                "available": False,
                "auto_prompt": "이미지 생성이 비활성화되어 있습니다 (OPENROUTER_API_KEY 미설정).",
            },
            intro_text="이미지 생성이 설정되지 않아 이 단계를 건너뜁니다.",
        )
        complete_msgs = build_phase_complete(
            6,
            ensure_todos(state),
            "[Phase 6 완료] 이미지 생성 건너뜀. 이제 Phase 7: 설정 저장을 진행합니다.",
        )
        return {
            "messages": list(info_msgs) + list(complete_msgs),
            "image_url": None,
            "current_phase": 7,
            "image_skipped": True,
        }

    auto_prompt = _get_image_prompt_seed(state)
    msgs, tool_call_id = make_pending_tool_card(
        "image_choice",
        {
            "phase": 6,
            "title": "에이전트 이미지를 생성하시겠습니까?",
            "auto_prompt": auto_prompt,
            "available": True,
            "options": [
                {"value": "skip", "label": "넘어가기"},
                {"value": "generate", "label": "생성하기"},
            ],
        },
        intro_text="이제 에이전트의 이미지를 만들어 보겠습니다.",
    )
    return {
        "messages": msgs,
        "image_skipped": False,
        "pending_tool_call_id": tool_call_id,
    }


# ---------------------------------------------------------------------------
# Node: phase6_choice_wait
# ---------------------------------------------------------------------------


async def phase6_choice_wait(state: BuilderState) -> dict:
    """interrupt → skip/generate 결정을 state에 반영. 라우팅은 graph가."""
    auto_prompt = _get_image_prompt_seed(state)

    response = interrupt(
        {
            "type": "image_choice",
            "phase": 6,
            "title": "에이전트 이미지를 생성하시겠습니까?",
            "auto_prompt": auto_prompt,
            "options": [
                {"value": "skip", "label": "넘어가기"},
                {"value": "generate", "label": "생성하기"},
            ],
        }
    )

    choice = ""
    custom_prompt = ""
    if isinstance(response, dict):
        choice = str(response.get("choice", "")).lower()
        custom_prompt = str(response.get("prompt") or response.get("auto_prompt") or "")
    elif isinstance(response, str):
        choice = response.lower()

    pending_tc_id = state.get("pending_tool_call_id")

    if choice in ("skip", "넘어가기", "넘어가"):
        close_msgs = close_pending_tool_card(pending_tc_id, "image_choice", "skip")
        complete_msgs = build_phase_complete(
            6,
            ensure_todos(state),
            "[Phase 6 완료] 이미지 생성 건너뜀. 이제 Phase 7: 설정 저장을 진행합니다.",
        )
        return {
            "messages": [*close_msgs, *complete_msgs],
            "image_url": None,
            "current_phase": 7,
            "image_skipped": True,
            "pending_tool_call_id": None,
        }

    close_msgs = close_pending_tool_card(pending_tc_id, "image_choice", "generate")
    return {
        "messages": close_msgs,
        "last_revision_message": custom_prompt or auto_prompt,
        "image_skipped": False,
        "pending_tool_call_id": None,
    }


# ---------------------------------------------------------------------------
# Node: phase6_image_generate (이미지 생성 + image_approval ToolMessage emit)
# ---------------------------------------------------------------------------


async def phase6_image_generate(state: BuilderState) -> dict:
    session_id = state.get("session_id", "session")
    prompt = state.get("last_revision_message") or _get_image_prompt_seed(state)

    try:
        public_url, _local_path = await generate_agent_image(
            prompt=prompt, session_id=session_id
        )
    except ImageGenerationError as exc:
        logger.warning("Image generation failed: %s", exc)
        msgs, fail_tc_id = make_pending_tool_card(
            "image_approval",
            {
                "phase": 6,
                "title": "이미지 생성 실패",
                "image_url": None,
                "prompt": prompt,
                "error": str(exc),
                "options": [
                    {"value": "regenerate", "label": "재시도"},
                    {"value": "skip", "label": "넘어가기"},
                ],
            },
            intro_text=f"이미지 생성에 실패했습니다: {exc}",
        )
        return {
            "messages": msgs,
            "image_url": None,
            "pending_tool_call_id": fail_tc_id,
        }

    msgs, ok_tc_id = make_pending_tool_card(
        "image_approval",
        {
            "phase": 6,
            "title": "이미지 미리보기",
            "image_url": public_url,
            "prompt": prompt,
            "options": [
                {"value": "confirm", "label": "확정"},
                {"value": "regenerate", "label": "재생성"},
                {"value": "skip", "label": "넘어가기"},
            ],
        },
        intro_text="이미지가 생성되었습니다. 확인해주세요.",
    )
    return {
        "messages": msgs,
        "image_url": public_url,
        "last_revision_message": None,
        "pending_tool_call_id": ok_tc_id,
    }


# ---------------------------------------------------------------------------
# Node: phase6_image_approval (interrupt + 분기)
# ---------------------------------------------------------------------------


async def phase6_image_approval(state: BuilderState) -> dict:
    """interrupt → 확정/재생성/skip 결정을 state에 반영. 라우팅은 graph가."""
    response = interrupt(
        {
            "type": "image_approval",
            "phase": 6,
            "image_url": state.get("image_url"),
        }
    )

    choice = ""
    new_prompt = ""
    if isinstance(response, dict):
        choice = str(response.get("choice", "")).lower()
        new_prompt = str(response.get("prompt") or "")
    elif isinstance(response, str):
        choice = response.lower()

    pending_tc_id = state.get("pending_tool_call_id")

    if choice in ("confirm", "확정"):
        close_msgs = close_pending_tool_card(pending_tc_id, "image_approval", "확정")
        complete_msgs = build_phase_complete(
            6,
            ensure_todos(state),
            "[Phase 6 완료] 에이전트 이미지 확정. 이제 Phase 7: 설정 저장을 진행합니다.",
        )
        return {
            "messages": [*close_msgs, *complete_msgs],
            "current_phase": 7,
            "image_skipped": True,
            "pending_tool_call_id": None,
        }

    if choice in ("skip", "넘어가기"):
        close_msgs = close_pending_tool_card(pending_tc_id, "image_approval", "skip")
        complete_msgs = build_phase_complete(
            6,
            ensure_todos(state),
            "[Phase 6 완료] 이미지 생성 건너뜀. 이제 Phase 7: 설정 저장을 진행합니다.",
        )
        return {
            "messages": [*close_msgs, *complete_msgs],
            "image_url": None,
            "current_phase": 7,
            "image_skipped": True,
            "pending_tool_call_id": None,
        }

    # regenerate — image_url 클리어 + last_revision_message 설정
    close_msgs = close_pending_tool_card(pending_tc_id, "image_approval", "재생성")
    base_prompt: Any = state.get("image_url") and _get_image_prompt_seed(state)
    target_prompt = new_prompt or base_prompt or _get_image_prompt_seed(state)
    return {
        "messages": close_msgs,
        "last_revision_message": str(target_prompt),
        "image_url": None,
        "image_skipped": False,
        "pending_tool_call_id": None,
    }
