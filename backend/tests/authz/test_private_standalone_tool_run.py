"""Standalone execution checks a persisted credential edge before effects."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import resolve_org_context
from app.credentials import service
from app.credentials.creation import CredentialCreation
from app.schemas.tool import ToolCreate
from app.services import tool_service
from app.tools.registry import registry

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


async def test_standalone_tool_denies_foreign_credential_before_runner(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    org = org_fixture
    own = await resolve_org_context(db, org.user, cookie=str(org.first))
    foreign = await resolve_org_context(db, org.user, cookie=str(org.second))
    credential = await service.create(
        db, CredentialCreation("http_bearer", "Foreign dummy", {"token": "B06_DUMMY_ONLY"}), foreign
    )
    tool = await tool_service.create_tool(
        db,
        context=own,
        data=ToolCreate(
            definition_key="http_request",
            name="Owned malformed tool",
            parameters={"url": "https://example.invalid/bounded"},
        ),
    )
    tool.credential_id = credential.id
    await db.commit()
    with pytest.MonkeyPatch.context() as patches:
        decrypt = AsyncMock(wraps=service.decrypt_with_external)
        runner = AsyncMock(return_value={"bounded": True})
        patches.setattr(service, "decrypt_with_external", decrypt)
        patches.setattr(registry.require("http_request"), "runner", runner)
        response = await raw_client.post(
            f"/api/tools/{tool.id}/run", json={"runtime_args": {}}, headers=org.session.headers()
        )
        assert response.status_code == 404
        decrypt.assert_not_awaited()
        runner.assert_not_awaited()
    assert tool.last_used_at is None
