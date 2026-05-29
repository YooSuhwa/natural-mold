"""Temporal context helpers for date-sensitive agent answers."""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.agent_runtime.temporal import (
    build_temporal_context_prompt,
    resolve_relative_date_expression,
)
from app.agent_runtime.tool_factory import create_builtin_tool

SEOUL = ZoneInfo("Asia/Seoul")


def test_temporal_context_prompt_includes_today_and_this_weekend() -> None:
    now = datetime(2026, 5, 29, 11, 33, tzinfo=SEOUL)

    prompt = build_temporal_context_prompt(now=now)

    assert "현재 기준 날짜와 시간" in prompt
    assert "2026-05-29" in prompt
    assert "금요일" in prompt
    assert "Asia/Seoul" in prompt
    assert "이번 주말: 2026-05-30(토요일) ~ 2026-05-31(일요일)" in prompt
    assert "상대 날짜" in prompt


def test_resolve_relative_date_expression_this_weekend() -> None:
    now = datetime(2026, 5, 29, 11, 33, tzinfo=SEOUL)

    result = resolve_relative_date_expression("이번주 주말", now=now)

    assert result["success"] is True
    assert result["label"] == "이번 주말"
    assert result["start_date"] == "2026-05-30"
    assert result["end_date"] == "2026-05-31"
    assert result["weekday_start"] == "토요일"
    assert result["weekday_end"] == "일요일"


def test_resolve_relative_date_expression_next_wednesday() -> None:
    now = datetime(2026, 5, 29, 11, 33, tzinfo=SEOUL)

    result = resolve_relative_date_expression("다음주 수요일 약속", now=now)

    assert result["success"] is True
    assert result["label"] == "다음 주 수요일"
    assert result["start_date"] == "2026-06-03"
    assert result["end_date"] == "2026-06-03"
    assert result["weekday_start"] == "수요일"


def test_resolve_relative_date_expression_recent_news_window() -> None:
    now = datetime(2026, 5, 29, 11, 33, tzinfo=SEOUL)

    result = resolve_relative_date_expression("한컴 최근 뉴스", now=now)

    assert result["success"] is True
    assert result["label"] == "최근 7일"
    assert result["start_date"] == "2026-05-23"
    assert result["end_date"] == "2026-05-29"
    assert result["timezone"] == "Asia/Seoul"


def test_resolve_relative_date_expression_recent_schedule_does_not_match_sunday() -> None:
    now = datetime(2026, 5, 29, 11, 33, tzinfo=SEOUL)

    result = resolve_relative_date_expression("최근 일정", now=now)

    assert result["success"] is True
    assert result["label"] == "최근 7일"
    assert result["start_date"] == "2026-05-23"
    assert result["end_date"] == "2026-05-29"


@pytest.mark.asyncio
async def test_resolve_relative_date_builtin_tool_returns_json() -> None:
    tool = create_builtin_tool("builtin:resolve_relative_date")

    assert tool is not None
    result = await tool.coroutine(
        expression="다음주 수요일",
        reference_date="2026-05-29T11:33:00+09:00",
    )

    data = json.loads(result)
    assert data["success"] is True
    assert data["start_date"] == "2026-06-03"
    assert data["weekday_start"] == "수요일"
