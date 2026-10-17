"""Native PostgreSQL chat-entry barriers for the prepared lease (parent Task 10, I23).

Real two-connection races between the owned chat worker and the app-local
organization archive endpoint: a leaf parked inside its permit holds no lock,
so archive commits while it is in flight and the exact lease finishes; the
materialization transaction commits, rolls back or fails activation with
archive waiting; checkpoint lineage stays exact; the four
tool classes dispatch only through the archived lease; MCP discovery timeout
cleans up before the marker. Only the canonical runner may execute this file.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

from app.agent_runtime import checkpointer
from app.agent_runtime.agent_stream_runner import execute_agent_stream
from app.agent_runtime.prepared_run_registry import get_prepared_run_registry
from app.exceptions import NotFoundError
from app.services import conversation_run_worker, prepared_run_adapter
from app.services.thread_branch_service import build_fork_overwrite_input
from tests.authz import prepared_entry_testkit as ekit
from tests.authz import prepared_materialization_testkit as kit
from tests.authz.task10_runtime_barriers import (
    Barriers,
    Gate,
    assert_blocked,
    finish,
    install_chat,
    park_after,
    tool_call,
)

if TYPE_CHECKING:
    from tests.authz.task10_native_fixture import Lane

pytestmark = pytest.mark.integration
pytest_plugins = ["tests.authz.task10_native_fixture"]
PAYLOAD = [{"role": "user", "content": "hi"}]
LS = tool_call("ls", {"path": "/"}, "ls-1")


async def _chat(lane: Lane, tmp_path: Path, script: list[AIMessage]) -> ekit.Harness:
    async with lane.sessions() as db:
        harness = await ekit.setup(db, tmp_path, lane.monkeypatch, script)
    lane.rebind()
    lane.track(harness.seeded.run_id)
    return harness


def _start(
    harness: ekit.Harness, run_id: Any = None, payload: Any = PAYLOAD, fork: str | None = None
) -> asyncio.Task[Any]:
    async def go() -> Any:
        cfg = await ekit.entry_cfg(harness, checkpoint_id=fork)
        target = run_id or harness.seeded.run_id
        source = {"moldy_source": "edit"} if fork else {}
        return await ekit.run_through_worker(
            harness, target, cfg, payload, execute_agent_stream, **source
        )

    return asyncio.create_task(go())


def _prepared(harness: ekit.Harness, row: Any) -> None:
    assert (row.status, row.error_code) == ("completed", None), row.error_message
    assert row.runtime_materialization_sha256 == harness.leases[-1].digest
    assert harness.resolutions[-1] == (False, True) and harness.prepare_agent_calls == 0
    assert all(after_marker is False for after_marker, _ in harness.resolutions)


async def test_native_chat_lease_first_archive_commits_during_parked_leaf_then_finishes(
    task10_lane: Lane, tmp_path: Path
) -> None:
    harness = await _chat(task10_lane, tmp_path, [])
    root = harness.seeded.root_model
    gate = Gate()
    factory = Barriers({root: [LS, AIMessage("done")], harness.seeded.child_model: []})
    factory.gates[root] = gate
    install_chat(task10_lane.monkeypatch, harness, factory)
    admin = await task10_lane.admin(harness.seeded.graph.context)

    run = _start(harness)
    await gate.wait_entered()
    response = await finish(task10_lane.lifecycle(admin, "archive"))  # never waits on the leaf
    assert not run.done()
    gate.release.set()
    row = await finish(run)

    assert response.status_code == 200 and response.json()["status"] == "archived"
    _prepared(harness, row)
    assert [root, root] == kit.SENDS
    task10_lane.track(row.id, leased=True)


async def test_native_chat_archive_first_terminalizes_without_dispatch(
    task10_lane: Lane, tmp_path: Path
) -> None:
    harness = await _chat(task10_lane, tmp_path, [AIMessage("never")])
    admin = await task10_lane.admin(harness.seeded.graph.context)
    cfg = await ekit.entry_cfg(harness)  # accepted before the archive commits
    assert (await task10_lane.lifecycle(admin, "archive")).status_code == 200

    seeded = harness.seeded
    # The worker's entry gateway refuses the archived organization before any task.
    with pytest.raises(NotFoundError):
        await conversation_run_worker.start_conversation_run(
            run_id=seeded.run_id,
            conversation_id=seeded.request.conversation_id,
            cfg=cfg,
            user=seeded.user,
            input_payload=PAYLOAD,
            moldy_source="chat",
            executor_fn=execute_agent_stream,
        )
    assert conversation_run_worker.get_run_task_registry().get(seeded.run_id) is None
    row = await task10_lane.run(seeded.run_id)
    assert (row.status, row.is_active, row.error_code) == ("failed", False, "organization_archived")
    assert row.runtime_materialized_at is None and row.completed_at is not None
    assert kit.SENDS == [] and harness.leases == [] and harness.server.discoveries == 0


@pytest.mark.parametrize("gap", ["commit", "rollback", "activation_failed"])
async def test_native_chat_materialization_transaction_gaps(
    task10_lane: Lane, tmp_path: Path, gap: str
) -> None:
    harness = await _chat(task10_lane, tmp_path, [LS, AIMessage("done")])
    admin = await task10_lane.admin(harness.seeded.graph.context)
    monkeypatch = task10_lane.monkeypatch
    gate = park_after(monkeypatch, prepared_run_adapter, "resolve_agent_context")
    parked = prepared_run_adapter.resolve_agent_context

    async def failing(*args: Any, **kwargs: Any) -> Any:
        await parked(*args, **kwargs)
        raise RuntimeError("prepare failed under the locks")

    if gap == "rollback":
        monkeypatch.setattr(prepared_run_adapter, "resolve_agent_context", failing)
    if gap == "activation_failed":
        registry = get_prepared_run_registry()

        def refuse(*_a: Any, **_k: Any) -> None:
            raise RuntimeError("activation refused")

        monkeypatch.setattr(registry, "activate", refuse)

    run = _start(harness)
    await gate.wait_entered()
    archive = asyncio.create_task(task10_lane.lifecycle(admin, "archive"))
    await assert_blocked(archive)
    gate.release.set()
    response, row = await finish(archive), await finish(run)

    assert response.status_code == 200
    if gap == "commit":
        _prepared(harness, row)
        assert len(kit.SENDS) == 2
        task10_lane.track(row.id, leased=True)
        return
    # Activation failure races the released archive for the durable fail; both
    # bounded outcomes are terminal, keep the marker, and never dispatch.
    codes = {
        "rollback": {("runtime_error", "prepared_run_start_failed")},
        "activation_failed": {
            ("prepared_run_activation_failed", "Prepared run could not be started"),
            ("runtime_error", "prepared_run_start_failed"),
        },
    }[gap]
    assert (row.status, row.is_active) == ("failed", False)
    assert (row.error_code, row.error_message) in codes
    assert (row.runtime_materialized_at is not None) is (gap == "activation_failed")
    assert kit.SENDS == [] and get_prepared_run_registry().state(row.id) is None


async def test_native_chat_checkpoint_lineage_is_exact(task10_lane: Lane, tmp_path: Path) -> None:
    harness = await _chat(task10_lane, tmp_path, [LS, AIMessage("first")])
    row = await finish(_start(harness))
    _prepared(harness, row)
    task10_lane.track(row.id, leased=True)
    saver, thread = checkpointer.get_checkpointer(), str(harness.seeded.request.conversation_id)
    scope: RunnableConfig = {"configurable": {"thread_id": thread}}
    first = {_id(c): _meta(c, "moldy_run_id") for c in saver.list(scope)}
    assert first and set(first.values()) == {str(row.id)}
    head = await saver.aget_tuple(scope)
    assert head is not None and head.parent_config is not None
    fork = _id_of(head.parent_config)

    harness.script([AIMessage("edited")])
    edit = await build_fork_overwrite_input(saver, thread, fork, append=[HumanMessage("edit")])
    second_id = await ekit.queued_run(harness, source="edit")
    task10_lane.track(second_id, leased=True)
    second = await finish(_start(harness, second_id, edit, fork))

    _prepared(harness, second)
    assert harness.leases[-1].checkpoint_seed.checkpoint_id == fork
    after = list(saver.list(scope))
    added = [c for c in after if _id(c) not in first]
    assert added and {_meta(c, "moldy_run_id") for c in added} == {str(second_id)}
    assert {_id_of(c.config, "checkpoint_ns") for c in added} == {""}
    # Descendants hang off the exact fork; the first run's checkpoints are untouched.
    oldest = min(added, key=lambda c: _meta(c, "step"))
    assert oldest.parent_config is not None and _id_of(oldest.parent_config) == fork
    assert {_id(c): _meta(c, "moldy_run_id") for c in after if _id(c) in first} == first


def _id_of(config: Any, key: str = "checkpoint_id") -> str:
    return str(dict(config).get("configurable", {})[key])


def _meta(checkpoint: Any, key: str) -> Any:
    return dict(checkpoint.metadata or {})[key]


def _id(checkpoint: Any) -> str:
    return _id_of(checkpoint.config)


async def test_native_chat_four_tool_classes_dispatch_only_through_archived_lease(
    task10_lane: Lane, tmp_path: Path
) -> None:
    harness = await _chat(task10_lane, tmp_path, [])
    seeded = harness.seeded
    root, child = seeded.root_model, seeded.child_model
    admin = await task10_lane.admin(seeded.graph.context)
    out = f"/conversations/{seeded.request.conversation_id}/out.txt"
    batch = AIMessage(
        content="",
        tool_calls=[
            {"name": "ls", "args": {"path": "/"}, "id": "snapshot"},
            {"name": "write_file", "args": {"file_path": out, "content": "x"}, "id": "output"},
            {"name": "read_file", "args": {"file_path": "/etc/passwd"}, "id": "unprepared"},
            {
                "name": "task",
                "args": {"description": "d", "subagent_type": seeded.child_name},
                "id": "child",
            },
        ],
    )

    async def archive() -> None:
        assert (await task10_lane.lifecycle(admin, "archive")).status_code == 200

    factory = Barriers(
        {root: [LS, batch, AIMessage("done")], child: [AIMessage("helped")]},
        hooks={root: archive},
    )
    install_chat(task10_lane.monkeypatch, harness, factory)
    row = await finish(_start(harness))

    _prepared(harness, row)
    task10_lane.track(row.id, leased=True)
    assert [root, root, child, root] == kit.SENDS
    results = {m.tool_call_id: m for m in ekit.SEEN[-1] if isinstance(m, ToolMessage)}
    assert results["snapshot"].status == results["output"].status == "success"
    assert results["unprepared"].status == "error" and str(results["child"].content) == "helped"
    access = harness.leases[-1].tool_access
    assert {access["root/ls"], access["root/write_file"], access["root/task"]} == {
        "snapshot_safe",
        "run_output",
        "live_application_resource",
    }
    assert access["root/lookup"] == "resolved_external" and harness.server.calls == 0


class _SlowServer(kit.FakeMcpServer):
    open_sessions: int = 0

    async def discover(self, key: str, config: dict, interceptors: list) -> list[BaseTool]:
        self.discoveries += 1
        self.open_sessions += 1
        try:
            await asyncio.sleep(5)
            return []
        finally:
            self.open_sessions -= 1


async def test_native_chat_mcp_timeout_cleans_up_before_marker(
    task10_lane: Lane, tmp_path: Path
) -> None:
    harness = await _chat(task10_lane, tmp_path, [AIMessage("never")])
    server = _SlowServer()
    install_chat(
        task10_lane.monkeypatch, harness, harness.factory, server, mcp_per_server_timeout=0.05
    )
    admin = await task10_lane.admin(harness.seeded.graph.context)

    row = await finish(_start(harness))

    # The shared pre-marker code table (run_error_codes) surfaces the cause's machine code.
    assert (row.status, row.error_code) == ("failed", "mcp_preparation_failed")
    assert row.runtime_materialized_at is None and harness.leases == []
    assert server.discoveries == 1 and server.open_sessions == 0 and kit.SENDS == []
    archived = await finish(task10_lane.lifecycle(admin, "archive"))
    assert archived.status_code == 200
