from __future__ import annotations

from typing import TypedDict

from langchain_core.messages import BaseMessage

from app.schemas.skill_builder import JsonValue


class SkillBuilderState(TypedDict, total=False):
    messages: list[BaseMessage]
    user_id: str
    session_id: str
    mode: str
    source_skill_id: str | None
    base_snapshot: dict[str, JsonValue] | None
    user_request: str
    current_phase: int
    intent: dict[str, JsonValue]
    draft_package: dict[str, JsonValue]
    validation_result: dict[str, JsonValue]
    compatibility_result: dict[str, JsonValue]
    changelog_draft: dict[str, JsonValue]
    eval_result: dict[str, JsonValue]
    trigger_eval_result: dict[str, JsonValue]
    review_message: str
    next_action: str
