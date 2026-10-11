"""Actual private marketplace source and incomplete installation target witnesses."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.marketplace import install_service
from app.marketplace.schemas import InstallMarketplaceItemIn
from app.models.agent_blueprint import AgentBlueprint
from app.models.tool import Tool
from app.models.user import User

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_scope import AgentCase

pytest_plugins = ["tests.authz.test_private_agent_scope"]


@pytest.mark.parametrize("foreign", [False, True])
@pytest.mark.parametrize("operator", [False, True])
async def test_private_source_item_cannot_be_installed_across_current_company(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    agent_case: AgentCase,
    foreign: bool,
    operator: bool,
) -> None:
    own, other = private_scopes.first, private_scopes.second
    csrf = agent_case.client.cookies.get("moldy_csrf")
    assert csrf is not None
    headers = {"X-CSRF-Token": csrf}
    marker = "F4_PRIVATE_COMPANY_A_SYSTEM_PROMPT"
    amended = await agent_case.client.put(
        f"/api/agents/{agent_case.agent_id}", json={"system_prompt": marker}, headers=headers
    )
    assert amended.status_code == 200, amended.text
    published = await agent_case.client.post(
        f"/api/marketplace/items/from-agent/{agent_case.agent_id}",
        json={"name": "F4 private source", "visibility": "private"},
        headers=headers,
    )
    assert published.status_code == 201, published.text
    item_id = published.json()["id"]
    await db.execute(update(User).where(User.id == own.user_id).values(is_super_user=operator))
    await db.commit()
    actor = await agent_case.client.get("/api/auth/me")
    assert actor.status_code == 200 and actor.json()["is_super_user"] is operator
    current = other if foreign else own
    # Remove AgentCase's synthetic domainless cookie before the server sets its host cookie.
    agent_case.client.cookies.delete(ACTIVE_ORG_COOKIE)
    switched = await agent_case.client.post(
        "/api/me/active-org",
        json={"org_id": str(current.org_id)},
        headers=headers,
    )
    assert switched.status_code == 200, switched.text
    assert agent_case.client.cookies.get(ACTIVE_ORG_COOKIE) == str(current.org_id)
    own_agent = await agent_case.client.get(f"/api/agents/{agent_case.agent_id}")
    assert own_agent.status_code == (404 if foreign else 200), own_agent.text
    detail = await agent_case.client.get(f"/api/marketplace/items/{item_id}")
    assert detail.status_code == (404 if foreign else 200), detail.text
    # When the same exact item ID is submitted to the actual installer under B/A.
    installed = await agent_case.client.post(
        f"/api/marketplace/items/{item_id}/install",
        json={},
        headers=headers,
    )
    rows = list((await db.scalars(select(AgentBlueprint))).all())
    leaked = [row for row in rows if row.org_id == current.org_id and marker in str(row.spec)]
    if foreign:
        assert installed.status_code == 404 and not leaked, installed.text
    else:
        assert installed.status_code == 201 and len(leaked) == 1, installed.text


async def test_missing_install_target_denies_before_staged_private_sql_write(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    org_fixture: OrgFixture,
) -> None:
    own = private_scopes.first
    pending = Tool(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="F4 pending private install marker",
        definition_key="http_request",
    )
    db.add(pending)
    statements = Mock()
    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", statements)
    denied = False
    try:
        await install_service.install_item(
            db,
            item_id=uuid4(),
            user=CurrentUser(
                id=own.user_id, email=org_fixture.user.email, name=org_fixture.user.name
            ),
            context=own,
            body=InstallMarketplaceItemIn(),
        )
    except AppError as error:
        assert error.status == 404
        denied = True
    finally:
        event.remove(engine, "before_cursor_execute", statements)
    writes = [
        call.args[2]
        for call in statements.call_args_list
        if call.args[2].lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE "))
    ]
    assert pending in db.new
    await db.rollback()
    assert denied and not writes, writes
