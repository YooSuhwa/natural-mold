"""Atomic prepared-run materialization: exact order, then activation (internal I15)."""

from __future__ import annotations

import asyncio
import dataclasses
import functools
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from deepagents.middleware.subagents import create_sub_agent
from langchain.agents import create_agent
from langchain_core.messages import AIMessage
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import prepared_run_transaction as transaction
from app.agent_runtime.prepared_run_compile import PreparedCompileError
from app.agent_runtime.prepared_run_registry import (
    LEASE_DUPLICATE,
    LeaseState,
    PreparedRunLeaseError,
)
from app.agent_runtime.prepared_subagent_catalog import PreparedChildCatalogError
from app.agent_runtime.runtime_hydration_guard import post_marker_execution
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.services import agent_execution_runtime
from app.services.prepared_run_state import fail_prepared_run, mark_prepared_running
from tests.authz import prepared_materialization_testkit as kit
from tests.authz.test_prepared_runtime_installed_compiler import PostMarkerSpies
from tests.conftest import TestSession

EXPECTED_TRACE = [
    "locks",
    "prepare_inputs",
    "snapshot_seed",
    "mcp_discovery",
    "lease_models",
    "probe_compile",
    "census",
    "reserve",
    "install_interceptors",
    "compile_children",
    "compile_root",
    "stage",
    "close_transient",
    "marker_running",
    "commit",
    "activate",
    "schedule",
]
HELPER = kit.child("helper", tools=[], model=kit.spec("helper"))


