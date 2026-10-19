"""Recipients of an item's ACL with their profile names (S16; I-04 keeps the ACL rows)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.principal_names import user_display_names
from app.models.marketplace import MarketplaceItem, MarketplaceItemACL
from app.schemas.marketplace_acl import MarketplaceAclEntry, MarketplaceAclList


async def acl_recipients(db: AsyncSession, item: MarketplaceItem) -> MarketplaceAclList:
    rows = list(
        await db.scalars(
            select(MarketplaceItemACL)
            .where(MarketplaceItemACL.item_id == item.id)
            .order_by(MarketplaceItemACL.created_at, MarketplaceItemACL.user_id)
        )
    )
    names = await user_display_names(db, {row.user_id for row in rows})
    return MarketplaceAclList(
        items=[
            MarketplaceAclEntry(
                user_id=row.user_id, display_name=names.get(row.user_id), permission=row.permission
            )
            for row in rows
        ]
    )
