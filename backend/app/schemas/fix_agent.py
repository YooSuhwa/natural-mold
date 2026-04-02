from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class FixAgentMessageRequest(BaseModel):
    content: str
    conversation_history: list[dict[str, str]] = []


class FixAgentChanges(BaseModel):
    system_prompt: str | None = None
    name: str | None = None
    description: str | None = None
    add_tools: list[str] = []
    remove_tools: list[str] = []
    model_name: str | None = None
    model_params: dict[str, Any] | None = None


class FixAgentResponse(BaseModel):
    role: str = "assistant"
    content: str
    action: str  # "preview" | "apply" | "ask"
    changes: FixAgentChanges | None = None
    summary: str | None = None
    question: str | None = None
    conversation_history: list[dict[str, str]]
