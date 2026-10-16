"""Task 10 I4 extraction contracts: facades re-export moved nodes and stay patchable."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.services import (
    agent_execution_acceptance,
    agent_execution_legacy_queries,
    agent_execution_owner,
    conversation_agent_context,
    conversation_run_creation,
    conversation_run_creation_persistence,
    conversation_run_delivery,
    conversation_run_execution,
    conversation_run_service,
    conversation_run_task_registry,
    conversation_run_transitions,
    conversation_run_worker,
    conversation_stream_persistence,
    conversation_stream_protocol,
    conversation_stream_service,
)
from app.services.chat import chat_execution_preparation, conversation_pages, conversations
from app.services.conversation_run_envelope_projection import active_unlinked_envelopes
from app.services.conversation_run_ownership_cancellation import participant_run_conflicts

FACADE_EXPORTS = [
    (conversation_run_worker, conversation_run_task_registry, "RunTaskRegistry"),
    (conversation_run_worker, conversation_run_transitions, "_transition"),
    (conversation_run_worker, conversation_run_transitions, "_heartbeat_until_terminal"),
    (conversation_run_worker, conversation_run_delivery, "_activate_latest_branch_leaf_if_needed"),
    (conversation_run_worker, conversation_run_delivery, "_backfill_turn_attachments"),
    (conversation_run_worker, conversation_run_execution, "_run_conversation"),
    (conversation_stream_service, conversation_agent_context, "resolve_agent_context"),
    (conversation_stream_service, conversation_stream_protocol, "StreamCtx"),
    (conversation_stream_service, conversation_stream_protocol, "sse_handler"),
    (conversation_stream_service, conversation_stream_persistence, "finalize_trace"),
    (conversation_stream_service, conversation_stream_persistence, "build_persist_callback"),
    (conversation_run_service, conversation_run_creation, "create_run"),
    (conversation_run_service, conversation_run_creation, "transition_run"),
    (conversation_run_service, conversation_run_creation, "ALLOWED_TRANSITIONS"),
    (conversation_run_service, conversation_run_creation_persistence, "mark_stale_active_runs"),
    (
        conversation_run_service,
        conversation_run_creation_persistence,
        "sweep_stale_conversation_runs",
    ),
    (agent_execution_owner, agent_execution_acceptance, "accept_owned_execution"),
    (agent_execution_owner, agent_execution_acceptance, "OwnedExecutionRequest"),
    (agent_execution_owner, agent_execution_legacy_queries, "active_unlinked_envelopes"),
    (agent_execution_owner, agent_execution_legacy_queries, "participant_run_conflicts"),
    (conversations, chat_execution_preparation, "prepare_executable_conversation"),
    (conversations, chat_execution_preparation, "require_executable_conversation"),
    (conversations, chat_execution_preparation, "create_conversation"),
    (conversations, conversation_pages, "list_global_conversations_page"),
]


@pytest.mark.parametrize(("facade", "owner", "name"), FACADE_EXPORTS)
def test_facade_reexports_moved_node_by_identity(facade: Any, owner: Any, name: str) -> None:
    assert getattr(facade, name) is getattr(owner, name)


def test_legacy_lifecycle_queries_keep_original_query_objects() -> None:
    assert agent_execution_owner.active_unlinked_envelopes is active_unlinked_envelopes
    assert agent_execution_owner.participant_run_conflicts is participant_run_conflicts


def test_original_public_all_lists_are_preserved() -> None:
    assert set(conversation_run_worker.__all__) >= {
        "RunTaskRegistry",
        "RunTaskRegistrationError",
        "RunTaskAlreadyRegisteredError",
        "get_run_task_registry",
        "reset_run_task_registry_for_tests",
        "start_conversation_run",
    }
    assert set(conversation_stream_service.__all__) >= {
        "async_session",
        "execute_agent_stream",
        "resume_agent_stream",
        "resolve_llm_api_key_for_agent",
    }
    assert "create_run" in conversation_run_service.__all__
    assert "accept_owned_execution" in agent_execution_owner.__all__


async def test_registry_shutdown_observes_late_facade_patch(monkeypatch) -> None:
    calls: list[str] = []

    async def first(run_ids: list[uuid.UUID]) -> None:
        calls.append("first")

    async def second(run_ids: list[uuid.UUID]) -> None:
        calls.append("second")

    registry = conversation_run_worker.RunTaskRegistry(worker_instance_id="i4-extraction")
    monkeypatch.setattr(conversation_run_worker, "_persist_shutdown_intent", first)
    await registry.shutdown()
    monkeypatch.setattr(conversation_run_worker, "_persist_shutdown_intent", second)
    await registry.shutdown()

    assert calls == ["first", "second"]


class _NoConversationSession:
    def __init__(self, label: str, opened: list[str]) -> None:
        self.label = label
        self.opened = opened

    async def __aenter__(self) -> _NoConversationSession:
        self.opened.append(self.label)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def get(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def commit(self) -> None:
        return None


async def test_moved_worker_and_stream_code_observe_late_session_patch(monkeypatch) -> None:
    opened: list[str] = []
    run_id = uuid.uuid4()
    for label in ("first", "second"):
        session = _NoConversationSession(label, opened)
        monkeypatch.setattr(conversation_run_worker, "async_session", lambda s=session: s)
        await conversation_run_transitions._persist_shutdown_intent([run_id])
        monkeypatch.setattr(conversation_stream_service, "async_session", lambda s=session: s)
        await conversation_stream_persistence.finalize_trace(
            uuid.uuid4(), str(run_id), [], [], [], success=True
        )

    assert opened == ["first", "first", "second", "second"]


async def test_stream_ctx_finalize_callback_observes_late_facade_patch(monkeypatch) -> None:
    calls: list[str] = []
    ctx = conversation_stream_service.StreamCtx(
        run_id="run-i4",
        broker=SimpleNamespace(),  # type: ignore[arg-type]
        persist_cb=None,  # type: ignore[arg-type]
        trace_sink=[],
        msg_id_sink=[],
        error_sink=[],
        langfuse_sink=[],
    )
    callback = ctx.finalize_callback(uuid.uuid4())
    for label in ("first", "second"):

        async def fake_finalize(*_args: object, _label: str = label, **_kwargs: object) -> None:
            calls.append(_label)

        monkeypatch.setattr(conversation_stream_service, "finalize_trace", fake_finalize)
        await callback(True)

    assert calls == ["first", "second"]


async def test_create_run_observes_late_facade_active_run_patch(monkeypatch) -> None:
    from app.models.conversation import Conversation

    conversation_id, agent_id = uuid.uuid4(), uuid.uuid4()
    held = Conversation(id=conversation_id, agent_id=agent_id)
    proof = SimpleNamespace(
        require_live=lambda _db: None,
        require_scope=lambda _scope: None,
        covers_conversation=lambda _cid: True,
        cursor=SimpleNamespace(held_row=lambda _cls, _cid: held),
    )

    async def ensure_policy(_db: object, _proof: object, _target: object) -> tuple:
        return None, None, object()

    monkeypatch.setattr(conversation_run_creation, "live_proof", lambda _db: proof)
    monkeypatch.setattr(
        conversation_run_creation,
        "ensure_conversation_runtime_policy_from_locked_proof",
        ensure_policy,
    )
    monkeypatch.setattr(conversation_run_creation, "execution_scope", lambda _ctx: None)
    seen: list[str] = []
    for label in ("first", "second"):

        async def active_run(_db: object, *, _label: str = label, **_kwargs: object) -> object:
            seen.append(_label)
            return object()

        monkeypatch.setattr(conversation_run_service, "get_active_run", active_run)
        with pytest.raises(HTTPException) as raised:
            await conversation_run_service.create_run(
                SimpleNamespace(),  # type: ignore[arg-type]
                conversation_id=conversation_id,
                agent_id=agent_id,
                context=SimpleNamespace(),  # type: ignore[arg-type]
                source="chat",
                input_preview=None,
            )
        assert raised.value.status_code == 409

    assert seen == ["first", "second"]
