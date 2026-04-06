"""Static metadata for well-known models. Costs are in USD per token."""

from __future__ import annotations

from decimal import Decimal

MODEL_METADATA: dict[str, dict] = {
    # OpenAI
    "gpt-4o": {
        "display_name": "GPT-4o",
        "context_window": 128000,
        "input_modalities": ["text", "image"],
        "output_modalities": ["text"],
        "cost_per_input_token": Decimal("0.0000025"),
        "cost_per_output_token": Decimal("0.00001"),
    },
    "gpt-4o-mini": {
        "display_name": "GPT-4o Mini",
        "context_window": 128000,
        "input_modalities": ["text", "image"],
        "output_modalities": ["text"],
        "cost_per_input_token": Decimal("0.00000015"),
        "cost_per_output_token": Decimal("0.0000006"),
    },
    "gpt-4.1": {
        "display_name": "GPT-4.1",
        "context_window": 1047576,
        "input_modalities": ["text", "image"],
        "output_modalities": ["text"],
        "cost_per_input_token": Decimal("0.000002"),
        "cost_per_output_token": Decimal("0.000008"),
    },
    "gpt-4.1-mini": {
        "display_name": "GPT-4.1 Mini",
        "context_window": 1047576,
        "input_modalities": ["text", "image"],
        "output_modalities": ["text"],
        "cost_per_input_token": Decimal("0.0000004"),
        "cost_per_output_token": Decimal("0.0000016"),
    },
    "gpt-4.1-nano": {
        "display_name": "GPT-4.1 Nano",
        "context_window": 1047576,
        "input_modalities": ["text", "image"],
        "output_modalities": ["text"],
        "cost_per_input_token": Decimal("0.0000001"),
        "cost_per_output_token": Decimal("0.0000004"),
    },
    "o3": {
        "display_name": "o3",
        "context_window": 200000,
        "input_modalities": ["text", "image"],
        "output_modalities": ["text"],
        "cost_per_input_token": Decimal("0.00001"),
        "cost_per_output_token": Decimal("0.00004"),
    },
    "o3-mini": {
        "display_name": "o3 Mini",
        "context_window": 200000,
        "input_modalities": ["text"],
        "output_modalities": ["text"],
        "cost_per_input_token": Decimal("0.0000011"),
        "cost_per_output_token": Decimal("0.0000044"),
    },
    "o4-mini": {
        "display_name": "o4 Mini",
        "context_window": 200000,
        "input_modalities": ["text", "image"],
        "output_modalities": ["text"],
        "cost_per_input_token": Decimal("0.0000011"),
        "cost_per_output_token": Decimal("0.0000044"),
    },
    # Anthropic
    "claude-opus-4-20250514": {
        "display_name": "Claude Opus 4",
        "context_window": 200000,
        "input_modalities": ["text", "image"],
        "output_modalities": ["text"],
        "cost_per_input_token": Decimal("0.000015"),
        "cost_per_output_token": Decimal("0.000075"),
    },
    "claude-sonnet-4-20250514": {
        "display_name": "Claude Sonnet 4",
        "context_window": 200000,
        "input_modalities": ["text", "image"],
        "output_modalities": ["text"],
        "cost_per_input_token": Decimal("0.000003"),
        "cost_per_output_token": Decimal("0.000015"),
    },
    "claude-haiku-4-20250514": {
        "display_name": "Claude Haiku 4",
        "context_window": 200000,
        "input_modalities": ["text", "image"],
        "output_modalities": ["text"],
        "cost_per_input_token": Decimal("0.0000008"),
        "cost_per_output_token": Decimal("0.000004"),
    },
    # Google
    "gemini-2.0-flash": {
        "display_name": "Gemini 2.0 Flash",
        "context_window": 1048576,
        "input_modalities": ["text", "image", "audio", "video"],
        "output_modalities": ["text", "image"],
        "cost_per_input_token": Decimal("0.0000001"),
        "cost_per_output_token": Decimal("0.0000004"),
    },
    "gemini-2.5-pro": {
        "display_name": "Gemini 2.5 Pro",
        "context_window": 1048576,
        "input_modalities": ["text", "image", "audio", "video"],
        "output_modalities": ["text"],
        "cost_per_input_token": Decimal("0.00000125"),
        "cost_per_output_token": Decimal("0.00001"),
    },
    "gemini-2.5-flash": {
        "display_name": "Gemini 2.5 Flash",
        "context_window": 1048576,
        "input_modalities": ["text", "image", "audio", "video"],
        "output_modalities": ["text"],
        "cost_per_input_token": Decimal("0.00000015"),
        "cost_per_output_token": Decimal("0.0000006"),
    },
}

# Anthropic static model list (no /models endpoint)
ANTHROPIC_MODELS = [
    "claude-opus-4-20250514",
    "claude-sonnet-4-20250514",
    "claude-haiku-4-20250514",
]


def enrich_model(model_name: str, base: dict | None = None) -> dict:
    """Enrich a model dict with static metadata if available."""
    meta = MODEL_METADATA.get(model_name, {})
    result = dict(base) if base else {}
    for key in (
        "display_name",
        "context_window",
        "input_modalities",
        "output_modalities",
        "cost_per_input_token",
        "cost_per_output_token",
    ):
        if (key not in result or result[key] is None) and key in meta:
            result[key] = meta[key]
    if "display_name" not in result or result["display_name"] is None:
        result["display_name"] = model_name
    return result
