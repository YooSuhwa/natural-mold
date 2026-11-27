"""Exercise goal workspace selection through execute_in_skill and its real lease transactions."""

import asyncio
import hashlib
from collections.abc import AsyncGenerator, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from deepagents.backends import StateBackend
from langchain.tools import ToolRuntime
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import runtime_component_builder, skill_executor, skill_sandbox
from app.agent_runtime.goal_sandbox_scope import goal_sandbox_runner_for, goal_sandbox_scope
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.runtime_preparation import prepare_runtime_components_impl
from app.agent_runtime.runtime_preparation_bindings import _runtime_preparation_bindings
from app.agent_runtime.skill_sandbox import SkillRunResult
from app.config import settings
from app.database import tenant_session
from app.dependencies import CurrentUser
from app.marketplace.skill_runtime import (
    ResolvedCredential,
    SkillRuntimeDescriptor,
    SkillToolContext,
)
from app.migrations.org_authz_v1.contracts import ORG, TENANT
from app.models.goal_sandbox import GoalSandbox
from app.services import conversation_run_worker
from app.services import goal_sandbox_leases as leases
from app.services import goal_sandbox_runner as runner
from tests.authz.agent_execution_runtime_fixture import seed_runtime_run
from tests.authz.registration_fixture import seed_registration_default
from tests.authz.run_fixture_config import run_fixture_config
from tests.authz.runtime_prototype import ScriptedModel
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
def worker_run_id() -> Iterator[str]:
    run_id = str(uuid4())
    with goal_sandbox_scope(run_id, runner.run_if_eligible):
        yield run_id


@pytest.fixture
async def skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, db: AsyncSession, worker_run_id: str
) -> SkillRun:
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
        goal_sandbox_run_id=worker_run_id,
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


async def test_worker_capability_is_scoped_by_run_and_lifetime() -> None:
    run_id = str(uuid4())
    started = asyncio.Event()
    check_again = asyncio.Event()

    async def inherited_task() -> None:
        started.set()
        assert goal_sandbox_runner_for(run_id) is runner.run_if_eligible
        assert goal_sandbox_runner_for(str(uuid4())) is None
        assert goal_sandbox_runner_for(None) is None
        await check_again.wait()
        assert goal_sandbox_runner_for(run_id) is None

    with goal_sandbox_scope(run_id, runner.run_if_eligible):
        inherited = asyncio.create_task(inherited_task())
        await started.wait()
    check_again.set()
    await inherited
    assert goal_sandbox_runner_for(run_id) is None


async def test_conversation_worker_supplies_the_current_runs_sandbox_capability() -> None:
    run, _agent, conversation = await seed_runtime_run()
    matching: list[bool] = []

    async def inspect(*_args: Any, **_kwargs: Any) -> AsyncGenerator[str, None]:
        matching.append(goal_sandbox_runner_for(str(run.id)) is runner.run_if_eligible)
        if False:  # a stream with no emitted frames
            yield ""

    registry = conversation_run_worker.RunTaskRegistry(worker_instance_id="sandbox-worker")
    await conversation_run_worker.start_conversation_run(
        run_id=run.id,
        conversation_id=conversation.id,
        cfg=await run_fixture_config(run.id, TestSession),
        user=CurrentUser(id=conversation.user_id, email="owner@test", name="Owner"),
        input_payload={"messages": [{"role": "user", "content": "run"}]},
        moldy_source="chat",
        executor_fn=inspect,
        registry=registry,
    )
    task = registry.get(run.id)
    assert task is not None
    await task
    assert matching == [True]
    assert goal_sandbox_runner_for(str(run.id)) is None


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
    first, second = (call.kwargs for call in skill.open.await_args_list)
    assert first["action"] == "create"
    assert second["action"] == ("resume" if pause else "connect")
    assert first["workspace_id"] == second["workspace_id"]
    assert second["provider_id"] == "test-workspace"
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

        async def revoke_on_open(**_kwargs):
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


