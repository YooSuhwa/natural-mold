from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Overwrite
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from app.agent_runtime.run_secrets import reset_run_secrets, set_run_secrets
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.model import Model
from app.models.user import User
from app.routers.conversation_agent_protocol_replay import (
    load_protocol_events,
    protocol_replay_generator,
)
from app.services import trace_storage
from app.services.conversation_stream_service import build_persist_callback
from tests.agent_runtime.langgraph_streaming_fixtures import ProtocolAgent
from tests.agent_runtime.runtime_contract_helpers import (
    assert_contract_matches,
    contract_diff_paths,
    load_contract_fixture,
)
from tests.conftest import TEST_USER_ID

_CONVERSATION_ID = uuid.UUID("10000000-0000-0000-0000-000000000008")
_RUN_ID = "40000000-0000-0000-0000-000000000008"
_SECRET = "BranchReplayOpaqueSecretValue42"
_INTERNAL_PATH = "/private/.moldy-internal/offload/spill/branch-replay-secret"
_PRIVATE_MEMORY = "only the user may see this memory"


class _DeltaCheckpointer:
    def __init__(self) -> None:
        self.messages = {
            "ck-before-edit": [
                HumanMessage(content="original question", id="user-original"),
                AIMessage(content="original answer", id="assistant-original"),
            ],
            "ck-regenerate-leaf": [
                HumanMessage(content="regenerate question", id="user-regenerate"),
                AIMessage(content="answer to replace", id="assistant-replace"),
            ],
        }

    async def aget_delta_channel_history(
        self, *, config: dict[str, Any], channels: list[str]
    ) -> dict[str, Any]:
        assert channels == ["messages"]
        checkpoint_id = config["configurable"]["checkpoint_id"]
        return {"messages": {"seed": self.messages[checkpoint_id], "writes": []}}


async def _seed_conversation(db: AsyncSession) -> Conversation:
    user = await db.get(User, TEST_USER_ID)
    if user is None:
        db.add(User(id=TEST_USER_ID, email="branch-replay@test.dev", name="Branch Replay"))
    db.add(
        Model(
            provider="openai",
            model_name="gpt-4o",
            display_name="Contract Model",
        )
    )
    await db.flush()
    model = (await db.execute(select(Model))).scalars().one()
    db.add(
        Agent(
            user_id=TEST_USER_ID,
            name="Branch Contract Agent",
            system_prompt="Keep the current contract.",
            model_id=model.id,
        )
    )
    await db.flush()
    agent = (await db.execute(select(Agent))).scalars().one()
    conversation = Conversation(
        id=_CONVERSATION_ID,
        agent_id=agent.id,
        title="Branch Replay Contract",
    )
    db.add(conversation)
    await db.commit()
    return conversation


def _command(checkpoint_id: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "id": f"command-{checkpoint_id}",
        "method": "run.start",
        "params": {
            "checkpoint": {"checkpoint_id": checkpoint_id},
            "input": {"messages": messages},
        },
    }


async def _collect_branch_manifest(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    conversation = await _seed_conversation(db)
    started: list[dict[str, Any]] = []

    async def capture_start(**kwargs: Any) -> None:
        started.append(kwargs)

    monkeypatch.setattr(
        "app.agent_runtime.checkpointer.get_checkpointer",
        lambda: _DeltaCheckpointer(),
    )
    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol.start_conversation_run",
        capture_start,
    )
    url = f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/commands"
    edit_response = await client.post(
        url,
        json=_command(
            "ck-before-edit",
            [{"role": "user", "content": "edited question", "id": "user-edited"}],
        ),
    )
    assert edit_response.status_code == 200
    edit_run = await db.get(
        ConversationRun,
        uuid.UUID(edit_response.json()["result"]["run_id"]),
    )
    assert edit_run is not None
    edit_input = started[-1]["input_payload"]["messages"]
    assert isinstance(edit_input, Overwrite)
    edit_manifest = {
        "checkpoint_id": started[-1]["cfg"].checkpoint_id,
        "messages": [message.content for message in edit_input.value],
        "moldy_source": started[-1]["moldy_source"],
        "persisted_source": edit_run.source,
    }
    edit_run.status = "completed"
    edit_run.is_active = False
    await db.commit()

    regenerate_response = await client.post(
        url,
        json=_command("ck-regenerate-leaf", []),
    )
    assert regenerate_response.status_code == 200
    regenerate_run = await db.get(
        ConversationRun,
        uuid.UUID(regenerate_response.json()["result"]["run_id"]),
    )
    assert regenerate_run is not None
    regenerate_input = started[-1]["input_payload"]["messages"]
    assert isinstance(regenerate_input, Overwrite)
    return {
        "edit": edit_manifest,
        "regenerate": {
            "checkpoint_id": started[-1]["cfg"].checkpoint_id,
            "messages": [message.content for message in regenerate_input.value],
            "moldy_source": started[-1]["moldy_source"],
            "persisted_source": regenerate_run.source,
        },
    }


