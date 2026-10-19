"""Producers of the 14.2 notifications outside access requests (H-02).

* ``grant.received`` (E-01 share to a user; the access-request approval path sends
  ``access_request.decided`` instead), ``ownership.transferred`` (E-01 transfer: the new
  owner, and the previous owner when a governor moved it), ``delegation.broken`` (F-03
  validator: the agent's managers; only the broken link's type and reason, never the
  linked resource's id or name), ``trigger.blocked`` (F-08: the trigger owner, once per
  streak of the same reason) — all in the caller's session;
* ``connection.owner_needed`` (F-07 ``fixable_by=owner``): the credential owner, written
  in its own session (the runtime middleware holds none); 24-hour merge per credential.
  The readiness panel's "소유자에게 알림" (G-04) writes the same notice the same way.
"""

import logging
from collections.abc import Iterable, Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.notifications import Target, notify, resource_link, resource_managers
from app.authz.org_context import OrgContext
from app.database import SessionTenant, async_session, tenant_session
from app.models.agent import Agent
from app.models.agent_trigger import AgentTrigger
from app.models.credential import Credential
from app.models.organization import OrganizationMember

logger = logging.getLogger(__name__)


async def grant_received(
    db: AsyncSession, ctx: OrgContext, resource_type: str, row: Any, user_id: UUID, level: str
) -> None:
    await notify(
        db,
        kind="grant.received",
        recipients=[user_id],
        tenant_id=ctx.tenant_id,
        org_id=ctx.org_id,
        target=Target(resource_type, row.id, row.name),
        actor_id=ctx.user_id,
        payload={"level": level},
        link=resource_link(resource_type, row.id),
    )


async def ownership_transferred(
    db: AsyncSession,
    ctx: OrgContext,
    resource_type: str,
    row: Any,
    *,
    new_owner: UUID,
    previous: UUID | None,
) -> None:
    common: dict[str, Any] = {
        "kind": "ownership.transferred",
        "tenant_id": ctx.tenant_id,
        "org_id": ctx.org_id,
        "target": Target(resource_type, row.id, row.name),
        "actor_id": ctx.user_id,
        "link": resource_link(resource_type, row.id),
    }
    await notify(db, recipients=[new_owner], payload={"role": "new_owner"}, **common)
    if previous is not None:
        await notify(db, recipients=[previous], payload={"role": "previous_owner"}, **common)


async def delegation_broken(
    db: AsyncSession,
    *,
    org_id: UUID,
    tenant_id: UUID,
    agent_id: UUID,
    reason: str,
    resource_type: str,
    cache: dict[UUID, set[UUID]] | None = None,
) -> None:
    """``cache``: managers per agent, shared by one validator run."""
    agent = await db.get(Agent, agent_id)
    if agent is None:
        return
    cache = {} if cache is None else cache
    if agent_id not in cache:
        cache[agent_id] = await resource_managers(db, org_id, "agent", agent_id, mode="enforce")
    await notify(
        db,
        kind="delegation.broken",
        recipients=sorted(cache[agent_id]),
        tenant_id=tenant_id,
        org_id=org_id,
        target=Target("agent", agent_id, agent.name),
        payload={"reason": reason, "resource_type": resource_type},
        link=f"/agents/{agent_id}/settings",
    )


async def trigger_blocked(db: AsyncSession, trigger: AgentTrigger, reason: str) -> None:
    agent = await db.get(Agent, trigger.agent_id)
    await notify(
        db,
        kind="trigger.blocked",
        recipients=[trigger.user_id],
        tenant_id=trigger.tenant_id,
        org_id=trigger.org_id,
        target=Target("agent", trigger.agent_id, agent.name if agent else None),
        payload={"reason": reason, "trigger_name": trigger.name},
        link=f"/agents/{trigger.agent_id}/settings",
    )


async def _personal_org(db: AsyncSession, credential: Credential) -> UUID | None:
    """The org of an org-less credential: the owner's only active org in its company."""
    if credential.org_id is not None:
        return credential.org_id
    orgs = list(
        await db.scalars(
            select(OrganizationMember.org_id).where(
                OrganizationMember.user_id == credential.user_id,
                OrganizationMember.tenant_id == credential.tenant_id,
                OrganizationMember.status == "active",
            )
        )
    )
    if len(orgs) != 1:
        logger.info("owner_notification_skipped credential_id=%s orgs=%d", credential.id, len(orgs))
        return None
    return orgs[0]


async def owner_needed(items: Iterable[Mapping[str, Any]], tenants: Mapping[str, str]) -> bool:
    """One ``connection.owner_needed`` per credential owner; never raises into the run.
    ``tenants`` maps a credential id to its company (one tenant session each); a
    credential of unknown company is skipped. ``False`` when a write failed."""
    by_tenant: dict[UUID, set[UUID]] = {}
    for item in items:
        raw = str(item.get("credential_id"))
        try:
            credential_id, tenant_id = UUID(raw), UUID(str(tenants.get(raw)))
        except ValueError:
            continue
        by_tenant.setdefault(tenant_id, set()).add(credential_id)
    try:
        for tenant_id, ids in by_tenant.items():
            async with tenant_session(SessionTenant(tenant_id), maker=async_session) as db:
                for credential_id in sorted(ids):
                    await _owner_needed_one(db, credential_id)
                await db.commit()
    except Exception:  # noqa: BLE001 - a notification never fails the run
        logger.exception("owner_notification_failed")
        return False
    return True


async def owner_needed_count(owners: Iterable[tuple[UUID, UUID]]) -> int:
    """G-04 readiness panel button: ``connection.owner_needed`` for ``(credential, company)``
    pairs, each read and written in a session of the owner's company (as the runtime path).
    Returns the notices written (merged ones included); 0 when a write failed."""
    by_tenant: dict[UUID, set[UUID]] = {}
    for credential_id, tenant_id in owners:
        by_tenant.setdefault(tenant_id, set()).add(credential_id)
    written = 0
    try:
        for tenant_id, ids in by_tenant.items():
            async with tenant_session(SessionTenant(tenant_id), maker=async_session) as db:
                for credential_id in sorted(ids, key=str):
                    written += await _owner_needed_one(db, credential_id)
                await db.commit()
    except Exception:  # noqa: BLE001 - reported to the caller as nothing notified
        logger.exception("owner_notification_failed")
        return 0
    return written


async def _owner_needed_one(db: AsyncSession, credential_id: UUID) -> int:
    credential = await db.get(Credential, credential_id)
    if credential is None or credential.user_id is None or credential.tenant_id is None:
        return 0
    org_id = await _personal_org(db, credential)
    if org_id is None:
        return 0
    rows = await notify(
        db,
        kind="connection.owner_needed",
        recipients=[credential.user_id],
        tenant_id=credential.tenant_id,
        org_id=org_id,
        target=Target("credential", credential.id, credential.name),
        link=resource_link("credential", credential.id),
    )
    return len(rows)


__all__ = [
    "delegation_broken",
    "grant_received",
    "owner_needed",
    "owner_needed_count",
    "ownership_transferred",
    "trigger_blocked",
]
