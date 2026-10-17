"""Seeds for F-05 run-time credential resolution: agent owner OTHER, caller TEST."""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.identity import (
    AgentRunSource,
    make_agent_runtime_name,
    resolve_agent_run_identity,
)
from app.authz.fake import FakeAuthz
from app.authz.service import AuthzService
from app.credentials import tenant_encryption
from app.models.agent import Agent
from app.models.credential import Credential
from app.models.mcp_tool import AgentMcpToolLink
from app.models.resource_credential_binding import ResourceCredentialBinding
from app.models.tool import AgentToolLink, Tool
from app.services import chat_service
from tests.authz.d04_support import OTHER_USER_ID, add_server, add_tool
from tests.authz.e01_support import add_agent, add_credential
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID

OWNER, CALLER = OTHER_USER_ID, TEST_USER_ID


def enforce(*tuples: tuple[str, str, str]) -> AuthzService:
    return AuthzService(FakeAuthz(frozenset(tuples)), mode="enforce")


async def secret(
    db: AsyncSession, owner: UUID, value: str, definition_key: str = "http_bearer"
) -> Credential:
    credential = await add_credential(db, owner=owner, definition_key=definition_key)
    await tenant_encryption.seal(credential, {"token": value, "api_key": value}, db=db)
    await db.commit()
    return credential


async def shared_agent(db: AsyncSession, *, mode: str = "per_user") -> Agent:
    agent = await add_agent(db, owner=OWNER)
    agent.identity_mode = mode
    await db.commit()
    return agent


async def per_user_tool(db: AsyncSession, agent: Agent, *, owner: UUID = OWNER) -> Tool:
    tool = await add_tool(db, owner=owner)
    tool.credential_mode, tool.credential_definition_key = "per_user", "http_bearer"
    db.add(AgentToolLink(agent_id=agent.id, tool_id=tool.id))
    await db.commit()
    return tool


async def per_user_server(db: AsyncSession, agent: Agent) -> Any:
    server, mcp_tool = await add_server(db, owner=OWNER)
    server.credential_mode, server.credential_definition_key = "per_user", "http_bearer"
    db.add(AgentMcpToolLink(agent_id=agent.id, mcp_tool_id=mcp_tool.id))
    await db.commit()
    return server


async def bind(
    db: AsyncSession, user: UUID, kind: str, resource_id: UUID, credential: Credential
) -> None:
    db.add(
        ResourceCredentialBinding(
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            user_id=user,
            resource_type=kind,
            resource_id=resource_id,
            credential_id=credential.id,
        )
    )
    await db.commit()


def identity(agent: Agent, caller: UUID = CALLER) -> Any:
    return resolve_agent_run_identity(
        agent_id=agent.id,
        agent_owner_user_id=agent.user_id,
        runtime_name=agent.runtime_name or make_agent_runtime_name(agent.id),
        identity_mode=agent.identity_mode,
        source=AgentRunSource.CHAT,
        caller_user_id=caller,
    )


async def loaded(db: AsyncSession, agent: Agent) -> Agent:
    """The agent tree as F-06 will hand it to the run (owner-scoped load for now)."""
    fetched = await chat_service.get_agent_with_tools(db, agent.id, unit_context(agent.user_id))
    assert fetched is not None
    return fetched


async def run_config(
    db: AsyncSession,
    agent: Agent,
    authz: AuthzService,
    *,
    caller: UUID = CALLER,
    read_only: bool = False,
) -> list[dict[str, Any]]:
    return await chat_service.build_tools_config(
        await loaded(db, agent),
        context=unit_context(caller),
        db=db,
        identity=identity(agent, caller),
        read_only=read_only,
        authz=authz,
    )
