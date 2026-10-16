"""Archive-wins cascade inside the organization-archive transaction (internal I22).

After :func:`lock_archive_scope` holds every affected row in canonical order,
active triggers pause (``next_run_at`` cleared), active deployments disable,
and run/input/linked-envelope rows take the frozen I14 projection with the
public reason ``organization_archived``. Live API/trigger envelopes of the
organization that were never linked to a run fail with the same code and a
finish time. Agent rows are never archived, nothing is replayed or re-enabled,
and a repeated cascade changes nothing.

Lease exemption: a run carrying the durable materialization pair
(``runtime_materialized_at`` + digest) is left untouched. The pair is the
cross-process criterion because the prepared-run registry is process-local and
cannot be consulted for leases held by other workers. With a live lease the run
finishes through I14's closed contract; without one (worker loss) every
dispatch fails closed in ``lease_dispatch_permit.open_dispatch_permit``
(``prepared_run_lease_missing``) and the heartbeat sweep
(``conversation_run_creation_persistence.sweep_stale_conversation_runs`` ->
``mark_stale_active_runs``) terminalizes it as ``stale``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.prepared_terminal_writer import (
    DenialOutcome,
    terminalize_authority_denial,
)
from app.authz.execution_lock_cursor import LockClass
from app.models.organization import Organization
from app.services.tenants import organization_archive_enumeration as enumeration

ARCHIVE_REASON = "organization_archived"
# Same user-facing text the I14 writer stores for linked envelopes.
ARCHIVE_MESSAGE = "조직이 보관되어 실행이 종료되었습니다"


@dataclass(frozen=True, slots=True)
class ArchiveCascadeOutcome:
    denial: DenialOutcome
    paused_trigger_ids: tuple[UUID, ...]
    disabled_deployment_ids: tuple[UUID, ...]
    failed_unlinked_envelope_ids: tuple[UUID, ...]
    lock_trace: tuple[tuple[LockClass, UUID], ...]


async def cascade_organization_archive(
    db: AsyncSession, organization: Organization, archived_at: datetime
) -> ArchiveCascadeOutcome:
    """Stage the cascade for an already locked, nondefault, archivable organization."""
    scope = await enumeration.lock_archive_scope(db, organization)
    stamp = archived_at.replace(tzinfo=None)
    for trigger in scope.triggers:
        trigger.status = "paused"
        trigger.next_run_at = None
    for deployment in scope.deployments:
        deployment.status = "disabled"
        deployment.updated_at = stamp
    denial = terminalize_authority_denial(
        ARCHIVE_REASON,
        inputs=scope.inputs,
        runs=scope.runs,
        api_runs=scope.api_runs,
        trigger_runs=scope.trigger_runs,
    )
    for api_run in scope.unlinked_api_runs:
        api_run.status, api_run.finished_at = "failed", stamp
        api_run.error_code, api_run.error_message = ARCHIVE_REASON, ARCHIVE_MESSAGE
    for trigger_run in scope.unlinked_trigger_runs:
        trigger_run.status, trigger_run.finished_at = "failed", stamp
        trigger_run.error_message = ARCHIVE_REASON
    await db.flush()
    return ArchiveCascadeOutcome(
        denial=denial,
        paused_trigger_ids=tuple(trigger.id for trigger in scope.triggers),
        disabled_deployment_ids=tuple(deployment.id for deployment in scope.deployments),
        failed_unlinked_envelope_ids=tuple(
            row.id for row in (*scope.unlinked_api_runs, *scope.unlinked_trigger_runs)
        ),
        lock_trace=scope.lock_trace,
    )
