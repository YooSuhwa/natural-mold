"""Private conversation mutations authorize the persisted root before writing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.exceptions import AppError
from app.models.conversation import Conversation
from app.models.organization import OrganizationMember
from app.schemas.conversation import ConversationUpdate
from app.schemas.conversation_scope import (
    ConversationAutoTitle,
    ConversationBranchMutation,
    ConversationMutation,
    ConversationPromotion,
)
from app.services import conversation_side_chat_service
from app.services.chat import conversations

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]
Operation = Literal["update", "read", "touch", "title", "branch", "switch", "draft"]


@dataclass(frozen=True, slots=True)
class MutationScenario:
    row: Conversation
    context: OrgContext
    denied: bool


@pytest.fixture(params=["foreign", "revoked", "own"])
async def mutation_conversation(
    db: AsyncSession, private_scopes: PrivateScopes, request: pytest.FixtureRequest
) -> MutationScenario:
    context = private_scopes.first
    row = Conversation(
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        agent_id=None,
        agent_name_snapshot="Fixture retained agent",
        title="새 대화",
        source="draft",
        unread_count=3,
        active_branch_checkpoint_id="fixture-checkpoint",
        last_activity_at=datetime.now(UTC),
    )
    db.add(row)
    await db.commit()
    match request.param:
        case "foreign":
            return MutationScenario(row, private_scopes.second, True)
        case "revoked":
            await db.execute(
                delete(OrganizationMember).where(
                    OrganizationMember.org_id == context.org_id,
                    OrganizationMember.user_id == context.user_id,
                )
            )
            await db.commit()
            return MutationScenario(row, context, True)
        case "own":
            return MutationScenario(row, context, False)
        case _:
            pytest.fail("Unknown mutation fixture")


async def _mutate(db: AsyncSession, scenario: MutationScenario, operation: Operation) -> None:
    row, context = scenario.row, scenario.context
    match operation:
        case "update":
            await conversations.update_conversation(
                db, ConversationMutation(row.id, context, ConversationUpdate(title="Changed"))
            )
        case "read":
            await conversations.mark_conversation_read(db, row.id, context)
        case "touch":
            await conversations.touch_conversation(db, row.id, context)
        case "title":
            await conversations.maybe_set_auto_title(
                db, ConversationAutoTitle(row.id, context, "Changed")
            )
        case "branch":
            await conversations.clear_active_branch_override(db, row.id, context)
        case "switch":
            await conversations.set_active_branch(
                db, ConversationBranchMutation(row.id, context, "new-checkpoint")
            )
        case "draft":
            await conversations.promote_draft_conversation(
                db, ConversationPromotion(row.id, context, "Changed")
            )
        case unreachable:
            assert_never(unreachable)


@pytest.mark.parametrize(
    "operation", ["update", "read", "touch", "title", "branch", "switch", "draft"]
)
async def test_conversation_mutation_requires_own_active_company(
    db: AsyncSession, mutation_conversation: MutationScenario, operation: Operation
) -> None:
    # Given an explicitly scoped cached conversation and current membership state.
    row = mutation_conversation.row
    before_activity = row.last_activity_at.replace(tzinfo=None)
    before = (
        row.title,
        row.unread_count,
        row.updated_at,
        row.active_branch_checkpoint_id,
        row.source,
    )
    # When a direct service call attempts the private mutation.
    if mutation_conversation.denied:
        with pytest.raises(AppError) as rejected:
            await _mutate(db, mutation_conversation, operation)
        assert rejected.value.status == 404
    else:
        await _mutate(db, mutation_conversation, operation)
    await db.refresh(row)
    after = (
        row.title,
        row.unread_count,
        row.updated_at,
        row.active_branch_checkpoint_id,
        row.source,
    )
    # Then foreign/revoked writes leave data unchanged, while authorized writes take effect.
    if mutation_conversation.denied:
        assert after == before
        assert row.last_activity_at.replace(tzinfo=None) == before_activity
    else:
        match operation:
            case "update" | "title":
                assert row.title == "Changed"
            case "read":
                assert row.unread_count == 0 and row.last_read_at is not None
            case "touch":
                assert row.updated_at > before[2]
                assert row.last_activity_at.replace(tzinfo=None) > before_activity
            case "branch":
                assert row.active_branch_checkpoint_id is None
            case "switch":
                assert row.active_branch_checkpoint_id == "new-checkpoint"
            case "draft":
                assert row.source == "ui" and row.title == "Changed"
            case unreachable:
                assert_never(unreachable)


async def test_side_chat_promotion_requires_current_private_scope(
    db: AsyncSession, mutation_conversation: MutationScenario
) -> None:
    # Given a cached side conversation and an own, foreign or revoked actor.
    row = mutation_conversation.row
    row.source = "side_chat"
    await db.commit()
    # When the service attempts to make it visible in ordinary history.
    if mutation_conversation.denied:
        with pytest.raises(AppError) as rejected:
            await conversation_side_chat_service.promote_side_chat(
                db, row.id, mutation_conversation.context
            )
        assert rejected.value.status == 404
    else:
        assert (
            await conversation_side_chat_service.promote_side_chat(
                db, row.id, mutation_conversation.context
            )
            is True
        )
    # Then only an authorized active owner can change the source.
    await db.flush()
    await db.refresh(row)
    assert row.source == ("side_chat" if mutation_conversation.denied else "ui")
