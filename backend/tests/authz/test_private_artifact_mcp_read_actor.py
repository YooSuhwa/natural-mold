"""Captured private readers refresh authority before metadata or SQL effects."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.exceptions import AppError
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.organization import OrganizationMember
from app.models.user import User
from app.services import mcp_service
from app.services.artifacts import library
from app.services.chat.conversations import create_conversation

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase
pytest_plugins = ["tests.authz.test_private_agent_api_scope"]
Action = Literal[
    "artifact_list",
    "artifact_message",
    "artifact_library",
    "artifact_recent",
    "artifact_stats",
    "mcp_all",
    "mcp_server",
]
Change = Literal["active", "membership", "account"]


@pytest.mark.parametrize("change", ["active", "membership", "account"])
@pytest.mark.parametrize(
    "action",
    [
        "artifact_list",
        "artifact_message",
        "artifact_library",
        "artifact_recent",
        "artifact_stats",
        "mcp_all",
        "mcp_server",
    ],
)
async def test_read_denies_revoked_actor(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    api_case: ApiCase,
    action: Action,
    change: Change,
) -> None:
    own = private_scopes.first
    conversation = await create_conversation(db, api_case.first.agent_id, context=own)
    artifact = ConversationArtifact(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        agent_id=api_case.first.agent_id,
        conversation_id=conversation.id,
        assistant_msg_id="f4-turn",
        linked_message_ids=["f4-message"],
        logical_path="private-review.txt",
        display_name="PRIVATE_F4_MARKER.txt",
        mime_type="text/plain",
        size_bytes=7,
        sha256="a" * 64,
    )
    server = McpServer(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="PRIVATE_F4_MCP",
        transport="stdio",
        command="never-executed",
    )
    db.add_all([artifact, server])
    await db.flush()
    version = ArtifactVersion(
        artifact_id=artifact.id,
        version_number=1,
        object_key="never-read",
        original_filename="private-review.txt",
        size_bytes=7,
        sha256="a" * 64,
    )
    tool = McpTool(
        server_id=server.id, name="PRIVATE_F4_TOOL", description="private metadata", input_schema={}
    )
    db.add_all([version, tool])
    await db.flush()
    artifact.current_version_id = version.id
    await db.commit()
    match change:
        case "active":
            pass
        case "membership":
            await db.execute(
                update(OrganizationMember)
                .where(
                    OrganizationMember.org_id == own.org_id,
                    OrganizationMember.user_id == own.user_id,
                )
                .values(status="removed")
                .execution_options(synchronize_session=False)
            )
            await db.commit()
        case "account":
            await db.execute(
                update(User)
                .where(User.id == own.user_id)
                .values(is_active=False)
                .execution_options(synchronize_session=False)
            )
            await db.commit()
        case unreachable:
            assert_never(unreachable)
    disclosed = False
    try:
        match action:
            case "artifact_list":
                rows = await library.list_conversation_artifacts(
                    db, context=own, conversation_id=conversation.id
                )
                disclosed = any(row.display_name == artifact.display_name for row in rows)
            case "artifact_message":
                grouped = await library.list_conversation_artifacts_by_message_id(
                    db, context=own, conversation_id=conversation.id
                )
                disclosed = bool(grouped.get("f4-message"))
            case "artifact_library":
                page = await library.list_library_artifacts(
                    db,
                    context=own,
                    q=None,
                    agent_id=None,
                    conversation_id=None,
                    kind=None,
                    favorite=None,
                    limit=50,
                    cursor=None,
                )
                disclosed = any(row.display_name == artifact.display_name for row in page.items)
            case "artifact_recent":
                recent = await library.list_recent_artifacts(db, context=own, limit=20)
                disclosed = any(row.display_name == artifact.display_name for row in recent)
            case "artifact_stats":
                stats = await library.get_library_stats(db, context=own)
                disclosed = stats.total_count == 1 and stats.total_size_bytes == 7
            case "mcp_all":
                tools = await mcp_service.list_all_tools(db, own)
                disclosed = any((row.name == tool.name for row, _ in tools))
            case "mcp_server":
                server_tools = await mcp_service.load_tools_for(db, server.id, own)
                disclosed = any(row.name == tool.name for row in server_tools)
            case unreachable:
                assert_never(unreachable)
    except AppError as error:
        assert error.status in {403, 404}
    assert disclosed == (change == "active")


@pytest.mark.parametrize("revoked", [False, True])
async def test_fresh_http_control(
    db: AsyncSession, private_scopes: PrivateScopes, api_case: ApiCase, revoked: bool
) -> None:
    own = private_scopes.first
    api_case.client.cookies.set(ACTIVE_ORG_COOKIE, str(own.org_id))
    if revoked:
        await db.execute(
            update(OrganizationMember)
            .where(
                OrganizationMember.org_id == own.org_id, OrganizationMember.user_id == own.user_id
            )
            .values(status="removed")
        )
        await db.commit()
    for path in ("/api/artifacts/stats", "/api/mcp-servers/all-tools"):
        response = await api_case.client.get(path)
        assert response.status_code == (403 if revoked else 200)


RunAction = Literal[
    "active_run",
    "active_runs",
    "current_run",
    "latest_run",
    "specific_run",
    "interrupted_run",
    "tool_list",
]
