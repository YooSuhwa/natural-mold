"""Discover available LLM models from a stored Credential.

The discovery layer is the bridge between the Credential domain (which owns
provider keys) and the Model catalog (which stores per-row pricing/metadata).
Each provider has a different way of listing models — OpenAI exposes
``/v1/models``, Anthropic uses a static catalog, Google returns generative
methods, OpenRouter ships pricing inline, and OpenAI-compatible endpoints are
the user's responsibility.

The dispatch by ``definition_key`` keeps the per-provider quirks isolated;
common output is the :class:`DiscoveredModel` dataclass with a ``source``
("openrouter" | "litellm" | "manual") indicating where the pricing came from.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.models.credential import Credential
from app.models.model import Model
from app.services.model_filtering import (
    is_custom_openai_endpoint,
    should_include_model,
)
from app.services.model_metadata import enrich_model, get_anthropic_models

logger = logging.getLogger(__name__)

# Per-request HTTP timeout. Chosen short enough to fail fast in the UI yet
# generous enough for first-call provider warm-ups.
_TIMEOUT = 15.0

# Provider source labels (kept loose to ease future provider additions).
PricingSource = Literal["openrouter", "litellm", "manual"]

_ANTHROPIC_DISCOVERY_FALLBACKS: tuple[dict[str, Any], ...] = (
    {
        "model_name": "claude-sonnet-4-6",
        "display_name": "Claude Sonnet 4.6",
        "context_window": 200000,
        "max_output_tokens": 64000,
        "cost_per_input_token": Decimal("0.000003"),
        "cost_per_output_token": Decimal("0.000015"),
        "supports_vision": True,
        "supports_function_calling": True,
    },
    {
        "model_name": "claude-haiku-4-5",
        "display_name": "Claude Haiku 4.5",
        "context_window": 200000,
        "max_output_tokens": 64000,
        "cost_per_input_token": Decimal("0.000001"),
        "cost_per_output_token": Decimal("0.000005"),
        "supports_vision": True,
        "supports_function_calling": True,
    },
)


@dataclass
class DiscoveredModel:
    """A single model surfaced from a discovery call.

    ``source`` records where the pricing/meta came from so the UI can show a
    badge: ``openrouter`` for inline OpenRouter pricing, ``litellm`` for
    catalog-enriched values, ``manual`` for "no pricing data available".
    """

    model_name: str
    display_name: str
    provider: str
    source: PricingSource
    context_window: int | None = None
    max_output_tokens: int | None = None
    cost_per_input_token: Decimal | None = None
    cost_per_output_token: Decimal | None = None
    input_modalities: list[str] | None = None
    output_modalities: list[str] | None = None
    supports_vision: bool | None = None
    supports_function_calling: bool | None = None
    supports_reasoning: bool | None = None
    already_registered: bool = False

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly representation for the API surface."""

        return {
            "model_name": self.model_name,
            "display_name": self.display_name,
            "provider": self.provider,
            "source": self.source,
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "cost_per_input_token": (
                str(self.cost_per_input_token)
                if self.cost_per_input_token is not None
                else None
            ),
            "cost_per_output_token": (
                str(self.cost_per_output_token)
                if self.cost_per_output_token is not None
                else None
            ),
            "input_modalities": self.input_modalities,
            "output_modalities": self.output_modalities,
            "supports_vision": self.supports_vision,
            "supports_function_calling": self.supports_function_calling,
            "supports_reasoning": self.supports_reasoning,
            "already_registered": self.already_registered,
        }


@dataclass
class _DispatchEntry:
    """Provider-specific dispatch metadata."""

    provider: str
    handler: Any
    fields: tuple[str, ...] = field(default_factory=tuple)


# Map ``credential.definition_key`` → discovery handler + provider label.
# Handlers are looked up below after their function definitions.


# -- Public API --------------------------------------------------------------


async def discover_from_credential(
    db: AsyncSession,
    credential: Credential,
) -> list[DiscoveredModel]:
    """Discover models reachable with the given Credential.

    Dispatches by ``credential.definition_key``. The Credential is decrypted
    via the service layer (handles ``__external__`` markers); the resulting
    payload is destructured per-provider. Raises ``ValueError`` if the
    Credential's definition is not a known LLM provider.
    """

    data = await credential_service.decrypt_with_external(credential.data_encrypted)
    entry = _DISPATCH.get(credential.definition_key)
    if entry is None:
        raise ValueError(
            f"definition '{credential.definition_key}' is not a discoverable LLM provider"
        )
    handler, provider = entry

    discovered = await handler(data)
    await _mark_already_registered(db, provider, discovered)
    return discovered


# -- Per-provider handlers ---------------------------------------------------


