"""Prepared chat-run contracts beyond the happy path (I16 review fixes).

* an activated lease the stream never claimed is closed by the worker;
* only legacy-shape unsupported reasons fall back; ``inputs_unlocked`` fails;
* real memory reads under the locks never commit (no default-row insert);
* spend/audit are booked after the lease terminalized;
* the builder's seed delta channels match the compiled graph.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.channels.delta import DeltaChannel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.langgraph_agent_stream_runner import execute_agent_stream_langgraph
from app.agent_runtime.prepared_child_launch import get_child_launch_ledger
from app.agent_runtime.prepared_run_input_builder import DELTA_CHANNELS
from app.agent_runtime.prepared_run_input_policy import is_legacy_fallback
from app.agent_runtime.prepared_run_registry import get_prepared_run_registry
from app.hooks import HookRegistry, SpendHook
from app.models.daily_spend_user import DailySpendUser
from app.models.memory import AgentMemorySettings, UserMemorySettings
from app.services import (
    conversation_run_execution,
    prepared_conversation_start,
    prepared_run_adapter,
    spend_writer,
)
from app.services.conversation_stream_service import resolve_agent_context
from tests.authz import prepared_entry_testkit as ekit
from tests.authz import prepared_materialization_testkit as kit
from tests.conftest import TestSession

PAYLOAD = {"messages": [{"role": "user", "content": "hi"}]}


async def _run(harness: ekit.Harness) -> Any:
    cfg = await ekit.entry_cfg(harness)
    return await ekit.run_through_worker(
        harness, harness.seeded.run_id, cfg, PAYLOAD, execute_agent_stream_langgraph
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("audit down"), asyncio.CancelledError()])
async def test_unclaimed_lease_is_closed_when_worker_fails_before_dispatch(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    harness = await ekit.setup(db, tmp_path, monkeypatch, [AIMessage("never")])
    terminalized: list[Any] = []
    ledger = get_child_launch_ledger()
    original = ledger.terminalize
    monkeypatch.setattr(
        ledger, "terminalize", lambda run_id: (terminalized.append(run_id), original(run_id))[1]
    )

    async def audit(**_kwargs: Any) -> None:
        raise failure

    monkeypatch.setattr(conversation_run_execution, "_record_run_audit", audit)
    row = await _run(harness)

    assert row.runtime_materialized_at is not None  # activated after commit ...
    assert get_prepared_run_registry().state(row.id) is None  # ... and closed
    assert terminalized == [row.id]
    assert set(harness.factory.closes().values()) == {1}
    assert kit.SENDS == []
    if isinstance(failure, RuntimeError):
        assert (row.status, row.error_code) == ("failed", "runtime_error")


def test_fallback_allowlist_is_legacy_shapes_only() -> None:
    for reason in ("memory_tools", "provider_middleware:openai", "middleware:model_retry"):
        assert is_legacy_fallback(reason)
    assert is_legacy_fallback("child:agent_ab12:provider:bedrock")
    for reason in ("inputs_unlocked", "skill_source_missing:x", "child:agent_ab12:surprise"):
        assert not is_legacy_fallback(reason)


@pytest.mark.asyncio
async def test_inputs_unlocked_fails_the_run_instead_of_falling_back(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await ekit.setup(db, tmp_path, monkeypatch, [AIMessage("never")])
    prepared_conversation_start.FALLBACKS.clear()

    async def committing(db: AsyncSession, *args: Any, **kwargs: Any) -> Any:
        cfg = await resolve_agent_context(db, *args, **kwargs)
        await db.commit()  # releases the canonical locks mid-preparation
        return cfg

    monkeypatch.setattr(prepared_run_adapter, "resolve_agent_context", committing)
    row = await _run(harness)

    # I24 M2: the pre-marker cause code is the run's error code (was runtime_error).
    assert (row.status, row.error_code) == ("failed", "prepared_run_unsupported:inputs_unlocked")
    assert row.runtime_materialized_at is None and harness.leases == []
    assert prepared_conversation_start.FALLBACKS == {} and harness.prepare_agent_calls == 0
    assert kit.SENDS == []


@pytest.mark.asyncio
async def test_real_memory_reads_keep_the_locks_without_a_settings_row(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await ekit.setup(
        db, tmp_path, monkeypatch, [AIMessage("done")], memory_settings=False, with_child=False
    )
    db.add(
        AgentMemorySettings(agent_id=harness.seeded.graph.agent_id, memory_policy_override="off")
    )
    await db.commit()

    row = await _run(harness)

    assert row.status == "completed", (row.status, row.error_code, row.error_message)
    assert row.runtime_materialization_sha256 == harness.leases[-1].digest
    async with TestSession() as check:
        count = await check.scalar(select(func.count()).select_from(UserMemorySettings))
    assert count == 0  # read-or-default, nothing inserted/committed under the locks
    # The builder's seed delta channels are exactly the compiled graph's DeltaChannels.
    channels = harness.leases[-1].runnable.channels
    assert {n for n, c in channels.items() if isinstance(c, DeltaChannel)} == set(DELTA_CHANNELS)


@pytest.mark.asyncio
async def test_spend_is_booked_after_the_prepared_lease_terminalized(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    usage = {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}
    harness = await ekit.setup(db, tmp_path, monkeypatch, [AIMessage("done", usage_metadata=usage)])
    queue = spend_writer.DailySpendUpdateQueue()
    monkeypatch.setattr(queue, "_session_factory", TestSession)
    registry = HookRegistry()
    registry.register(SpendHook())
    monkeypatch.setattr("app.hooks.builtin.spend_hook.spend_queue", queue)
    monkeypatch.setattr("app.hooks.hooks", registry)

    row = await _run(harness)
    await queue._flush_remaining()  # noqa: SLF001

    assert row.status == "completed"
    async with TestSession() as check:
        spend = await check.scalar(
            select(DailySpendUser).where(
                DailySpendUser.user_id == harness.seeded.graph.context.user_id
            )
        )
    assert spend is not None and (spend.total_tokens_in, spend.total_tokens_out) == (11, 7)
