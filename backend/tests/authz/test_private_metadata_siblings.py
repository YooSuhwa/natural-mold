"""Bounded real service metadata sibling controls with captured membership revocation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_api import service as api_service
from app.credentials import service as credentials
from app.exceptions import AppError
from app.models.builder_session import BuilderSession
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.organization import OrganizationMember
from app.models.skill import Skill
from app.services import builder_service, mcp_service, tool_service
from app.skills import service as skills

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase

pytest_plugins = ["tests.authz.test_private_agent_api_scope"]
Action = Literal[
    "credential_list",
    "credential_get",
    "skill_list",
    "mcp_list",
    "builder_get",
    "deployment_candidates",
    "tools_catalog",
]


@pytest.mark.parametrize("revoked", [False, True])
@pytest.mark.parametrize(
    "action",
    [
        "credential_list",
        "credential_get",
        "skill_list",
        "mcp_list",
        "builder_get",
        "deployment_candidates",
        "tools_catalog",
    ],
)
async def test_metadata_siblings_refresh_captured_actor(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    api_case: ApiCase,
    action: Action,
    revoked: bool,
) -> None:
    own = private_scopes.first
    credential = Credential(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        scope="personal",
        name="Metadata fixture",
        definition_key="openai",
        data_encrypted="dummy-metadata-only",
        key_id="00000000",
    )
    skill = Skill(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="Metadata skill",
        slug="metadata-skill",
        kind="text",
    )
    server = McpServer(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="Metadata MCP",
        transport="stdio",
        command="dummy-never-run",
    )
    builder = BuilderSession(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        user_request="Metadata builder",
    )
    db.add_all([credential, skill, server, builder])
    await db.commit()
    if revoked:
        await db.execute(
            update(OrganizationMember)
            .where(
                OrganizationMember.user_id == own.user_id, OrganizationMember.org_id == own.org_id
            )
            .values(status="removed")
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    disclosed = False
    try:
        match action:
            case "credential_list":
                rows = await credentials.list_for_user(db, own)
                disclosed = any(row.id == credential.id for row in rows)
            case "credential_get":
                loaded = await credentials.get_for_user(db, credential.id, own)
                disclosed = loaded is not None
            case "skill_list":
                skill_rows = await skills.list_skills(db, own)
                disclosed = any(row.id == skill.id for row in skill_rows)
            case "mcp_list":
                server_rows = await mcp_service.list_servers(db, own)
                disclosed = any(row.id == server.id for row in server_rows)
            case "builder_get":
                loaded_builder = await builder_service.get_session(db, builder.id, own)
                disclosed = loaded_builder is not None
            case "deployment_candidates":
                candidates = await api_service.list_deployment_candidates(db, own)
                disclosed = any(row.agent_id == api_case.first.agent_id for row in candidates)
            case "tools_catalog":
                catalog = await tool_service.get_tools_catalog(db, own)
                disclosed = any(row["id"] == str(skill.id) for row in catalog)
            case unreachable:
                assert_never(unreachable)
    except AppError as error:
        denied_status = error.status
        if denied_status not in {403, 404}:
            raise
    assert disclosed != revoked
