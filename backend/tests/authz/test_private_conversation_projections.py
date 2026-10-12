"""Conversation history survives missing agents without exposing foreign agent details."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, assert_never
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.model import Model
from app.services.chat import conversations, runtime_context

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


@dataclass(frozen=True, slots=True)
class ProjectionScenario:
    context: OrgContext
    conversation_id: UUID


@pytest.fixture(params=["missing", "foreign"])
async def projection_scenario(
    db: AsyncSession, private_scopes: PrivateScopes, request: pytest.FixtureRequest
) -> ProjectionScenario:
    agent_id = None
    agent_lifecycle_state = "purged"
    agent_purged_at = datetime.now(UTC)
    if request.param == "foreign":
        model = Model(provider="openai", model_name="fixture", display_name="Fixture")
        db.add(model)
        await db.flush()
        foreign = private_scopes.second
        agent = Agent(
            user_id=foreign.user_id,
            org_id=foreign.org_id,
            tenant_id=foreign.tenant_id,
            name="Foreign private name",
            system_prompt="Foreign private prompt",
            model_id=model.id,
        )
        db.add(agent)
        await db.flush()
        agent_id = agent.id
        agent_lifecycle_state = "active"
        agent_purged_at = None
    context = private_scopes.first
    row = Conversation(
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        agent_id=agent_id,
        agent_lifecycle_state=agent_lifecycle_state,
        agent_purged_at=agent_purged_at,
        agent_name_snapshot="Retained snapshot",
        last_activity_at=datetime.now(UTC),
        title="Retained history",
    )
    db.add(row)
    await db.commit()
    scenario = ProjectionScenario(context, row.id)
    db.expunge_all()
    return scenario


async def test_private_global_history_preserves_agentless_or_mismatched_roots(
    db: AsyncSession, projection_scenario: ProjectionScenario
) -> None:
    # Given a directly owned history whose live Agent is absent or foreign.
    # When the history navigator reads the active private company.
    rows, _, _ = await conversations.list_global_conversations_page(
        db, projection_scenario.context, limit=10
    )
    # Then its own retained row stays visible without the foreign Agent projection.
    assert [row.id for row in rows] == [projection_scenario.conversation_id]
    assert rows[0].agent is None
    assert rows[0].agent_name_snapshot == "Retained snapshot"


@pytest.mark.parametrize("reader", ["ui", "runtime"])
async def test_private_conversation_projection_hides_foreign_or_missing_agent(
    db: AsyncSession,
    projection_scenario: ProjectionScenario,
    reader: Literal["ui", "runtime"],
) -> None:
    # Given a directly owned history with a mismatched or absent live Agent.
    # When a conversation reader hydrates its child projection.
    match reader:
        case "ui":
            row = await conversations.get_owned_ui_conversation_with_agent(
                db, projection_scenario.conversation_id, projection_scenario.context
            )
        case "runtime":
            row = await runtime_context.get_owned_conversation_with_agent(
                db, projection_scenario.conversation_id, projection_scenario.context
            )
        case unreachable:
            assert_never(unreachable)
    # Then retained ownership survives but the foreign live Agent is not exposed.
    assert row is not None and row.id == projection_scenario.conversation_id
    assert row.agent is None
