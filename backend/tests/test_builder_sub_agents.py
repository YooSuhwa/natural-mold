"""Tests for builder sub-agents — intent_analyzer, tool_recommender,
middleware_recommender, prompt_generator.

All LLM calls are mocked via invoke_with_json_retry / invoke_for_text patches.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.builder import (
    AgentCreationIntent,
    MiddlewareRecommendation,
    ToolRecommendation,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_intent(**overrides) -> AgentCreationIntent:
    defaults = {
        "agent_name": "Weather Bot",
        "agent_name_ko": "날씨 봇",
        "agent_description": "날씨를 알려주는 에이전트입니다.",
        "primary_task_type": "날씨 정보 조회",
        "use_cases": ["날씨 검색"],
        "required_capabilities": ["웹 검색"],
    }
    defaults.update(overrides)
    return AgentCreationIntent(**defaults)


TOOL_CATALOG = [
    {"name": "Web Search", "type": "prebuilt", "description": "웹 검색"},
    {"name": "Web Scraper", "type": "prebuilt", "description": "웹 스크래핑"},
]

MW_CATALOG = [
    {
        "type": "summarization",
        "name": "SummarizationMiddleware",
        "description": "요약",
        "category": "context",
    },
    {
        "type": "tool_retry",
        "name": "ToolRetryMiddleware",
        "description": "재시도",
        "category": "reliability",
    },
]


# ---------------------------------------------------------------------------
# intent_analyzer.analyze_intent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_intent_success():
    """Mock LLM returns valid intent JSON."""
    mock_data = {
        "agent_name": "News Bot",
        "agent_name_ko": "뉴스 봇",
        "agent_description": "뉴스를 요약하는 에이전트",
        "primary_task_type": "뉴스 요약",
        "use_cases": ["뉴스 검색"],
        "required_capabilities": ["웹 검색"],
    }

    with patch(
        "app.agent_runtime.builder.sub_agents.intent_analyzer.invoke_with_json_retry",
        new_callable=AsyncMock,
        return_value=mock_data,
    ):
        from app.agent_runtime.builder.sub_agents.intent_analyzer import analyze_intent

        result = await analyze_intent("뉴스 봇 만들어줘")
        assert result.agent_name == "News Bot"
        assert result.agent_name_ko == "뉴스 봇"
        assert result.primary_task_type == "뉴스 요약"


@pytest.mark.asyncio
async def test_analyze_intent_fallback():
    """When LLM fails, fallback intent is returned."""
    with patch(
        "app.agent_runtime.builder.sub_agents.intent_analyzer.invoke_with_json_retry",
        new_callable=AsyncMock,
        side_effect=ValueError("JSON parsing failed"),
    ):
        from app.agent_runtime.builder.sub_agents.intent_analyzer import analyze_intent

        result = await analyze_intent("테스트 봇")
        assert result.agent_name == "Custom Agent"
        assert result.agent_name_ko == "맞춤 에이전트"
        assert "테스트 봇" in result.agent_description


# ---------------------------------------------------------------------------
# tool_recommender.recommend_tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recommend_tools_success():
    """Mock LLM returns valid tool recommendations."""
    mock_data = [
        {"tool_name": "Web Search", "description": "웹 검색", "reason": "검색 필요"},
    ]

    with patch(
        "app.agent_runtime.builder.sub_agents.tool_recommender.invoke_with_json_retry",
        new_callable=AsyncMock,
        return_value=mock_data,
    ):
        from app.agent_runtime.builder.sub_agents.tool_recommender import recommend_tools

        intent = _make_intent()
        result = await recommend_tools(intent, TOOL_CATALOG)
        assert len(result) == 1
        assert result[0].tool_name == "Web Search"


@pytest.mark.asyncio
async def test_recommend_tools_filters_invalid():
    """Tools not in catalog are filtered out."""
    mock_data = [
        {"tool_name": "Web Search", "description": "웹 검색", "reason": "필요"},
        {"tool_name": "Nonexistent Tool", "description": "없는 도구", "reason": "필터됨"},
    ]

    with patch(
        "app.agent_runtime.builder.sub_agents.tool_recommender.invoke_with_json_retry",
        new_callable=AsyncMock,
        return_value=mock_data,
    ):
        from app.agent_runtime.builder.sub_agents.tool_recommender import recommend_tools

        intent = _make_intent()
        result = await recommend_tools(intent, TOOL_CATALOG)
        assert len(result) == 1
        assert result[0].tool_name == "Web Search"


@pytest.mark.asyncio
async def test_recommend_tools_failure():
    """When LLM fails, empty list is returned."""
    with patch(
        "app.agent_runtime.builder.sub_agents.tool_recommender.invoke_with_json_retry",
        new_callable=AsyncMock,
        side_effect=ValueError("JSON parsing failed"),
    ):
        from app.agent_runtime.builder.sub_agents.tool_recommender import recommend_tools

        intent = _make_intent()
        result = await recommend_tools(intent, TOOL_CATALOG)
        assert result == []


@pytest.mark.asyncio
async def test_recommend_tools_non_list_response():
    """When LLM returns dict instead of list, empty list is returned."""
    with patch(
        "app.agent_runtime.builder.sub_agents.tool_recommender.invoke_with_json_retry",
        new_callable=AsyncMock,
        return_value={"not": "a list"},
    ):
        from app.agent_runtime.builder.sub_agents.tool_recommender import recommend_tools

        intent = _make_intent()
        result = await recommend_tools(intent, TOOL_CATALOG)
        assert result == []


# ---------------------------------------------------------------------------
# middleware_recommender.recommend_middlewares
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recommend_middlewares_success():
    """Mock LLM returns valid middleware recommendations."""
    mock_data = [
        {"middleware_name": "tool_retry", "description": "재시도", "reason": "안정성"},
    ]

    with patch(
        "app.agent_runtime.builder.sub_agents.middleware_recommender.invoke_with_json_retry",
        new_callable=AsyncMock,
        return_value=mock_data,
    ):
        from app.agent_runtime.builder.sub_agents.middleware_recommender import (
            recommend_middlewares,
        )

        intent = _make_intent()
        tools = [ToolRecommendation(tool_name="Web Search", description="검색", reason="필요")]
        result = await recommend_middlewares(intent, tools, MW_CATALOG)
        assert len(result) == 1
        assert result[0].middleware_name == "tool_retry"


@pytest.mark.asyncio
async def test_recommend_middlewares_filters_invalid():
    """Middlewares not in catalog are filtered out."""
    mock_data = [
        {"middleware_name": "tool_retry", "description": "재시도", "reason": "필요"},
        {"middleware_name": "nonexistent_mw", "description": "없음", "reason": "필터됨"},
    ]

    with patch(
        "app.agent_runtime.builder.sub_agents.middleware_recommender.invoke_with_json_retry",
        new_callable=AsyncMock,
        return_value=mock_data,
    ):
        from app.agent_runtime.builder.sub_agents.middleware_recommender import (
            recommend_middlewares,
        )

        intent = _make_intent()
        tools = []
        result = await recommend_middlewares(intent, tools, MW_CATALOG)
        assert len(result) == 1


@pytest.mark.asyncio
async def test_recommend_middlewares_failure():
    """When LLM fails, empty list is returned."""
    with patch(
        "app.agent_runtime.builder.sub_agents.middleware_recommender.invoke_with_json_retry",
        new_callable=AsyncMock,
        side_effect=ValueError("JSON parsing failed"),
    ):
        from app.agent_runtime.builder.sub_agents.middleware_recommender import (
            recommend_middlewares,
        )

        intent = _make_intent()
        result = await recommend_middlewares(intent, [], MW_CATALOG)
        assert result == []


@pytest.mark.asyncio
async def test_recommend_middlewares_non_list_response():
    """When LLM returns dict instead of list, empty list is returned."""
    with patch(
        "app.agent_runtime.builder.sub_agents.middleware_recommender.invoke_with_json_retry",
        new_callable=AsyncMock,
        return_value={"not": "a list"},
    ):
        from app.agent_runtime.builder.sub_agents.middleware_recommender import (
            recommend_middlewares,
        )

        intent = _make_intent()
        result = await recommend_middlewares(intent, [], MW_CATALOG)
        assert result == []


# ---------------------------------------------------------------------------
# prompt_generator.generate_system_prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_system_prompt_success():
    """Mock LLM returns valid prompt text."""
    long_prompt = (
        "# Weather Bot\n\n"
        "## Role\n날씨 봇입니다.\n\n"
        "## Tool Guidelines\n### Web Search\n- Purpose: 검색\n\n"
        "## Workflow\n1. 요청 분석\n2. 도구 호출\n\n"
        "## Constraints\n- ALWAYS: 정확한 정보 제공\n" + "내용 " * 200
    )

    with patch(
        "app.agent_runtime.builder.sub_agents.prompt_generator.invoke_for_text",
        new_callable=AsyncMock,
        return_value=long_prompt,
    ):
        from app.agent_runtime.builder.sub_agents.prompt_generator import generate_system_prompt

        intent = _make_intent()
        tools = [ToolRecommendation(tool_name="Web Search", description="검색", reason="필요")]
        mws = [
            MiddlewareRecommendation(
                middleware_name="tool_retry", description="재시도", reason="안정성"
            )
        ]
        result = await generate_system_prompt(intent, tools, mws)
        assert "Weather Bot" in result


@pytest.mark.asyncio
async def test_generate_system_prompt_fallback():
    """When LLM returns None, fallback prompt is generated."""
    with patch(
        "app.agent_runtime.builder.sub_agents.prompt_generator.invoke_for_text",
        new_callable=AsyncMock,
        return_value=None,
    ):
        from app.agent_runtime.builder.sub_agents.prompt_generator import generate_system_prompt

        intent = _make_intent()
        tools = [ToolRecommendation(tool_name="Web Search", description="검색", reason="필요")]
        result = await generate_system_prompt(intent, tools, [])
        assert "Weather Bot" in result
        assert "## Role" in result
        assert "Web Search" in result


@pytest.mark.asyncio
async def test_generate_system_prompt_no_tools_fallback():
    """Fallback prompt with no tools."""
    with patch(
        "app.agent_runtime.builder.sub_agents.prompt_generator.invoke_for_text",
        new_callable=AsyncMock,
        return_value=None,
    ):
        from app.agent_runtime.builder.sub_agents.prompt_generator import generate_system_prompt

        intent = _make_intent()
        result = await generate_system_prompt(intent, [], [])
        assert "사용 가능한 도구가 없습니다" in result


# ---------------------------------------------------------------------------
# helpers: invoke_for_text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_for_text_retry_on_short():
    """invoke_for_text retries when response is too short."""
    short_response = MagicMock()
    short_response.content = "short"

    long_response = MagicMock()
    long_response.content = "A" * 600

    mock_model = AsyncMock()
    mock_model.ainvoke = AsyncMock(side_effect=[short_response, long_response])

    with (
        patch(
            "app.agent_runtime.builder.sub_agents.helpers._get_builder_model",
            return_value=mock_model,
        ),
        patch(
            "app.agent_runtime.builder.sub_agents.helpers._get_fallback_model",
            return_value=None,
        ),
    ):
        from app.agent_runtime.builder.sub_agents.helpers import invoke_for_text

        result = await invoke_for_text("system", "task", min_length=500)
        assert result == "A" * 600
        assert mock_model.ainvoke.call_count == 2


@pytest.mark.asyncio
async def test_invoke_for_text_all_short():
    """invoke_for_text returns last attempt even if short when retries exhausted."""
    short_response = MagicMock()
    short_response.content = "short"

    mock_model = AsyncMock()
    mock_model.ainvoke = AsyncMock(return_value=short_response)

    with (
        patch(
            "app.agent_runtime.builder.sub_agents.helpers._get_builder_model",
            return_value=mock_model,
        ),
        patch(
            "app.agent_runtime.builder.sub_agents.helpers._get_fallback_model",
            return_value=None,
        ),
    ):
        from app.agent_runtime.builder.sub_agents.helpers import invoke_for_text

        result = await invoke_for_text("system", "task", min_length=500, max_retries=2)
        # Last attempt returns the short text
        assert result == "short"


@pytest.mark.asyncio
async def test_invoke_with_json_retry_ignores_trailing_text():
    """JSON 뒤에 설명 텍스트가 붙어도(LiteLLM gateway류) 첫 JSON을 파싱한다.

    기존 json.loads는 "Extra data"로 실패 → fallback. raw_decode로 trailing
    텍스트를 무시하므로 1회 시도에 성공한다.
    """
    response = MagicMock()
    response.content = '{"options": ["a", "b"]}\n\n위 옵션을 추천합니다.'

    mock_model = AsyncMock()
    mock_model.ainvoke = AsyncMock(return_value=response)

    with (
        patch(
            "app.agent_runtime.builder.sub_agents.helpers._get_builder_model",
            return_value=mock_model,
        ),
        patch(
            "app.agent_runtime.builder.sub_agents.helpers._get_fallback_model",
            return_value=None,
        ),
    ):
        from app.agent_runtime.builder.sub_agents.helpers import (
            invoke_with_json_retry,
        )

        result = await invoke_with_json_retry("system", "task", max_retries=1)
        assert result == {"options": ["a", "b"]}
        assert mock_model.ainvoke.call_count == 1


# ---------------------------------------------------------------------------
# tool_recommender helper: _format_catalog, _build_task_description
# ---------------------------------------------------------------------------


def test_format_catalog_empty():
    from app.agent_runtime.builder.sub_agents.tool_recommender import _format_catalog

    assert "사용 가능한 항목이 없습니다" in _format_catalog([])


def test_format_catalog_items():
    from app.agent_runtime.builder.sub_agents.tool_recommender import _format_catalog

    result = _format_catalog(TOOL_CATALOG)
    assert "Web Search" in result
    assert "Web Scraper" in result


def test_format_catalog_includes_kind_prefix():
    """카탈로그 줄에 [kind] prefix 가 노출되어야 LLM 이 종류 인지 — skill 포함."""
    from app.agent_runtime.builder.sub_agents.tool_recommender import _format_catalog

    catalog = [
        {"name": "Web Search", "kind": "tool", "description": "웹 검색"},
        {"name": "list_departments", "kind": "mcp", "description": "부서 목록"},
        {"name": "seat_layout", "kind": "skill", "description": "좌석 가이드"},
    ]
    result = _format_catalog(catalog)
    assert "[tool] Web Search" in result
    assert "[mcp] list_departments" in result
    assert "[skill] seat_layout" in result


@pytest.mark.asyncio
async def test_recommend_tools_skill_kind_canonicalized():
    """LLM 이 kind 를 누락/잘못 답해도 카탈로그 정답으로 정정 — skill 매칭 회귀 가드."""
    from unittest.mock import AsyncMock, patch

    catalog = [
        {"name": "seat_layout_guide", "kind": "skill", "description": "좌석 가이드"},
    ]
    # LLM 이 kind 를 일부러 잘못 답함 — 시스템이 skill 로 정정해야 함
    mock_data = [
        {
            "tool_name": "seat_layout_guide",
            "kind": "tool",
            "description": "좌석",
            "reason": "위치 안내",
        },
    ]
    with patch(
        "app.agent_runtime.builder.sub_agents.tool_recommender.invoke_with_json_retry",
        new_callable=AsyncMock,
        return_value=mock_data,
    ):
        from app.agent_runtime.builder.sub_agents.tool_recommender import recommend_tools

        intent = _make_intent()
        result = await recommend_tools(intent, catalog)
        assert len(result) == 1
        assert result[0].tool_name == "seat_layout_guide"
        assert result[0].kind == "skill"  # 카탈로그 정답으로 정정


def test_build_task_description_includes_revision_section():
    """Revision 메시지가 별도 '절대 우선' 섹션으로 task description 에 포함."""
    from app.agent_runtime.builder.sub_agents.tool_recommender import (
        _build_task_description,
    )

    intent = _make_intent()
    catalog = [{"name": "seating-guide", "kind": "skill", "description": "좌석"}]
    previous = [
        {"tool_name": "search_employees", "kind": "mcp", "reason": "직원 검색"},
        {"tool_name": "list_departments", "kind": "mcp", "reason": "부서 목록"},
        {"tool_name": "seating-guide", "kind": "skill", "reason": "좌석"},
    ]
    description = _build_task_description(
        intent,
        catalog,
        previous_recommendations=previous,
        revision_message="seating-guide 이것만 있으면 될 것 같아",
    )
    assert "직전 추천 (수정 대상)" in description
    assert "search_employees" in description
    assert "사용자 수정 요청 (절대 우선)" in description
    assert "seating-guide 이것만 있으면 될 것 같아" in description
    # 한정 표현 가이드가 LLM 입력에 포함되는지
    assert "이것만" in description


def test_build_task_description_skips_revision_when_absent():
    """Revision 없으면 직전/수정 섹션 모두 미포함 (1차 추천 호출)."""
    from app.agent_runtime.builder.sub_agents.tool_recommender import (
        _build_task_description,
    )

    intent = _make_intent()
    description = _build_task_description(intent, [{"name": "x", "kind": "tool"}])
    assert "직전 추천" not in description
    assert "사용자 수정 요청" not in description


# ---------------------------------------------------------------------------
# middleware_recommender helper: _format_catalog
# ---------------------------------------------------------------------------


def test_mw_format_catalog_empty():
    from app.agent_runtime.builder.sub_agents.middleware_recommender import _format_catalog

    assert "사용 가능한 미들웨어가 없습니다" in _format_catalog([])


def test_mw_format_catalog_with_provider():
    from app.agent_runtime.builder.sub_agents.middleware_recommender import _format_catalog

    catalog = [
        {
            "type": "anthropic_prompt_caching",
            "name": "AnthropicPromptCachingMiddleware",
            "description": "캐싱",
            "category": "provider",
            "provider_specific": "anthropic",
        },
    ]
    result = _format_catalog(catalog)
    assert "anthropic" in result


# ---------------------------------------------------------------------------
# prompt_generator helpers: _format_tools, _format_middlewares
# ---------------------------------------------------------------------------


def test_format_tools_empty():
    from app.agent_runtime.builder.sub_agents.prompt_generator import _format_tools

    assert "추천된 도구 없음" in _format_tools([])


def test_format_tools_items():
    from app.agent_runtime.builder.sub_agents.prompt_generator import _format_tools

    tools = [ToolRecommendation(tool_name="Web Search", description="검색", reason="필요")]
    result = _format_tools(tools)
    assert "Web Search" in result


def test_format_middlewares_empty():
    from app.agent_runtime.builder.sub_agents.prompt_generator import _format_middlewares

    assert "추천된 미들웨어 없음" in _format_middlewares([])


def test_format_middlewares_items():
    from app.agent_runtime.builder.sub_agents.prompt_generator import _format_middlewares

    mws = [
        MiddlewareRecommendation(
            middleware_name="tool_retry", description="재시도", reason="안정성"
        )
    ]
    result = _format_middlewares(mws)
    assert "tool_retry" in result
