"""Per-source normalizer tests — ensure each upstream shape lands in ModelEntry."""

from __future__ import annotations

from decimal import Decimal

from app.services.model_catalog import normalize


def test_normalize_litellm_drops_sample_spec_and_keeps_known_models() -> None:
    raw = {
        "sample_spec": {"litellm_provider": "openai"},
        "claude-haiku-4-5": {
            "litellm_provider": "anthropic",
            "max_input_tokens": 200000,
            "max_output_tokens": 64000,
            "input_cost_per_token": 1e-06,
            "output_cost_per_token": 5e-06,
            "supports_vision": True,
            "supports_function_calling": True,
            "supports_reasoning": True,
        },
        "openai/gpt-4o": {
            "max_input_tokens": 128000,
            "input_cost_per_token": 5e-06,
        },
    }
    out = normalize.normalize_litellm(raw)

    # sample_spec dropped, two real entries remain.
    keys = sorted(out.keys())
    assert ("anthropic", "claude-haiku-4-5") in keys
    assert ("openai", "gpt-4o") in keys
    assert ("openai", "sample_spec") not in keys

    haiku = out[("anthropic", "claude-haiku-4-5")]
    assert haiku.context_window == 200000
    assert haiku.max_output_tokens == 64000
    assert haiku.cost_per_input_token == Decimal("1e-06")
    assert haiku.supports_vision is True
    assert haiku.sources == ["litellm"]


def test_normalize_openrouter_extracts_pricing_and_modalities() -> None:
    raw = {
        "data": [
            {
                "id": "anthropic/claude-haiku-4-5",
                "name": "Claude Haiku 4.5",
                "context_length": 200000,
                "architecture": {
                    "input_modalities": ["text", "image"],
                    "output_modalities": ["text"],
                },
                "top_provider": {"max_completion_tokens": 64000},
                "pricing": {"prompt": "0.000001", "completion": "0.000005"},
                "supported_parameters": ["tools", "reasoning"],
            },
            # Missing 'id' is dropped.
            {"name": "no-id"},
        ]
    }
    out = normalize.normalize_openrouter(raw)
    assert ("anthropic", "claude-haiku-4-5") in out

    entry = out[("anthropic", "claude-haiku-4-5")]
    assert entry.display_name == "Claude Haiku 4.5"
    assert entry.context_window == 200000
    assert entry.max_output_tokens == 64000
    assert entry.cost_per_input_token == Decimal("0.000001")
    assert entry.cost_per_output_token == Decimal("0.000005")
    assert entry.supports_vision is True  # image in input modalities
    assert entry.supports_function_calling is True  # tools in supported
    assert entry.supports_reasoning is True
    assert entry.sources == ["openrouter"]


def test_normalize_llm_prices_converts_per_million_to_per_token() -> None:
    raw = {
        "prices": [
            {
                "id": "claude-3-haiku",
                "vendor": "anthropic",
                "name": "Claude 3 Haiku",
                "input": 0.25,  # per million
                "output": 1.25,
            },
            {"id": "broken"},  # vendor missing → dropped
        ]
    }
    out = normalize.normalize_llm_prices(raw)
    assert ("anthropic", "claude-3-haiku") in out
    entry = out[("anthropic", "claude-3-haiku")]
    # 0.25 / 1_000_000 = 2.5e-07
    assert entry.cost_per_input_token == Decimal("0.25") / Decimal("1000000")
    assert entry.cost_per_output_token == Decimal("1.25") / Decimal("1000000")
    assert entry.sources == ["llm_prices"]


def test_normalize_pydantic_genai_handles_dict_and_list_prices() -> None:
    raw = [
        {
            "id": "anthropic",
            "models": [
                {
                    "id": "claude-haiku-4-5",
                    "name": "Claude Haiku 4.5",
                    "context_window": 200000,
                    "prices": {"input_mtok": 1, "output_mtok": 5},
                },
                {
                    "id": "claude-opus-4-6",
                    "name": "Claude Opus 4.6",
                    "context_window": 200000,
                    # Tiered pricing wrapped in a list — pick the unconstrained baseline.
                    "prices": [
                        {
                            "prices": {
                                "input_mtok": {
                                    "base": 5,
                                    "tiers": [{"start": 200000, "price": 10}],
                                },
                                "output_mtok": {"base": 25, "tiers": []},
                            }
                        },
                    ],
                },
            ],
        },
        # Garbage entry should be ignored, not crash.
        "junk",
    ]
    out = normalize.normalize_pydantic_genai(raw)

    haiku = out[("anthropic", "claude-haiku-4-5")]
    assert haiku.cost_per_input_token == Decimal("1") / Decimal("1000000")
    assert haiku.cost_per_output_token == Decimal("5") / Decimal("1000000")
    assert haiku.context_window == 200000

    opus = out[("anthropic", "claude-opus-4-6")]
    # base = 5 / 1M
    assert opus.cost_per_input_token == Decimal("5") / Decimal("1000000")
    assert opus.cost_per_output_token == Decimal("25") / Decimal("1000000")


def test_provider_alias_normalizes_known_variants() -> None:
    assert normalize._normalize_provider("Azure_OpenAI") == "azure_openai"
    assert normalize._normalize_provider("googleai") == "google_genai"
    assert normalize._normalize_provider("x-ai") == "xai"
    # Unknown values pass through lowercased so we never silently drop data.
    assert normalize._normalize_provider("WeirdNew") == "weirdnew"
    assert normalize._normalize_provider(None) is None
