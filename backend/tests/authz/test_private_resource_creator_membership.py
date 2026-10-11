"""Fresh captured authority must precede private root and filesystem creation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, assert_never
from uuid import UUID

import pytest
from anyio import Path
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.config import settings
from app.credentials import service as credential_service
from app.credentials.creation import CredentialCreation
from app.exceptions import AppError
from app.models.model import Model
from app.models.organization import OrganizationMember
from app.schemas.agent import AgentCreate
from app.schemas.mcp import McpServerCreate
from app.schemas.tool import ToolCreate
from app.services import agent_service, mcp_service, tool_service
from app.skills import service as skill_service
from app.skills.creation import TextSkillCreation

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.test_private_skill_scope"]
type Writer = Literal["credential", "tool", "mcp", "agent", "skill"]


@dataclass(frozen=True, slots=True)
class CreateTarget:
    context: OrgContext
    writer: Writer
    model_id: UUID
    marker: str


async def create_root(db: AsyncSession, target: CreateTarget) -> UUID:
    match target.writer:
        case "credential":
            row = await credential_service.create(
                db,
                CredentialCreation("openai", target.marker, {"api_key": "DUMMY_ONLY"}),
                target.context,
            )
        case "tool":
            row = await tool_service.create_tool(
                db,
                context=target.context,
                data=ToolCreate(definition_key="http_request", name=target.marker),
            )
        case "mcp":
            row = await mcp_service.create_server(
                db,
                context=target.context,
                data=McpServerCreate(
                    name=target.marker, transport="sse", url="https://example.invalid"
                ),
            )
        case "agent":
            row = await agent_service.create_agent(
                db,
                AgentCreate(name=target.marker, system_prompt="Fixture", model_id=target.model_id),
                target.context,
            )
        case "skill":
            row = await skill_service.create_text_skill(
                db,
                TextSkillCreation(
                    target.marker,
                    target.marker,
                    "Fixture",
                    f'---\nname: {target.marker}\ndescription: "Fixture"\n---\nFixture body\n',
                ),
                target.context,
            )
        case unreachable:
            assert_never(unreachable)
    return row.id


@pytest.mark.parametrize("writer", ["credential", "tool", "mcp", "agent", "skill"])
async def test_private_creator_denies_revoked_captured_membership(
    db: AsyncSession, private_scopes: PrivateScopes, writer: Writer
) -> None:
    # Given a valid captured context and actual owned positive service-writer control.
    context = private_scopes.first
    model = Model(provider="openai", model_name="fixture", display_name="Fixture")
    db.add(model)
    await db.commit()
    model_id = model.id
    control_id = await create_root(db, CreateTarget(context, writer, model_id, "owned-control"))
    await db.commit()
    assert control_id is not None
    await db.execute(
        update(OrganizationMember)
        .where(
            OrganizationMember.org_id == context.org_id,
            OrganizationMember.user_id == context.user_id,
        )
        .values(status="suspended")
    )
    await db.commit()
    before_files = {path async for path in Path(settings.data_root).rglob("SKILL.md")}
    # When that same caller invokes the production creator after authority is revoked.
    denied = False
    created_id: UUID | None = None
    try:
        created_id = await create_root(db, CreateTarget(context, writer, model_id, "revoked-write"))
        await db.commit()
    except AppError:
        denied = True
    after_files = {path async for path in Path(settings.data_root).rglob("SKILL.md")}
    # Then neither a durable row nor a new private Skill file may have been produced.
    assert denied and created_id is None and after_files == before_files
