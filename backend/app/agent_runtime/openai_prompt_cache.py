"""Organization-opted-in OpenAI prompt routing, scoped to the running chat."""

from __future__ import annotations

import hmac
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
from langchain_core.language_models import LanguageModelInput
from langchain_openai import ChatOpenAI
from pydantic import Field

from app.authz.org_context import OrgContext
from app.config import settings
from app.security.key_provider import get_active_key


@dataclass(slots=True)
class _RoutingScope:
    # Tasks inherit the scope object. Revoking it also clears the key in a
    # child task that captured this context and outlived the parent run.
    key: str | None


_scope: ContextVar[_RoutingScope | None] = ContextVar("openai_prompt_cache_scope", default=None)


@contextmanager
def conversation_prompt_cache(
    context: OrgContext, conversation_id: UUID, *, run_path: str
) -> Iterator[None]:
    key = None
    if run_path == "chat" and context.org_id in settings.openai_prompt_cache_org_ids:
        data = (
            b"moldy:openai-prompt-cache:v1\0"
            + context.tenant_id.bytes
            + context.org_id.bytes
            + conversation_id.bytes
        )
        key = hmac.digest(get_active_key().raw, data, "sha256").hex()
    current = _RoutingScope(key)
    token = _scope.set(current)
    try:
        yield
    finally:
        current.key = None
        _scope.reset(token)


def _official_openai_base(base: object) -> bool:
    if not isinstance(base, (str, httpx.URL)):
        return False
    try:
        url = httpx.URL(base)
    except httpx.InvalidURL:
        return False
    return url in _OPENAI_URLS


_OPENAI_URLS = frozenset(httpx.URL(f"https://api.openai.com/v1{suffix}") for suffix in ("", "/"))


def prompt_cache_options(provider: str, base_url: object, configured_key: object) -> dict[str, Any]:
    """Only opt the canonical provider model into the per-send scope lookup."""
    if provider != "openai" or not _official_openai_base(base_url):
        return {}
    options: dict[str, Any] = {"cache_routing_supported": True}
    if isinstance(configured_key, str):
        options["configured_prompt_cache_key"] = configured_key
    return options


class ConversationCacheChatOpenAI(ChatOpenAI):
    """Add routing only to the outgoing payload, never the shared model defaults."""

    cache_routing_supported: bool = Field(default=False, exclude=True, repr=False)
    configured_prompt_cache_key: str | None = Field(default=None, exclude=True, repr=False)

    def _get_request_payload(
        self, input_: LanguageModelInput, *, stop: list[str] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        scope = _scope.get()
        if not self.cache_routing_supported or scope is None or scope.key is None:
            return payload
        if not all(
            _official_openai_base(getattr(root, "base_url", None))
            for root in (self.root_client, self.root_async_client)
        ):
            return payload
        extra_body = payload.get("extra_body")
        if isinstance(extra_body, Mapping) and "prompt_cache_key" in extra_body:
            return payload
        key = self.configured_prompt_cache_key
        payload.setdefault("prompt_cache_key", key if key is not None else scope.key)
        return payload
