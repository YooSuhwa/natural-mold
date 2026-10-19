"""Owner, soft-delete and reinstall controls for the shared read boundary."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.agent_blueprint import AgentBlueprint
from app.models.marketplace import MarketplaceInstallation
from app.models.tool import Tool
from app.models.user import User
from app.routers.agent_blueprints import get_agent_blueprint, list_agent_blueprints
from tests.authz.blueprint_read_support import pending_tool

if TYPE_CHECKING:
    from tests.authz.blueprint_read_fixture import BlueprintReadCase

pytest_plugins = ["tests.authz.blueprint_read_fixture"]


async def test_soft_uninstalled_root_is_missing_without_flushing_or_deleting_rows(
    db: AsyncSession, blueprint_read_case: BlueprintReadCase
) -> None:
    case = blueprint_read_case
    pending = pending_tool(db, case.context)
    with pytest.raises(AppError) as error:
        await get_agent_blueprint(case.hidden.id, db=db, user=case.actor, context=case.context)
    assert error.value.status == 404
    with db.no_autoflush:
        assert await db.scalar(select(Tool.id).where(Tool.id == pending.id)) is None
        assert (
            await db.scalar(select(AgentBlueprint.id).where(AgentBlueprint.id == case.hidden.id))
            == case.hidden.id
        )
    assert pending in db.new


async def test_reinstall_omits_stale_installation_but_preserves_physical_rows(
    db: AsyncSession, blueprint_read_case: BlueprintReadCase
) -> None:
    case = blueprint_read_case
    stale = MarketplaceInstallation(
        id=uuid4(),
        item_id=case.installation.item_id,
        version_id=case.installation.version_id,
        resource_type="agent",
        installed_agent_blueprint_id=case.latest.id,
        user_id=case.context.user_id,
        org_id=case.context.org_id,
        tenant_id=case.context.tenant_id,
        install_status="uninstalled",
        is_dirty=True,
    )
    db.add(stale)
    await db.commit()
    rows = await list_agent_blueprints(db=db, user=case.actor, context=case.context)
    detail = await get_agent_blueprint(case.latest.id, db=db, user=case.actor, context=case.context)
    assert [row.id for row in rows] == [case.latest.id, case.first.id]
    assert rows[0].installation_id == detail.installation_id == case.installation.id
    assert await db.scalar(select(func.count()).select_from(MarketplaceInstallation)) == 2


async def test_operator_has_no_foreign_owner_or_company_blueprint_bypass(
    db: AsyncSession, blueprint_read_case: BlueprintReadCase
) -> None:
    case = blueprint_read_case
    owner = User(id=uuid4(), email="other-blueprint-owner@example.test", name="Other owner")
    db.add(owner)
    await db.flush()
    await db.execute(update(User).where(User.id == case.context.user_id).values(is_super_user=True))
    await db.execute(
        update(AgentBlueprint)
        .where(AgentBlueprint.id == case.latest.id)
        .values(user_id=owner.id)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    context = replace(case.context, is_super_user=True)
    pending = pending_tool(db, context)
    rows = await list_agent_blueprints(db=db, user=case.actor, context=context)
    assert [row.id for row in rows] == [case.first.id]
    for target in (case.latest.id, case.foreign.id):
        with pytest.raises(AppError) as error:
            await get_agent_blueprint(target, db=db, user=case.actor, context=context)
        assert error.value.status == 404
    assert pending in db.new
    with db.no_autoflush:
        assert await db.scalar(select(Tool.id).where(Tool.id == pending.id)) is None
