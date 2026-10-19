"""Company-wide publication of a marketplace item (plan 9.7, 10.2, Q17; E-09).

State machine on ``marketplace_items.tenant_publication``:

* request: ``disabled`` policy -> 403 ``policy_disabled``; ``open`` -> ``approved`` at once;
  ``approval`` -> ``pending`` (not visible to the company's other organizations until a
  company administrator approves). An item already ``pending``/``approved`` is returned
  unchanged (9.7.1 duplicate request). A ``rejected`` item may request again.
* withdraw: any state -> ``none`` (the publisher cancels a request or a publication).
* approve/reject: ``app.services.tenants.publication_requests``.

Only a published, ``public`` organization item can be company-published: a private,
restricted, unlisted, draft, disabled or platform item is 409 ``tenant_publication_invalid``.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz.catalog_scope import load_catalog_management_candidate
from app.authz.error_codes import authorization_error
from app.authz.marketplace_access import PUBLISH, item_gate
from app.authz.marketplace_tenant_projection import tenant_publication_changed
from app.authz.org_context import OrgContext
from app.authz.resource_gate import gate_cap
from app.authz.resource_lifecycle import LifecycleChange
from app.authz.service import AuthzService
from app.authz.service_types import AuthzMode
from app.dependencies import CurrentUser
from app.marketplace.access import manageable_item
from app.marketplace.tenant_publication_policy import publish_policy
from app.models.marketplace import MarketplaceItem
from app.models.organization import Tenant
from app.schemas.tenant_publication import TenantPublicationState
from app.services import audit_service


async def managed_item(
    db: AsyncSession,
    authz: AuthzService,
    ctx: OrgContext,
    user: CurrentUser,
    item_id: UUID,
    *,
    publish: bool,
) -> MarketplaceItem:
    """The item's ``can_manage`` (D-05 gate; legacy owner/ACL ``manage``); a request
    (``publish``) then needs ``cap_publish_marketplace`` (legacy allow), checked only once
    the item is authorized so it never tells a hidden item from a missing one. Withdrawal
    needs ``can_manage`` alone: a publisher can always narrow."""
    gate = item_gate(db, authz, ctx, user, "can_manage")
    loaded = None
    if gate is None or not gate.enforced:
        loaded = await load_catalog_management_candidate(db, item_id, ctx)
    item = await manageable_item(item_id, loaded, user, gate)
    if publish:
        await gate_cap(authz, ctx, PUBLISH)
    return item


@dataclass(frozen=True, slots=True)
class PublicationCall:
    db: AsyncSession
    request: Request
    user: CurrentUser
    mode: AuthzMode


def publication_state(item: MarketplaceItem) -> TenantPublicationState:
    return TenantPublicationState(
        item_id=item.id,
        tenant_publication=item.tenant_publication,  # type: ignore[arg-type]
        requested_at=item.tenant_publication_requested_at,
        reviewed_at=item.tenant_publication_reviewed_at,
        note=item.tenant_publication_note,
    )


async def record_publication_event(
    db: AsyncSession,
    *,
    actor: CurrentUser,
    request: Request,
    action: str,
    item: MarketplaceItem,
    metadata: dict[str, object],
    reason: str | None = None,
) -> None:
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=actor.id,
        actor_email_snapshot=actor.email,
        owner_user_id=item.owner_user_id,
        action=action,
        target_type="marketplace_item",
        target_id=item.id,
        target_name_snapshot=item.name,
        target_owner_user_id=item.owner_user_id,
        outcome="success",
        reason_message=reason,
        request=request,
        metadata={"tenant_id": str(item.tenant_id), "org_id": str(item.org_id), **metadata},
    )


def _require_publishable(item: MarketplaceItem) -> None:
    organization_item = not item.is_system and item.org_id is not None
    if not (organization_item and item.visibility == "public" and item.status == "published"):
        raise authorization_error("tenant_publication_invalid")


def _clear(item: MarketplaceItem) -> None:
    item.tenant_publication_requested_by = None
    item.tenant_publication_requested_at = None
    item.tenant_publication_reviewed_by = None
    item.tenant_publication_reviewed_at = None
    item.tenant_publication_note = None


async def request_company_publication(
    call: PublicationCall, item: MarketplaceItem
) -> LifecycleChange:
    """``item`` is already authorized (``can_manage`` + ``cap_publish_marketplace``).

    Lock order is tenant -> item (as in the approval decision); the policy is read under
    the tenant lock, so a concurrent policy change is either before or after this request.
    """
    tenant = await call.db.scalar(
        select(Tenant)
        .where(Tenant.id == item.tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    await call.db.refresh(item, with_for_update=True)
    _require_publishable(item)
    if tenant is None:
        raise authorization_error("tenant_publication_invalid")
    policy = publish_policy(tenant)
    if policy == "disabled":
        raise authorization_error("policy_disabled")
    previous = item.tenant_publication
    if previous in ("pending", "approved"):
        return LifecycleChange()
    now = datetime.now(UTC)
    _clear(item)
    item.tenant_publication = "approved" if policy == "open" else "pending"
    item.tenant_publication_requested_by = call.user.id
    item.tenant_publication_requested_at = now
    if policy == "open":
        item.tenant_publication_reviewed_at = now
    await record_publication_event(
        call.db,
        actor=call.user,
        request=call.request,
        action=audit_actions.MARKETPLACE_TENANT_PUBLISH_REQUEST,
        item=item,
        metadata={"policy": policy, "previous": previous, "result": item.tenant_publication},
    )
    return await tenant_publication_changed(call.db, item, previous, mode=call.mode)


async def withdraw_company_publication(
    call: PublicationCall, item: MarketplaceItem
) -> LifecycleChange:
    """Back to organization-public; idempotent for an item that was never requested."""
    await call.db.refresh(item, with_for_update=True)
    previous = item.tenant_publication
    if previous == "none":
        return LifecycleChange()
    item.tenant_publication = "none"
    _clear(item)
    await record_publication_event(
        call.db,
        actor=call.user,
        request=call.request,
        action=audit_actions.MARKETPLACE_TENANT_PUBLISH_CANCEL,
        item=item,
        metadata={"previous": previous, "result": "none"},
    )
    return await tenant_publication_changed(call.db, item, previous, mode=call.mode)
