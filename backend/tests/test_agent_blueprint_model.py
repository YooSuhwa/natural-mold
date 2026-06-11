"""Agent Blueprint persistence contract."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_blueprint import AgentBlueprint
from app.models.marketplace import MarketplaceInstallation, MarketplaceItem, MarketplaceVersion
from app.models.user import User
from tests.conftest import TEST_USER_ID


@pytest.mark.asyncio
async def test_agent_marketplace_installation_points_to_agent_blueprint(
    db: AsyncSession,
) -> None:
    user = User(
        id=TEST_USER_ID,
        email="test@test.com",
        name="Test User",
        hashed_password="h",
        is_active=True,
        is_super_user=True,
    )
    db.add(user)
    item = MarketplaceItem(
        id=uuid.uuid4(),
        resource_type="agent",
        owner_user_id=TEST_USER_ID,
        is_system=False,
        is_listed=False,
        name="Research Agent",
        slug="research-agent",
        visibility="public",
        status="published",
        moderation_status="approved",
        source_kind="user",
    )
    db.add(item)
    await db.flush()
    version = MarketplaceVersion(
        id=uuid.uuid4(),
        item_id=item.id,
        version_label="agent-1",
        version_number=1,
        resource_type="agent",
        payload_kind="agent_spec",
        payload={"schema_version": 1, "resource": "agent_blueprint"},
        storage_path=None,
        content_hash="a" * 64,
        size_bytes=64,
        created_by=TEST_USER_ID,
    )
    db.add(version)
    await db.flush()
    item.latest_version_id = version.id

    blueprint = AgentBlueprint(
        id=uuid.uuid4(),
        user_id=TEST_USER_ID,
        name="Research Agent",
        description="A reusable agent design",
        spec={"schema_version": 1, "resource": "agent_blueprint"},
        spec_hash="a" * 64,
        source_marketplace_item_id=item.id,
        source_marketplace_version_id=version.id,
        origin_user_id=TEST_USER_ID,
        origin_kind="imported_by_me",
    )
    db.add(blueprint)
    await db.flush()

    installation = MarketplaceInstallation(
        id=uuid.uuid4(),
        user_id=TEST_USER_ID,
        item_id=item.id,
        version_id=version.id,
        resource_type="agent",
        installed_agent_blueprint_id=blueprint.id,
        install_status="active",
        is_dirty=False,
    )
    db.add(installation)
    await db.flush()

    assert installation.installed_agent_blueprint_id == blueprint.id
    assert installation.installed_agent_id is None
