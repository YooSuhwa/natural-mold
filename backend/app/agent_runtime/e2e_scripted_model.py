from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from copy import deepcopy
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import BaseTool
from pydantic import PrivateAttr

from app.agent_runtime.e2e_langgraph_v3_script import (
    LANGGRAPH_V3_MARKER,
    is_langgraph_v3_prompt,
    is_langgraph_v3_subagent_prompt,
    langgraph_v3_message,
    langgraph_v3_subagent_parts,
    langgraph_v3_subagent_response,
)

SCRIPTED_DOCUMENT_COMMANDS: dict[str, dict[str, str]] = {
    "E2E_DOCX": {
        "skill_directory": "/skills/docx-document",
        "command": (
            "node scripts/create_docx.cjs --input examples/e2e-docx.json "
            "--output moldy-docx-demo.docx"
        ),
    },
    "E2E_XLSX": {
        "skill_directory": "/skills/xlsx-spreadsheet",
        "command": (
            "node scripts/create_xlsx.cjs --input examples/e2e-xlsx.json "
            "--output moldy-xlsx-demo.xlsx"
        ),
    },
    "E2E_PPTX": {
        "skill_directory": "/skills/pptx-presentation",
        "command": (
            "node scripts/create_pptx.cjs --input examples/e2e-pptx.json "
            "--output moldy-pptx-demo.pptx"
        ),
    },
    "E2E_HWPX": {
        "skill_directory": "/skills/patent-hwpx-generator",
        "command": (
            "python scripts/generate_hwpx.py --input examples/e2e-patent.json "
            "--output moldy-patent-demo.hwpx"
        ),
    },
}
LANGGRAPH_V3_ARTIFACT_COMMAND = {
    "skill_directory": "/skills/docx-document",
    "command": "node scripts/create_langgraph_v3_artifacts.cjs --prefix moldy-langgraph-v3",
}

