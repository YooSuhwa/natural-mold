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

    slow_stream_delay_seconds: float = 0.2
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
            elif isinstance(tool, dict) and isinstance(tool.get("name"), str):
                names.append(tool["name"])
            elif hasattr(tool, "__name__"):
                names.append(str(tool.__name__))
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

        for marker, tool_args in SCRIPTED_DOCUMENT_COMMANDS.items():
            if marker in human_text:
                message = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": f"call_{marker.lower()}",
                            "name": "execute_in_skill",
                            "args": dict(tool_args),
                        }
                    ],
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
    "LANGGRAPH_V3_MARKER",
    "SCRIPTED_DOCUMENT_COMMANDS",
    "SLOW_STREAM_MARKER",
    "TOKEN_USAGE_CONTENT",
    "TOKEN_USAGE_MARKER",
    "TOKEN_USAGE_METADATA",
    "VISUAL_SLOW_STREAM_MARKER",
]
