"""Global account erasure guards; no private content or organization is inferred.

10.7 "사용자 삭제" (E-02): the restricted owner FKs (m83) block deletion while the account
owns any resource, archived agents included; the 409 lists them so the deletion screen
can send the operator to transfer or delete first. Grants held by the account are
revoked through ``GrantService`` (outbox-ordered) before the user row is locked.
"""

from collections import defaultdict
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.authz.error_codes import authorization_error
from app.authz.org_context import OrgContext
from app.authz.org_member_exit import owner_queries
from app.authz.tenant_org_write_locks import (
    TenantOrgWriteTarget,
    lock_member_rows,
    lock_tenant_org_writes,
)
from app.models.authz import ResourceGrant
from app.models.conversation import Conversation
from app.models.organization import OrganizationMember

if TYPE_CHECKING:  # app.dependencies imports this module through user_service
    from app.authz.grants import GrantService
    from app.authz.resource_lifecycle import LifecycleChange


async def owned_account_resources(db: AsyncSession, user_id: UUID) -> dict[str, list[str]]:
    """Every resource the account still owns, by type (all organizations)."""
    owned: dict[str, list[str]] = {}
    for kind, query in owner_queries(user_id):
        rows = await db.scalars(query.order_by(*query.selected_columns))
        if ids := [str(value) for value in rows]:
            owned[kind] = ids
    return owned


async def require_account_resources_released(db: AsyncSession, user_id: UUID) -> None:
    """The caller holds the User deletion lock before checking all restricted owner FKs."""
    if owned := await owned_account_resources(db, user_id):
        raise authorization_error("resource_transfer_required", {"resources": owned})


def _held_grants(user_id: UUID) -> Select[tuple[ResourceGrant]]:
    return select(ResourceGrant).where(
        ResourceGrant.principal_type == "user",
        ResourceGrant.principal_id == user_id,
        ResourceGrant.state == "active",
    )


async def lock_account_scopes(db: AsyncSession, user_id: UUID) -> None:
    """Company → organization → the account's member rows, for every organization it
    belongs to or holds grants in: the member-removal order (org → member rows → grants
    → resource), so a share or removal racing the erasure serializes instead of
    deadlocking."""
    member_scopes = select(OrganizationMember.tenant_id, OrganizationMember.org_id).where(
        OrganizationMember.user_id == user_id
    )
    grant_scopes = _held_grants(user_id).with_only_columns(
        ResourceGrant.tenant_id, ResourceGrant.org_id
    )
    pairs = {(row[0], row[1]) for row in (await db.execute(member_scopes)).all()}
    pairs |= {(row[0], row[1]) for row in (await db.execute(grant_scopes)).all()}
    by_tenant: defaultdict[UUID, set[UUID]] = defaultdict(set)
    for tenant_id, org_id in pairs:
        by_tenant[tenant_id].add(org_id)
    targets = [TenantOrgWriteTarget(tenant, orgs) for tenant, orgs in by_tenant.items()]
    if targets:
        await lock_tenant_org_writes(db, targets)
    for tenant_id, org_id in sorted(pairs, key=lambda pair: pair[1].bytes):
        await lock_member_rows(db, tenant_id, org_id, user_id)


async def revoke_account_grants(
    db: AsyncSession,
    user_id: UUID,
    actor_id: UUID,
    *,
    service: "GrantService | None" = None,
) -> "LifecycleChange":
    """Revoke every active grant naming the account, one organization context each;
    ``actor_id`` (the operator) is the revoker and audit actor."""
    from app.authz.grants import GrantService  # call-time: import cycle via dependencies
    from app.authz.resource_lifecycle import LifecycleChange

    grants = service or GrantService()
    outbox: list[UUID] = []
    held = list(await db.scalars(_held_grants(user_id).order_by(ResourceGrant.id)))
    for grant in held:
        ctx = OrgContext(org_id=grant.org_id, tenant_id=grant.tenant_id, user_id=actor_id)
        change = await grants.revoke(db, ctx, grant.id, audit={"account_erasure": str(user_id)})
        outbox += change.outbox_ids
    return LifecycleChange(tuple(outbox))


async def require_no_account_grants(db: AsyncSession, user_id: UUID) -> None:
    """Under the User lock: a grant written after the revocation pass → retry (409)."""
    found = await db.scalar(_held_grants(user_id).with_only_columns(ResourceGrant.id).limit(1))
    if found is not None:
        raise authorization_error("conflict")


async def lock_account_history(db: AsyncSession, user_id: UUID) -> list[UUID]:
    """Account cleanup uses direct conversation ownership across the account's scopes."""
    return list(
        (
            await db.scalars(
                select(Conversation.id)
                .where(Conversation.user_id == user_id)
                .order_by(Conversation.id)
                .with_for_update(of=Conversation)
            )
        ).all()
    )
