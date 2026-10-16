"""Lease model factory: new, lease-owned, single-send chat models (internal I12).

Unlike :func:`app.agent_runtime.model_factory.create_chat_model`, nothing here
reads or writes the shared model cache: every call constructs a brand-new
model plus brand-new HTTP clients that only this lease owns and closes.
Supported providers are the OpenAI family (``openai``/``custom``/
``openrouter``/``openai_compatible``), ``anthropic`` and ``google``; anything
else, and any caller client/transport/retry override, is rejected before the
materialization marker with ``provider_transport_not_single_send``.

Every provider gets its canonical base URL pinned explicitly when the spec
names none (OpenAI family via ``_apply_openai_compatible_base_url``; ``custom``
has no canonical endpoint and must name one), and the pin is verified after
construction, so ``OPENAI_API_BASE``/``OPENAI_BASE_URL``/``ANTHROPIC_BASE_URL``/
``ANTHROPIC_API_URL``/``LANGSMITH_GATEWAY``/``GOOGLE_GEMINI_BASE_URL`` cannot
redirect a lease send.
If construction fails after the owned clients exist, they are closed before
the error propagates.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx
import httpx2
from langchain_core.language_models import BaseChatModel

from app.agent_runtime.lease_bound_model import (
    LeaseProviderTransportError,
    LeaseSendGuard,
    reject_transport_overrides,
)
from app.agent_runtime.lease_google_chat_model import LeaseGoogleChatModel
from app.agent_runtime.lease_google_transport import (
    GOOGLE_ADAPTER_MAX_RETRIES,
    GOOGLE_CANONICAL_BASE_URL,
    ProviderTransportDescriptor,
    new_google_transport_bundle,
    pending_google_transport,
    redact_base_url,
)
from app.agent_runtime.lease_owned_model_client import (
    ANTHROPIC_CANONICAL_BASE_URL,
    OwnedHttpClients,
    build_anthropic_model,
    build_openai_family_model,
    close_unused_owned,
)
from app.agent_runtime.model_provider_builders import (
    _apply_anthropic_quirks,
    _apply_context_window_profile,
    _apply_gpt5_quirks,
    _apply_openai_compatible_base_url,
)
from app.agent_runtime.model_provider_registry import _GPT5_DEFAULT_COMPLETION_TOKENS

OPENAI_FAMILY = frozenset({"openai", "custom", "openrouter", "openai_compatible"})
SUPPORTED_PROVIDERS = OPENAI_FAMILY | {"anthropic", "google"}
_SCALAR_PARAMS = ("temperature", "top_p", "max_tokens")
_ALLOWED_PARAMS = frozenset({*_SCALAR_PARAMS, "context_window", "use_responses_api"})


@dataclass(frozen=True, slots=True)
class LeaseModelSpec:
    """Factory-owned inputs for one lease model; carries no client or transport."""

    provider: str
    model_name: str
    api_key: str = field(repr=False)
    base_url: str | None = None
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(eq=False, slots=True)
class LeaseOwnedModel:
    """One lease-owned model, its frozen transport facts, and its cleanup owner."""

    model: BaseChatModel
    transport: ProviderTransportDescriptor
    _cleanup: Any = field(repr=False)

    async def aclose(self) -> None:
        """Close each owned client side exactly once; never a shared client."""

        await self._cleanup.aclose()


def _base_kwargs(spec: LeaseModelSpec) -> tuple[dict[str, Any], int | None]:
    if spec.provider not in SUPPORTED_PROVIDERS:
        raise LeaseProviderTransportError(f"unsupported lease provider {spec.provider!r}")
    if not spec.api_key:
        raise LeaseProviderTransportError("lease models require a resolved api key")
    reject_transport_overrides(spec.params, where="lease model params")
    unknown = sorted(set(spec.params) - _ALLOWED_PARAMS)
    if unknown:
        raise LeaseProviderTransportError(f"unsupported lease model params {unknown}")
    kwargs: dict[str, Any] = {"model": spec.model_name, "api_key": spec.api_key}
    if spec.base_url:
        kwargs["base_url"] = spec.base_url
    for name in _SCALAR_PARAMS:
        value = spec.params.get(name)
        if value is None or (name == "top_p" and value == 1.0):
            continue
        kwargs[name] = value
    if spec.params.get("use_responses_api") is not None:
        if spec.provider not in OPENAI_FAMILY:
            raise LeaseProviderTransportError("use_responses_api is OpenAI-family only")
        kwargs["use_responses_api"] = bool(spec.params["use_responses_api"])
    window = spec.params.get("context_window")
    return kwargs, window if isinstance(window, int) and window > 0 else None


def _descriptor(spec: LeaseModelSpec, model: Any, owned: OwnedHttpClients, base: str | None):
    return ProviderTransportDescriptor(
        provider=spec.provider,
        transport_kind="httpx_async",
        client_identity=id(owned.async_client),
        sdk_attempts=1,
        adapter_max_retries=int(model.max_retries),
        base_url=redact_base_url(base),
        api_version=None,
        allowed_headers=(),
        cleanup_owner="lease_owned_http_clients",
    )


def create_lease_chat_model(spec: LeaseModelSpec, *, guard: LeaseSendGuard) -> LeaseOwnedModel:
    """Construct a new lease-owned model; the caller owns ``aclose()``."""

    kwargs, window = _base_kwargs(spec)
    if spec.provider == "google":
        bundle = new_google_transport_bundle()
        kwargs.setdefault("base_url", GOOGLE_CANONICAL_BASE_URL)
        kwargs.update(max_retries=GOOGLE_ADAPTER_MAX_RETRIES, vertexai=False)
        try:
            with pending_google_transport(bundle):
                google = LeaseGoogleChatModel(**kwargs)
            google.bind_lease_guard(guard)
        except BaseException:
            close_unused_owned(bundle.cleanup.aclose)
            raise
        _apply_context_window_profile(google, window)
        return LeaseOwnedModel(google, google.lease_transport, bundle.cleanup)

    owned = OwnedHttpClients.fresh(httpx2 if spec.provider == "anthropic" else httpx)
    try:
        model, base = _build_http_model(spec, kwargs, guard, owned)
    except BaseException:
        close_unused_owned(owned.aclose)
        raise
    _apply_context_window_profile(model, window)
    return LeaseOwnedModel(model, _descriptor(spec, model, owned, base), owned)


def _build_http_model(
    spec: LeaseModelSpec, kwargs: dict[str, Any], guard: LeaseSendGuard, owned: OwnedHttpClients
) -> tuple[Any, str | None]:
    if spec.provider == "anthropic":
        kwargs.setdefault("base_url", ANTHROPIC_CANONICAL_BASE_URL)
        _apply_anthropic_quirks(spec.provider, kwargs)
        model = build_anthropic_model(kwargs, guard=guard, owned=owned)
        return model, model.anthropic_api_url
    _apply_gpt5_quirks(
        spec.provider,
        spec.model_name,
        kwargs,
        completion_token_default=_GPT5_DEFAULT_COMPLETION_TOKENS,
    )
    _apply_openai_compatible_base_url(spec.provider, kwargs)
    model = build_openai_family_model({**kwargs, "stream_usage": True}, guard=guard, owned=owned)
    return model, model.openai_api_base
