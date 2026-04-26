"""Tests for app.agent_runtime.assistant.tools.clarify_tools — ask_clarifying_question."""

from __future__ import annotations

import json

import pytest

from app.agent_runtime.assistant.tools.clarify_tools import build_clarify_tools


@pytest.mark.asyncio
async def test_ask_clarifying_question():
    """ask_clarifying_question returns JSON with question + 4 options."""
    tools = build_clarify_tools()
    assert len(tools) == 1
    tool = tools[0]
    assert tool.name == "ask_clarifying_question"

    result = await tool.ainvoke(
        {
            "question": "어떤 유형의 검색을 원하시나요?",
            "option_1": "웹 검색",
            "option_2": "뉴스 검색",
            "option_3": "이미지 검색",
        }
    )

    data = json.loads(result)
    assert data["type"] == "clarifying_question"
    assert data["question"] == "어떤 유형의 검색을 원하시나요?"
    assert len(data["options"]) == 4
    assert "웹 검색" in data["options"]
    assert "뉴스 검색" in data["options"]
    assert "이미지 검색" in data["options"]
    assert "직접 입력" in data["options"]
