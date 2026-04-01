from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI

from app.config import settings


PROVIDER_MAP: dict[str, type[BaseChatModel]] = {
    "openai": ChatOpenAI,
    "anthropic": ChatAnthropic,
    "google": ChatGoogleGenerativeAI,
    "custom": ChatOpenAI,  # OpenAI-compatible endpoints via base_url
}

# Map provider to env var key for default API keys
PROVIDER_API_KEY_MAP = {
    "openai": settings.openai_api_key,
    "anthropic": settings.anthropic_api_key,
    "google": settings.google_api_key,
}


def create_chat_model(
    provider: str,
    model_name: str,
    api_key: str | None = None,
    base_url: str | None = None,
) -> BaseChatModel:
    cls = PROVIDER_MAP.get(provider, ChatOpenAI)

    resolved_key = api_key or PROVIDER_API_KEY_MAP.get(provider, "")

    kwargs: dict = {"model": model_name}

    if resolved_key:
        kwargs["api_key"] = resolved_key
    if base_url:
        kwargs["base_url"] = base_url

    return cls(**kwargs)