@pytest.mark.asyncio
async def test_complete_lease_activates_after_commit_with_exact_trace(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = kit.fresh_registry(monkeypatch)
    queued = await kit.queued_run(db)
    factory = kit.ScriptedFactory(
        {
            "root": [kit.echo_call("call-e"), kit.task_call("helper", "call-t"), AIMessage("done")],
            "helper": [AIMessage(content="helped")],
        }
    )
    server = kit.FakeMcpServer()
    transient: list[str] = []
    trace: list[str] = []
    locked: list[bool] = []

    async def close_transient() -> None:
        transient.append(f"closed:sends={len(kit.SENDS)}")

    async def schedule(lease: Any) -> None:
        assert registry.state(lease.binding.run_id) is LeaseState.ACTIVE
        assert kit.SENDS == [] and kit.ECHOED == [] and server.calls == 0

    traced = functools.partial(transaction.materialize_and_activate, trace=trace.append)
    monkeypatch.setattr(agent_execution_runtime, "materialize_and_activate", traced)
    inputs = kit.inputs(
        tmp_path,
        thread_id=str(queued.graph.conversation_ids[0]),
        mcp_configs=(kit.mcp_config(),),
        subagents=(HELPER,),
        child_tools={"root/helper": frozenset()},
        transient_closers=(close_transient,),
    )

    lease = await agent_execution_runtime.materialize_prepared_execution(
        queued.request,
        prepare_inputs=kit.resolver(inputs, locked),
        schedule=schedule,
        seams=kit.seams(factory, server),
    )

    assert trace == EXPECTED_TRACE and locked == [True]
    assert server.discoveries == 1 and transient == ["closed:sends=0"]
    row = await _run_row(queued.run_id)
    assert row.status == "running" and row.runtime_materialization_sha256 == lease.digest
    assert set(lease.binding.participant_paths) == {"root", "root/general-purpose", "root/helper"}
    assert "lookup" in lease.runnable.nodes["tools"].bound.tools_by_name
    assert {"tool:root:mcp:lookup", "child_launch:root/helper"} <= lease.descriptor_ids
    spies = PostMarkerSpies(monkeypatch)
    with post_marker_execution(str(queued.run_id)):
        result = await lease.runnable.ainvoke(
            {"messages": list(lease.messages)}, dict(lease.config)
        )
    assert result["messages"][-1].content == "done"
    assert kit.SENDS == ["root", "root", "helper", "root"] and kit.ECHOED == ["call-e"]
    assert spies.compiled == [] and "task" not in spies.executed
    assert await registry.terminalize(queued.run_id) == "closed"
    assert factory.closes() == {"root": 1, "helper": 1}


async def _run_row(run_id: Any) -> ConversationRun:
    async with TestSession() as db:
        row = await db.get(ConversationRun, run_id)
        assert row is not None
        return row


def _cancel_then_compile(spec: dict[str, Any], *, response_format: Any = None) -> Any:
    task = asyncio.current_task()
    assert task is not None
    task.cancel()
    return create_sub_agent(spec, response_format=response_format)  # type: ignore[arg-type]


def _wrong_surface(spec: dict[str, Any], *, response_format: Any = None) -> Any:
    return create_agent(spec["model"], tools=[kit.echo])


def _broken_compile(*_args: Any, **_kwargs: Any) -> Any:
    raise TypeError("final compile failed")


# stage -> (raised, durable row status, error code, post-reserve)
MATRIX: dict[str, tuple[type[BaseException], str, str | None, bool]] = {
    "resolve": (KeyError, "queued", None, False),
    "prepare": (PreparedChildCatalogError, "queued", None, False),
    "final_compile": (TypeError, "queued", None, True),
    "surface": (PreparedCompileError, "queued", None, True),
    "stage": (PreparedRunLeaseError, "queued", None, True),
    "cancel_compile": (asyncio.CancelledError, "queued", None, True),
    "transient": (OSError, "queued", None, True),
    "flush": (IntegrityError, "queued", None, True),
    "commit": (ConnectionError, "queued", None, True),
    "cancel": (asyncio.CancelledError, "queued", None, True),
    "commit_in_doubt": (ConnectionError, "failed", "prepared_run_commit_in_doubt", True),
    "activate": (PreparedRunLeaseError, "failed", "prepared_run_activation_failed", True),
    "schedule": (LookupError, "failed", "prepared_run_schedule_failed", True),
}
MATCH = {"surface": "effective_surface_changed", "stage": "prepared_run_lease_mismatch"}
COMPILERS = {
    "final_compile": _broken_compile,
    "surface": _wrong_surface,
    "cancel_compile": _cancel_then_compile,
}


async def _claimed_input(db: AsyncSession, queued: kit.QueuedRun) -> Any:
    item = ConversationRunInput(
        conversation_id=queued.graph.conversation_ids[0],
        agent_id=queued.graph.agent_id,
        user_id=queued.graph.context.user_id,
        run_id=queued.run_id,
        client_request_id=uuid4().hex,
        source="chat",
        status="claimed",
        position=1,
        input_payload={"text": "hi"},
    )
    db.add(item)
    await db.commit()
    return item.id


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", sorted(MATRIX))
async def test_failure_matrix_closes_and_never_reconstructs(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    registry = kit.fresh_registry(monkeypatch)
    queued = await kit.queued_run(db)
    input_id = await _claimed_input(db, queued)
    factory = kit.ScriptedFactory({"root": [AIMessage("done")], "helper": [AIMessage("h")]})
    closed: list[str] = []

    async def first() -> None:
        closed.append("first")
        if stage == "transient":
            raise OSError("transient close failed")

    async def second() -> None:
        closed.append("second")

    mark_real = mark_prepared_running("w-1")

    async def mark(tx: AsyncSession, run: ConversationRun) -> None:
        if stage == "flush":
            run.runtime_materialization_sha256 = "not-a-digest"
        await mark_real(tx, run)
        if stage == "cancel":
            raise asyncio.CancelledError
        if stage in {"commit", "commit_in_doubt"}:
            real_commit = tx.commit

            async def commit() -> None:
                if stage == "commit_in_doubt":
                    await real_commit()
                raise ConnectionError("commit outcome unknown")

            tx.commit = commit  # type: ignore[method-assign]

    async def schedule(_lease: Any) -> None:
        if stage == "schedule":
            raise LookupError("scheduler full")

    if stage == "activate":

        def refuse(*_a: Any, **_k: Any) -> None:
            raise PreparedRunLeaseError("activation_crashed")

        monkeypatch.setattr(registry, "activate", refuse)
    if stage == "stage":
        reserve = registry.reserve

        def tampered(binding: Any) -> str:
            token = reserve(binding)
            registry._entries[binding.run_id].token = "tampered"  # noqa: SLF001
            return token

        monkeypatch.setattr(registry, "reserve", tampered)
    rogue = kit.child("rogue", tools=[])
    inputs = kit.inputs(
        tmp_path,
        thread_id=str(queued.graph.conversation_ids[0]),
        subagents=(HELPER, rogue) if stage == "prepare" else (HELPER,),
        child_tools={"root/helper": frozenset()},
        transient_closers=(first, second),
    )
    resolved = kit.resolver(inputs)

    async def prepare(db: AsyncSession, run: ConversationRun) -> Any:
        if stage == "resolve":
            raise KeyError("agent_vanished")
        return await resolved(db, run)

    seams = kit.seams(factory)
    if stage in COMPILERS:
        seams = dataclasses.replace(seams, compile_child=COMPILERS[stage])
    raised, status, code, post_reserve = MATRIX[stage]
    async with TestSession() as tx:
        with pytest.raises(raised, match=MATCH.get(stage)):
            await transaction.materialize_and_activate(
                tx,
                agent_execution_runtime._execution_target(queued.request),
                prepare_inputs=prepare,
                mark_running=mark,
                fail_running=fail_prepared_run,
                schedule=schedule,
                session_factory=TestSession,
                seams=seams,
            )
    if stage == "cancel_compile":
        current = asyncio.current_task()
        assert current is not None
        current.uncancel()

    assert factory.closes() == ({} if stage == "resolve" else {"root": 1, "helper": 1})
    assert closed == ([] if stage == "resolve" else ["second", "first"])
    assert registry.state(queued.run_id) is None and kit.SENDS == []
    row = await _run_row(queued.run_id)
    assert (row.status, row.error_code) == (status, code)
    assert row.is_active is (status != "failed")
    assert (row.runtime_materialized_at is not None) is (status == "failed")
    async with TestSession() as check:
        claimed = await check.get(ConversationRunInput, input_id)
    assert claimed is not None and claimed.status == ("failed" if status == "failed" else "claimed")
    if not post_reserve:
        registry.reserve(kit.binding_for(queued))
        return
    with pytest.raises(PreparedRunLeaseError) as duplicate:
        registry.reserve(kit.binding_for(queued))
    assert duplicate.value.code == LEASE_DUPLICATE
