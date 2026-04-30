"""3-layer merge tests — provider/model/provider_model layering, sparse fill, curated overrides."""

from __future__ import annotations

from decimal import Decimal

from app.services.model_catalog import merge, resolve


def _providers() -> dict:
    return {
        "openai": {
            "display_name": "OpenAI",
            "api_type": "openai",
            "default_base_url": "https://api.openai.com/v1",
        },
        "anthropic": {
            "display_name": "Anthropic",
            "api_type": "anthropic",
        },
    }


def test_merge_combines_sources_with_priority() -> None:
    """OpenRouter pricing wins; LiteLLM fills missing context window."""

    snapshots = {
        "litellm": {
            "openai/gpt-4o": {
                "litellm_provider": "openai",
                "max_input_tokens": 128000,
                "input_cost_per_token": 1e-05,  # less accurate
            }
        },
        "openrouter": {
            "data": [
                {
                    "id": "openai/gpt-4o",
                    "name": "GPT-4o",
                    "pricing": {"prompt": "0.000005", "completion": "0.00002"},
                    "context_length": 128000,
                    "top_provider": {"max_completion_tokens": 16384},
                }
            ]
        },
    }
    catalog = merge.build_catalog(snapshots, _providers(), curated={})

    pm = catalog["provider_models"]["openai/gpt-4o"]
    # OpenRouter is higher priority — its pricing wins.
    assert pm["cost_per_input_token"] == "0.000005"
    assert pm["cost_per_output_token"] == "0.00002"
    # Both sources contribute; merged sources reflect order seen.
    assert pm["sources"] == ["openrouter", "litellm"]
    # Context window agrees.
    assert pm["context_window"] == 128000

    # Top-level model row inherits the same merged data.
    m = catalog["models"]["gpt-4o"]
    assert m["context_window"] == 128000
    assert m["cost_per_input_token"] == "0.000005"


def test_merge_falls_back_to_lower_priority_for_missing_field() -> None:
    """LiteLLM fills max_output_tokens when OpenRouter omits it."""

    snapshots = {
        "openrouter": {
            "data": [{"id": "anthropic/claude-haiku-4-5", "pricing": {"prompt": "0.000001"}}]
        },
        "litellm": {
            "claude-haiku-4-5": {
                "litellm_provider": "anthropic",
                "max_output_tokens": 64000,
            }
        },
    }
    catalog = merge.build_catalog(snapshots, _providers(), curated={})
    pm = catalog["provider_models"]["anthropic/claude-haiku-4-5"]
    assert pm["max_output_tokens"] == 64000
    assert pm["cost_per_input_token"] == "0.000001"


def test_merge_applies_curated_aliases_and_excluded() -> None:
    """Aliases collapse variants; excluded ids are dropped."""

    snapshots = {
        "litellm": {
            "claude-3.5-haiku": {  # alias target = claude-3-5-haiku
                "litellm_provider": "anthropic",
                "max_input_tokens": 200000,
            },
            "search_api": {"litellm_provider": "openai"},  # excluded by default
        }
    }
    curated = {
        "aliases": {"claude-3.5-haiku": "claude-3-5-haiku"},
        "excluded": {"exact_model_ids": ["search_api"], "prefixes": []},
        "overrides": {},
    }
    catalog = merge.build_catalog(snapshots, _providers(), curated=curated)
    assert "claude-3-5-haiku" in catalog["models"]
    assert "claude-3.5-haiku" not in catalog["models"]
    assert "openai/search_api" not in catalog["provider_models"]


def test_merge_applies_overrides_at_provider_model_layer() -> None:
    snapshots = {
        "openrouter": {
            "data": [
                {
                    "id": "openai/gpt-4o",
                    "pricing": {"prompt": "0.000005"},
                }
            ]
        }
    }
    curated = {
        "overrides": {
            "openai/gpt-4o": {"cost_per_input_token": "0.000003"},
            "gpt-4o": {"display_name": "Custom Display"},
        },
    }
    catalog = merge.build_catalog(snapshots, _providers(), curated=curated)
    assert catalog["provider_models"]["openai/gpt-4o"]["cost_per_input_token"] == "0.000003"
    assert catalog["models"]["gpt-4o"]["display_name"] == "Custom Display"


def test_resolve_walks_three_layers_and_inherits_nulls() -> None:
    catalog = {
        "providers": {"openai": {"display_name": "OpenAI", "api_type": "openai"}},
        "models": {
            "gpt-4o": {
                "display_name": "GPT-4o",
                "context_window": 128000,
                "cost_per_input_token": "0.000005",
            }
        },
        "provider_models": {
            "openai/gpt-4o": {
                "model_ref": "gpt-4o",
                "provider_model_id": "gpt-4o-2024-11-20",
                "cost_per_output_token": "0.00002",
            }
        },
    }
    resolved = resolve.resolve_model(catalog, "openai", "gpt-4o")

    # Provider metadata stays under its own key.
    assert resolved["provider"]["api_type"] == "openai"
    # Model defaults flow in.
    assert resolved["context_window"] == 128000
    assert resolved["cost_per_input_token"] == "0.000005"
    # Provider override fills the missing field + supplies provider_model_id.
    assert resolved["cost_per_output_token"] == "0.00002"
    assert resolved["provider_model_id"] == "gpt-4o-2024-11-20"


def test_resolve_returns_empty_for_unknown_model() -> None:
    catalog = {"providers": {}, "models": {}, "provider_models": {}}
    assert resolve.resolve_model(catalog, "openai", "totally-fake") == {}


def test_list_models_by_provider_filters_and_dedupes() -> None:
    catalog = {
        "provider_models": {
            "openai/gpt-4o": {"model_ref": "gpt-4o"},
            "openai/gpt-4o-mini": {"model_ref": "gpt-4o-mini"},
            "anthropic/claude-haiku-4-5": {"model_ref": "claude-haiku-4-5"},
        }
    }
    assert resolve.list_models_by_provider(catalog, "openai") == ["gpt-4o", "gpt-4o-mini"]
    assert resolve.list_models_by_provider(catalog, "anthropic") == ["claude-haiku-4-5"]
    assert resolve.list_models_by_provider(catalog, "unknown") == []


def test_decimal_pricing_renders_as_string_not_scientific() -> None:
    """Build the catalog and ensure pricing values are JSON-safe (str)."""

    snapshots = {
        "litellm": {
            "claude-haiku-4-5": {
                "litellm_provider": "anthropic",
                "input_cost_per_token": Decimal("1e-06"),
            }
        }
    }
    catalog = merge.build_catalog(snapshots, _providers(), curated={})
    pm = catalog["provider_models"]["anthropic/claude-haiku-4-5"]
    assert isinstance(pm["cost_per_input_token"], str)
    # round-trip through Decimal stays exact
    assert Decimal(pm["cost_per_input_token"]) == Decimal("1e-06")
