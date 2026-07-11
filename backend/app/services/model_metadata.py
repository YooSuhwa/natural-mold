"""Generated metadata accessors for well-known LLM models.

Loads the merged catalog produced by ``app.services.model_catalog_updater``
(``data/model_catalog/catalog.json``) and exposes a thin lookup API. The
catalog itself is built from public datasets fetched at runtime.

Backwards compatibility:
- ``enrich_model(model_name, base=None)`` keeps the M7 signature. The
  ``model_name`` argument may be a bare canonical name (``gpt-4o``) or a
  ``provider/model`` slug (``openai/gpt-4o``) — we split the latter and
  apply the 3-layer resolve so provider-specific overrides win.
- ``get_anthropic_models()`` still returns a list of canonical model names
  belonging to provider ``anthropic`` from the catalog.

If ``catalog.json`` is missing (first run before the updater has fired), these
helpers return sparse defaults until the scheduled bootstrap refresh writes the
generated catalog.
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

_catalog: dict[str, Any] | None = None
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
                        "catalog.json not found at %s — model metadata will be sparse "
                        "until the catalog updater writes a generated catalog.",
                        _CATALOG_PATH,
                    )
                    _catalog = {}
    assert _catalog is not None  # noqa: S101 — set in the branch above (type narrowing)
    return _catalog


def reset_catalog_cache() -> None:
    """Drop the cached catalog so the next call re-reads the file."""

    global _catalog, _anthropic_models
    with _lock:
        _catalog = None
        _anthropic_models = None


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

    Resolution order: generated catalog ``provider_models`` rows under the
    ``anthropic`` slug. If the generated catalog is missing, returns an empty
    list until the catalog updater runs.
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

    _anthropic_models = []
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

    if "display_name" not in result or result["display_name"] is None:
        # ``provider/model`` slugs render better as the bare canonical name.
        _, bare = _split_provider_prefix(model_name)
        result["display_name"] = bare or model_name

    return result
