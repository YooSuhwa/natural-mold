"""A cached/reparented version cannot escape the authorized source item."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import Mock
from uuid import UUID

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.marketplace import MarketplaceItem, MarketplaceVersion

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_scope import AgentCase
pytest_plugins = ["tests.authz.test_private_agent_scope"]


@pytest.mark.parametrize("explicit", [False, True])
async def test_reparented_source_version_never_installs(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    agent_case: AgentCase,
    explicit: bool,
) -> None:
    csrf = agent_case.client.cookies.get("moldy_csrf")
    assert csrf is not None
    headers = {"X-CSRF-Token": csrf}
    published = await agent_case.client.post(
        f"/api/marketplace/items/from-agent/{agent_case.agent_id}",
        json={"name": "Original source", "visibility": "private"},
        headers=headers,
    )
    assert published.status_code == 201
    own_id = UUID(published.json()["id"])
    item = await db.get(MarketplaceItem, own_id)
    assert item is not None and item.latest_version_id is not None
    version = await db.get(MarketplaceVersion, item.latest_version_id)
    assert version is not None
    other = private_scopes.second
    foreign = MarketplaceItem(
        resource_type="agent",
        owner_user_id=other.user_id,
        org_id=other.org_id,
        tenant_id=other.tenant_id,
        is_system=False,
        name="Other source",
        slug="other-source",
        visibility="private",
        status="published",
    )
    db.add(foreign)
    await db.flush()
    version_id = version.id
    await db.execute(
        update(MarketplaceVersion)
        .where(MarketplaceVersion.id == version_id)
        .values(item_id=foreign.id)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    assert version.item_id == own_id
    assert (
        await db.scalar(
            select(MarketplaceVersion.item_id).where(MarketplaceVersion.id == version_id)
        )
        == foreign.id
    )
    statements = Mock()
    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", statements)
    try:
        response = await agent_case.client.post(
            f"/api/marketplace/items/{own_id}/install",
            json={"version_id": str(version_id)} if explicit else {},
            headers=headers,
        )
    finally:
        event.remove(engine, "before_cursor_execute", statements)
    assert response.status_code == 404, response.text
    version_reads = [
        call.args[2]
        for call in statements.call_args_list
        if "FROM marketplace_versions" in call.args[2]
    ]
    assert version_reads
    assert all("marketplace_versions.item_id =" in sql for sql in version_reads), version_reads
