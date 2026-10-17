"""Best-effort evaluation-run usage ledger write, in the run's company (H-07, 8.8).

Moved from ``skill_usage_service``; the session maker is read from that module at call
time so its ``async_session`` stays the test patch surface.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from decimal import Decimal

from app.models.skill_builder_session import JsonValue
from app.services import skill_usage_service
from app.services.skill_evaluation_worker_tenants import company_session

logger = logging.getLogger(__name__)


def _usage_int(value: JsonValue | None) -> int:
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return 0
    return int(value)


async def record_evaluation_usage_nonfatal(
    *,
    tenant_id: uuid.UUID | None,
    skill_id: uuid.UUID,
    user_id: uuid.UUID,
    evaluation_run_id: uuid.UUID,
    model_name: str | None,
    usage: Mapping[str, JsonValue],
) -> None:
    """Own-session, best-effort evaluation-run ledger write.

    Called AFTER the run's completion is committed, so the completion is never
    rolled back if this write fails or is cancelled — ``asyncio.CancelledError``
    (a BaseException) would otherwise bypass a broad ``except Exception`` and
    unwind the run's transaction (repo CLAUDE.md cancel-vs-Exception rule).
    """
    raw_cost = usage.get("cost_usd")
    cost = (
        Decimal(str(raw_cost))
        if isinstance(raw_cost, int | float) and not isinstance(raw_cost, bool)
        else None
    )
    try:
        async with company_session(
            tenant_id, user_id, maker=skill_usage_service.async_session
        ) as db:
            await skill_usage_service.record_evaluation_usage(
                db,
                skill_id=skill_id,
                user_id=user_id,
                evaluation_run_id=evaluation_run_id,
                model_name=model_name,
                tokens_in=_usage_int(usage.get("tokens_in")),
                tokens_out=_usage_int(usage.get("tokens_out")),
                cost_usd=cost,
            )
            await db.commit()
    except Exception:  # noqa: BLE001 — ledger write must not fail the run
        logger.warning(
            "skill evaluation usage ledger write failed run_id=%s",
            evaluation_run_id,
            exc_info=True,
        )
