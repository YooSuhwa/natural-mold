"""Journal widget pointers must recheck credential metadata before exposing a connection."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState

from app.authz.company_catalog import CREDENTIAL_ORGS_KEY, with_designation
from app.authz.run_decisions import fresh_run_decision_scope
from app.authz.service import AuthzService
from app.credentials import service as credential_service
from app.credentials.creation import CredentialCreation
from app.mcp.apps_binding_connection import binding_connection_visible
from app.mcp.apps_binding_store import AuthorizedBinding
from app.models.conversation_run import ConversationRun
from app.models.credential import Credential
from app.models.mcp_app_invocation import McpAppInvocationBinding
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.organization import Tenant
from app.models.user import User
from app.services import mcp_apps_service
from tests.authz.e07_support import credential as company_credential
from tests.authz.f03_support import GraphFake

if TYPE_CHECKING:
    from tests.authz.private_mcp_apps_fixture import McpAppCase


pytest_plugins = ["tests.authz.private_scope_fixture", "tests.authz.private_mcp_apps_fixture"]


@pytest.fixture
async def authorized(db: AsyncSession, mcp_app_case: McpAppCase) -> AuthorizedBinding:
    run = await db.get(ConversationRun, mcp_app_case.run_id)
    binding = await db.scalar(
        select(McpAppInvocationBinding).where(McpAppInvocationBinding.run_id == mcp_app_case.run_id)
    )
    assert run is not None and binding is not None
    server = await db.get(McpServer, binding.mcp_server_id)
    tool = await db.get(McpTool, binding.mcp_tool_id)
    assert server is not None and tool is not None
    return AuthorizedBinding(binding, run, server, tool, mcp_app_case.context)


async def pointer_visible(db: AsyncSession, target: AuthorizedBinding) -> bool:
    def assert_metadata_only(state: ORMExecuteState) -> None:
        assert state.is_select
        assert getattr(state.statement, "_for_update_arg", None) is None
        assert "data_encrypted" not in str(state.statement).lower()

    event.listen(db.sync_session, "do_orm_execute", assert_metadata_only)
    try:
        return await binding_connection_visible(db, target)
    finally:
        event.remove(db.sync_session, "do_orm_execute", assert_metadata_only)


@pytest.mark.parametrize("change", ["disabled", "different_owner"])
async def test_shared_personal_key_must_remain_usable_by_its_subject(
    db: AsyncSession, authorized: AuthorizedBinding, change: str
) -> None:
    secret = await credential_service.create(
        db,
        CredentialCreation(definition_key="http_bearer", name="Private", data={"token": "test"}),
        authorized.org_context,
    )
    identity = secret.id
    authorized.server.credential_id = identity
    await db.commit()
    db.expunge(secret)
    assert await pointer_visible(db, authorized)

    stored = await db.get(Credential, identity)
    assert stored is not None
    if change == "disabled":
        stored.status = "disabled"
    else:
        other = User(email="widget-other-owner@example.test", name="Other")
        db.add(other)
        await db.flush()
        stored.user_id = other.id
    await db.commit()
    db.expunge(stored)
    assert not await pointer_visible(db, authorized)


async def designate_company_key(db: AsyncSession, target: AuthorizedBinding) -> tuple[Tenant, UUID]:
    ctx = target.org_context
    company = await company_credential(db, tenant_id=ctx.tenant_id)
    tenant = await db.get(Tenant, ctx.tenant_id)
    assert tenant is not None
    tenant.settings = with_designation(
        tenant.settings, CREDENTIAL_ORGS_KEY, company.id, [ctx.org_id]
    )
    target.server.credential_id = company.id
    identity = company.id
    await db.commit()
    db.expunge(company)
    return tenant, identity


async def test_company_key_must_still_be_designated_to_this_organization(
    db: AsyncSession, authorized: AuthorizedBinding
) -> None:
    tenant, identity = await designate_company_key(db, authorized)
    assert await pointer_visible(db, authorized)
    tenant.settings = with_designation(tenant.settings, CREDENTIAL_ORGS_KEY, identity, [])
    await db.commit()
    assert not await pointer_visible(db, authorized)


async def test_new_decision_observes_company_key_revocation_inside_long_lived_read(
    db: AsyncSession, authorized: AuthorizedBinding, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, identity = await designate_company_key(db, authorized)
    grant = (f"user:{authorized.org_context.user_id}", "can_use", f"credential:{identity}")
    fake = GraphFake(allowed={grant})
    service = AuthzService(fake, mode="enforce", enforce_types=frozenset({"credential"}))
    monkeypatch.setattr("app.authz.runtime._current", service)
    with fresh_run_decision_scope():
        assert await pointer_visible(db, authorized)
        fake.allowed.clear()
        assert not await pointer_visible(db, authorized)
    assert len(fake.consistencies) >= 2
    assert set(fake.consistencies) == {"HIGHER_CONSISTENCY"}


async def test_explicit_widget_read_can_open_credential_after_pointer_only_check(
    db: AsyncSession, authorized: AuthorizedBinding, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "fixture-widget-command-token"
    secret = await credential_service.create(
        db,
        CredentialCreation(definition_key="http_bearer", name="Command", data={"token": token}),
        authorized.org_context,
    )
    authorized.server.credential_id = secret.id
    await db.commit()
    db.expunge(secret)
    assert await pointer_visible(db, authorized)

    wire = AsyncMock(
        return_value={
            "success": True,
            "contents": [
                {
                    "uri": authorized.binding.resource_uri,
                    "mimeType": mcp_apps_service.MCP_APP_MIME_TYPE,
                    "text": "<p>Fixture widget</p>",
                }
            ],
        }
    )
    monkeypatch.setattr(mcp_apps_service, "read_mcp_resource_once", wire)
    result = await mcp_apps_service.read_bound_resource(db, authorized)
    assert result["html"] == "<p>Fixture widget</p>"
    wire.assert_awaited_once()
    assert wire.await_args is not None
    assert wire.await_args.kwargs["headers"]["Authorization"] == f"Bearer {token}"
