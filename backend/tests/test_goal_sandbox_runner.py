"""Exercise goal workspace selection through execute_in_skill and its real lease transactions."""

import asyncio
import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from deepagents.backends import StateBackend
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.tools import BaseTool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import goal_sandbox_runner as runner
from app.agent_runtime import runtime_component_builder, skill_executor, skill_sandbox
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.runtime_preparation import prepare_runtime_components_impl
from app.agent_runtime.runtime_preparation_bindings import _runtime_preparation_bindings
from app.agent_runtime.skill_sandbox import SkillRunResult
from app.config import settings
from app.database import tenant_session
from app.marketplace.skill_runtime import (
    ResolvedCredential,
    SkillRuntimeDescriptor,
    SkillToolContext,
)
from app.migrations.org_authz_v1.contracts import ORG, TENANT
from app.models.goal_sandbox import GoalSandbox
from app.services import goal_sandbox_leases as leases
from tests.authz.registration_fixture import seed_registration_default
from tests.authz.unit_scope_fixture import unit_context
from tests.sqlite_test_engine import TestSession


@dataclass
class SkillRun:
    tool: BaseTool
    ctx: SkillToolContext
    descriptor: SkillRuntimeDescriptor
    scope: leases.SandboxScope
    resolve: AsyncMock
    index_outputs: AsyncMock
    open: AsyncMock
    command: AsyncMock
    ephemeral: AsyncMock
    close: AsyncMock

    async def invoke(self) -> str:
        return await self.tool.ainvoke(
            {"skill_directory": "/skills/demo", "command": "python scripts/run.py"}
        )

    async def workspace(self) -> GoalSandbox | None:
        async with TestSession() as db:
            return await db.scalar(
                select(GoalSandbox).where(GoalSandbox.scope_hash == self.scope.fingerprint)
            )


CONTENT = b"finished"
MANIFEST = {"result.txt": hashlib.sha256(CONTENT).hexdigest()}


@pytest.fixture
async def skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, db: AsyncSession) -> SkillRun:
    await seed_registration_default(db)
    await db.commit()
    directory = tmp_path / "runtime" / "demo"
    (directory / "scripts").mkdir(parents=True)
    (directory / "scripts" / "run.py").write_text("print('finished')")
    descriptor = SkillRuntimeDescriptor(uuid4(), "demo", "Demo", "", directory, directory)
    skill_hash = runner.skill_tree_digest(skill_sandbox.read_skill_tree(directory, 1000))
    scope = leases.SandboxScope(
        tenant_id=TENANT,
        org_id=ORG,
        user_id=uuid4(),
        conversation_id=uuid4(),
        goal_id=uuid4(),
        goal_version=1,
        agent_id=uuid4(),
        skill_id=descriptor.id,
        policy_version=1,
        policy_hash="1" * 64,
        skill_hash=skill_hash,
        image="sha256:test",
    )
    ctx = SkillToolContext(
        thread_id=str(scope.conversation_id),
        tenant_id=TENANT,
        user_id=scope.user_id,
        runtime_root=directory.parent,
        output_dir=tmp_path / "out",
        descriptors={"demo": descriptor},
        goal_sandbox_run_id=str(uuid4()),
    )
    monkeypatch.setattr(settings, "goal_sandbox_enabled", True)
    monkeypatch.setattr(settings, "skill_sandbox_enabled", True)
    monkeypatch.setattr(
        runner, "tenant_session", lambda actor: tenant_session(actor, maker=TestSession)
    )
    close = AsyncMock()
    opened = AsyncMock(return_value=SimpleNamespace(id="test-workspace", close=close))
    resolve = AsyncMock(return_value=scope)
    command = AsyncMock(return_value=SkillRunResult("finished", "", 0))
    ephemeral = AsyncMock(return_value=SkillRunResult("ordinary", "", 0))
    index_outputs = AsyncMock()
    monkeypatch.setattr(runner, "index_goal_outputs", index_outputs)
    monkeypatch.setattr(runner, "resolve_goal_sandbox_scope", resolve)
    monkeypatch.setattr(runner.provider, "open_workspace", opened)
    monkeypatch.setattr(runner.provider, "prepare_workspace", AsyncMock())
    monkeypatch.setattr(
        runner.provider, "collect_outputs", AsyncMock(return_value={"result.txt": CONTENT})
    )
    monkeypatch.setattr(skill_sandbox, "run_command", command)
    monkeypatch.setattr(skill_sandbox, "run_in_sandbox", ephemeral)
    monkeypatch.setattr(skill_executor, "record_credential_audits", AsyncMock())
    monkeypatch.setattr(skill_executor, "record_sandbox_run", AsyncMock())
    monkeypatch.setattr(
        "app.services.skill_usage_service.record_chat_execution_nonfatal", AsyncMock()
    )
    return SkillRun(
        skill_executor._create_skill_execute_tool(ctx),
        ctx,
        descriptor,
        scope,
        resolve,
        index_outputs,
        opened,
        command,
        ephemeral,
        close,
    )


