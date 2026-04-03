"""Tests for app.agent_runtime.model_factory — LLM provider instantiation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestCreateChatModel:
    """Tests for create_chat_model()."""

    def test_openai_provider(self):
        mock_cls = MagicMock()
        with (
            patch.dict(
                "app.agent_runtime.model_factory.PROVIDER_MAP",
                {"openai": mock_cls},
            ),
        ):
            from app.agent_runtime.model_factory import create_chat_model

            result = create_chat_model("openai", "gpt-4o", api_key="sk-test")

        mock_cls.assert_called_once()
        kwargs = mock_cls.call_args[1]
        assert kwargs["model"] == "gpt-4o"
        assert kwargs["api_key"] == "sk-test"
        assert result is mock_cls.return_value

    def test_anthropic_provider(self):
        mock_cls = MagicMock()
        with (
            patch.dict(
                "app.agent_runtime.model_factory.PROVIDER_MAP",
                {"anthropic": mock_cls},
            ),
        ):
            from app.agent_runtime.model_factory import create_chat_model

            result = create_chat_model("anthropic", "claude-3-opus", api_key="sk-ant-test")

        mock_cls.assert_called_once()
        kwargs = mock_cls.call_args[1]
        assert kwargs["model"] == "claude-3-opus"
        assert kwargs["api_key"] == "sk-ant-test"
        assert result is mock_cls.return_value

    def test_google_provider(self):
        mock_cls = MagicMock()
        with (
            patch.dict(
                "app.agent_runtime.model_factory.PROVIDER_MAP",
                {"google": mock_cls},
            ),
        ):
            from app.agent_runtime.model_factory import create_chat_model

            result = create_chat_model("google", "gemini-pro", api_key="goog-key")

        mock_cls.assert_called_once()
        kwargs = mock_cls.call_args[1]
        assert kwargs["model"] == "gemini-pro"
        assert kwargs["api_key"] == "goog-key"
        assert result is mock_cls.return_value

    def test_unknown_provider_defaults_to_openai_class(self):
        """Unknown provider should fall back to ChatOpenAI (the default in .get())."""
        from app.agent_runtime.model_factory import PROVIDER_MAP

        # "ollama" is not in PROVIDER_MAP, so it falls through to the default
        assert "ollama" not in PROVIDER_MAP

    def test_unknown_provider_uses_default(self):
        """Unknown provider dispatches to the ChatOpenAI default."""
        mock_cls = MagicMock()
        with (
            patch("app.agent_runtime.model_factory.ChatOpenAI", mock_cls),
            patch.dict(
                "app.agent_runtime.model_factory.PROVIDER_MAP",
                clear=True,  # empty map so "ollama" misses
            ),
        ):
            from app.agent_runtime.model_factory import create_chat_model

            result = create_chat_model("ollama", "llama3", api_key="test-key")

        mock_cls.assert_called_once()
        kwargs = mock_cls.call_args[1]
        assert kwargs["model"] == "llama3"
        assert result is mock_cls.return_value

    def test_with_base_url(self):
        mock_cls = MagicMock()
        with patch.dict(
            "app.agent_runtime.model_factory.PROVIDER_MAP",
            {"openai": mock_cls},
        ):
            from app.agent_runtime.model_factory import create_chat_model

            create_chat_model("openai", "gpt-4o", api_key="k", base_url="http://localhost:11434")

        kwargs = mock_cls.call_args[1]
        assert kwargs["base_url"] == "http://localhost:11434"

    def test_no_base_url_omits_key(self):
        mock_cls = MagicMock()
        with patch.dict(
            "app.agent_runtime.model_factory.PROVIDER_MAP",
            {"openai": mock_cls},
        ):
            from app.agent_runtime.model_factory import create_chat_model

            create_chat_model("openai", "gpt-4o", api_key="k")

        kwargs = mock_cls.call_args[1]
        assert "base_url" not in kwargs

    def test_api_key_from_settings_when_not_provided(self):
        """When api_key param is None, the key from settings should be used."""
        mock_cls = MagicMock()
        with (
            patch.dict(
                "app.agent_runtime.model_factory.PROVIDER_MAP",
                {"openai": mock_cls},
            ),
            patch.dict(
                "app.agent_runtime.model_factory.PROVIDER_API_KEY_MAP",
                {"openai": "settings-key"},
            ),
        ):
            from app.agent_runtime.model_factory import create_chat_model

            create_chat_model("openai", "gpt-4o", api_key=None)

        kwargs = mock_cls.call_args[1]
        assert kwargs["api_key"] == "settings-key"

    def test_explicit_api_key_overrides_settings(self):
        """Explicit api_key parameter should take precedence over settings."""
        mock_cls = MagicMock()
        with (
            patch.dict(
                "app.agent_runtime.model_factory.PROVIDER_MAP",
                {"openai": mock_cls},
            ),
            patch.dict(
                "app.agent_runtime.model_factory.PROVIDER_API_KEY_MAP",
                {"openai": "settings-key"},
            ),
        ):
            from app.agent_runtime.model_factory import create_chat_model

            create_chat_model("openai", "gpt-4o", api_key="explicit-key")

        kwargs = mock_cls.call_args[1]
        assert kwargs["api_key"] == "explicit-key"
