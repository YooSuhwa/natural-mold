"""Tests for app.agent_runtime.builder.orchestrator — graph structure + phase logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_runtime.builder.orchestrator import (
    BuilderState,
    build_builder_graph,
    phase1_init,
    phase6_config,
    phase7_preview,
)
from app.agent_runtime.builder.sub_agents.helpers import (
    invoke_with_json_retry,
    strip_code_fences,
)

# ---------------------------------------------------------------------------
# Helper — default BuilderState factory
# ---------------------------------------------------------------------------


def _make_state(**overrides: object) -> BuilderState:
    """Return a BuilderState dict with sensible defaults, overridden by kwargs."""
    defaults: BuilderState = {
        "user_id": "user-1",
        "user_request": "테스트",
        "session_id": "sess-1",
        "project_path": "",
        "intent": None,
        "tools": [],
        "middlewares": [],
        "system_prompt": "",
        "draft_config": None,
        "agent_id": "",
        "current_phase": 0,
        "error": "",
        "available_tools_catalog": [],
        "available_middlewares_catalog": [],
        "default_model_name": "",
        "sse_events": [],
    }
    return {**defaults, **overrides}  # type: ignore[typeddict-item]


# ---------------------------------------------------------------------------
# phase1_init — project_path generation (no LLM)
# ---------------------------------------------------------------------------


def test_phase1_init():
    state = _make_state(
        user_id="user-123",
        user_request="날씨 봇 만들어줘",
        session_id="abcd1234-0000-0000-0000-000000000000",
    )
    result = phase1_init(state)
    assert result["current_phase"] == 1
    assert "user-123" in result["project_path"]
    assert "abcd1234" in result["project_path"]
    assert len(result["sse_events"]) == 1
    assert result["sse_events"][0]["phase"] == 1
    assert result["sse_events"][0]["status"] == "completed"


# ---------------------------------------------------------------------------
# phase6_config — draft config assembly (no LLM)
# ---------------------------------------------------------------------------


def test_phase6_config():
    state = _make_state(
        user_id="user-123",
        user_request="뉴스 요약 봇",
        project_path="/tmp/test",
        intent={
            "agent_name": "News Summarizer",
            "agent_name_ko": "뉴스 요약기",
            "agent_description": "뉴스를 요약하는 에이전트",
            "primary_task_type": "뉴스 요약",
            "use_cases": ["뉴스 요약", "트렌드 파악"],
        },
        tools=[
            {"tool_name": "Web Search", "description": "웹 검색", "reason": "검색 필요"},
        ],
        middlewares=[
            {"middleware_name": "summarization", "description": "요약", "reason": "요약 필요"},
        ],
        system_prompt="You are a news summarizer.",
        current_phase=5,
        default_model_name="openai:gpt-4o",
    )
    result = phase6_config(state)
    assert result["current_phase"] == 6

    draft = result["draft_config"]
    assert draft["name"] == "News Summarizer"
    assert draft["name_ko"] == "뉴스 요약기"
    assert draft["system_prompt"] == "You are a news summarizer."
    assert "Web Search" in draft["tools"]
    assert "summarization" in draft["middlewares"]
    assert draft["model_name"] == "openai:gpt-4o"


# ---------------------------------------------------------------------------
# phase7_preview — preview SSE events (no LLM)
# ---------------------------------------------------------------------------


def test_phase7_preview():
    state = _make_state(
        user_id="user-123",
        project_path="/tmp/test",
        intent={},
        draft_config={"name": "Test"},
        current_phase=6,
    )
    result = phase7_preview(state)
    assert result["current_phase"] == 7
    assert len(result["sse_events"]) == 1
    assert result["sse_events"][0]["status"] == "completed"
    assert "준비됐어요" in result["sse_events"][0]["message"]


# ---------------------------------------------------------------------------
# strip_code_fences — helper
# ---------------------------------------------------------------------------


def test_strip_code_fences_json():
    text = '```json\n{"key": "value"}\n```'
    assert strip_code_fences(text) == '{"key": "value"}'


def test_strip_code_fences_no_fence():
    text = '{"key": "value"}'
    assert strip_code_fences(text) == '{"key": "value"}'


def test_strip_code_fences_plain():
    text = "```\nhello\n```"
    assert strip_code_fences(text) == "hello"


# ---------------------------------------------------------------------------
# invoke_with_json_retry — mock LLM
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_with_json_retry_success():
    """First attempt returns valid JSON."""
    mock_response = MagicMock()
    mock_response.content = '{"result": "ok"}'

    mock_model = AsyncMock()
    mock_model.ainvoke = AsyncMock(return_value=mock_response)

    with patch(
        "app.agent_runtime.builder.sub_agents.helpers.create_chat_model",
        return_value=mock_model,
    ):
        result = await invoke_with_json_retry("system", "task")
        assert result == {"result": "ok"}
        assert mock_model.ainvoke.call_count == 1


@pytest.mark.asyncio
async def test_invoke_with_json_retry_retry_then_success():
    """First attempt returns invalid JSON, second returns valid."""
    bad_response = MagicMock()
    bad_response.content = "This is not JSON"

    good_response = MagicMock()
    good_response.content = '{"result": "ok"}'

    mock_model = AsyncMock()
    mock_model.ainvoke = AsyncMock(side_effect=[bad_response, good_response])

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
        result = await invoke_with_json_retry("system", "task")
        assert result == {"result": "ok"}
        assert mock_model.ainvoke.call_count == 2


@pytest.mark.asyncio
async def test_invoke_with_json_retry_all_fail():
    """All attempts fail to parse JSON."""
    bad_response = MagicMock()
    bad_response.content = "not json"

    mock_model = AsyncMock()
    mock_model.ainvoke = AsyncMock(return_value=bad_response)

    with (
        patch(
            "app.agent_runtime.builder.sub_agents.helpers._get_builder_model",
            return_value=mock_model,
        ),
        patch(
            "app.agent_runtime.builder.sub_agents.helpers._get_fallback_model",
            return_value=None,
        ),
        pytest.raises(ValueError, match="JSON parsing failed"),
    ):
        await invoke_with_json_retry("system", "task", max_retries=2)


# ---------------------------------------------------------------------------
# Graph structure — nodes and edges
# ---------------------------------------------------------------------------


def test_graph_structure():
    """Verify the compiled graph has expected nodes and edges."""
    graph = build_builder_graph()

    # Check that all expected nodes exist in the graph
    node_names = set(graph.nodes.keys())
    expected_nodes = {"phase1", "phase2", "phase3", "phase4", "phase5", "phase6", "phase7"}
    # LangGraph may add __start__ and __end__ nodes
    assert expected_nodes.issubset(node_names), f"Missing nodes: {expected_nodes - node_names}"


# ---------------------------------------------------------------------------
# phase2_intent — mock analyze_intent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase2_intent_success():
    """phase2 calls analyze_intent and returns intent data."""
    from app.agent_runtime.builder.orchestrator import phase2_intent

    mock_intent = MagicMock()
    mock_intent.agent_name_ko = "날씨 봇"
    mock_intent.model_dump.return_value = {
        "agent_name": "Weather Bot",
        "agent_name_ko": "날씨 봇",
        "agent_description": "날씨 정보",
        "primary_task_type": "날씨 검색",
        "use_cases": ["날씨 검색"],
    }

    state = _make_state(
        user_request="날씨 봇 만들어줘",
        current_phase=1,
    )

    with patch(
        "app.agent_runtime.builder.orchestrator.analyze_intent",
        new_callable=AsyncMock,
        return_value=mock_intent,
    ):
        result = await phase2_intent(state)
        assert result["current_phase"] == 2
        assert result["intent"]["agent_name"] == "Weather Bot"
        assert len(result["sse_events"]) >= 2  # started + sub_agent + completed


@pytest.mark.asyncio
async def test_phase2_intent_failure():
    """phase2 returns error when analyze_intent raises."""
    from app.agent_runtime.builder.orchestrator import phase2_intent

    state = _make_state(current_phase=1)

    with patch(
        "app.agent_runtime.builder.orchestrator.analyze_intent",
        new_callable=AsyncMock,
        side_effect=RuntimeError("LLM connection failed"),
    ):
        result = await phase2_intent(state)
        assert "error" in result
        assert "Phase 2 failed" in result["error"]


# ---------------------------------------------------------------------------
# phase3_tools — mock recommend_tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase3_tools_success():
    """phase3 calls recommend_tools and returns recommendations."""
    from app.agent_runtime.builder.orchestrator import phase3_tools

    mock_tools = [
        MagicMock(
            tool_name="Web Search",
            model_dump=MagicMock(
                return_value={
                    "tool_name": "Web Search",
                    "description": "검색",
                    "reason": "필요",
                }
            ),
        ),
    ]

    state = _make_state(
        user_request="검색 봇",
        intent={
            "agent_name": "Search Bot",
            "agent_name_ko": "검색 봇",
            "agent_description": "검색",
            "primary_task_type": "검색",
            "use_cases": ["검색"],
        },
        current_phase=2,
    )

    with patch(
        "app.agent_runtime.builder.orchestrator.recommend_tools",
        new_callable=AsyncMock,
        return_value=mock_tools,
    ):
        result = await phase3_tools(state)
        assert result["current_phase"] == 3
        assert len(result["tools"]) == 1


@pytest.mark.asyncio
async def test_phase3_tools_skip_on_error():
    """phase3 skips if there's an existing error."""
    from app.agent_runtime.builder.orchestrator import phase3_tools

    state = _make_state(
        current_phase=2,
        error="Phase 2 failed",
    )

    result = await phase3_tools(state)
    assert result["current_phase"] == 3
    assert result["sse_events"] == []


