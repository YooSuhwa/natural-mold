"""Measured LLM usage capture for skill evaluation runs (Phase 3 spec §5.1).

The evaluator threads one :class:`LlmUsageCollector` through every model call
(arms + grader); the worker converts the collected totals into the persisted
``run.usage`` rollup and a ``skill_usage_events`` ledger row.

Cost stays ``None`` when the runner model has no pricing — "unknown" must not
collapse into "free" (spec §3.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.model import Model
from app.schemas.skill_builder import JsonValue


@dataclass(frozen=True, slots=True)
class ModelPricing:
    cost_per_input_token: Decimal | None
    cost_per_output_token: Decimal | None

    @property
    def available(self) -> bool:
        return self.cost_per_input_token is not None and self.cost_per_output_token is not None

    def cost_for(self, tokens_in: int, tokens_out: int) -> Decimal | None:
        if not self.available:
            return None
        assert self.cost_per_input_token is not None  # noqa: S101 — narrowed by available
        assert self.cost_per_output_token is not None  # noqa: S101
        return (
            self.cost_per_input_token * tokens_in + self.cost_per_output_token * tokens_out
        ).quantize(Decimal("0.000001"))


UNPRICED = ModelPricing(cost_per_input_token=None, cost_per_output_token=None)


async def resolve_model_pricing(db: AsyncSession, model_name: str | None) -> ModelPricing:
    """Look up per-token pricing for a runner model name.

    Multiple providers may register the same ``model_name`` — prefer a row
    that actually carries pricing so a priced catalog entry is not shadowed
    by an unpriced manual one.
    """

    if not model_name:
        return UNPRICED
    rows = (
        await db.execute(
            select(Model.cost_per_input_token, Model.cost_per_output_token).where(
                Model.model_name == model_name
            )
        )
    ).all()
    for cost_in, cost_out in rows:
        if cost_in is not None and cost_out is not None:
            return ModelPricing(cost_per_input_token=cost_in, cost_per_output_token=cost_out)
    if rows:
        cost_in, cost_out = rows[0]
        return ModelPricing(cost_per_input_token=cost_in, cost_per_output_token=cost_out)
    return UNPRICED


@dataclass(slots=True)
class LlmUsageCollector:
    """Accumulates ``usage_metadata`` across the evaluator's model calls."""

    model_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    metadata_seen: bool = False
    _last_call_tokens: tuple[int, int] = field(default=(0, 0))

    def add_response(self, message: Any) -> tuple[int, int]:
        """Record one model response; returns ``(tokens_in, tokens_out)`` of it.

        ``usage_metadata`` is optional on ``AIMessage`` (scripted/e2e models
        may omit it) — missing metadata counts the call with zero tokens but
        also flips ``metadata_seen`` false so cost stays unknown, never $0.
        """

        self.model_calls += 1
        usage = getattr(message, "usage_metadata", None)
        if isinstance(usage, dict):
            self.metadata_seen = True
        tokens_in = _int_or_zero(usage.get("input_tokens")) if isinstance(usage, dict) else 0
        tokens_out = _int_or_zero(usage.get("output_tokens")) if isinstance(usage, dict) else 0
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self._last_call_tokens = (tokens_in, tokens_out)
        return tokens_in, tokens_out

    def rollup(self, pricing: ModelPricing) -> dict[str, JsonValue]:
        # If calls were made but NO response carried usage_metadata, the token
        # totals are "unknown", not "zero" — a priced model must not then report
        # a $0 cost that masquerades as a genuinely free measured run
        # (unknown ≠ free, spec §3.1). Cost stays None in that case.
        measured_tokens = self.metadata_seen or self.model_calls == 0
        cost = pricing.cost_for(self.tokens_in, self.tokens_out) if measured_tokens else None
        return {
            "measured": True,
            "model_calls": self.model_calls,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": float(cost) if cost is not None else None,
        }


def _int_or_zero(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    return 0
