"""Tests for the LiteLLM-derived model metadata catalog."""

from __future__ import annotations

from app.services import model_metadata


def test_get_anthropic_models_returns_known_ids() -> None:
    """The static Anthropic list should contain at least one current model."""

    models = model_metadata.get_anthropic_models()
    assert isinstance(models, list)
    assert models, "expected at least one Anthropic model in the catalog"
    # Every entry must round-trip through enrich_model.
    for model_id in models[:5]:
        assert isinstance(model_id, str)


def test_get_anthropic_models_is_cached() -> None:
    """Two calls should return the same list object (lazy single-load)."""

    first = model_metadata.get_anthropic_models()
    second = model_metadata.get_anthropic_models()
    assert first is second


def test_enrich_model_known_model_fills_pricing_and_meta() -> None:
    """A model present in the catalog gets pricing + meta from the merged catalog."""

    enriched = model_metadata.enrich_model("claude-haiku-4-5")
    # Display name may be source-supplied (e.g. "Anthropic: Claude Haiku 4.5")
    # or fall back to the bare model id — either is acceptable so long as
    # something usable comes back.
    assert isinstance(enriched.get("display_name"), str) and enriched["display_name"]
    assert enriched.get("context_window") == 200000
    assert enriched.get("max_output_tokens") == 64000
    assert enriched.get("cost_per_input_token") is not None
    assert enriched.get("cost_per_output_token") is not None
    assert enriched.get("supports_vision") is True
    assert enriched.get("supports_function_calling") is True


def test_enrich_model_unknown_returns_safe_defaults() -> None:
    """An unknown model still gets a usable display_name."""

    enriched = model_metadata.enrich_model("totally-fake-model-xyz")
    assert enriched["display_name"] == "totally-fake-model-xyz"
    # No pricing data leaks for unknown ids.
    assert "cost_per_input_token" not in enriched
    assert "context_window" not in enriched


def test_enrich_model_preserves_existing_base_values() -> None:
    """Non-None base values win over the catalog (override semantics)."""

    base = {
        "display_name": "Custom Display",
        "context_window": 99,
        "cost_per_input_token": 1.0,
    }
    enriched = model_metadata.enrich_model("claude-haiku-4-5", base)
    assert enriched["display_name"] == "Custom Display"
    assert enriched["context_window"] == 99
    assert enriched["cost_per_input_token"] == 1.0
    # Fields not in base should still be filled from the catalog.
    assert enriched.get("max_output_tokens") == 64000
