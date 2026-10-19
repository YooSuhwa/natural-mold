from __future__ import annotations

import uuid
from datetime import UTC as _FIXTURE_UTC
from datetime import datetime as _fixture_datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.conversation_pinned_summary import ConversationPinnedSummary
from app.services.conversation_pinned_summary_service import (
    InvalidPinnedSummarySourceError,
    MessageRole,
    PinSummaryTarget,
    SummarySource,
    pin_summary,
    resolve_source_status,
    unpin_summary,
)
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID, seed_agent


def _source(
    message_id: str,
    *,
    role: MessageRole = "assistant",
    text: str = "Pinned answer",
    branch: str = "checkpoint-active",
) -> SummarySource:
    return SummarySource(
        message_id=message_id,
        role=role,
        text=text,
        branch_checkpoint_id=branch,
    )


@pytest.mark.asyncio
async def test_pin_summary_persists_bounded_snapshot_across_session_reload(
    db: AsyncSession,
) -> None:
    # Given
    conversation_id = uuid.uuid4()
    message_id = f"lc-run-{uuid.uuid4()}"
    source = _source(message_id, text="x" * 5000)
    _, _, agent = await seed_agent(db)
    db.add(
        Conversation(
            id=conversation_id,
            agent_id=agent.id,
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            user_id=agent.user_id,
            agent_name_snapshot=agent.name,
            last_activity_at=_fixture_datetime.now(_FIXTURE_UTC),
        )
    )
    await db.flush()

    # When
    row = await pin_summary(
        db, PinSummaryTarget(conversation_id, unit_context(TEST_USER_ID), message_id, (source,))
    )
    await db.commit()
    db.expunge_all()
    restored = await db.get(ConversationPinnedSummary, conversation_id)

    # Then
    assert len(row.snapshot_text) == 4000
    assert restored is not None
    assert restored.source_message_id == message_id
    assert restored.snapshot_text == "x" * 4000


def test_resolve_source_status_marks_changed_source_without_mutating_snapshot() -> None:
    # Given
    message_id = f"lc-run-{uuid.uuid4()}"
    row = ConversationPinnedSummary(
        conversation_id=uuid.uuid4(),
        source_message_id=message_id,
        source_branch_checkpoint_id="checkpoint-original",
        snapshot_text="Original answer",
        source_text_hash="93e98e2f1d58aa08d2f12805dfc3bcbe86b224fbd4f9957479bf5b14be2d443c",
    )

    # When
    status = resolve_source_status(row, (_source(message_id, text="Changed answer"),), ())

    # Then
    assert status == "changed"
    assert row.snapshot_text == "Original answer"


@pytest.mark.asyncio
async def test_pin_summary_treats_trimmed_source_as_current(db: AsyncSession) -> None:
    # Given
    conversation_id = uuid.uuid4()
    message_id = f"lc-run-{uuid.uuid4()}"
    source = _source(message_id, text="  Pinned answer  ")
    _, _, agent = await seed_agent(db)
    db.add(
        Conversation(
            id=conversation_id,
            agent_id=agent.id,
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            user_id=agent.user_id,
            agent_name_snapshot=agent.name,
            last_activity_at=_fixture_datetime.now(_FIXTURE_UTC),
        )
    )
    await db.flush()
    row = await pin_summary(
        db, PinSummaryTarget(conversation_id, unit_context(TEST_USER_ID), message_id, (source,))
    )

    # When
    status = resolve_source_status(row, (source,), ())

    # Then
    assert status == "current"


def test_resolve_source_status_distinguishes_other_branch_from_deleted() -> None:
    # Given
    message_id = f"lc-run-{uuid.uuid4()}"
    row = ConversationPinnedSummary(
        conversation_id=uuid.uuid4(),
        source_message_id=message_id,
        source_branch_checkpoint_id="checkpoint-original",
        snapshot_text="Original answer",
        source_text_hash="93e98e2f1d58aa08d2f12805dfc3bcbe86b224fbd4f9957479bf5b14be2d443c",
    )

    # When
    other_branch = resolve_source_status(row, (), (_source(message_id),))
    deleted = resolve_source_status(row, (), ())

    # Then
    assert other_branch == "other_branch"
    assert deleted == "deleted"


@pytest.mark.asyncio
async def test_pin_summary_rejects_non_assistant_source(db: AsyncSession) -> None:
    # Given an authorized conversation and a non-assistant source.
    _, _, agent = await seed_agent(db)
    conversation = Conversation(
        agent_id=agent.id,
        user_id=agent.user_id,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        agent_name_snapshot=agent.name,
        last_activity_at=_fixture_datetime.now(_FIXTURE_UTC),
    )
    db.add(conversation)
    await db.flush()
    message_id = f"lc-run-{uuid.uuid4()}"
    # When / Then only valid assistant sources can be pinned.
    with pytest.raises(InvalidPinnedSummarySourceError) as exc_info:
        await pin_summary(
            db,
            PinSummaryTarget(
                conversation.id,
                unit_context(TEST_USER_ID),
                message_id,
                (_source(message_id, role="user"),),
            ),
        )
    assert exc_info.value.reason == "not_assistant"


@pytest.mark.asyncio
async def test_unpin_summary_removes_persisted_selection(db: AsyncSession) -> None:
    # Given
    conversation_id = uuid.uuid4()
    message_id = f"lc-run-{uuid.uuid4()}"
    _, _, agent = await seed_agent(db)
    db.add(
        Conversation(
            id=conversation_id,
            agent_id=agent.id,
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            user_id=agent.user_id,
            agent_name_snapshot=agent.name,
            last_activity_at=_fixture_datetime.now(_FIXTURE_UTC),
        )
    )
    await db.flush()
    await pin_summary(
        db,
        PinSummaryTarget(
            conversation_id, unit_context(TEST_USER_ID), message_id, (_source(message_id),)
        ),
    )
    await db.commit()

    # When
    await unpin_summary(db, conversation_id, unit_context(TEST_USER_ID))
    await db.commit()

    # Then
    assert await db.get(ConversationPinnedSummary, conversation_id) is None
