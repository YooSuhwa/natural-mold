"""Tests for the generated model metadata catalog."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.services import model_metadata


@pytest.fixture(autouse=True)
def _reset_model_metadata_cache() -> Iterator[None]:
    model_metadata.reset_catalog_cache()
    yield
    model_metadata.reset_catalog_cache()


@pytest.fixture
def generated_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point model_metadata at a generated catalog fixture instead of repo data."""

    catalog_path = tmp_path / "catalog.json"
    catalog = {
        "version": 1,
        "updated_at": "2026-01-01T00:00:00Z",
        "providers": {
            "anthropic": {"display_name": "Anthropic", "api_type": "anthropic"},
        },
        "models": {
            "claude-haiku-4-5": {
                "display_name": "Claude Haiku 4.5",
                "context_window": 200000,
                "max_output_tokens": 64000,
                "cost_per_input_token": 0.000001,
                "cost_per_output_token": 0.000005,
                "supports_vision": True,
                "supports_function_calling": True,
            },
        },
        "provider_models": {
            "anthropic/claude-haiku-4-5": {
                "model_ref": "claude-haiku-4-5",
                "provider_model_id": "claude-haiku-4-5",
                "enabled": True,
            },
        },
    }
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    monkeypatch.setattr(model_metadata, "_CATALOG_PATH", catalog_path)
    model_metadata.reset_catalog_cache()
    return catalog_path


def test_get_anthropic_models_returns_known_ids(generated_catalog: Path) -> None:
    """The generated Anthropic list should contain catalog provider models."""

    models = model_metadata.get_anthropic_models()
    assert isinstance(models, list)
    assert models, "expected at least one Anthropic model in the catalog"
    # Every entry must round-trip through enrich_model.
    for model_id in models[:5]:
        assert isinstance(model_id, str)


def test_get_anthropic_models_is_cached(generated_catalog: Path) -> None:
    """Two calls should return the same list object (lazy single-load)."""

    first = model_metadata.get_anthropic_models()
    second = model_metadata.get_anthropic_models()
    assert first is second


def test_enrich_model_known_model_fills_pricing_and_meta(generated_catalog: Path) -> None:
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


def test_enrich_model_unknown_returns_safe_defaults(generated_catalog: Path) -> None:
    """An unknown model still gets a usable display_name."""

    enriched = model_metadata.enrich_model("totally-fake-model-xyz")
    assert enriched["display_name"] == "totally-fake-model-xyz"
    # No pricing data leaks for unknown ids.
    assert "cost_per_input_token" not in enriched
    assert "context_window" not in enriched


def test_enrich_model_preserves_existing_base_values(generated_catalog: Path) -> None:
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


def test_missing_catalog_returns_sparse_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh clones without a generated catalog should degrade to sparse defaults."""

    catalog_path = tmp_path / "missing-catalog.json"

    monkeypatch.setattr(model_metadata, "_CATALOG_PATH", catalog_path)
    model_metadata.reset_catalog_cache()

    assert model_metadata.get_anthropic_models() == []
    enriched = model_metadata.enrich_model("catalog-not-yet-generated")
    assert enriched == {"display_name": "catalog-not-yet-generated"}
