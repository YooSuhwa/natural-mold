"""Foreign Skill edges must fail before runtime content or credentials are prepared."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.marketplace import skill_runtime
from app.models.agent import Agent
from app.models.model import Model
from app.models.skill import AgentSkillLink, Skill
from app.schemas.agent import AgentCreate
from app.services import agent_invocation_service, agent_service
from app.services.agent_invocation_service import AgentInvocationPrincipal
from app.services.chat.runtime_context import get_agent_with_tools

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


@dataclass(frozen=True, slots=True)
class ForeignSkillCase:
    parent: Agent
    context: OrgContext


@pytest.fixture
async def foreign_skill_case(
    db: AsyncSession, private_scopes: PrivateScopes, tmp_path: Path
) -> ForeignSkillCase:
    model = Model(provider="openai", model_name="fixture", display_name="Fixture")
    db.add(model)
    await db.commit()
    parent = await agent_service.create_agent(
        db,
        AgentCreate(name="Parent", system_prompt="Fixture", model_id=model.id),
        private_scopes.first,
    )
    foreign = private_scopes.second
    source = tmp_path / "foreign-skill.md"
    source.write_text("# Private foreign company Skill\n", encoding="utf-8")
    skill = Skill(
        user_id=foreign.user_id,
        org_id=foreign.org_id,
        tenant_id=foreign.tenant_id,
        name="Foreign private Skill",
        slug="foreign-private",
        kind="text",
        storage_path=str(source),
    )
    db.add(skill)
    await db.flush()
    db.add(AgentSkillLink(agent_id=parent.id, skill_id=skill.id))
    await db.commit()
    db.expunge_all()
    loaded = await get_agent_with_tools(db, parent.id, private_scopes.first)
    assert loaded is not None
    return ForeignSkillCase(loaded, private_scopes.first)


async def _prepare_physical_runtime(db: AsyncSession, case: ForeignSkillCase, root: Path) -> None:
    user = CurrentUser(id=case.context.user_id, email="fixture@example.com", name="Fixture")
    cfg = await agent_invocation_service.build_agent_config_for_loaded_agent(
        db,
        case.parent,
        context=case.context,
        thread_id="fixture-thread",
        principal=AgentInvocationPrincipal.chat_user(user),
        source="chat",
        current_user=user,
    )
    skill_runtime.build_skill_runtime_context(
        cfg, data_dir=root / "runtime-data", output_root=root / "outputs"
    )


async def test_foreign_skill_is_denied_before_any_runtime_configuration(
    db: AsyncSession, foreign_skill_case: ForeignSkillCase, tmp_path: Path
) -> None:
    case = foreign_skill_case
    with pytest.MonkeyPatch.context() as patches:
        resolve = AsyncMock(return_value="B06_DUMMY_ONLY")
        patches.setattr(agent_invocation_service, "resolve_llm_api_key_for_agent", resolve)
        materialize = Mock(wraps=skill_runtime._materialize_skill)
        patches.setattr(skill_runtime, "_materialize_skill", materialize)
        with pytest.raises(AppError) as captured:
            await _prepare_physical_runtime(db, case, tmp_path)
        assert captured.value.status == 404
        resolve.assert_not_awaited()
        materialize.assert_not_called()
        assert not (tmp_path / "runtime-data").exists()
