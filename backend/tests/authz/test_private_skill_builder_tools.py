"""Skill Builder tools authorize the bound session before workspace effects."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal, assert_never
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.skill_builder import eval_runner, tools
from app.authz.org_context import OrgContext
from app.exceptions import AppError
from app.models.organization import OrganizationMember
from app.models.skill_builder_session import SkillBuilderSession
from app.services import skill_builder_finalize, skill_builder_service, skill_draft_workspace
from tests.conftest import TestSession

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]
ToolName = Literal["validate_skill", "generate_evals", "test_skill_draft", "finalize_skill"]


@dataclass(frozen=True, slots=True)
class DraftToolCase:
    session: SkillBuilderSession
    context: OrgContext
    foreign_context: OrgContext
    workspace: Path
    bound_path: str


@pytest.fixture
async def draft_tool_case(
    db: AsyncSession, private_scopes: PrivateScopes, tmp_path: Path
) -> DraftToolCase:
    row = await skill_builder_service.create_session(
        db, context=private_scopes.first, user_request="Fixture"
    )
    path = f"skill-drafts/{row.id}"
    row.draft_workspace_path = path
    await db.commit()
    return DraftToolCase(row, private_scopes.first, private_scopes.second, tmp_path, path)


@pytest.fixture(params=["foreign", "revoked", "workspace", "stored_workspace"])
async def denied_draft_tool_case(
    db: AsyncSession, draft_tool_case: DraftToolCase, request: pytest.FixtureRequest
) -> DraftToolCase:
    match request.param:
        case "foreign":
            return replace(draft_tool_case, context=draft_tool_case.foreign_context)
        case "workspace":
            return replace(draft_tool_case, bound_path="skill-drafts/another-session")
        case "stored_workspace":
            path = "skill-drafts/another-session"
            draft_tool_case.session.draft_workspace_path = path
            await db.commit()
            return replace(draft_tool_case, bound_path=path)
        case "revoked":
            await db.execute(
                delete(OrganizationMember).where(
                    OrganizationMember.org_id == draft_tool_case.context.org_id,
                    OrganizationMember.user_id == draft_tool_case.context.user_id,
                )
            )
            await db.commit()
            return draft_tool_case
        case _:
            pytest.fail("Unknown draft tool fixture")


@pytest.mark.parametrize(
    "tool_name", ["validate_skill", "generate_evals", "test_skill_draft", "finalize_skill"]
)
async def test_denied_tool_cannot_observe_or_mutate_workspace(
    db: AsyncSession, denied_draft_tool_case: DraftToolCase, tool_name: ToolName
) -> None:
    case = denied_draft_tool_case
    with pytest.MonkeyPatch.context() as patches:
        read_files = Mock(return_value=[])
        sandbox = AsyncMock(return_value="Fixture")
        finalize = AsyncMock(return_value={})
        patches.setattr(skill_draft_workspace, "load_draft_files", read_files)
        patches.setattr(skill_draft_workspace, "resolve_workspace_dir", lambda _: case.workspace)
        patches.setattr(eval_runner, "run_eval_skill_command", sandbox)
        patches.setattr(skill_builder_finalize, "finalize_draft_session", finalize)
        available = tools.build_skill_builder_tools(
            session_id=str(case.session.id),
            workspace_path=case.bound_path,
            session_factory=TestSession,
            context=case.context,
            include_runtime_tools=True,
        )
        tool = next(item for item in available if item.name == tool_name)
        match tool_name:
            case "validate_skill" | "finalize_skill":
                arguments = {}
            case "generate_evals":
                arguments = {"intent": "Explicit intent must still authorize"}
            case "test_skill_draft":
                arguments = {"command": "python fixture.py"}
            case unreachable:
                assert_never(unreachable)
        with pytest.raises(AppError):
            await tool.ainvoke(arguments)
        read_files.assert_not_called()
        sandbox.assert_not_awaited()
        finalize.assert_not_awaited()
    await db.refresh(case.session)
    assert case.session.validation_result is None
    assert not (case.workspace / "evals/evals.json").exists()
