"""Notifications of access requests (plan 14.2; H-01 call sites, delivered by H-02).

``access_request.created`` goes to the resource's managers (``can_share`` holders, the
requester excluded) and ``access_request.decided`` to the requester. Both run inside the
request's transaction, after the row change. Payloads hold enums only (level, decision);
the requester and decider are the actor.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.notifications import notify, resource_link, resource_managers, resource_target
from app.models.access_request import AccessRequest

CREATED_KIND = "access_request.created"
DECIDED_KIND = "access_request.decided"
INBOX_LINK = "/settings/organization/requests"


async def notify_created(db: AsyncSession, request: AccessRequest, *, mode: str) -> None:
    managers = await resource_managers(
        db, request.org_id, request.resource_type, request.resource_id, mode=mode
    )
    await notify(
        db,
        kind=CREATED_KIND,
        recipients=sorted(managers),
        tenant_id=request.tenant_id,
        org_id=request.org_id,
        target=await resource_target(db, request.resource_type, request.resource_id),
        actor_id=request.requester_id,
        payload={"level": request.relation},
        link=INBOX_LINK,
    )


async def notify_decided(db: AsyncSession, request: AccessRequest) -> None:
    await notify(
        db,
        kind=DECIDED_KIND,
        recipients=[request.requester_id],
        tenant_id=request.tenant_id,
        org_id=request.org_id,
        target=await resource_target(db, request.resource_type, request.resource_id),
        actor_id=request.decided_by,
        payload={
            "decision": request.status,
            "level": request.granted_relation or request.relation,
        },
        link=resource_link(request.resource_type, request.resource_id),
    )
