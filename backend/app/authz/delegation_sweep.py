"""When the delegation validator runs (plan 8.6 위임 검증, 10.2, 10.6, 10.7 item 5; F-03).

Every run uses its own company session (8.8), commits, then applies its tuples; the
caller's request session is never touched (no rollback/expiry of the caller's objects).

* ``revalidate_after`` / ``revalidate_object`` / ``revalidate_grant`` /
  ``revalidate_outbox``: right after a revocation path committed and applied (share
  revoke / stage change, ownership transfer, member exit, catalog policy). Best effort:
  never raises — the response already succeeded; a failure (``AuthzUnavailable``
  included) is logged, and the marker poll (``delegation_retry``) re-runs the scope
  once the revocation markers are finalized; the daily job is the backstop.
* ``run_delegation_sweep``: the daily leader job ``authz_delegation_validate`` (C-05
  ``jobs.py``; ``app.scheduler`` keeps the APScheduler wrapper), one organization at a
  time, one organization's failure logged without stopping the others.

Legacy mode is a no-op everywhere (OpenFGA is not consulted).
"""

import logging
from collections.abc import Callable, Collection
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_delegations import LINK_SOURCES, Key
from app.authz.delegation_links import DelegationScope
from app.authz.delegation_validator import DelegationReport, validate_delegations
from app.authz.resource_lifecycle import apply_lifecycle
from app.authz.service_core import AuthzDecisions
from app.database import SessionTenant, platform_session, tenant_session
from app.models.authz import AuthzOutbox, ResourceGrant
from app.models.organization import Organization

logger = logging.getLogger(__name__)
SWEEP_TRIGGER = "daily_sweep"
Maker = Callable[[], AsyncSession]


def _session(tenant_id: UUID, maker: Maker | None) -> AsyncSession:
    scope = SessionTenant(tenant_id)
    return tenant_session(scope) if maker is None else tenant_session(scope, maker=maker)


async def validate_in_session(
    authz: AuthzDecisions,
    scope: DelegationScope,
    tenant_id: UUID,
    *,
    trigger: str,
    maker: Maker | None = None,
    dry_run: bool = False,
) -> DelegationReport:
    """One validation in a fresh company session: validate, commit, apply, log."""
    async with _session(tenant_id, maker) as db:
        report = await validate_delegations(
            db, scope, authz=authz, trigger=trigger, dry_run=dry_run
        )
        await db.commit()
    await apply_lifecycle(report.change, authz)
    _log(scope, report, trigger)
    return report


async def revalidate_after(
    authz: AuthzDecisions, scope: DelegationScope, tenant_id: UUID, *, trigger: str
) -> DelegationReport:
    """``validate_in_session`` that never raises (module doc)."""
    if authz.mode == "legacy":
        return DelegationReport()
    try:
        return await validate_in_session(authz, scope, tenant_id, trigger=trigger)
    except Exception:  # noqa: BLE001 - the caller already committed; retried (module doc)
        logger.exception("delegation validation failed", extra={"trigger": trigger})
        return DelegationReport()


async def revalidate_object(
    authz: AuthzDecisions, org_id: UUID, tenant_id: UUID, obj: Key, *, trigger: str
) -> DelegationReport:
    """The delegations of one object (share stage change, ownership transfer)."""
    scope = DelegationScope(org_id, objects=frozenset({obj}))
    return await revalidate_after(authz, scope, tenant_id, trigger=trigger)


async def revalidate_grant(
    authz: AuthzDecisions, org_id: UUID, tenant_id: UUID, grant_id: UUID
) -> DelegationReport:
    """After ``DELETE /api/grants/{id}``: the delegations of the revoked grant's object."""
    if authz.mode == "legacy":
        return DelegationReport()
    query = select(ResourceGrant.resource_type, ResourceGrant.resource_id).where(
        ResourceGrant.id == grant_id
    )
    try:
        async with _session(tenant_id, None) as db:
            found = (await db.execute(query)).first()
    except Exception:  # noqa: BLE001 - best effort, as revalidate_after
        logger.exception("delegation validation failed", extra={"trigger": "grant_revoke"})
        return DelegationReport()
    if found is None:
        return DelegationReport()
    obj = (found[0], found[1])
    return await revalidate_object(authz, org_id, tenant_id, obj, trigger="grant_revoke")


async def revalidate_outbox(
    authz: AuthzDecisions,
    org_id: UUID,
    tenant_id: UUID,
    outbox_ids: Collection[UUID],
    *,
    trigger: str,
) -> DelegationReport:
    """The delegations of the objects whose tuples ``outbox_ids`` deleted (catalog)."""
    if authz.mode == "legacy" or not outbox_ids:
        return DelegationReport()
    query = select(AuthzOutbox.tuple_key).where(
        AuthzOutbox.id.in_(list(outbox_ids)), AuthzOutbox.op == "delete"
    )
    try:
        async with _session(tenant_id, None) as db:
            rows = list(await db.scalars(query))
    except Exception:  # noqa: BLE001 - best effort, as revalidate_after
        logger.exception("delegation validation failed", extra={"trigger": trigger})
        return DelegationReport()
    objects = {key for raw in rows if (key := _object_key(raw)) is not None}
    if not objects:
        return DelegationReport()
    scope = DelegationScope(org_id, objects=frozenset(objects))
    return await revalidate_after(authz, scope, tenant_id, trigger=trigger)


def _object_key(tuple_key: str) -> Key | None:
    """``user relation type:id`` → ``(type, id)`` for delegated object types only."""
    kind, _, raw = tuple_key.rpartition(" ")[2].partition(":")
    if kind not in LINK_SOURCES:
        return None
    try:
        return kind, UUID(raw)
    except ValueError:
        return None


async def run_delegation_sweep(
    authz: AuthzDecisions,
    *,
    maker: Maker | None = None,  # tests pass their session factory
    dry_run: bool = False,
) -> dict[object, DelegationReport]:
    """Validate every organization's delegations (one transaction per organization)."""
    if authz.mode == "legacy":
        return {}
    listing = (
        platform_session("authz.delegation_validate")
        if maker is None
        else platform_session("authz.delegation_validate", maker=maker)
    )
    async with listing as db:
        rows = (
            await db.execute(
                select(Organization.id, Organization.tenant_id).order_by(Organization.id)
            )
        ).all()
    reports: dict[object, DelegationReport] = {}
    for org_id, tenant_id in rows:
        try:
            reports[org_id] = await validate_in_session(
                authz,
                DelegationScope(org_id),
                tenant_id,
                trigger=SWEEP_TRIGGER,
                maker=maker,
                dry_run=dry_run,
            )
        except Exception:  # noqa: BLE001 - keep the other organizations' sweep alive
            logger.exception("delegation sweep failed for org %s; retried tomorrow", org_id)
    return reports


def _log(scope: DelegationScope, report: DelegationReport, trigger: str) -> None:
    counts = {
        "broken": len(report.broken),
        "repaired": len(report.repaired),
        "deferred": len(report.deferred),
        "unenforced": len(report.unenforced),
        "failed": len(report.failed),
    }
    if any(counts.values()):
        logger.info(
            "authz_delegations_validated",
            extra={"org_id": str(scope.org_id), "trigger": trigger, **counts},
        )
