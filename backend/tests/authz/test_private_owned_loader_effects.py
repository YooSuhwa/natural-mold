"""Independent frozen-source pre-denial SQL witnesses; isolated SQLite only."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never
from unittest.mock import Mock

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials.mutation_scope import load_scoped_credential
from app.exceptions import AppError
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.skill import Skill
from app.models.tool import Tool
from app.services import mcp_service, tool_service
from app.skills import service as skill_service

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
pytest_plugins = ["tests.authz.private_scope_fixture"]


@pytest.mark.parametrize("surface", ["tool", "mcp", "credential", "skill"])
async def test_foreign_target_denial_does_not_flush_staged_content(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    surface: Literal["tool", "mcp", "credential", "skill"],
) -> None:
    own, foreign = (private_scopes.first, private_scopes.second)
    scope = {"user_id": foreign.user_id, "org_id": foreign.org_id, "tenant_id": foreign.tenant_id}
    tool = Tool(**scope, name="Foreign original", definition_key="http_request")
    mcp = McpServer(
        **scope, name="Foreign original", transport="streamable_http", url="https://invalid.local"
    )
    credential = Credential(
        **scope,
        name="Foreign original",
        definition_key="openai_api",
        data_encrypted="dummy",
        key_id="dummy",
        field_keys=[],
    )
    skill = Skill(**scope, name="Foreign original", slug="f2-foreign-original", kind="text")
    db.add_all([tool, mcp, credential, skill])
    await db.commit()
    match surface:
        case "tool":
            row = tool
        case "mcp":
            row = mcp
        case "credential":
            row = credential
        case "skill":
            row = skill
        case unreachable:
            assert_never(unreachable)
    target_id = row.id
    row.name = "F2 unauthorized staged update"
    flush = Mock()
    event.listen(db.sync_session, "before_flush", flush)
    denied = False
    try:
        match surface:
            case "tool":
                await tool_service.load_owned(db, target_id, own)
            case "mcp":
                await mcp_service.load_owned(db, target_id, own)
            case "credential":
                await load_scoped_credential(db, target_id, own)
            case "skill":
                denied = await skill_service.get_skill(db, target_id, own) is None
            case unreachable:
                assert_never(unreachable)
    except AppError as error:
        denied = error.status == 404
    finally:
        event.remove(db.sync_session, "before_flush", flush)
    with db.no_autoflush:
        stored_name = await db.scalar(select(type(row).name).where(type(row).id == target_id))
    staged = row in db.dirty
    await db.rollback()
    assert denied
    assert flush.call_count == 0
    assert staged
    assert stored_name == "Foreign original"
