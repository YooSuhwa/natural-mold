# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest"]
# ///
# Run through official isolation with dedicated PYTHONPATH and explicit pytest config/plugin.
"""Cross-domain sibling durable writers must revalidate a genuine captured actor."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_api import service as api_service
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.agent_api import AgentApiKey, AgentDeployment
from app.models.mcp_server import McpServer
from app.models.organization import OrganizationMember
from app.models.share_link import ShareLink
from app.schemas.agent import AgentCreate
from app.schemas.agent_api import AgentApiKeyCreate, AgentDeploymentCreate, AgentDeploymentUpdate
from app.schemas.mcp import McpServerCreate, McpServerUpdate
from app.services import agent_service, mcp_service, share_service

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_secret_collector import CollectorCase

pytest_plugins = ["tests.authz.test_private_secret_collector"]
Operation = Literal[
    "create_mcp",
    "update_mcp",
    "delete_mcp",
    "create_agent",
    "create_share",
    "revoke_share",
    "create_deployment",
    "update_deployment",
    "create_key",
    "revoke_key",
]


async def snapshot(db: AsyncSession) -> tuple[tuple[str, ...], ...]:
    queries = (
        select(Agent.id, Agent.name),
        select(McpServer.id, McpServer.name),
        select(ShareLink.id, ShareLink.revoked_at),
        select(AgentDeployment.id, AgentDeployment.allow_streaming),
        select(AgentApiKey.id, AgentApiKey.revoked_at),
    )
    snapshots: list[tuple[str, ...]] = []
    for query in queries:
        rows = (await db.execute(query)).all()
        snapshots.append(tuple(sorted(str(row) for row in rows)))
    return tuple(snapshots)


@pytest.mark.parametrize(
    "operation",
    [
        "create_mcp",
        "update_mcp",
        "delete_mcp",
        "create_agent",
        "create_share",
        "revoke_share",
        "create_deployment",
        "update_deployment",
        "create_key",
        "revoke_key",
    ],
)
async def test_current_private_writer_rejects_revoked_scope(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    collector_case: CollectorCase,
    operation: Operation,
) -> None:
    own = private_scopes.first
    agent = await db.get(Agent, collector_case.conversation.agent_id)
    assert agent is not None
    server = await mcp_service.create_server(
        db,
        context=own,
        data=McpServerCreate(
            name="Owned server", transport="streamable_http", url="https://example.invalid/mcp"
        ),
    )
    key, _ = await api_service.create_api_key(
        db, own, AgentApiKeyCreate(name="Owned key", scopes=["read"], allow_all_deployments=True)
    )
    deployment = None
    if operation == "update_deployment":
        deployment = await api_service.create_deployment(
            db, own, AgentDeploymentCreate(agent_id=agent.id)
        )
    if operation == "revoke_share":
        await share_service.create_or_get_active_share(db, collector_case.conversation, own)
    await db.commit()
    with pytest.raises(AppError) as missing:
        await mcp_service.load_owned(db, server.id, private_scopes.second)
    assert missing.value.status == 404
    await db.execute(
        update(OrganizationMember)
        .where(OrganizationMember.org_id == own.org_id, OrganizationMember.user_id == own.user_id)
        .values(status="suspended")
    )
    await db.commit()
    before = await snapshot(db)
    denied = False
    try:
        match operation:
            case "create_mcp":
                await mcp_service.create_server(
                    db,
                    context=own,
                    data=McpServerCreate(
                        name="Revoked server",
                        transport="streamable_http",
                        url="https://example.invalid/mcp",
                    ),
                )
            case "update_mcp":
                await mcp_service.update_server(
                    db, server=server, context=own, data=McpServerUpdate(name="Revoked overwrite")
                )
            case "delete_mcp":
                await mcp_service.delete_server(db, server=server, context=own)
            case "create_agent":
                await agent_service.create_agent(
                    db,
                    AgentCreate(
                        name="Revoked agent", system_prompt="Fixture", model_id=agent.model_id
                    ),
                    own,
                )
            case "create_share":
                await share_service.create_or_get_active_share(db, collector_case.conversation, own)
            case "revoke_share":
                await share_service.revoke_share(db, collector_case.conversation.id, own)
            case "create_deployment":
                await api_service.create_deployment(
                    db, own, AgentDeploymentCreate(agent_id=agent.id)
                )
            case "update_deployment":
                assert deployment is not None
                await api_service.update_deployment(
                    db, own, deployment.id, AgentDeploymentUpdate(allow_streaming=False)
                )
            case "create_key":
                await api_service.create_api_key(
                    db,
                    own,
                    AgentApiKeyCreate(
                        name="Revoked key", scopes=["read"], allow_all_deployments=True
                    ),
                )
            case "revoke_key":
                await api_service.revoke_api_key(db, own, key.id)
            case unreachable:
                assert_never(unreachable)
        await db.commit()
    except AppError:
        denied = True
    unchanged = await snapshot(db) == before
    assert denied and unchanged