@pytest.mark.parametrize("enabled", [False, True], ids=["disabled", "enabled"])
async def test_root_and_inherited_skill_tools_compile_and_dispatch(
    skill: SkillRun, enabled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "goal_sandbox_enabled", enabled)
    assert skill.ctx.goal_sandbox_run_id
    root = skill_executor._create_skill_execute_tool(skill.ctx)

    @tool
    def read_call_id(runtime: ToolRuntime) -> str:
        """Read the tool-call ID the graph assigned."""
        return str(runtime.tool_call_id)

    specs = runtime_component_builder._normalize_declarative_subagents(
        [
            {"name": "inherited", "description": "Task", "system_prompt": "Do the task"},
            {
                "name": "selected",
                "description": "Task",
                "system_prompt": "Do the task",
                "tools": [root, read_call_id],
            },
            {"name": "empty", "description": "Task", "system_prompt": "Do the task", "tools": []},
        ],
        model=FakeListChatModel(responses=["done"]),
        tools=[root, read_call_id],
        backend=StateBackend(),
        permissions=None,
        interrupt_on=None,
        skills=["/skills/"],
    )
    scopes = {spec["name"]: spec["tools"] for spec in specs}
    assert scopes.pop("empty") == []
    assert set(scopes) == {"general-purpose", "inherited", "selected"}
    # Dispatch children first to prove they cannot create the parent's private workspace.
    scopes["root"] = [root, read_call_id]
    for name, tools in scopes.items():
        execute, injected_tool = tools
        assert execute.name == "execute_in_skill"
        assert execute.metadata == root.metadata
        assert set(execute.tool_call_schema.model_json_schema()["properties"]) == {
            "skill_directory",
            "command",
        }
        assert injected_tool is read_call_id
        assert "runtime" not in injected_tool.tool_call_schema.model_json_schema()["properties"]
        graph = StateGraph(MessagesState)
        graph.add_node("tools", ToolNode(tools))
        graph.add_edge(START, "tools")
        call_id = f"{name}-injected"
        request = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": execute.name,
                    "args": {
                        "skill_directory": "demo",
                        "command": "python scripts/run.py",
                        "ctx": {"goal_sandbox_run_id": skill.ctx.goal_sandbox_run_id},
                    },
                    "id": f"{name}-skill",
                },
                {"name": injected_tool.name, "args": {}, "id": call_id},
            ],
        )
        result = await graph.compile().ainvoke({"messages": [request]})
        messages = {m.tool_call_id: m for m in result["messages"] if isinstance(m, ToolMessage)}
        assert messages[call_id].content == call_id
        if name == "root" and enabled:
            assert "finished" in messages[f"{name}-skill"].content
            assert await skill.workspace() is not None
        else:
            assert messages[f"{name}-skill"].content == "ordinary"
            skill.resolve.assert_not_awaited()
            assert await skill.workspace() is None


async def test_task_delegates_to_general_purpose_without_root_workspace(skill: SkillRun) -> None:
    def call(name: str, arguments: dict[str, str], call_id: str) -> AIMessage:
        return AIMessage(content="", tool_calls=[{"name": name, "args": arguments, "id": call_id}])

    args = {"skill_directory": "demo", "command": "python scripts/run.py"}
    model = ScriptedModel(
        responses=[
            call(
                "task",
                {"subagent_type": "general-purpose", "description": "Use the demo skill"},
                "delegate",
            ),
            call("execute_in_skill", args, "child-skill"),
            AIMessage(content="child done"),
            call("execute_in_skill", args, "root-skill"),
            AIMessage(content="root done"),
        ]
    )
    graph = runtime_component_builder.build_agent(model, [skill.tool], "Use the demo skill")
    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "Delegate the first step"}]}
    )
    assert result["messages"][-1].content == "root done"
    replies = {m.tool_call_id: m.content for m in result["messages"] if isinstance(m, ToolMessage)}
    assert "child done" in replies["delegate"]
    assert "finished" in replies["root-skill"]
    skill.ephemeral.assert_awaited_once()
    skill.command.assert_awaited_once()
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
