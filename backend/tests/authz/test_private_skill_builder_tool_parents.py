# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest"]
# ///
# Run via official wrapper, dedicated PYTHONPATH and -c backend/pyproject.toml -p tests.conftest.
"""Independent malformed SkillBuilder source/finalized-edge tool witness."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal
from unittest.mock import Mock

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.skill_builder import tools
from app.exceptions import AppError
from app.models.skill_builder_session import SkillBuilderSession
from app.services import skill_builder_service, skill_draft_workspace
from tests.conftest import TestSession

if TYPE_CHECKING:
    from tests.authz.test_private_skill_builder_tools import DraftToolCase
    from tests.authz.test_private_skill_scope import SkillCase

pytest_plugins = [
    "tests.authz.test_private_skill_builder_tools",
    "tests.authz.test_private_skill_scope",
]
Edge = Literal["source_skill_id", "finalized_skill_id"]


@pytest.mark.parametrize("edge", ["source_skill_id", "finalized_skill_id"])
@pytest.mark.parametrize("foreign", [False, True])
async def test_tool_revalidates_skill_parents_before_effects(
    db: AsyncSession,
    draft_tool_case: DraftToolCase,
    skill_case: SkillCase,
    edge: Edge,
    foreign: bool,
) -> None:
    case = draft_tool_case
    selected = skill_case.second if foreign else skill_case.first
    await db.execute(
        update(SkillBuilderSession)
        .where(SkillBuilderSession.id == case.session.id)
        .values({edge: selected.id})
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    shared = await skill_builder_service.get_session(db, case.session.id, case.context)
    assert (shared is None) == foreign
    read = Mock(return_value=[])
    with pytest.MonkeyPatch.context() as patches:
        patches.setattr(skill_draft_workspace, "load_draft_files", read)
        available = tools.build_skill_builder_tools(
            session_id=str(case.session.id),
            workspace_path=case.bound_path,
            session_factory=TestSession,
            context=case.context,
        )
        validate = next(tool for tool in available if tool.name == "validate_skill")
        denied = False
        try:
            await validate.ainvoke({})
        except AppError:
            denied = True
    stored = await db.scalar(
        select(SkillBuilderSession.validation_result).where(
            SkillBuilderSession.id == case.session.id
        )
    )
    if foreign:
        assert denied and read.call_count == 0 and stored is None
    else:
        assert not denied and read.call_count == 1 and stored is not None
