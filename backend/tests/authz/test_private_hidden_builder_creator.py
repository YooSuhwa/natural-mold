"""Hidden SkillBuilder creators refresh authority before model resolution and insertion."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.agent import Agent
from app.models.organization import OrganizationMember
from app.services import skill_builder_hidden_agent
from app.services import system_credential_resolver as system_models

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase

pytest_plugins = ["tests.authz.test_private_agent_api_scope"]


@pytest.mark.parametrize("revoked", [False, True])
async def test_hidden_agent_writer_rechecks_membership_before_model_or_row(
    db: AsyncSession, api_case: ApiCase, private_scopes: PrivateScopes, revoked: bool
) -> None:
    context = private_scopes.first
    if revoked:
        await db.execute(
            delete(OrganizationMember).where(
                OrganizationMember.org_id == context.org_id,
                OrganizationMember.user_id == context.user_id,
            )
        )
        await db.commit()
    model = AsyncMock(
        return_value=system_models.ResolvedSystemModel(
            provider="openai", model_name="fixture", api_key=None, base_url=None
        )
    )
    denied = False
    with pytest.MonkeyPatch.context() as patches:
        patches.setattr(skill_builder_hidden_agent, "resolve_system_model", model)
        try:
            await skill_builder_hidden_agent.get_or_create_skill_builder_agent(db, context)
        except AppError:
            denied = True
    await db.commit()
    count = await db.scalar(
        select(func.count())
        .select_from(Agent)
        .where(Agent.org_id == context.org_id, Agent.runtime_profile == "skill_builder")
    )
    print(f"HIDDEN revoked={revoked} denied={denied} models={model.await_count} rows={count}")
    assert denied == revoked
    assert model.await_count == (0 if revoked else 1)
    assert count == (0 if revoked else 1)
