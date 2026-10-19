"""Current marketplace versions are children of an explicit catalog source."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.marketplace import MarketplaceVersion


@dataclass(frozen=True, slots=True)
class ItemVersionTarget:
    item_id: UUID
    version_id: UUID


async def load_item_versions(
    db: AsyncSession, targets: frozenset[ItemVersionTarget]
) -> list[MarketplaceVersion]:
    """Batch only the explicit source/version pairs supplied by scoped installations."""
    if not targets:
        return []
    return list(
        (
            await db.scalars(
                select(MarketplaceVersion)
                .where(
                    tuple_(MarketplaceVersion.item_id, MarketplaceVersion.id).in_(
                        [(target.item_id, target.version_id) for target in targets]
                    )
                )
                .execution_options(autoflush=False, populate_existing=True)
            )
        ).all()
    )


async def load_item_version(
    db: AsyncSession, item_id: UUID, version_id: UUID | None
) -> MarketplaceVersion | None:
    """Resolve a persisted association without flushing unrelated staged writes."""
    if version_id is None:
        return None
    return await db.scalar(
        select(MarketplaceVersion)
        .where(MarketplaceVersion.id == version_id, MarketplaceVersion.item_id == item_id)
        .execution_options(autoflush=False, populate_existing=True)
    )
