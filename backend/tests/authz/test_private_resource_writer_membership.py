"""Real captured membership must precede shared-root private writer effects."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from anyio import Path
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import AppError
from app.models.organization import OrganizationMember
from app.models.skill import Skill
from app.models.tool import Tool
from app.schemas.tool import ToolCreate, ToolPatch
from app.services import tool_service
from app.skills import service as skill_service
from app.skills.creation import TextSkillCreation

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


@pytest.mark.parametrize("operation", ["create_skill", "create_tool", "update_tool", "delete_tool"])
async def test_private_writers_deny_revoked_captured_membership(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    operation: Literal["create_skill", "create_tool", "update_tool", "delete_tool"],
) -> None:
    own = private_scopes.first
    tool = await tool_service.create_tool(
        db, context=own, data=ToolCreate(definition_key="http_request", name="Owned control")
    )
    await db.commit()
    with pytest.raises(AppError) as foreign:
        await tool_service.update_tool(
            db, tool=tool, context=private_scopes.second, data=ToolPatch(name="Foreign overwrite")
        )
    assert foreign.value.status == 404
    await db.execute(
        update(OrganizationMember)
        .where(OrganizationMember.org_id == own.org_id, OrganizationMember.user_id == own.user_id)
        .values(status="suspended")
    )
    await db.commit()
    skill_count = await db.scalar(select(func.count()).select_from(Skill))
    tool_count = await db.scalar(select(func.count()).select_from(Tool))
    files_before = tuple(
        sorted([path async for path in Path(settings.data_root).rglob("SKILL.md")])
    )
    denied = False
    try:
        match operation:
            case "create_skill":
                await skill_service.create_text_skill(
                    db,
                    TextSkillCreation(
                        "Revoked fixture",
                        "revoked-fixture",
                        "Fixture",
                        "---\nname: revoked-fixture\ndescription: Fixture\n---\n# Fixture\n",
                    ),
                    own,
                )
            case "create_tool":
                await tool_service.create_tool(
                    db,
                    context=own,
                    data=ToolCreate(definition_key="http_request", name="Revoked fixture"),
                )
            case "update_tool":
                await tool_service.update_tool(
                    db, tool=tool, context=own, data=ToolPatch(name="Revoked overwrite")
                )
            case "delete_tool":
                await tool_service.delete_tool(db, tool=tool, context=own)
            case unreachable:
                assert_never(unreachable)
        await db.commit()
    except AppError:
        denied = True
    persisted_name = await db.scalar(select(Tool.name).where(Tool.id == tool.id))
    rows_unchanged = (
        await db.scalar(select(func.count()).select_from(Skill)) == skill_count
        and await db.scalar(select(func.count()).select_from(Tool)) == tool_count
        and persisted_name == "Owned control"
    )
    files_unchanged = (
        tuple(sorted([path async for path in Path(settings.data_root).rglob("SKILL.md")]))
        == files_before
    )
    assert denied and rows_unchanged and files_unchanged
