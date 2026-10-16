"""Shared in-process wire for lease provider transport tests (internal I12, slice I12a).

Every lease-owned HTTP client (async and sync, every provider) is built through
the ``_new_async_transport`` / ``_new_sync_transport`` seam, which this module
replaces with an ``httpx.MockTransport``: no test using :func:`install_wire`
can reach a real host.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from types import ModuleType
from typing import Any, cast

import anthropic
import httpx
import openai
import pytest

from app.agent_runtime import lease_owned_model_client as owned_module
from app.agent_runtime.lease_bound_model import LeaseSendGuard
from app.agent_runtime.lease_dispatch_permit import DispatchDeniedError, DispatchPermit
from app.agent_runtime.lease_model_factory import (
    LeaseModelSpec,
    LeaseOwnedModel,
    create_lease_chat_model,
)
from app.agent_runtime.lease_provider_retry_policy import NO_APPLICATION_RETRY

Handler = Callable[[Any, ModuleType], Any]
PROVIDERS = {
    "openai": ("openai", "gpt-4o-mini", {}),
    "openai_responses": ("openai", "gpt-4o-mini", {"use_responses_api": True}),
    "openrouter": ("openrouter", "some/model", {}),
    "anthropic": ("anthropic", "claude-sonnet-4-5", {}),
    "google": ("google", "gemini-2.5-flash", {}),
}
CONNECTION_ERRORS = (openai.APIConnectionError, anthropic.APIConnectionError, httpx.ConnectError)
FORBIDDEN = {
    "client_args": {"proxy": "http://p"},
    "async_client_args": {"transport": None},
    "httpx_async_client": object(),
    "aiohttp_client": object(),
    "retry": 3,
    "retries": 1,
    "retry_options": {"attempts": 5},
    "http_options": {"timeout": 5},
    "max_retries": 2,
}


class Wire:
    """Shared ordered event log: permit open/close and transport sends."""

    def __init__(self) -> None:
        self.events: list[tuple[Any, ...]] = []
        self.hosts: list[str] = []
        self.handler: Handler = _connect_error
        self.deny_from: int | None = None
        self.opened = 0
        self.transports_built = 0

    @property
    def sends(self) -> int:
        return sum(1 for event in self.events if event[0] == "send")

    def permit_descriptors(self) -> list[str]:
        return [event[2] for event in self.events if event[0] == "open"]

    def transport(self, lib: ModuleType) -> Any:
        self.transports_built += 1

        def handle(request: Any) -> Any:
            self.events.append(("send", request.url.path))
            self.hosts.append(request.url.host)
            return self.handler(request, lib)

        return lib.MockTransport(handle)

    def __call__(self, lease: Any, *, kind: str, descriptor_id: str, **_: Any) -> Any:
        return self._permit(kind, descriptor_id)

    @asynccontextmanager
    async def _permit(self, kind: str, descriptor_id: str):
        if self.deny_from is not None and self.opened + 1 >= self.deny_from:
            raise DispatchDeniedError("organization_archived")
        self.opened += 1
        number = self.opened
        self.events.append(("open", number, descriptor_id))
        try:
            yield DispatchPermit(cast(Any, kind), descriptor_id, False)
        finally:
            self.events.append(("close", number))


def _connect_error(request: Any, lib: ModuleType) -> Any:
    raise lib.ConnectError("connection refused", request=request)


def status_503(request: Any, lib: ModuleType) -> Any:
    body = {"error": {"code": 503, "message": "unavailable", "status": "UNAVAILABLE"}}
    return lib.Response(503, json=body)


def install_wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    """Route every lease-owned transport through one in-process fake."""

    state = Wire()
    monkeypatch.setattr(owned_module, "_new_async_transport", state.transport)
    monkeypatch.setattr(owned_module, "_new_sync_transport", state.transport)
    return state


def guard_for(wire: Wire, descriptor: str = "provider:root", **overrides: Any) -> LeaseSendGuard:
    async def no_sleep(_: float) -> None:
        wire.events.append(("sleep",))

    options: dict[str, Any] = {"policy": NO_APPLICATION_RETRY, "sleep": no_sleep, **overrides}
    return LeaseSendGuard(
        lease=cast(Any, object()),
        descriptor_id=descriptor,
        allowed_descriptors=frozenset({descriptor}),
        session_factory=cast(Any, None),
        permit_opener=wire,
        **options,
    )


def build(case: str, guard: LeaseSendGuard, **params: Any) -> LeaseOwnedModel:
    provider, model_name, extra = PROVIDERS[case]
    spec = LeaseModelSpec(provider, model_name, "test-key", params={**extra, **params})
    return create_lease_chat_model(spec, guard=guard)


async def drive(owned: LeaseOwnedModel, mode: str, **kwargs: Any) -> list[Any]:
    if mode == "ainvoke":
        return [await owned.model.ainvoke("hello", **kwargs)]
    return [chunk async for chunk in owned.model.astream("hello", **kwargs)]
