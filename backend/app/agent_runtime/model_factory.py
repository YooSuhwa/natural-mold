from __future__ import annotations

import os
import ssl

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

PROVIDER_API_KEY_MAP = {
    "openai": settings.openai_api_key,
    "anthropic": settings.anthropic_api_key,
    "google": settings.google_api_key,
}

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
    cls = PROVIDER_MAP.get(provider, ChatOpenAI)

    resolved_key = api_key or PROVIDER_API_KEY_MAP.get(provider, "")

    kwargs: dict = {"model": model_name}

    if resolved_key:
        kwargs["api_key"] = resolved_key
    if base_url:
        kwargs["base_url"] = base_url

    # Model parameters (temperature, top_p, max_tokens, etc.)
    for param in ("temperature", "top_p", "max_tokens"):
        if param in extra and extra[param] is not None:
            kwargs[param] = extra[param]

    # Enable usage metadata in streaming responses
    kwargs["stream_usage"] = True

    if _ssl_ctx and cls in (ChatOpenAI,):
        kwargs["http_async_client"] = httpx.AsyncClient(verify=_ssl_ctx)
        kwargs["http_client"] = httpx.Client(verify=_ssl_ctx)

    return cls(**kwargs)
