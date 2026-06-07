from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from pydantic import PrivateAttr

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
        if messages and isinstance(messages[-1], ToolMessage):
            message = AIMessage(
                content="문서 파일 생성이 완료되었습니다. 오른쪽 파일 패널에서 확인하세요."
            )
            return ChatResult(generations=[ChatGeneration(message=message)])

        latest_human = next(
            (message for message in reversed(messages) if isinstance(message, HumanMessage)),
            None,
        )
        human_text = _message_text(latest_human) if latest_human is not None else ""
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


__all__ = ["E2EScriptedChatModel", "SCRIPTED_DOCUMENT_COMMANDS"]
