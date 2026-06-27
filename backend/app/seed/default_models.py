from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# ``context_window`` is the published max INPUT token limit per model. It is the
# single source of truth for both the chat context gauge and deepagents'
# auto-summarization threshold (trigger = 0.85 × context_window), injected into
# the LangChain model profile by ``model_factory._apply_context_window_profile``.
# Operators set this per row in the model UI for custom / gateway models.
DEFAULT_MODELS = [
    {
        "provider": "anthropic",
        "model_name": "claude-sonnet-4-6",
        "display_name": "Claude Sonnet 4.6",
        "is_default": True,
        "cost_per_input_token": Decimal("0.000003"),
        "cost_per_output_token": Decimal("0.000015"),
        "context_window": 200000,  # Anthropic Claude standard API context (1M is beta-gated)
    },
    {
        "provider": "openai",
        "model_name": "gpt-5.4-mini",
        "display_name": "GPT-5.4 Mini",
        "is_default": False,
        "cost_per_input_token": Decimal("0.0000025"),
        "cost_per_output_token": Decimal("0.00001"),
        "context_window": 400000,  # OpenAI GPT-5 family context window
    },
    {
        "provider": "openai",
        "model_name": "gpt-5.4",
        "display_name": "GPT-5.4",
        "is_default": False,
        "cost_per_input_token": Decimal("0.0000025"),
        "cost_per_output_token": Decimal("0.000015"),
        "context_window": 400000,  # OpenAI GPT-5 family context window
    },
    {
        "provider": "google",
        "model_name": "gemini-2.0-flash",
        "display_name": "Gemini 2.0 Flash",
        "is_default": False,
        "cost_per_input_token": Decimal("0.0000001"),
        "cost_per_output_token": Decimal("0.0000004"),
        "context_window": 1048576,  # Gemini 2.0 Flash documented 1,048,576 input tokens
    },
]


async def backfill_default_model_context_windows(db: AsyncSession) -> None:
    """Fill ``context_window`` for the seeded default models where still NULL.

    The model seed only inserts when the table is empty, so DBs created before
    ``context_window`` was seeded keep NULL values — which disables the chat
    context gauge and leaves the auto-summarization threshold on its fixed
    fallback. This backfill is idempotent and matches by ``(provider,
    model_name)``; it only touches rows where the value is still NULL, so an
    operator-customised window is never overwritten.
    """

    from sqlalchemy import update

    from app.models.model import Model

    for model_data in DEFAULT_MODELS:
        cw = model_data.get("context_window")
        if cw is None:
            continue
        await db.execute(
            update(Model)
            .where(
                Model.provider == model_data["provider"],
                Model.model_name == model_data["model_name"],
                Model.context_window.is_(None),
            )
            .values(context_window=cw)
        )
