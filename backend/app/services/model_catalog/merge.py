"""Three-layer merge: providers + models + provider_models.

For ``openai/gpt-4o`` the resolver walks:
1. ``providers["openai"]``       — provider-level defaults (api_type, base url)
2. ``models["gpt-4o"]``          — model-level defaults (provider-agnostic)
3. ``provider_models["openai/gpt-4o"]`` — most specific overrides

Within step 2 we also fold in the curated ``aliases.json`` and ``overrides.json``
so the registry has stable canonical names and supports user-controlled
field-level overrides.

Source-priority for filling sparse fields when multiple normalizers contribute:
  curated > openrouter > litellm > llm_prices > pydantic_genai

Higher-priority sources win for *non-null* fields; null fields fall through to
the next source. This matches the additive/sparse rule the upstream
ai-model-list project uses (see ``NOTICES.md``).
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from .normalize import NORMALIZER_BY_SOURCE, ModelEntry
from .rules import apply_aliases, is_excluded, merge_override

logger = logging.getLogger(__name__)


# Source priority — first wins for non-null fields. Curated layer is applied
# separately as the final overlay; this list governs upstream-fetched sources.
SOURCE_PRIORITY: tuple[str, ...] = (
    "openrouter",
    "litellm",
    "llm_prices",
    "pydantic_genai",
)


def _to_jsonable(value: Any) -> Any:
    """Render ``Decimal`` → ``str`` so JSON dump preserves precision."""

    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    return value


def _entry_to_model_dict(entry: ModelEntry) -> dict[str, Any]:
    """Convert an entry to the catalog ``models`` row shape."""

    raw = asdict(entry)
    # Drop merge-only metadata + provider (provider lives in provider_models keys).
    for key in ("provider", "model_name"):
        raw.pop(key, None)
    return _strip_nulls(_to_jsonable(raw))


def _entry_to_provider_model_dict(entry: ModelEntry, model_ref: str) -> dict[str, Any]:
    """Convert an entry to a ``provider_models`` row shape."""

    raw = {
        "model_ref": model_ref,
        "provider_model_id": entry.model_name,
        "enabled": True,
        "context_window": entry.context_window,
        "max_output_tokens": entry.max_output_tokens,
        "cost_per_input_token": entry.cost_per_input_token,
        "cost_per_output_token": entry.cost_per_output_token,
        "rankings": entry.rankings,
        "sources": list(entry.sources),
    }
    return _strip_nulls(_to_jsonable(raw))


def _strip_nulls(value: Any) -> Any:
    """Recursively drop None values + empty dicts/lists."""

    if isinstance(value, dict):
        cleaned = {
            k: stripped
            for k, stripped in ((k, _strip_nulls(v)) for k, v in value.items())
            if stripped is not None
        }
        return cleaned or None
    if isinstance(value, list):
        cleaned = [
            stripped
            for stripped in (_strip_nulls(v) for v in value)
            if stripped is not None
        ]
        return cleaned if cleaned else None
    return value


def _pick(base: Any, overlay: Any) -> Any:
    """Return ``base`` if non-null/non-empty; otherwise ``overlay``."""

    if base is None:
        return overlay
    return base


def _merge_two_entries(base: ModelEntry, overlay: ModelEntry) -> ModelEntry:
    """Sparse merge: fill ``base`` fields where ``overlay`` has non-null values.

    ``base`` wins on conflict; ``overlay`` only fills the gaps. ``sources``
    accumulates so the catalog records every contributor.
    """

    merged_sources = list(dict.fromkeys([*base.sources, *overlay.sources]))
    rankings = None
    if base.rankings or overlay.rankings:
        rankings = {**(overlay.rankings or {}), **(base.rankings or {})}
    return ModelEntry(
        provider=base.provider or overlay.provider,
        model_name=base.model_name or overlay.model_name,
        display_name=base.display_name or overlay.display_name,
        context_window=_pick(base.context_window, overlay.context_window),
        max_output_tokens=_pick(base.max_output_tokens, overlay.max_output_tokens),
        cost_per_input_token=_pick(base.cost_per_input_token, overlay.cost_per_input_token),
        cost_per_output_token=_pick(base.cost_per_output_token, overlay.cost_per_output_token),
        supports_vision=_pick(base.supports_vision, overlay.supports_vision),
        supports_function_calling=_pick(
            base.supports_function_calling, overlay.supports_function_calling
        ),
        supports_reasoning=_pick(base.supports_reasoning, overlay.supports_reasoning),
        input_modalities=base.input_modalities or overlay.input_modalities,
        output_modalities=base.output_modalities or overlay.output_modalities,
        rankings=rankings,
        sources=merged_sources,
    )


def normalize_all(
    snapshots: dict[str, Any],
) -> dict[str, dict[tuple[str | None, str | None], ModelEntry]]:
    """Run every available normalizer; return ``{source: {(provider, model): ModelEntry}}``."""

    by_source: dict[str, dict[tuple[str | None, str | None], ModelEntry]] = {}
    for source_name, payload in snapshots.items():
        normalizer = NORMALIZER_BY_SOURCE.get(source_name)
        if normalizer is None:
            continue
        try:
            by_source[source_name] = normalizer(payload)
        except Exception:  # noqa: BLE001
            logger.exception("normalizer %s raised; skipping", source_name)
            by_source[source_name] = {}
    return by_source


def build_catalog(
    snapshots: dict[str, Any],
    providers_meta: dict[str, Any],
    curated: dict[str, Any] | None = None,
    *,
    updated_at: str | None = None,
) -> dict[str, Any]:
    """Build the full 3-layer catalog dict from upstream snapshots and curated policies.

    ``providers_meta`` is the static ``providers.json`` content.
    ``curated`` may carry ``aliases``, ``overrides``, and ``excluded`` keys.
    """

    curated = curated or {}
    aliases = curated.get("aliases", {}) or {}
    overrides = curated.get("overrides", {}) or {}
    excluded = curated.get("excluded", {}) or {}

    by_source = normalize_all(snapshots)

    # 1) Provider-keyed map: for every (provider, canonical_model), merge entries
    #    from all sources in priority order. ``provider/model`` rows still need
    #    canonical-name resolution so aliases collapse on the fly.
    by_provider_model: dict[tuple[str, str], ModelEntry] = {}
    for source_name in SOURCE_PRIORITY:
        entries = by_source.get(source_name, {})
        for (provider, raw_model_name), entry in entries.items():
            if not provider or not raw_model_name:
                continue
            canonical = apply_aliases(raw_model_name, aliases)
            if is_excluded(canonical, excluded):
                continue
            entry.model_name = canonical
            key = (provider, canonical)
            existing = by_provider_model.get(key)
            if existing is None:
                by_provider_model[key] = entry
            else:
                # ``existing`` came from a higher-priority source; treat it as
                # the base and let this row fill any gaps.
                by_provider_model[key] = _merge_two_entries(existing, entry)

    # 2) Model-level defaults: collapse provider_models into a model-key map by
    #    further merging across providers — the model-default row carries
    #    everything every provider agrees on. Conflicts simply prefer the
    #    earlier provider's row (alphabetical for determinism).
    models_layer: dict[str, ModelEntry] = {}
    for (_provider, canonical), entry in sorted(by_provider_model.items()):
        existing = models_layer.get(canonical)
        if existing is None:
            models_layer[canonical] = ModelEntry(
                provider=None,
                model_name=canonical,
                display_name=entry.display_name,
                context_window=entry.context_window,
                max_output_tokens=entry.max_output_tokens,
                cost_per_input_token=entry.cost_per_input_token,
                cost_per_output_token=entry.cost_per_output_token,
                supports_vision=entry.supports_vision,
                supports_function_calling=entry.supports_function_calling,
                supports_reasoning=entry.supports_reasoning,
                input_modalities=entry.input_modalities,
                output_modalities=entry.output_modalities,
                rankings=dict(entry.rankings) if entry.rankings else None,
                sources=list(entry.sources),
            )
        else:
            models_layer[canonical] = _merge_two_entries(existing, entry)

    # 3) Build the output dict with curated overrides applied as the final
    #    layer — first to ``models`` rows, then to ``provider_models`` rows.
    catalog: dict[str, Any] = {
        "version": 1,
        "updated_at": updated_at or _utc_iso(),
        "providers": _build_providers(providers_meta),
        "models": {},
        "provider_models": {},
    }

    for canonical, entry in sorted(models_layer.items()):
        model_dict = _entry_to_model_dict(entry)
        if model_dict is None:
            continue
        # Apply curated overrides at the model layer (key = canonical name).
        model_dict = merge_override(model_dict, overrides.get(canonical))
        catalog["models"][canonical] = model_dict

    for (provider, canonical), entry in sorted(by_provider_model.items()):
        pm_key = f"{provider}/{canonical}"
        pm_dict = _entry_to_provider_model_dict(entry, model_ref=canonical)
        if pm_dict is None:
            continue
        pm_dict = merge_override(pm_dict, overrides.get(pm_key))
        catalog["provider_models"][pm_key] = pm_dict

    return catalog


def _build_providers(providers_meta: dict[str, Any]) -> dict[str, Any]:
    """Pass through the static providers map, dropping any all-null rows."""

    out: dict[str, Any] = {}
    for slug, meta in providers_meta.items():
        if not isinstance(meta, dict):
            continue
        cleaned = _strip_nulls(meta)
        if cleaned:
            out[slug] = cleaned
    return out


def _utc_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
