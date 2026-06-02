"""Filter discovered model lists down to chat-capable identifiers.

The chat surface only wants conversational LLMs — embedding/audio/image/legacy
models pollute the picker. Each LLM provider has its own naming convention, so
``should_include_model`` dispatches by provider with sensible defaults. Custom
APIs (Ollama, vLLM, Azure-style deployments behind an OpenAI-compatible
endpoint) include all models because the user is responsible for what they
expose.
"""

from __future__ import annotations

from urllib.parse import urlparse

# Hosts considered "official" for OpenAI's API surface. Any other host triggers
# the custom-API branch (filters disabled, source treated as user-curated).
_OFFICIAL_OPENAI_HOSTS: frozenset[str] = frozenset({"api.openai.com"})

# OpenAI: drop completion-only / non-chat / deprecated families.
_OPENAI_PREFIX_BLOCK: tuple[str, ...] = (
    "babbage",
    "davinci",
    "computer-use",
    "dall-e",
    "text-embedding",
    "tts",
    "whisper",
    "omni-moderation",
    "sora",
)
_OPENAI_SUBSTR_BLOCK: tuple[str, ...] = (
    "-tts",
    "-realtime",
)


def is_custom_openai_endpoint(base_url: str | None) -> bool:
    """Return ``True`` if ``base_url`` points to a non-official OpenAI host.

    A missing URL falls back to the public OpenAI host (i.e. *not* custom).
    """

    if not base_url:
        return False
    try:
        parsed = urlparse(base_url)
    except (ValueError, AttributeError):
        return True
    host = (parsed.hostname or "").lower()
    if not host:
        return True
    return host not in _OFFICIAL_OPENAI_HOSTS


def should_include_model(
    provider: str,
    model_id: str,
    is_custom_api: bool,
) -> bool:
    """Decide whether ``model_id`` is worth surfacing in the picker.

    Custom APIs short-circuit to ``True`` — the user is in charge of what
    their endpoint exposes, and we have no reliable taxonomy for arbitrary
    OpenAI-compatible deployments. Official providers get the conservative
    blocklist.
    """

    if is_custom_api:
        return True

    if not model_id:
        return False

    provider_norm = (provider or "").lower()

    if provider_norm == "openai":
        if any(model_id.startswith(prefix) for prefix in _OPENAI_PREFIX_BLOCK):
            return False
        if any(snippet in model_id for snippet in _OPENAI_SUBSTR_BLOCK):
            return False
        # Filter out the legacy text-completion family (gpt-3.5-turbo-instruct).
        return not (model_id.startswith("gpt-") and "instruct" in model_id)

    if provider_norm == "anthropic":
        # Anthropic ships only chat models in its public surface.
        return True

    if provider_norm == "google_genai":
        # Google's /v1beta/models response carries supportedGenerationMethods,
        # which the discovery layer checks before calling here. Pass-through.
        return True

    if provider_norm == "openrouter":
        # OpenRouter curates its own catalog; trust it.
        return True

    if provider_norm == "openai_compatible":
        # Same logic as the custom-API short-circuit above, kept explicit.
        return True

    # Unknown providers default to inclusive — better to over-list than to
    # silently swallow a model the user wants.
    return True


__all__ = ["is_custom_openai_endpoint", "should_include_model"]
