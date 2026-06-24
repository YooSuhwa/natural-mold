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

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.message_event import MessageEvent
from app.models.model import Model
from app.models.user import User
from app.services.chat_service import gc_orphan_draft_conversations


async def _seed_agent(db: AsyncSession) -> uuid.UUID:
    """Create User + Model + Agent, return agent_id."""
    user = User(id=uuid.uuid4(), email=f"u-{uuid.uuid4().hex[:8]}@test.com", name="Test")
    db.add(user)
    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    db.add(model)
    await db.flush()
    agent = Agent(user_id=user.id, name="Chat Agent", system_prompt="Hi", model_id=model.id)
    db.add(agent)
    await db.flush()
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

    deleted = await gc_orphan_draft_conversations(db, retention_hours=24)
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

    assert await gc_orphan_draft_conversations(db, retention_hours=24) == 0


@pytest.mark.asyncio
async def test_gc_negative_retention_rejected(db: AsyncSession) -> None:
    with pytest.raises(ValueError):
        await gc_orphan_draft_conversations(db, retention_hours=-1)
