"""LLM model factory — wrap provider SDKs in a uniform LangChain interface.

Greenfield M5: API keys live exclusively in :class:`Credential` rows now, so
``PROVIDER_API_KEY_MAP`` and the legacy ``llm_provider`` join are gone. The
caller (:mod:`app.services.chat_service` via the conversations router and the
trigger executor) decrypts ``Agent.llm_credential`` and passes the resolved
``api_key`` here. Env-var fallback is retained for the small set of internal
sub-agents (Builder/Assistant) that don't have a credential of their own.
"""

from __future__ import annotations

import os
import ssl
from typing import Any

import certifi
import httpx
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI

from app.config import settings

PROVIDER_MAP: dict[str, type[BaseChatModel]] = {
    "openai": ChatOpenAI,
    "anthropic": ChatAnthropic,
    "google": ChatGoogleGenerativeAI,
    "custom": ChatOpenAI,
    "openrouter": ChatOpenAI,
    "openai_compatible": ChatOpenAI,
}


# Internal callers (Builder/Assistant sub-agents) don't have a Credential row;
# they fall back to env-derived settings. End-user agents get their key from
# ``Agent.llm_credential`` via the chat runtime.
_ENV_FALLBACK: dict[str, str] = {
    "openai": settings.openai_api_key,
    "anthropic": settings.anthropic_api_key,
    "google": settings.google_api_key,
    "openrouter": settings.openrouter_api_key,
}

# Backwards-compatible alias used by Assistant/Builder sub-agent helpers.
PROVIDER_API_KEY_MAP = _ENV_FALLBACK


# 사내 프록시 SSL — certifi 기본 인증서 + HC_SSL.pem 결합
_hc_cert = os.path.expanduser("~/.ssl/HC_SSL.pem")
_ssl_ctx: ssl.SSLContext | None = None
if os.path.exists(_hc_cert):
    _ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    _ssl_ctx.load_verify_locations(_hc_cert)


def create_chat_model(
    provider: str,
    model_name: str,
    api_key: str | None = None,
    base_url: str | None = None,
    **extra: object,
) -> BaseChatModel:
    """Build a LangChain chat model for ``provider``.

    ``api_key`` is the resolved (decrypted) key from the caller. When ``None``
    the env-var fallback is consulted for ``openai``/``anthropic``/``google``/
    ``openrouter`` only — other providers must receive an explicit key.
    """

    cls = PROVIDER_MAP.get(provider, ChatOpenAI)

    resolved_key = api_key or _ENV_FALLBACK.get(provider) or None
    kwargs: dict[str, Any] = {"model": model_name}
    if resolved_key:
        kwargs["api_key"] = resolved_key
    if base_url:
        kwargs["base_url"] = base_url

    for param in ("temperature", "top_p", "max_tokens"):
        if param in extra and extra[param] is not None:
            if param == "top_p" and extra[param] == 1.0:
                continue
            kwargs[param] = extra[param]

    # Anthropic rejects temperature + top_p simultaneously.
    if provider == "anthropic" and "temperature" in kwargs and "top_p" in kwargs:
        kwargs.pop("top_p")

    kwargs["stream_usage"] = True

    if _ssl_ctx and cls in (ChatOpenAI,):
        kwargs["http_async_client"] = httpx.AsyncClient(verify=_ssl_ctx)
        kwargs["http_client"] = httpx.Client(verify=_ssl_ctx)

    return cls(**kwargs)


def env_provider_keys() -> dict[str, str | None]:
    """Return the env-var fallback map. Used by ``provider_api_keys`` paths."""

    return {provider: key or None for provider, key in _ENV_FALLBACK.items()}


__all__ = ["PROVIDER_MAP", "create_chat_model", "env_provider_keys"]
