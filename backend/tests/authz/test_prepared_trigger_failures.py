"""Prepared trigger failure/cancel outcomes are recorded and never wedge the trigger (I18)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import agent_stream_runner, credential_resolution
from app.agent_runtime.credential_resolution import LLMCredentialRequiredError
from app.agent_runtime.prepared_run_registry import get_prepared_run_registry
from app.exceptions import AppError
from app.models.agent_trigger import AgentTrigger
from app.models.conversation_run import ConversationRun
from app.services.conversation_run_state import request_cancel_run
from tests.authz import prepared_materialization_testkit as kit
from tests.authz import prepared_trigger_testkit as tkit
from tests.conftest import TestSession


def _scripts(seeded: tkit.SeededTrigger) -> dict[str, list[AIMessage]]:
    return {seeded.root_model: [AIMessage("trigger done")]}


async def _no_runs_left(tmp_path: Path, run_id: Any) -> None:
    assert get_prepared_run_registry().active_run_ids() == set()
    assert not (tmp_path / "runs" / str(run_id)).exists()


@pytest.mark.asyncio
async def test_credential_lost_after_acceptance_fails_run_and_next_fire_proceeds(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await tkit.seed_trigger(db)
    harness = tkit.install(monkeypatch, tmp_path, seeded, _scripts(seeded))
    restore = credential_resolution.resolve_llm_api_key_for_agent

    async def deleted(*_a: Any, **_k: Any) -> str:
        # The credential is deleted between acceptance and the locked config build.
        raise LLMCredentialRequiredError()

    monkeypatch.setattr(credential_resolution, "resolve_llm_api_key_for_agent", deleted)
    await tkit.execute(seeded)

    (first,) = await tkit.trigger_runs(seeded)
    assert (first.status, first.error_message) == ("failed", "llm_credential_required")
    assert first.finished_at is not None
    failed = await tkit.conversation_run(first.conversation_run_id)
    assert (failed.status, failed.error_code) == ("failed", "llm_credential_required")
    assert kit.SENDS == [] and harness.at_activation == []
    await _no_runs_left(tmp_path, failed.id)

    monkeypatch.setattr(credential_resolution, "resolve_llm_api_key_for_agent", restore)
    tkit.install(monkeypatch, tmp_path, seeded, _scripts(seeded))
    await tkit.execute(seeded)
    _first, second = sorted(await tkit.trigger_runs(seeded), key=lambda row: row.started_at)
    assert (second.status, second.output_preview) == ("success", "trigger done")


@pytest.mark.asyncio
async def test_provider_failure_during_dispatch_records_the_exact_message(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await tkit.seed_trigger(db)
    tkit.install(monkeypatch, tmp_path, seeded, _scripts(seeded))

    async def explode(*_a: Any, **_k: Any) -> str:
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(agent_stream_runner, "execute_prepared_agent_invoke", explode)
    await tkit.execute(seeded)

    (run,) = await tkit.trigger_runs(seeded)
    assert (run.status, run.error_message) == ("failed", "provider exploded")
    conversation_run = await tkit.conversation_run(run.conversation_run_id)
    assert conversation_run.status == "failed"
    assert conversation_run.runtime_materialized_at is not None
    await _no_runs_left(tmp_path, conversation_run.id)


@pytest.mark.asyncio
async def test_app_error_during_dispatch_takes_the_legacy_app_error_branch(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await tkit.seed_trigger(db)
    tkit.install(monkeypatch, tmp_path, seeded, _scripts(seeded))

    async def denied(*_a: Any, **_k: Any) -> str:
        raise AppError("lease_permit_denied", "denied", status=403)

    monkeypatch.setattr(agent_stream_runner, "execute_prepared_agent_invoke", denied)
    await tkit.execute(seeded)

    (run,) = await tkit.trigger_runs(seeded)
    # Legacy ``except AppError``: no finish_trigger_run, only the run projection.
    assert (run.status, run.error_message, run.finished_at) == ("failed", None, None)
    conversation_run = await tkit.conversation_run(run.conversation_run_id)
    assert conversation_run.status == "failed"
    await _no_runs_left(tmp_path, conversation_run.id)


@pytest.mark.asyncio
async def test_cancel_requested_before_start_is_acknowledged_canceled(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await tkit.seed_trigger(db)
    harness = tkit.install(monkeypatch, tmp_path, seeded, _scripts(seeded))

    async def cancel(other: AsyncSession) -> None:
        run = await other.scalar(select(ConversationRun).where(ConversationRun.status == "queued"))
        assert run is not None
        await request_cancel_run(other, run, reason="stop")

    tkit.before_locks(monkeypatch, cancel)
    await tkit.execute(seeded)

    (run,) = await tkit.trigger_runs(seeded)
    assert (run.status, run.error_message) == ("canceled", None)
    conversation_run = await tkit.conversation_run(run.conversation_run_id)
    assert (conversation_run.status, conversation_run.is_active) == ("canceled", False)
    assert conversation_run.runtime_materialized_at is None
    async with TestSession() as check:
        trigger = await check.get(AgentTrigger, seeded.trigger_id)
    assert trigger is not None and trigger.failure_count == 0
    assert kit.SENDS == [] and harness.at_activation == []


@pytest.mark.asyncio
async def test_child_identity_error_records_the_legacy_message(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await tkit.seed_trigger(db, with_child=True, child_identity="per_user")
    tkit.install(monkeypatch, tmp_path, seeded, _scripts(seeded))

    await tkit.execute(seeded)

    (run,) = await tkit.trigger_runs(seeded)
    assert run.status == "failed"
    assert run.error_message == "per_user subagent identity requires an authenticated parent caller"
    conversation_run = await tkit.conversation_run(run.conversation_run_id)
    assert (conversation_run.status, conversation_run.error_code) == (
        "failed",
        "agent_identity_error",
    )
    assert kit.SENDS == []


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "pre-existing: legacy build_subagents_config raises MissingGreenlet for a "
        "linked child (child.model lazy-loads after the agent-tree reload); also "
        "reproduces with the flag off"
    ),
)
async def test_unsupported_fallback_with_child_runs_legacy(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await tkit.seed_trigger(db, stored_policy=False, with_child=True)
    harness = tkit.install(monkeypatch, tmp_path, seeded, _scripts(seeded))

    await tkit.execute(seeded)

    (run,) = await tkit.trigger_runs(seeded)
    assert run.status == "success" and harness.legacy_invokes == [tkit.INPUT]
