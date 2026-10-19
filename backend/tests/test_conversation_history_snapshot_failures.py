from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from app.credentials.external_secrets import proxy as external_secrets_proxy
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_history import (
    ConversationHistoryCheckpoint,
    ConversationHistoryEvent,
    ConversationHistoryManifest,
)
from app.models.message_event import MessageEvent
from app.services.conversation_history_secrets import HistorySecretSourceError
from app.services.conversation_history_snapshot import (
    HistorySnapshotIncompleteError,
    materialize_conversation_history,
)
from tests.conftest import TestSession
from tests.conversation_history_snapshot_fixture import (
    AGENT_ID,
    SnapshotCase,
    authorize_snapshot_agent,
    collect_fixture_secret,
    fail_secret_collection,
    materialization_request,
    seed_external_secret_source,
    snapshot_case,
)


@pytest.mark.asyncio
async def test_secret_resolution_failure_leaves_history_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = await snapshot_case()
    monkeypatch.setattr(
        "app.services.conversation_history_snapshot.authorize_agent_lifecycle",
        authorize_snapshot_agent,
    )
    monkeypatch.setattr(
        "app.services.conversation_history_snapshot.collect_strict_history_secrets",
        fail_secret_collection,
    )
    async with TestSession() as db:
        with pytest.raises(HistorySnapshotIncompleteError) as error:
            await materialize_conversation_history(materialization_request(db, case))
        assert error.value.code == "AGENT_HISTORY_SNAPSHOT_INCOMPLETE"
        assert list(await db.scalars(select(ConversationHistoryCheckpoint))) == []


@pytest.mark.asyncio
async def test_external_secret_io_failure_is_typed_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = await snapshot_case()
    async with TestSession() as db:
        source_event = await seed_external_secret_source(db, case.conversation.id)
        monkeypatch.setattr(
            "app.services.conversation_history_snapshot.authorize_agent_lifecycle",
            authorize_snapshot_agent,
        )

        async def provider_io_failure(_provider_name: str, _secret_name: str) -> str | None:
            raise OSError("external-provider-io-failure")

        monkeypatch.setattr(external_secrets_proxy, "get", provider_io_failure)
        with pytest.raises(HistorySnapshotIncompleteError) as error:
            await materialize_conversation_history(materialization_request(db, case))

        async def unresolved_external(_provider_name: str, _secret_name: str) -> str | None:
            return None

        monkeypatch.setattr(external_secrets_proxy, "get", unresolved_external)
        with pytest.raises(HistorySnapshotIncompleteError) as unresolved_error:
            await materialize_conversation_history(materialization_request(db, case))

        async def cancelled_external(_provider_name: str, _secret_name: str) -> str | None:
            raise asyncio.CancelledError

        monkeypatch.setattr(external_secrets_proxy, "get", cancelled_external)
        with pytest.raises(asyncio.CancelledError):
            await materialize_conversation_history(materialization_request(db, case))

        persisted_agent = await db.get(Agent, AGENT_ID)
        persisted_conversation = await db.get(Conversation, case.conversation.id)
        persisted_event = await db.get(MessageEvent, source_event.id)
        assert error.value.code == "AGENT_HISTORY_SNAPSHOT_INCOMPLETE"
        assert isinstance(error.value.__cause__, HistorySecretSourceError)
        assert unresolved_error.value.code == "AGENT_HISTORY_SNAPSHOT_INCOMPLETE"
        assert isinstance(unresolved_error.value.__cause__, HistorySecretSourceError)
        assert list(await db.scalars(select(ConversationHistoryManifest))) == []
        assert list(await db.scalars(select(ConversationHistoryCheckpoint))) == []
        assert list(await db.scalars(select(ConversationHistoryEvent))) == []
        assert persisted_agent is not None
        assert (persisted_agent.name, persisted_agent.status, persisted_agent.archived_at) == (
            "Snapshot Agent",
            "active",
            None,
        )
        assert persisted_conversation is not None
        assert persisted_conversation.agent_lifecycle_state == "active"
        assert persisted_event is not None
        assert persisted_event.events == source_event.events
        assert len([item async for item in case.saver.alist(None)]) == 2


@pytest.mark.asyncio
async def test_missing_checkpoint_aborts_without_retained_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = await snapshot_case()

    async def missing_tuple(_config):
        return None

    monkeypatch.setattr(case.saver, "aget_tuple", missing_tuple)
    monkeypatch.setattr(
        "app.services.conversation_history_snapshot.authorize_agent_lifecycle",
        authorize_snapshot_agent,
    )
    monkeypatch.setattr(
        "app.services.conversation_history_snapshot.collect_strict_history_secrets",
        collect_fixture_secret,
    )
    async with TestSession() as db:
        with pytest.raises(HistorySnapshotIncompleteError) as error:
            await materialize_conversation_history(materialization_request(db, case))
        assert error.value.code == "AGENT_HISTORY_SNAPSHOT_INCOMPLETE"
        assert list(await db.scalars(select(ConversationHistoryCheckpoint))) == []


@pytest.mark.asyncio
async def test_unsupported_value_aborts_without_retained_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = await snapshot_case()
    saver = InMemorySaver()
    checkpoint = empty_checkpoint()
    checkpoint["id"] = "checkpoint-opaque"
    checkpoint["channel_values"] = {
        "messages": [HumanMessage(id="message-user", content="safe")],
        "opaque": b"not-json",
    }
    checkpoint["channel_versions"] = {"messages": "1", "opaque": "1"}
    await saver.aput(
        {"configurable": {"thread_id": str(base.conversation.id), "checkpoint_ns": ""}},
        checkpoint,
        {"source": "input", "step": 0},
        {"messages": "1", "opaque": "1"},
    )
    case = SnapshotCase(base.conversation, saver)
    monkeypatch.setattr(
        "app.services.conversation_history_snapshot.authorize_agent_lifecycle",
        authorize_snapshot_agent,
    )
    monkeypatch.setattr(
        "app.services.conversation_history_snapshot.collect_strict_history_secrets",
        collect_fixture_secret,
    )
    async with TestSession() as db:
        with pytest.raises(HistorySnapshotIncompleteError) as error:
            await materialize_conversation_history(materialization_request(db, case))
        assert error.value.code == "AGENT_HISTORY_SNAPSHOT_INCOMPLETE"
        assert list(await db.scalars(select(ConversationHistoryCheckpoint))) == []