@pytest.mark.asyncio
async def test_phase3_tools_failure_recoverable():
    """phase3 returns empty tools on failure (recoverable)."""
    from app.agent_runtime.builder.orchestrator import phase3_tools

    state = _make_state(
        intent={
            "agent_name": "Test",
            "agent_name_ko": "테스트",
            "agent_description": "테스트",
            "primary_task_type": "테스트",
            "use_cases": ["테스트"],
        },
        current_phase=2,
    )

    with patch(
        "app.agent_runtime.builder.orchestrator.recommend_tools",
        new_callable=AsyncMock,
        side_effect=RuntimeError("LLM failed"),
    ):
        result = await phase3_tools(state)
        assert result["tools"] == []
        # Should have warning event, not hard error
        has_warning = any(e.get("status") == "warning" for e in result["sse_events"])
        assert has_warning


# ---------------------------------------------------------------------------
# phase4_middlewares — mock recommend_middlewares
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase4_middlewares_success():
    """phase4 calls recommend_middlewares and returns recommendations."""
    from app.agent_runtime.builder.orchestrator import phase4_middlewares

    mock_mws = [
        MagicMock(
            middleware_name="tool_retry",
            model_dump=MagicMock(
                return_value={
                    "middleware_name": "tool_retry",
                    "description": "재시도",
                    "reason": "안정성",
                }
            ),
        ),
    ]

    state = _make_state(
        intent={
            "agent_name": "Test",
            "agent_name_ko": "테스트",
            "agent_description": "테스트",
            "primary_task_type": "테스트",
            "use_cases": ["테스트"],
        },
        tools=[{"tool_name": "Web Search", "description": "검색", "reason": "필요"}],
        current_phase=3,
    )

    with patch(
        "app.agent_runtime.builder.orchestrator.recommend_middlewares",
        new_callable=AsyncMock,
        return_value=mock_mws,
    ):
        result = await phase4_middlewares(state)
        assert result["current_phase"] == 4
        assert len(result["middlewares"]) == 1


