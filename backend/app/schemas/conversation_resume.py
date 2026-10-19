"""HiTL resume decision schemas (moved out of ``schemas.conversation``; G-06)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, model_validator


class Decision(BaseModel):
    """단일 tool_call에 대한 인간 결정.

    LangChain ``HumanInTheLoopMiddleware``의 ``HITLResponse.decisions[i]``와
    동일 shape (1:1 매칭). router에서 검증한 뒤 ``model_dump(exclude_none=True)``
    로 dict 직렬화하여 ``Command(resume={"decisions": [dict, ...]})``로 송신한다
    (LangChain 미들웨어는 ``NotRequired`` TypedDict를 받음).

    - ``approve``: 추가 필드 없음.
    - ``edit``: ``edited_action={"name": str, "args": dict}`` 필수.
    - ``reject``: ``message`` 선택 (없으면 미들웨어가 기본 메시지 생성).
    - ``respond``: ``message`` 필수 (synthetic ToolMessage content).
    """

    type: Literal["approve", "edit", "reject", "respond"]
    edited_action: dict[str, Any] | None = None  # type=edit 시 필수
    message: str | None = None  # type=respond 시 필수, type=reject 시 선택
    # auth_required only (G-06): the re-ask being answered, so a stale one is refused.
    attempt: int | None = None

    @model_validator(mode="after")
    def _validate_payload_for_type(self) -> Decision:
        if self.type == "edit" and self.edited_action is None:
            raise ValueError("Decision(type='edit') requires 'edited_action'")
        if self.type == "respond" and self.message is None:
            raise ValueError("Decision(type='respond') requires 'message'")
        return self


class ResumeRequest(BaseModel):
    """HiTL resume 요청. LangChain ``HITLResponse`` 호환 표준 wire."""

    decisions: list[Decision]
