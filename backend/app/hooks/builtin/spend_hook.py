"""Spend hook — enqueues a :class:`SpendEntry` after every successful
``agent_invoke`` call so the daily aggregate tables stay current.

The hook only fires when:

* ``ctx.kind == "agent_invoke"`` (we don't roll up tool-call costs or MCP
  probes — those don't contribute to LLM spend).
* The call has at least one of ``tokens_in`` / ``tokens_out`` / ``cost_usd``
  on the :class:`HookResult` (otherwise we'd insert blank rows).

Failure isolation is provided by :class:`HookRegistry` (it logs and
continues), but we add a defensive ``try/except`` here too because dropping a
spend record must never fail an agent run.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from app.hooks.base import CustomHook, HookContext, HookResult
from app.services.spend_writer import SpendEntry, spend_queue

logger = logging.getLogger(__name__)


class SpendHook(CustomHook):
    name = "spend_hook"
    enabled = True

    async def async_post_call_hook(self, ctx: HookContext, result: HookResult) -> None:
        if ctx.kind != "agent_invoke":
            return
        if (
            (result.tokens_in or 0) == 0
            and (result.tokens_out or 0) == 0
            and (result.cost_usd or 0) == 0
        ):
            # Streaming aborted before usage was reported (interrupt etc.).
            return

        try:
            cost = result.cost_usd
            cost_decimal: Decimal
            if cost is None:
                cost_decimal = Decimal("0")
            elif isinstance(cost, Decimal):
                cost_decimal = cost
            else:
                cost_decimal = Decimal(str(cost))

            entry = SpendEntry(
                date=datetime.now(UTC).date(),
                user_id=ctx.user_id,
                agent_id=ctx.agent_id,
                model_id=ctx.model_id,
                tokens_in=int(result.tokens_in or 0),
                tokens_out=int(result.tokens_out or 0),
                cost_usd=cost_decimal,
            )
            spend_queue.add(entry)
        except Exception:  # noqa: BLE001 — defensive; hook must not raise
            logger.warning(
                "spend hook failed to enqueue entry (kind=%s req=%s)",
                ctx.kind,
                ctx.request_id,
                exc_info=True,
            )

    # Failure path is intentionally ignored — failed calls don't book spend
    # because providers usually don't bill them. Override here in the future
    # if you want to record attempted-but-failed calls separately.


__all__ = ["SpendHook"]
