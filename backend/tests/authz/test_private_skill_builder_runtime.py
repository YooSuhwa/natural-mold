"""Skill Builder runtime cannot bind a foreign session to an owned conversation."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_policy import SKILL_BUILDER_RUNTIME_POLICY
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.services import conversation_stream_service, skill_builder_service, skill_draft_workspace
from app.services import system_credential_resolver as system_models
from app.services.chat.runtime_context import get_agent_with_tools

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_secret_collector import CollectorCase

pytest_plugins = ["tests.authz.test_private_secret_collector"]


async def test_skill_builder_runtime_rejects_foreign_session_before_workspace_or_model(
    db: AsyncSession, collector_case: CollectorCase, private_scopes: PrivateScopes
) -> None:
    conversation = collector_case.conversation
    assert conversation.agent_id is not None
    agent = await get_agent_with_tools(db, conversation.agent_id, private_scopes.first)
    assert agent is not None
    session = await skill_builder_service.create_session(
        db, context=private_scopes.second, user_request="Other organization private draft"
    )
    session.conversation_id = conversation.id
    await db.commit()
    user = CurrentUser(id=private_scopes.first.user_id, email="fixture@example.com", name="Fixture")
    with pytest.MonkeyPatch.context() as patches:
        create_workspace = Mock(return_value=f"skill-drafts/{session.id}")
        resolve_model = AsyncMock(
            return_value=system_models.ResolvedSystemModel(
                provider="openai", model_name="fixture", api_key=None, base_url=None
            )
        )
        patches.setattr(skill_draft_workspace, "create_workspace", create_workspace)
        patches.setattr(system_models, "resolve_system_model", resolve_model)
        with pytest.raises(AppError) as rejected:
            await conversation_stream_service._resolve_skill_builder_agent_context(
                db,
                conversation,
                agent,
                user,
                context=private_scopes.first,
                runtime_policy=SKILL_BUILDER_RUNTIME_POLICY,
                checkpoint_id=None,
            )
        assert rejected.value.status == 404
        create_workspace.assert_not_called()
        resolve_model.assert_not_awaited()
    await db.refresh(session)
    assert session.draft_workspace_path is None
