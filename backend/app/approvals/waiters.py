"""The runs that wait on approval requests: a schedule run or an Agent API run (17.3).

``settle_best_effort`` ends them when their resumed run ends; ``abandon_best_effort`` ends
them when their requests are retired without a decision. Both never fail the caller.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable

from app.approvals import api_runs, triggers
from app.authz.org_context import OrgContext
from app.database import TenantScoped
from app.workflows import tool_waits

logger = logging.getLogger(__name__)


async def settle_best_effort(
    run_id: uuid.UUID, status: str, context: OrgContext, secret_values: Iterable[str] = ()
) -> None:
    try:
        await triggers.settle(run_id, status, context)
        await api_runs.settle(run_id, status, context, secret_values)
        await tool_waits.settle(run_id, status, context, secret_values)
    except Exception:
        logger.exception("approval waiter settle failed run_id=%s", run_id)


async def abandon_best_effort(tenant: TenantScoped, request_ids: Iterable[uuid.UUID]) -> None:
    ids = list(request_ids)
    if not ids:
        return
    try:
        await triggers.abandon(tenant, ids)
        await api_runs.abandon(tenant, ids)
        await tool_waits.abandon(tenant, ids)
    except Exception:
        logger.exception("approval waiter abandon failed requests=%s", ids)


__all__ = ["abandon_best_effort", "settle_best_effort"]