async def _discover_openai(data: dict[str, Any]) -> list[DiscoveredModel]:
    api_key = data.get("api_key")
    base_url = data.get("base_url") or "https://api.openai.com/v1"
    is_custom = is_custom_openai_endpoint(base_url)
    url = f"{base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
    items = response.json().get("data", []) or []

    out: list[DiscoveredModel] = []
    for item in items:
        model_id = item.get("id") or ""
        if not model_id or not should_include_model("openai", model_id, is_custom):
            continue
        enriched = enrich_model(model_id)
        out.append(_from_enriched("openai", model_id, enriched, is_custom))

    out.sort(key=lambda m: m.model_name)
    return out


async def _discover_anthropic(data: dict[str, Any]) -> list[DiscoveredModel]:
    """Anthropic has no /models endpoint — use the static catalog.

    The API key is *not* round-tripped against the messages endpoint here;
    that probe lives in the credential `test` recipe to keep discovery cheap
    and side-effect-free (the live test would consume tokens).
    """

    out: list[DiscoveredModel] = []
    model_ids = get_anthropic_models()
    if not model_ids:
        return [
            DiscoveredModel(
                provider="anthropic",
                source="litellm",
                input_modalities=["text", "image"],
                output_modalities=["text"],
                **item,
            )
            for item in _ANTHROPIC_DISCOVERY_FALLBACKS
        ]

    for model_id in model_ids:
        enriched = enrich_model(model_id)
        out.append(_from_enriched("anthropic", model_id, enriched, False))
    out.sort(key=lambda m: m.model_name)
    return out


async def _discover_google(data: dict[str, Any]) -> list[DiscoveredModel]:
    api_key = data.get("api_key")
    base_url = (
        data.get("base_url") or "https://generativelanguage.googleapis.com/v1beta"
    )
    url = f"{base_url.rstrip('/')}/models"
    params: dict[str, str] = {}
    if api_key:
        params["key"] = api_key

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
    items = response.json().get("models", []) or []

    out: list[DiscoveredModel] = []
    for item in items:
        methods = item.get("supportedGenerationMethods") or []
        if "generateContent" not in methods:
            continue
        full_name = item.get("name") or ""
        model_id = full_name.removeprefix("models/")
        if not model_id:
            continue
        enriched = enrich_model(model_id)
        # Display name from the Google response wins if the catalog is silent.
        display = enriched.get("display_name") or item.get("displayName") or model_id
        context = enriched.get("context_window") or item.get("inputTokenLimit")
        max_output = enriched.get("max_output_tokens") or item.get("outputTokenLimit")
        cost_in = _to_decimal(enriched.get("cost_per_input_token"))
        cost_out = _to_decimal(enriched.get("cost_per_output_token"))
        source: PricingSource = (
            "litellm" if (cost_in is not None or cost_out is not None) else "manual"
        )
        out.append(
            DiscoveredModel(
                model_name=model_id,
                display_name=display,
                provider="google_genai",
                source=source,
                context_window=context,
                max_output_tokens=max_output,
                cost_per_input_token=cost_in,
                cost_per_output_token=cost_out,
                input_modalities=enriched.get("input_modalities"),
                output_modalities=enriched.get("output_modalities"),
                supports_vision=enriched.get("supports_vision"),
                supports_function_calling=enriched.get("supports_function_calling"),
                supports_reasoning=enriched.get("supports_reasoning"),
            )
        )
    out.sort(key=lambda m: m.model_name)
    return out


async def _discover_openrouter(data: dict[str, Any]) -> list[DiscoveredModel]:
    """OpenRouter ships pricing inline; trust it but fall back to the catalog."""

    api_key = data.get("api_key")
    base_url = data.get("base_url") or "https://openrouter.ai/api/v1"
    url = f"{base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
    items = response.json().get("data", []) or []

    out: list[DiscoveredModel] = []
    for item in items:
        model_id = item.get("id") or ""
        if not model_id:
            continue
        if not should_include_model("openrouter", model_id, False):
            continue

        pricing = item.get("pricing") or {}
        arch = item.get("architecture") or {}
        top = item.get("top_provider") or {}
        supported = item.get("supported_parameters") or []
        input_mod = arch.get("input_modalities")
        output_mod = arch.get("output_modalities")

        cost_in = _to_decimal(pricing.get("prompt"))
        cost_out = _to_decimal(pricing.get("completion"))

        if cost_in is None and cost_out is None:
            # Fall back to the catalog when OpenRouter omits pricing.
            enriched = enrich_model(model_id)
            cost_in = _to_decimal(enriched.get("cost_per_input_token"))
            cost_out = _to_decimal(enriched.get("cost_per_output_token"))
            source: PricingSource = (
                "litellm" if (cost_in is not None or cost_out is not None) else "manual"
            )
        else:
            source = "openrouter"

        out.append(
            DiscoveredModel(
                model_name=model_id,
                display_name=item.get("name") or model_id,
                provider="openrouter",
                source=source,
                context_window=item.get("context_length"),
                max_output_tokens=top.get("max_completion_tokens"),
                cost_per_input_token=cost_in,
                cost_per_output_token=cost_out,
                input_modalities=input_mod,
                output_modalities=output_mod,
                supports_vision="image" in (input_mod or []) if input_mod else None,
                supports_function_calling="tools" in supported if supported else None,
                supports_reasoning="reasoning" in supported if supported else None,
            )
        )

    out.sort(key=lambda m: m.model_name)
    return out