SLOW_STREAM_MARKER = "E2E_SLOW_STREAM"
# G2 — 런을 강제로 실패시켜 채팅 에러 버블 + retry(fork 재실행) 경로를
# E2E/capture로 검증한다. 예외는 LangGraph 스트림에서 잡혀 run.status="failed"로
# 이어진다(streaming.py). scripted 모델 게이트라 프로덕션엔 존재하지 않는다.
ERROR_MARKER = "E2E_ERROR"
SLOW_STREAM_PARTS = (
    "E2E slow ",
    "stream ",
    "completed ",
    "after ",
    "detached ",
    "navigation.",
)
VISUAL_SLOW_STREAM_MARKER = "E2E_VISUAL_SLOW_STREAM"
VISUAL_SLOW_STREAM_PARTS = (
    "E2E visual ",
    "stream ",
    "fixture ",
    "is ",
    "still ",
    "running; ",
    "the ",
    "assistant ",
    "response ",
    "is ",
    "partial, ",
    "the ",
    "thread ",
    "remains ",
    "active, ",
    "and ",
    "the ",
    "capture ",
    "should ",
    "show ",
    "an ",
    "in-progress ",
    "message ",
    "before ",
    "the ",
    "remaining ",
    "chunks ",
    "arrive; ",
    "visual ",
    "stream ",
    "fixture ",
    "complete.",
)
ARTIFACT_SLOW_FINAL_MARKER = "E2E_ARTIFACT_SLOW_FINAL"
ARTIFACT_SLOW_FINAL_PARTS = (
    "E2E artifact ",
    "final response ",
    "is still ",
    "streaming ",
    "while the ",
    "generated ",
    "file is ",
    "already ",
    "visible, ",
    "then ",
    "completed ",
    "after generated file.",
)
TOKEN_USAGE_MARKER = "E2E_TOKEN_USAGE_STREAM"  # noqa: S105 — E2E marker, not a secret
TOKEN_USAGE_CONTENT = "E2E token usage isolated conversation response."  # noqa: S105 — E2E fixture text, not a secret
TOKEN_USAGE_METADATA = {
    "input_tokens": 30,
    "output_tokens": 12,
    "total_tokens": 42,
}
CHAT_RICH_OUTPUT_MARKER = "E2E_CHAT_RICH_OUTPUTS"
CHAT_RICH_OUTPUT_PROMPT = (
    "체크리스트, 표, TypeScript 코드, 수식, 이미지, 링크, 인용문, "
    "Mermaid 다이어그램을 모두 포함해서 채팅 출력 예시를 보여줘"
)
CHAT_RICH_OUTPUT_CONTENT = "\n".join(
    (
        "# E2E rich output contract",
        "",
        "이 응답은 채팅 렌더러가 여러 출력 형식을 안정적으로 유지하는지 확인합니다.",
        "",
        "- [x] E2E checklist item",
        "- Inline code: `e2e_inline_code`",
        "",
        "| Surface | Contract |",
        "| --- | --- |",
        "| Table | E2E table cell |",
        "",
        "```ts",
        "export function e2eRichOutput(value: number): number {",
        "  return value + 7",
        "}",
        "```",
        "",
        "Inline math $x + y = z$ and block math:",
        "",
        "$$",
        "a^2 + b^2 = c^2",
        "$$",
        "",
        "![E2E rich output image](/moldy-mascot.webp)",
        "",
        "[E2E reference link](https://example.com/e2e-chat-rich-output)",
        "",
        "> E2E blockquote remains rendered.",
        "",
        "```mermaid",
        "flowchart LR",
        "  A[E2E Mermaid Source] --> B[E2E Mermaid Rendered]",
        "```",
    )
)
DAILY_GREETING_MARKER = "E2E_DAILY_GREETING"
# Warm, natural daily-assistant opener for the conversation-capture tour. Kept as a
# dedicated marker so the bare fallback ("E2E scripted document model is ready.") stays
# byte-identical — many specs poll on that exact sentinel for the marker-less turn.
DAILY_GREETING_CONTENT = "\n".join(
    (
        "안녕하세요! 일상을 도와드리는 비서예요. 😊",
        "",
        "오늘은 이런 걸 함께 할 수 있어요:",
        "",
        "- 📅 일정 정리와 리마인더",
        "- 🍳 식단·운동 루틴 추천",
        "- 🔎 궁금한 정보 검색 요약",
        "",
        "무엇부터 시작해볼까요?",
    )
)
HITL_APPROVAL_MARKER = "E2E_HITL_APPROVAL"
HITL_MULTI_MARKER = "E2E_HITL_MULTI"
# Two execute_in_skill calls in ONE AIMessage. langchain's HumanInTheLoopMiddleware
# batches every interrupting tool call from a single AIMessage into ONE interrupt
# with N action_requests, so this fixture drives the multi-action approval-card +
# HiTL coordinator path (collect both decisions, resume once). Distinct ids keep the
# two synthetic request_approval cards from collapsing, and distinct docx outputs let
# both skills execute successfully against the single installed docx-document skill.
HITL_MULTI_TOOL_CALLS = (
    {
        "id": "call_e2e_hitl_multi_0",
        "name": "execute_in_skill",
        "args": {
            "skill_directory": "/skills/docx-document",
            "command": (
                "node scripts/create_docx.cjs --input examples/e2e-docx.json "
                "--output moldy-hitl-multi-1.docx"
            ),
        },
    },
    {
        "id": "call_e2e_hitl_multi_1",
        "name": "execute_in_skill",
        "args": {
            "skill_directory": "/skills/docx-document",
            "command": (
                "node scripts/create_docx.cjs --input examples/e2e-docx.json "
                "--output moldy-hitl-multi-2.docx"
            ),
        },
    },
)
# A rejected tool call returns to the model as a ToolMessage with status="error"
# (HumanInTheLoopMiddleware). The scripted model must acknowledge the cancellation
# instead of reusing the generic "완료" completion line — otherwise a rejected
# approval reads as if the tool had run.
HITL_REJECTED_ACK_CONTENT = (
    "알겠습니다. 요청하신 도구 실행은 취소했어요. 다른 도움이 필요하면 말씀해 주세요."
)
HITL_EDIT_MARKER = "E2E_HITL_EDIT"
# Single ``edit_file`` call. Unlike ``execute_in_skill`` (allowed_decisions
# ``[approve, reject]``), ``edit_file`` carries the ``[approve, edit, reject]``
# policy (``default_deepagents_interrupt_policy``), so its approval card shows the
# 수정 button → the field-based editor. The args MIX editable fields with a
# sensitive key (``api_key``) so the editor demonstrates the read-only secret
# lock (``<redacted>``, restored by the backend by index). Capture-only: the run
# is meant to pause on the card; the tool itself never needs to execute cleanly.
HITL_EDIT_TOOL_CALL = {
    "id": "call_e2e_hitl_edit",
    "name": "edit_file",
    "args": {
        "file_path": "/conversations/deploy-config.yaml",
        "old_string": "region: us-east-1",
        "new_string": "region: ap-northeast-2",
        "api_key": "sk-live-9d8f7a6b5c4e3210fedcba98",
    },
}
# --- 스킬 빌더 챗 (skill-studio phase 1, M6 E2E) -----------------------------
# 결정론 시퀀스: WRITE(드래프트 파일 2개 write_file — 빌더 분기는 파일 도구
# 승인 카드를 제외하므로 즉시 실행) → VALIDATE(validate_skill) →
# TEST(test_skill_draft, CODE_EXECUTION 승인 카드 + "이 세션에서 계속 허용") →
# RETEST(동의 후 무카드 — args가 달라야 승인 카드 pill-strip 키와 충돌하지
# 않는다, HITL_MULTI의 distinct-output 선례) → FINALIZE(finalize_skill, 항상
# 승인 카드). WRITE 메시지는 마커 뒤에 워크스페이스 가상 경로를 실어 보낸다
# (scripted model은 세션 id를 알 수 없다).
SKILL_BUILDER_WRITE_MARKER = "E2E_SKILL_BUILDER_WRITE"
SKILL_BUILDER_VALIDATE_MARKER = "E2E_SKILL_BUILDER_VALIDATE"
SKILL_BUILDER_TEST_MARKER = "E2E_SKILL_BUILDER_TEST"
SKILL_BUILDER_RETEST_MARKER = "E2E_SKILL_BUILDER_RETEST"
SKILL_BUILDER_FINALIZE_MARKER = "E2E_SKILL_BUILDER_FINALIZE"
# CONFLICT는 FINALIZE를 부분 문자열로 포함 — 매처에서 반드시 먼저 검사(RETEST 선례).
SKILL_BUILDER_FINALIZE_CONFLICT_MARKER = "E2E_SKILL_BUILDER_FINALIZE_CONFLICT"
_SKILL_DRAFT_PATH_RE = re.compile(r"/skill-drafts/[0-9a-fA-F-]{36}")
SKILL_BUILDER_SANDBOX_OUTPUT = "E2E_DRAFT_SANDBOX_OK"
SKILL_BUILDER_SKILL_MD = (
    "---\n"
    "name: e2e-notes\n"
    'description: "Use when summarizing meeting notes into action items for the E2E tour."\n'
    "---\n\n"
    "Use when summarizing meeting notes. Run scripts/hello.py to verify the sandbox.\n"
)
SKILL_BUILDER_SCRIPT = f"print('{SKILL_BUILDER_SANDBOX_OUTPUT}')\n"
SKILL_BUILDER_TEST_COMMAND = "python scripts/hello.py"
SKILL_BUILDER_RETEST_COMMAND = "python scripts/hello.py --again"
SKILL_BUILDER_WRITE_FINAL = "드래프트 파일을 작성했습니다. SKILL.md와 스크립트를 확인해 주세요."
SKILL_BUILDER_VALIDATE_FINAL = "드래프트 검증을 실행했습니다. 오른쪽 레일에서 결과를 확인하세요."
SKILL_BUILDER_TEST_FINAL = "드래프트 시험 실행이 끝났습니다."
SKILL_BUILDER_FINALIZE_FINAL = "스킬을 저장했습니다. 스킬 목록에서 확인할 수 있어요."
SKILL_BUILDER_FINALIZE_CONFLICT_FINAL = (
    "원본 스킬이 세션 시작 후 변경되어 저장하지 못했습니다. "
    "최신 버전에서 개선 세션을 다시 시작해 주세요."
)


