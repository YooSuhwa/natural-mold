"""Company publication approval queue (plan 9.7 ``can_manage_tenant_catalog``, 10.2, S05).

Approving is a publication-scope decision, not a share (D8): the approver gets no access
to the item's contents, and the queue shows only the name, description, publishing
organization, publisher and required credential types. A request id is the item id;
anything that is not a pending request of this company, or no longer publishable
(published, ``public``, organization-owned), is the same 404 ``not_found`` and is not
listed. A later narrowing does not reset ``tenant_publication``; this filter and the
company-public read rule (``marketplace_access._company_public``) make it inert.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from fastapi import Request
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.authz import audit_actions
from app.authz.error_codes import authorization_error
from app.authz.marketplace_tenant_projection import tenant_publication_changed
from app.authz.resource_lifecycle import LifecycleChange
from app.authz.service_types import AuthzMode
from app.dependencies import CurrentUser
from app.marketplace.tenant_publication import publication_state, record_publication_event
from app.models.marketplace import MarketplaceItem as Item
from app.models.organization import Organization
from app.models.user import User
from app.schemas import tenant_publication as schemas
from app.services.tenants import pagination
from app.tenancy.admin_context import (
    TenantAdminContext,
    TenantAdminTarget,
    load_tenant_admin_context,
)

CATALOG = "can_manage_tenant_catalog"


def _publishable_pending(tenant_id: UUID):
    return and_(
        Item.tenant_id == tenant_id,
        Item.tenant_publication == "pending",
        Item.is_system.is_(False),
        Item.org_id.is_not(None),
        Item.status == "published",
        Item.visibility == "public",
    )


@dataclass(frozen=True, slots=True)
class PublicationDecision:
    db: AsyncSession
    request: Request
    actor: CurrentUser
    mode: AuthzMode


def _credential_types(item: Item) -> tuple[str, ...]:
    version = item.latest_version
    found = version.credential_requirements if version is not None else None
    keys = {
        str(r["definition_key"])
        for r in found or ()
        if isinstance(r, dict) and r.get("definition_key")
    }
    return tuple(sorted(keys))


async def list_publication_requests(
    db: AsyncSession, context: TenantAdminContext, query: pagination.TenantPageQuery
) -> schemas.PublicationRequestPage:
    if CATALOG not in context.permissions:
        raise authorization_error("permission_required")
    tenant_id = context.tenant.id
    scope = pagination.TenantCursorScope(
        list_kind="tenant_publication_requests", tenant_id=tenant_id, filter_key="pending"
    )
    cursor = pagination.decode_cursor(query.cursor, scope)
    requested_at = Item.tenant_publication_requested_at
    statement = (
        select(Item, Organization.name, User.name)
        .join(Organization, Organization.id == Item.org_id)
        .outerjoin(User, User.id == Item.tenant_publication_requested_by)
        .where(_publishable_pending(tenant_id))
        .options(selectinload(Item.latest_version))
    )
    if cursor is not None:
        statement = statement.where(
            or_(
                requested_at > cursor.sort_value,
                and_(requested_at == cursor.sort_value, Item.id > cursor.position),
            )
        )
    found = (
        await db.execute(statement.order_by(requested_at, Item.id).limit(query.limit + 1))
    ).all()
    selected, more = found[: query.limit], len(found) > query.limit
    last = selected[-1][0] if selected else None
    next_cursor = (
        pagination.encode_cursor(
            pagination.TenantCursor(
                scope=scope, sort_value=last.tenant_publication_requested_at, position=last.id
            )
        )
        if more and last is not None and last.tenant_publication_requested_at is not None
        else None
    )
    items = tuple(
        schemas.PublicationRequestItem(
            item_id=item.id,
            name=item.name,
            description=item.description,
            resource_type=item.resource_type,
            organization=schemas.PublicationRequestOrganization(id=item.org_id, name=org_name),
            publisher=schemas.PublicationRequestPublisher(
                id=item.tenant_publication_requested_by, name=publisher
            ),
            requested_at=item.tenant_publication_requested_at,
            credential_types=_credential_types(item),
        )
        for item, org_name, publisher in selected
    )
    return schemas.PublicationRequestPage(items=items, next_cursor=next_cursor, has_more=more)


async def decide_publication_request(
    decision: PublicationDecision,
    context: TenantAdminContext,
    item_id: UUID,
    *,
    approve: bool,
    note: str | None = None,
) -> tuple[schemas.TenantPublicationState, LifecycleChange]:
    """Lock the company (permission re-read), then the pending item; caller commits."""
    db = decision.db
    fresh = await load_tenant_admin_context(
        db, context.actor, TenantAdminTarget(context.tenant.id, permission=CATALOG)
    )
    item = await db.scalar(
        select(Item)
        .where(Item.id == item_id, _publishable_pending(fresh.tenant.id))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if item is None:
        raise authorization_error("not_found")
    item.tenant_publication = "approved" if approve else "rejected"
    item.tenant_publication_reviewed_by = decision.actor.id
    item.tenant_publication_reviewed_at = datetime.now(UTC)
    item.tenant_publication_note = note
    action = (
        audit_actions.MARKETPLACE_TENANT_PUBLISH_APPROVE
        if approve
        else audit_actions.MARKETPLACE_TENANT_PUBLISH_REJECT
    )
    await record_publication_event(
        db,
        actor=decision.actor,
        request=decision.request,
        action=action,
        item=item,
        metadata={"previous": "pending", "result": item.tenant_publication},
        reason=note,
    )
    change = await tenant_publication_changed(db, item, "pending", mode=decision.mode)
    return publication_state(item), change
