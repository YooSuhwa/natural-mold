"""Tests for app.agent_runtime.assistant.assistant_agent — build + prompt loading."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

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


@pytest.mark.asyncio
async def test_build_assistant_agent():
    """build_assistant_agent wires model/tools/prompt/checkpointer into build_agent."""
    import uuid
    from unittest.mock import AsyncMock

    import app.agent_runtime.assistant.assistant_agent as mod

    mod._load_system_prompt.cache_clear()

    mock_model = MagicMock()
    mock_read_tools = [MagicMock()]
    mock_write_tools = [MagicMock()]
    mock_clarify_tools = [MagicMock()]
    mock_checkpointer = MagicMock()
    mock_compiled_graph = MagicMock()

    from app.services.system_credential_resolver import ResolvedSystemModel

    resolved = ResolvedSystemModel(
        provider="anthropic",
        model_name="claude-sonnet-4-6",
        api_key="sk-test",
        base_url=None,
    )
    with (
        patch.object(mod, "_load_system_prompt", return_value="Test prompt"),
        patch.object(
            mod, "resolve_system_model", AsyncMock(return_value=resolved)
        ),
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

        result = await mod.build_assistant_agent(db, agent_id, user_id, thread_id)

    assert result is mock_compiled_graph
    mock_build.assert_called_once()
    call_kwargs = mock_build.call_args
    assert call_kwargs.kwargs["model"] is mock_model
    assert call_kwargs.kwargs["system_prompt"] == "Test prompt"
    assert call_kwargs.kwargs["checkpointer"] is mock_checkpointer
    assert len(call_kwargs.kwargs["tools"]) == 3

    mod._load_system_prompt.cache_clear()


# ---------------------------------------------------------------------------
# resolve_system_api_key — ENV → system credential → None
# (Lives in app.services.system_credential_resolver; patch that module.)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_system_api_key_prefers_env():
    """ENV-supplied key wins; no DB call needed."""
    from unittest.mock import AsyncMock

    import app.services.system_credential_resolver as resolver

    db = AsyncMock()
    with patch.object(resolver, "PROVIDER_API_KEY_MAP", {"anthropic": "sk-env"}):
        key = await resolver.resolve_system_api_key(db, "anthropic")
    assert key == "sk-env"


@pytest.mark.asyncio
async def test_resolve_system_api_key_falls_back_to_system_credential():
    """ENV missing → DB system credential is decrypted and returned."""
    from unittest.mock import AsyncMock

    import app.services.system_credential_resolver as resolver

    fake_cred = MagicMock(id="cred-1", data_encrypted="blob")
    with (
        patch.object(resolver, "PROVIDER_API_KEY_MAP", {}),
        patch.object(
            resolver.credential_service,
            "find_system_by_definition",
            AsyncMock(return_value=fake_cred),
        ),
        patch.object(
            resolver.credential_service,
            "decrypt_with_external",
            AsyncMock(return_value={"api_key": "sk-system"}),
        ),
    ):
        key = await resolver.resolve_system_api_key(AsyncMock(), "anthropic")
    assert key == "sk-system"