@pytest.mark.asyncio
async def test_phase4_middlewares_skip_on_error():
    """phase4 skips if there's an existing error."""
    from app.agent_runtime.builder.orchestrator import phase4_middlewares

    state = _make_state(current_phase=3, error="some error")
    result = await phase4_middlewares(state)
    assert result["sse_events"] == []


@pytest.mark.asyncio
async def test_phase4_middlewares_failure_recoverable():
    """phase4 returns empty middlewares on failure (recoverable)."""
    from app.agent_runtime.builder.orchestrator import phase4_middlewares

    state = _make_state(
        intent={
            "agent_name": "Test",
            "agent_name_ko": "테스트",
            "agent_description": "테스트",
            "primary_task_type": "테스트",
            "use_cases": ["테스트"],
        },
        current_phase=3,
    )

    with patch(
        "app.agent_runtime.builder.orchestrator.recommend_middlewares",
        new_callable=AsyncMock,
        side_effect=RuntimeError("LLM failed"),
    ):
        result = await phase4_middlewares(state)
        assert result["middlewares"] == []


# ---------------------------------------------------------------------------
# phase5_prompt — mock generate_system_prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase5_prompt_success():
    """phase5 calls generate_system_prompt and returns it."""
    from app.agent_runtime.builder.orchestrator import phase5_prompt

    state = _make_state(
        intent={
            "agent_name": "Test",
            "agent_name_ko": "테스트",
            "agent_description": "테스트",
            "primary_task_type": "테스트",
            "use_cases": ["테스트"],
        },
        current_phase=4,
    )

    with patch(
        "app.agent_runtime.builder.orchestrator.generate_system_prompt",
        new_callable=AsyncMock,
        return_value="# Test Agent\n\nYou are a test agent.",
    ):
        result = await phase5_prompt(state)
        assert result["current_phase"] == 5
        assert "Test Agent" in result["system_prompt"]


