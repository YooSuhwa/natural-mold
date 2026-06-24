"""ADR-021 C2 — value-based redaction in the state-snapshot endpoint path.

The state / messages endpoints are plain HTTP GETs that run OUTSIDE any agent
run, so the run-scoped secret ContextVar is never set there. Before C2,
``load_thread_state_snapshot`` called ``redact_protocol_data`` with no
``secret_values`` and (since the ContextVar was ``None``) value-based masking
was a no-op — an opaque tool credential echoed into a persisted message
leaked through the state API verbatim.

These tests drive the real ``load_thread_state_snapshot`` (with a fake
checkpointer, mirroring the existing hydration tests) and assert that:

* passing ``secret_values`` explicitly masks the opaque secret (C2 behaviour),
* NOT passing it leaves the opaque secret intact — which is precisely the
  pre-C2 leak, and what proves the endpoint must thread the secret set in.

The opaque secret (``Zt4hWp7QmD2sLx9KbearerlessVAL``) carries no sensitive key
and matches no value heuristic, so only value-based replacement can mask it.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.user import User
from app.routers.conversation_agent_protocol_state_snapshot import (
    load_thread_state_snapshot,
    serialize_langchain_message,
)
from app.services.thread_branch_service import _CheckpointSlim
from tests.conftest import TEST_USER_ID

# Opaque, no sensitive-key prefix, not Bearer/sk-/JWT/DSN -> heuristics can't
# touch it; only value-based masking of the run's actual secret will.
OPAQUE_SECRET = "Zt4hWp7QmD2sLx9KbearerlessVAL"


class _FakeCheckpointer:
    def __init__(
        self,
        checkpoints: list[_CheckpointSlim],
        *,
        values_by_checkpoint: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._checkpoints = checkpoints
        self._values_by_checkpoint = values_by_checkpoint or {}

    async def alist(self, _config: Any) -> AsyncIterator[Any]:
        for checkpoint in self._checkpoints:
            channel_values = dict(self._values_by_checkpoint.get(checkpoint.checkpoint_id, {}))
            channel_values.setdefault("messages", checkpoint.messages)
            yield type(
                "CheckpointTuple",
                (),
                {
                    "config": {"configurable": {"checkpoint_id": checkpoint.checkpoint_id}},
                    "parent_config": (
                        {"configurable": {"checkpoint_id": checkpoint.parent_checkpoint_id}}
                        if checkpoint.parent_checkpoint_id
                        else None
                    ),
                    "checkpoint": {"channel_values": channel_values},
                },
            )()

    async def aget_tuple(self, _config: Any) -> Any:
        configurable = _config.get("configurable") if isinstance(_config, dict) else {}
        checkpoint_id = (
            configurable.get("checkpoint_id") if isinstance(configurable, dict) else None
        )
        if isinstance(checkpoint_id, str):
            for checkpoint in self._checkpoints:
                if checkpoint.checkpoint_id != checkpoint_id:
                    continue
                channel_values = dict(self._values_by_checkpoint.get(checkpoint_id, {}))
                channel_values.setdefault("messages", checkpoint.messages)
                return type(
                    "CheckpointTuple",
                    (),
                    {
                        "config": {"configurable": {"checkpoint_id": checkpoint_id}},
                        "checkpoint": {"channel_values": channel_values},
                        "pending_writes": [],
                    },
                )()
        return type("CheckpointTuple", (), {"pending_writes": []})()


async def _seed_conversation(db: AsyncSession) -> Conversation:
    user = await db.get(User, TEST_USER_ID)
    if user is None:
        user = User(id=TEST_USER_ID, email="state-redact@test.dev", name="State Redact")
        db.add(user)
    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    db.add(model)
    await db.flush()
    agent = Agent(
        user_id=user.id,
        name="State Redact Agent",
        system_prompt="You are helpful.",
        model_id=model.id,
    )
    db.add(agent)
    await db.flush()
    conversation = Conversation(agent_id=agent.id, title="State Redact Conversation")
    db.add(conversation)
    await db.commit()
    return conversation


def _leaky_checkpoint() -> _CheckpointSlim:
    # An assistant turn that echoed a tool credential in plain prose.
    return _CheckpointSlim(
        checkpoint_id="ck-leaf",
        parent_checkpoint_id=None,
        messages=[
            HumanMessage(id="user-1", content="look it up"),
            AIMessage(
                id="assistant-1",
                content=f"the API returned {OPAQUE_SECRET} for your account",
            ),
        ],
    )


@pytest.mark.asyncio
async def test_state_snapshot_masks_secret_when_passed(monkeypatch, db: AsyncSession) -> None:
    conversation = await _seed_conversation(db)
    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol_state_snapshot.get_checkpointer",
        lambda: _FakeCheckpointer([_leaky_checkpoint()]),
    )

    snapshot = await load_thread_state_snapshot(
        conversation,
        db=db,
        secret_values={OPAQUE_SECRET},
    )

    rendered = json.dumps(snapshot.values)
    assert OPAQUE_SECRET not in rendered, "opaque secret leaked through state snapshot"
    assert "<redacted>" in rendered


@pytest.mark.asyncio
async def test_state_snapshot_leaks_without_secret_values(monkeypatch, db: AsyncSession) -> None:
    """Pre-C2 behaviour: no run secrets + no ContextVar -> opaque secret leaks.

    This pins WHY the endpoint must collect and pass ``secret_values`` — the
    heuristics alone cannot mask an opaque, key-less value.
    """

    conversation = await _seed_conversation(db)
    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol_state_snapshot.get_checkpointer",
        lambda: _FakeCheckpointer([_leaky_checkpoint()]),
    )

    snapshot = await load_thread_state_snapshot(conversation, db=db)  # no secret_values

    assert OPAQUE_SECRET in json.dumps(snapshot.values), (
        "without secret_values the opaque value must survive (heuristics can't "
        "catch it) — proving value-based masking is the real defence"
    )


def test_serialize_langchain_message_masks_secret_value() -> None:
    msg = AIMessage(id="m-1", content=f"the API returned {OPAQUE_SECRET} for your account")
    payload = serialize_langchain_message(msg, secret_values={OPAQUE_SECRET})
    assert OPAQUE_SECRET not in json.dumps(payload)
    assert "<redacted>" in json.dumps(payload)

    # And without the secret set, it is left intact (heuristics can't catch it).
    leaked = serialize_langchain_message(msg)
    assert OPAQUE_SECRET in json.dumps(leaked)
