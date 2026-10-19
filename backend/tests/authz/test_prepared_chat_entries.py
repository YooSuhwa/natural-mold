"""Chat/edit/regenerate/resume/protocol entries execute through the prepared lease (I16).

Each case drives the exact executor + input shape its entry hands to
``start_conversation_run`` through the real owned worker with the lease flag
on: the config is re-resolved once under the canonical locks, the marker pair
is committed before the first (permit-guarded) provider send, ``_prepare_agent``
is never called and nothing is hydrated after the marker.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import checkpointer
from app.agent_runtime.agent_stream_runner import execute_agent_stream, resume_agent_stream
from app.agent_runtime.langgraph_agent_stream_runner import (
    execute_agent_stream_langgraph,
    resume_agent_stream_langgraph,
)
from app.agent_runtime.prepared_run_registry import get_prepared_run_registry
from app.exceptions import AppError
from app.models.audit_event import AuditEvent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.message_event import MessageEventChunk
from app.models.organization import Organization
from app.services import prepared_conversation_start
from app.services.chat.chat_execution_preparation import create_conversation
from app.services.conversation_branch_service import with_regeneration_guidance
from app.services.tenants import organization_archive_enumeration as enumeration
from app.services.tenants.organization_archive_enumeration import ArchiveScope
from app.services.thread_branch_service import build_fork_overwrite_input
from tests.authz import prepared_entry_testkit as ekit
from tests.authz import prepared_materialization_testkit as kit
from tests.authz.test_organization_archive_concurrency import _case, _context, _count, _lifecycle
from tests.conftest import TestSession

pytest_plugins = [
    "tests.authz.tenant_organizations_fixture",
    "tests.authz.tenant_organization_lifecycle_app",
]


def _assert_prepared(harness: ekit.Harness, row: ConversationRun, *, sends: int) -> None:
    assert not prepared_conversation_start.FALLBACKS, prepared_conversation_start.FALLBACKS
    assert row.status == "completed", (row.status, row.error_code, row.error_message)
    assert row.runtime_materialized_at is not None
    assert row.runtime_materialization_sha256 == harness.leases[-1].digest
    assert harness.resolutions[-1] == (False, True)
    assert all(after is False for after, _ in harness.resolutions)
    assert harness.prepare_agent_calls == 0
    assert kit.SENDS.count(harness.seeded.root_model) == sends
    assert get_prepared_run_registry().state(row.id) is None


def _human_texts(index: int = -1) -> list[str]:
    return [str(m.content) for m in ekit.SEEN[index] if isinstance(m, HumanMessage)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("executor", "payload"),
    [
        (execute_agent_stream, [{"role": "user", "content": "hi"}]),
        (execute_agent_stream_langgraph, {"messages": [{"role": "user", "content": "hi"}]}),
    ],
    ids=["rest_chat", "protocol_run_start"],
)
async def test_chat_and_protocol_start_consume_the_exact_lease(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, executor, payload
) -> None:
    harness = await ekit.setup(db, tmp_path, monkeypatch, [AIMessage("done")])
    cfg = await ekit.entry_cfg(harness)
    harness.resolutions.clear()

    row = await ekit.run_through_worker(harness, harness.seeded.run_id, cfg, payload, executor)

    _assert_prepared(harness, row, sends=1)
    assert harness.resolutions == [(False, True)]
    assert harness.server.discoveries == 1
    assert _human_texts() == ["hi"]
    async with TestSession() as check:
        chunks = await check.scalar(
            select(func.count())
            .select_from(MessageEventChunk)
            .where(MessageEventChunk.assistant_msg_id == str(row.id))
        )
    assert chunks  # stream events persisted through the gated capability callback


@pytest.mark.asyncio
@pytest.mark.parametrize("executor", [execute_agent_stream, execute_agent_stream_langgraph])
async def test_edit_and_regenerate_seed_the_exact_fork_checkpoint(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, executor
) -> None:
    harness = await ekit.setup(db, tmp_path, monkeypatch, [AIMessage("first")])
    first = await ekit.run_through_worker(
        harness,
        harness.seeded.run_id,
        await ekit.entry_cfg(harness),
        [{"role": "user", "content": "hi"}],
        execute_agent_stream,
    )
    assert first.status == "completed"
    thread = str(harness.seeded.request.conversation_id)
    head = await checkpointer.get_checkpointer().aget_tuple({"configurable": {"thread_id": thread}})
    assert head is not None
    assert head.parent_config is not None
    fork_id = str(head.parent_config.get("configurable", {})["checkpoint_id"])

    harness.script([AIMessage("edited answer")])
    edit = await build_fork_overwrite_input(
        checkpointer.get_checkpointer(), thread, fork_id, append=[HumanMessage("edited")]
    )
    edit_cfg = await ekit.entry_cfg(harness, checkpoint_id=fork_id)
    row = await ekit.run_through_worker(
        harness,
        await ekit.queued_run(harness, source="edit"),
        edit_cfg,
        edit,
        executor,
        moldy_source="edit",
    )
    _assert_prepared(harness, row, sends=2)
    assert harness.leases[-1].checkpoint_seed.checkpoint_id == fork_id
    assert _human_texts()[-1] == "edited"

    harness.script([AIMessage("regenerated")])
    regen = await build_fork_overwrite_input(checkpointer.get_checkpointer(), thread, fork_id)
    guidance = lambda cfg: with_regeneration_guidance(cfg, AIMessage("first"))  # noqa: E731
    row = await ekit.run_through_worker(
        harness,
        await ekit.queued_run(harness, source="regenerate"),
        guidance(await ekit.entry_cfg(harness, checkpoint_id=fork_id)),
        regen,
        executor,
        moldy_source="regenerate",
        config_transform=guidance,
    )
    _assert_prepared(harness, row, sends=3)
    assert harness.leases[-1].checkpoint_seed.checkpoint_id == fork_id
    system = str(ekit.SEEN[-1][0].content)
    assert "재생성 요청" in system and "first" in system


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "executors",
    [
        (execute_agent_stream, resume_agent_stream),
        (execute_agent_stream_langgraph, resume_agent_stream_langgraph),
    ],
    ids=["rest_resume", "protocol_input_respond"],
)
async def test_resume_continues_the_captured_interrupt_through_the_lease(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, executors
) -> None:
    call = AIMessage("", tool_calls=[{"name": "lookup", "args": {}, "id": "call-1"}])
    harness = await ekit.setup(db, tmp_path, monkeypatch, [call])
    start, resume = executors
    payload = [{"role": "user", "content": "look it up"}]
    if start is execute_agent_stream_langgraph:
        payload = {"messages": payload}
    first = await ekit.run_through_worker(
        harness, harness.seeded.run_id, await ekit.entry_cfg(harness), payload, start
    )
    assert first.status == "interrupted", (first.status, first.error_code)
    assert harness.server.calls == 0

    harness.script([AIMessage("found it")])
    row = await ekit.run_through_worker(
        harness,
        await ekit.queued_run(harness, source="resume"),
        await ekit.entry_cfg(harness),
        {"decisions": [{"type": "approve"}]},
        resume,
        moldy_source="resume",
    )
    _assert_prepared(harness, row, sends=2)
    assert harness.server.calls == 1
    assert len(harness.leases) == 2


@pytest.mark.asyncio
async def test_unsupported_agent_falls_back_to_legacy_deterministically(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # OpenAI moderation middleware is not leasable yet: legacy path, logged + counted.
    harness = await ekit.setup(db, tmp_path, monkeypatch, [AIMessage("done")], provider="openai")
    prepared_conversation_start.FALLBACKS.clear()
    payload = {"messages": [{"role": "user", "content": "hi"}]}

    row = await ekit.run_through_worker(
        harness,
        harness.seeded.run_id,
        await ekit.entry_cfg(harness),
        payload,
        execute_agent_stream_langgraph,
    )

    assert prepared_conversation_start.FALLBACKS == {"provider_middleware:openai": 1}
    assert harness.prepare_agent_calls == 1 and harness.leases == []
    # The legacy transition really started the run, whose (spied) prepare failed.
    assert (row.status, row.error_code) == ("failed", "runtime_error")
    assert row.started_at is not None and row.worker_instance_id is not None
    assert row.runtime_materialized_at is None and row.runtime_materialization_sha256 is None
    assert kit.SENDS == [] and harness.server.discoveries == 0
    assert get_prepared_run_registry().state(row.id) is None


@pytest.mark.asyncio
async def test_archive_wins_conversation_create_without_escape(
    db: AsyncSession, lifecycle_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    case, w = await _case(db, lifecycle_client)
    context = _context(case)
    # Create-first: the conversation (with its accepted run) commits before the
    # archive enumeration, so archive enumerates it and terminalizes the run.
    early = await create_conversation(db, w.agent_id, context=context)
    run = ConversationRun(
        conversation_id=early.id,
        agent_id=w.agent_id,
        user_id=context.user_id,
        source="chat",
        status="queued",
        is_active=True,
    )
    db.add(run)
    await db.commit()
    early_id, run_id = early.id, run.id
    scopes: list[ArchiveScope] = []
    original = enumeration.lock_archive_scope

    async def spy(session: AsyncSession, organization: Organization) -> ArchiveScope:
        scopes.append(scope := await original(session, organization))
        return scope

    monkeypatch.setattr(enumeration, "lock_archive_scope", spy)
    assert (await _lifecycle(db, lifecycle_client, case, "archive")).status_code == 200
    assert early_id in scopes[0].conversation_ids
    db.expire_all()
    archived = await db.get(ConversationRun, run_id)
    assert archived is not None
    assert (archived.status, archived.error_code) == ("failed", "organization_archived")

    # Archive-first: creation is denied with zero conversation/run/audit delta.
    counts = [await _count(db, model) for model in (Conversation, ConversationRun, AuditEvent)]
    with pytest.raises(AppError) as denied:
        await create_conversation(db, w.agent_id, context=context)
    await db.rollback()
    assert denied.value.code == "organization_unavailable"
    assert [await _count(db, m) for m in (Conversation, ConversationRun, AuditEvent)] == counts
