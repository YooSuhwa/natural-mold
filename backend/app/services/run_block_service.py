"""Audit and owner-notification hook for runs BLOCKED at dispatch (plan 11.6, 14.1; F-08).

``record_run_blocked`` writes ``run.blocked`` (system actor, the agent as target,
``reason_code`` = the 11.6 reason, ``owner_user_id`` = the run's account, i.e. the
trigger or queue owner, ``target_owner_user_id`` = the agent's owner) in the caller's
session, so it commits with the blocked run row. ``notify_owner_blocked`` is the call
site of the 14.2 ``trigger.blocked`` notification; the notification table and sender
are P7, so it only logs for now.
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.audit_actions import RUN_BLOCKED
from app.services import audit_service

logger = logging.getLogger(__name__)
InvokerType = Literal["schedule", "queue", "api", "subagent"]


async def record_run_blocked(
    db: AsyncSession,
    *,
    invoker: InvokerType,
    reason: str,
    agent_id: uuid.UUID | None,
    owner_user_id: uuid.UUID | None,
    agent_owner_user_id: uuid.UUID | None,
    code: str | None = None,
    run_id: uuid.UUID | None = None,
    source_id: uuid.UUID | None = None,
) -> None:
    await audit_service.record_event(
        db,
        actor_type="system",
        action=RUN_BLOCKED,
        target_type="agent",
        target_id=agent_id,
        target_owner_user_id=agent_owner_user_id,
        owner_user_id=owner_user_id,
        outcome="denied",
        reason_code=reason,
        run_id=run_id,
        metadata={
            "invoker_type": invoker,
            "code": code,
            "source_id": str(source_id) if source_id is not None else None,
        },
    )


async def notify_owner_blocked(
    *,
    kind: Literal["trigger.blocked"],
    owner_user_id: uuid.UUID | None,
    reason: str,
    source_id: uuid.UUID | None,
) -> None:
    """P7 (14.2) hook: deliver ``kind`` to the owner. No notification store exists yet."""
    logger.info(
        "notification %s pending for owner %s (reason %s, source %s)",
        kind,
        owner_user_id,
        reason,
        source_id,
    )


__all__ = ["InvokerType", "notify_owner_blocked", "record_run_blocked"]
