"""Tests for ``app.services.model_filtering``."""

from __future__ import annotations

import pytest

from app.services.model_filtering import (
    is_custom_openai_endpoint,
    should_include_model,
)

# -- is_custom_openai_endpoint -----------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (None, False),
        ("", False),
        ("https://api.openai.com/v1", False),
        ("https://api.openai.com", False),
        ("https://localhost:8000/v1", True),
        ("http://my-vllm:8001/v1", True),
        ("https://gateway.example.com/openai/v1", True),
    ],
)
def test_is_custom_openai_endpoint(url: str | None, expected: bool) -> None:
    assert is_custom_openai_endpoint(url) is expected


# -- should_include_model: custom API short-circuit --------------------------


@pytest.mark.parametrize(
    "model_id",
    [
        "babbage-002",  # blocked on official OpenAI
        "text-embedding-3-large",
        "whisper-1",
        "tts-1",
        "gpt-3.5-turbo-instruct",
    ],
)
def test_custom_api_includes_everything(model_id: str) -> None:
    """Custom endpoints surface every model — the user owns curation."""

    assert should_include_model("openai", model_id, is_custom_api=True) is True


# -- should_include_model: OpenAI official rules -----------------------------


@pytest.mark.parametrize(
    "model_id",
    [
        "babbage-002",
        "davinci-002",
        "computer-use-preview",
        "dall-e-3",
        "text-embedding-3-large",
        "tts-1",
        "gpt-4o-tts",  # substring rule
        "whisper-1",
        "omni-moderation-latest",
        "sora-1",
        "gpt-4o-realtime-preview",  # substring rule
        "gpt-3.5-turbo-instruct",
    ],
)
def test_openai_blocklist(model_id: str) -> None:
    assert should_include_model("openai", model_id, is_custom_api=False) is False


@pytest.mark.parametrize(
    "model_id",
    [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-5-thinking",
        "o1-preview",
        "o3-mini",
        "chatgpt-4o-latest",
    ],
)
def test_openai_allows_chat_models(model_id: str) -> None:
    assert should_include_model("openai", model_id, is_custom_api=False) is True


def test_openai_empty_id_rejected() -> None:
    assert should_include_model("openai", "", is_custom_api=False) is False


# -- Other providers ---------------------------------------------------------


def test_anthropic_passthrough() -> None:
    assert should_include_model("anthropic", "claude-anything", False) is True


def test_google_genai_passthrough() -> None:
    """Google's ``generateContent`` filter happens upstream of this helper."""

    assert should_include_model("google_genai", "gemini-2.0-flash", False) is True


def test_openrouter_passthrough() -> None:
    assert should_include_model("openrouter", "anthropic/claude-haiku-4-5", False) is True


def test_openai_compatible_passthrough() -> None:
    assert should_include_model("openai_compatible", "anything", False) is True


def test_unknown_provider_defaults_inclusive() -> None:
    """Unknown providers err on the side of showing the model."""

    assert should_include_model("future-provider", "x-1", False) is True
