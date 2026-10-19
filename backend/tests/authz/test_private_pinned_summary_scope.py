"""Pinned summary children inherit the current private conversation scope."""

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
from app.models.conversation_pinned_summary import ConversationPinnedSummary
from app.models.organization import OrganizationMember
from app.services.conversation_pinned_summary_service import (
    PinSummaryTarget,
    SummarySource,
    get_summary,
    pin_summary,
    unpin_summary,
)

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]
Operation = Literal["read", "pin", "unpin"]


@dataclass(frozen=True, slots=True)
class SummaryScenario:
    conversation: Conversation
    context: OrgContext
    denied: bool


@pytest.fixture(params=["foreign", "revoked", "own"])
async def summary_scenario(
    db: AsyncSession, private_scopes: PrivateScopes, request: pytest.FixtureRequest
) -> SummaryScenario:
    own = private_scopes.first
    conversation = Conversation(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        agent_id=None,
        agent_lifecycle_state="purged",
        agent_purged_at=datetime.now(UTC),
        agent_name_snapshot="Retained fixture",
        last_activity_at=datetime.now(UTC),
    )
    db.add(conversation)
    await db.flush()
    db.add(
        ConversationPinnedSummary(
            conversation_id=conversation.id,
            source_message_id="fixture",
            source_branch_checkpoint_id="checkpoint",
            snapshot_text="Original snapshot",
            source_text_hash="fixture-hash",
        )
    )
    await db.commit()
    match request.param:
        case "foreign":
            return SummaryScenario(conversation, private_scopes.second, True)
        case "revoked":
            await db.execute(
                delete(OrganizationMember).where(
                    OrganizationMember.org_id == own.org_id,
                    OrganizationMember.user_id == own.user_id,
                )
            )
            await db.commit()
            return SummaryScenario(conversation, own, True)
        case "own":
            return SummaryScenario(conversation, own, False)
        case _:
            pytest.fail("Unknown summary fixture")


async def _apply(db: AsyncSession, scenario: SummaryScenario, operation: Operation) -> None:
    conversation_id = scenario.conversation.id
    match operation:
        case "read":
            row = await get_summary(db, conversation_id, scenario.context)
            assert row is not None and row.snapshot_text == "Original snapshot"
        case "pin":
            source = SummarySource("fixture", "assistant", "Changed snapshot", "checkpoint")
            await pin_summary(
                db, PinSummaryTarget(conversation_id, scenario.context, "fixture", (source,))
            )
        case "unpin":
            await unpin_summary(db, conversation_id, scenario.context)
        case unreachable:
            assert_never(unreachable)


@pytest.mark.parametrize("operation", ["read", "pin", "unpin"])
async def test_summary_service_requires_current_private_conversation(
    db: AsyncSession, summary_scenario: SummaryScenario, operation: Operation
) -> None:
    # Given a persisted pinned snapshot in a declared private conversation.
    # When an own, foreign or revoked actor calls the real summary service.
    if summary_scenario.denied:
        with pytest.raises(AppError) as rejected:
            await _apply(db, summary_scenario, operation)
        assert rejected.value.status == 404
    else:
        await _apply(db, summary_scenario, operation)
    await db.flush()
    db.expunge_all()
    row = await db.get(ConversationPinnedSummary, summary_scenario.conversation.id)
    # Then only an active private owner can alter or read the child snapshot.
    if summary_scenario.denied or operation == "read":
        assert row is not None and row.snapshot_text == "Original snapshot"
    elif operation == "pin":
        assert row is not None and row.snapshot_text == "Changed snapshot"
    else:
        assert row is None
