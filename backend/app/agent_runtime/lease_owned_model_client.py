"""Lease-owned HTTP clients and OpenAI-family / Anthropic lease models (internal I12).

Each lease model gets brand-new ``httpx`` clients built here — never the
langchain default client caches and never internal I2's shared model cache —
with transport retries ``0`` and SDK ``max_retries=0``. The only retry is the
application loop in :mod:`app.agent_runtime.lease_bound_model`.

``_new_async_transport`` / ``_new_sync_transport`` are the private, test-only
transport-constructor seam (looked up at call time); the public factory API
accepts no caller client or transport.

Proxy policy: every owned client is built on an explicit transport, so httpx
ignores ``HTTP(S)_PROXY``/``ALL_PROXY`` environment proxies (it only mounts
env proxies when no transport is given). An ``OPENAI_PROXY`` environment value
would make ``ChatOpenAI`` build its own proxied client, so it is rejected as
``provider_transport_not_single_send`` instead of being honoured.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import cached_property
from types import ModuleType
from typing import Any

import anthropic
import httpx
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from pydantic import PrivateAttr

from app.agent_runtime.lease_bound_model import (
    LeaseBoundChatModelMixin,
    LeaseProviderTransportError,
    LeaseSendGuard,
)
from app.agent_runtime.model_client_cache import _ssl_ctx


def _new_async_transport(lib: ModuleType) -> Any:
    return lib.AsyncHTTPTransport(retries=0, verify=_ssl_ctx)


def _new_sync_transport(lib: ModuleType) -> Any:
    return lib.HTTPTransport(retries=0, verify=_ssl_ctx)


def new_owned_async_client(lib: ModuleType = httpx) -> Any:
    """A fresh async client on a zero-retry transport (call-time seam lookup).

    ``lib`` is ``httpx`` for OpenAI/Google and ``httpx2`` for the installed
    Anthropic SDK, which only accepts its own HTTP client type.
    """

    return lib.AsyncClient(transport=_new_async_transport(lib))


def new_owned_sync_client(lib: ModuleType = httpx) -> Any:
    return lib.Client(transport=_new_sync_transport(lib))


ANTHROPIC_CANONICAL_BASE_URL = "https://api.anthropic.com"


def close_unused_owned(aclose: Callable[[], Awaitable[None]]) -> None:
    """Close never-used owned clients from synchronous construction-failure paths.

    The clients have sent nothing, so they hold no connections bound to any
    event loop; a private loop on a short-lived thread runs the normal async
    close without touching the caller's (possibly running) loop. Cleanup
    errors never mask the construction error being propagated.
    """

    def run() -> None:
        with contextlib.suppress(Exception):  # best effort; the original error propagates
            asyncio.run(_await(aclose))

    worker = threading.Thread(target=run, name="lease-owned-client-close")
    worker.start()
    worker.join()


async def _await(aclose: Callable[[], Awaitable[None]]) -> None:
    await aclose()


@dataclass(eq=False, slots=True)
class OwnedHttpClients:
    """The exact clients one lease model owns; each side closes at most once."""

    async_client: Any
    sync_client: Any = None
    _async_closed: bool = field(default=False, init=False)
    _sync_closed: bool = field(default=False, init=False)

    @classmethod
    def fresh(cls, lib: ModuleType = httpx) -> OwnedHttpClients:
        return cls(async_client=new_owned_async_client(lib), sync_client=new_owned_sync_client(lib))

    async def aclose(self) -> None:
        if self.sync_client is not None and not self._sync_closed:
            self._sync_closed = True
            self.sync_client.close()
        if not self._async_closed:
            self._async_closed = True
            await self.async_client.aclose()


class LeaseChatOpenAI(LeaseBoundChatModelMixin, ChatOpenAI):
    """``ChatOpenAI`` bound to one lease guard and its own HTTP clients."""

    _lease_guard: LeaseSendGuard | None = PrivateAttr(default=None)


class LeaseChatAnthropic(LeaseBoundChatModelMixin, ChatAnthropic):
    """``ChatAnthropic`` whose SDK clients use the lease-owned HTTP clients.

    The installed ``_client``/``_async_client`` properties draw from
    langchain-anthropic's process-wide default client cache; these overrides
    build the SDK clients on the lease-owned clients instead.
    """

    _lease_guard: LeaseSendGuard | None = PrivateAttr(default=None)
    _owned_http: OwnedHttpClients | None = PrivateAttr(default=None)

    def bind_owned_http(self, owned: OwnedHttpClients) -> None:
        if self._owned_http is not None:
            raise LeaseProviderTransportError("anthropic lease clients are already bound")
        self._owned_http = owned

    def lease_async_sdk_client(self) -> anthropic.AsyncClient:
        return self._async_client

    def _require_owned_http(self) -> OwnedHttpClients:
        if self._owned_http is None or self._owned_http.sync_client is None:
            raise LeaseProviderTransportError("anthropic lease clients are not bound")
        return self._owned_http

    @cached_property
    def _client(self) -> anthropic.Client:
        sync_client = self._require_owned_http().sync_client
        return anthropic.Client(**self._client_params, http_client=sync_client)

    @cached_property
    def _async_client(self) -> anthropic.AsyncClient:
        async_client = self._require_owned_http().async_client
        return anthropic.AsyncClient(**self._client_params, http_client=async_client)


def build_openai_family_model(
    kwargs: dict[str, Any], *, guard: LeaseSendGuard, owned: OwnedHttpClients
) -> LeaseChatOpenAI:
    try:
        model = LeaseChatOpenAI(
            **kwargs,
            max_retries=0,
            http_client=owned.sync_client,
            http_async_client=owned.async_client,
        )
    except ValueError as exc:
        if "openai_proxy" not in str(exc):
            raise
        raise LeaseProviderTransportError(
            "an OPENAI_PROXY proxy cannot share the lease-owned transport"
        ) from exc
    model.bind_lease_guard(guard)
    _verify_openai_base_url(model, kwargs.get("base_url"))
    root = model.root_async_client
    bound = root._client if root is not None else None  # noqa: SLF001 — verify SDK binding
    if root is None or bound is not owned.async_client or root.max_retries != 0:
        raise LeaseProviderTransportError("openai async client is not the lease-owned client")
    return model


def _verify_openai_base_url(model: LeaseChatOpenAI, pinned: str | None) -> None:
    """The factory-pinned base URL must survive into both SDK roots (env cannot win)."""

    if not pinned:
        raise LeaseProviderTransportError("openai-family lease models require a pinned base url")
    expected = pinned.rstrip("/")
    roots = (model.root_client, model.root_async_client)
    if model.openai_api_base != pinned or any(
        root is None or str(root.base_url).rstrip("/") != expected for root in roots
    ):
        raise LeaseProviderTransportError("openai base url was redirected after construction")


def build_anthropic_model(
    kwargs: dict[str, Any], *, guard: LeaseSendGuard, owned: OwnedHttpClients
) -> LeaseChatAnthropic:
    model = LeaseChatAnthropic(**kwargs, max_retries=0)
    model.bind_lease_guard(guard)
    model.bind_owned_http(owned)
    sdk = model.lease_async_sdk_client()
    if model.anthropic_api_url != kwargs.get("base_url"):
        raise LeaseProviderTransportError("anthropic base url was redirected after construction")
    if sdk._client is not owned.async_client or sdk.max_retries != 0:  # noqa: SLF001
        raise LeaseProviderTransportError("anthropic async client is not the lease-owned client")
    return model