# --- Phase 3 llm-2 measured A/B evaluation arms -----------------------------
# The evaluator sends [SystemMessage, HumanMessage] per call; the system
# prompt's first line is a stable marker (see skill_evaluation_ab_arms.py —
# kept as literals here to avoid an app.services import cycle, same reasoning
# as UI_DATA_KIND_BY_MARKER). Responses carry usage_metadata so the measured
# cost/token plumbing lights up deterministically in E2E.
SKILL_EVAL_WITH_ARM_PROMPT_HEAD = "You are Moldy's skill evaluation with-skill arm."
SKILL_EVAL_WITHOUT_ARM_PROMPT_HEAD = "You are Moldy's skill evaluation baseline arm."
SKILL_EVAL_AB_GRADER_PROMPT_HEAD = "You are Moldy's skill evaluation A/B grader."
# Legacy llm-1 run-level grader — kept answerable so older paths don't hang.
SKILL_EVAL_LEGACY_GRADER_PROMPT_HEAD = "You are Moldy's skill evaluation grader."
SKILL_EVAL_WITH_ARM_ANSWER = (
    "담당자/마감일 표\n| 담당자 | 마감일 |\n| --- | --- |\n| 김철수 | 7월 15일 |"
)
SKILL_EVAL_WITHOUT_ARM_ANSWER = (
    "회의록을 다시 확인해 보셔야 할 것 같습니다. 일반적인 요약만 가능해요."
)
SKILL_EVAL_AB_GRADER_JSON = (
    '{"case_index": 0, "status": "passed", "score": 0.95,'
    ' "baseline_status": "failed", "baseline_score": 0.3,'
    ' "grader_feedback": "with-arm은 스킬 지시대로 표를 반환했고 baseline은 형식을 놓쳤습니다.",'
    ' "evidence": "with_skill_answer가 expected의 담당자/마감일 표와 일치합니다."}'
)
SKILL_EVAL_LEGACY_GRADER_JSON = (
    '{"case_results": [{"case_index": 0, "status": "passed", "score": 0.95,'
    ' "baseline_status": "failed", "baseline_score": 0.3,'
    ' "grader_feedback": "scripted legacy grader", "evidence": "scripted"}]}'
)


def _skill_eval_arm_response(messages: list[BaseMessage]) -> AIMessage | None:
    if not messages:
        return None
    first = messages[0]
    if isinstance(first, HumanMessage | ToolMessage):
        return None
    system_text = _message_text(first)
    if system_text.startswith(SKILL_EVAL_WITH_ARM_PROMPT_HEAD):
        content, tokens_in, tokens_out = SKILL_EVAL_WITH_ARM_ANSWER, 620, 180
    elif system_text.startswith(SKILL_EVAL_WITHOUT_ARM_PROMPT_HEAD):
        content, tokens_in, tokens_out = SKILL_EVAL_WITHOUT_ARM_ANSWER, 240, 60
    elif system_text.startswith(SKILL_EVAL_AB_GRADER_PROMPT_HEAD):
        content, tokens_in, tokens_out = SKILL_EVAL_AB_GRADER_JSON, 900, 120
    elif system_text.startswith(SKILL_EVAL_LEGACY_GRADER_PROMPT_HEAD):
        content, tokens_in, tokens_out = SKILL_EVAL_LEGACY_GRADER_JSON, 900, 120
    else:
        return None
    return AIMessage(
        content=content,
        usage_metadata={
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "total_tokens": tokens_in + tokens_out,
        },
    )


def _skill_builder_write_tool_calls(workspace: str) -> list[dict[str, Any]]:
    return [
        {
            "id": "call_e2e_sb_write_skill_md",
            "name": "write_file",
            "args": {"file_path": f"{workspace}/SKILL.md", "content": SKILL_BUILDER_SKILL_MD},
        },
        {
            "id": "call_e2e_sb_write_script",
            "name": "write_file",
            "args": {
                "file_path": f"{workspace}/scripts/hello.py",
                "content": SKILL_BUILDER_SCRIPT,
            },
        },
    ]


def _skill_builder_tool_calls(human_text: str) -> list[dict[str, Any]] | None:
    """마커 → 이번 턴에 방출할 tool_calls (아니면 None)."""

    if SKILL_BUILDER_WRITE_MARKER in human_text:
        match = _SKILL_DRAFT_PATH_RE.search(human_text)
        if match is None:
            return None
        return _skill_builder_write_tool_calls(match.group(0))
    if SKILL_BUILDER_RETEST_MARKER in human_text:
        return [
            {
                "id": "call_e2e_sb_retest",
                "name": "test_skill_draft",
                "args": {"command": SKILL_BUILDER_RETEST_COMMAND},
            }
        ]
    if SKILL_BUILDER_TEST_MARKER in human_text:
        return [
            {
                "id": "call_e2e_sb_test",
                "name": "test_skill_draft",
                "args": {"command": SKILL_BUILDER_TEST_COMMAND},
            }
        ]
    if SKILL_BUILDER_VALIDATE_MARKER in human_text:
        return [{"id": "call_e2e_sb_validate", "name": "validate_skill", "args": {}}]
    if SKILL_BUILDER_FINALIZE_CONFLICT_MARKER in human_text:
        return [{"id": "call_e2e_sb_finalize_conflict", "name": "finalize_skill", "args": {}}]
    if SKILL_BUILDER_FINALIZE_MARKER in human_text:
        return [{"id": "call_e2e_sb_finalize", "name": "finalize_skill", "args": {}}]
    return None


def _skill_builder_final_content(human_text: str) -> str | None:
    if SKILL_BUILDER_WRITE_MARKER in human_text:
        return SKILL_BUILDER_WRITE_FINAL
    if SKILL_BUILDER_VALIDATE_MARKER in human_text:
        return SKILL_BUILDER_VALIDATE_FINAL
    if SKILL_BUILDER_RETEST_MARKER in human_text or SKILL_BUILDER_TEST_MARKER in human_text:
        return SKILL_BUILDER_TEST_FINAL
    if SKILL_BUILDER_FINALIZE_CONFLICT_MARKER in human_text:
        # 도구가 SOURCE_SKILL_CHANGED를 반환한 뒤 "에이전트가 사용자에게 설명"
        # 하는 §2-3 계약의 결정론 재현.
        return SKILL_BUILDER_FINALIZE_CONFLICT_FINAL
    if SKILL_BUILDER_FINALIZE_MARKER in human_text:
        return SKILL_BUILDER_FINALIZE_FINAL
    return None


