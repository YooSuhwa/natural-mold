"""AG-UI restores a widget only while the exact MCP invocation remains readable."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy import delete, event, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState

from app.authz.fake import FakeAuthz
from app.authz.org_context import OrgContext
from app.authz.service import AuthzService
from app.credentials import service as credential_service
from app.credentials.creation import CredentialCreation
from app.mcp.apps_binding_store import AuthorizedBinding, BindingDraft, mint_invocation_binding
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.models.mcp_app_invocation import McpAppInvocationBinding
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.organization import Organization, OrganizationMember
from app.models.resource_credential_binding import ResourceCredentialBinding
from app.services import ag_ui_widget_access
from app.services.ag_ui_widget_access import readable_widget_bindings

if TYPE_CHECKING:
    from tests.authz.private_mcp_apps_fixture import McpAppCase
    from tests.authz.private_scope_fixture import PrivateScopes


pytest_plugins = ["tests.authz.private_scope_fixture", "tests.authz.private_mcp_apps_fixture"]


@dataclass(frozen=True)
class Widget:
    context: OrgContext
    conversation: Conversation
    run: ConversationRun
    original: McpAppInvocationBinding
    minted: UUID

    async def read(
        self, db: AsyncSession, context: OrgContext | None = None
    ) -> frozenset[tuple[str, str]]:
        with read_only(db):
            return await readable_widget_bindings(
                db, conversation=self.conversation, context=context or self.context, run=self.run
            )


@pytest.fixture
async def widget(db: AsyncSession, mcp_app_case: McpAppCase) -> Widget:
    ctx = mcp_app_case.context
    conversation = await db.get(Conversation, mcp_app_case.conversation_id)
    original = await db.scalar(select(McpAppInvocationBinding))
    assert original is not None and original.agent_id is not None and conversation is not None
    run = ConversationRun(
        conversation_id=conversation.id,
        agent_id=original.agent_id,
        user_id=ctx.user_id,
        org_id=ctx.org_id,
        tenant_id=ctx.tenant_id,
        source="chat",
        run_source="CHAT",
        status="completed",
        is_active=False,
    )
    db.add(run)
    await db.flush()
    minted = await mint_invocation_binding(
        db,
        BindingDraft(
            run_id=run.id,
            conversation_id=conversation.id,
            user_id=ctx.user_id,
            org_context=ctx,
            agent_id=original.agent_id,
            credential_subject_user_id=ctx.user_id,
            mcp_server_id=original.mcp_server_id,
            mcp_tool_id=original.mcp_tool_id,
            tool_call_id=original.tool_call_id,
            remote_tool_name=original.remote_tool_name,
            resource_uri=original.resource_uri,
            tool_meta=original.tool_meta,
            result_meta=None,
            structured_content={"private": "outside the replay response"},
        ),
    )
    await db.commit()
    return Widget(ctx, conversation, run, original, minted)


@contextmanager
def read_only(db: AsyncSession) -> Iterator[None]:
    def unlocked_select(state: ORMExecuteState) -> None:
        assert state.is_select and getattr(state.statement, "_for_update_arg", None) is None

    def forbid_write(*args: object) -> None:
        pytest.fail("A widget lookup must preserve its caller's transaction")

    hooks = [
        ("do_orm_execute", unlocked_select),
        ("before_flush", forbid_write),
        ("before_commit", forbid_write),
        ("after_rollback", forbid_write),
    ]
    for name, callback in hooks:
        event.listen(db.sync_session, name, callback)
    try:
        yield
    finally:
        for name, callback in hooks:
            event.remove(db.sync_session, name, callback)


async def test_replay_scopes_a_minted_widget_to_its_run_and_current_organization(
    db: AsyncSession, widget: Widget, private_scopes: PrivateScopes, monkeypatch: pytest.MonkeyPatch
) -> None:
    other_org = Organization(
        name="Other project", slug="widget-other", tenant_id=widget.context.tenant_id
    )
    db.add(other_org)
    await db.flush()
    db.add(
        OrganizationMember(
            org_id=other_org.id, tenant_id=widget.context.tenant_id, user_id=widget.context.user_id
        )
    )
    await db.commit()
    same_tenant = replace(widget.context, org_id=other_org.id)
    assert widget.minted != widget.original.id
    assert await widget.read(db) == {(widget.original.tool_call_id, str(widget.minted))}
    for other in (same_tenant, private_scopes.second):
        assert not await widget.read(db, other)

    await db.execute(
        delete(McpAppInvocationBinding).where(McpAppInvocationBinding.id == widget.minted)
    )
    await db.commit()

    def forbid_remote_check(*args: object, **kwargs: object) -> None:
        pytest.fail("A run without widgets must not require any remote authorization")

    monkeypatch.setattr("app.authz.runtime.runtime_authz", forbid_remote_check)
    assert not await widget.read(db)


@pytest.mark.parametrize(
    "revocation", ["permission", "disabled_tool", "archived_agent", "changed_resource"]
)
async def test_widget_disappears_after_live_access_changes(
    db: AsyncSession, widget: Widget, revocation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = await db.get(McpTool, widget.original.mcp_tool_id)
    assert tool is not None
    grant = (f"user:{widget.context.user_id}", "can_invoke", f"mcp_tool:{tool.id}")

    def allow(granted: bool) -> None:
        service = AuthzService(
            FakeAuthz(allowed=frozenset([grant]) if granted else frozenset()),
            mode="enforce",
            enforce_types=frozenset({"mcp_tool"}),
        )
        monkeypatch.setattr("app.authz.runtime._current", service)

    allow(True)
    assert await widget.read(db) == {(widget.original.tool_call_id, str(widget.minted))}
    if revocation == "permission":
        allow(False)
    elif revocation == "disabled_tool":
        tool.enabled = False
    elif revocation == "archived_agent":
        agent = await db.get(Agent, widget.run.agent_id)
        assert agent is not None
        agent.archived_at = utc_now_naive()
        agent.archived_by = widget.context.user_id
    elif revocation == "changed_resource":
        tool.metadata_json = {"ui": {"resourceUri": "ui://fixture/replaced"}}
    await db.commit()
    assert not await widget.read(db)


async def test_widget_requires_current_connection_without_opening_credentials_or_mcp(
    db: AsyncSession, widget: Widget, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = widget.context
    server = await db.get(McpServer, widget.original.mcp_server_id)
    assert server is not None
    credential = await credential_service.create(
        db,
        CredentialCreation(definition_key="http_bearer", name="Widget", data={"token": "fixture"}),
        ctx,
    )
    server.credential_mode, server.credential_definition_key = "per_user", "http_bearer"
    connection = ResourceCredentialBinding(
        org_id=ctx.org_id,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        resource_type="mcp_server",
        resource_id=server.id,
        credential_id=credential.id,
    )
    db.add(connection)
    await db.commit()

    def forbid(*args: object, **kwargs: object) -> None:
        pytest.fail("Displaying a widget pointer must not open a credential or contact MCP")

    for path in (
        "app.credentials.tenant_encryption.open_payload",
        "app.mcp.auth.open_values",
        "app.mcp.auth.refresh_oauth_token",
        "app.services.mcp_apps_service.resolve_mcp_auth",
        "app.services.mcp_apps_service.read_mcp_resource_once",
        "app.services.mcp_apps_service.call_mcp_tool_once",
    ):
        monkeypatch.setattr(path, forbid)

    assert await widget.read(db) == {(widget.original.tool_call_id, str(widget.minted))}
    await db.execute(
        delete(ResourceCredentialBinding).where(ResourceCredentialBinding.id == connection.id)
    )
    await db.commit()
    assert not await widget.read(db)


@pytest.mark.parametrize("changed", ["connection_mode", "delegation"])
async def test_committed_authority_change_during_widget_check_suppresses_the_old_result(
    db: AsyncSession, widget: Widget, changed: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    check = ag_ui_widget_access.binding_connection_visible
    checked = False

    async def change_after_check(reader: AsyncSession, binding: AuthorizedBinding) -> bool:
        nonlocal checked
        assert await check(reader, binding)
        assert binding.binding.id == widget.minted
        async with AsyncSession(db.bind) as writer, writer.begin():
            if changed == "connection_mode":
                await writer.execute(
                    update(McpServer)
                    .where(McpServer.id == binding.server.id)
                    .values(credential_mode="per_user", credential_definition_key="http_bearer")
                )
            else:
                await writer.execute(
                    update(AgentMcpToolLink)
                    .where(
                        AgentMcpToolLink.agent_id == widget.run.agent_id,
                        AgentMcpToolLink.mcp_tool_id == binding.origin_tool.id,
                    )
                    .values(link_status="broken")
                )
        checked = True
        return True

    monkeypatch.setattr(ag_ui_widget_access, "binding_connection_visible", change_after_check)
    assert not await widget.read(db)
    assert checked
