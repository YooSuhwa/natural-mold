"""Catalog reads authorize persisted targets without flushing unrelated writes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.marketplace import service as catalog
from app.models.marketplace import MarketplaceItem
from app.models.tool import Tool
from tests.authz.catalog_actor_fixture import catalog_sql, consume_catalog, pending_tool
from tests.authz.catalog_actor_fixture import catalog_witness as catalog_witness
from tests.authz.org_context_fixture import org_fixture as org_fixture

if TYPE_CHECKING:
    from tests.authz.catalog_actor_fixture import CatalogAction, CatalogWitness

type MissingAction = Literal["detail", "version", "versions"]


@pytest.mark.parametrize("action", ["detail", "project", "list", "page"])
async def test_catalog_accepted_read_preserves_pending_metadata(
    db: AsyncSession, catalog_witness: CatalogWitness, action: CatalogAction
) -> None:
    witness = catalog_witness
    witness.item.name = "Caller staged accepted catalog metadata"
    assert await consume_catalog(db, witness, action)
    assert witness.item.name == "Caller staged accepted catalog metadata"
    await db.flush()


@pytest.mark.parametrize(
    "case",
    [
        (a, moved)
        for a in ("detail", "version", "versions", "project", "list", "page")
        for moved in (False, True)
    ],
)
async def test_catalog_target_uses_persisted_scope(
    db: AsyncSession, catalog_witness: CatalogWitness, case: tuple[CatalogAction, bool]
) -> None:
    action, moved = case
    witness = catalog_witness
    if moved:
        await db.execute(
            update(MarketplaceItem)
            .where(MarketplaceItem.id == witness.item.id)
            .values(org_id=witness.other.org_id, tenant_id=witness.other.tenant_id)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        assert witness.item.org_id == witness.context.org_id
    pending = pending_tool(witness)
    db.add(pending)
    consumed = False
    with catalog_sql(db) as statements:
        try:
            consumed = await consume_catalog(db, witness, action)
        except AppError as error:
            assert error.status == 404
    if moved:
        assert not consumed
        assert not any(
            sql.lower().lstrip().startswith(("insert ", "update ", "delete ")) for sql in statements
        )
        assert pending in db.new
    else:
        assert consumed
        await db.flush()
        assert await db.scalar(select(Tool.id).where(Tool.id == pending.id)) == pending.id


@pytest.mark.parametrize("action", ["detail", "version", "versions"])
async def test_catalog_missing_target_retains_pending_write(
    db: AsyncSession, catalog_witness: CatalogWitness, action: MissingAction
) -> None:
    witness = catalog_witness
    pending = pending_tool(witness)
    db.add(pending)
    target_id = uuid4()
    with catalog_sql(db) as statements:
        match action:
            case "version":
                result = await catalog.get_version(
                    db, user=witness.user, context=witness.context, version_id=target_id
                )
            case "versions":
                result = await catalog.list_versions(
                    db, user=witness.user, context=witness.context, item_id=target_id
                )
            case "detail":
                result = await catalog.get_item(
                    db, user=witness.user, context=witness.context, item_id=target_id
                )
            case unreachable:
                assert_never(unreachable)
    assert result is None
    assert not any(
        sql.lower().lstrip().startswith(("insert ", "update ", "delete ")) for sql in statements
    )
    assert pending in db.new
