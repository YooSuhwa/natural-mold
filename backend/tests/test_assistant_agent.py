"""Tests for app.agent_runtime.assistant.assistant_agent — build + prompt loading."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# _load_system_prompt — file exists
# ---------------------------------------------------------------------------


def test_load_system_prompt_from_file(tmp_path):
    """_load_system_prompt reads file and returns content."""
    import app.agent_runtime.assistant.assistant_agent as mod

    # Clear the lru_cache before testing
    mod._load_system_prompt.cache_clear()

    prompt_file = tmp_path / "test_prompt.md"
    prompt_file.write_text("You are a test assistant.", encoding="utf-8")

    with patch.object(mod, "_PROMPT_PATH", prompt_file):
        result = mod._load_system_prompt()

    assert result == "You are a test assistant."
    mod._load_system_prompt.cache_clear()


# ---------------------------------------------------------------------------
# _load_system_prompt — file not found → fallback
# ---------------------------------------------------------------------------


def test_load_system_prompt_fallback(tmp_path):
    """When prompt file doesn't exist, returns fallback string."""
    import app.agent_runtime.assistant.assistant_agent as mod

    mod._load_system_prompt.cache_clear()

    nonexistent = tmp_path / "nonexistent.md"

    with patch.object(mod, "_PROMPT_PATH", nonexistent):
        result = mod._load_system_prompt()

    assert "Moldy Agent Assistant" in result
    assert "VERIFY" in result
    mod._load_system_prompt.cache_clear()


# ---------------------------------------------------------------------------
# build_assistant_agent — integrates model, tools, prompt, checkpointer
# ---------------------------------------------------------------------------


def test_build_assistant_agent():
    """build_assistant_agent calls build_agent with model, tools, prompt, checkpointer."""
    import uuid
    from unittest.mock import AsyncMock

    import app.agent_runtime.assistant.assistant_agent as mod

    mod._load_system_prompt.cache_clear()
    mod._get_assistant_model.cache_clear()

    mock_model = MagicMock()
    mock_read_tools = [MagicMock()]
    mock_write_tools = [MagicMock()]
    mock_clarify_tools = [MagicMock()]
    mock_checkpointer = MagicMock()
    mock_compiled_graph = MagicMock()

    with (
        patch.object(mod, "_load_system_prompt", return_value="Test prompt"),
        patch.object(mod, "create_chat_model", return_value=mock_model),
        patch.object(mod, "build_read_tools", return_value=mock_read_tools),
        patch.object(mod, "build_write_tools", return_value=mock_write_tools),
        patch.object(mod, "build_clarify_tools", return_value=mock_clarify_tools),
        patch.object(mod, "get_checkpointer", return_value=mock_checkpointer),
        patch.object(mod, "build_agent", return_value=mock_compiled_graph) as mock_build,
    ):
        db = AsyncMock()
        agent_id = uuid.uuid4()
        user_id = uuid.uuid4()
        thread_id = f"assistant_{agent_id}"

        result = mod.build_assistant_agent(db, agent_id, user_id, thread_id)

    assert result is mock_compiled_graph
    mock_build.assert_called_once()
    call_kwargs = mock_build.call_args
    assert call_kwargs.kwargs["model"] is mock_model
    assert call_kwargs.kwargs["system_prompt"] == "Test prompt"
    assert call_kwargs.kwargs["checkpointer"] is mock_checkpointer
    assert len(call_kwargs.kwargs["tools"]) == 3  # read + write + clarify

    mod._load_system_prompt.cache_clear()
    mod._get_assistant_model.cache_clear()


# ---------------------------------------------------------------------------
# _get_assistant_model — uses settings
# ---------------------------------------------------------------------------


def test_get_assistant_model():
    """_get_assistant_model creates model from settings."""
    import app.agent_runtime.assistant.assistant_agent as mod

    mod._get_assistant_model.cache_clear()

    mock_model = MagicMock()

    with (
        patch.object(mod, "create_chat_model", return_value=mock_model) as mock_create,
        patch.object(mod, "settings") as mock_settings,
        patch.object(mod, "PROVIDER_API_KEY_MAP", {"anthropic": "sk-ant-test"}),
    ):
        mock_settings.assistant_model_provider = "anthropic"
        mock_settings.assistant_model_name = "claude-sonnet-4-20250514"
        result = mod._get_assistant_model()

    assert result is mock_model
    mock_create.assert_called_once_with(
        "anthropic", "claude-sonnet-4-20250514", api_key="sk-ant-test"
    )
    mod._get_assistant_model.cache_clear()