TOOL_GROUP_MARKER = "E2E_TOOL_GROUP"
# Generic tool-call grouping fixture. ONE AIMessage emits N≥2 *consecutive*
# tool_calls of the SAME tool (``current_datetime`` ×3) plus ONE call of a
# DIFFERENT tool (``resolve_relative_date`` ×1). The frontend
# ``MessagePrimitive.GroupedParts`` must collapse the three identical calls into
# a single group container ("current_datetime · 3회") while the single different
# call renders as its own pill.
#
# Both tools are the always-appended temporal builtins
# (``_append_temporal_tools`` in ``runtime_component_builder``): no network, no
# external API, no flakiness, and — unlike ``write_file``/``execute_in_skill`` —
# they carry NO default HITL ``interrupt_on`` policy, so the run streams to
# completion without pausing on an approval card. Neither tool name maps to a
# ``chat.toolGroup.labels.*`` key, so the group header falls back to the raw
# tool name, which the E2E asserts on directly (locale-independent, robust).
TOOL_GROUP_GROUPED_TOOL = "current_datetime"
TOOL_GROUP_GROUPED_COUNT = 3
TOOL_GROUP_SEPARATE_TOOL = "resolve_relative_date"
TOOL_GROUP_FINAL_CONTENT = "E2E tool group rendering complete."


def _tool_group_tool_calls() -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = [
        {
            "id": f"call_e2e_tool_group_dt_{index}",
            "name": TOOL_GROUP_GROUPED_TOOL,
            "args": {},
        }
        for index in range(TOOL_GROUP_GROUPED_COUNT)
    ]
    calls.append(
        {
            "id": "call_e2e_tool_group_relative",
            "name": TOOL_GROUP_SEPARATE_TOOL,
            "args": {"expression": "오늘"},
        }
    )
    return calls


SEARCH_GROUP_MARKER = "E2E_SEARCH_GROUP"
# Search-tool grouping + source-aggregate fixture (LITE deep-research source row).
# ONE AIMessage emits ``tavily_search`` ×3 *consecutive same-tool* calls with
# DISTINCT queries. ``tavily_search`` is the deterministic, no-network scripted
# search builtin (``builtin:e2e_scripted_search`` in ``tool_factory``) that the
# runtime appends only when ``e2e_scripted_model_enabled`` is set. Each query
# returns a different multi-domain slice, so the frontend search-group aggregate
# collapses the 3 calls into ONE container ("웹 검색 · 3회") whose header shows
# domain badges + "출처 N개". ``tavily_search`` maps to label key ``webSearch``
# and carries NO HITL interrupt, so the run streams to completion uninterrupted.
#
# Source math (see ``_E2E_SCRIPTED_SEARCH_RESULTS``): 3 calls × 3 results = 9
# unique URLs across 5 unique domains (react.dev, vercel.com, nextjs.org,
# typescriptlang.org, developer.mozilla.org) → sourceCount = 9.
SEARCH_GROUP_TOOL = "tavily_search"
SEARCH_GROUP_QUERIES = ("react routing", "react hooks", "typescript generics")
SEARCH_GROUP_COUNT = len(SEARCH_GROUP_QUERIES)
SEARCH_GROUP_SOURCE_COUNT = 9
SEARCH_GROUP_DOMAIN_COUNT = 5
SEARCH_GROUP_FINAL_CONTENT = "E2E search group rendering complete."


def _search_group_tool_calls() -> list[dict[str, Any]]:
    return [
        {
            "id": f"call_e2e_search_group_{index}",
            "name": SEARCH_GROUP_TOOL,
            "args": {"query": query},
        }
        for index, query in enumerate(SEARCH_GROUP_QUERIES)
    ]


# W2-6 memory lifecycle fixtures. ONE memory-tool call per turn; the tool's
# policy branch decides the outcome — write_policy=auto → memory_saved (직접
# 저장 pill), write_policy=ask → memory_proposed (제안 카드: 승인/수정/거부).
# The spec flips the policy via PATCH /api/me/memory-settings between scenes.
MEMORY_SAVE_MARKER = "E2E_MEMORY_SAVE"
MEMORY_SAVE_CONTENT = "사용자는 결론 먼저, 표 중심의 보고서를 선호한다."
MEMORY_SAVE_REASON = "사용자가 보고서 형식을 명시적으로 지정함"
MEMORY_PROPOSE_MARKER = "E2E_MEMORY_PROPOSE"
MEMORY_PROPOSE_CONTENT = "매주 월요일 아침에 주간 계획 브리핑을 받고 싶어한다."
MEMORY_PROPOSE_REASON = "반복 일정 선호로 보임 — 저장 여부는 사용자 확인 필요"
MEMORY_FINAL_CONTENT = "E2E memory tool run complete."


# W2-2 rich search card fixtures. ONE standalone ``tavily_search`` call (not a
# group — grouping needs N≥2 consecutive same-tool calls) so the pill renders
# expanded with result cards. ``E2E_SEARCH_RICH``'s query is curated in
# ``tool_factory`` to return an ``answer`` (summary box) + content snippets;
# ``E2E_SEARCH_SHOP``'s ``shop:`` prefix returns the Naver shopping ``items``
# shape (thumbnail/lprice/mallName → 썸네일+가격 카드).
SEARCH_RICH_MARKER = "E2E_SEARCH_RICH"
SEARCH_RICH_QUERY = "agentic os 오버뷰"
SEARCH_RICH_FINAL_CONTENT = "E2E rich search rendering complete."
SEARCH_SHOP_MARKER = "E2E_SEARCH_SHOP"
SEARCH_SHOP_QUERY = "shop:무선 키보드"
SEARCH_SHOP_FINAL_CONTENT = "E2E shop search rendering complete."


def _single_search_tool_call(marker: str, query: str) -> list[dict[str, Any]]:
    return [
        {
            "id": f"call_{marker.lower()}",
            "name": SEARCH_GROUP_TOOL,
            "args": {"query": query},
        }
    ]


