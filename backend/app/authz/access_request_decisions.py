"""Approve or deny an access request (plan 9.7 "접근 요청", 10.2, 10.9, S18; H-01).

A decision needs ``can_share`` on the requested resource; a caller without it gets 404,
the same as an unknown id or another organization's request (no enumeration). Lock
order is E-02's: organization row, then the request row, then whatever ``share`` locks.

Approve runs the E-01 share path for the requester (same rules: principal still an
active member, levels, agent-only expiry, D17 ``confirm_delegation``, personal
accounts, owner re-check). An approver may lower the level. When the requester already
holds a direct grant at that level or above, nothing is written (the share path would
otherwise lower it) and the request is closed as approved.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions, denials
from app.authz.access_request_notify import notify_decided
from app.authz.access_request_rules import RANK, check_approval_level, expire_stale, now
from app.authz.access_requests import audit_request, lock_request, require_pending
from app.authz.org_context import OrgContext
from app.authz.resource_lifecycle import LifecycleChange
from app.authz.service import AuthzService
from app.authz.share_targets import SHARE_TYPES, ShareType, authorize_target
from app.authz.sharing import lock_org_row, share, share_grants
from app.models.access_request import AccessRequest
from app.schemas.access_request import AccessRequestApprove, AccessRequestDeny
from app.schemas.grant import GrantCreate, PrincipalRef


async def _decidable(
    db: AsyncSession, authz: AuthzService, ctx: OrgContext, request_id: UUID
) -> tuple[AccessRequest, ShareType]:
    found = await db.get(AccessRequest, request_id, populate_existing=True)
    kind = None if found is None else SHARE_TYPES.get(found.resource_type)
    if found is None or kind is None or found.org_id != ctx.org_id:
        raise denials.not_found()
    await authorize_target(
        db, authz, ctx, kind, found.resource_id, "can_share", min_visible="can_share"
    )
    await lock_org_row(db, ctx.org_id)
    await expire_stale(db, ctx.org_id)
    request = await lock_request(db, request_id)
    if request is None:
        raise denials.not_found()
    require_pending(request)
    return request, kind


def _close(request: AccessRequest, ctx: OrgContext, status: str, note: str | None) -> None:
    stamp = now()
    request.status, request.decided_by, request.decided_at = status, ctx.user_id, stamp
    request.decision_note, request.updated_at = note, stamp


async def approve_request(
    db: AsyncSession,
    authz: AuthzService,
    ctx: OrgContext,
    request_id: UUID,
    body: AccessRequestApprove,
) -> tuple[AccessRequest, LifecycleChange]:
    request, kind = await _decidable(db, authz, ctx, request_id)
    level = check_approval_level(request.relation, body.level)
    requester = ("user", request.requester_id)
    held = [
        grant
        for grant in await share_grants(db, ctx, kind, request.resource_id)
        if (grant.principal_type, grant.principal_id) == requester
    ]
    change = LifecycleChange()
    if not any(RANK.get(grant.relation, -1) >= RANK[level] for grant in held):
        grant = GrantCreate(
            principal=PrincipalRef(type="user", id=request.requester_id),
            level=level,  # type: ignore[arg-type]  # requestable levels are share levels
            expires_at=body.expires_at,
            confirm_delegation=body.confirm_delegation,
        )
        change = (
            await share(db, authz, ctx, kind, request.resource_id, grant, notify=False)
        ).change
    _close(request, ctx, "approved", body.note)
    request.granted_relation = level
    await db.flush()
    await audit_request(db, ctx, audit_actions.ACCESS_REQUEST_APPROVE, request)
    await notify_decided(db, request)
    return request, change


async def deny_request(
    db: AsyncSession,
    authz: AuthzService,
    ctx: OrgContext,
    request_id: UUID,
    body: AccessRequestDeny,
) -> AccessRequest:
    request, _ = await _decidable(db, authz, ctx, request_id)
    _close(request, ctx, "denied", body.note)
    await db.flush()
    await audit_request(db, ctx, audit_actions.ACCESS_REQUEST_DENY, request)
    await notify_decided(db, request)
    return request
