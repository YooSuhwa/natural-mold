"""Orphan draft-conversation GC (data hygiene).

``POST .../conversations/draft`` creates ``source="draft"`` rows that only
flip to ``"ui"`` once the user sends a first message (promotion). A draft
abandoned before sending is invisible to the UI (which lists ``source=="ui"``)
and never deleted, so empty drafts accumulate. This locks in the contract:
only drafts that are BOTH old AND message-less are collected — recent drafts,
promoted conversations, and drafts that already have a recorded turn all stay.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from datetime import UTC as _FIXTURE_UTC
from datetime import datetime as _fixture_datetime
from typing import Literal, assert_never

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.platform_scope import PlatformScope
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.message_event import MessageEvent
from app.schemas.conversation_scope import DraftConversationCleanup
from app.services.chat_service import gc_orphan_draft_conversations
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, seed_agent


@pytest.mark.parametrize("child_kind", ["side", "run", "input"])
async def test_gc_preserves_drafts_with_recorded_child_work(
    db: AsyncSession, child_kind: Literal["side", "run", "input"]
) -> None:
    # Given an old draft with durable child work but no recorded message event.
    agent_id = await _seed_agent(db)
    parent = _conv(agent_id, source="draft", age_hours=48)
    db.add(parent)
    await db.flush()
    match child_kind:
        case "side":
            child = _conv(agent_id, source="side_chat", age_hours=48)
            child.side_chat_parent_id = parent.id
            db.add(child)
        case "run":
            db.add(
                ConversationRun(
                    conversation_id=parent.id,
                    agent_id=agent_id,
                    user_id=TEST_USER_ID,
                    source="chat",
                    status="running",
                    is_active=True,
                )
            )
        case "input":
            db.add(
                ConversationRunInput(
                    conversation_id=parent.id,
                    agent_id=agent_id,
                    user_id=TEST_USER_ID,
                    client_request_id="fixture",
                    source="chat",
                    status="pending",
                    position=1,
                    input_payload={"messages": [{"role": "user", "content": "Queued work"}]},
                    attachment_ids=[],
                )
            )
        case unreachable:
            assert_never(unreachable)
    await db.commit()
    parent_id = parent.id
    # When platform maintenance collects only genuinely empty abandoned drafts.
    deleted = await gc_orphan_draft_conversations(db, DraftConversationCleanup(PlatformScope(), 24))
    # Then child work is never removed through a draft-row cascade.
    assert deleted == 0
    db.expunge_all()
    assert await db.get(Conversation, parent_id) is not None


async def _seed_agent(db: AsyncSession) -> uuid.UUID:
    """Create User + Model + Agent, return agent_id."""
    _user, _model, agent = await seed_agent(
        db,
        user_id=TEST_USER_ID,
        name="Chat Agent",
        system_prompt="Hi",
    )
    return agent.id


def _conv(agent_id: uuid.UUID, *, source: str, age_hours: float) -> Conversation:
    created = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=age_hours)
    return Conversation(
        id=uuid.uuid4(),
        agent_id=agent_id,
        title="새 대화",
        source=source,
        created_at=created,
        updated_at=created,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        user_id=TEST_USER_ID,
        agent_name_snapshot="Fixture agent",
        last_activity_at=_fixture_datetime.now(_FIXTURE_UTC),
    )


@pytest.mark.asyncio
async def test_gc_deletes_only_old_empty_drafts(db: AsyncSession) -> None:
    agent_id = await _seed_agent(db)

    # Old empty draft — the only one that should be collected.
    old_draft = _conv(agent_id, source="draft", age_hours=48)
    # Recent draft (well inside the 24h window) — user may still be typing.
    recent_draft = _conv(agent_id, source="draft", age_hours=1)
    # Old, but promoted to "ui" — never a draft anymore, must stay.
    promoted = _conv(agent_id, source="ui", age_hours=48)
    # Old draft that already recorded a turn — message-less guard must spare it.
    draft_with_msg = _conv(agent_id, source="draft", age_hours=48)
    db.add_all([old_draft, recent_draft, promoted, draft_with_msg])
    await db.flush()

    db.add(
        MessageEvent(
            id=uuid.uuid4(),
            conversation_id=draft_with_msg.id,
            assistant_msg_id=f"msg-{uuid.uuid4().hex[:8]}",
            events=[],
        )
    )
    await db.commit()

    old_draft_id = old_draft.id
    survivor_ids = {recent_draft.id, promoted.id, draft_with_msg.id}

    deleted = await gc_orphan_draft_conversations(db, DraftConversationCleanup(PlatformScope(), 24))
    assert deleted == 1

    remaining = {row.id for row in (await db.execute(select(Conversation))).scalars().all()}
    assert old_draft_id not in remaining
    assert survivor_ids <= remaining


@pytest.mark.asyncio
async def test_gc_returns_zero_when_nothing_to_collect(db: AsyncSession) -> None:
    agent_id = await _seed_agent(db)
    db.add(_conv(agent_id, source="draft", age_hours=1))
    db.add(_conv(agent_id, source="ui", age_hours=48))
    await db.commit()

    assert (
        await gc_orphan_draft_conversations(db, DraftConversationCleanup(PlatformScope(), 24)) == 0
    )


@pytest.mark.asyncio
async def test_gc_negative_retention_rejected(db: AsyncSession) -> None:
    with pytest.raises(ValueError, match="retention_hours must be >= 1"):
        await gc_orphan_draft_conversations(db, DraftConversationCleanup(PlatformScope(), -1))


@pytest.mark.asyncio
async def test_gc_zero_retention_rejected_and_spares_just_created_draft(
    db: AsyncSession,
) -> None:
    # ``retention_hours == 0`` sets ``cutoff = now``, so a draft created moments ago
    # (user still typing) would be deleted. We REJECT 0 (chosen over clamping so a
    # mis-set value surfaces loudly), and the just-created draft must survive untouched.
    agent_id = await _seed_agent(db)
    fresh_draft = _conv(agent_id, source="draft", age_hours=0)
    db.add(fresh_draft)
    await db.commit()
    fresh_draft_id = fresh_draft.id

    with pytest.raises(ValueError, match="retention_hours must be >= 1"):
        await gc_orphan_draft_conversations(db, DraftConversationCleanup(PlatformScope(), 0))

    remaining = {row.id for row in (await db.execute(select(Conversation))).scalars().all()}
    assert fresh_draft_id in remaining
