"""Tests for app.services.thread_branch_service — LangGraph branch tree."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from langchain_core.messages import AIMessage, HumanMessage

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.user import User
from app.services.thread_branch_service import (
    _build_tree_from_checkpoints,
    _CheckpointSlim,
    rewind_to_checkpoint_before_message,
)
from tests.conftest import TEST_USER_ID, TestSession

# ---------------------------------------------------------------------------
# Pure tree builder unit tests (no DB / checkpointer needed)
# ---------------------------------------------------------------------------


def _msg(role: str, mid: str, text: str) -> Any:
    cls = HumanMessage if role == "user" else AIMessage
    return cls(content=text, id=mid)


def test_build_tree_single_branch():
    """Linear thread → flat parent_id chain, no siblings."""
    msgs = [_msg("user", "u1", "hi"), _msg("ai", "a1", "hello")]
    ck = _CheckpointSlim(checkpoint_id="ck1", parent_checkpoint_id=None, messages=msgs)
    tree = _build_tree_from_checkpoints([ck])

    assert len(tree.nodes) == 2
    assert tree.nodes[0].parent_id is None
    assert tree.nodes[1].parent_id == "u1"
    assert tree.branches_by_message == {}
    assert tree.active_tip_message_id == "a1"


def test_build_tree_user_edit_creates_sibling():
    """User edits 'hi' → 'hello'; nodes show *only* the active branch but the
    sibling map carries both branches."""
    leaf_a = _CheckpointSlim(
        checkpoint_id="ckA",
        parent_checkpoint_id=None,
        messages=[_msg("user", "u1", "hi"), _msg("ai", "a1", "world")],
    )
    leaf_b = _CheckpointSlim(
        checkpoint_id="ckB",
        parent_checkpoint_id=None,
        messages=[_msg("user", "u2", "hello"), _msg("ai", "a2", "hi back")],
    )
    # alist returns DESC by id; the most recent (B) should be active
    tree = _build_tree_from_checkpoints([leaf_b, leaf_a])

    # Active branch (B) is the only one in nodes — 2 messages.
    assert len(tree.nodes) == 2
    assert tree.active_tip_message_id == "a2"
    node_ids = [getattr(n.message, "id", None) for n in tree.nodes]
    assert node_ids == ["u2", "a2"]

    # u2 is the active user message; siblings include both branches sorted
    # chronologically (oldest checkpoint first) — ckA < ckB.
    sib_u2 = tree.branches_by_message["u2"]
    assert [s.message_id for s in sib_u2] == ["u1", "u2"]
    assert sib_u2[0].checkpoint_id == "ckA"
    assert sib_u2[1].checkpoint_id == "ckB"

    # Active node should advertise its position in the sibling list so
    # frontend can render ``<2/2>`` without indexOf'ing.
    active_user_node = tree.nodes[0]
    assert active_user_node.branch_index == 1  # u2 is the second (newest) sibling
    assert active_user_node.branch_total == 2


def test_build_tree_assistant_regenerate():
    """Same user message, two assistant replies → assistants are siblings."""
    leaf_a = _CheckpointSlim(
        checkpoint_id="ckA",
        parent_checkpoint_id="ck0",
        messages=[_msg("user", "u1", "tell me a joke"), _msg("ai", "a1", "joke 1")],
    )
    leaf_b = _CheckpointSlim(
        checkpoint_id="ckB",
        parent_checkpoint_id="ck0",
        messages=[_msg("user", "u1", "tell me a joke"), _msg("ai", "a2", "joke 2")],
    )
    parent = _CheckpointSlim(
        checkpoint_id="ck0",
        parent_checkpoint_id=None,
        messages=[_msg("user", "u1", "tell me a joke")],
    )
    tree = _build_tree_from_checkpoints([leaf_b, leaf_a, parent])

    # Only the active branch (B) is in nodes — 2 messages: u1, a2.
    assert len(tree.nodes) == 2
    node_ids = [getattr(n.message, "id", None) for n in tree.nodes]
    assert node_ids == ["u1", "a2"]
    # u1 is shared — no siblings at idx 0.
    assert "u1" not in tree.branches_by_message
    # a2 has a sibling a1 (regenerated assistant turn). Sorted chronologically
    # (ckA < ckB), so a1 is first and a2 (active) is second.
    sib_a2 = tree.branches_by_message["a2"]
    assert [s.message_id for s in sib_a2] == ["a1", "a2"]
    assert sib_a2[0].checkpoint_id == "ckA"
    assert sib_a2[1].checkpoint_id == "ckB"
    # Active assistant node = position 1 of 2.
    assistant_node = tree.nodes[1]
    assert assistant_node.branch_index == 1
    assert assistant_node.branch_total == 2


def test_build_tree_three_siblings_cycle_correctly():
    """3 user-edit branches → siblings sorted oldest→newest, active = newest,
    branch_index/branch_total report ``<3/3>`` so frontend can cycle through
    all three forks (not just the latest two)."""

    leaf_a = _CheckpointSlim(
        checkpoint_id="ck001",
        parent_checkpoint_id=None,
        messages=[_msg("user", "u1", "v1"), _msg("ai", "a1", "r1")],
    )
    leaf_b = _CheckpointSlim(
        checkpoint_id="ck002",
        parent_checkpoint_id=None,
        messages=[_msg("user", "u2", "v2"), _msg("ai", "a2", "r2")],
    )
    leaf_c = _CheckpointSlim(
        checkpoint_id="ck003",
        parent_checkpoint_id=None,
        messages=[_msg("user", "u3", "v3"), _msg("ai", "a3", "r3")],
    )
    # alist DESC: newest leaf (C) first.
    tree = _build_tree_from_checkpoints([leaf_c, leaf_b, leaf_a])

    # Active branch is the newest (C).
    node_ids = [getattr(n.message, "id", None) for n in tree.nodes]
    assert node_ids == ["u3", "a3"]

    # All three siblings present at the user position, sorted oldest → newest.
    sib_u3 = tree.branches_by_message["u3"]
    assert [s.message_id for s in sib_u3] == ["u1", "u2", "u3"]
    assert [s.checkpoint_id for s in sib_u3] == ["ck001", "ck002", "ck003"]

    # Active = u3 → position 2 of 3.
    assert tree.nodes[0].branch_index == 2
    assert tree.nodes[0].branch_total == 3


def test_build_tree_empty():
    tree = _build_tree_from_checkpoints([])
    assert tree.nodes == []
    assert tree.active_tip_message_id is None


def test_build_tree_honors_active_checkpoint_id():
    """When ``active_checkpoint_id`` points at the older leaf we should render
    that branch's chain instead of the newest leaf's."""
    leaf_a = _CheckpointSlim(
        checkpoint_id="ckA",
        parent_checkpoint_id=None,
        messages=[_msg("user", "u1", "hi"), _msg("ai", "a1", "world")],
    )
    leaf_b = _CheckpointSlim(
        checkpoint_id="ckB",
        parent_checkpoint_id=None,
        messages=[_msg("user", "u2", "hello"), _msg("ai", "a2", "hi back")],
    )
    # alist DESC — without the override B would be active.
    tree = _build_tree_from_checkpoints([leaf_b, leaf_a], active_checkpoint_id="ckA")

    assert tree.active_checkpoint_id == "ckA"
    node_ids = [getattr(n.message, "id", None) for n in tree.nodes]
    assert node_ids == ["u1", "a1"]
    # Sorted chronologically: ckA < ckB → u1 first, u2 second. Active is u1.
    sib_u1 = tree.branches_by_message["u1"]
    assert [s.message_id for s in sib_u1] == ["u1", "u2"]
    # Active u1 is the OLDEST sibling — branch_index 0/2.
    assert tree.nodes[0].branch_index == 0
    assert tree.nodes[0].branch_total == 2


# ---------------------------------------------------------------------------
# rewind_to_checkpoint_before_message
# ---------------------------------------------------------------------------


class _FakeCheckpointer:
    def __init__(self, checkpoints: list[_CheckpointSlim]):
        self._cks = checkpoints

    async def alist(self, _config: Any) -> AsyncIterator[Any]:
        for ck in self._cks:
            yield type(
                "CT",
                (),
                {
                    "config": {"configurable": {"checkpoint_id": ck.checkpoint_id}},
                    "parent_config": (
                        {"configurable": {"checkpoint_id": ck.parent_checkpoint_id}}
                        if ck.parent_checkpoint_id
                        else None
                    ),
                    "checkpoint": {"channel_values": {"messages": ck.messages}},
                },
            )()


@pytest.mark.asyncio
async def test_rewind_finds_parent_checkpoint():
    """rewind('a1') → parent of the checkpoint that introduced a1."""
    parent = _CheckpointSlim(
        checkpoint_id="ck0",
        parent_checkpoint_id=None,
        messages=[_msg("user", "u1", "hi")],
    )
    leaf = _CheckpointSlim(
        checkpoint_id="ck1",
        parent_checkpoint_id="ck0",
        messages=[_msg("user", "u1", "hi"), _msg("ai", "a1", "hello")],
    )
    cp = _FakeCheckpointer([leaf, parent])
    cid = await rewind_to_checkpoint_before_message(cp, "thread1", "a1")
    # The parent of ck1 (which introduced a1) is ck0 — that's where we fork.
    assert cid == "ck0"


@pytest.mark.asyncio
async def test_rewind_first_message_returns_none():
    """Rewinding to the very first message → None (start from empty)."""
    leaf = _CheckpointSlim(
        checkpoint_id="ck1",
        parent_checkpoint_id=None,
        messages=[_msg("user", "u1", "hi")],
    )
    cp = _FakeCheckpointer([leaf])
    cid = await rewind_to_checkpoint_before_message(cp, "thread1", "u1")
    assert cid is None


# ---------------------------------------------------------------------------
# Router smoke tests — edit / regenerate / switch-branch
# ---------------------------------------------------------------------------


async def _seed_agent_and_conv() -> tuple[uuid.UUID, uuid.UUID]:
    async with TestSession() as db:
        user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
        db.add(user)
        model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
        db.add(model)
        await db.flush()
        agent = Agent(
            user_id=user.id,
            name="Branch Agent",
            system_prompt="You are helpful.",
            model_id=model.id,
        )
        db.add(agent)
        await db.flush()
        conv = Conversation(agent_id=agent.id, title="Branch Conv")
        db.add(conv)
        await db.commit()
        return agent.id, conv.id


@pytest.mark.asyncio
async def test_switch_branch_persists_checkpoint(client: AsyncClient):
    _agent_id, conv_id = await _seed_agent_and_conv()

    resp = await client.post(
        f"/api/conversations/{conv_id}/messages/switch-branch",
        json={"checkpoint_id": "ck-xyz"},
    )
    assert resp.status_code == 204

    async with TestSession() as db:
        from sqlalchemy import select

        result = await db.execute(
            select(Conversation).where(Conversation.id == conv_id)
        )
        row = result.scalar_one()
        assert row.active_branch_checkpoint_id == "ck-xyz"


@pytest.mark.asyncio
async def test_edit_message_streams_with_checkpoint_fork(client: AsyncClient):
    _agent_id, conv_id = await _seed_agent_and_conv()

    captured: list = []

    async def mock_stream(cfg, messages_history, **_kw):
        captured.append((cfg, messages_history))
        yield 'event: message_end\ndata: {"content": "edited reply", "usage": {}}\n\n'

    # Simulate a single existing user message in the thread.
    msg_id = "u-original"
    leaf = _CheckpointSlim(
        checkpoint_id="ck1",
        parent_checkpoint_id=None,
        messages=[_msg("user", msg_id, "original")],
    )
    fake_cp = _FakeCheckpointer([leaf])

    # Map the langchain raw id → uuid the API exposes
    from app.agent_runtime.message_utils import parse_msg_id

    exposed = parse_msg_id(msg_id, conv_id, 0)

    with (
        patch(
            "app.agent_runtime.checkpointer.get_checkpointer",
            return_value=fake_cp,
        ),
        patch("app.routers.conversations.execute_agent_stream", side_effect=mock_stream),
    ):
        resp = await client.post(
            f"/api/conversations/{conv_id}/messages/edit",
            json={"message_id": str(exposed), "new_content": "edited"},
        )

    assert resp.status_code == 200
    assert "edited reply" in resp.text
    cfg, history = captured[0]
    # Editing the first user message → rewind to before it = None checkpoint.
    assert cfg.checkpoint_id is None
    # langgraph 1.2 DeltaChannel — fork-edit must Overwrite the messages
    # channel so ancestor pending_writes (the original "original" message)
    # don't get replayed onto the new branch. History before idx=0 is empty,
    # so the Overwrite value is just the new HumanMessage.
    from langchain_core.messages import HumanMessage
    from langgraph.types import Overwrite

    assert isinstance(history, dict)
    ow = history.get("messages")
    assert isinstance(ow, Overwrite)
    assert len(ow.value) == 1
    assert isinstance(ow.value[0], HumanMessage)
    assert ow.value[0].content == "edited"


@pytest.mark.asyncio
async def test_regenerate_does_not_duplicate_user_message(client: AsyncClient):
    """B5 regression — regenerate must rewind to the user-only checkpoint and
    invoke the executor with an EMPTY message history. Passing the user content
    again would cause LangGraph to append a second copy of the user turn,
    leading to "[user, user, ai_new]" on screen."""

    _agent_id, conv_id = await _seed_agent_and_conv()

    captured: list = []

    async def mock_stream(cfg, messages_history, **_kw):
        captured.append((cfg, messages_history))
        yield 'event: message_end\ndata: {"content": "regen reply", "usage": {}}\n\n'

    # Two-step thread: parent ck0 has only the user; leaf ck1 added the
    # assistant. Regenerating the assistant should fork from ck0 (state has
    # user, no assistant) and the executor must NOT be given the user content
    # as new input.
    parent = _CheckpointSlim(
        checkpoint_id="ck0",
        parent_checkpoint_id=None,
        messages=[_msg("user", "u1", "정말 슬펐어")],
    )
    leaf = _CheckpointSlim(
        checkpoint_id="ck1",
        parent_checkpoint_id="ck0",
        messages=[_msg("user", "u1", "정말 슬펐어"), _msg("ai", "a1", "응답1")],
    )
    fake_cp = _FakeCheckpointer([leaf, parent])

    with (
        patch(
            "app.agent_runtime.checkpointer.get_checkpointer",
            return_value=fake_cp,
        ),
        patch(
            "app.routers.conversations.execute_agent_stream",
            side_effect=mock_stream,
        ),
    ):
        resp = await client.post(
            f"/api/conversations/{conv_id}/messages/regenerate",
            json={},
        )

    assert resp.status_code == 200
    assert "regen reply" in resp.text
    cfg, history = captured[0]
    # Forked from ck0 (state holds only the user message — no assistant).
    assert cfg.checkpoint_id == "ck0"
    # The executor must receive an empty history so the user turn isn't
    # appended a second time when LangGraph resumes the graph.
    assert history == []


@pytest.mark.asyncio
async def test_regenerate_targeted_assistant_uses_correct_checkpoint(
    client: AsyncClient,
):
    """When ``message_id`` points at a specific assistant in a multi-turn
    thread, rewind must land on the checkpoint whose state ends with the
    parent user turn for that assistant — not the head of the thread."""

    _agent_id, conv_id = await _seed_agent_and_conv()

    captured: list = []

    async def mock_stream(cfg, messages_history, **_kw):
        captured.append((cfg, messages_history))
        yield 'event: message_end\ndata: {"content": "regen", "usage": {}}\n\n'

    ck0 = _CheckpointSlim(
        checkpoint_id="ck0",
        parent_checkpoint_id=None,
        messages=[_msg("user", "u1", "안녕?")],
    )
    ck1 = _CheckpointSlim(
        checkpoint_id="ck1",
        parent_checkpoint_id="ck0",
        messages=[_msg("user", "u1", "안녕?"), _msg("ai", "a1", "안녕!")],
    )
    ck2 = _CheckpointSlim(
        checkpoint_id="ck2",
        parent_checkpoint_id="ck1",
        messages=[
            _msg("user", "u1", "안녕?"),
            _msg("ai", "a1", "안녕!"),
            _msg("user", "u2", "정말 슬펐어"),
        ],
    )
    leaf = _CheckpointSlim(
        checkpoint_id="ck3",
        parent_checkpoint_id="ck2",
        messages=[
            _msg("user", "u1", "안녕?"),
            _msg("ai", "a1", "안녕!"),
            _msg("user", "u2", "정말 슬펐어"),
            _msg("ai", "a2", "응답1"),
        ],
    )
    fake_cp = _FakeCheckpointer([leaf, ck2, ck1, ck0])

    from app.agent_runtime.message_utils import parse_msg_id

    a2_exposed = parse_msg_id("a2", conv_id, 3)

    with (
        patch(
            "app.agent_runtime.checkpointer.get_checkpointer",
            return_value=fake_cp,
        ),
        patch(
            "app.routers.conversations.execute_agent_stream",
            side_effect=mock_stream,
        ),
    ):
        resp = await client.post(
            f"/api/conversations/{conv_id}/messages/regenerate",
            json={"message_id": str(a2_exposed)},
        )

    assert resp.status_code == 200
    cfg, history = captured[0]
    # ck2 holds [u1, a1, u2] — exactly the state needed to fork a sibling
    # of a2 without duplicating u2.
    assert cfg.checkpoint_id == "ck2"
    assert history == []


@pytest.mark.asyncio
async def test_list_messages_envelope_shape(client: AsyncClient):
    """GET /messages now returns an envelope with messages + tip metadata."""
    _agent_id, conv_id = await _seed_agent_and_conv()

    leaf = _CheckpointSlim(
        checkpoint_id="ck1",
        parent_checkpoint_id=None,
        messages=[_msg("user", "u1", "Hi"), _msg("ai", "a1", "Hello")],
    )
    fake_cp = _FakeCheckpointer([leaf])
    fake_cp.aget_tuple = AsyncMock(  # type: ignore[attr-defined]
        return_value=type(
            "CT", (), {"checkpoint": {"channel_values": {"messages": leaf.messages}}}
        )()
    )

    with patch(
        "app.agent_runtime.checkpointer.get_checkpointer",
        return_value=fake_cp,
    ):
        resp = await client.get(f"/api/conversations/{conv_id}/messages")

    assert resp.status_code == 200
    body = resp.json()
    assert "messages" in body
    assert len(body["messages"]) == 2
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][1]["role"] == "assistant"
    assert body["messages"][1]["parent_id"] is not None
    assert body["active_checkpoint_id"] == "ck1"
