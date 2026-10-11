"""Actual m83 installation mutations refresh actors before private row hydration."""

from dataclasses import dataclass
from typing import Literal, assert_never
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext, resolve_org_context
from app.config import settings
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.marketplace import install_service
from app.marketplace.schemas import UpdateMarketplaceInstallationIn
from app.models.marketplace import MarketplaceInstallation, MarketplaceItem, MarketplaceVersion
from app.models.organization import OrganizationMember
from app.models.skill import Skill
from app.models.tool import Tool
from app.models.user import User
from tests.authz.queue_resource_fixture import queue_resource_sql
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration
type NativeAction = Literal["update", "delete"]


@dataclass(frozen=True, slots=True)
class NativeInstallation:
    id: UUID
    context: OrgContext
    skill_id: UUID
    version_id: UUID


async def _seed_installation(db: AsyncSession, context: OrgContext) -> NativeInstallation:
    item = MarketplaceItem(
        resource_type="skill",
        name="Installation read order",
        slug="native-install-order",
        is_system=True,
        owner_user_id=None,
        org_id=None,
        tenant_id=None,
        visibility="system",
        status="published",
    )
    skill = Skill(
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        name="Installed read order",
        slug="native-install-order",
        kind="text",
        credential_requirements=[],
    )
    db.add_all([item, skill])
    await db.flush()
    version = MarketplaceVersion(
        item_id=item.id,
        version_label="fixture-1",
        version_number=1,
        resource_type="skill",
        payload_kind="skill_package",
        content_hash="a" * 64,
        credential_requirements=[],
    )
    db.add(version)
    await db.flush()
    item.latest_version_id = version.id
    installation = MarketplaceInstallation(
        item_id=item.id,
        version_id=version.id,
        resource_type="skill",
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        installed_skill_id=skill.id,
    )
    db.add(installation)
    await db.commit()
    return NativeInstallation(installation.id, context, skill.id, version.id)


async def _mutate(db: AsyncSession, target: NativeInstallation, action: NativeAction) -> None:
    context = target.context
    user = CurrentUser(
        id=context.user_id,
        email="native-install-order@example.com",
        name="Installer",
        is_super_user=context.is_super_user,
    )
    match action:
        case "update":
            await install_service.update_installation(
                db,
                installation_id=target.id,
                user=user,
                context=context,
                body=UpdateMarketplaceInstallationIn(strategy="keep_current"),
            )
        case "delete":
            await install_service.delete_installation(
                db,
                installation_id=target.id,
                user=user,
                context=context,
            )
        case unreachable:
            assert_never(unreachable)


@pytest.mark.parametrize(
    "case",
    [
        (action, operator, revoked)
        for action in ("update", "delete")
        for operator in (False, True)
        for revoked in (False, True)
    ],
)
async def test_native_installation_actor_precedes_private_row_and_preserves_caller_writes(
    monkeypatch: pytest.MonkeyPatch, case: tuple[NativeAction, bool, bool]
) -> None:
    action, operator, revoked = case
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    monkeypatch.setattr(settings, "allow_first_user_as_admin", operator)
    async with signup_app(monkeypatch) as stack:
        response = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-install-order@example.com",
                "name": "Installer",
                "password": "native-install-order-fixture-password",
            },
        )
        assert response.status_code == 201, response.text
        user_id = UUID(response.json()["user"]["id"])
        async with stack.sessions() as db:
            user = await db.get(User, user_id)
            assert user is not None and user.is_super_user is operator
            context = await resolve_org_context(
                db,
                CurrentUser(id=user.id, email=user.email, name=user.name, is_super_user=operator),
                cookie=None,
            )
            target = await _seed_installation(db, context)
            if revoked:
                async with stack.sessions() as writer:
                    await writer.execute(
                        update(OrganizationMember)
                        .where(
                            OrganizationMember.org_id == context.org_id,
                            OrganizationMember.user_id == user_id,
                        )
                        .values(status="removed")
                    )
                    await writer.commit()
            pending_id = uuid4()
            pending = Tool(
                id=pending_id,
                user_id=user_id,
                org_id=context.org_id,
                tenant_id=context.tenant_id,
                name="Native installation caller write",
                definition_key="http_request",
            )
            db.add(pending)
            allowed = False
            with queue_resource_sql(db) as statements:
                try:
                    await _mutate(db, target, action)
                    allowed = True
                except AppError as error:
                    assert error.status == 404
            assert allowed is not revoked
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
                assert private_queries == [], "Revoked actor hydrated native private installation"
                await db.rollback()
            async with stack.sessions() as verifier:
                assert (
                    await verifier.scalar(select(Tool.id).where(Tool.id == pending_id)) is not None
                ) == allowed
                installation = await verifier.get(MarketplaceInstallation, target.id)
                assert installation is not None
                assert (installation.installed_skill_id, installation.version_id) == (
                    target.skill_id,
                    target.version_id,
                )
                match action:
                    case "delete":
                        expected_status = "uninstalled" if allowed else "active"
                    case "update":
                        expected_status = "active"
                    case unreachable:
                        assert_never(unreachable)
                assert installation.install_status == expected_status
                assert await verifier.get(Skill, target.skill_id) is not None
