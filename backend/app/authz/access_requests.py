"""Create and cancel access requests (plan 8.4, 9.7 "접근 요청", 10.9, S18; H-01).

``POST /api/access-requests``: only a caller holding the type's ``min_visible`` on a
resource of the current organization gets a real request. Anyone else — a missing id, a
hidden or foreign resource, OpenFGA down — gets ``202`` with no body and nothing is
written, so the answer never says whether the resource exists (8.4). Legacy mode: only
the owner sees a resource, and the owner holds every level (422 ``already_granted``),
so requests are effectively an enforce-mode feature.

Cancel: the requester's own pending request; any other id is 404.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions, denials
from app.authz.access_request_notify import notify_created
from app.authz.access_request_rules import (
    REQUEST_TTL,
    check_limits,
    check_requestable,
    expire_stale,
    level_permission,
    now,
    pending_duplicate,
)
from app.authz.error_codes import authorization_error
from app.authz.grant_validation import invalid
from app.authz.org_context import OrgContext
from app.authz.principal import Principal
from app.authz.service import AuthzService
from app.authz.share_targets import SHARE_TYPES, is_owner, load_target, share_object
from app.authz.sharing import lock_org_row
from app.models.access_request import AccessRequest
from app.schemas.access_request import AccessRequestCreate
from app.services import audit_service


@dataclass(frozen=True, slots=True)
class CreateResult:
    request: AccessRequest | None  # ``None``: the 202 answer, nothing written
    created: bool = False


async def audit_request(
    db: AsyncSession,
    ctx: OrgContext,
    action: str,
    request: AccessRequest,
    *,
    extra: dict[str, object] | None = None,
) -> None:
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=ctx.user_id,
        action=action,
        target_type="access_request",
        target_id=request.id,
        outcome="success",
        org_id=ctx.org_id,
        tenant_id=ctx.tenant_id,
        metadata={
            "resource_type": request.resource_type,
            "resource_id": str(request.resource_id),
            "level": request.relation,
            "requester_id": str(request.requester_id),
            "granted_level": request.granted_relation,
            **(extra or {}),
        },
    )


async def create_request(
    db: AsyncSession, authz: AuthzService, ctx: OrgContext, body: AccessRequestCreate
) -> CreateResult:
    kind = SHARE_TYPES[body.resource_type]
    check_requestable(kind, body.level)
    row = await load_target(db, ctx, kind, body.resource_id)
    if row is None:
        return CreateResult(None)
    principal, obj = Principal.for_user(ctx), share_object(kind, row.id)
    owner = is_owner(row, ctx)
    visible = await authz.can_request_access(
        principal,
        obj,
        min_visible=kind.min_visible,
        resource_org_id=row.org_id,
        legacy_visible=owner,
    )
    if not visible:
        return CreateResult(None)
    wanted = level_permission(kind, body.level)
    if await authz.my_permissions(principal, obj, [wanted], legacy=[wanted] if owner else []):
        raise invalid("already_granted")
    await lock_org_row(db, ctx.org_id)  # org → access request: serializes dedupe + quota
    await expire_stale(db, ctx.org_id)
    found = await pending_duplicate(db, ctx.org_id, kind, row.id, ctx.user_id, body.level)
    if found is not None:
        return CreateResult(found)
    await check_limits(db, ctx.org_id, ctx.user_id)
    stamp = now()
    request = AccessRequest(
        org_id=ctx.org_id,
        tenant_id=ctx.tenant_id,
        resource_type=kind.name,
        resource_id=row.id,
        relation=body.level,
        requester_id=ctx.user_id,
        reason=body.reason,
        created_at=stamp,
        updated_at=stamp,
        expires_at=stamp + REQUEST_TTL,
    )
    try:
        async with db.begin_nested():
            db.add(request)
    except IntegrityError:  # a concurrent identical request won the partial unique index
        found = await pending_duplicate(db, ctx.org_id, kind, row.id, ctx.user_id, body.level)
        if found is None:
            raise
        return CreateResult(found)
    await audit_request(db, ctx, audit_actions.ACCESS_REQUEST_CREATE, request)
    await notify_created(db, request, mode=authz.mode)
    return CreateResult(request, created=True)


async def lock_request(db: AsyncSession, request_id: UUID) -> AccessRequest | None:
    return await db.scalar(
        select(AccessRequest)
        .where(AccessRequest.id == request_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def require_pending(request: AccessRequest) -> None:
    if request.status != "pending":
        raise authorization_error("access_request_closed", {"status": request.status})


async def cancel_request(db: AsyncSession, ctx: OrgContext, request_id: UUID) -> AccessRequest:
    await lock_org_row(db, ctx.org_id)
    await expire_stale(db, ctx.org_id)
    request = await lock_request(db, request_id)
    if request is None or request.org_id != ctx.org_id or request.requester_id != ctx.user_id:
        raise denials.not_found()
    require_pending(request)
    stamp = now()
    request.status, request.decided_at, request.updated_at = "cancelled", stamp, stamp
    await db.flush()
    await audit_request(db, ctx, audit_actions.ACCESS_REQUEST_CANCEL, request)
    return request


async def cancel_member_requests(db: AsyncSession, ctx: OrgContext, user_id: UUID) -> list[UUID]:
    """E-02 member exit (caller holds the org lock): the leaver's pending requests close."""
    pending = await db.scalars(
        select(AccessRequest)
        .where(
            AccessRequest.org_id == ctx.org_id,
            AccessRequest.requester_id == user_id,
            AccessRequest.status == "pending",
        )
        .order_by(AccessRequest.id)
        .with_for_update()
    )
    closed: list[UUID] = []
    for request in list(pending):
        stamp = now()
        request.status, request.decided_at, request.updated_at = "cancelled", stamp, stamp
        request.decided_by = ctx.user_id
        await db.flush()
        await audit_request(
            db, ctx, audit_actions.ACCESS_REQUEST_CANCEL, request, extra={"member_exit": True}
        )
        closed.append(request.id)
    return closed
