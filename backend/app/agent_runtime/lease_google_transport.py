"""Google lease transport construction, freezing and cleanup (internal I12).

The factory builds one fresh ``httpx.AsyncClient`` and one fresh
``httpx.Client`` (both through the zero-retry transport seam) and hands them
to :class:`LeaseGoogleChatModel`'s validator through a private construction
context — the model never accepts a caller client. After the
validator builds the single ``google.genai.Client``, the transport is frozen
into a non-secret :class:`ProviderTransportDescriptor` for the lease digest
(its ``base_url`` carries no userinfo, query or fragment). Injecting the sync
client too means the SDK never builds its own default sync client, so no
sync SDK path (count-tokens, sync generate) can reach a host the seam does not
own.

:class:`GoogleOwnedCleanup` replaces the installed ``_ClientCleanup`` for the
lease model: it closes the SDK client, the exact injected sync client and the
exact injected async client, each once, on the loop that used it. The SDK
refuses to close caller-provided clients, so the installed cleanup is never
invoked.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.agent_runtime import lease_owned_model_client as owned_clients
from app.agent_runtime.lease_bound_model import LeaseProviderTransportError

GOOGLE_SDK_ATTEMPTS = 1
GOOGLE_ADAPTER_MAX_RETRIES = 1
GOOGLE_CANONICAL_BASE_URL = "https://generativelanguage.googleapis.com/"


def redact_base_url(base_url: str | None) -> str | None:
    """Drop userinfo, query and fragment so the descriptor never carries secrets."""

    if base_url is None:
        return None
    parts = urlsplit(base_url)
    host = parts.hostname or ""
    host = f"[{host}]" if ":" in host else host
    netloc = f"{host}:{parts.port}" if parts.port is not None else host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


OWNED_CLIENT_IDENTITY = "fresh_lease_owned_client"


@dataclass(frozen=True, slots=True)
class ProviderTransportDescriptor:
    """Non-secret, process-local facts about one lease model's transport."""

    provider: str
    transport_kind: str
    client_identity: int
    sdk_attempts: int
    adapter_max_retries: int
    base_url: str | None
    api_version: str | None
    allowed_headers: tuple[str, ...]
    cleanup_owner: str

    def digest_material(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "transport_kind": self.transport_kind,
            # ``client_identity`` is an in-process ``id()``: digesting it made the
            # material non-reproducible. Only the stable ownership fact is hashed.
            "client_identity": OWNED_CLIENT_IDENTITY,
            "sdk_attempts": self.sdk_attempts,
            "adapter_max_retries": self.adapter_max_retries,
            "base_url": self.base_url,
            "api_version": self.api_version,
            "allowed_headers": list(self.allowed_headers),
            "cleanup_owner": self.cleanup_owner,
        }


@dataclass(eq=False, slots=True)
class GoogleOwnedCleanup:
    """Cleanup token owning one SDK client plus the exact injected HTTP clients."""

    async_http: httpx.AsyncClient
    sync_http: httpx.Client
    sdk_client: Any = None
    _loop: asyncio.AbstractEventLoop | None = field(default=None, init=False)
    _sdk_closed: bool = field(default=False, init=False)
    _sync_closed: bool = field(default=False, init=False)
    _async_closed: bool = field(default=False, init=False)

    def __eq__(self, other: object) -> bool:
        # Mirrors the installed token: cleanup state stays out of model equality.
        return isinstance(other, GoogleOwnedCleanup)

    __hash__ = object.__hash__

    def register_async_loop(self) -> None:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._loop is None:
            self._loop = running

    async def aclose(self) -> None:
        running = asyncio.get_running_loop()
        if self._loop is not None and running is not self._loop:
            raise RuntimeError(
                "The async client must be closed on the event loop where it was used."
            )
        if self.sdk_client is not None and not self._sdk_closed:
            self._sdk_closed = True
            self.sdk_client.close()
        if not self._sync_closed:
            self._sync_closed = True
            self.sync_http.close()
        if not self._async_closed:
            self._async_closed = True
            await self.async_http.aclose()


@dataclass(frozen=True, slots=True)
class GoogleTransportBundle:
    """Factory-owned values the lease Google validator is allowed to consume."""

    async_http: httpx.AsyncClient
    sync_http: httpx.Client
    cleanup: GoogleOwnedCleanup


_PENDING: ContextVar[GoogleTransportBundle | None] = ContextVar(
    "lease_google_transport_pending", default=None
)


def new_google_transport_bundle() -> GoogleTransportBundle:
    async_http = owned_clients.new_owned_async_client()
    sync_http = owned_clients.new_owned_sync_client()
    cleanup = GoogleOwnedCleanup(async_http, sync_http)
    return GoogleTransportBundle(async_http=async_http, sync_http=sync_http, cleanup=cleanup)


@contextmanager
def pending_google_transport(bundle: GoogleTransportBundle) -> Iterator[None]:
    """Expose ``bundle`` to exactly the validator running inside this block."""

    token = _PENDING.set(bundle)
    try:
        yield
    finally:
        _PENDING.reset(token)


def take_pending_google_transport() -> GoogleTransportBundle:
    bundle = _PENDING.get()
    if bundle is None:
        raise LeaseProviderTransportError("google lease model built outside the lease factory")
    _PENDING.set(None)
    return bundle


def verify_and_freeze_google_transport(
    sdk_client: Any, bundle: GoogleTransportBundle, *, headers: dict[str, str]
) -> ProviderTransportDescriptor:
    """Prove the SDK selected the exact owned clients with one attempt and no aiohttp."""

    api_client = sdk_client._api_client  # noqa: SLF001 — read-only proof of SDK binding
    options = api_client._http_options  # noqa: SLF001
    retry = options.retry_options
    if (
        api_client._async_httpx_client is not bundle.async_http  # noqa: SLF001
        or options.httpx_async_client is not bundle.async_http
        or api_client._httpx_client is not bundle.sync_http  # noqa: SLF001
        or options.httpx_client is not bundle.sync_http
        or api_client._use_aiohttp()  # noqa: SLF001
        or retry is None
        or retry.attempts != GOOGLE_SDK_ATTEMPTS
        or api_client._aiohttp_session is not None  # noqa: SLF001
    ):
        raise LeaseProviderTransportError("google SDK did not bind the owned transports")
    return ProviderTransportDescriptor(
        provider="google",
        transport_kind="httpx_async",
        client_identity=id(bundle.async_http),
        sdk_attempts=GOOGLE_SDK_ATTEMPTS,
        adapter_max_retries=GOOGLE_ADAPTER_MAX_RETRIES,
        base_url=redact_base_url(options.base_url),
        api_version=options.api_version,
        allowed_headers=tuple(sorted(name.lower() for name in headers)),
        cleanup_owner="lease_google_owned_cleanup",
    )
