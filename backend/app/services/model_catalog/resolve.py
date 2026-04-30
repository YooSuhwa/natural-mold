"""Catalog lookup helpers — apply the 3-layer merge to a single model query.

Used at request time (``enrich_model``) — the caller passes ``provider`` and
``model_name`` and gets back a flat dict with provider defaults + model
defaults + provider-specific overrides applied in order. Null fields in a
later layer inherit from the earlier layer so the result is always sparse.

Tiered lookup (mirrors ai-model-list's resolve_canonical_key intent):

  1. Try exact ``provider/model_name`` overlay — wins when the snapshot has
     its own pricing distinct from the base (e.g. ``gpt-5-2025-08-07`` lives
     in provider_models with different costs from ``gpt-5``).
  2. Canonicalize the input (strip nested prefixes / tier suffixes / alias
     lookup) and retry. Catches snapshots registered as aliases under a
     base canonical (which means the curator certified "same pricing").
  3. Fall back to the model-level defaults dict (no provider override).
"""

from __future__ import annotations

from typing import Any

from app.services.model_catalog.rules import canonicalize_model_id


def resolve_model(
    catalog: dict[str, Any],
    provider: str | None,
    model_name: str,
) -> dict[str, Any]:
    """Walk providers → models → provider_models in priority order.

    Returns an empty dict if neither the model nor any provider override
    knows the model. Provider metadata is *not* merged into the model dict —
    it stays under ``provider`` so callers can decide whether to surface it.
    """

    if not model_name:
        return {}

    providers = catalog.get("providers") or {}
    models = catalog.get("models") or {}
    provider_models = catalog.get("provider_models") or {}
    aliases = catalog.get("aliases") or {}

    # Resolve the canonical name once — used for both model-default lookup
    # and as the fallback overlay key.
    canonical = canonicalize_model_id(model_name, aliases)

    base: dict[str, Any] = {}

    # Step 1: provider defaults (only the metadata that can affect a model
    # request — base url etc. live alongside the result for the caller).
    provider_meta = providers.get(provider) if provider else None
    if isinstance(provider_meta, dict):
        base["provider"] = provider_meta

    # Step 2: model defaults — try canonical first, fall back to raw.
    for try_name in (canonical, model_name):
        model_defaults = models.get(try_name)
        if isinstance(model_defaults, dict):
            for key, value in model_defaults.items():
                if value is not None:
                    base[key] = value
            break

    # Step 3: provider-specific overrides (most specific layer wins).
    # Tiered: exact raw key wins (snapshot with distinct pricing), canonical
    # second (snapshot is alias-only — base pricing is correct).
    if provider:
        overlay_keys: list[str] = [f"{provider}/{model_name}"]
        if canonical != model_name:
            overlay_keys.append(f"{provider}/{canonical}")

        for key in overlay_keys:
            overlay = provider_models.get(key)
            if not isinstance(overlay, dict):
                continue
            for ok, ov in overlay.items():
                if ov is not None and ok not in {
                    "model_ref",
                    "provider_model_id",
                    "enabled",
                }:
                    base[ok] = ov
            # ``provider_model_id`` is the wire identifier — surface it under
            # a distinct key so the caller can use it without overwriting
            # ``model_name`` of the registry row.
            if overlay.get("provider_model_id"):
                base["provider_model_id"] = overlay["provider_model_id"]
            break  # First hit wins; raw exact takes precedence over canonical.

    return base


def list_models_by_provider(
    catalog: dict[str, Any], provider: str
) -> list[str]:
    """Return canonical model names registered for ``provider`` via ``provider_models``."""

    out: list[str] = []
    seen: set[str] = set()
    provider_models = catalog.get("provider_models") or {}
    prefix = f"{provider}/"
    for key, entry in provider_models.items():
        if not key.startswith(prefix):
            continue
        if not isinstance(entry, dict):
            continue
        canonical = entry.get("model_ref") or key.split("/", 1)[1]
        if canonical in seen:
            continue
        seen.add(canonical)
        out.append(canonical)
    out.sort()
    return out
