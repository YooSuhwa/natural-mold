"""Installation readers authorize the current actor before private hydration."""

from dataclasses import dataclass, replace
from typing import Final, Literal, assert_never
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.catalog_scope import load_private_installation
from app.authz.org_context import OrgContext
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.marketplace import install_service
from app.marketplace.schemas import UpdateMarketplaceInstallationIn
from app.models.marketplace import MarketplaceInstallation
from app.models.organization import OrganizationMember, TenantMember
from app.models.tool import Tool
from app.models.user import User
from tests.authz.private_scope_fixture import PrivateScopes
from tests.authz.queue_resource_fixture import queue_resource_sql
from tests.authz.test_private_installation_summary_scope import InstalledFixture
from tests.conftest import TestSession

pytest_plugins = ["tests.authz.test_private_installation_summary_scope"]

type Action = Literal["load", "update", "delete"]
type State = Literal["active", "org_removed", "tenant_removed", "inactive"]
ACTIONS: Final[tuple[Action, ...]] = ("load", "update", "delete")
STATES: Final[tuple[State, ...]] = ("active", "org_removed", "tenant_removed", "inactive")


@dataclass(frozen=True, slots=True)
class InstallationTarget:
    installation_id: UUID
    context: OrgContext
    skill_id: UUID
    version_id: UUID


@dataclass(frozen=True, slots=True)
class InstallationCase:
    action: Action
    state: State
    operator: bool


@pytest.fixture
async def installation_target(
    db: AsyncSession, installed_fixture: InstalledFixture, private_scopes: PrivateScopes
) -> InstallationTarget:
    row = (
        await db.execute(
            select(MarketplaceInstallation.id, MarketplaceInstallation.version_id).where(
                MarketplaceInstallation.installed_skill_id == installed_fixture.skill.id
            )
        )
    ).one()
    return InstallationTarget(row[0], private_scopes.first, installed_fixture.skill.id, row[1])


async def _apply(db: AsyncSession, target: InstallationTarget, case: InstallationCase) -> bool:
    context = replace(target.context, is_super_user=case.operator)
    user = CurrentUser(
        id=context.user_id,
        email="install-order@example.test",
        name="Installer",
        is_super_user=case.operator,
    )
    match case.action:
        case "load":
            return await load_private_installation(db, target.installation_id, context) is not None
        case "update":
            await install_service.update_installation(
                db,
                installation_id=target.installation_id,
                user=user,
                context=context,
                body=UpdateMarketplaceInstallationIn(strategy="keep_current"),
            )
        case "delete":
            await install_service.delete_installation(
                db,
                installation_id=target.installation_id,
                user=user,
                context=context,
            )
        case unreachable:
            assert_never(unreachable)
    return True


@pytest.mark.parametrize(
    "case",
    [
        InstallationCase(action, state, operator)
        for action in ACTIONS
        for state in STATES
        for operator in (False, True)
    ],
)
async def test_installation_checks_current_actor_before_private_row(
    db: AsyncSession, installation_target: InstallationTarget, case: InstallationCase
) -> None:
    target = installation_target
    context = target.context
    async with TestSession() as writer:
        await writer.execute(
            update(User).where(User.id == context.user_id).values(is_super_user=case.operator)
        )
        match case.state:
            case "active":
                await writer.commit()
            case "org_removed":
                await writer.execute(
                    update(OrganizationMember)
                    .where(
                        OrganizationMember.org_id == context.org_id,
                        OrganizationMember.user_id == context.user_id,
                    )
                    .values(status="removed")
                )
                await writer.commit()
            case "tenant_removed":
                await writer.execute(
                    update(TenantMember)
                    .where(
                        TenantMember.tenant_id == context.tenant_id,
                        TenantMember.user_id == context.user_id,
                    )
                    .values(status="removed")
                )
                await writer.commit()
            case "inactive":
                await writer.execute(
                    update(User).where(User.id == context.user_id).values(is_active=False)
                )
                await writer.commit()
            case unreachable:
                assert_never(unreachable)
    pending_id = uuid4()
    pending = Tool(
        id=pending_id,
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        name="Installation staged write",
        definition_key="http_request",
    )
    db.add(pending)
    allowed = False
    with queue_resource_sql(db) as statements:
        try:
            allowed = await _apply(db, target, case)
        except AppError as error:
            assert error.status == 404
    assert allowed == (case.state == "active")
    assert pending in db.new
    assert not any(
        s.lstrip().lower().startswith(("insert", "update", "delete")) for s in statements
    )
    private_queries = [
        index
        for index, statement in enumerate(statements)
        if "marketplace_installations.install_status" in statement.lower()
    ]
    if allowed:
        actor_index = next(
            i for i, s in enumerate(statements) if "organization_members" in s.lower()
        )
        assert private_queries and actor_index < private_queries[0]
        await db.commit()
    else:
        assert private_queries == [], "Revoked actor hydrated private installation metadata"
        await db.rollback()
    async with TestSession() as verifier:
        assert (
            await verifier.scalar(select(Tool.id).where(Tool.id == pending_id)) is not None
        ) == allowed
        row = (
            await verifier.execute(
                select(
                    MarketplaceInstallation.installed_skill_id, MarketplaceInstallation.version_id
                ).where(MarketplaceInstallation.id == target.installation_id)
            )
        ).one()
        assert tuple(row) == (target.skill_id, target.version_id)
