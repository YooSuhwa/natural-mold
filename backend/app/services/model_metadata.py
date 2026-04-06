"""Static metadata for well-known models. Costs are in USD per token.
Source: LiteLLM model_prices_and_context_window.json (MIT license)."""

from __future__ import annotations

import json
from pathlib import Path

_CATALOG_PATH = Path(__file__).parent.parent / "data" / "litellm_model_catalog.json"
_catalog: dict | None = None


def _get_catalog() -> dict:
    global _catalog
    if _catalog is None:
        with open(_CATALOG_PATH) as f:
            _catalog = json.load(f)
    return _catalog


# Anthropic static model list (no /models endpoint)
ANTHROPIC_MODELS = [k for k, v in _get_catalog().items() if v.get("provider") == "anthropic"]


def enrich_model(model_name: str, base: dict | None = None) -> dict:
    """Enrich a model dict with static metadata if available."""
    catalog = _get_catalog()
    meta = catalog.get(model_name, {})
    result = dict(base) if base else {}

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
        result["display_name"] = model_name

    return result
