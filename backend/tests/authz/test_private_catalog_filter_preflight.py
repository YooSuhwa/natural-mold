"""Authorization precedes display filters without discarding accepted staged metadata."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.marketplace import service as catalog
from app.marketplace.schemas import MarketplaceItemListFilters
from app.models.marketplace import MarketplaceItem
from app.models.tool import Tool
from tests.authz.catalog_actor_fixture import catalog_sql, pending_tool
from tests.authz.catalog_actor_fixture import catalog_witness as catalog_witness
from tests.authz.org_context_fixture import org_fixture as org_fixture

if TYPE_CHECKING:
    from tests.authz.catalog_actor_fixture import CatalogWitness

type FilterAction = Literal["list", "page"]
type FilterState = Literal["staged", "flushed", "moved"]


@pytest.mark.parametrize(
    "case",
    [(action, state) for action in ("list", "page") for state in ("staged", "flushed", "moved")],
)
async def test_catalog_authority_preflight_does_not_use_pending_display_filters(
    db: AsyncSession, catalog_witness: CatalogWitness, case: tuple[FilterAction, FilterState]
) -> None:
    action, state = case
    witness = catalog_witness
    name = "Accepted pending search display"
    witness.item.name = name
    match state:
        case "staged":
            pass
        case "flushed":
            await db.flush()
        case "moved":
            await db.execute(
                update(MarketplaceItem)
                .where(MarketplaceItem.id == witness.item.id)
                .values(org_id=witness.other.org_id, tenant_id=witness.other.tenant_id)
                .execution_options(autoflush=False, synchronize_session=False)
            )
            await db.commit()
        case unreachable:
            assert_never(unreachable)
    pending = pending_tool(witness)
    db.add(pending)
    filters = MarketplaceItemListFilters(q=name)
    with catalog_sql(db) as statements:
        match action:
            case "list":
                rows = await catalog.list_items(
                    db, user=witness.user, context=witness.context, filters=filters
                )
            case "page":
                page = await catalog.list_items_page(
                    db, user=witness.user, context=witness.context, filters=filters
                )
                rows = page.items
            case unreachable:
                assert_never(unreachable)
    if state == "moved":
        assert rows == []
        assert pending in db.new
        assert not any(
            sql.lower().lstrip().startswith(("insert ", "update ", "delete ")) for sql in statements
        )
    else:
        assert len(rows) == 1 and rows[0].id == witness.item.id and rows[0].name == name
        assert witness.item.name == name
        await db.flush()
        assert await db.scalar(select(Tool.id).where(Tool.id == pending.id)) == pending.id
        assert (
            await db.scalar(
                select(MarketplaceItem.name).where(MarketplaceItem.id == witness.item.id)
            )
            == name
        )
