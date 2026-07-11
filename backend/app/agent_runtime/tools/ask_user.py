"""ask_user — 사용자에게 질문하고 응답을 기다리는 도구.

LangGraph interrupt()를 사용하여 그래프 실행을 일시정지하고,
사용자의 응답을 받은 후 Command(resume=)로 재개한다.
"""

from typing import Any, Literal

from langchain_core.tools import tool
from langgraph.types import interrupt
from pydantic import BaseModel, Field, model_validator


class AskUserOption(BaseModel):
    """Serializable option item for ask_user v2 payloads."""

    id: str | None = None
    label: str
    description: str | None = None
    disabled: bool | None = None


class AskUserQuestion(BaseModel):
    """One step in a question_flow payload."""

    id: str | None = None
    label: str | None = None
    question: str | None = None
    type: Literal["single_select", "multi_select", "text"] | None = None
    options: list[str | AskUserOption] | None = None
    required: bool | None = None


class AskUserInput(BaseModel):
    """Backward-compatible schema for the canonical ask_user tool."""

    question: str | None = None
    options: list[str | AskUserOption] | None = None
    mode: Literal["question_flow", "option_list"] | None = None
    title: str | None = None
    questions: list[AskUserQuestion] | None = None
    minSelections: int | None = Field(default=None, ge=0)  # noqa: N815 — tool schema wire contract
    maxSelections: int | None = Field(default=None, ge=1)  # noqa: N815 — tool schema wire contract

    @model_validator(mode="after")
    def _validate_shape(self) -> "AskUserInput":
        if self.mode is None and not self.question:
            raise ValueError("ask_user requires question when mode is omitted")
        if self.mode == "question_flow" and not self.questions:
            raise ValueError("question_flow mode requires questions")
        if self.mode == "option_list" and not self.options:
            raise ValueError("option_list mode requires options")
        if (
            self.minSelections is not None
            and self.maxSelections is not None
            and self.minSelections > self.maxSelections
        ):
            raise ValueError("minSelections cannot be greater than maxSelections")
        return self


def _extract_respond_message(response: object) -> str:
    """Return the user's message from LangChain's standard HITL resume shape."""

    if isinstance(response, dict):
        decisions = response.get("decisions")
        if isinstance(decisions, list):
            for decision in decisions:
                if not isinstance(decision, dict):
                    continue
                if decision.get("type") == "respond":
                    message = decision.get("message")
                    if isinstance(message, str):
                        return message
                    if message is not None:
                        return str(message)
    return str(response)


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items() if item is not None}
    return value


def _build_interrupt_payload(
    *,
    question: str | None,
    options: list[str | AskUserOption] | None,
    mode: Literal["question_flow", "option_list"] | None,
    title: str | None,
    questions: list[AskUserQuestion] | None,
    minSelections: int | None,  # noqa: N803 — mirrors tool schema wire contract
    maxSelections: int | None,  # noqa: N803 — mirrors tool schema wire contract
) -> dict[str, Any]:
    if mode is None:
        return {
            "type": "ask_user",
            "question": question or "",
            "options": _to_jsonable(options or []),
        }

    payload: dict[str, Any] = {"type": "ask_user", "mode": mode}
    for key, value in {
        "title": title,
        "question": question,
        "questions": questions,
        "options": options,
        "minSelections": minSelections,
        "maxSelections": maxSelections,
    }.items():
        if value is not None:
            payload[key] = _to_jsonable(value)
    return payload


@tool(args_schema=AskUserInput)
def ask_user(
    question: str | None = None,
    options: list[str | AskUserOption] | None = None,
    mode: Literal["question_flow", "option_list"] | None = None,
    title: str | None = None,
    questions: list[AskUserQuestion] | None = None,
    minSelections: int | None = None,  # noqa: N803 — mirrors tool schema wire contract
    maxSelections: int | None = None,  # noqa: N803 — mirrors tool schema wire contract
) -> str:
    """사용자에게 질문하고 응답을 기다립니다.

    다음 상황에서만 사용하세요:
    - 사용자의 요청이 모호하여 2가지 이상 해석이 가능할 때
    - 중요한 작업 실행 전 최종 확인이 필요할 때
    - 여러 옵션 중 사용자의 선호를 확인해야 할 때

    다음 상황에서는 사용하지 마세요:
    - 일반적인 질문에 답변할 때 (바로 답하세요)
    - 이미 충분한 정보가 있을 때
    - 단순한 인사나 잡담

    Args:
        question: 사용자에게 보여줄 단일 질문 (legacy)
        options: 선택지 목록 (legacy 또는 option_list)
        mode: question_flow 또는 option_list 렌더 모드
        title: 확장 모드 카드 제목
        questions: question_flow 단계 목록
        minSelections: option_list 최소 선택 수
        maxSelections: option_list 최대 선택 수
    """
    response = interrupt(
        _build_interrupt_payload(
            question=question,
            options=options,
            mode=mode,
            title=title,
            questions=questions,
            minSelections=minSelections,
            maxSelections=maxSelections,
        )
    )
    return _extract_respond_message(response)
