"""Credential-only reuse must bind retained versions to the installed source."""

from __future__ import annotations

from typing import Literal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials.creation import CredentialCreation
from app.models.marketplace import MarketplaceItem, MarketplaceVersion
from tests.authz.unit_scope_fixture import create_test_credential, unit_context
from tests.conftest import TEST_USER_ID
from tests.test_marketplace_agent_blueprint import (
    _ensure_test_user,
    _make_agent_item_with_dependencies,
)


@pytest.mark.parametrize("case", [("agent", False), ("agent", True), ("mcp", False), ("mcp", True)])
async def test_reuse_retained_version_matches_current_source(
    client: AsyncClient, db: AsyncSession, case: tuple[Literal["agent", "mcp"], bool]
) -> None:
    kind, moved = case
    await _ensure_test_user(db)
    credential = await create_test_credential(
        db,
        CredentialCreation(
            definition_key="mcp_oauth2",
            name="Association fixture credential",
            data={"access_token": "dummy-token", "refresh_token": "dummy-refresh"},
        ),
        unit_context(TEST_USER_ID),
    )
    item, version = await _make_agent_item_with_dependencies(db)
    key = "mcp_docs_mcp" if kind == "agent" else "mcp_auth"
    if kind == "mcp":
        item.resource_type = "mcp"
        version.resource_type = "mcp"
        version.payload_kind = "mcp_template"
        version.payload = {
            "name": "Association fixture MCP",
            "transport": "streamable_http",
            "url": "https://example.invalid/mcp",
            "credential_definition_key": "mcp_oauth2",
            "headers": {},
            "tool_snapshot": [],
        }
    version.credential_requirements = [
        {
            "key": key,
            "definition_key": "mcp_oauth2",
            "required": True,
            "label": "Fixture credential",
            "fields": [],
            "injection": "config",
            "scope": "user",
        }
    ]
    await db.commit()
    initial = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={"credential_bindings": {key: str(credential.id)}},
    )
    assert initial.status_code == 201, initial.text
    # A newer source version makes the retained installation version a distinct read.
    requirements = version.credential_requirements
    assert requirements is not None
    latest = MarketplaceVersion(
        id=uuid4(),
        item_id=item.id,
        version_label="fixture-2",
        version_number=2,
        resource_type=kind,
        payload_kind=version.payload_kind,
        payload=dict(version.payload),
        credential_requirements=list(requirements),
        dependency_requirements=[],
        execution_profile={},
        content_hash="c" * 64,
        size_bytes=1,
        created_by=TEST_USER_ID,
    )
    db.add(latest)
    await db.flush()
    item.latest_version_id = latest.id
    if moved:
        target = MarketplaceItem(
            owner_user_id=item.owner_user_id,
            org_id=item.org_id,
            tenant_id=item.tenant_id,
            resource_type=kind,
            name="Other association fixture",
            slug=f"fixture-{uuid4().hex}",
            visibility="private",
            status="published",
            is_system=False,
        )
        db.add(target)
        await db.flush()
        await db.execute(
            update(MarketplaceVersion)
            .where(MarketplaceVersion.id == version.id)
            .values(item_id=target.id)
            .execution_options(synchronize_session=False)
        )
    await db.commit()
    detail = await client.get(f"/api/marketplace/items/{item.id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["installation"]["status"] == ("needs_setup" if moved else "active"), (
        detail.text
    )
    response = await client.post(
        f"/api/marketplace/items/{item.id}/install",
        json={"install_mode": "reuse_or_update", "credential_bindings": {key: str(credential.id)}},
    )
    assert response.status_code == (404 if moved else 201), response.text
    if not moved:
        assert UUID(response.json()["id"]) == UUID(initial.json()["id"])
        assert response.json()["install_status"] == "active"
