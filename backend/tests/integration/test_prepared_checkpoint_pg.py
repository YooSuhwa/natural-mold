"""Prepared chat checkpoints must accept local graph writes over asynchronous PostgreSQL I/O."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection, sql
from psycopg.rows import DictRow, dict_row
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import checkpointer
from app.agent_runtime.checkpoint_seed import capture_checkpoint_seed
from app.agent_runtime.langgraph_agent_stream_runner import (
    execute_agent_stream_langgraph,
    resume_agent_stream_langgraph,
)
from app.agent_runtime.lease_checkpoint_namespace import (
    CheckpointLineageDeniedError,
    LeaseNamespaceAuthority,
)
from app.agent_runtime.lease_checkpoint_saver import LeaseCheckpointSaver
from app.services.thread_branch_service import materialize_messages_at_checkpoint
from app.tenancy.checkpoint_audit import CHECKPOINT_TENANT_KEY, run_configurable
from tests.authz import prepared_entry_testkit as ekit
from tests.authz import prepared_materialization_testkit as kit


@pytest.fixture
async def pg_saver(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncPostgresSaver]:
    raw_url = os.environ.get("INTEGRATION_DATABASE_URL")
    if not raw_url:
        pytest.skip("INTEGRATION_DATABASE_URL is required for the PostgreSQL checkpointer")
    url = make_url(raw_url).set(drivername="postgresql").render_as_string(hide_password=False)
    schema = sql.Identifier(f"lease_checkpoints_{uuid4().hex}")
    async with await AsyncConnection[DictRow].connect(
        url, row_factory=dict_row, autocommit=True
    ) as connection:
        await connection.execute(sql.SQL("CREATE SCHEMA {}").format(schema))
        try:
            await connection.execute(sql.SQL("SET search_path TO {}").format(schema))
            saver = AsyncPostgresSaver(connection)
            await saver.setup()
            monkeypatch.setattr(checkpointer, "_checkpointer", saver)
            yield saver
        finally:
            await connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(schema))


@pytest.mark.parametrize("delegate", [False, True], ids=["root", "delegated"])
async def test_prepared_worker_persists_model_and_delegated_graph_writes(
    db: AsyncSession,
    pg_saver: AsyncPostgresSaver,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    delegate: bool,
) -> None:
    harness = await ekit.setup(db, tmp_path, monkeypatch, [AIMessage("done")], with_child=delegate)
    seeded = harness.seeded
    if delegate:
        harness.factory.scripts = {
            seeded.root_model: [kit.task_call(seeded.child_name, "delegate"), AIMessage("done")],
            seeded.child_model: [AIMessage("delegated result")],
        }
    monkeypatch.setattr(checkpointer, "get_checkpointer", lambda: pg_saver)
    row = await ekit.run_through_worker(
        harness,
        seeded.run_id,
        await ekit.entry_cfg(harness),
        {"messages": [{"role": "user", "content": "go"}]},
        execute_agent_stream_langgraph,
    )

    assert (row.status, row.error_code) == ("completed", None), row.error_message
    assert row.runtime_materialization_sha256 == harness.leases[-1].digest
    assert (
        [seeded.root_model, seeded.child_model, seeded.root_model]
        if delegate
        else [seeded.root_model]
    ) == kit.SENDS
    thread = str(seeded.request.conversation_id)
    history = [saved async for saved in pg_saver.alist({"configurable": {"thread_id": thread}})]
    assert history and any(saved.pending_writes for saved in history)
    assert all(
        saved.metadata.get(CHECKPOINT_TENANT_KEY) == str(seeded.graph.context.tenant_id)
        for saved in history
    )
    assert all(saved.metadata.get("moldy_run_id") == str(row.id) for saved in history)
    root = await pg_saver.aget_tuple({"configurable": {"thread_id": thread, "checkpoint_ns": ""}})
    assert root is not None and root.parent_config is not None
    messages = await materialize_messages_at_checkpoint(pg_saver, thread, root.checkpoint["id"])
    assert messages[-1].content == "done"
    if delegate:
        assert any(
            saved.config.get("configurable", {})["checkpoint_ns"].startswith("tools:")
            for saved in history
        )


async def test_prepared_worker_resumes_durable_approval_once(
    db: AsyncSession,
    pg_saver: AsyncPostgresSaver,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = AIMessage("", tool_calls=[{"name": "lookup", "args": {}, "id": "lookup-1"}])
    harness = await ekit.setup(db, tmp_path, monkeypatch, [call], with_child=False)
    monkeypatch.setattr(checkpointer, "get_checkpointer", lambda: pg_saver)
    initial = await ekit.run_through_worker(
        harness,
        harness.seeded.run_id,
        await ekit.entry_cfg(harness),
        {"messages": [{"role": "user", "content": "look it up"}]},
        execute_agent_stream_langgraph,
    )
    assert initial.status == "interrupted", initial.error_message
    assert harness.server.calls == 0
    thread = str(harness.seeded.request.conversation_id)
    interrupted = await pg_saver.aget_tuple(
        {"configurable": {"thread_id": thread, "checkpoint_ns": ""}}
    )
    assert interrupted is not None and any(
        ch == "__interrupt__" for _, ch, _ in interrupted.pending_writes or []
    )

    harness.script([AIMessage("approved result")])
    resumed = await ekit.run_through_worker(
        harness,
        await ekit.queued_run(harness, source="resume"),
        await ekit.entry_cfg(harness),
        {"decisions": [{"type": "approve"}]},
        resume_agent_stream_langgraph,
        moldy_source="resume",
    )
    assert resumed.status == "completed", resumed.error_message
    assert harness.leases[-1].checkpoint_seed.checkpoint_id == interrupted.checkpoint["id"]
    assert harness.server.calls == 1
    assert [harness.seeded.root_model, harness.seeded.root_model] == kit.SENDS


async def test_foreign_checkpoint_writes_stay_outside_prepared_lineage(
    pg_saver: AsyncPostgresSaver,
) -> None:
    thread, run, tenant = str(uuid4()), uuid4(), uuid4()
    config = run_configurable(thread, run, tenant)
    first = empty_checkpoint()
    await pg_saver.aput({"configurable": {**config, "checkpoint_ns": ""}}, first, {"step": -1}, {})
    seed = await capture_checkpoint_seed(
        pg_saver, thread_id=thread, checkpoint_id=first["id"], delta_channels=()
    )
    bound = LeaseCheckpointSaver(
        pg_saver, seed=seed, run_id=str(run), namespaces=LeaseNamespaceAuthority(())
    )
    other = uuid4()
    sibling_holder = LeaseCheckpointSaver(
        pg_saver, seed=seed, run_id=str(other), namespaces=LeaseNamespaceAuthority(())
    )
    sibling = empty_checkpoint()
    await sibling_holder.aput(
        {
            "configurable": {
                **run_configurable(thread, other, tenant),
                "checkpoint_ns": "",
                "checkpoint_id": first["id"],
            }
        },
        sibling,
        {"step": 0},
        {},
    )
    other_company = run_configurable(str(uuid4()), uuid4(), uuid4())
    other_root = empty_checkpoint()
    await pg_saver.aput(
        {"configurable": {**other_company, "checkpoint_ns": ""}}, other_root, {"step": -1}, {}
    )
    exact = {**config, "checkpoint_ns": "", "checkpoint_id": first["id"]}
    for reason, overrides in [
        ("writes", {"checkpoint_id": sibling["id"]}),
        ("writes", {"checkpoint_id": str(uuid4())}),
        ("run", {"moldy_run_id": str(other)}),
        ("namespace", {"checkpoint_ns": "unregistered:task"}),
        ("thread", {**other_company, "checkpoint_id": other_root["id"]}),
    ]:
        with pytest.raises(
            CheckpointLineageDeniedError, match=f"checkpoint_lineage_denied:{reason}$"
        ):
            await bound.aput_writes(
                {"configurable": {**exact, **overrides}}, [("test", "denied")], "foreign"
            )
    # Query the entire isolated schema: a join through committed checkpoints would miss
    # a raw write accidentally sent for an unknown checkpoint id.
    assert isinstance(pg_saver.conn, AsyncConnection)
    writes = await pg_saver.conn.execute("SELECT count(*) AS n FROM checkpoint_writes")
    assert await writes.fetchone() == {"n": 0}
