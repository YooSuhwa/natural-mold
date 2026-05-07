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

    def test_no_base_url_pins_canonical_endpoint(self):
        """openai provider + base_url 미지정 → canonical OpenAI endpoint 강제.

        OS env ``OPENAI_BASE_URL`` 우회 차단 가드 (RunPod proxy / 사내 헬퍼
        등으로 export 해도 OpenAI 본 endpoint 로 라우팅).
        """
        mock_cls = MagicMock()
        with patch.dict(
            "app.agent_runtime.model_factory.PROVIDER_MAP",
            {"openai": mock_cls},
        ):
            from app.agent_runtime.model_factory import create_chat_model

            create_chat_model("openai", "gpt-4o", api_key="k")

        kwargs = mock_cls.call_args[1]
        assert kwargs["base_url"] == "https://api.openai.com/v1"

    def test_api_key_none_falls_back_to_settings(self):
        """api_key=None일 때 PROVIDER_API_KEY_MAP의 settings 키로 fallback.

        새 langchain-anthropic / langchain-openai은 pydantic strict로 None을 거부하므로
        명시 fallback 후 None이면 kwargs에서 제외 (라이브러리 환경변수 fallback).
        """
        mock_cls = MagicMock()
        with (
            patch.dict(
                "app.agent_runtime.model_factory.PROVIDER_MAP",
                {"openai": mock_cls},
            ),
            patch.dict(
                "app.agent_runtime.model_factory.PROVIDER_API_KEY_MAP",
                {"openai": "settings-fallback"},
            ),
        ):
            from app.agent_runtime.model_factory import create_chat_model

            create_chat_model("openai", "gpt-4o", api_key=None)

        kwargs = mock_cls.call_args[1]
        assert kwargs["api_key"] == "settings-fallback"

    def test_api_key_none_with_no_settings_excluded_from_kwargs(self):
        """api_key 인자도 None, settings도 None이면 kwargs에서 아예 제외 (라이브러리가
        환경변수로 직접 잡게)."""
        mock_cls = MagicMock()
        with (
            patch.dict(
                "app.agent_runtime.model_factory.PROVIDER_MAP",
                {"openai": mock_cls},
            ),
            patch.dict(
                "app.agent_runtime.model_factory.PROVIDER_API_KEY_MAP",
                {"openai": None},
            ),
        ):
            from app.agent_runtime.model_factory import create_chat_model

            create_chat_model("openai", "gpt-4o", api_key=None)

        kwargs = mock_cls.call_args[1]
        assert "api_key" not in kwargs

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


class TestCreateChatModelGpt5Family:
    """Tests for ``create_chat_model`` GPT-5 / o-series reasoning model guard.

    Reasoning models (gpt-5*, o1*, o3*, o4*) reject ``max_tokens`` and
    non-default ``temperature`` from the OpenAI Chat Completions API. They
    also burn output tokens on hidden reasoning chains, so a tight cap
    leaves the visible ``content`` empty even when the API responds 200 OK.
    """

    @staticmethod
    def _patched_create(model_name: str, **extra):
        from unittest.mock import MagicMock, patch
        mock_cls = MagicMock()
        with patch.dict(
            "app.agent_runtime.model_factory.PROVIDER_MAP",
            {"openai": mock_cls},
        ):
            from app.agent_runtime.model_factory import create_chat_model
            create_chat_model("openai", model_name, api_key="sk-test", **extra)
        return mock_cls.call_args[1]

    def test_gpt5_drops_max_tokens_and_forwards_max_completion_tokens(self):
        kwargs = self._patched_create("gpt-5.5-2026-04-23", max_tokens=512)
        assert "max_tokens" not in kwargs
        # langchain-openai 0.3+ 는 top-level kwarg 로만 forward
        assert kwargs["max_completion_tokens"] == 512

    def test_gpt5_drops_non_default_temperature(self):
        kwargs = self._patched_create("gpt-5", temperature=0.7)
        assert "temperature" not in kwargs

    def test_gpt5_default_completion_tokens_when_caller_omits_cap(self):
        """No max_tokens passed → default 4096 to avoid empty content regression."""
        kwargs = self._patched_create("gpt-5")
        assert kwargs["max_completion_tokens"] == 4096

    def test_o3_family_also_guarded(self):
        kwargs = self._patched_create("o3-mini")
        assert "max_tokens" not in kwargs
        assert kwargs["max_completion_tokens"] == 4096

    def test_non_gpt5_keeps_max_tokens_top_level(self):
        """gpt-4o is NOT a reasoning family — max_tokens stays as-is."""
        kwargs = self._patched_create("gpt-4o", max_tokens=256)
        assert kwargs["max_tokens"] == 256
        assert "max_completion_tokens" not in kwargs

    def test_gpt5_no_userwarning_from_model_kwargs(self):
        """``max_completion_tokens`` 는 top-level — model_kwargs 안에 들어가면
        LangChain 이 UserWarning 후 제거하므로 OpenAI 에 forward 되지 않는다."""
        kwargs = self._patched_create("gpt-5.5", max_tokens=200)
        model_kw = kwargs.get("model_kwargs", {})
        assert "max_completion_tokens" not in model_kw


class TestCreateChatModelBaseUrlGuard:
    """``OPENAI_BASE_URL`` env 우회 차단 가드.

    ChatOpenAI 가 base_url 미지정 시 OpenAI Python SDK 가 ``OPENAI_BASE_URL``
    env 로 fallback. 사용자 셸이 RunPod proxy / Claude Code helper / 사내
    프록시로 export 해놓으면 OpenAI 본 endpoint 가 아닌 엉뚱한 호스트로
    라우팅되어 404 회귀. provider 별 canonical endpoint 명시 set 으로 차단.
    """

    @staticmethod
    def _patched_create(provider: str, model_name: str, base_url=None):
        from unittest.mock import MagicMock, patch
        mock_cls = MagicMock()
        with patch.dict(
            "app.agent_runtime.model_factory.PROVIDER_MAP",
            {provider: mock_cls},
        ):
            from app.agent_runtime.model_factory import create_chat_model
            create_chat_model(provider, model_name, api_key="sk-test", base_url=base_url)
        return mock_cls.call_args[1]

    def test_openai_base_url_pinned_when_caller_omits(self):
        """openai provider + base_url 미지정 → canonical endpoint 강제 (RunPod env 차단)."""
        kwargs = self._patched_create("openai", "gpt-5.5")
        assert kwargs["base_url"] == "https://api.openai.com/v1"

    def test_openrouter_base_url_pinned_when_caller_omits(self):
        """openrouter provider 도 같은 가드 — qwen/llama/* 같은 OpenRouter 모델."""
        kwargs = self._patched_create("openrouter", "qwen/qwen3.6-27b")
        assert kwargs["base_url"] == "https://openrouter.ai/api/v1"

    def test_explicit_base_url_takes_precedence(self):
        """caller 가 base_url 명시 → 가드 우회 (사용자 의도 보존)."""
        custom = "https://custom.proxy.example/v1"
        kwargs = self._patched_create("openai", "gpt-4o", base_url=custom)
        assert kwargs["base_url"] == custom

    def test_anthropic_no_base_url_pin(self):
        """anthropic 은 ChatOpenAI 가 아니므로 base_url 가드 영향 없음."""
        kwargs = self._patched_create("anthropic", "claude-sonnet-4-6")
        assert "base_url" not in kwargs
