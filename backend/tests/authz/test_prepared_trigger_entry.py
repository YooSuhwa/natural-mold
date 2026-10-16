"""Scheduled triggers through bounded pre-marker preparation and the active lease (I18)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import prepared_run_materializer
from app.agent_runtime.prepared_run_registry import get_prepared_run_registry
from app.authz.tenant_org_write_locks import lock_tenant_org_write
from app.config import settings
from app.models.agent_trigger import AgentTrigger
from app.models.conversation_run import ConversationRun
from app.models.organization import Organization
from app.services import prepared_trigger_resolver, trigger_service
from app.services.tenants.organization_archive_terminalizer import cascade_organization_archive
from tests.authz import prepared_materialization_testkit as kit
from tests.authz import prepared_trigger_testkit as tkit
from tests.conftest import TestSession


def _scripts(seeded: tkit.SeededTrigger) -> dict[str, list[AIMessage]]:
    return {seeded.root_model: [tkit.mcp_call("m1"), AIMessage("trigger done")]}


@pytest.mark.asyncio
async def test_trigger_materializes_then_dispatches_with_exact_terminal_metadata(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await tkit.seed_trigger(db, with_child=True)
    scripts = _scripts(seeded)
    assert seeded.child_model is not None
    scripts[seeded.child_model] = []
    harness = tkit.install(monkeypatch, tmp_path, seeded, scripts)
    compiled: list[tuple[Any, set[str]]] = []
    compile_root = prepared_run_materializer.compile_root

    def compile_spy(**kwargs: Any) -> Any:
        graph = compile_root(**kwargs)
        compiled.append((kwargs["interrupt_on"], set(graph.nodes["tools"].bound.tools_by_name)))
        return graph

    monkeypatch.setattr(prepared_run_materializer, "compile_root", compile_spy)

    result = await tkit.execute(seeded)

    # Trigger mode: no HiTL policy (MCP approvals included) and no ask_user tool.
    assert compiled and all(policy is None for policy, _tools in compiled)
    assert all(tkit.MCP_TOOL in tools and "ask_user" not in tools for _p, tools in compiled)

    # Full lease before running: one bounded MCP discovery, zero provider sends.
    assert harness.at_activation == [(0, 1)]
    assert harness.server.discoveries == 1 and harness.server.calls == 1
    assert [seeded.root_model, seeded.root_model] == kit.SENDS
    assert harness.legacy_invokes == [] and harness.hydrations == []
    (run,) = await tkit.trigger_runs(seeded)
    assert result is not None and result.id == run.id
    assert (run.status, run.output_preview, run.error_message) == ("success", "trigger done", None)
    assert run.thread_id == str(run.conversation_id) and run.duration_ms is not None
    assert run.finished_at is not None and run.identity_mode == "fixed"
    conversation_run = await tkit.conversation_run(run.conversation_run_id)
    assert (conversation_run.status, conversation_run.is_active) == ("completed", False)
    assert conversation_run.runtime_materialized_at is not None
    assert conversation_run.runtime_materialization_sha256
    assert get_prepared_run_registry().state(conversation_run.id) is None
    assert (tmp_path / "runs").is_dir()
    assert not (tmp_path / "runs" / str(conversation_run.id)).exists()
    async with TestSession() as check:
        trigger = await check.get(AgentTrigger, seeded.trigger_id)
    assert trigger is not None
    assert (trigger.run_count, trigger.last_status, trigger.failure_count) == (1, "success", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "reason", "status", "message", "failures"),
    [
        ("status", "paused", "trigger_inactive", "skipped", "trigger is not active", 0),
        ("input_message", "다른 입력", "input_changed", "failed", None, 1),
    ],
)
async def test_trigger_changed_by_another_backend_is_revalidated_under_locks(
    db: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
    reason: str,
    status: str,
    message: str | None,
    failures: int,
) -> None:
    seeded = await tkit.seed_trigger(db)
    harness = tkit.install(monkeypatch, tmp_path, seeded, _scripts(seeded))

    async def mutate(other: AsyncSession) -> None:
        trigger = await other.get(AgentTrigger, seeded.trigger_id)
        assert trigger is not None
        setattr(trigger, field, value)

    tkit.before_locks(monkeypatch, mutate)
    await tkit.execute(seeded)

    code = f"prepared_trigger_stale:{reason}"
    (run,) = await tkit.trigger_runs(seeded)
    assert (run.status, run.error_message) == (status, message or code)
    conversation_run = await tkit.conversation_run(run.conversation_run_id)
    assert (conversation_run.status, conversation_run.error_code) == ("failed", code)
    assert conversation_run.runtime_materialized_at is None
    async with TestSession() as check:
        trigger = await check.get(AgentTrigger, seeded.trigger_id)
    assert trigger is not None
    assert (trigger.last_status, trigger.failure_count, trigger.run_count) == (status, failures, 0)
    assert kit.SENDS == [] and harness.server.discoveries == 0
    assert harness.at_activation == [] and harness.legacy_invokes == []
    assert not (tmp_path / "runs" / str(conversation_run.id)).exists()


async def _archive(other: AsyncSession, seeded: tkit.SeededTrigger) -> None:
    context = seeded.graph.context
    locked = await lock_tenant_org_write(other, context.tenant_id, (context.org_id,))
    organization = locked.organizations[0]
    stamp = datetime.now(UTC)
    await cascade_organization_archive(other, organization, stamp)
    organization.status, organization.archived_at = "archived", stamp.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_archive_wins_before_trigger_activation(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await tkit.seed_trigger(db)
    harness = tkit.install(monkeypatch, tmp_path, seeded, _scripts(seeded))
    real = tkit.before_locks(monkeypatch, lambda other: _archive(other, seeded))

    await tkit.execute(seeded)

    (run,) = await tkit.trigger_runs(seeded)
    assert (run.status, run.error_message) == ("failed", "organization_archived")
    conversation_run = await tkit.conversation_run(run.conversation_run_id)
    assert (conversation_run.status, conversation_run.error_code) == (
        "failed",
        "organization_archived",
    )
    assert conversation_run.runtime_materialized_at is None
    assert kit.SENDS == [] and harness.server.discoveries == 0 and harness.at_activation == []
    assert get_prepared_run_registry().active_run_ids() == set()

    # While archived the paused trigger creates no run; after restore it is not replayed.
    monkeypatch.setattr(prepared_trigger_resolver, "acquire_execution_proof", real)
    assert await tkit.execute(seeded) is None
    assert len(await tkit.trigger_runs(seeded)) == 1
    async with TestSession() as other:
        organization = await other.get(Organization, seeded.graph.context.org_id)
        assert organization is not None
        organization.status, organization.archived_at = "active", None
        await other.commit()
    skipped = await tkit.execute(seeded)
    assert skipped is not None and skipped.status == "skipped"
    async with TestSession() as check:
        runs = await check.scalar(select(func.count()).select_from(ConversationRun))
        trigger = await check.get(AgentTrigger, seeded.trigger_id)
    assert runs == 1 and trigger is not None and trigger.status == "paused"
    assert kit.SENDS == [] and harness.server.discoveries == 0


@pytest.mark.asyncio
async def test_mcp_preparation_failure_fails_trigger_before_marker(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await tkit.seed_trigger(db)
    server = kit.FakeMcpServer(names=("unrelated",))
    harness = tkit.install(monkeypatch, tmp_path, seeded, _scripts(seeded), server)

    await tkit.execute(seeded)

    (run,) = await tkit.trigger_runs(seeded)
    assert run.status == "failed" and run.error_message == "mcp_preparation_failed"
    conversation_run = await tkit.conversation_run(run.conversation_run_id)
    assert conversation_run.status == "failed"
    assert conversation_run.runtime_materialized_at is None
    assert server.discoveries == 1 and kit.SENDS == [] and harness.at_activation == []
    assert get_prepared_run_registry().active_run_ids() == set()


@pytest.mark.asyncio
async def test_unsupported_agent_falls_back_to_legacy_invoke(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await tkit.seed_trigger(db, stored_policy=False)
    harness = tkit.install(monkeypatch, tmp_path, seeded, _scripts(seeded))

    await tkit.execute(seeded)

    assert harness.legacy_invokes == [tkit.INPUT] and harness.at_activation == []
    (run,) = await tkit.trigger_runs(seeded)
    assert (run.status, run.output_preview) == ("success", "legacy output")
    conversation_run = await tkit.conversation_run(run.conversation_run_id)
    assert conversation_run.status == "completed"
    assert conversation_run.runtime_materialized_at is None
    assert kit.SENDS == [] and harness.server.discoveries == 0


@pytest.mark.asyncio
async def test_flag_off_never_enters_the_prepared_entry(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await tkit.seed_trigger(db)
    harness = tkit.install(monkeypatch, tmp_path, seeded, _scripts(seeded))
    monkeypatch.setattr(settings, "prepared_run_lease_enabled", False)

    async def forbidden(*_a: Any, **_k: Any) -> str:
        raise AssertionError("prepared entry used with the flag off")

    monkeypatch.setattr(trigger_service, "run_prepared_trigger_execution", forbidden)
    await tkit.execute(seeded)

    assert harness.legacy_invokes == [tkit.INPUT]
    (run,) = await tkit.trigger_runs(seeded)
    assert run.status == "success"
