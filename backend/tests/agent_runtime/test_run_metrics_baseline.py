from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent_runtime.run_metrics_baseline import (
    baseline_completed_tool_call_source_identities,
    baseline_message_identities,
)


def test_baseline_extracts_only_stable_assistant_ids_without_content() -> None:
    result = baseline_message_identities(
        [
            HumanMessage(content="historical user secret", id="human-before"),
            AIMessage(content="historical assistant secret", id="assistant-before"),
            AIMessage(content="missing id"),
        ]
    )

    assert result == frozenset({((), "assistant-before")})
    assert "secret" not in repr(result)


def test_completed_tool_baseline_requires_stable_source_and_result() -> None:
    result = baseline_completed_tool_call_source_identities(
        [
            AIMessage(
                content="",
                id="assistant-completed",
                tool_calls=[{"id": "call-completed", "name": "search", "args": {}}],
            ),
            ToolMessage(content="done", tool_call_id="call-completed"),
            AIMessage(
                content="",
                id="assistant-pending",
                tool_calls=[{"id": "call-completed", "name": "task", "args": {}}],
            ),
        ]
    )

    assert result == frozenset({((), "assistant-completed", "call-completed")})
