"""Typed catalog sources and SQL observations for captured read boundaries."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Unpack, assert_never

import pytest
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext, resolve_org_context
from app.dependencies import CurrentUser
from app.marketplace import service as catalog
from app.marketplace.schemas import MarketplaceItemListFilters
from app.models.marketplace import MarketplaceItem, MarketplaceVersion
from app.models.tool import Tool
from tests.authz.blueprint_preflight_fixture import SqlExecution

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

type CatalogAction = Literal["detail", "version", "list", "page", "versions", "project"]


@dataclass(frozen=True, slots=True)
class CatalogWitness:
    user: CurrentUser
    context: OrgContext
    other: OrgContext
    client: AsyncClient
    item: MarketplaceItem
    version: MarketplaceVersion


@pytest.fixture
async def catalog_witness(
    db: AsyncSession, org_fixture: OrgFixture, raw_client: AsyncClient
) -> CatalogWitness:
    context = await resolve_org_context(db, org_fixture.user, cookie=str(org_fixture.first))
    other = await resolve_org_context(db, org_fixture.user, cookie=str(org_fixture.second))
    item = MarketplaceItem(
        owner_user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        resource_type="agent",
        name="Owned catalog fixture",
        slug="owned-catalog-fixture",
        visibility="private",
        status="published",
        is_system=False,
    )
    db.add(item)
    await db.flush()
    version = MarketplaceVersion(
        item_id=item.id,
        version_label="1",
        version_number=1,
        resource_type="agent",
        payload_kind="agent_spec",
        payload={"system_prompt": "private-fixture-marker"},
        content_hash="f" * 64,
        size_bytes=1,
        created_by=context.user_id,
    )
    db.add(version)
    await db.flush()
    item.latest_version_id = version.id
    await db.commit()
    current = await catalog.get_item(db, user=org_fixture.user, context=context, item_id=item.id)
    assert current is item
    return CatalogWitness(org_fixture.user, context, other, raw_client, item, version)


def pending_tool(witness: CatalogWitness) -> Tool:
    context = witness.context
    return Tool(
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        name="Unrelated catalog pending fixture",
        definition_key="http_request",
    )


async def consume_catalog(db: AsyncSession, witness: CatalogWitness, action: CatalogAction) -> bool:
    user, context, item = witness.user, witness.context, witness.item
    match action:
        case "detail":
            result = await catalog.get_item(db, user=user, context=context, item_id=item.id)
            return result is not None and result.id == item.id
        case "version":
            detail = await catalog.get_version(
                db, user=user, context=context, version_id=witness.version.id
            )
            return (
                detail is not None
                and detail.payload.get("system_prompt") == "private-fixture-marker"
            )
        case "list":
            rows = await catalog.list_items(
                db, user=user, context=context, filters=MarketplaceItemListFilters()
            )
            return any(row.id == item.id for row in rows)
        case "page":
            page = await catalog.list_items_page(
                db, user=user, context=context, filters=MarketplaceItemListFilters()
            )
            return any(row.id == item.id for row in page.items)
        case "versions":
            versions = await catalog.list_versions(db, user=user, context=context, item_id=item.id)
            return versions is not None and any(row.id == witness.version.id for row in versions)
        case "project":
            projected = await catalog.project_item(db, item=item, user=user, context=context)
            return projected.id == item.id
        case unreachable:
            assert_never(unreachable)


@contextmanager
def catalog_sql(db: AsyncSession) -> Iterator[list[str]]:
    statements: list[str] = []

    def observe(**execution: Unpack[SqlExecution]) -> None:
        statement = execution["statement"]
        lowered = statement.lower().lstrip()
        if lowered.startswith(("insert ", "update ", "delete ")) or (
            lowered.startswith("select ")
            and any(
                table in lowered
                for table in (
                    "marketplace_items",
                    "marketplace_versions",
                    "marketplace_installations",
                )
            )
        ):
            statements.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", observe, named=True)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", observe)