@pytest.mark.parametrize("mode", ["disabled", "no_goal_run", "network", "credential", "unprofiled"])
async def test_unsuitable_skills_use_ephemeral_sandbox(
    skill: SkillRun, mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if mode == "disabled":
        monkeypatch.setattr(settings, "goal_sandbox_enabled", False)
    elif mode == "no_goal_run":
        skill.ctx.goal_sandbox_run_id = None
    elif mode == "network":
        skill.descriptor.execution_profile = {"requires_network": True}
    elif mode == "credential":
        skill.descriptor.credential_bindings["bound"] = ResolvedCredential(
            uuid4(), "test", {"key": "TEST_KEY"}, {"key": "synthetic-fixture"}
        )
    else:
        skill.resolve.return_value = None
    assert await skill.invoke() == "ordinary"
    skill.ephemeral.assert_awaited_once()
    skill.command.assert_not_awaited()
    skill.open.assert_not_awaited()
    if mode != "unprofiled":
        skill.resolve.assert_not_awaited()
    assert await skill.workspace() is None


@pytest.mark.parametrize("pause", [False, True])
async def test_followup_uses_same_provider_workspace_after_idle(
    skill: SkillRun, pause: bool
) -> None:
    assert "finished" in await skill.invoke()
    if pause:
        async with TestSession() as db:
            now = datetime.now(UTC)
            claim = await leases.claim_maintenance(
                db,
                TENANT,
                idle_before=now,
                expire_before=now - timedelta(days=1),
                ttl=timedelta(minutes=2),
            )
            assert claim and claim.action == "pause"
            assert await leases.complete_maintenance(db, claim)
            await db.commit()
    assert "finished" in await skill.invoke()
    first, second = (args.args[0] for args in skill.open.await_args_list)
    assert first.action == "create"
    assert second.action == ("resume" if pause else "connect")
    assert first.workspace_id == second.workspace_id
    assert second.provider_id == "test-workspace"
    assert all(
        args.args[2]["MOLDY_GOAL_WORK_DIR"] == runner.provider.WORK_DIR
        for args in skill.command.await_args_list
    )
    assert skill.index_outputs.await_count == 2
    assert skill.index_outputs.await_args.kwargs["run_id"] == UUID(skill.ctx.goal_sandbox_run_id)
    assert skill.index_outputs.await_args.kwargs["manifest"] == MANIFEST
    assert skill.close.await_count == 2
    assert (skill.ctx.output_dir / "result.txt").read_bytes() == CONTENT
    record = await skill.workspace()
    assert record and record.state == "ready" and record.receipt == MANIFEST


@pytest.mark.parametrize("boundary", ["after_open", "after_upload", "before_publish"])
async def test_changed_authorization_never_retries_on_other_runtime(
    skill: SkillRun, boundary: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if boundary == "after_open":

        async def revoke_on_open(*_args):
            skill.resolve.return_value = None
            return SimpleNamespace(id="test-workspace", close=skill.close)

        skill.open.side_effect = revoke_on_open
    elif boundary == "after_upload":

        async def revoke_on_upload(*args, **kwargs):
            skill.resolve.return_value = None

        monkeypatch.setattr(runner.provider, "prepare_workspace", revoke_on_upload)
    else:

        async def revoke_on_collect(*_args):
            skill.resolve.return_value = None
            return {"result.txt": CONTENT}

        monkeypatch.setattr(runner.provider, "collect_outputs", revoke_on_collect)
    assert "authorization" in await skill.invoke()
    skill.ephemeral.assert_not_awaited()
    skill.close.assert_awaited_once()
    assert skill.command.await_count == (1 if boundary == "before_publish" else 0)
    assert not (skill.ctx.output_dir / "result.txt").exists()
    record = await skill.workspace()
    assert record and record.state == "needs_recovery"
    assert record.pending_manifest == (MANIFEST if boundary == "before_publish" else None)


async def test_failed_goal_command_is_not_replayed(skill: SkillRun) -> None:
    skill.command.return_value = SkillRunResult("", "script failed", 1)
    assert "script failed" in await skill.invoke()
    assert "in use" in await skill.invoke()
    skill.command.assert_awaited_once()
    skill.ephemeral.assert_not_awaited()
    record = await skill.workspace()
    assert record and record.state == "needs_recovery" and record.pending_manifest is None


async def test_concurrent_writer_is_fenced_and_cancellation_marks_uncertain(
    skill: SkillRun,
) -> None:
    executing = asyncio.Event()

    async def wait_for_cancel(*_args):
        executing.set()
        await asyncio.Event().wait()

    skill.command.side_effect = wait_for_cancel
    active = asyncio.create_task(skill.invoke())
    try:
        await asyncio.wait_for(executing.wait(), timeout=5)
        assert "in use" in await skill.invoke()
    finally:
        active.cancel()
        with pytest.raises(asyncio.CancelledError):
            await active
    skill.command.assert_awaited_once()
    skill.ephemeral.assert_not_awaited()
    skill.close.assert_awaited_once()
    record = await skill.workspace()
    assert record and record.state == "needs_recovery" and record.pending_manifest is None


async def test_cancel_after_durable_manifest_preserves_it(skill: SkillRun) -> None:
    async def cancelled(*args, **kwargs):
        if kwargs.get("in_flight") is not None:
            raise asyncio.CancelledError
        return skill.scope

    skill.resolve.side_effect = cancelled
    with pytest.raises(asyncio.CancelledError):
        await skill.invoke()
    record = await skill.workspace()
    assert record and record.state == "needs_recovery" and record.pending_manifest == MANIFEST
    assert not (skill.ctx.output_dir / "result.txt").exists()
    skill.ephemeral.assert_not_awaited()


async def test_existing_user_output_is_never_replaced_by_goal(skill: SkillRun) -> None:
    skill.ctx.output_dir.mkdir()
    result = skill.ctx.output_dir / "result.txt"
    result.write_bytes(b"newer user output")
    assert "already in use" in await skill.invoke()
    assert result.read_bytes() == b"newer user output"
    record = await skill.workspace()
    assert record and record.state == "needs_recovery" and record.pending_manifest == MANIFEST
    skill.ephemeral.assert_not_awaited()


async def test_explicit_and_inherited_children_do_not_receive_root_goal_workspaces(
    skill: SkillRun,
) -> None:
    specs = runtime_component_builder._normalize_declarative_subagents(
        [
            {"name": "inherited", "description": "Task", "system_prompt": "Do the task"},
            {
                "name": "selected",
                "description": "Task",
                "system_prompt": "Do the task",
                "tools": [skill.tool],
            },
            {"name": "empty", "description": "Task", "system_prompt": "Do the task", "tools": []},
        ],
        model=FakeListChatModel(responses=["done"]),
        tools=[skill.tool],
        backend=StateBackend(),
        permissions=None,
        interrupt_on=None,
        skills=["/skills/"],
    )
    tools = {spec["name"]: spec["tools"] for spec in specs}
    assert tools["empty"] == []
    for name in ["general-purpose", "inherited", "selected"]:
        child = tools[name][0]
        assert child.name == "execute_in_skill"
        assert child.metadata == skill.tool.metadata
        assert child.args == skill.tool.args
        assert (
            await child.ainvoke({"skill_directory": "demo", "command": "python scripts/run.py"})
            == "ordinary"
        )
    skill.resolve.assert_not_awaited()
    assert await skill.workspace() is None
    assert "finished" in await skill.invoke()
    assert await skill.workspace() is not None


@pytest.mark.parametrize(
    ("run_path", "is_trigger", "child", "eligible"),
    [
        ("chat", False, False, True),
        ("chat", False, True, False),
        ("api", False, False, False),
        ("trigger", True, False, False),
        ("chat", True, False, False),
        ("workflow", False, False, False),
    ],
)
async def test_runtime_build_only_binds_workspace_to_ordinary_named_root(
    skill: SkillRun, run_path: str, is_trigger: bool, child: bool, eligible: bool
) -> None:
    cfg = AgentConfig(
        provider="test",
        model_name="test",
        api_key=None,
        base_url=None,
        system_prompt="",
        tools_config=[],
        thread_id=skill.ctx.thread_id,
        org_context=unit_context(skill.scope.user_id),
        agent_runtime_name="named-agent",
        agent_skills=[{"slug": "demo"}],
        run_path=run_path,
        usage_root_agent_id=str(uuid4()) if child else None,
    )

    class SkillBound(Exception):
        pass

    def capture(ctx):
        assert ctx is skill.ctx
        raise SkillBound

    skill.ctx.goal_sandbox_run_id = None
    model = FakeListChatModel(responses=["done"])
    bindings = replace(
        _runtime_preparation_bindings(),
        build_model_candidates=lambda cfg: [model],
        build_skill_dependency_tool_configs=lambda **kwargs: [],
        memory_write_policy_for_run=AsyncMock(return_value="off"),
        build_skill_runtime_context=lambda *args, **kwargs: skill.ctx,
        create_skill_execute_tool=capture,
    )
    run_id = str(uuid4())
    with pytest.raises(SkillBound):
        await prepare_runtime_components_impl(
            cfg,
            is_trigger_mode=is_trigger,
            include_ask_user=False,
            include_agent_memory_file=False,
            run_id=run_id,
            bindings=bindings,
        )
    assert skill.ctx.goal_sandbox_run_id == (run_id if eligible else None)