UI_DATA_DEMO_MARKER = "E2E_UI_DATA_DEMO"
# Generative UI demo fixtures (chat-generative-ui-dev-plan §7.3). ONE AIMessage
# calls the E2E-only ``e2e_ui_data_demo`` tool (with a ``kind`` arg) whose JSON
# result projects into a ``moldy.ui_data`` event; the follow-up turn streams a
# final message. Tool name matches ``tool_factory.E2E_UI_DATA_DEMO_TOOL_NAME``
# (kept as a literal to avoid an import cycle). Each marker maps to a ui_data
# ``kind`` so Phase 2 component types extend by adding one entry here + a fixture.
UI_DATA_TOOL_NAME = "e2e_ui_data_demo"
UI_DATA_TOOL_CALL_ID = "call_e2e_ui_data_demo"
UI_DATA_DEMO_FINAL_CONTENT = "E2E generative UI demo rendered."
UI_DATA_KIND_BY_MARKER = {
    "E2E_UI_DATA_DEMO": "demo_note",
    "E2E_UI_DATA_TABLE": "data_table",
    "E2E_UI_DATA_CHART": "chart",
    "E2E_UI_DATA_STATS": "stats",
    "E2E_UI_DATA_TERMINAL": "terminal",
}
# Backward-compatible aliases.
UI_DATA_DEMO_TOOL_NAME = UI_DATA_TOOL_NAME
UI_DATA_DEMO_TOOL_CALL_ID = UI_DATA_TOOL_CALL_ID


def _ui_data_marker_kind(human_text: str) -> str | None:
    for marker, kind in UI_DATA_KIND_BY_MARKER.items():
        if marker in human_text:
            return kind
    return None


ASK_USER_FRUIT_MARKER = "E2E_ASK_USER_FRUIT"
ASK_USER_FRUIT_TOOL_CALL_ID = "call_e2e_ask_user_fruit"
ASK_USER_FRUIT_PREFACE_CONTENT = "네, 골라봐요!"
ASK_USER_FRUIT_FINAL_CONTENT = "E2E ask_user fruit selection received."
ASK_USER_FRUIT_TOOL_ARGS = {
    "mode": "option_list",
    "title": "입력이 필요합니다",
    "question": "어떤 과일이 좋아요?",
    "options": [
        {"id": "apple", "label": "🍎 사과"},
        {"id": "grape", "label": "🍇 포도"},
        {"id": "pear", "label": "🍐 배"},
    ],
    "minSelections": 1,
    "maxSelections": 1,
}

# Additional ask_user shape variants (capture matrix). Each marker → one
# AIMessage that calls ``ask_user`` with a different payload shape so every
# renderer (text input, multi-select option_list, multi-step question_flow) is
# exercised. The follow-up turn (after the ToolMessage) streams ``final``.
ASK_USER_VARIANTS: dict[str, dict[str, Any]] = {
    "E2E_ASK_USER_TEXT": {
        "preface": "어떤 톤으로 작성할까요?",
        # mode omitted + no options → free-text input card (user-input-ui).
        "args": {"question": "원하시는 글의 톤을 자유롭게 적어주세요. (예: 친근하게, 전문적으로)"},
        "final": "E2E ask_user text input received.",
    },
    "E2E_ASK_USER_MULTI": {
        "preface": "관심 있는 운동을 모두 골라주세요!",
        "args": {
            "mode": "option_list",
            "title": "입력이 필요합니다",
            "question": "관심 있는 운동을 모두 선택하세요 (복수 선택 가능)",
            "options": [
                {"id": "run", "label": "🏃 러닝"},
                {"id": "swim", "label": "🏊 수영"},
                {"id": "yoga", "label": "🧘 요가"},
                {"id": "climb", "label": "🧗 클라이밍"},
            ],
            "minSelections": 1,
            "maxSelections": 3,
        },
        "final": "E2E ask_user multi-select received.",
    },
    "E2E_ASK_USER_FLOW": {
        "preface": "여행 취향을 몇 가지 여쭤볼게요.",
        "args": {
            "mode": "question_flow",
            "title": "여행 선호 조사",
            "questions": [
                {
                    "id": "dest",
                    "label": "목적지",
                    "question": "어디로 떠나고 싶으세요?",
                    "type": "single_select",
                    "options": ["국내", "아시아", "유럽"],
                    "required": True,
                },
                {
                    "id": "act",
                    "label": "활동",
                    "question": "하고 싶은 활동을 모두 고르세요",
                    "type": "multi_select",
                    "options": ["맛집 투어", "휴양", "액티비티", "쇼핑"],
                },
                {
                    "id": "note",
                    "label": "메모",
                    "question": "추가로 원하는 점이 있다면 적어주세요",
                    "type": "text",
                },
            ],
        },
        "final": "E2E ask_user question flow received.",
    },
}


def _ask_user_variant(human_text: str) -> tuple[str, dict[str, Any]] | None:
    for marker, variant in ASK_USER_VARIANTS.items():
        if marker in human_text:
            return marker, variant
    return None


def _is_rich_output_request(human_text: str) -> bool:
    if CHAT_RICH_OUTPUT_MARKER in human_text:
        return True

    lowered = human_text.lower()
    requested_surfaces = ("체크리스트", "표", "코드", "수식", "이미지", "링크")
    mentions_required_surfaces = all(surface in human_text for surface in requested_surfaces)
    mentions_quote = "인용" in human_text or "blockquote" in lowered
    mentions_mermaid = "mermaid" in lowered or "머메이드" in human_text
    return mentions_required_surfaces and mentions_quote and mentions_mermaid


def _is_ask_user_fruit_request(human_text: str) -> bool:
    if ASK_USER_FRUIT_MARKER in human_text:
        return True

    lowered = human_text.lower()
    asks_user = "ask user" in lowered or "ask_user" in lowered
    mentions_fruit_options = all(option in human_text for option in ("사과", "포도", "배"))
    return asks_user and mentions_fruit_options


def _is_hitl_approval_request(human_text: str) -> bool:
    # Explicit marker always wins so a generic prompt like "도구 승인 절차를
    # 설명해줘" (explain the tool-approval flow) does not accidentally fire an
    # ``execute_in_skill`` tool call.
    if HITL_APPROVAL_MARKER in human_text:
        return True

    lowered = human_text.lower()
    mentions_tool = "mcp" in lowered or "도구" in human_text or "tool" in lowered
    mentions_hitl = "hitl" in lowered or "승인" in human_text or "approval" in lowered
    # Require an explicit execution intent in addition to the tool/approval
    # mention so descriptive prompts ("설명/알려줘") are not mistaken for an
    # approval-triggering request. Backward compatible with existing specs that
    # phrase the prompt as "도구 사용 승인" / "tool ... HITL".
    mentions_execution = (
        "사용" in human_text or "실행" in human_text or "use" in lowered or "run" in lowered
    )
    return mentions_tool and mentions_hitl and mentions_execution