async def _discover_openai_compatible(data: dict[str, Any]) -> list[DiscoveredModel]:
    """Catch-all for self-hosted / OpenAI-compatible deployments.

    No filtering is applied (``is_custom_api=True``) and the catalog is only
    consulted opportunistically — most local deployments ship custom model
    IDs the catalog has never heard of.
    """

    base_url = data.get("base_url")
    if not base_url:
        raise ValueError("openai_compatible credential requires a base_url")

    api_key = data.get("api_key")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    primary_url = f"{base_url.rstrip('/')}/models"

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            response = await client.get(primary_url, headers=headers)
            response.raise_for_status()
            items = response.json().get("data", []) or []
        except httpx.HTTPStatusError:
            # Ollama-style fallback: /api/tags returns ``{"models": [{"name": ...}]}``.
            stripped = base_url.rstrip("/").removesuffix("/v1")
            fallback_url = f"{stripped}/api/tags"
            response = await client.get(fallback_url, headers=headers)
            response.raise_for_status()
            items = [
                {"id": m.get("name") or ""}
                for m in (response.json().get("models", []) or [])
            ]

    out: list[DiscoveredModel] = []
    for item in items:
        model_id = item.get("id") or item.get("name") or ""
        if not model_id:
            continue
        enriched = enrich_model(model_id)
        cost_in = _to_decimal(enriched.get("cost_per_input_token"))
        cost_out = _to_decimal(enriched.get("cost_per_output_token"))
        source: PricingSource = (
            "litellm" if (cost_in is not None or cost_out is not None) else "manual"
        )
        out.append(
            DiscoveredModel(
                model_name=model_id,
                display_name=enriched.get("display_name") or model_id,
                provider="openai_compatible",
                source=source,
                context_window=enriched.get("context_window"),
                max_output_tokens=enriched.get("max_output_tokens"),
                cost_per_input_token=cost_in,
                cost_per_output_token=cost_out,
                input_modalities=enriched.get("input_modalities"),
                output_modalities=enriched.get("output_modalities"),
                supports_vision=enriched.get("supports_vision"),
                supports_function_calling=enriched.get("supports_function_calling"),
                supports_reasoning=enriched.get("supports_reasoning"),
            )
        )

    out.sort(key=lambda m: m.model_name)
    return out


# -- Helpers -----------------------------------------------------------------


def _to_decimal(value: Any) -> Decimal | None:
    """Coerce numbers/strings to ``Decimal`` while accepting None/empty."""

    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (TypeError, ValueError):
        return None


def _from_enriched(
    provider: str,
    model_id: str,
    enriched: dict[str, Any],
    is_custom: bool,
) -> DiscoveredModel:
    cost_in = _to_decimal(enriched.get("cost_per_input_token"))
    cost_out = _to_decimal(enriched.get("cost_per_output_token"))
    source: PricingSource = (
        "litellm" if (cost_in is not None or cost_out is not None) else "manual"
    )
    return DiscoveredModel(
        model_name=model_id,
        display_name=enriched.get("display_name") or model_id,
        provider=provider,
        source=source,
        context_window=enriched.get("context_window"),
        max_output_tokens=enriched.get("max_output_tokens"),
        cost_per_input_token=cost_in,
        cost_per_output_token=cost_out,
        input_modalities=enriched.get("input_modalities"),
        output_modalities=enriched.get("output_modalities"),
        supports_vision=enriched.get("supports_vision"),
        supports_function_calling=enriched.get("supports_function_calling"),
        supports_reasoning=enriched.get("supports_reasoning"),
    )


async def _mark_already_registered(
    db: AsyncSession,
    provider: str,
    discovered: list[DiscoveredModel],
) -> None:
    """Set ``already_registered`` on any model that already lives in ``models``.

    Single IN query keyed by ``(provider, model_name)`` to avoid the obvious
    N+1 trap.
    """

    if not discovered:
        return
    names = [m.model_name for m in discovered]
    result = await db.execute(
        select(Model.model_name).where(
            Model.provider == provider, Model.model_name.in_(names)
        )
    )
    seen = {row[0] for row in result.all()}
    for model in discovered:
        if model.model_name in seen:
            model.already_registered = True


# -- Dispatch table ----------------------------------------------------------


_DISPATCH: dict[str, tuple[Any, str]] = {
    "openai": (_discover_openai, "openai"),
    "anthropic": (_discover_anthropic, "anthropic"),
    "google_genai": (_discover_google, "google_genai"),
    "openrouter": (_discover_openrouter, "openrouter"),
    "openai_compatible": (_discover_openai_compatible, "openai_compatible"),
}


__all__ = [
    "DiscoveredModel",
    "PricingSource",
    "discover_from_credential",
]
