"""Independent bounded witnesses for cached capability and builder roots."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.credentials.creation import CredentialCreation
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.skill_builder_session import SkillBuilderSession
from app.models.tool import Tool
from app.services import skill_builder_service
from app.services.chat.runtime_context import get_agent_with_tools
from app.services.chat.secrets import collect_conversation_secret_values

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_secret_collector import CollectorCase

pytest_plugins = ["tests.authz.test_private_secret_collector"]


async def test_confirmation_rechecks_persisted_session_scope(
    db: AsyncSession, private_scopes: PrivateScopes
) -> None:
    """An exact-scope cached row must not authorize a persisted foreign session."""
    own, foreign = private_scopes.first, private_scopes.second
    session = await skill_builder_service.create_session(db, context=own, user_request="Fixture")
    session.draft_package = {
        "name": "Fixture",
        "slug": "fixture",
        "description": "Fixture",
        "files": [],
    }
    await db.commit()
    await db.execute(
        update(SkillBuilderSession)
        .where(SkillBuilderSession.id == session.id)
        .values(org_id=foreign.org_id, tenant_id=foreign.tenant_id)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    assert session.org_id == own.org_id
    with pytest.raises(AppError):
        await skill_builder_service.confirm_session(db, session, context=own)
    await db.commit()
    stored = (
        await db.execute(
            select(
                SkillBuilderSession.org_id,
                SkillBuilderSession.status,
                SkillBuilderSession.validation_result,
            ).where(SkillBuilderSession.id == session.id)
        )
    ).one()
    assert stored.org_id == foreign.org_id
    assert stored.validation_result is None, "Foreign persisted builder row was mutated"


async def test_collector_rechecks_persisted_tool_credential_edge(
    db: AsyncSession, collector_case: CollectorCase, private_scopes: PrivateScopes
) -> None:
    """A previously loaded owned capability must not hide a persisted foreign FK."""
    own = private_scopes.first
    credential = await credential_service.create(
        db, CredentialCreation("openai", "Owned fixture", {"api_key": "B06_DUMMY_ONLY"}), own
    )
    agent = await db.get(Agent, collector_case.conversation.agent_id)
    assert agent is not None
    agent.llm_credential_id = credential.id
    await db.execute(update(Tool).values(credential_id=credential.id))
    await db.commit()
    db.expunge_all()
    cached = await get_agent_with_tools(db, agent.id, own)
    assert cached is not None
    tool = cached.tool_links[0].tool
    assert tool is not None and tool.credential_id == credential.id
    foreign = await credential_service.create(
        db,
        CredentialCreation("openai", "Foreign fixture", {"api_key": "B06_DUMMY_FOREIGN"}),
        private_scopes.second,
    )
    await db.execute(
        update(Tool)
        .where(Tool.id == tool.id)
        .values(credential_id=foreign.id)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    assert tool.credential_id == credential.id
    decrypt = AsyncMock(wraps=credential_service.decrypt_with_external)
    with pytest.MonkeyPatch.context() as patches:
        patches.setattr(credential_service, "decrypt_with_external", decrypt)
        with pytest.raises(AppError):
            await collect_conversation_secret_values(db, collector_case.conversation, own)
    assert decrypt.await_count == 0, "Owned decryption preceded persisted malformed-edge rejection"
