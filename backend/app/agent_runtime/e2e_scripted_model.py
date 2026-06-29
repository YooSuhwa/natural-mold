from __future__ import annotations

import time
from collections.abc import Callable, Sequence
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
TOKEN_USAGE_MARKER = "E2E_TOKEN_USAGE_STREAM"
TOKEN_USAGE_CONTENT = "E2E token usage isolated conversation response."
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


UI_DATA_DEMO_MARKER = "E2E_UI_DATA_DEMO"
# Generative UI demo fixture (chat-generative-ui-dev-plan §7.3). ONE AIMessage
# calls the E2E-only ``e2e_ui_data_demo`` tool whose JSON result projects into a
# ``moldy.ui_data`` event (``demo_note``); the follow-up turn streams a final
# message. Tool name matches ``tool_factory.E2E_UI_DATA_DEMO_TOOL_NAME`` (kept as
# a literal to avoid an import cycle).
UI_DATA_DEMO_TOOL_NAME = "e2e_ui_data_demo"
UI_DATA_DEMO_TOOL_CALL_ID = "call_e2e_ui_data_demo"
UI_DATA_DEMO_FINAL_CONTENT = "E2E generative UI demo rendered."

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


def _document_tool_call(marker: str, tool_args: dict[str, str]) -> dict[str, Any]:
    return {
        "id": f"call_{marker.lower()}",
        "name": "execute_in_skill",
        "args": dict(tool_args),
    }


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
        clone._bound_tool_names = tuple(names)
        return clone

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        human_text = self._latest_human_text(messages)
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
            if UI_DATA_DEMO_MARKER in human_text:
                message = AIMessage(content=UI_DATA_DEMO_FINAL_CONTENT)
                return ChatResult(generations=[ChatGeneration(message=message)])
            if SEARCH_GROUP_MARKER in human_text:
                message = AIMessage(content=SEARCH_GROUP_FINAL_CONTENT)
                return ChatResult(generations=[ChatGeneration(message=message)])
            if TOOL_GROUP_MARKER in human_text:
                message = AIMessage(content=TOOL_GROUP_FINAL_CONTENT)
                return ChatResult(generations=[ChatGeneration(message=message)])
            if _is_ask_user_fruit_request(human_text):
                message = AIMessage(content=ASK_USER_FRUIT_FINAL_CONTENT)
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

        if UI_DATA_DEMO_MARKER in human_text:
            # ONE AIMessage calls the demo tool; its result projects into a
            # ``moldy.ui_data`` (demo_note) event. Fresh args dict every call.
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": UI_DATA_DEMO_TOOL_CALL_ID,
                        "name": UI_DATA_DEMO_TOOL_NAME,
                        "args": {},
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
    "E2EScriptedChatModel",
    "ARTIFACT_SLOW_FINAL_MARKER",
    "CHAT_RICH_OUTPUT_CONTENT",
    "CHAT_RICH_OUTPUT_MARKER",
    "CHAT_RICH_OUTPUT_PROMPT",
    "HITL_APPROVAL_MARKER",
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
    "VISUAL_SLOW_STREAM_MARKER",
]
