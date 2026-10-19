"""Lost-lease recovery when the execution gateway is denied (parent Task 10, I19/I23).

The ordinary recovery path re-enters the canonical execution gateway, which
requires an active Tenant, active Organization and active owner memberships.
After an organization archive (whose cascade intentionally leaves paired,
lease-owned runs alone), a tenant suspension or an owner-membership
revocation, that gateway denies — yet the worker that held the lease is gone,
so the run must still terminalize instead of staying ``running`` forever.

This narrow terminal-only path takes the parent-first write locks without the
authority proof — Tenant -> Organization (``lock_tenant_org_write``) -> Agent
lifecycle -> Conversation -> Run -> Input, the canonical order with the
membership classes skipped — and then, exactly like the gateway path, decides
liveness through the prepared registry: a live local lease is never touched;
``LEASE_MISSING`` fails the run ``prepared_run_lease_missing`` (worker loss is
the cause, whatever the authority state; linked claimed/pending inputs fail and
linked API/trigger envelopes are projected by ``transition_run``), and a
``canceling`` orphan becomes ``canceled`` with the dead owner's cancellation
acknowledged. Nothing is read beyond these rows and nothing is rebuilt.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.prepared_run_registry import (
    LEASE_MISSING,
    PreparedRunLeaseError,
    get_prepared_run_registry,
)
from app.authz.tenant_org_write_locks import lock_tenant_org_write
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.services import agent_lifecycle_locks
from app.services.conversation_run_state import transition_run

_MESSAGE = "Prepared run could not be started"


def _lease_missing(run: ConversationRun) -> bool:
    digest = run.runtime_materialization_sha256
    if digest is None:
        return False
    try:
        get_prepared_run_registry().ensure_live_for_marker(run.id, marker_digest=digest)
    except PreparedRunLeaseError as error:
        return error.code == LEASE_MISSING
    return False


async def _locked_run(db: AsyncSession, row: Any) -> ConversationRun | None:
    parents = (
        await db.execute(
            select(Conversation.tenant_id, Conversation.org_id).where(
                Conversation.id == row.conversation_id
            )
        )
    ).one_or_none()
    if parents is None:
        return None
    await lock_tenant_org_write(db, parents[0], (parents[1],))
    await agent_lifecycle_locks.acquire_agent_lifecycle_locks(db, (row.agent_id,))
    await db.scalar(
        select(Conversation.id).where(Conversation.id == row.conversation_id).with_for_update()
    )
    return await db.scalar(
        select(ConversationRun)
        .where(
            ConversationRun.id == row.id,
            ConversationRun.conversation_id == row.conversation_id,
            ConversationRun.agent_id == row.agent_id,
            ConversationRun.user_id == row.user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def terminalize_denied_orphan(db: AsyncSession, row: Any) -> bool:
    """Terminalize one gateway-denied paired orphan; ``True`` when written."""

    run = await _locked_run(db, row)
    if (
        run is None
        or not run.is_active
        or run.status not in ("running", "canceling")
        or run.runtime_materialized_at is None
        or not _lease_missing(run)
    ):
        await db.rollback()
        return False
    if run.status == "canceling":
        await transition_run(db, run, "canceled", cancellation_ack_worker_id=run.worker_instance_id)
    else:
        inputs = await db.scalars(
            select(ConversationRunInput)
            .where(
                ConversationRunInput.run_id == run.id,
                ConversationRunInput.status.in_(("pending", "claimed")),
            )
            .with_for_update()
        )
        for item in inputs.all():
            item.status = "failed"
        await transition_run(db, run, "failed", error_code=LEASE_MISSING, error_message=_MESSAGE)
    await db.commit()
    return True
