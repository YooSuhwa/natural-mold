"""Static metadata accessors for well-known LLM models.

Loads the merged catalog produced by ``app.services.model_catalog_updater``
(``data/model_catalog/catalog.json``) and exposes a thin lookup API. The
catalog itself is built from public datasets — LiteLLM, OpenRouter,
llm-prices, and pydantic genai-prices — see ``NOTICES.md`` for attribution.

Backwards compatibility:
- ``enrich_model(model_name, base=None)`` keeps the M7 signature. The
  ``model_name`` argument may be a bare canonical name (``gpt-4o``) or a
  ``provider/model`` slug (``openai/gpt-4o``) — we split the latter and
  apply the 3-layer resolve so provider-specific overrides win.
- ``get_anthropic_models()`` still returns a list of canonical model names
  belonging to provider ``anthropic`` from the catalog.

If ``catalog.json`` is missing (first run before any cron has fired) we fall
back to the legacy single-file LiteLLM snapshot so the system stays
functional. The legacy path returns the same dict shape as the catalog-based
path.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from threading import RLock
from typing import Any

from app.services.model_catalog import resolve

logger = logging.getLogger(__name__)


_CATALOG_DIR = Path(__file__).parent.parent / "data" / "model_catalog"
_CATALOG_PATH = _CATALOG_DIR / "catalog.json"
# Legacy single-file snapshot retained for first-run + emergency rollback.
_LEGACY_CATALOG_PATH = Path(__file__).parent.parent / "data" / "litellm_model_catalog.json"

_catalog: dict[str, Any] | None = None
_legacy_catalog: dict[str, Any] | None = None
_anthropic_models: list[str] | None = None
_lock = RLock()


def _load_catalog() -> dict[str, Any]:
    """Lazy-load the merged catalog; cache for the worker lifetime."""

    global _catalog
    if _catalog is None:
        with _lock:
            if _catalog is None:
                if _CATALOG_PATH.exists():
                    with _CATALOG_PATH.open(encoding="utf-8") as f:
                        _catalog = json.load(f)
                else:
                    logger.warning(
                        "catalog.json not found at %s — model_metadata will rely on the "
                        "legacy LiteLLM snapshot until the catalog updater runs.",
                        _CATALOG_PATH,
                    )
                    _catalog = {}
    assert _catalog is not None
    return _catalog


def _load_legacy_catalog() -> dict[str, Any]:
    global _legacy_catalog
    if _legacy_catalog is None:
        with _lock:
            if _legacy_catalog is None:
                if _LEGACY_CATALOG_PATH.exists():
                    with _LEGACY_CATALOG_PATH.open(encoding="utf-8") as f:
                        _legacy_catalog = json.load(f)
                else:
                    _legacy_catalog = {}
    assert _legacy_catalog is not None
    return _legacy_catalog


def reset_catalog_cache() -> None:
    """Drop the cached catalog so the next call re-reads the file."""

    global _catalog, _anthropic_models, _legacy_catalog
    with _lock:
        _catalog = None
        _anthropic_models = None
        _legacy_catalog = None


def _split_provider_prefix(model_name: str) -> tuple[str | None, str]:
    """Accept ``provider/model`` and return the parts; bare names pass through."""

    if "/" in model_name:
        provider, name = model_name.split("/", 1)
        return provider, name
    return None, model_name


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def get_anthropic_models() -> list[str]:
    """Anthropic canonical model names (no /models endpoint). Lazy loaded.

    Resolution order:
    1. Catalog ``provider_models`` rows under the ``anthropic`` slug.
    2. Catalog ``models`` whose ``owned_by`` matches anthropic.
    3. Legacy LiteLLM snapshot rows whose ``provider == 'anthropic'``.
    """

    global _anthropic_models
    if _anthropic_models is not None:
        return _anthropic_models

    catalog = _load_catalog()
    if catalog:
        names = resolve.list_models_by_provider(catalog, "anthropic")
        if names:
            _anthropic_models = names
            return _anthropic_models

    # Fallback: legacy file.
    legacy = _load_legacy_catalog()
    _anthropic_models = sorted(
        k for k, v in legacy.items() if isinstance(v, dict) and v.get("provider") == "anthropic"
    )
    return _anthropic_models


def enrich_model(model_name: str, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Enrich a model dict with catalog-derived metadata.

    ``model_name`` accepts either a bare canonical name (``gpt-4o``) or a
    ``provider/model`` slug (``openai/gpt-4o``). The 3-layer resolve fills
    sparse fields (provider defaults → model defaults → provider override).

    ``base`` non-None values always win over catalog values (override semantics).
    """

    result = dict(base) if base else {}
    catalog = _load_catalog()

    if catalog:
        provider, name = _split_provider_prefix(model_name)
        resolved = resolve.resolve_model(catalog, provider, name)
        for key, value in resolved.items():
            if key == "provider":
                # Provider metadata stays under its own key so the caller can
                # decide whether to surface base_url / api_type / etc.
                if "provider_meta" not in result or result["provider_meta"] is None:
                    result["provider_meta"] = value
                continue
            if key in {"sources", "rankings", "provider_model_id"}:
                if key not in result or result[key] is None:
                    result[key] = value
                continue
            if key not in result or result[key] is None:
                result[key] = value
    else:
        # Legacy fallback (no catalog yet) — mirror the old field-name map.
        legacy = _load_legacy_catalog()
        meta = legacy.get(model_name, {})
        field_map = {
            "max_input_tokens": "context_window",
            "max_output_tokens": "max_output_tokens",
            "input_cost_per_token": "cost_per_input_token",
            "output_cost_per_token": "cost_per_output_token",
            "supports_vision": "supports_vision",
            "supports_function_calling": "supports_function_calling",
            "supports_reasoning": "supports_reasoning",
            "supported_modalities": "input_modalities",
            "supported_output_modalities": "output_modalities",
        }
        for src_key, dst_key in field_map.items():
            if (dst_key not in result or result[dst_key] is None) and src_key in meta:
                result[dst_key] = meta[src_key]

    if "display_name" not in result or result["display_name"] is None:
        # ``provider/model`` slugs render better as the bare canonical name.
        _, bare = _split_provider_prefix(model_name)
        result["display_name"] = bare or model_name

    return result
