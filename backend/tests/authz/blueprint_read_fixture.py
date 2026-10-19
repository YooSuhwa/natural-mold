"""Committed private Blueprint roots and catalog installation for read controls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.dependencies import CurrentUser
from app.models.agent_blueprint import AgentBlueprint
from app.models.marketplace import MarketplaceInstallation, MarketplaceItem, MarketplaceVersion
from tests.authz.blueprint_read_support import blueprint_root

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


@dataclass(frozen=True, slots=True)
class BlueprintReadCase:
    context: OrgContext
    other: OrgContext
    actor: CurrentUser
    first: AgentBlueprint
    latest: AgentBlueprint
    foreign: AgentBlueprint
    hidden: AgentBlueprint
    installation: MarketplaceInstallation


@pytest.fixture
async def blueprint_read_case(db: AsyncSession, private_scopes: PrivateScopes) -> BlueprintReadCase:
    own, other = private_scopes.first, private_scopes.second
    first = blueprint_root(own, "First", datetime(2026, 1, 1))
    latest = blueprint_root(own, "Latest", datetime(2026, 2, 1))
    foreign = blueprint_root(other, "Foreign", datetime(2026, 3, 1))
    hidden = blueprint_root(own, "Uninstalled", datetime(2026, 4, 1))
    hidden.install_status = "uninstalled"
    item = MarketplaceItem(
        id=uuid4(),
        resource_type="agent",
        name="Blueprint catalog",
        slug="blueprint-read",
        is_system=True,
        owner_user_id=None,
        org_id=None,
        tenant_id=None,
        visibility="system",
        status="published",
    )
    db.add_all([first, latest, foreign, hidden, item])
    await db.flush()
    version = MarketplaceVersion(
        id=uuid4(),
        item_id=item.id,
        version_label="1",
        version_number=1,
        resource_type="agent",
        payload_kind="agent_spec",
        content_hash="b" * 64,
        credential_requirements=[],
    )
    db.add(version)
    await db.flush()
    installation = MarketplaceInstallation(
        id=uuid4(),
        item_id=item.id,
        version_id=version.id,
        resource_type="agent",
        installed_agent_blueprint_id=latest.id,
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        install_status="needs_setup",
        is_dirty=True,
    )
    db.add(installation)
    await db.commit()
    return BlueprintReadCase(
        own,
        other,
        CurrentUser(id=own.user_id, email="fixture@example.com", name="Fixture"),
        first,
        latest,
        foreign,
        hidden,
        installation,
    )
