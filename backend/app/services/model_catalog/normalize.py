"""Per-source normalizers: raw upstream payload → ``ModelEntry`` dataclass.

Each upstream dataset uses a different identifier scheme, pricing unit, and
key shape:

- LiteLLM: USD per token, key is ``provider/model`` or bare ``model``,
  ``litellm_provider`` carries the provider slug.
- OpenRouter: USD per token (string), pricing object has ``prompt`` / ``completion``,
  ``id`` is always ``provider/model``.
- llm-prices: USD per *million* tokens (numeric), id is bare,
  ``vendor`` carries the provider slug.
- pydantic genai-prices: structured per-provider list with a ``models`` array
  containing match rules + ``prices.input_mtok`` / ``prices.output_mtok``.

Output is a ``dict[(provider, model_name), ModelEntry]`` so the merge step
can keep provider+model collisions distinct.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

# Map upstream provider slugs onto the canonical Moldy provider keys defined
# in ``providers.json``. Anything missing flows through unchanged so we never
# silently drop data — the merge step will simply skip unknown providers if
# strict filtering is requested elsewhere.
_PROVIDER_ALIASES: dict[str, str] = {
    "anthropic": "anthropic",
    "openai": "openai",
    "openai_chat": "openai",
    "openai_chat_completion": "openai",
    "azure": "azure_openai",
    "azure_openai": "azure_openai",
    "azure_ai": "azure_openai",
    "google": "google",
    "google_genai": "google_genai",
    "googleai": "google_genai",
    "google_ai_studio": "google_genai",
    "gemini": "google_genai",
    "vertex_ai": "google",
    "vertex_ai-language-models": "google",
    "openrouter": "openrouter",
    "groq": "groq",
    "mistral": "mistral",
    "mistralai": "mistral",
    "cohere": "cohere",
    "cohere_chat": "cohere",
    "deepseek": "deepseek",
    "x-ai": "xai",
    "xai": "xai",
    "amazon": "amazon",
    "bedrock": "amazon",
    "meta": "meta",
    "meta-llama": "meta",
}


def _normalize_provider(raw: str | None) -> str | None:
    if not raw:
        return None
    lowered = raw.lower().strip()
    return _PROVIDER_ALIASES.get(lowered, lowered)


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError):
        return None


@dataclass
class ModelEntry:
    """Common shape every normalizer emits.

    All fields are optional/sparse — missing data is ``None`` so the merge
    step can additively fill from later sources. ``cost_per_input_token`` /
    ``cost_per_output_token`` are USD *per token* (not per-million) to stay
    compatible with the existing ``Model`` ORM column semantics.
    """

    provider: str | None = None
    model_name: str | None = None
    display_name: str | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None
    cost_per_input_token: Decimal | None = None
    cost_per_output_token: Decimal | None = None
    supports_vision: bool | None = None
    supports_function_calling: bool | None = None
    supports_reasoning: bool | None = None
    input_modalities: list[str] | None = None
    output_modalities: list[str] | None = None
    rankings: dict[str, float] | None = None
    sources: list[str] = field(default_factory=list)

    @property
    def key(self) -> tuple[str | None, str | None]:
        return (self.provider, self.model_name)


# --------------------------------------------------------------------------
# Per-source normalizers
# --------------------------------------------------------------------------


def _split_litellm_id(model_id: str) -> tuple[str | None, str]:
    """LiteLLM keys are either ``provider/model`` or bare ``model``."""

    if "/" in model_id:
        provider, model = model_id.split("/", 1)
        return provider, model
    return None, model_id


def normalize_litellm(raw: dict[str, Any]) -> dict[tuple[str | None, str | None], ModelEntry]:
    """Convert the LiteLLM ``model_prices_and_context_window.json`` snapshot.

    The first row (``sample_spec``) is a documentation stub — drop it.
    """

    out: dict[tuple[str | None, str | None], ModelEntry] = {}
    if not isinstance(raw, dict):
        return out

    for key, meta in raw.items():
        if key in {"sample_spec", "search_api", "model_router"}:
            continue
        if not isinstance(meta, dict):
            continue

        prefix_provider, model_name = _split_litellm_id(key)
        provider_raw = meta.get("litellm_provider") or prefix_provider
        provider = _normalize_provider(provider_raw)
        if not provider or not model_name:
            continue

        entry = ModelEntry(
            provider=provider,
            model_name=model_name,
            display_name=model_name,
            context_window=_safe_int(meta.get("max_input_tokens") or meta.get("max_tokens")),
            max_output_tokens=_safe_int(meta.get("max_output_tokens")),
            cost_per_input_token=_to_decimal(meta.get("input_cost_per_token")),
            cost_per_output_token=_to_decimal(meta.get("output_cost_per_token")),
            supports_vision=_safe_bool(meta.get("supports_vision")),
            supports_function_calling=_safe_bool(meta.get("supports_function_calling")),
            supports_reasoning=_safe_bool(meta.get("supports_reasoning")),
            input_modalities=_safe_list(meta.get("supported_modalities")),
            output_modalities=_safe_list(meta.get("supported_output_modalities")),
            sources=["litellm"],
        )
        out[entry.key] = entry
    return out


def normalize_openrouter(raw: Any) -> dict[tuple[str | None, str | None], ModelEntry]:
    """Convert the OpenRouter ``/models`` payload.

    OpenRouter wraps the array under ``{"data": [...]}``. Pricing values are
    string-encoded USD per token; OpenRouter is the inline pricing source.
    """

    out: dict[tuple[str | None, str | None], ModelEntry] = {}
    items = raw.get("data") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return out

    for item in items:
        if not isinstance(item, dict):
            continue

        ident = item.get("id") or ""
        if "/" not in ident:
            continue
        owner_raw, model_name = ident.split("/", 1)

        # OpenRouter exposes the upstream owner — surface that as the provider
        # so the merge step can join with provider-specific rows from other
        # sources. The ``openrouter`` provider itself owns the route, but the
        # canonical model lives under its real provider.
        provider = _normalize_provider(owner_raw)
        if not provider or not model_name:
            continue

        pricing = item.get("pricing") or {}
        arch = item.get("architecture") or {}
        top = item.get("top_provider") or {}
        supported = item.get("supported_parameters") or []

        input_mod = _safe_list(arch.get("input_modalities"))
        output_mod = _safe_list(arch.get("output_modalities"))

        entry = ModelEntry(
            provider=provider,
            model_name=model_name,
            display_name=item.get("name") or model_name,
            context_window=_safe_int(item.get("context_length")),
            max_output_tokens=_safe_int(top.get("max_completion_tokens")),
            cost_per_input_token=_to_decimal(pricing.get("prompt")),
            cost_per_output_token=_to_decimal(pricing.get("completion")),
            supports_vision=("image" in input_mod) if input_mod else None,
            supports_function_calling=("tools" in supported) if supported else None,
            supports_reasoning=("reasoning" in supported) if supported else None,
            input_modalities=input_mod or None,
            output_modalities=output_mod or None,
            sources=["openrouter"],
        )
        out[entry.key] = entry
    return out


def normalize_llm_prices(raw: Any) -> dict[tuple[str | None, str | None], ModelEntry]:
    """Convert the simonw/llm-prices ``current.json`` snapshot.

    Prices are USD per *million* tokens — divide by 1e6 to align with the
    per-token convention used elsewhere.
    """

    out: dict[tuple[str | None, str | None], ModelEntry] = {}
    items = raw.get("prices") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return out

    one_m = Decimal("1000000")

    for item in items:
        if not isinstance(item, dict):
            continue
        provider = _normalize_provider(item.get("vendor"))
        model_name = item.get("id")
        if not provider or not model_name:
            continue

        input_mtok = _to_decimal(item.get("input"))
        output_mtok = _to_decimal(item.get("output"))

        entry = ModelEntry(
            provider=provider,
            model_name=model_name,
            display_name=item.get("name") or model_name,
            cost_per_input_token=(input_mtok / one_m) if input_mtok is not None else None,
            cost_per_output_token=(output_mtok / one_m) if output_mtok is not None else None,
            sources=["llm_prices"],
        )
        out[entry.key] = entry
    return out


def _extract_pydantic_prices(prices: Any) -> tuple[Decimal | None, Decimal | None]:
    """pydantic genai-prices uses two shapes:

    - ``{"input_mtok": <num|tier_obj>, "output_mtok": <num|tier_obj>, ...}``
    - ``[{"prices": {...}}, {"constraint": {...}, "prices": {...}}, ...]``

    Pick the unconstrained baseline (first list entry) or the dict directly.
    Tier objects (``{"base": x, "tiers": [...]}``) reduce to ``base``.
    """

    if isinstance(prices, list):
        # First unconstrained pricing wins; otherwise the first entry.
        chosen: dict[str, Any] | None = None
        for item in prices:
            if not isinstance(item, dict):
                continue
            inner = item.get("prices")
            if isinstance(inner, dict):
                if "constraint" not in item:
                    chosen = inner
                    break
                if chosen is None:
                    chosen = inner
        prices = chosen or {}

    if not isinstance(prices, dict):
        return None, None

    def _coerce(value: Any) -> Decimal | None:
        if isinstance(value, dict):
            return _to_decimal(value.get("base"))
        return _to_decimal(value)

    return _coerce(prices.get("input_mtok")), _coerce(prices.get("output_mtok"))


def normalize_pydantic_genai(
    raw: Any,
) -> dict[tuple[str | None, str | None], ModelEntry]:
    """Convert the pydantic genai-prices snapshot.

    Top-level shape: ``[{id, name, models: [{id, name, context_window, prices}]}]``.
    See ``_extract_pydantic_prices`` for the per-model price shape variants.
    """

    out: dict[tuple[str | None, str | None], ModelEntry] = {}
    if not isinstance(raw, list):
        return out

    one_m = Decimal("1000000")

    for provider_item in raw:
        if not isinstance(provider_item, dict):
            continue
        provider = _normalize_provider(provider_item.get("id"))
        if not provider:
            continue

        models = provider_item.get("models") or []
        if not isinstance(models, list):
            continue

        for model in models:
            if not isinstance(model, dict):
                continue
            model_name = model.get("id")
            if not model_name:
                continue

            input_mtok, output_mtok = _extract_pydantic_prices(model.get("prices"))

            entry = ModelEntry(
                provider=provider,
                model_name=model_name,
                display_name=model.get("name") or model_name,
                context_window=_safe_int(model.get("context_window")),
                cost_per_input_token=(
                    input_mtok / one_m if input_mtok is not None else None
                ),
                cost_per_output_token=(
                    output_mtok / one_m if output_mtok is not None else None
                ),
                sources=["pydantic_genai"],
            )
            out[entry.key] = entry
    return out


# Dispatch table consumed by the merge step.
NORMALIZER_BY_SOURCE = {
    "litellm": normalize_litellm,
    "openrouter": normalize_openrouter,
    "llm_prices": normalize_llm_prices,
    "pydantic_genai": normalize_pydantic_genai,
}


# --------------------------------------------------------------------------
# Tiny helpers — type coercion that swallows malformed values safely.
# --------------------------------------------------------------------------


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return bool(value)


def _safe_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return None
