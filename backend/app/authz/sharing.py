"""Share, list and revoke on a resource (plan 9.7 "공유", 10.2; E-01).

Writes go through ``GrantService`` in the caller's transaction (grant rows and their
outbox rows together, plus its ``grant.*`` audit events); the route commits and then
``apply_lifecycle``s the returned change before responding (8.6 item 2).

* Same principal and level (and expiry): the existing grant is returned, nothing is
  written (9.7.1 duplicate rule, 200).
* Same principal, other level: the old grant is revoked and a new one created; both
  appear in the audit log (10.2).
* Same principal and level, other expiry: ``GrantService`` changes the row in place.

* A grantee may revoke their own grant without ``can_revoke``; revocations record
  ``self_revoke``/``governance`` in the audit metadata.
* 10.4 (E-05): a personal account credential, or a resource carrying one, is refused
  with ``409 personal_account_shared`` (``personal_accounts``).
* After the grant write the resource row is locked and its owner re-checked
  (org → grants → resource, the transfer order); a concurrent transfer → 409.

Follow-ups after a revocation (10.2): the delegation validator and the readiness cache
do not exist yet (F-02/m86, P6); their hooks belong next to ``revoke_share``.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import denials
from app.authz.grants import GrantRequest, GrantService, GrantTarget
from app.authz.org_context import OrgContext
from app.authz.personal_accounts import require_no_personal_accounts
from app.authz.principal import Principal
from app.authz.resource_lifecycle import LifecycleChange
from app.authz.service import AuthzService
from app.authz.share_rules import (
    check_level,
    check_self,
    require_credential_owner,
    require_delegation_confirmed,
    require_org_wide,
    utc,
)
from app.authz.share_targets import (
    SHARE_TYPES,
    ShareRow,
    ShareType,
    authorize_target,
    is_owner,
    load_target,
    recheck_owner,
    share_object,
)
from app.models.authz import ResourceGrant
from app.models.organization import Organization
from app.schemas.grant import GrantCreate, GrantView, PrincipalRef

# Grants this API shows and manages; delegation/system_fanout rows have their own owners.
SHARE_KINDS = ("share", "migration")
SHARE_PRINCIPALS = ("user", "group", "organization")


@dataclass(frozen=True, slots=True)
class ShareResult:
    grant: ResourceGrant
    created: bool
    change: LifecycleChange


def grant_view(grant: ResourceGrant) -> GrantView:
    return GrantView(
        grant_id=grant.id,
        principal=PrincipalRef.model_validate(
            {"type": grant.principal_type, "id": grant.principal_id}
        ),
        level=grant.relation,  # type: ignore[arg-type]  # share rows carry 10.1 levels only
        expires_at=utc(grant.expires_at),
        granted_by=grant.granted_by,
        granted_at=utc(grant.granted_at) or grant.granted_at,
    )


async def lock_org_row(db: AsyncSession, org_id: UUID) -> None:
    """Single lock order for every grant write (E-02): the organization row first, then
    member rows (``check_principal`` FOR SHARE), grants, resource. A level change revokes
    and an expiry change records a marker — both bump the org epoch — so share and
    transfer take this lock up front, as a member removal does."""
    await db.execute(select(Organization.id).where(Organization.id == org_id).with_for_update())


async def share_grants(
    db: AsyncSession, ctx: OrgContext, kind: ShareType, resource_id: UUID
) -> list[ResourceGrant]:
    """Active share grants of the current organization on one resource."""
    found = await db.scalars(
        select(ResourceGrant)
        .where(
            ResourceGrant.org_id == ctx.org_id,
            ResourceGrant.resource_type == kind.name,
            ResourceGrant.resource_id == resource_id,
            ResourceGrant.state == "active",
            ResourceGrant.grant_kind.in_(SHARE_KINDS),
            ResourceGrant.principal_type.in_(SHARE_PRINCIPALS),
        )
        .order_by(ResourceGrant.granted_at, ResourceGrant.id)
        .execution_options(populate_existing=True)
    )
    return list(found)


async def list_shares(
    db: AsyncSession, authz: AuthzService, ctx: OrgContext, kind: ShareType, resource_id: UUID
) -> list[GrantView]:
    await authorize_target(db, authz, ctx, kind, resource_id, "can_revoke")
    return [grant_view(grant) for grant in await share_grants(db, ctx, kind, resource_id)]


async def share(
    db: AsyncSession,
    authz: AuthzService,
    ctx: OrgContext,
    kind: ShareType,
    resource_id: UUID,
    body: GrantCreate,
    *,
    service: GrantService | None = None,
) -> ShareResult:
    row = await authorize_target(db, authz, ctx, kind, resource_id, "can_share")
    require_credential_owner(kind, row, ctx)
    check_level(kind, row, body)
    principal = (body.principal.type, body.principal.id)
    held = [
        g
        for g in await share_grants(db, ctx, kind, row.id)
        if (g.principal_type, g.principal_id) == principal
    ]
    if not is_owner(row, ctx):
        check_self(ctx, body, held)
    await require_org_wide(db, authz, ctx, body)
    await require_no_personal_accounts(db, row)  # 10.4, E-05: before any confirmation
    await require_delegation_confirmed(db, kind, row, body, held)
    same = [g for g in held if g.relation == body.level]
    others = [g for g in held if g.relation != body.level]
    if same and not others and utc(same[0].expires_at) == utc(body.expires_at):
        return ShareResult(same[0], created=False, change=LifecycleChange())
    await lock_org_row(db, ctx.org_id)  # before check_principal's member row (E-02)
    grants = service or GrantService()
    outbox: list[UUID] = []
    for stale in others:  # a level change: revoke, then create (both audited)
        outbox += (await grants.revoke(db, ctx, stale.id)).outbox_ids
    request = GrantRequest(body.level, *principal, expires_at=body.expires_at)
    owner = row.user_id
    change = await grants.grant(db, ctx, GrantTarget(kind.name, row.id), request)
    outbox += change.outbox_ids
    await recheck_owner(db, kind, row.id, owner)
    return ShareResult(change.grant, created=not same, change=LifecycleChange(tuple(outbox)))


async def revoke_share(
    db: AsyncSession,
    authz: AuthzService,
    ctx: OrgContext,
    grant_id: UUID,
    *,
    service: GrantService | None = None,
) -> LifecycleChange:
    """``DELETE /api/grants/{id}``: ``can_revoke`` on the grant's resource (9.7).

    Grants of another organization, delegations, fanout rows and rows of types this API
    does not manage are 404, the same as a missing id (no enumeration).
    """
    grant = await db.get(ResourceGrant, grant_id, populate_existing=True)
    kind = None if grant is None else SHARE_TYPES.get(grant.resource_type)
    if (
        grant is None
        or kind is None
        or grant.org_id != ctx.org_id
        or grant.state != "active"
        or grant.grant_kind not in SHARE_KINDS
        or grant.principal_type not in SHARE_PRINCIPALS
    ):
        raise denials.not_found()
    own = (grant.principal_type, grant.principal_id) == ("user", ctx.user_id)
    row = await load_target(db, ctx, kind, grant.resource_id)
    if not own:  # a grantee may always give up their own grant (10.2)
        row = await authorize_target(
            db, authz, ctx, kind, grant.resource_id, "can_revoke", min_visible="can_revoke"
        )
    if row is None:
        raise denials.not_found()
    audit = {"self_revoke": own, "governance": await _governing(authz, ctx, kind, row, own)}
    owner = row.user_id
    change = await (service or GrantService()).revoke(db, ctx, grant.id, audit=audit)
    await recheck_owner(db, kind, row.id, owner)
    return LifecycleChange(change.outbox_ids)


async def _governing(
    authz: AuthzService, ctx: OrgContext, kind: ShareType, row: ShareRow, own: bool
) -> bool:
    """A revocation by someone who may revoke but not share: org/company governance."""
    if own or is_owner(row, ctx):
        return False
    held = await authz.my_permissions(
        Principal.for_user(ctx), share_object(kind, row.id), ["can_share"], legacy=[]
    )
    return not held
