"""Current scalar projections preserve cached caller state and read-only rows."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.agent_blueprint import AgentBlueprint
from app.models.marketplace import MarketplaceInstallation
from app.models.tool import Tool
from app.routers.agent_blueprints import get_agent_blueprint, list_agent_blueprints
from tests.authz.blueprint_read_support import pending_tool

if TYPE_CHECKING:
    from tests.authz.blueprint_read_fixture import BlueprintReadCase

pytest_plugins = ["tests.authz.blueprint_read_fixture"]


async def test_list_and_detail_preserve_normal_projection_and_caller_flush(
    db: AsyncSession, blueprint_read_case: BlueprintReadCase
) -> None:
    case = blueprint_read_case
    pending = pending_tool(db, case.context)
    rows = await list_agent_blueprints(db=db, user=case.actor, context=case.context)
    assert [row.id for row in rows] == [case.latest.id, case.first.id]
    assert all(row.spec is None for row in rows)
    assert rows[0].installation_id == case.installation.id
    assert rows[0].install_status == "needs_setup" and rows[0].is_dirty
    assert rows[1].installation_id is None and rows[1].install_status == "active"
    detail = await get_agent_blueprint(case.latest.id, db=db, user=case.actor, context=case.context)
    assert detail.spec == case.latest.spec
    assert detail.tags == ["fixture"] and detail.categories == ["read"]
    assert detail.description == "Persisted description" and detail.icon_id == "bot"
    assert detail.created_agent_count == 0 and detail.spec_hash == "b" * 64
    with db.no_autoflush:
        assert await db.scalar(select(Tool.id).where(Tool.id == pending.id)) is None
        assert await db.scalar(select(func.count()).select_from(AgentBlueprint)) == 4
        assert await db.scalar(select(func.count()).select_from(MarketplaceInstallation)) == 1
    assert pending in db.new
    await db.flush()
    assert await db.scalar(select(Tool.id).where(Tool.id == pending.id)) == pending.id


async def test_current_metadata_does_not_refresh_away_staged_root_and_installation_edits(
    db: AsyncSession, blueprint_read_case: BlueprintReadCase
) -> None:
    case = blueprint_read_case
    await db.execute(
        update(AgentBlueprint)
        .where(AgentBlueprint.id == case.latest.id)
        .values(name="Current DB name", created_agent_count=7, spec={"current": True})
        .execution_options(synchronize_session=False)
    )
    await db.execute(
        update(MarketplaceInstallation)
        .where(MarketplaceInstallation.id == case.installation.id)
        .values(install_status="disabled", is_dirty=False)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    assert case.latest.name == "Latest" and case.installation.install_status == "needs_setup"
    case.latest.description = "Staged description"
    case.installation.install_status = "active"
    rows = await list_agent_blueprints(db=db, user=case.actor, context=case.context)
    detail = await get_agent_blueprint(case.latest.id, db=db, user=case.actor, context=case.context)
    assert rows[0].name == detail.name == "Current DB name"
    assert detail.created_agent_count == 7 and detail.spec == {"current": True}
    assert rows[0].install_status == detail.install_status == "disabled"
    assert not detail.is_dirty and not rows[0].is_dirty
    assert detail.description == "Persisted description"
    assert case.latest.description == "Staged description" and case.latest in db.dirty
    assert case.installation.install_status == "active" and case.installation in db.dirty
    await db.flush()
    assert (
        await db.scalar(
            select(AgentBlueprint.description).where(AgentBlueprint.id == case.latest.id)
        )
        == "Staged description"
    )
    assert (
        await db.scalar(
            select(MarketplaceInstallation.install_status).where(
                MarketplaceInstallation.id == case.installation.id
            )
        )
        == "active"
    )


@pytest.mark.parametrize("scope_change", ["root", "installation", "uninstalled"])
async def test_current_scope_or_soft_uninstall_overrides_cached_orm_objects(
    db: AsyncSession,
    blueprint_read_case: BlueprintReadCase,
    scope_change: Literal["root", "installation", "uninstalled"],
) -> None:
    case = blueprint_read_case
    match scope_change:
        case "root":
            await db.execute(
                update(AgentBlueprint)
                .where(AgentBlueprint.id == case.latest.id)
                .values(org_id=case.other.org_id, tenant_id=case.other.tenant_id)
                .execution_options(synchronize_session=False)
            )
        case "installation":
            await db.execute(
                update(MarketplaceInstallation)
                .where(MarketplaceInstallation.id == case.installation.id)
                .values(org_id=case.other.org_id, tenant_id=case.other.tenant_id)
                .execution_options(synchronize_session=False)
            )
        case "uninstalled":
            await db.execute(
                update(MarketplaceInstallation)
                .where(MarketplaceInstallation.id == case.installation.id)
                .values(install_status="uninstalled")
                .execution_options(synchronize_session=False)
            )
        case unreachable:
            assert_never(unreachable)
    await db.commit()
    pending = pending_tool(db, case.context)
    rows = await list_agent_blueprints(db=db, user=case.actor, context=case.context)
    if scope_change == "root":
        assert [row.id for row in rows] == [case.first.id]
        with pytest.raises(AppError) as error:
            await get_agent_blueprint(case.latest.id, db=db, user=case.actor, context=case.context)
        assert error.value.status == 404
    else:
        detail = await get_agent_blueprint(
            case.latest.id, db=db, user=case.actor, context=case.context
        )
        assert rows[0].installation_id is None and detail.installation_id is None
        assert detail.install_status == "active" and not detail.is_dirty
    assert pending in db.new
    with db.no_autoflush:
        assert await db.scalar(select(Tool.id).where(Tool.id == pending.id)) is None
