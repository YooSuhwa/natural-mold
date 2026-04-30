"""JSON Schema validation tests for the merged catalog."""

from __future__ import annotations

from app.services.model_catalog import validate


def _minimal_valid() -> dict:
    return {
        "version": 1,
        "updated_at": "2026-04-29T12:00:00Z",
        "providers": {
            "openai": {"display_name": "OpenAI", "api_type": "openai"}
        },
        "models": {"gpt-4o": {"display_name": "GPT-4o", "context_window": 128000}},
        "provider_models": {
            "openai/gpt-4o": {"model_ref": "gpt-4o", "enabled": True}
        },
    }


def test_validate_accepts_minimal_catalog() -> None:
    errors = validate.validate_catalog(_minimal_valid())
    assert errors == []


def test_validate_rejects_missing_top_level_key() -> None:
    bad = _minimal_valid()
    bad.pop("models")
    errors = validate.validate_catalog(bad)
    assert any("models" in err for err in errors)


def test_validate_rejects_wrong_version_const() -> None:
    bad = _minimal_valid()
    bad["version"] = 2
    errors = validate.validate_catalog(bad)
    assert any("version" in err for err in errors)


def test_validate_rejects_provider_without_display_name() -> None:
    bad = _minimal_valid()
    bad["providers"]["openai"] = {"api_type": "openai"}
    errors = validate.validate_catalog(bad)
    assert any("display_name" in err for err in errors)


def test_validate_rejects_invalid_api_type_enum() -> None:
    bad = _minimal_valid()
    bad["providers"]["openai"]["api_type"] = "totally-fake-protocol"
    errors = validate.validate_catalog(bad)
    assert any("api_type" in err for err in errors)


def test_validate_accepts_decimal_pricing_as_string_or_number() -> None:
    """Catalog stores cost as str (Decimal-safe) or number — both must validate."""

    candidate = _minimal_valid()
    candidate["models"]["gpt-4o"]["cost_per_input_token"] = "0.000005"
    assert validate.validate_catalog(candidate) == []
    candidate["models"]["gpt-4o"]["cost_per_input_token"] = 0.000005
    assert validate.validate_catalog(candidate) == []
