"""``SUPER_MODERATION``: the only operator path into marketplace items (plan 10.5, D-05).

10.5 "운영자 우회": the operator bypass is split out to review, listing management and
restoring disabled items; general reads lose it. When ``marketplace_item`` is enforced:

* ``disable`` / ``enable`` (enable = restore) reach **platform items only** (``is_system``,
  no organization/company): that was the operator's whole legacy reach on these routes
  (``load_catalog_management_candidate`` is owner-or-platform). Organization items are
  decided by OpenFGA like for anyone else.
* ``listing``: the SUPER ``/admin/items/{id}/listed`` route reads back the item it just
  changed (it already loaded it by id under ``require_super_user``).

Every moderation decision records a ``marketplace.super_moderation`` audit event (the
route's own read-backs pass ``record=False``). Open question for
the spec owner (progress D-05): whether ``disable`` of a user-published item should stay
an operator takedown (today it is FGA-only when enforced).
"""

from typing import Literal
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.dependencies import CurrentUser
from app.models.marketplace import MarketplaceItem
from app.services import audit_service

ModerationAction = Literal["disable", "enable", "listing"]
AUDIT_ACTION = "marketplace.super_moderation"


def _scope(action: ModerationAction):  # noqa: ANN202 - SQLAlchemy clause
    if action == "listing":
        return None
    return and_(
        MarketplaceItem.is_system.is_(True),
        MarketplaceItem.org_id.is_(None),
        MarketplaceItem.tenant_id.is_(None),
    )


async def super_moderation(
    db: AsyncSession,
    user: CurrentUser,
    item_id: UUID,
    action: ModerationAction,
    *,
    record: bool = True,
) -> MarketplaceItem | None:
    """The item when ``user`` is an operator and ``action`` reaches it; else ``None``.

    ``None`` means "not a moderation case": the caller falls back to the OpenFGA decision.
    """
    if not user.is_super_user:
        return None
    query = select(MarketplaceItem).where(MarketplaceItem.id == item_id)
    scope = _scope(action)
    if scope is not None:
        query = query.where(scope)
    row = await db.scalar(
        query.options(
            selectinload(MarketplaceItem.latest_version),
            selectinload(MarketplaceItem.acl_entries),
        ).execution_options(autoflush=False, populate_existing=True)
    )
    if row is None or not record:  # read-backs of the same decision are not re-recorded
        return row
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        action=AUDIT_ACTION,
        target_type="marketplace_item",
        target_id=row.id,
        target_name_snapshot=row.name,
        outcome="success",
        reason_code=f"super_moderation.{action}",
        metadata={"moderation": action, "is_system": row.is_system},
    )
    return row
