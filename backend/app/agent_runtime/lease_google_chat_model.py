"""The only prepared Google adapter: ``LeaseGoogleChatModel`` (internal I12).

``validate_environment`` has the same name as the installed Pydantic model
validator, so Pydantic replaces the parent's entry (leaf-last) and the
installed default-client constructor never runs. This override reproduces the
installed scalar/base-url/header validation without calling ``super()`` and
builds exactly one ``google.genai.Client`` whose async side is the exact
factory-owned ``httpx.AsyncClient`` and whose sync side is the exact
factory-owned ``httpx.Client`` (both zero-retry seam transports) with SDK
``HttpRetryOptions(attempts=1)``; the adapter's ``max_retries`` is pinned to
``1`` (the installed SDK reads ``0`` as "default retries"). Without a caller
base URL the canonical Gemini endpoint is pinned explicitly, so
``GOOGLE_GEMINI_BASE_URL`` cannot redirect the owned transport. A raw
``model_kwargs`` transport/retry key is rejected before the installed
``build_extra`` can hoist it into a field (``retries`` is ``max_retries``'s
alias).

The installed ``_agenerate``/``_astream`` request and response conversion is
inherited unchanged; ``_build_request_config`` only rejects transport/retry
overrides and disables SDK automatic function calling (whose generator would
defer the first send past the permit and may issue more than one request)
before delegating to the installed builder.
"""

from __future__ import annotations

from typing import Any, Self

from google.genai import Client
from google.genai.types import AutomaticFunctionCallingConfig, HttpOptions, HttpRetryOptions
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai._common import get_user_agent
from langchain_google_genai.chat_models import _is_gemini_3_or_later
from pydantic import PrivateAttr, SecretStr, model_validator

from app.agent_runtime.lease_bound_model import (
    LeaseBoundChatModelMixin,
    LeaseProviderTransportError,
    LeaseSendGuard,
    reject_transport_overrides,
)
from app.agent_runtime.lease_google_transport import (
    GOOGLE_ADAPTER_MAX_RETRIES,
    GOOGLE_CANONICAL_BASE_URL,
    GOOGLE_SDK_ATTEMPTS,
    ProviderTransportDescriptor,
    take_pending_google_transport,
    verify_and_freeze_google_transport,
)

_RANGES: tuple[tuple[str, float, float], ...] = (
    ("temperature", 0.0, 2.0),
    ("top_p", 0.0, 1.0),
    ("frequency_penalty", -2.0, 2.0),
    ("presence_penalty", -2.0, 2.0),
)


def _resolve_base_url(base_url: str | dict[str, Any] | None) -> str | None:
    if base_url is None or isinstance(base_url, str):
        return base_url
    if set(base_url) == {"api_endpoint"}:
        return str(base_url["api_endpoint"])
    raise ValueError("When providing base_url as a dict, it can only contain the api_endpoint key.")


class LeaseGoogleChatModel(LeaseBoundChatModelMixin, ChatGoogleGenerativeAI):
    """Gemini Developer API model bound to one lease guard and owned transport."""

    _lease_guard: LeaseSendGuard | None = PrivateAttr(default=None)
    _lease_transport: ProviderTransportDescriptor | None = PrivateAttr(default=None)

    @model_validator(mode="before")
    @classmethod
    def _lease_reject_raw_model_kwargs(cls, values: Any) -> Any:
        if isinstance(values, dict):
            nested = {"model_kwargs": values.get("model_kwargs")}
            reject_transport_overrides(nested, where="google construction")
        return values

    @model_validator(mode="after")
    def validate_environment(self) -> Self:
        """Replaces the installed validator; never calls ``super()``."""

        bundle = take_pending_google_transport()
        self._reject_unprovable_configuration()
        if "temperature" not in self.model_fields_set and _is_gemini_3_or_later(self.model):
            self.temperature = None
        for name, low, high in _RANGES:
            value = getattr(self, name)
            if value is not None and not low <= value <= high:
                raise ValueError(f"{name} must be in the range [{low}, {high}]")
        if self.top_k is not None and self.top_k <= 0:
            raise ValueError("top_k must be positive")

        additional_headers = self.additional_headers or {}
        self.default_metadata = tuple(additional_headers.items())
        _, user_agent = get_user_agent("ChatGoogleGenerativeAI")
        headers = {"user-agent": user_agent, **additional_headers}
        api_key = self.google_api_key
        if isinstance(api_key, SecretStr):
            api_key = api_key.get_secret_value()
        if not api_key:
            raise ValueError("API key required for Gemini Developer API.")

        http_options = HttpOptions(
            base_url=_resolve_base_url(self.base_url) or GOOGLE_CANONICAL_BASE_URL,
            api_version=self.api_version,
            headers=headers,
            httpx_client=bundle.sync_http,
            httpx_async_client=bundle.async_http,
            retry_options=HttpRetryOptions(attempts=GOOGLE_SDK_ATTEMPTS),
        )
        self.client = Client(api_key=api_key, http_options=http_options)
        bundle.cleanup.sdk_client = self.client
        self._client_cleanup = bundle.cleanup  # type: ignore[assignment]
        self._lease_transport = verify_and_freeze_google_transport(
            self.client, bundle, headers=headers
        )
        return self

    def _reject_unprovable_configuration(self) -> None:
        if self.max_retries != GOOGLE_ADAPTER_MAX_RETRIES:
            raise LeaseProviderTransportError("google adapter max_retries must be pinned to 1")
        if self.client is not None or self.client_args is not None:
            raise LeaseProviderTransportError("google caller client/client_args are forbidden")
        if getattr(self, "_use_vertexai", True) or self.credentials is not None:
            raise LeaseProviderTransportError("google auth path cannot retain the owned client")
        reject_transport_overrides({"model_kwargs": self.model_kwargs}, where="google construction")

    def _build_request_config(self, *args: Any, **kwargs: Any) -> Any:
        """Reject per-request transport/retry overrides before the installed builder."""

        if kwargs.get("max_retries") != GOOGLE_ADAPTER_MAX_RETRIES:
            raise LeaseProviderTransportError("google per-request max_retries must stay 1")
        retries = kwargs.pop("max_retries")
        reject_transport_overrides(kwargs, where="google request config")
        if "automatic_function_calling" in kwargs:
            raise LeaseProviderTransportError("google automatic function calling is forbidden")
        return super()._build_request_config(
            *args,
            max_retries=retries,
            automatic_function_calling=AutomaticFunctionCallingConfig(disable=True),
            **kwargs,
        )

    @property
    def lease_transport(self) -> ProviderTransportDescriptor:
        if self._lease_transport is None:
            raise LeaseProviderTransportError("google lease transport is not frozen")
        return self._lease_transport
