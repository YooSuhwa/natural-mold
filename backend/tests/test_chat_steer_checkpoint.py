"""A root run can acknowledge only its own durable steer IDs."""

from __future__ import annotations

import asyncio
import multiprocessing
import os
from collections.abc import AsyncIterator
from concurrent.futures import ProcessPoolExecutor
from typing import Any, TypedDict
from uuid import UUID, uuid4

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple, empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy.engine import make_url

from app.agent_runtime.chat_steer_checkpoint import (
    confirmed_steer_ids,
    run_checkpoint_for_steers,
)
from app.agent_runtime.checkpoint_seed import capture_checkpoint_seed
from app.agent_runtime.lease_checkpoint_namespace import LeaseNamespaceAuthority
from app.agent_runtime.lease_checkpoint_saver import LeaseCheckpointSaver
from app.schemas.conversation_graph_input import STEER_RECEIPTS_CHANNEL


class _State(TypedDict, total=False):
    text: str
    moldy_run_steer_receipts: dict[str, Any]


def _config(conversation_id: UUID, run_id: UUID, tenant_id: UUID, **extra: str) -> RunnableConfig:
    return {
        "configurable": {
            "thread_id": str(conversation_id),
            "checkpoint_ns": "",
            "moldy_run_id": str(run_id),
            "moldy_tenant_id": str(tenant_id),
            **extra,
        }
    }


def _receipts(run_id: UUID, *row_ids: UUID, staged: tuple[UUID, ...] = ()) -> dict[str, Any]:
    return {
        "run_id": str(run_id),
        "staged_ids": [str(row_id) for row_id in staged],
        "ids": [str(row_id) for row_id in row_ids],
    }


async def _write(
    saver: BaseCheckpointSaver[Any], config: RunnableConfig, receipts: Any
) -> RunnableConfig:
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {STEER_RECEIPTS_CHANNEL: receipts}
    versions = {STEER_RECEIPTS_CHANNEL: saver.get_next_version(None, None)}
    checkpoint["channel_versions"] = versions
    return await saver.aput(config, checkpoint, {"step": 1}, versions)


async def test_compacted_history_and_newer_sibling_do_not_hide_target_run() -> None:
    thread, tenant, seed_run, run, sibling, receipt, foreign = (uuid4() for _ in range(7))
    saver = InMemorySaver()
    builder = StateGraph(_State)
    builder.add_node("summarize", lambda state: {"text": "summary"})
    builder.add_edge(START, "summarize")
    builder.add_edge("summarize", END)
    graph = builder.compile(checkpointer=saver)
    seed = _config(thread, seed_run, tenant)
    await graph.ainvoke({"text": "seed"}, seed)
    saved = await saver.aget_tuple(seed)
    assert saved is not None
    fork = saved.checkpoint["id"]
    for target_run, row in [(run, receipt), (sibling, foreign)]:
        result = await graph.ainvoke(
            {
                "text": "directive transient content",
                "moldy_run_steer_receipts": _receipts(target_run, row),
            },
            _config(thread, target_run, tenant, checkpoint_id=fork),
        )
        assert result["text"] == "summary"
    found = await confirmed_steer_ids(
        thread, run, [receipt, foreign], saver=saver, tenant_id=tenant
    )
    assert found == {receipt}
    assert not await confirmed_steer_ids(thread, run, [receipt], saver=saver, tenant_id=uuid4())
    assert not await confirmed_steer_ids(uuid4(), run, [receipt], saver=saver, tenant_id=tenant)


async def test_pending_writes_and_child_snapshots_are_not_root_commit_evidence() -> None:
    thread, tenant, run, row_id = (uuid4() for _ in range(4))
    config = _config(thread, run, tenant)
    saver = InMemorySaver()
    saved = await _write(saver, config, _receipts(run))
    receipts = _receipts(run, row_id)
    await saver.aput_writes(saved, [(STEER_RECEIPTS_CHANNEL, receipts)], "pre-model-task")
    await _write(saver, _config(thread, run, tenant, checkpoint_ns="task:child"), receipts)
    assert not await confirmed_steer_ids(thread, run, [row_id], saver=saver, tenant_id=tenant)


async def test_committed_staging_snapshot_does_not_confirm_model_consumption() -> None:
    thread, tenant, run, first, second = (uuid4() for _ in range(5))
    saver = InMemorySaver()
    await _write(saver, _config(thread, run, tenant), _receipts(run, staged=(first, second)))
    assert not await confirmed_steer_ids(
        thread, run, [first, second], saver=saver, tenant_id=tenant
    )
    await _write(saver, _config(thread, run, tenant), _receipts(run, first, staged=(second,)))
    assert await confirmed_steer_ids(
        thread, run, [first, second], saver=saver, tenant_id=tenant
    ) == {first}


