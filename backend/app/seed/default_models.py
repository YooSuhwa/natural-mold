from __future__ import annotations

from decimal import Decimal

DEFAULT_MODELS = [
    {
        "provider": "openai",
        "model_name": "gpt-4o",
        "display_name": "GPT-4o",
        "is_default": True,
        "cost_per_input_token": Decimal("0.0000025"),
        "cost_per_output_token": Decimal("0.00001"),
    },
    {
        "provider": "anthropic",
        "model_name": "claude-sonnet-4-20250514",
        "display_name": "Claude Sonnet 4",
        "is_default": False,
        "cost_per_input_token": Decimal("0.000003"),
        "cost_per_output_token": Decimal("0.000015"),
    },
    {
        "provider": "google",
        "model_name": "gemini-2.0-flash",
        "display_name": "Gemini 2.0 Flash",
        "is_default": False,
        "cost_per_input_token": Decimal("0.0000001"),
        "cost_per_output_token": Decimal("0.0000004"),
    },
]
