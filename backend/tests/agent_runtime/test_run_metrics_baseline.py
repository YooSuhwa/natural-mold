from langchain_core.messages import AIMessage, HumanMessage

from app.agent_runtime.run_metrics_baseline import baseline_message_identities


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
