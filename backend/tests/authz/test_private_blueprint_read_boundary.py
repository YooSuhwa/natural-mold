"""Blueprint GET decisions must precede caller-staged private SQL effects."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.agent_blueprint import AgentBlueprint
from app.models.tool import Tool
from app.routers.agent_blueprints import get_agent_blueprint

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


@pytest.mark.parametrize("case", ["own", "foreign", "missing"])
async def test_detail_decides_target_before_pending_private_insert(
    db: AsyncSession, private_scopes: PrivateScopes, case: Literal["own", "foreign", "missing"]
) -> None:
    own, other = private_scopes.first, private_scopes.second
    owner = own if case == "own" else other
    blueprint = AgentBlueprint(
        id=uuid4(),
        user_id=owner.user_id,
        org_id=owner.org_id,
        tenant_id=owner.tenant_id,
        name="Read boundary",
        spec_hash="b" * 64,
        spec={"agent": {"name": "Read boundary"}},
    )
    db.add(blueprint)
    await db.commit()
    pending = Tool(
        id=uuid4(),
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="Pending private writer",
        definition_key="http_request",
        parameters={"url": "https://example.test/fixture"},
    )
    db.add(pending)
    actor = CurrentUser(id=own.user_id, email="fixture@example.com", name="Fixture")
    target_id = uuid4() if case == "missing" else blueprint.id
    status: int | None = None
    result_id = None
    try:
        response = await get_agent_blueprint(target_id, db=db, user=actor, context=own)
        result_id = response.id
    except AppError as error:
        status = error.status
    with db.no_autoflush:
        stored = await db.scalar(select(Tool.id).where(Tool.id == pending.id))
    print(f"case={case}; status={status}; pending_tool_inserted={stored is not None}")
    assert status == (None if case == "own" else 404)
    assert result_id == (target_id if case == "own" else None)
    assert stored is None, "Blueprint READ executed a caller-staged private INSERT"
    assert pending in db.new
    await db.flush()
    assert await db.scalar(select(Tool.id).where(Tool.id == pending.id)) == pending.id
    await db.rollback()
