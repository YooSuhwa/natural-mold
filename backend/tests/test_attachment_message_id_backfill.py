"""M1 — backfill ``message_attachments.message_id`` at turn finalize.

The crux: the id stamped on a sent upload must equal the id the read path
(:func:`list_messages_from_checkpointer`) will later key attachment hydration
on. ``msg_id_sink`` only carries AI message ids, so the user message id can't
be read there — it must be derived from the post-run checkpoint with the
**same** ``parse_msg_id`` walk the read path uses, never assumed at ``idx=0``.

These tests pin the resolver to the read path by feeding both the **same**
constructed message tree, and prove an attachment stamped with the resolver's
output is echoed on the correct user bubble.
"""

from __future__ import annotations

import uuid

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.message_attachment import MessageAttachment
from app.models.model import Model
from app.models.user import User
from app.services.chat_service import (
    link_attachments_to_message,
    list_messages_from_checkpointer,
    resolve_turn_user_message_id,
)
from app.services.thread_branch_service import MessageTree, MessageTreeNode
from tests.conftest import TEST_USER_ID


async def _seed_conversation(db: AsyncSession) -> Conversation:
    if await db.get(User, TEST_USER_ID) is None:
        db.add(User(id=TEST_USER_ID, email="test@test.com", name="Test"))
    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    db.add(model)
    await db.flush()
    agent = Agent(user_id=TEST_USER_ID, name="A", system_prompt="x", model_id=model.id)
    db.add(agent)
    await db.flush()
    conv = Conversation(agent_id=agent.id, title="t")
    db.add(conv)
    await db.flush()
    return conv


def _tree(messages: list[BaseMessage]) -> MessageTree:
    """Linear active-chain tree mirroring what ``build_message_tree`` returns."""

    nodes = [
        MessageTreeNode(message=m, parent_id=None, introduced_by_checkpoint_id="ck")
        for m in messages
    ]
    tip = str(getattr(messages[-1], "id", None)) if messages else None
    return MessageTree(nodes=nodes, active_tip_message_id=tip, active_checkpoint_id="ck")


def _attachment(conv: Conversation, *, message_id: str | None) -> MessageAttachment:
    upload_id = uuid.uuid4()
    return MessageAttachment(
        id=upload_id,
        user_id=TEST_USER_ID,
        conversation_id=conv.id,
        message_id=message_id,
        filename="f.png",
        mime_type="image/png",
        size_bytes=3,
        storage_path=f"/tmp/{upload_id}.png",
        url=f"/api/uploads/{upload_id}",
    )


@pytest.mark.asyncio
async def test_resolver_id_matches_read_path_and_echoes_on_last_user_bubble(
    db: AsyncSession,
) -> None:
    conv = await _seed_conversation(db)
    # Two turns: the SECOND human (idC) is "this turn" — the assistant reply
    # never appends a human, so the last human in the active chain is it.
    id_a, id_c = str(uuid.uuid4()), str(uuid.uuid4())
    tree = _tree(
        [
            HumanMessage(content="first", id=id_a),
            AIMessage(content="reply 1", id=str(uuid.uuid4())),
            HumanMessage(content="second", id=id_c),
            AIMessage(content="reply 2", id=str(uuid.uuid4())),
        ]
    )

    resolved = await resolve_turn_user_message_id(db, conv, tree=tree)
    # A valid-UUID id is returned verbatim by parse_msg_id (idx irrelevant).
    assert resolved == id_c

    # Stamp an upload with the resolver's output, then read back through the
    # real hydration path with the SAME tree: it must echo on that user bubble.
    att = _attachment(conv, message_id=resolved)
    db.add(att)
    await db.flush()

    responses = await list_messages_from_checkpointer(
        db, conv, user_id=TEST_USER_ID, tree=tree
    )
    user_msgs = [r for r in responses if r.role == "user"]
    last_user = user_msgs[-1]
    assert str(last_user.id) == resolved
    assert last_user.attachments is not None
    assert [a.id for a in last_user.attachments] == [att.id]
    # The earlier user bubble carries no attachment.
    assert user_msgs[0].attachments is None


@pytest.mark.asyncio
async def test_share_view_excludes_attachments(db: AsyncSession) -> None:
    """D11/M2 — a public share reads with ``user_id=None``; backfilled
    attachments must stay out of the snapshot (authed-view side channel only)."""

    conv = await _seed_conversation(db)
    msg_id = str(uuid.uuid4())
    tree = _tree(
        [HumanMessage(content="hi", id=msg_id), AIMessage(content="yo", id=str(uuid.uuid4()))]
    )
    db.add(_attachment(conv, message_id=msg_id))
    await db.flush()

    # Authed view echoes it...
    authed = await list_messages_from_checkpointer(db, conv, user_id=TEST_USER_ID, tree=tree)
    assert any(r.attachments for r in authed)

    # ...the share view (user_id=None) does not.
    shared = await list_messages_from_checkpointer(db, conv, user_id=None, tree=tree)
    assert all(r.attachments is None for r in shared)


@pytest.mark.asyncio
async def test_resolver_matches_read_path_for_idless_human(db: AsyncSession) -> None:
    """When the checkpoint message has no id, both sides fall back to the
    same ``uuid5(conversation_id, idx)`` — so idx alignment must match too."""

    conv = await _seed_conversation(db)
    tree = _tree(
        [
            HumanMessage(content="hi", id=None),
            AIMessage(content="yo", id=None),
        ]
    )

    resolved = await resolve_turn_user_message_id(db, conv, tree=tree)
    responses = await list_messages_from_checkpointer(
        db, conv, user_id=TEST_USER_ID, tree=tree
    )
    last_user = [r for r in responses if r.role == "user"][-1]
    assert resolved == str(last_user.id)
    # Derived deterministically from (conversation_id, idx=0), not a random uuid.
    assert resolved == str(uuid.uuid5(conv.id, "0"))


@pytest.mark.asyncio
async def test_resolver_returns_none_without_user_message(db: AsyncSession) -> None:
    conv = await _seed_conversation(db)
    tree = _tree([AIMessage(content="only ai", id=str(uuid.uuid4()))])
    assert await resolve_turn_user_message_id(db, conv, tree=tree) is None


@pytest.mark.asyncio
async def test_link_attachments_only_stamps_null_rows(db: AsyncSession) -> None:
    conv = await _seed_conversation(db)
    fresh = _attachment(conv, message_id=None)
    already = _attachment(conv, message_id="earlier-turn-msg")
    db.add_all([fresh, already])
    await db.flush()

    updated = await link_attachments_to_message(
        db, attachment_ids=[fresh.id, already.id], message_id="this-turn-msg"
    )
    assert updated == 1

    rows = {
        r.id: r.message_id
        for r in (await db.execute(select(MessageAttachment))).scalars().all()
    }
    assert rows[fresh.id] == "this-turn-msg"
    # An already-linked row (e.g. a stale orphan from a failed finalize) is
    # never re-pointed at this turn — cross-send mis-link guard.
    assert rows[already.id] == "earlier-turn-msg"


@pytest.mark.asyncio
async def test_link_attachments_empty_is_noop(db: AsyncSession) -> None:
    assert await link_attachments_to_message(db, attachment_ids=[], message_id="m") == 0
