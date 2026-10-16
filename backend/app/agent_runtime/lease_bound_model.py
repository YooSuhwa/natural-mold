"""Provider send boundary for lease-owned chat models (internal I12).

Every actual provider send — root/child primary, application retry, fallback
candidate, summarization — goes through a lease-owned model whose
``_agenerate``/``_astream`` run inside a freshly opened canonical dispatch
permit. The permit stays open through the HTTP send and, for streams, through
eager establishment (the first chunk); later chunks are a bounded read of that
one response. A retryable failure closes the permit before the bounded backoff
and the next application attempt opens a distinct permit, so revocation during
backoff stops at one send. Synchronous sends and provider-side token counting
(``get_num_tokens*``/``get_token_ids``, which call count-tokens endpoints or
download tokenizer files synchronously) are refused: they would bypass the
async permit entirely.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar, cast

from app.agent_runtime.lease_dispatch_permit import (
    DispatchDeniedError,
    DispatchPermit,
    PermitKind,
    SessionFactory,
    open_dispatch_permit,
)
from app.agent_runtime.lease_provider_retry_policy import LeaseProviderRetryPolicy
from app.agent_runtime.runtime_config import PreparedRunLease

PROVIDER_TRANSPORT_NOT_SINGLE_SEND = "provider_transport_not_single_send"

FORBIDDEN_TRANSPORT_KEYS = frozenset(
    {
        "client",
        "async_client",
        "root_client",
        "root_async_client",
        "http_client",
        "http_async_client",
        "client_args",
        "async_client_args",
        "httpx_client",
        "httpx_async_client",
        "aiohttp_client",
        "transport",
        "retry",
        "retries",
        "retry_options",
        "http_options",
        "max_retries",
        "openai_proxy",
        "anthropic_proxy",
        "streaming",
    }
)

# Request-config-only keys a caller could pass per invocation to change how many
# requests one logical send issues (Google automatic function calling) or how
# the response is consumed; rejected before any permit opens.
REQUEST_ONLY_FORBIDDEN_KEYS = frozenset(
    {"automatic_function_calling", "should_return_http_response"}
)
INVOCATION_FORBIDDEN_KEYS = FORBIDDEN_TRANSPORT_KEYS | REQUEST_ONLY_FORBIDDEN_KEYS

T = TypeVar("T")
_END = object()


class LeaseProviderTransportError(RuntimeError):
    """A provider/transport cannot prove one permitted send; rejected pre-send.

    Deliberately not a ``ValueError`` so Pydantic validators re-raise it
    unchanged instead of wrapping it in a ``ValidationError``.
    """

    def __init__(self, detail: str = "", *, code: str = PROVIDER_TRANSPORT_NOT_SINGLE_SEND) -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def reject_transport_overrides(
    params: Mapping[str, Any],
    *,
    where: str,
    forbidden_keys: frozenset[str] = FORBIDDEN_TRANSPORT_KEYS,
) -> None:
    """Refuse caller client/transport/retry overrides, including in ``model_kwargs``."""

    keys = set(params)
    nested = params.get("model_kwargs")
    if isinstance(nested, Mapping):
        keys |= set(nested)
    elif nested is not None:
        raise LeaseProviderTransportError(f"{where}: model_kwargs must be a mapping")
    forbidden = sorted(keys & forbidden_keys)
    if forbidden:
        raise LeaseProviderTransportError(f"{where}: forbidden override {forbidden}")


class PermitOpener(Protocol):
    def __call__(
        self,
        lease: PreparedRunLease,
        *,
        kind: PermitKind,
        descriptor_id: str,
        allowed_descriptors: frozenset[str],
        session_factory: SessionFactory,
    ) -> AbstractAsyncContextManager[DispatchPermit]: ...


@dataclass(frozen=True, slots=True)
class LeaseSendGuard:
    """Opens one permit per provider send attempt for one model descriptor."""

    lease: PreparedRunLease
    descriptor_id: str
    allowed_descriptors: frozenset[str]
    session_factory: SessionFactory
    policy: LeaseProviderRetryPolicy = LeaseProviderRetryPolicy()
    kind: PermitKind = "provider"
    permit_opener: PermitOpener = open_dispatch_permit
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    def _permit(self) -> AbstractAsyncContextManager[DispatchPermit]:
        return self.permit_opener(
            self.lease,
            kind=self.kind,
            descriptor_id=self.descriptor_id,
            allowed_descriptors=self.allowed_descriptors,
            session_factory=self.session_factory,
        )

    async def _backoff_or_raise(self, exc: Exception, attempt: int) -> None:
        if isinstance(exc, (DispatchDeniedError, LeaseProviderTransportError)):
            raise exc
        if not self.policy.should_retry(exc, attempt):
            raise exc
        await self.sleep(self.policy.delay_for(attempt))

    async def send(self, attempt: Callable[[], Awaitable[T]]) -> T:
        """Run one non-streaming send per permit with bounded application retry."""

        number = 1
        while True:
            try:
                async with self._permit():
                    return await attempt()
            except Exception as exc:  # noqa: BLE001 — classified by the retry policy
                await self._backoff_or_raise(exc, number)
            number += 1

    async def stream(self, attempt: Callable[[], AsyncIterator[T]]) -> AsyncIterator[T]:
        """Hold the permit through eager stream establishment (first chunk)."""

        number = 1
        while True:
            chunks = attempt()
            try:
                async with self._permit():
                    first = await anext(chunks, _END)
            except Exception as exc:  # noqa: BLE001 — classified by the retry policy
                await _aclose(chunks)
                await self._backoff_or_raise(exc, number)
                number += 1
                continue
            break
        if first is _END:
            return
        try:
            yield cast(T, first)
            async for chunk in chunks:
                yield chunk
        finally:
            await _aclose(chunks)


async def _aclose(chunks: AsyncIterator[Any]) -> None:
    close = getattr(chunks, "aclose", None)
    if close is not None:
        await close()


class LeaseBoundChatModelMixin:
    """Mixin placed before the provider chat model class in the MRO.

    Concrete lease classes declare ``_lease_guard`` as a Pydantic private
    attribute; a model without a bound guard refuses to send.
    """

    def bind_lease_guard(self, guard: LeaseSendGuard) -> None:
        """Bind the per-model send guard once, at lease construction."""

        if getattr(self, "_lease_guard", None) is not None:
            raise LeaseProviderTransportError("lease send guard is already bound")
        self._lease_guard = guard

    def _require_lease_guard(self) -> LeaseSendGuard:
        guard = getattr(self, "_lease_guard", None)
        if not isinstance(guard, LeaseSendGuard):
            raise LeaseProviderTransportError("lease send guard is not bound")
        return guard

    async def _agenerate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> Any:
        guard = self._require_lease_guard()
        reject_transport_overrides(kwargs, where="invoke", forbidden_keys=INVOCATION_FORBIDDEN_KEYS)
        parent = cast(Any, super())._agenerate  # noqa: SLF001 — cooperative override
        return await guard.send(
            lambda: parent(messages, stop=stop, run_manager=run_manager, **kwargs)
        )

    async def _astream(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> AsyncIterator[Any]:
        guard = self._require_lease_guard()
        reject_transport_overrides(kwargs, where="stream", forbidden_keys=INVOCATION_FORBIDDEN_KEYS)
        parent = cast(Any, super())._astream  # noqa: SLF001 — cooperative override
        stream = guard.stream(
            lambda: parent(messages, stop=stop, run_manager=run_manager, **kwargs)
        )
        async for chunk in stream:
            yield chunk

    def _generate(self, *args: Any, **kwargs: Any) -> Any:
        raise LeaseProviderTransportError("synchronous provider sends bypass the dispatch permit")

    def _stream(self, *args: Any, **kwargs: Any) -> Any:
        raise LeaseProviderTransportError("synchronous provider sends bypass the dispatch permit")

    def get_num_tokens(self, text: str) -> int:
        raise LeaseProviderTransportError("provider token counting bypasses the dispatch permit")

    def get_num_tokens_from_messages(self, messages: Any, tools: Any = None) -> int:
        raise LeaseProviderTransportError("provider token counting bypasses the dispatch permit")

    def get_token_ids(self, text: str) -> list[int]:
        raise LeaseProviderTransportError("provider token counting bypasses the dispatch permit")