def _redaction_event() -> dict[str, Any]:
    return {
        "type": "event",
        "method": "values",
        "params": {
            "namespace": [],
            "data": {
                "public_label": "stable-public-value",
                "secret_echo": f"tool returned {_SECRET}",
                "offload": {
                    "offload_path": _INTERNAL_PATH,
                    "public_tag": "stable-pointer-tag",
                },
            },
        },
        "seq": 1,
        "event_id": "raw-redaction-values-1",
    }


async def _collect_replay_manifest(db: AsyncSession) -> dict[str, Any]:
    persist = build_persist_callback(_CONVERSATION_ID, _RUN_ID)
    token = set_run_secrets({_SECRET})
    try:
        _ = [
            chunk
            async for chunk in stream_agent_response_langgraph(
                ProtocolAgent([_redaction_event()]),
                [{"role": "user", "content": "fixed contract input"}],
                {"configurable": {"thread_id": str(_CONVERSATION_ID)}},
                persist_callback=persist,
                run_id=_RUN_ID,
                recalled_memories=[{"id": "memory-public-1", "content": _PRIVATE_MEMORY}],
            )
        ]
    finally:
        reset_run_secrets(token)

    record = (
        await db.execute(
            select(trace_storage.MessageEvent).where(
                trace_storage.MessageEvent.assistant_msg_id == _RUN_ID
            )
        )
    ).scalar_one()
    persisted = await trace_storage.load_events(db, record)
    replay_events = await load_protocol_events(db, _CONVERSATION_ID, secret_values=(_SECRET,))
    tail_chunks = [
        chunk
        async for chunk in protocol_replay_generator(
            replay_events,
            {},
            after_id=replay_events[0]["id"],
        )
    ]
    persisted_blob = json.dumps(persisted, ensure_ascii=False)
    replay_blob = "".join(tail_chunks)
    for private_value in (_SECRET, _INTERNAL_PATH, _PRIVATE_MEMORY):
        assert private_value not in persisted_blob
        assert private_value not in replay_blob
    values = next(event for event in replay_events if event["method"] == "values")
    memory = next(
        event
        for event in replay_events
        if event["method"] == "custom" and event["data"].get("name") == "moldy.memory_recalled"
    )
    return {
        "persisted_methods": [event["method"] for event in persisted],
        "replay_event_ids": [event["id"] for event in replay_events],
        "tail_event_ids": [
            line.removeprefix("id: ")
            for chunk in tail_chunks
            for line in chunk.splitlines()
            if line.startswith("id: ")
        ],
        "public_label": values["data"]["public_label"],
        "public_tag": values["data"]["offload"]["public_tag"],
        "offload_redacted": values["data"]["offload"]["internal_reference_redacted"],
        "secret_mask": values["data"]["secret_echo"],
        "memory_id": memory["data"]["payload"]["memories"][0]["id"],
        "memory_content": memory["data"]["payload"]["memories"][0]["content"],
    }


@pytest.mark.asyncio
async def test_runtime_branch_replay_contract_matches_checked_fixture(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = await _collect_branch_manifest(client, db, monkeypatch)
    replay = await _collect_replay_manifest(db)

    assert_contract_matches("runtime_branch_replay_v1.json", {**branch, "replay": replay})


def test_runtime_branch_replay_contract_reports_source_mutation_path() -> None:
    expected = load_contract_fixture("runtime_branch_replay_v1.json")
    regenerate = expected["regenerate"]
    assert isinstance(regenerate, dict)
    mutated = {**expected, "regenerate": {**regenerate, "persisted_source": "edit"}}

    assert contract_diff_paths(expected, mutated) == ["$.regenerate.persisted_source"]