def _is_hitl_multi_request(human_text: str) -> bool:
    # Explicit marker only — there is no natural-language form. The multi-action
    # fixture must never fire accidentally because it stalls the run on two
    # approval cards until both are resolved.
    return HITL_MULTI_MARKER in human_text


def _is_hitl_edit_request(human_text: str) -> bool:
    # Explicit marker only — stalls the run on an edit-capable approval card.
    return HITL_EDIT_MARKER in human_text


def _document_tool_call(marker: str, tool_args: dict[str, str]) -> dict[str, Any]:
    return {
        "id": f"call_{marker.lower()}",
        "name": "execute_in_skill",
        "args": dict(tool_args),
    }


def _is_rejected_tool_message(message: ToolMessage) -> bool:
    """A HITL rejection (not a tool execution error).

    ``HumanInTheLoopMiddleware`` returns a rejected tool call as an error
    ``ToolMessage`` whose content says the tool was rejected / not executed. A
    tool that ran and failed is also an error ToolMessage, so match on the
    rejection wording rather than status alone.
    """
    if getattr(message, "status", None) != "error":
        return False
    content = _message_text(message).lower()
    return "rejected the tool call" in content or "was not executed" in content


def _message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return str(content)


class E2EScriptedChatModel(BaseChatModel):
    """Deterministic dev-only model for document artifact E2E tests."""

    slow_stream_delay_seconds: float = 0.75
    _bound_tool_names: tuple[str, ...] = PrivateAttr(default_factory=tuple)

    @property
    def _llm_type(self) -> str:
        return "e2e_scripted"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> E2EScriptedChatModel:
        clone = self.model_copy(deep=True)
        names: list[str] = []
        for tool in tools:
            if isinstance(tool, BaseTool):
                names.append(tool.name)
            elif isinstance(tool, dict):
                if isinstance(tool.get("name"), str):
                    names.append(tool["name"])
                    continue
                function = tool.get("function")
                if isinstance(function, dict) and isinstance(function.get("name"), str):
                    names.append(function["name"])
            elif hasattr(tool, "__name__"):
                names.append(str(tool.__name__))
            elif hasattr(tool, "name") and isinstance(tool.name, str):
                names.append(tool.name)
        clone._bound_tool_names = tuple(names)  # noqa: SLF001 — clone is our own class
        return clone

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        skill_eval_response = _skill_eval_arm_response(messages)
        if skill_eval_response is not None:
            return ChatResult(generations=[ChatGeneration(message=skill_eval_response)])
        human_text = self._latest_human_text(messages)
        if ERROR_MARKER in human_text:
            # RateLimitError prefix triggers public_stream_error_message masking so
            # the error bubble shows the production-representative provider failure
            # message (and run.error_message stores that masked value).
            raise RuntimeError("RateLimitError: E2E scripted model error simulation")
        if is_langgraph_v3_subagent_prompt(human_text):
            return ChatResult(
                generations=[ChatGeneration(message=langgraph_v3_subagent_response(human_text))]
            )

        if is_langgraph_v3_prompt(human_text):
            message = langgraph_v3_message(
                messages,
                human_text,
                bound_tool_names=self._bound_tool_names,
                docx_tool_args=LANGGRAPH_V3_ARTIFACT_COMMAND,
            )
            return ChatResult(generations=[ChatGeneration(message=message)])

        if messages and isinstance(messages[-1], ToolMessage):
            # A REJECTED tool call comes back as an error ToolMessage carrying a
            # rejection notice. Distinguish it from a genuine tool EXECUTION error
            # (e.g. an approved edit that ran and failed) by the message text, not
            # just status="error" — otherwise an edit-approve whose tool errors
            # would wrongly read as "취소했어요".
            if _is_rejected_tool_message(messages[-1]):
                message = AIMessage(content=HITL_REJECTED_ACK_CONTENT)
                return ChatResult(generations=[ChatGeneration(message=message)])
            skill_builder_final = _skill_builder_final_content(human_text)
            if skill_builder_final is not None:
                message = AIMessage(content=skill_builder_final)
                return ChatResult(generations=[ChatGeneration(message=message)])
            if _ui_data_marker_kind(human_text) is not None:
                message = AIMessage(content=UI_DATA_DEMO_FINAL_CONTENT)
                return ChatResult(generations=[ChatGeneration(message=message)])
            if SEARCH_GROUP_MARKER in human_text:
                message = AIMessage(content=SEARCH_GROUP_FINAL_CONTENT)
                return ChatResult(generations=[ChatGeneration(message=message)])
            if SEARCH_RICH_MARKER in human_text:
                message = AIMessage(content=SEARCH_RICH_FINAL_CONTENT)
                return ChatResult(generations=[ChatGeneration(message=message)])
            if SEARCH_SHOP_MARKER in human_text:
                message = AIMessage(content=SEARCH_SHOP_FINAL_CONTENT)
                return ChatResult(generations=[ChatGeneration(message=message)])
            if MEMORY_SAVE_MARKER in human_text or MEMORY_PROPOSE_MARKER in human_text:
                message = AIMessage(content=MEMORY_FINAL_CONTENT)
                return ChatResult(generations=[ChatGeneration(message=message)])
            if TOOL_GROUP_MARKER in human_text:
                message = AIMessage(content=TOOL_GROUP_FINAL_CONTENT)
                return ChatResult(generations=[ChatGeneration(message=message)])
            if _is_ask_user_fruit_request(human_text):
                message = AIMessage(content=ASK_USER_FRUIT_FINAL_CONTENT)
                return ChatResult(generations=[ChatGeneration(message=message)])
            variant_done = _ask_user_variant(human_text)
            if variant_done is not None:
                message = AIMessage(content=variant_done[1]["final"])
                return ChatResult(generations=[ChatGeneration(message=message)])
            if ARTIFACT_SLOW_FINAL_MARKER in human_text:
                message = AIMessage(content="".join(ARTIFACT_SLOW_FINAL_PARTS))
                return ChatResult(generations=[ChatGeneration(message=message)])
            message = AIMessage(
                content="문서 파일 생성이 완료되었습니다. 오른쪽 파일 패널에서 확인하세요."
            )
            return ChatResult(generations=[ChatGeneration(message=message)])

        if SLOW_STREAM_MARKER in human_text:
            message = AIMessage(content="".join(SLOW_STREAM_PARTS))
            return ChatResult(generations=[ChatGeneration(message=message)])
        if VISUAL_SLOW_STREAM_MARKER in human_text:
            message = AIMessage(content="".join(VISUAL_SLOW_STREAM_PARTS))
            return ChatResult(generations=[ChatGeneration(message=message)])
        if TOKEN_USAGE_MARKER in human_text:
            message = AIMessage(
                content=TOKEN_USAGE_CONTENT,
                usage_metadata=dict(TOKEN_USAGE_METADATA),
            )
            return ChatResult(generations=[ChatGeneration(message=message)])
        if _is_rich_output_request(human_text):
            message = AIMessage(content=CHAT_RICH_OUTPUT_CONTENT)
            return ChatResult(generations=[ChatGeneration(message=message)])
        if _is_ask_user_fruit_request(human_text):
            message = AIMessage(
                content=ASK_USER_FRUIT_PREFACE_CONTENT,
                tool_calls=[
                    {
                        "id": ASK_USER_FRUIT_TOOL_CALL_ID,
                        "name": "ask_user",
                        "args": dict(ASK_USER_FRUIT_TOOL_ARGS),
                    }
                ],
            )
            return ChatResult(generations=[ChatGeneration(message=message)])

        variant = _ask_user_variant(human_text)
        if variant is not None:
            marker, spec = variant
            # deepcopy the args (nested options/questions lists) so a downstream
            # in-place mutation can't corrupt the module-level variant spec.
            message = AIMessage(
                content=spec["preface"],
                tool_calls=[
                    {
                        "id": f"call_{marker.lower()}",
                        "name": "ask_user",
                        "args": deepcopy(spec["args"]),
                    }
                ],
            )
            return ChatResult(generations=[ChatGeneration(message=message)])

        skill_builder_calls = _skill_builder_tool_calls(human_text)
        if skill_builder_calls is not None:
            message = AIMessage(content="", tool_calls=skill_builder_calls)
            return ChatResult(generations=[ChatGeneration(message=message)])

        if _is_hitl_multi_request(human_text):
            message = AIMessage(
                content="",
                # Deep-copy each call's args too (not just the outer dict) so a
                # downstream in-place mutation can't corrupt the module-level
                # HITL_MULTI_TOOL_CALLS constant across runs — matching the
                # dict(tool_args) copy in _document_tool_call.
                tool_calls=[{**call, "args": dict(call["args"])} for call in HITL_MULTI_TOOL_CALLS],
            )
            return ChatResult(generations=[ChatGeneration(message=message)])

        if _is_hitl_edit_request(human_text):
            # ONE edit_file call → an edit-capable approval card (수정 button +
            # field editor). Fresh args dict so the module constant can't be
            # mutated downstream.
            message = AIMessage(
                content="",
                tool_calls=[{**HITL_EDIT_TOOL_CALL, "args": dict(HITL_EDIT_TOOL_CALL["args"])}],
            )
            return ChatResult(generations=[ChatGeneration(message=message)])

        ui_data_kind = _ui_data_marker_kind(human_text)
        if ui_data_kind is not None:
            # ONE AIMessage calls the demo tool with the requested kind; its JSON
            # result projects into a ``moldy.ui_data`` event. The tool_call_id is
            # per-kind so a multi-turn conversation (different kinds) never reuses
            # one id within a thread — duplicate ids confuse LangGraph state and
            # collapse the frontend's per-turn tool_call_id attach. Fresh args.
            args: dict[str, Any] = {} if ui_data_kind == "demo_note" else {"kind": ui_data_kind}
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": f"call_e2e_ui_data_{ui_data_kind}",
                        "name": UI_DATA_TOOL_NAME,
                        "args": args,
                    }
                ],
            )
            return ChatResult(generations=[ChatGeneration(message=message)])

        if SEARCH_GROUP_MARKER in human_text:
            # ONE AIMessage with N≥2 consecutive same-tool search calls (distinct
            # queries → distinct scripted result slices). Fresh dicts every call
            # so the downstream graph cannot mutate shared module state.
            message = AIMessage(content="", tool_calls=_search_group_tool_calls())
            return ChatResult(generations=[ChatGeneration(message=message)])

        if SEARCH_RICH_MARKER in human_text:
            message = AIMessage(
                content="",
                tool_calls=_single_search_tool_call(SEARCH_RICH_MARKER, SEARCH_RICH_QUERY),
            )
            return ChatResult(generations=[ChatGeneration(message=message)])

        if MEMORY_SAVE_MARKER in human_text:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call_e2e_memory_save",
                        "name": "save_user_memory",
                        "args": {
                            "content": MEMORY_SAVE_CONTENT,
                            "reason": MEMORY_SAVE_REASON,
                        },
                    }
                ],
            )
            return ChatResult(generations=[ChatGeneration(message=message)])

        if MEMORY_PROPOSE_MARKER in human_text:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call_e2e_memory_propose",
                        "name": "propose_memory",
                        "args": {
                            "scope": "user",
                            "content": MEMORY_PROPOSE_CONTENT,
                            "reason": MEMORY_PROPOSE_REASON,
                        },
                    }
                ],
            )
            return ChatResult(generations=[ChatGeneration(message=message)])

        if SEARCH_SHOP_MARKER in human_text:
            message = AIMessage(
                content="",
                tool_calls=_single_search_tool_call(SEARCH_SHOP_MARKER, SEARCH_SHOP_QUERY),
            )
            return ChatResult(generations=[ChatGeneration(message=message)])

        if TOOL_GROUP_MARKER in human_text:
            # ONE AIMessage with N≥2 consecutive same-tool calls + 1 different
            # tool. _tool_group_tool_calls() builds fresh dicts every call so the
            # downstream graph cannot mutate shared module state across runs.
            message = AIMessage(content="", tool_calls=_tool_group_tool_calls())
            return ChatResult(generations=[ChatGeneration(message=message)])

        if _is_hitl_approval_request(human_text):
            message = AIMessage(
                content="",
                tool_calls=[
                    _document_tool_call("E2E_DOCX", SCRIPTED_DOCUMENT_COMMANDS["E2E_DOCX"]),
                ],
            )
            return ChatResult(generations=[ChatGeneration(message=message)])

        for marker, tool_args in SCRIPTED_DOCUMENT_COMMANDS.items():
            if marker in human_text:
                message = AIMessage(
                    content="",
                    tool_calls=[_document_tool_call(marker, tool_args)],
                )
                return ChatResult(generations=[ChatGeneration(message=message)])

        if DAILY_GREETING_MARKER in human_text:
            message = AIMessage(content=DAILY_GREETING_CONTENT)
            return ChatResult(generations=[ChatGeneration(message=message)])

        message = AIMessage(content="E2E scripted document model is ready.")
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ):
        human_text = self._latest_human_text(messages)
        if ERROR_MARKER in human_text:
            # RateLimitError prefix triggers public_stream_error_message masking so
            # the error bubble shows the production-representative provider failure
            # message (and run.error_message stores that masked value).
            raise RuntimeError("RateLimitError: E2E scripted model error simulation")
        if is_langgraph_v3_subagent_prompt(human_text):
            for part in langgraph_v3_subagent_parts(human_text):
                if self.slow_stream_delay_seconds > 0:
                    time.sleep(self.slow_stream_delay_seconds)
                yield ChatGenerationChunk(message=AIMessageChunk(content=part))
            return

        if (
            messages
            and isinstance(messages[-1], ToolMessage)
            and ARTIFACT_SLOW_FINAL_MARKER in human_text
        ):
            for part in ARTIFACT_SLOW_FINAL_PARTS:
                if self.slow_stream_delay_seconds > 0:
                    time.sleep(self.slow_stream_delay_seconds)
                yield ChatGenerationChunk(message=AIMessageChunk(content=part))
            return
        if SLOW_STREAM_MARKER in human_text:
            for part in SLOW_STREAM_PARTS:
                if self.slow_stream_delay_seconds > 0:
                    time.sleep(self.slow_stream_delay_seconds)
                yield ChatGenerationChunk(message=AIMessageChunk(content=part))
            return
        if VISUAL_SLOW_STREAM_MARKER in human_text:
            for part in VISUAL_SLOW_STREAM_PARTS:
                if self.slow_stream_delay_seconds > 0:
                    time.sleep(self.slow_stream_delay_seconds)
                yield ChatGenerationChunk(message=AIMessageChunk(content=part))
            return

        result = self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        message = result.generations[0].message
        usage_metadata = getattr(message, "usage_metadata", None)
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content=message.content,
                tool_calls=list(getattr(message, "tool_calls", []) or []),
                usage_metadata=usage_metadata,
            )
        )

    def _latest_human_text(self, messages: list[BaseMessage]) -> str:
        latest_human = next(
            (message for message in reversed(messages) if isinstance(message, HumanMessage)),
            None,
        )
        return _message_text(latest_human) if latest_human is not None else ""


