"""Policy initialization follows the explicit private conversation and Agent scope."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.organization import OrganizationMember
from app.schemas.conversation_scope import ConversationPolicyTarget
from app.services.conversation_runtime_policy import ensure_conversation_runtime_policy
from app.services.conversation_side_chat_service import get_or_create_side_chat

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


@dataclass(frozen=True, slots=True)
class PolicyScenario:
    conversation: Conversation
    context: OrgContext
    denied: bool


@pytest.fixture(params=["foreign", "revoked", "own", "foreign_agent"])
async def policy_scenario(
    db: AsyncSession, private_scopes: PrivateScopes, request: pytest.FixtureRequest
) -> PolicyScenario:
    own = private_scopes.first
    agent_context = private_scopes.second if request.param == "foreign_agent" else own
    model = Model(provider="openai", model_name="fixture", display_name="Fixture")
    db.add(model)
    await db.flush()
    agent = Agent(
        user_id=agent_context.user_id,
        org_id=agent_context.org_id,
        tenant_id=agent_context.tenant_id,
        name="Policy fixture",
        system_prompt="Fixture",
        model_id=model.id,
    )
    db.add(agent)
    await db.flush()
    row = Conversation(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        agent_id=agent.id,
        agent_name_snapshot="Policy fixture",
        last_activity_at=datetime.now(UTC),
    )
    db.add(row)
    await db.commit()
    match request.param:
        case "foreign":
            return PolicyScenario(row, private_scopes.second, True)
        case "revoked":
            await db.execute(
                delete(OrganizationMember).where(
                    OrganizationMember.org_id == own.org_id,
                    OrganizationMember.user_id == own.user_id,
                )
            )
            await db.commit()
            return PolicyScenario(row, own, True)
        case "foreign_agent":
            return PolicyScenario(row, own, True)
        case "own":
            return PolicyScenario(row, own, False)
        case _:
            pytest.fail("Unknown policy fixture")


async def test_policy_initialization_requires_current_private_root_and_agent(
    db: AsyncSession, policy_scenario: PolicyScenario
) -> None:
    # Given an uninitialized private conversation and explicit actor scope.
    row = policy_scenario.conversation
    assert row.runtime_policy_snapshot is None
    # When policy initialization attempts to modify the persisted conversation.
    if policy_scenario.denied:
        with pytest.raises(AppError) as rejected:
            await ensure_conversation_runtime_policy(
                db, ConversationPolicyTarget(row.id, policy_scenario.context)
            )
        assert rejected.value.status == 404
    else:
        conversation, _, policy = await ensure_conversation_runtime_policy(
            db, ConversationPolicyTarget(row.id, policy_scenario.context)
        )
        assert conversation.id == row.id
        assert conversation.runtime_policy_hash == policy.policy_hash
    # Then foreign/revoked/mismatched writes leave the policy tuple empty.
    await db.flush()
    await db.refresh(row)
    if policy_scenario.denied:
        assert row.runtime_policy_snapshot is None
        assert row.runtime_policy_hash is None
    else:
        assert row.runtime_policy_snapshot is not None


async def test_side_chat_reuse_rejects_mismatched_existing_child(
    db: AsyncSession, private_scopes: PrivateScopes
) -> None:
    # Given an owned parent whose existing unique side child has a foreign scope.
    own = private_scopes.first
    foreign = private_scopes.second
    model = Model(provider="openai", model_name="side-fixture", display_name="Fixture")
    db.add(model)
    await db.flush()
    agent = Agent(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="Fixture",
        system_prompt="Fixture",
        model_id=model.id,
    )
    db.add(agent)
    await db.flush()
    parent = Conversation(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        agent_id=agent.id,
        agent_name_snapshot="Fixture",
        last_activity_at=datetime.now(UTC),
    )
    db.add(parent)
    await db.flush()
    child = Conversation(
        user_id=foreign.user_id,
        org_id=foreign.org_id,
        tenant_id=foreign.tenant_id,
        agent_id=agent.id,
        agent_name_snapshot="Foreign fixture",
        source="side_chat",
        side_chat_parent_id=parent.id,
        last_activity_at=datetime.now(UTC),
    )
    db.add(child)
    await db.commit()
    # When side-chat reuse attempts to traverse that invalid parent/child link.
    with pytest.raises(AppError) as rejected:
        await get_or_create_side_chat(db, parent.id, own)
    # Then the child remains foreign and is neither returned nor replaced.
    assert rejected.value.status == 404
    await db.refresh(child)
    assert child.source == "side_chat" and child.org_id == foreign.org_id
