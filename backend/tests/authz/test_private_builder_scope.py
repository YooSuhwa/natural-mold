"""Builder sessions and final Agent materialization keep the declared private organization."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.agent import Agent
from app.models.model import Model
from app.schemas.builder import BuilderStatus
from app.schemas.skill_builder import SkillBuilderStatus
from app.schemas.skill_builder_scope import SkillBuilderChatAttachment
from app.services import builder_service, skill_builder_service
from app.services.chat.conversations import create_conversation

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


async def test_builder_session_writer_and_lookup_keep_exact_scope(
    db: AsyncSession, private_scopes: PrivateScopes
) -> None:
    session = await builder_service.create_session(db, private_scopes.first, "Fixture request")
    assert (session.user_id, session.org_id, session.tenant_id) == (
        private_scopes.first.user_id,
        private_scopes.first.org_id,
        private_scopes.first.tenant_id,
    )
    assert await builder_service.get_session(db, session.id, private_scopes.first) is not None
    assert await builder_service.get_session(db, session.id, private_scopes.second) is None


async def test_builder_confirm_materializes_agent_in_session_scope(
    db: AsyncSession, private_scopes: PrivateScopes
) -> None:
    model = Model(provider="openai", model_name="fixture", display_name="Fixture")
    db.add(model)
    await db.commit()
    session = await builder_service.create_session(db, private_scopes.first, "Fixture request")
    session.status = BuilderStatus.PREVIEW
    session.draft_config = {
        "name": "Built fixture",
        "system_prompt": "Fixture",
        "model_name": "openai:fixture",
    }
    await db.commit()
    agent = await builder_service.confirm_build(db, session, private_scopes.first)
    assert agent is not None
    assert (agent.user_id, agent.org_id, agent.tenant_id) == (
        session.user_id,
        session.org_id,
        session.tenant_id,
    )
    assert session.status == BuilderStatus.COMPLETED
    assert await db.scalar(select(Agent.id).where(Agent.id == agent.id)) == agent.id


async def test_builder_confirm_rejects_same_owner_foreign_company_before_materializing(
    db: AsyncSession, private_scopes: PrivateScopes
) -> None:
    from app.exceptions import AppError

    session = await builder_service.create_session(db, private_scopes.first, "Fixture request")
    session.status = BuilderStatus.PREVIEW
    session.draft_config = {"name": "Must not be materialized", "system_prompt": "Fixture"}
    await db.commit()
    with pytest.raises(AppError) as captured:
        await builder_service.confirm_build(db, session, private_scopes.second)
    assert captured.value.status == 404
    assert await db.scalar(select(Agent.id)) is None
    assert session.status == BuilderStatus.PREVIEW


async def test_skill_builder_cannot_attach_foreign_conversation(
    db: AsyncSession, private_scopes: PrivateScopes
) -> None:
    # Given an own session and a same-user conversation in another company.
    model = Model(provider="openai", model_name="fixture", display_name="Fixture")
    db.add(model)
    await db.flush()
    other = private_scopes.second
    agent = Agent(
        user_id=other.user_id,
        org_id=other.org_id,
        tenant_id=other.tenant_id,
        model_id=model.id,
        name="Foreign fixture",
        system_prompt="Fixture",
    )
    db.add(agent)
    await db.flush()
    conversation = await create_conversation(db, agent.id, context=other)
    session = await skill_builder_service.create_session(
        db, context=private_scopes.first, user_request="Fixture"
    )
    await db.commit()
    # When runtime attachment attempts to cross the persisted private boundary.
    with pytest.raises(AppError) as rejected:
        await skill_builder_service.attach_chat_runtime(
            db,
            SkillBuilderChatAttachment(
                session.id, conversation.id, "draft/fixture", private_scopes.first
            ),
        )
    # Then the session keeps its original empty conversation reference.
    assert rejected.value.status == 404
    await db.refresh(session)
    assert session.conversation_id is None


async def test_foreign_skill_builder_claim_does_not_change_state(
    db: AsyncSession, private_scopes: PrivateScopes
) -> None:
    # Given a ready Skill builder session in company A.
    session = await skill_builder_service.create_session(
        db, context=private_scopes.first, user_request="Fixture"
    )
    session.status = SkillBuilderStatus.REVIEW.value
    await db.commit()
    # When the same owner tries confirmation from company B.
    claimed = await skill_builder_service.claim_for_confirming(
        db, session.id, context=private_scopes.second
    )
    # Then the foreign session remains ready and untouched.
    assert claimed is False
    await db.refresh(session)
    assert session.status == SkillBuilderStatus.REVIEW.value


async def test_foreign_builder_confirmation_claim_does_not_change_state(
    db: AsyncSession, private_scopes: PrivateScopes
) -> None:
    # Given a preview session in company A.
    session = await builder_service.create_session(db, private_scopes.first, "Fixture")
    session.status = BuilderStatus.PREVIEW
    await db.commit()
    # When the same owner tries the state transition from company B.
    claimed = await builder_service.claim_for_confirming(
        db, session.id, context=private_scopes.second
    )
    # Then the foreign session remains untouched.
    assert claimed is False
    await db.refresh(session)
    assert session.status == BuilderStatus.PREVIEW