__all__ = [
    "SKILL_BUILDER_FINALIZE_MARKER",
    "SKILL_BUILDER_RETEST_MARKER",
    "SKILL_BUILDER_SANDBOX_OUTPUT",
    "SKILL_BUILDER_TEST_MARKER",
    "SKILL_BUILDER_VALIDATE_MARKER",
    "SKILL_BUILDER_WRITE_MARKER",
    "E2EScriptedChatModel",
    "ARTIFACT_SLOW_FINAL_MARKER",
    "CHAT_RICH_OUTPUT_CONTENT",
    "CHAT_RICH_OUTPUT_MARKER",
    "CHAT_RICH_OUTPUT_PROMPT",
    "DAILY_GREETING_CONTENT",
    "DAILY_GREETING_MARKER",
    "HITL_APPROVAL_MARKER",
    "HITL_EDIT_MARKER",
    "HITL_EDIT_TOOL_CALL",
    "HITL_REJECTED_ACK_CONTENT",
    "HITL_MULTI_MARKER",
    "HITL_MULTI_TOOL_CALLS",
    "ASK_USER_FRUIT_FINAL_CONTENT",
    "ASK_USER_FRUIT_MARKER",
    "ASK_USER_FRUIT_PREFACE_CONTENT",
    "ASK_USER_FRUIT_TOOL_ARGS",
    "ASK_USER_FRUIT_TOOL_CALL_ID",
    "LANGGRAPH_V3_MARKER",
    "SCRIPTED_DOCUMENT_COMMANDS",
    "SEARCH_GROUP_COUNT",
    "SEARCH_GROUP_DOMAIN_COUNT",
    "SEARCH_GROUP_FINAL_CONTENT",
    "SEARCH_GROUP_MARKER",
    "SEARCH_GROUP_QUERIES",
    "SEARCH_GROUP_SOURCE_COUNT",
    "SEARCH_GROUP_TOOL",
    "MEMORY_FINAL_CONTENT",
    "MEMORY_PROPOSE_CONTENT",
    "MEMORY_PROPOSE_MARKER",
    "MEMORY_SAVE_CONTENT",
    "MEMORY_SAVE_MARKER",
    "SEARCH_RICH_FINAL_CONTENT",
    "SEARCH_RICH_MARKER",
    "SEARCH_RICH_QUERY",
    "SEARCH_SHOP_FINAL_CONTENT",
    "SEARCH_SHOP_MARKER",
    "SEARCH_SHOP_QUERY",
    "SLOW_STREAM_MARKER",
    "TOKEN_USAGE_CONTENT",
    "TOKEN_USAGE_MARKER",
    "TOKEN_USAGE_METADATA",
    "TOOL_GROUP_FINAL_CONTENT",
    "TOOL_GROUP_GROUPED_COUNT",
    "TOOL_GROUP_GROUPED_TOOL",
    "TOOL_GROUP_MARKER",
    "TOOL_GROUP_SEPARATE_TOOL",
    "UI_DATA_DEMO_FINAL_CONTENT",
    "UI_DATA_DEMO_MARKER",
    "UI_DATA_DEMO_TOOL_CALL_ID",
    "UI_DATA_DEMO_TOOL_NAME",
    "UI_DATA_KIND_BY_MARKER",
    "UI_DATA_TOOL_CALL_ID",
    "UI_DATA_TOOL_NAME",
    "VISUAL_SLOW_STREAM_MARKER",
]
