from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import is_postgres
from app.models.marketplace import MarketplaceItem


async def lock_marketplace_item_install(db: AsyncSession, *, item_id: uuid.UUID) -> None:
    if not is_postgres(db):
        return
    await db.execute(
        select(MarketplaceItem.id).where(MarketplaceItem.id == item_id).with_for_update()
    )