def _read_after_worker_restart(
    dsn: str, thread: UUID, run: UUID, tenant: UUID, rows: tuple[UUID, ...]
) -> frozenset[UUID]:
    async def read() -> frozenset[UUID]:
        async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
            return await confirmed_steer_ids(thread, run, rows, saver=saver, tenant_id=tenant)

    return asyncio.run(read())


@pytest.mark.integration
async def test_new_process_recovers_consumed_receipt_and_leaves_prepared_row_unconfirmed() -> None:
    raw_url = os.environ.get("INTEGRATION_DATABASE_URL")
    if not raw_url:
        pytest.skip("INTEGRATION_DATABASE_URL is required")
    url = make_url(raw_url)
    if not (url.database or "").startswith("moldy_pg_lane_"):
        pytest.fail("checkpoint recovery requires a disposable PostgreSQL lane")
    dsn = url.set(drivername="postgresql").render_as_string(hide_password=False)
    thread, tenant, run, consumed, staged = (uuid4() for _ in range(5))
    rows = (consumed, staged)
    async with AsyncPostgresSaver.from_conn_string(dsn) as writer:
        await writer.setup()
        snapshot = await _write(writer, _config(thread, run, tenant), _receipts(run, staged=rows))
        assert not await confirmed_steer_ids(thread, run, rows, saver=writer, tenant_id=tenant)
        config = _config(
            thread, run, tenant, checkpoint_id=snapshot.get("configurable", {})["checkpoint_id"]
        )
        await _write(writer, config, _receipts(run, consumed, staged=(staged,)))
    # Spawn a fresh interpreter after the writer has closed its connection. It
    # cannot inherit an in-process callback, compiled graph, or cached receipt.
    with ProcessPoolExecutor(
        max_workers=1, mp_context=multiprocessing.get_context("spawn")
    ) as pool:
        proven = await asyncio.get_running_loop().run_in_executor(
            pool, _read_after_worker_restart, dsn, thread, run, tenant, rows
        )
    assert proven == {consumed}


async def test_prepared_lease_accepts_own_root_but_not_its_previous_run_seed() -> None:
    thread, tenant, prior, run, prior_id, row_id = (uuid4() for _ in range(6))
    upstream = InMemorySaver()
    prior_config = await _write(
        upstream, _config(thread, prior, tenant), _receipts(prior, prior_id)
    )
    seed = await capture_checkpoint_seed(
        upstream, thread_id=str(thread), checkpoint_id=None, delta_channels=()
    )
    saver = LeaseCheckpointSaver(
        upstream, seed=seed, run_id=str(run), namespaces=LeaseNamespaceAuthority(())
    )
    assert not await confirmed_steer_ids(thread, run, [prior_id], saver=saver, tenant_id=tenant)
    config = _config(
        thread, run, tenant, checkpoint_id=prior_config.get("configurable", {})["checkpoint_id"]
    )
    await _write(saver, config, _receipts(run, row_id))
    assert await confirmed_steer_ids(thread, run, [row_id], saver=saver, tenant_id=tenant) == {
        row_id
    }
    # A subsequent process has only the authorized upstream saver.
    assert await confirmed_steer_ids(thread, run, [row_id], saver=upstream, tenant_id=tenant) == {
        row_id
    }


@pytest.mark.parametrize("invalid", ["old_run", "legacy_list", "malformed_id"])
async def test_inherited_legacy_or_malformed_receipt_channel_fails_closed(invalid: str) -> None:
    thread, tenant, run, row_id = (uuid4() for _ in range(4))
    saver = InMemorySaver()
    receipts: Any = {
        "old_run": _receipts(uuid4(), row_id),
        "legacy_list": [str(row_id)],
        "malformed_id": {"run_id": str(run), "ids": [str(row_id), None]},
    }[invalid]
    await _write(saver, _config(thread, run, tenant), receipts)
    assert not await confirmed_steer_ids(thread, run, [row_id], saver=saver, tenant_id=tenant)


async def test_unavailable_or_wrong_checkpoint_is_inconclusive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread, tenant, run, row_id = (uuid4() for _ in range(4))
    saver = InMemorySaver()
    config = _config(thread, run, tenant, checkpoint_ns="tools:child")
    await _write(saver, config, _receipts(run, row_id))
    child = await saver.aget_tuple(config)
    assert child is not None

    async def bad_history(*args: Any, **kwargs: Any) -> AsyncIterator[CheckpointTuple]:
        yield child

    monkeypatch.setattr(saver, "alist", bad_history)
    assert await run_checkpoint_for_steers(thread, run, saver=saver, tenant_id=tenant) is None

    def unavailable(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("saver unavailable")

    monkeypatch.setattr(saver, "alist", unavailable)
    assert not await confirmed_steer_ids(thread, run, [row_id], saver=saver, tenant_id=tenant)