@pytest.mark.asyncio
async def test_phase5_prompt_skip_on_error():
    """phase5 skips if there's an existing error."""
    from app.agent_runtime.builder.orchestrator import phase5_prompt

    state = _make_state(current_phase=4, error="some error")
    result = await phase5_prompt(state)
    assert result["sse_events"] == []


@pytest.mark.asyncio
async def test_phase5_prompt_failure():
    """phase5 returns error on failure (non-recoverable)."""
    from app.agent_runtime.builder.orchestrator import phase5_prompt

    state = _make_state(
        intent={
            "agent_name": "Test",
            "agent_name_ko": "테스트",
            "agent_description": "테스트",
            "primary_task_type": "테스트",
            "use_cases": ["테스트"],
        },
        current_phase=4,
    )

    with patch(
        "app.agent_runtime.builder.orchestrator.generate_system_prompt",
        new_callable=AsyncMock,
        side_effect=RuntimeError("LLM failed"),
    ):
        result = await phase5_prompt(state)
        assert "error" in result
        assert "Phase 5 failed" in result["error"]


# ---------------------------------------------------------------------------
# phase6_config — error skip
# ---------------------------------------------------------------------------


def test_phase6_config_skip_on_error():
    """phase6 returns empty events on error."""
    state = _make_state(current_phase=5, error="some error")
    result = phase6_config(state)
    assert result["sse_events"] == []


# ---------------------------------------------------------------------------
# phase7_preview — error skip
# ---------------------------------------------------------------------------


def test_phase7_preview_skip_on_error():
    """phase7 returns empty events on error."""
    state = _make_state(current_phase=6, error="some error")
    result = phase7_preview(state)
    assert result["sse_events"] == []


# ---------------------------------------------------------------------------
# run_builder_pipeline — integration with all phases mocked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_builder_pipeline():
    """Full pipeline run with all sub-agents mocked."""
    from app.agent_runtime.builder.orchestrator import run_builder_pipeline

    mock_intent = MagicMock()
    mock_intent.agent_name_ko = "테스트 봇"
    mock_intent.model_dump.return_value = {
        "agent_name": "Test Bot",
        "agent_name_ko": "테스트 봇",
        "agent_description": "테스트",
        "primary_task_type": "테스트",
        "use_cases": ["테스트"],
    }

    mock_tools = [
        MagicMock(
            tool_name="Web Search",
            model_dump=MagicMock(
                return_value={
                    "tool_name": "Web Search",
                    "description": "검색",
                    "reason": "필요",
                }
            ),
        ),
    ]

    mock_mws = [
        MagicMock(
            middleware_name="tool_retry",
            model_dump=MagicMock(
                return_value={
                    "middleware_name": "tool_retry",
                    "description": "재시도",
                    "reason": "안정성",
                }
            ),
        ),
    ]

    with (
        patch(
            "app.agent_runtime.builder.orchestrator.analyze_intent",
            new_callable=AsyncMock,
            return_value=mock_intent,
        ),
        patch(
            "app.agent_runtime.builder.orchestrator.recommend_tools",
            new_callable=AsyncMock,
            return_value=mock_tools,
        ),
        patch(
            "app.agent_runtime.builder.orchestrator.recommend_middlewares",
            new_callable=AsyncMock,
            return_value=mock_mws,
        ),
        patch(
            "app.agent_runtime.builder.orchestrator.generate_system_prompt",
            new_callable=AsyncMock,
            return_value="# Test Bot\nYou are a test bot.",
        ),
    ):
        updates = []
        async for update in run_builder_pipeline(
            user_id="user-1",
            user_request="테스트 봇 만들어줘",
            session_id="test-session",
            tools_catalog=[],
            middlewares_catalog=[],
            default_model_name="openai:gpt-4o",
        ):
            updates.append(update)

        # Should have updates for all 7 phases
        phases = [u["phase"] for u in updates]
        assert 1 in phases
        assert 7 in phases

        # Final phase should have draft_config in state_update
        last = updates[-1]
        assert last["phase"] == 7
