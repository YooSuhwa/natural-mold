"""Tests for app.agent_runtime.executor — agent building and stream orchestration."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

from app.agent_runtime.offload_storage import ScopedOffloadBackend
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.runtime_policy import (
    ASSISTANT_RUNTIME_POLICY,
    LEGACY_RUNTIME_POLICY,
    SKILL_BUILDER_RUNTIME_POLICY,
    ResolvedRuntimePolicy,
    resolve_runtime_policy,
)
from app.agent_runtime.streaming import StreamErrorRecord
from app.marketplace.skill_runtime import SkillToolContext
from app.tools.risk import default_deepagents_interrupt_policy
from tests.tool_helpers import tool_coroutine

TEMPORAL_TOOL_NAMES = {"current_datetime", "resolve_relative_date"}


def _expected_interrupt_policy() -> dict:
    return {
        **default_deepagents_interrupt_policy(),
        "ask_user": {"allowed_decisions": ["respond"]},
    }


def test_stored_interrupt_filter_preserves_unrelated_hitl_entries() -> None:
    from app.agent_runtime.runtime_preparation import _without_stored_filesystem_interrupts

    result = _without_stored_filesystem_interrupts(
        {
            "write_file": True,
            "edit_file": True,
            "execute": True,
            "delete": True,
            "shell": True,
            "execute_in_skill": True,
            "ask_user": {"allowed_decisions": ["respond"]},
            "publish_record": {"allowed_decisions": ["approve", "reject"]},
            "read_file": True,
        }
    )

    assert result == {
        "ask_user": {"allowed_decisions": ["respond"]},
        "publish_record": {"allowed_decisions": ["approve", "reject"]},
        "read_file": True,
    }


def _cfg(**overrides) -> AgentConfig:
    """테스트용 AgentConfig 기본값 생성."""
    defaults: dict[str, object] = {
        "provider": "openai",
        "model_name": "gpt-4o",
        "api_key": None,
        "base_url": None,
        "system_prompt": "Hi",
        "tools_config": [],
        "thread_id": "t-1",
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)  # type: ignore[arg-type]


def _stored_policy(mode: str, *, todo_enabled: bool = True):
    return resolve_runtime_policy(
        {
            "version": 1,
            "filesystem": {"mode": mode},
            "todo": {"enabled": todo_enabled},
        }
    )


def _deep_research_skill() -> dict[str, object]:
    return {
        "id": "00000000-0000-0000-0000-0000000000d1",
        "slug": "deep-research",
        "name": "Deep Research",
        "description": "Deep research",
        "storage_path": "skills/deep-research",
        "execution_profile": {"tool_dependencies": ["tavily_search"]},
    }


def _stub_skill_context(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "app.agent_runtime.runtime_component_builder.build_skill_runtime_context",
        lambda *_args, **_kwargs: SkillToolContext(
            thread_id="t-1",
            output_dir=tmp_path / "outputs",
            runtime_root=tmp_path / "runtime",
            descriptors={},
        ),
    )


def _capture_runtime_tool_configs(captured: list[dict[str, object]]):
    def _factory(config: dict[str, object]):
        captured.append(dict(config))
        tool = MagicMock()
        tool.name = str(config.get("name"))
        return tool

    return _factory


# ---------------------------------------------------------------------------
# build_agent
# ---------------------------------------------------------------------------


@patch("app.agent_runtime.runtime_component_builder.create_deep_agent")
def test_build_agent_calls_deep_agent(mock_create: MagicMock):
    from deepagents.backends import StateBackend

    from app.agent_runtime.runtime_component_builder import (
        _MOLDY_FILESYSTEM_TOOL_NAMES,
        build_agent,
    )

    mock_model = MagicMock()
    mock_tools = [MagicMock(), MagicMock()]

    build_agent(mock_model, mock_tools, "You are helpful.")  # type: ignore[arg-type]

    call = mock_create.call_args.kwargs
    assert call["model"] is mock_model
    assert call["tools"] is mock_tools
    assert call["system_prompt"] == "You are helpful."
    assert isinstance(call["backend"], StateBackend)
    assert [item.name for item in call["middleware"]] == [
        "FilesystemMiddleware",
        "TodoListMiddleware",
    ]
    filesystem = call["middleware"][0]
    assert tuple(tool.name for tool in filesystem.tools) == _MOLDY_FILESYSTEM_TOOL_NAMES
    assert "delete" not in {tool.name for tool in filesystem.tools}
    assert call["interrupt_on"] is None
    assert call["checkpointer"] is None
    assert call["store"] is None
    assert call["skills"] is None
    assert call["memory"] is None
    assert call["permissions"] is None
    assert call["name"] is None

    # 0.7 no longer auto-adds todo support. Moldy provides an equivalent
    # general-purpose declarative spec so task-mode keeps the same contract.
    subagents = call["subagents"]
    assert [spec["name"] for spec in subagents] == ["general-purpose"]
    assert [item.name for item in subagents[0]["middleware"]] == [
        "FilesystemMiddleware",
        "TodoListMiddleware",
    ]
    assert subagents[0]["middleware"][0].backend is call["backend"]


@patch("app.agent_runtime.runtime_component_builder.create_deep_agent")
def test_build_agent_forwards_subagents_to_deep_agents(mock_create: MagicMock):
    from app.agent_runtime.runtime_component_builder import build_agent

    subagents = [
        {
            "name": "agent_abcd1234",
            "description": "helper",
            "system_prompt": "help the parent",
        }
    ]

    build_agent(
        MagicMock(),
        [],
        "You are helpful.",
        name="agent_parent12",
        subagents=subagents,
    )  # type: ignore[arg-type]

    assert mock_create.call_args.kwargs["name"] == "agent_parent12"
    normalized = mock_create.call_args.kwargs["subagents"]
    assert [spec["name"] for spec in normalized] == ["general-purpose", "agent_abcd1234"]
    assert normalized[1] is not subagents[0]
    assert normalized[1]["description"] == "helper"
    assert [item.name for item in normalized[1]["middleware"]] == [
        "FilesystemMiddleware",
        "TodoListMiddleware",
    ]


@patch("app.agent_runtime.runtime_component_builder.create_deep_agent")
def test_build_agent_passes_skills_and_memory(mock_create: MagicMock):
    from deepagents.backends import StateBackend

    from app.agent_runtime.runtime_component_builder import build_agent

    mock_model = MagicMock()
    mock_backend = StateBackend()

    build_agent(
        mock_model,
        [],
        "prompt",
        backend=mock_backend,
        skills=["/skills/"],
        memory=["/agents/abc/AGENTS.md"],
    )

    call_kwargs = mock_create.call_args[1]
    assert call_kwargs["skills"] == ["/skills/"]
    assert call_kwargs["memory"] == ["/agents/abc/AGENTS.md"]
    assert call_kwargs["backend"] is mock_backend
    assert call_kwargs["middleware"][0].backend is mock_backend
    general_purpose = call_kwargs["subagents"][0]
    assert general_purpose["name"] == "general-purpose"
    assert general_purpose["skills"] == ["/skills/"]
    assert general_purpose["tools"] == []


@patch("app.agent_runtime.runtime_component_builder.create_deep_agent")
def test_build_agent_passes_permissions(mock_create: MagicMock):
    from deepagents.middleware.filesystem import FilesystemPermission

    from app.agent_runtime.runtime_component_builder import build_agent

    permissions = [
        FilesystemPermission(
            operations=["read"],
            paths=["/runtime/t-1/skills/**"],
        )
    ]

    build_agent(MagicMock(), [], "prompt", permissions=permissions)

    call_kwargs = mock_create.call_args[1]
    assert call_kwargs["permissions"] == permissions
    assert call_kwargs["middleware"][0]._permissions == permissions
    assert call_kwargs["subagents"][0]["permissions"] == permissions


@patch("app.agent_runtime.runtime_component_builder.create_deep_agent")
def test_build_agent_replaces_duplicate_compatibility_middleware_and_uses_child_permissions(
    mock_create: MagicMock,
):
    from deepagents.backends import StateBackend
    from deepagents.middleware.filesystem import FilesystemMiddleware, FilesystemPermission
    from langchain.agents.middleware import TodoListMiddleware

    from app.agent_runtime.runtime_component_builder import build_agent

    parent_permissions = [FilesystemPermission(operations=["read"], paths=["/parent/**"])]
    child_permissions = [FilesystemPermission(operations=["read"], paths=["/child/**"])]
    backend = StateBackend()
    retained = MagicMock()
    retained.name = "RetainedMiddleware"
    incoming = [
        FilesystemMiddleware(backend=backend, tools=["read_file"]),
        TodoListMiddleware(),
        TodoListMiddleware(),
        retained,
    ]
    subagents = [
        {
            "name": "child",
            "description": "child helper",
            "system_prompt": "help",
            "permissions": child_permissions,
            "middleware": incoming,
        },
        {
            "name": "inherited-child",
            "description": "inherits parent filesystem policy",
            "system_prompt": "help",
        },
    ]

    build_agent(
        MagicMock(),
        [],
        "prompt",
        backend=backend,
        middleware=incoming,
        permissions=parent_permissions,
        subagents=subagents,
    )

    call = mock_create.call_args.kwargs
    assert [item.name for item in call["middleware"]] == [
        "FilesystemMiddleware",
        "TodoListMiddleware",
        "RetainedMiddleware",
    ]
    assert call["middleware"][0].backend is backend
    assert call["middleware"][0]._permissions == parent_permissions
    child = next(spec for spec in call["subagents"] if spec["name"] == "child")
    assert [item.name for item in child["middleware"]] == [
        "FilesystemMiddleware",
        "TodoListMiddleware",
        "RetainedMiddleware",
    ]
    assert child["middleware"][0].backend is backend
    assert child["middleware"][0]._permissions == child_permissions
    inherited_child = next(spec for spec in call["subagents"] if spec["name"] == "inherited-child")
    assert inherited_child["middleware"][0]._permissions == parent_permissions
    # Caller-owned declarative specs and middleware lists are never modified.
    assert subagents[0]["middleware"] is incoming


@patch("app.agent_runtime.runtime_component_builder.create_deep_agent")
def test_build_agent_normalizes_explicit_general_purpose_without_duplicate(
    mock_create: MagicMock,
):
    from app.agent_runtime.runtime_component_builder import build_agent

    explicit_general_purpose = {
        "name": "general-purpose",
        "description": "Custom description",
        "system_prompt": "Custom prompt",
    }
    build_agent(MagicMock(), [], "prompt", subagents=[explicit_general_purpose])

    specs = mock_create.call_args.kwargs["subagents"]
    assert [spec["name"] for spec in specs] == ["general-purpose"]
    assert specs[0] is not explicit_general_purpose
    assert specs[0]["description"] == "Custom description"
    assert specs[0]["system_prompt"] == "Custom prompt"
    assert [item.name for item in specs[0]["middleware"]] == [
        "FilesystemMiddleware",
        "TodoListMiddleware",
    ]


@patch("app.agent_runtime.runtime_component_builder.create_deep_agent")
def test_build_agent_leaves_compiled_and_async_subagents_unchanged(mock_create: MagicMock):
    from app.agent_runtime.runtime_component_builder import build_agent

    compiled = {"name": "compiled", "description": "compiled", "runnable": MagicMock()}
    asynchronous = {"name": "remote", "description": "remote", "graph_id": "remote-graph"}
    build_agent(MagicMock(), [], "prompt", subagents=[compiled, asynchronous])

    specs = mock_create.call_args.kwargs["subagents"]
    assert next(spec for spec in specs if spec["name"] == "compiled") is compiled
    assert next(spec for spec in specs if spec["name"] == "remote") is asynchronous


@pytest.mark.parametrize(
    ("mode", "expected_tools"),
    [
        ("inspect", ("ls", "read_file", "glob", "grep")),
        (
            "artifact_write",
            ("ls", "read_file", "write_file", "edit_file", "glob", "grep"),
        ),
    ],
)
@patch("app.agent_runtime.runtime_component_builder.create_deep_agent")
def test_build_agent_limits_stored_filesystem_profiles(
    mock_create: MagicMock,
    mode: str,
    expected_tools: tuple[str, ...],
) -> None:
    from app.agent_runtime.runtime_component_builder import build_agent

    reserved_tools = []
    for name in ("read_file", "write_file", "task", "write_todos"):
        tool = MagicMock()
        tool.name = name
        reserved_tools.append(tool)
    safe_tool = MagicMock()
    safe_tool.name = "safe_search"

    build_agent(
        MagicMock(),
        [*reserved_tools, safe_tool],
        "prompt",
        runtime_policy=_stored_policy(mode),
    )

    call = mock_create.call_args.kwargs
    assert call["tools"] == [safe_tool]
    assert tuple(tool.name for tool in call["middleware"][0].tools) == expected_tools
    child_filesystem = call["subagents"][0]["middleware"][0]
    assert tuple(tool.name for tool in child_filesystem.tools) == expected_tools


@pytest.mark.parametrize(
    ("todo_enabled", "expected_middleware_names"),
    [
        (True, ["FilesystemMiddleware", "TodoListMiddleware"]),
        (False, ["FilesystemMiddleware"]),
    ],
)
@patch("app.agent_runtime.runtime_component_builder.create_deep_agent")
def test_build_agent_applies_stored_todo_policy_to_parent_and_children(
    mock_create: MagicMock,
    todo_enabled: bool,
    expected_middleware_names: list[str],
) -> None:
    """Stored Todo policy gates every declarative Deep Agents compatibility stack."""
    from app.agent_runtime.runtime_component_builder import build_agent

    colliding_todo_tool = MagicMock()
    colliding_todo_tool.name = "write_todos"
    safe_tool = MagicMock()
    safe_tool.name = "safe_search"

    build_agent(
        MagicMock(),
        [colliding_todo_tool, safe_tool],
        "prompt",
        subagents=[
            {
                "name": "custom-child",
                "description": "helper",
                "system_prompt": "help",
                "tools": [colliding_todo_tool, safe_tool],
            }
        ],
        runtime_policy=_stored_policy("artifact_write", todo_enabled=todo_enabled),
    )

    call = mock_create.call_args.kwargs
    assert call["tools"] == [safe_tool]
    assert [item.name for item in call["middleware"]] == expected_middleware_names
    for child in call["subagents"]:
        assert [item.name for item in child["middleware"]] == expected_middleware_names
        assert [tool.name for tool in child["tools"]] == ["safe_search"]
    custom_child = next(spec for spec in call["subagents"] if spec["name"] == "custom-child")
    assert custom_child["tools"] == [safe_tool]


@pytest.mark.parametrize("todo_enabled", [True, False])
def test_build_agent_compiles_stored_todo_policy(todo_enabled: bool) -> None:
    """The direct graph exposes Todo state exactly when the stored policy enables it."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from app.agent_runtime.runtime_component_builder import build_agent

    agent = build_agent(
        FakeListChatModel(responses=["done"]),
        [],
        "prompt",
        runtime_policy=_stored_policy("artifact_write", todo_enabled=todo_enabled),
    )

    assert ("TodoListMiddleware.after_model" in agent.nodes) is todo_enabled


@pytest.mark.parametrize(
    "runtime_policy",
    [LEGACY_RUNTIME_POLICY, ASSISTANT_RUNTIME_POLICY, SKILL_BUILDER_RUNTIME_POLICY],
)
@patch("app.agent_runtime.runtime_component_builder.create_deep_agent")
def test_build_agent_preserves_nonstored_filesystem_manifest(
    mock_create: MagicMock,
    runtime_policy: ResolvedRuntimePolicy,
) -> None:
    from app.agent_runtime.runtime_component_builder import (
        _MOLDY_FILESYSTEM_TOOL_NAMES,
        build_agent,
    )

    build_agent(MagicMock(), [], "prompt", runtime_policy=runtime_policy)

    call = mock_create.call_args.kwargs
    assert tuple(tool.name for tool in call["middleware"][0].tools) == (
        _MOLDY_FILESYSTEM_TOOL_NAMES
    )
    assert [item.name for item in call["middleware"]] == [
        "FilesystemMiddleware",
        "TodoListMiddleware",
    ]
    assert [item.name for item in call["subagents"][0]["middleware"]] == [
        "FilesystemMiddleware",
        "TodoListMiddleware",
    ]


@pytest.mark.parametrize(
    ("opaque_key", "opaque_value"),
    [("runnable", None), ("graph_id", "")],
)
@patch("app.agent_runtime.runtime_component_builder.create_deep_agent")
def test_build_agent_rejects_opaque_subagents_for_stored_policy(
    mock_create: MagicMock,
    opaque_key: str,
    opaque_value: object,
) -> None:
    from app.agent_runtime.runtime_component_builder import build_agent
    from app.agent_runtime.runtime_policy_capabilities import RestrictedSubagentSpecError

    with pytest.raises(RestrictedSubagentSpecError) as exc_info:
        build_agent(
            MagicMock(),
            [],
            "prompt",
            subagents=[{"name": "opaque", "description": "opaque", opaque_key: opaque_value}],
            runtime_policy=_stored_policy("inspect"),
        )

    assert str(exc_info.value) == "RESTRICTED_SUBAGENT_SPEC_UNSUPPORTED"
    mock_create.assert_not_called()


@pytest.mark.asyncio
@patch("app.agent_runtime.runtime_component_builder.create_deep_agent")
async def test_stored_build_agent_without_permissions_denies_filesystem_reads(
    mock_create: MagicMock,
) -> None:
    from app.agent_runtime.runtime_component_builder import build_agent

    build_agent(MagicMock(), [], "prompt", runtime_policy=_stored_policy("inspect"))

    call = mock_create.call_args.kwargs
    filesystem = call["middleware"][0]
    read_file_tool = next(tool for tool in filesystem.tools if tool.name == "read_file")
    read_file = tool_coroutine(read_file_tool)
    result = await read_file(
        file_path="/conversations/other-thread/private.txt",
        runtime=SimpleNamespace(tool_call_id="call-denied"),
    )
    assert result.status == "error"
    assert result.content == "Error: filesystem permission denied"
    assert call["permissions"] == filesystem._permissions
    assert call["subagents"][0]["permissions"] == call["permissions"]


@pytest.mark.asyncio
@pytest.mark.parametrize("permission_mode", ["allow", "interrupt"])
@patch("app.agent_runtime.runtime_component_builder.create_deep_agent")
async def test_stored_build_agent_rejects_noncanonical_parent_permissions(
    mock_create: MagicMock,
    permission_mode: Literal["allow", "interrupt"],
) -> None:
    from deepagents.middleware.filesystem import FilesystemPermission

    from app.agent_runtime.runtime_component_builder import build_agent

    build_agent(
        MagicMock(),
        [],
        "prompt",
        permissions=[
            FilesystemPermission(
                operations=["read", "write"],
                paths=["/**"],
                mode=permission_mode,
            )
        ],
        runtime_policy=_stored_policy("artifact_write"),
    )

    call = mock_create.call_args.kwargs
    filesystem = call["middleware"][0]
    runtime = SimpleNamespace(tool_call_id="call-malformed-parent")
    read_file = tool_coroutine(next(tool for tool in filesystem.tools if tool.name == "read_file"))
    write_file = tool_coroutine(
        next(tool for tool in filesystem.tools if tool.name == "write_file")
    )
    read_result = await read_file(file_path="/unknown/private.txt", runtime=runtime)
    write_result = await write_file(
        file_path="/unknown/private.txt",
        content="blocked",
        runtime=runtime,
    )
    assert read_result.content == "Error: filesystem permission denied"
    assert write_result.content == "Error: filesystem permission denied"
    assert call["permissions"] == filesystem._permissions
    assert len(call["permissions"]) == 1
    assert call["permissions"][0].mode == "deny"
    assert call["permissions"][0].paths == ["/**"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tampering", ["copy", "reorder"])
@patch("app.agent_runtime.runtime_component_builder.create_deep_agent")
async def test_stored_build_agent_rejects_unattested_or_mutated_parent_permissions(
    mock_create: MagicMock,
    tampering: Literal["copy", "reorder"],
) -> None:
    from app.agent_runtime.runtime_component_builder import build_agent
    from app.agent_runtime.runtime_policy_capabilities import build_stored_filesystem_permissions

    permissions = build_stored_filesystem_permissions(
        thread_id="t-1",
        agent_id="agent-a",
        user_id="user-a",
        selected_skill_slugs=["selected"],
        agent_runtime_name=None,
        include_agent_memory_file=False,
        mode="artifact_write",
    )
    if tampering == "copy":
        candidate = list(permissions)
    else:
        permissions[0], permissions[1] = permissions[1], permissions[0]
        candidate = permissions

    build_agent(
        MagicMock(),
        [],
        "prompt",
        permissions=candidate,
        runtime_policy=_stored_policy("artifact_write"),
    )

    call = mock_create.call_args.kwargs
    filesystem = call["middleware"][0]
    read_file = tool_coroutine(next(tool for tool in filesystem.tools if tool.name == "read_file"))
    result = await read_file(
        file_path="/conversations/t-1/private.txt",
        runtime=SimpleNamespace(tool_call_id="call-tampered-parent"),
    )
    assert result.content == "Error: filesystem permission denied"
    assert len(call["permissions"]) == 1
    assert call["permissions"][0].mode == "deny"
    assert call["permissions"][0].paths == ["/**"]


@patch("app.agent_runtime.runtime_component_builder.create_deep_agent")
def test_stored_build_agent_preserves_canonical_skill_memory_and_output_permissions(
    mock_create: MagicMock,
) -> None:
    from deepagents.middleware.filesystem import _check_fs_permission

    from app.agent_runtime.runtime_component_builder import build_agent
    from app.agent_runtime.runtime_policy_capabilities import build_stored_filesystem_permissions

    permissions = build_stored_filesystem_permissions(
        thread_id="t-1",
        agent_id="agent-a",
        user_id="user-a",
        selected_skill_slugs=["selected"],
        agent_runtime_name=None,
        include_agent_memory_file=True,
        mode="artifact_write",
    )
    build_agent(
        MagicMock(),
        [],
        "prompt",
        permissions=permissions,
        runtime_policy=_stored_policy("artifact_write"),
    )

    effective = mock_create.call_args.kwargs["permissions"]
    assert _check_fs_permission(effective, "read", "/runtime/t-1/skills/selected/SKILL.md") == (
        "allow"
    )
    assert _check_fs_permission(effective, "read", "/agents/agent-a/AGENTS.md") == "allow"
    assert _check_fs_permission(effective, "write", "/conversations/t-1/report.md") == "allow"
    assert _check_fs_permission(effective, "read", "/conversations/other/private.md") == "deny"


@patch("app.agent_runtime.runtime_component_builder.create_deep_agent")
def test_build_agent_returns_agent(mock_create: MagicMock):
    from app.agent_runtime.runtime_component_builder import build_agent

    sentinel = MagicMock()
    mock_create.return_value = sentinel

    result = build_agent(MagicMock(), [], "prompt")
    assert result is sentinel


def test_build_agent_compiles_with_deepagents_07_compatibility_stack():
    """Exercise the real 0.7 graph assembly, not only the call boundary mock."""

    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from app.agent_runtime.runtime_component_builder import build_agent

    agent = build_agent(FakeListChatModel(responses=["done"]), [], "prompt")

    assert "TodoListMiddleware.after_model" in agent.nodes


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.runtime_component_builder.build_agent")
@patch("app.agent_runtime.runtime_component_builder.convert_to_langchain_messages")
@patch("app.agent_runtime.runtime_component_builder.create_chat_model")
async def test_prepare_agent_logs_timing_spans(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_checkpointer: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.agent_runtime import runtime_component_builder as executor

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = [HumanMessage(content="hello")]
    mock_build.return_value = MagicMock()
    mock_checkpointer.return_value = MagicMock()
    caplog.set_level(logging.DEBUG, logger="app.agent_runtime.runtime_component_builder")

    await executor._prepare_agent(
        _cfg(thread_id="thread-timing"),
        messages_history=[{"role": "user", "content": "hello"}],
    )

    messages = [record.message for record in caplog.records]
    timing = next(message for message in messages if "agent_prepare_timing" in message)
    assert "model_ms" in timing
    assert "tools_ms" in timing
    assert "middleware_ms" in timing
    assert "build_agent_ms" in timing


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.runtime_component_builder.build_agent")
@patch("app.agent_runtime.runtime_component_builder.convert_to_langchain_messages")
@patch("app.agent_runtime.runtime_component_builder.create_chat_model")
async def test_prepare_agent_skips_memory_tools_when_trigger_writes_are_off(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_checkpointer: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent_runtime import runtime_component_builder as executor

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = [HumanMessage(content="hello")]
    mock_build.return_value = MagicMock()
    mock_checkpointer.return_value = MagicMock()
    mock_memory_tool = MagicMock()
    mock_memory_tool.name = "save_user_memory"
    mock_build_memory_tools = MagicMock(return_value=[mock_memory_tool])

    monkeypatch.setattr(executor, "build_memory_tools", mock_build_memory_tools)
    monkeypatch.setattr(executor, "_load_memory_context", AsyncMock(return_value=("", [])))
    monkeypatch.setattr(
        executor,
        "_memory_write_policy_for_run",
        AsyncMock(return_value="off"),
        raising=False,
    )

    await executor._prepare_agent(
        _cfg(
            agent_id="00000000-0000-0000-0000-0000000000aa",
            user_id="00000000-0000-0000-0000-000000000001",
            thread_id="00000000-0000-0000-0000-000000000099",
        ),
        messages_history=[{"role": "user", "content": "hello"}],
        is_trigger_mode=True,
    )

    mock_build_memory_tools.assert_not_called()
    build_args = mock_build.call_args.args
    tool_names = {getattr(tool, "name", "") for tool in build_args[1]}
    assert "save_user_memory" not in tool_names
    assert "Long-term Memory Tool Rules" not in build_args[2]


# ---------------------------------------------------------------------------
# middleware model credential boundary
# ---------------------------------------------------------------------------


def test_resolve_middleware_model_requires_user_key():
    from app.agent_runtime.runtime_component_builder import (
        MiddlewareModelCredentialRequiredError,
        _resolve_middleware_model_params,
    )

    configs = [
        {
            "type": "summarization",
            "params": {"model": "openai:gpt-4o-mini"},
        }
    ]

    with pytest.raises(MiddlewareModelCredentialRequiredError) as exc:
        _resolve_middleware_model_params(configs, {})

    assert exc.value.code == "middleware_model_credential_required"
    assert exc.value.status == 422


@patch("app.agent_runtime.runtime_component_builder.create_chat_model")
def test_resolve_middleware_model_uses_user_key_without_env_fallback(
    mock_model_factory: MagicMock,
):
    from app.agent_runtime.runtime_component_builder import _resolve_middleware_model_params

    sentinel = MagicMock()
    mock_model_factory.return_value = sentinel
    configs = [
        {
            "type": "summarization",
            "params": {"model": "openai:gpt-4o-mini"},
        }
    ]

    result = _resolve_middleware_model_params(configs, {"openai": "sk-user"})

    mock_model_factory.assert_called_once_with(
        "openai",
        "gpt-4o-mini",
        api_key="sk-user",
        allow_env_fallback=False,
    )
    assert result[0]["params"]["model"] is sentinel


# ---------------------------------------------------------------------------
# execute_agent_stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.agent_stream_runner.stream_agent_response")
@patch("app.agent_runtime.runtime_component_builder.build_agent")
@patch("app.agent_runtime.runtime_component_builder.convert_to_langchain_messages")
@patch("app.agent_runtime.runtime_component_builder.create_chat_model")
async def test_execute_stream_keeps_temporal_context_out_of_user_message(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    from app.agent_runtime.agent_stream_runner import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = [HumanMessage(content="오늘 일정 알려줘")]
    mock_build.return_value = MagicMock()
    captured_stream_messages = {}

    async def fake_stream(_agent, input_, config, **_kwargs):
        captured_stream_messages["messages"] = input_
        captured_stream_messages["config"] = config
        yield "event: message_end\ndata: {}\n\n"

    mock_stream.side_effect = fake_stream

    chunks = []
    cfg = _cfg(api_key="sk-test", system_prompt="Hello", thread_id="thread-1")
    async for chunk in execute_agent_stream(cfg, []):
        chunks.append(chunk)

    mock_model_factory.assert_called_once_with("openai", "gpt-4o", "sk-test", None)
    mock_build.assert_called_once()
    tools_passed = mock_build.call_args[0][1]
    tool_names = {tool.name for tool in tools_passed}
    assert TEMPORAL_TOOL_NAMES.issubset(tool_names)
    assert "ask_user" in tool_names

    system_prompt = mock_build.call_args[0][2]
    assert system_prompt.startswith("Hello")
    assert "현재 기준 날짜" in system_prompt
    assert "Interactive Tool Rules" in system_prompt
    assert "ask_user" in system_prompt
    assert "MCP" in system_prompt

    messages = captured_stream_messages["messages"]
    assert len(messages) == 1
    assert isinstance(messages[0], HumanMessage)
    assert messages[0].content == "오늘 일정 알려줘"


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.agent_stream_runner.stream_agent_response")
@patch("app.agent_runtime.runtime_component_builder.build_agent")
@patch("app.agent_runtime.runtime_component_builder.convert_to_langchain_messages")
@patch("app.agent_runtime.runtime_component_builder.create_chat_model")
async def test_execute_stream_forwards_artifact_recorder(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    from app.agent_runtime.agent_stream_runner import execute_agent_stream

    recorder = object()
    captured_kwargs: dict[str, object] = {}
    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()
    mock_checkpointer.return_value = MagicMock()

    async def fake_stream(_agent, _input, _config, **kwargs):
        captured_kwargs.update(kwargs)
        yield "event: message_end\ndata: {}\n\n"

    mock_stream.side_effect = fake_stream

    async for _ in execute_agent_stream(_cfg(), [], artifact_recorder=recorder):
        pass

    assert captured_kwargs["artifact_recorder"] is recorder


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.agent_stream_runner.stream_agent_response")
@patch("app.agent_runtime.runtime_component_builder.build_agent")
@patch("app.agent_runtime.runtime_component_builder.convert_to_langchain_messages")
@patch("app.agent_runtime.runtime_component_builder.create_chat_model")
@patch("app.agent_runtime.runtime_component_builder.create_tool_for_runtime")
async def test_execute_stream_runtime_tool_called_per_entry(
    mock_factory: MagicMock,
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.agent_runtime.agent_stream_runner import execute_agent_stream
    from app.config import settings

    # builtin:e2e_scripted_search는 E2E 전용 도구(e2e_scripted_model_enabled로
    # gating)인데 dev .env가 E2E_SCRIPTED_MODEL_ENABLED=true라 unit 빌드에 새어
    # 든다. 이 테스트는 production-like runtime-tool 구성(개수)을 검증하므로
    # 명시적으로 비활성화한다.
    monkeypatch.setattr(settings, "e2e_scripted_model_enabled", False)

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()
    mock_factory.return_value = MagicMock()

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()

    tools_config = [
        {
            "tool_id": "1",
            "definition_key": "builtin:web_search",
            "name": "Web Search",
            "description": "search",
            "parameters": {},
            "credentials": None,
            "credential_id": None,
        },
        {
            "tool_id": "2",
            "definition_key": "naver_search_blog",
            "name": "Naver Blog Search",
            "description": "blog",
            "parameters": {"query": "x"},
            "credentials": {"client_id": "a", "client_secret": "b"},
            "credential_id": "cred-1",
        },
    ]

    async for _ in execute_agent_stream(_cfg(tools_config=tools_config), []):
        pass

    assert mock_factory.call_count == 2
    tools_passed = mock_build.call_args[0][1]
    # 2 user tools + temporal helpers + ask_user auto-injected helper
    assert len(tools_passed) == 5


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.agent_stream_runner.stream_agent_response")
@patch("app.agent_runtime.runtime_component_builder.build_agent")
@patch("app.agent_runtime.runtime_component_builder.convert_to_langchain_messages")
@patch("app.agent_runtime.runtime_component_builder.create_chat_model")
@patch("app.agent_runtime.runtime_component_builder.create_tool_for_runtime")
async def test_execute_stream_injects_skill_tool_dependency(
    mock_factory: MagicMock,
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from app.agent_runtime.agent_stream_runner import execute_agent_stream

    _stub_skill_context(monkeypatch, tmp_path)
    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()
    captured_configs: list[dict[str, object]] = []
    mock_factory.side_effect = _capture_runtime_tool_configs(captured_configs)

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()
    cfg = _cfg(agent_skills=[_deep_research_skill()])

    async for _ in execute_agent_stream(cfg, []):
        pass

    assert cfg.tools_config == []
    assert captured_configs == [
        {
            "tool_id": "skill-dependency:tavily_search",
            "definition_key": "tavily_search",
            "name": "tavily_search",
            "description": "Hosted Tavily web search used by attached skills.",
            "parameters": {},
            "credential_id": None,
            "credentials": None,
            "user_id": None,
            "agent_id": None,
            "is_skill_dependency": True,
        }
    ]


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.agent_stream_runner.stream_agent_response")
@patch("app.agent_runtime.runtime_component_builder.build_agent")
@patch("app.agent_runtime.runtime_component_builder.convert_to_langchain_messages")
@patch("app.agent_runtime.runtime_component_builder.create_chat_model")
@patch("app.agent_runtime.runtime_component_builder.create_tool_for_runtime")
async def test_execute_stream_dedupes_exact_skill_dependency_name(
    mock_factory: MagicMock,
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from app.agent_runtime.agent_stream_runner import execute_agent_stream

    _stub_skill_context(monkeypatch, tmp_path)
    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()
    captured_configs: list[dict[str, object]] = []
    mock_factory.side_effect = _capture_runtime_tool_configs(captured_configs)

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()
    explicit_tool = {
        "tool_id": "tool-1",
        "definition_key": "tavily_search",
        "name": "tavily_search",
        "description": "User-added Tavily",
        "parameters": {},
        "credential_id": None,
        "credentials": None,
    }

    async for _ in execute_agent_stream(
        _cfg(tools_config=[explicit_tool], agent_skills=[_deep_research_skill()]),
        [],
    ):
        pass

    assert captured_configs == [explicit_tool]


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.agent_stream_runner.stream_agent_response")
@patch("app.agent_runtime.runtime_component_builder.build_agent")
@patch("app.agent_runtime.runtime_component_builder.convert_to_langchain_messages")
@patch("app.agent_runtime.runtime_component_builder.create_chat_model")
@patch("app.agent_runtime.runtime_component_builder.create_tool_for_runtime")
async def test_execute_stream_keeps_stable_dependency_alias_when_explicit_tool_has_different_name(
    mock_factory: MagicMock,
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from app.agent_runtime.agent_stream_runner import execute_agent_stream

    _stub_skill_context(monkeypatch, tmp_path)
    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()
    captured_configs: list[dict[str, object]] = []
    mock_factory.side_effect = _capture_runtime_tool_configs(captured_configs)

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()
    explicit_tool = {
        "tool_id": "tool-1",
        "definition_key": "tavily_search",
        "name": "Research Search",
        "description": "User-added Tavily under a friendly name",
        "parameters": {},
        "credential_id": None,
        "credentials": None,
    }

    async for _ in execute_agent_stream(
        _cfg(tools_config=[explicit_tool], agent_skills=[_deep_research_skill()]),
        [],
    ):
        pass

    assert [config["name"] for config in captured_configs] == [
        "Research Search",
        "tavily_search",
    ]
    assert captured_configs[1]["definition_key"] == "tavily_search"
    assert captured_configs[1]["is_skill_dependency"] is True


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.agent_stream_runner.stream_agent_response")
@patch("app.agent_runtime.runtime_component_builder.build_agent")
@patch("app.agent_runtime.runtime_component_builder.convert_to_langchain_messages")
@patch("app.agent_runtime.runtime_component_builder.create_chat_model")
@patch("app.agent_runtime.runtime_component_builder.create_tool_for_runtime")
async def test_execute_stream_skips_unknown_tool(
    mock_factory: MagicMock,
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    """When the factory returns ``None`` (unknown definition_key), the
    executor must skip it instead of crashing the chat session."""

    from app.agent_runtime.agent_stream_runner import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()
    mock_factory.return_value = None

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()

    tools_config = [
        {
            "tool_id": "1",
            "definition_key": "unknown:thing",
            "name": "Mystery",
            "description": "?",
            "parameters": {},
            "credentials": None,
            "credential_id": None,
        }
    ]

    async for _ in execute_agent_stream(_cfg(tools_config=tools_config), []):
        pass

    tools_passed = mock_build.call_args[0][1]
    tool_names = {tool.name for tool in tools_passed}
    assert TEMPORAL_TOOL_NAMES.issubset(tool_names)
    assert "ask_user" in tool_names


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.agent_stream_runner.stream_agent_response")
@patch("app.agent_runtime.runtime_component_builder.build_agent")
@patch("app.agent_runtime.runtime_component_builder.convert_to_langchain_messages")
@patch("app.agent_runtime.runtime_component_builder.create_chat_model")
async def test_execute_stream_skips_custom_without_api_url(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    """Custom tool without api_url should be silently skipped."""
    from app.agent_runtime.agent_stream_runner import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()

    tools_config = [
        {"type": "custom", "name": "No URL Tool"}  # missing api_url
    ]

    async for _ in execute_agent_stream(_cfg(tools_config=tools_config), []):
        pass

    tools_passed = mock_build.call_args[0][1]
    tool_names = {tool.name for tool in tools_passed}
    assert TEMPORAL_TOOL_NAMES.issubset(tool_names)
    assert "ask_user" in tool_names


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.agent_stream_runner.stream_agent_response")
@patch("app.agent_runtime.runtime_component_builder.build_agent")
@patch("app.agent_runtime.runtime_component_builder.convert_to_langchain_messages")
@patch("app.agent_runtime.runtime_component_builder.create_chat_model")
async def test_execute_stream_passes_thread_id_in_config(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    from app.agent_runtime.agent_stream_runner import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = ["msg"]
    mock_build.return_value = MagicMock()

    captured_config = {}

    async def capture_stream(agent, messages, config, **_kwargs):
        captured_config.update(config)
        yield "done"

    mock_stream.side_effect = capture_stream

    async for _ in execute_agent_stream(
        _cfg(thread_id="my-thread-42"),
        [{"role": "user", "content": "hi"}],
    ):
        pass

    assert captured_config["configurable"]["thread_id"] == "my-thread-42"


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.agent_stream_runner.stream_agent_response")
@patch("app.agent_runtime.runtime_component_builder.build_agent")
@patch("app.agent_runtime.runtime_component_builder.convert_to_langchain_messages")
@patch("app.agent_runtime.runtime_component_builder.create_chat_model")
async def test_execute_stream_passes_recursion_limit_in_config(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    from app.agent_runtime.agent_stream_runner import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = [HumanMessage(content="hi")]
    mock_build.return_value = MagicMock()

    captured_config = {}

    async def capture_stream(agent, messages, config, **_kwargs):
        captured_config.update(config)
        yield "done"

    mock_stream.side_effect = capture_stream

    async for _ in execute_agent_stream(
        _cfg(model_params={"recursion_limit": 77}),
        [{"role": "user", "content": "hi"}],
    ):
        pass

    assert captured_config["recursion_limit"] == 77


@pytest.mark.asyncio
async def test_execute_stream_attaches_langfuse_trace_context(monkeypatch):
    from app.agent_runtime.agent_stream_runner import execute_agent_stream

    async def fake_prepare_agent(*args, **kwargs):
        return MagicMock(), [], {"configurable": {"thread_id": "conv-123"}}

    captured_config = {}

    async def fake_stream(_agent, _messages, config, **_kwargs):
        captured_config.update(config)
        yield "done"

    class FakeLangfuseContext:
        trace = SimpleNamespace(
            provider="langfuse",
            trace_id="lf-trace-run-123",
            trace_url="https://langfuse.local/project/moldy/traces/lf-trace-run-123",
        )
        metadata = {
            "moldy_run_id": "run-123",
            "moldy_source": "chat",
        }

        def configure_config(self, config):
            return {
                **config,
                "callbacks": ["langfuse-callback"],
                "metadata": self.metadata,
                "tags": ["moldy", "source:chat"],
            }

        def flush(self):
            pass

    monkeypatch.setattr("app.agent_runtime.agent_stream_runner._prepare_agent", fake_prepare_agent)
    monkeypatch.setattr("app.agent_runtime.agent_stream_runner.stream_agent_response", fake_stream)
    monkeypatch.setattr(
        "app.agent_runtime.agent_stream_runner.build_langfuse_run_context",
        lambda *_args, **_kwargs: FakeLangfuseContext(),
    )

    langfuse_sink = []
    async for _ in execute_agent_stream(
        _cfg(agent_id="agent-123", user_id="user-123"),
        [{"role": "user", "content": "hi"}],
        run_id="run-123",
        moldy_source="chat",
        langfuse_sink=langfuse_sink,
    ):
        pass

    assert captured_config["configurable"]["thread_id"] == "conv-123"
    assert captured_config["callbacks"] == ["langfuse-callback"]
    assert captured_config["metadata"]["moldy_run_id"] == "run-123"
    assert captured_config["tags"] == ["moldy", "source:chat"]
    assert langfuse_sink[0].trace_id == "lf-trace-run-123"


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.agent_stream_runner.stream_agent_response")
@patch("app.agent_runtime.runtime_component_builder.build_agent")
@patch("app.agent_runtime.runtime_component_builder.convert_to_langchain_messages")
@patch("app.agent_runtime.runtime_component_builder.create_chat_model")
async def test_execute_stream_skips_unknown_tool_type(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    """Unknown tool types should be silently ignored."""
    from app.agent_runtime.agent_stream_runner import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()

    tools_config = [
        {"type": "mcp", "name": "Some MCP Tool"}  # not handled in executor
    ]

    async for _ in execute_agent_stream(_cfg(tools_config=tools_config), []):
        pass

    tools_passed = mock_build.call_args[0][1]
    tool_names = {tool.name for tool in tools_passed}
    assert TEMPORAL_TOOL_NAMES.issubset(tool_names)
    assert "ask_user" in tool_names


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.agent_stream_runner.stream_agent_response")
@patch("app.agent_runtime.runtime_component_builder.build_agent")
@patch("app.agent_runtime.runtime_component_builder.convert_to_langchain_messages")
@patch("app.agent_runtime.runtime_component_builder.create_chat_model")
async def test_execute_stream_yields_chunks(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    """Verify that all chunks from stream_agent_response are yielded."""
    from app.agent_runtime.agent_stream_runner import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()

    async def fake_stream(*args, **kwargs):
        yield "chunk-1"
        yield "chunk-2"
        yield "chunk-3"

    mock_stream.return_value = fake_stream()

    chunks = []
    async for chunk in execute_agent_stream(_cfg(), []):
        chunks.append(chunk)

    assert chunks == ["chunk-1", "chunk-2", "chunk-3"]


@pytest.mark.asyncio
async def test_execute_stream_records_stream_error_as_hook_failure(monkeypatch):
    """A stream-visible error must call hook failure, not hook post success."""
    from app.agent_runtime.agent_stream_runner import execute_agent_stream

    class FailingAgent:
        async def astream(self, *args, **kwargs):
            yield (MagicMock(content="partial", type="ai", tool_calls=[]), {})
            raise RuntimeError("provider stream failed")

        async def aget_state(self, *args, **kwargs):
            state = MagicMock()
            state.tasks = []
            return state

    async def fake_prepare_agent(*args, **kwargs):
        return FailingAgent(), [], {"configurable": {"thread_id": "t-1"}}

    mock_hooks = MagicMock()
    mock_hooks.run_pre = AsyncMock()
    mock_hooks.run_post = AsyncMock()
    mock_hooks.run_failure = AsyncMock()

    monkeypatch.setattr(
        "app.agent_runtime.agent_stream_runner._prepare_agent",
        fake_prepare_agent,
    )
    monkeypatch.setattr("app.agent_runtime.agent_stream_runner.hooks", mock_hooks)

    errors: list[StreamErrorRecord] = []
    chunks = [
        chunk
        async for chunk in execute_agent_stream(
            _cfg(
                user_id="00000000-0000-0000-0000-000000000001",
                agent_id="00000000-0000-0000-0000-0000000000aa",
            ),
            [{"role": "user", "content": "hi"}],
            error_sink=errors,
        )
    ]

    assert any("event: error" in chunk for chunk in chunks)
    assert len(errors) == 1
    assert str(errors[0].error) == "provider stream failed"
    mock_hooks.run_pre.assert_awaited_once()
    mock_hooks.run_failure.assert_awaited_once()
    failure_error = mock_hooks.run_failure.await_args.args[1]
    assert isinstance(failure_error, RuntimeError)
    assert str(failure_error) == "provider stream failed"
    mock_hooks.run_post.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_stream_reports_usage_when_canceled_after_usage(monkeypatch):
    from app.agent_runtime.agent_stream_runner import execute_agent_stream

    async def fake_prepare_agent(*args, **kwargs):
        return MagicMock(), [], {"configurable": {"thread_id": "t-1"}}

    async def fake_stream_agent_response(*args, **kwargs):
        kwargs["usage_sink"].update(
            {"prompt_tokens": 11, "completion_tokens": 7, "estimated_cost": 0.0042}
        )
        yield 'event: content_delta\ndata: {"delta":"partial"}\n\n'
        raise asyncio.CancelledError()

    mock_hooks = MagicMock()
    mock_hooks.run_pre = AsyncMock()
    mock_hooks.run_post = AsyncMock()
    mock_hooks.run_failure = AsyncMock()

    monkeypatch.setattr(
        "app.agent_runtime.agent_stream_runner._prepare_agent",
        fake_prepare_agent,
    )
    monkeypatch.setattr(
        "app.agent_runtime.agent_stream_runner.stream_agent_response",
        fake_stream_agent_response,
    )
    monkeypatch.setattr("app.agent_runtime.agent_stream_runner.hooks", mock_hooks)

    stream = execute_agent_stream(
        _cfg(
            user_id="00000000-0000-0000-0000-000000000001",
            agent_id="00000000-0000-0000-0000-0000000000aa",
        ),
        [{"role": "user", "content": "hi"}],
    )
    assert await anext(stream)
    with pytest.raises(asyncio.CancelledError):
        await anext(stream)

    mock_hooks.run_post.assert_awaited_once()
    result = mock_hooks.run_post.await_args.args[1]
    assert result.tokens_in == 11
    assert result.tokens_out == 7
    assert result.cost_usd == 0.0042
    mock_hooks.run_failure.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_stream_records_stream_error_in_sink(monkeypatch):
    """Resume streams must expose the same typed error signal as normal streams."""
    from app.agent_runtime.agent_stream_runner import resume_agent_stream

    class FailingAgent:
        async def astream(self, *args, **kwargs):
            raise RuntimeError("resume stream failed")
            yield

        async def aget_state(self, *args, **kwargs):
            state = MagicMock()
            state.tasks = []
            return state

    async def fake_prepare_agent(*args, **kwargs):
        return FailingAgent(), [], {"configurable": {"thread_id": "t-1"}}

    mock_hooks = MagicMock()
    mock_hooks.run_pre = AsyncMock()
    mock_hooks.run_post = AsyncMock()
    mock_hooks.run_failure = AsyncMock()

    monkeypatch.setattr(
        "app.agent_runtime.agent_stream_runner._prepare_agent",
        fake_prepare_agent,
    )
    monkeypatch.setattr("app.agent_runtime.agent_stream_runner.hooks", mock_hooks)

    errors: list[StreamErrorRecord] = []
    chunks = [
        chunk
        async for chunk in resume_agent_stream(
            _cfg(
                user_id="00000000-0000-0000-0000-000000000001",
                agent_id="00000000-0000-0000-0000-0000000000aa",
            ),
            {"answer": "approved"},
            error_sink=errors,
        )
    ]

    assert any("event: error" in chunk for chunk in chunks)
    assert len(errors) == 1
    assert str(errors[0].error) == "resume stream failed"
    mock_hooks.run_failure.assert_awaited_once()
    mock_hooks.run_post.assert_not_awaited()


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.agent_stream_runner.stream_agent_response")
@patch("app.agent_runtime.runtime_component_builder.build_agent")
@patch("app.agent_runtime.runtime_component_builder.convert_to_langchain_messages")
@patch("app.agent_runtime.runtime_component_builder.create_chat_model")
async def test_execute_stream_passes_skills_and_memory(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
    tmp_path,
):
    """Skills and memory params are forwarded to build_agent when provided."""
    from app.agent_runtime.agent_stream_runner import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()

    agent_skills = [{"skill_id": "s1", "storage_path": "/data/skills/s1"}]

    mock_data_dir = tmp_path / "data"
    mock_data_dir.mkdir(exist_ok=True)

    with patch("app.agent_runtime.runtime_component_builder._DATA_DIR", mock_data_dir):
        async for _ in execute_agent_stream(
            _cfg(
                agent_skills=agent_skills,
                agent_id="agent-123",
                user_id="00000000-0000-0000-0000-000000000001",
            ),
            [],
        ):
            pass

    build_kwargs = mock_build.call_args[1]
    # ADR-017 Slice E (2026-05-19) — skill mount moved from the global
    # ``/skills/`` to a per-thread ``/runtime/<thread_id>/skills/`` so
    # one agent can never see another agent's selected skills
    # (Spec §9). The ``_cfg`` fixture uses ``thread_id="t-1"``.
    assert build_kwargs["skills"] == ["/runtime/t-1/skills/"]
    assert build_kwargs["memory"] == ["/agents/agent-123/AGENTS.md"]
    assert isinstance(build_kwargs["backend"], ScopedOffloadBackend)

    # Verify agent directory was created
    assert (mock_data_dir / "agents" / "agent-123").exists()


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.agent_stream_runner.stream_agent_response")
@patch("app.agent_runtime.runtime_component_builder.build_agent")
@patch("app.agent_runtime.runtime_component_builder.convert_to_langchain_messages")
@patch("app.agent_runtime.runtime_component_builder.create_chat_model")
async def test_execute_stream_injects_product_memory_tools_and_prompt(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
) -> None:
    from app.agent_runtime.agent_stream_runner import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()

    with (
        patch(
            "app.agent_runtime.runtime_component_builder._load_memory_context",
            new_callable=AsyncMock,
            create=True,
        ) as mock_memory_prompt,
        patch(
            "app.agent_runtime.runtime_component_builder._memory_write_policy_for_run",
            new_callable=AsyncMock,
            create=True,
        ) as mock_memory_write_policy,
    ):
        mock_memory_prompt.return_value = (
            "## Long-term Memory\n- The user prefers Korean.",
            [{"id": "m1", "scope": "user", "content": "The user prefers Korean."}],
        )
        mock_memory_write_policy.return_value = "ask"
        async for _ in execute_agent_stream(
            _cfg(
                agent_id="00000000-0000-0000-0000-0000000000aa",
                user_id="00000000-0000-0000-0000-000000000001",
                thread_id="00000000-0000-0000-0000-000000000099",
            ),
            [],
        ):
            pass

    build_args = mock_build.call_args.args
    tool_names = {getattr(tool, "name", "") for tool in build_args[1]}
    assert {"propose_memory", "save_user_memory", "save_agent_memory"} <= tool_names
    assert "## Long-term Memory" in build_args[2]
    assert "explicitly asks you to remember" in build_args[2]
    assert "propose_memory" in build_args[2]
    mock_memory_prompt.assert_awaited_once()
    mock_memory_write_policy.assert_awaited_once()


@pytest.mark.asyncio
async def test_prepare_runtime_components_adds_memory_rules_without_memory_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Child subagents get memory tool guidance without unsupported memory args."""
    from app.agent_runtime import runtime_component_builder as executor

    model = MagicMock()
    memory_tool = MagicMock()
    memory_tool.name = "save_user_memory"
    load_memory_prompt = AsyncMock(return_value=("SHOULD NOT LOAD", []))

    monkeypatch.setattr(executor, "_build_model_candidates", lambda _cfg: [model])
    monkeypatch.setattr(executor, "_build_mcp_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(executor, "create_tool_for_runtime", lambda _config: None)
    monkeypatch.setattr(executor, "build_memory_tools", lambda **_kwargs: [memory_tool])
    monkeypatch.setattr(
        executor,
        "_memory_write_policy_for_run",
        AsyncMock(return_value="ask"),
    )
    monkeypatch.setattr(executor, "_load_memory_context", load_memory_prompt)

    components = await executor._prepare_runtime_components(
        _cfg(
            agent_id="00000000-0000-0000-0000-0000000000aa",
            user_id="00000000-0000-0000-0000-000000000001",
            thread_id="00000000-0000-0000-0000-000000000099",
        ),
        is_trigger_mode=False,
        include_ask_user=False,
        include_agent_memory_file=False,
    )

    assert memory_tool in components.tools
    assert components.memory_sources is None
    assert "Long-term Memory Tool Rules" in components.system_prompt
    load_memory_prompt.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expects_artifact_prompt"),
    [("inspect", False), ("artifact_write", True)],
)
async def test_prepare_stored_filesystem_profile_omits_skill_execution_capabilities(
    mode: str,
    expects_artifact_prompt: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.agent_runtime import runtime_component_builder as executor

    model = MagicMock()
    artifact_prompt = MagicMock(return_value="generated-file-rules")
    resolve_credentials = AsyncMock()
    add_skill_secrets = MagicMock()
    execute_tool = MagicMock()
    execute_tool.name = "execute_in_skill"
    mcp_todo_tool = MagicMock()
    mcp_todo_tool.name = "write_todos"
    configured_tools: dict[str, MagicMock] = {}

    def configured_tool(config: dict[str, object]) -> MagicMock:
        tool = MagicMock()
        tool.name = str(config["name"])
        configured_tools[tool.name] = tool
        return tool

    skill_source = tmp_path / "selected"
    skill_source.mkdir()
    (skill_source / "SKILL.md").write_text("# Selected\n")

    monkeypatch.setattr(executor, "_build_model_candidates", lambda _cfg: [model])
    monkeypatch.setattr(executor, "_build_mcp_tools", AsyncMock(return_value=[mcp_todo_tool]))
    monkeypatch.setattr(executor, "create_tool_for_runtime", configured_tool)
    monkeypatch.setattr(executor, "_append_temporal_tools", lambda _tools: None)
    monkeypatch.setattr(executor, "_append_e2e_scripted_search_tool", lambda _tools: None)
    monkeypatch.setattr(executor, "_append_e2e_ui_data_demo_tool", lambda _tools: None)
    monkeypatch.setattr(executor, "_memory_write_policy_for_run", AsyncMock(return_value="off"))
    monkeypatch.setattr(executor, "_artifact_file_instruction_prompt", artifact_prompt)
    monkeypatch.setattr(executor, "resolve_runtime_credentials", resolve_credentials)
    monkeypatch.setattr(executor, "_add_skill_secrets_to_run", add_skill_secrets)
    monkeypatch.setattr(
        executor,
        "_create_skill_execute_tool",
        MagicMock(return_value=execute_tool),
    )
    monkeypatch.setattr(
        executor,
        "build_skill_runtime_context",
        lambda *_args, **_kwargs: SkillToolContext(
            thread_id="t-1",
            output_dir=tmp_path / "outputs",
            runtime_root=tmp_path / "runtime",
            descriptors={},
        ),
    )

    components = await executor._prepare_runtime_components(
        _cfg(
            runtime_policy=_stored_policy(mode),
            tools_config=[
                *[
                    {"name": name}
                    for name in (
                        "read_file",
                        "write_file",
                        "task",
                        "write_todos",
                        "safe_search",
                    )
                ],
                {"name": "mcp-write-todos", "mcp_server_url": "https://mcp.invalid"},
            ],
            agent_skills=[
                {
                    "slug": "selected",
                    "name": "Selected",
                    "storage_path": str(skill_source),
                }
            ],
        ),
        is_trigger_mode=False,
        include_ask_user=False,
        include_agent_memory_file=False,
    )

    assert components.skills_sources == ["/runtime/t-1/skills/"]
    assert {tool.name for tool in components.tools} == {"safe_search"}
    assert components.tools == [configured_tools["safe_search"]]
    assert components.interrupt_on is None
    assert artifact_prompt.called is expects_artifact_prompt
    resolve_credentials.assert_not_awaited()
    add_skill_secrets.assert_not_called()


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.agent_stream_runner.stream_agent_response")
@patch("app.agent_runtime.runtime_component_builder.build_agent")
@patch("app.agent_runtime.runtime_component_builder.convert_to_langchain_messages")
@patch("app.agent_runtime.runtime_component_builder.create_chat_model")
async def test_execute_stream_no_skills_no_memory_when_not_provided(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    """When agent_skills and agent_id are not provided, skills/memory should be None."""
    from app.agent_runtime.agent_stream_runner import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()

    async for _ in execute_agent_stream(_cfg(), []):
        pass

    build_kwargs = mock_build.call_args[1]
    assert build_kwargs["skills"] is None
    assert build_kwargs["memory"] is None


# ---------------------------------------------------------------------------
# interrupt_on extraction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.agent_stream_runner.stream_agent_response")
@patch("app.agent_runtime.runtime_component_builder.build_agent")
@patch("app.agent_runtime.runtime_component_builder.convert_to_langchain_messages")
@patch("app.agent_runtime.runtime_component_builder.create_chat_model")
async def test_interrupt_on_with_write_tools(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    """HiTL middleware + write tool → interrupt_on에 해당 도구 포함."""
    from app.agent_runtime.agent_stream_runner import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()

    async for _ in execute_agent_stream(
        _cfg(
            tools_config=[{"type": "builtin", "name": "Web Search"}],
            middleware_configs=[{"type": "human_in_the_loop", "params": {}}],
        ),
        [],
    ):
        pass

    build_kwargs = mock_build.call_args[1]
    # Read-only tools do not add approval, but interactive agents still wrap
    # ask_user and DeepAgents write tools through the standard policy.
    assert build_kwargs["interrupt_on"] == _expected_interrupt_policy()


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.agent_stream_runner.stream_agent_response")
@patch("app.agent_runtime.runtime_component_builder.build_agent")
@patch("app.agent_runtime.runtime_component_builder.convert_to_langchain_messages")
@patch("app.agent_runtime.runtime_component_builder.create_chat_model")
async def test_interrupt_on_without_hitl_middleware(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    """No HiTL middleware → ask_user still gets standard respond interrupt policy."""
    from app.agent_runtime.agent_stream_runner import execute_agent_stream

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()

    async for _ in execute_agent_stream(
        _cfg(middleware_configs=[{"type": "tool_retry", "params": {}}]),
        [],
    ):
        pass

    build_kwargs = mock_build.call_args[1]
    assert build_kwargs["interrupt_on"] == _expected_interrupt_policy()


@pytest.mark.asyncio
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.agent_stream_runner.stream_agent_response")
@patch("app.agent_runtime.runtime_component_builder.build_agent")
@patch("app.agent_runtime.runtime_component_builder.convert_to_langchain_messages")
@patch("app.agent_runtime.runtime_component_builder.create_chat_model")
async def test_ask_user_not_included_in_invoke(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
):
    """execute_agent_invoke should NOT include ask_user tool."""
    from app.agent_runtime.agent_stream_runner import execute_agent_invoke

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []

    mock_agent = MagicMock()
    mock_agent.ainvoke = AsyncMock(
        return_value={"messages": [MagicMock(content="Hello", type="ai")]}
    )
    mock_build.return_value = mock_agent

    await execute_agent_invoke(_cfg(), [])

    tools_passed = mock_build.call_args[0][1]
    tool_names = [t.name for t in tools_passed]
    assert "ask_user" not in tool_names
    assert TEMPORAL_TOOL_NAMES.issubset(set(tool_names))


@pytest.mark.asyncio
async def test_execute_agent_invoke_attaches_langfuse_trace_context(monkeypatch):
    from app.agent_runtime.agent_stream_runner import execute_agent_invoke

    class FakeAgent:
        config: dict | None = None

        async def ainvoke(self, _payload, *, config):
            self.config = config
            return {"messages": [SimpleNamespace(content="trigger output")]}

    fake_agent = FakeAgent()

    async def fake_prepare_agent(*args, **kwargs):
        return fake_agent, ["msg"], {"configurable": {"thread_id": "conv-trigger"}}

    class FakeLangfuseContext:
        trace = SimpleNamespace(
            provider="langfuse",
            trace_id="lf-trace-trigger",
            trace_url="https://langfuse.local/project/moldy/traces/lf-trace-trigger",
        )
        metadata = {"moldy_run_id": "trigger-run-1", "moldy_source": "trigger"}

        def configure_config(self, config):
            return {
                **config,
                "callbacks": ["langfuse-callback"],
                "metadata": self.metadata,
                "tags": ["moldy", "source:trigger"],
            }

        def flush(self):
            pass

    monkeypatch.setattr("app.agent_runtime.agent_stream_runner._prepare_agent", fake_prepare_agent)
    monkeypatch.setattr(
        "app.agent_runtime.agent_stream_runner.build_langfuse_run_context",
        lambda *_args, **_kwargs: FakeLangfuseContext(),
    )

    output = await execute_agent_invoke(
        _cfg(agent_id="agent-123", user_id="user-123"),
        [{"role": "user", "content": "scheduled"}],
        run_id="trigger-run-1",
        moldy_source="trigger",
    )

    assert output == "trigger output"
    assert fake_agent.config is not None
    assert fake_agent.config["callbacks"] == ["langfuse-callback"]
    assert fake_agent.config["metadata"]["moldy_run_id"] == "trigger-run-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_content", "expected_content", "secret_values"),
    [
        (
            "History at /conversation_history/session_0123456789abcdef0123456789abcdef.md; "
            "token=invoke-secret-42",
            "History at history_af5ef34f123d9b24bb00d96b; token=<redacted>",
            {"invoke-secret-42"},
        ),
        (
            "Unknown at /private/.moldy-internal/offload/history/secret",
            "internal_reference_redacted",
            set(),
        ),
        ("ordinary trigger output", "ordinary trigger output", set()),
    ],
)
async def test_execute_agent_invoke_projects_final_message_before_hook_and_return(
    monkeypatch: pytest.MonkeyPatch,
    raw_content: str,
    expected_content: str,
    secret_values: set[str],
) -> None:
    """Non-streaming trigger output crosses the same offload egress boundary."""
    from app.agent_runtime.agent_stream_runner import execute_agent_invoke

    source_message = SimpleNamespace(content=raw_content)

    class FakeAgent:
        async def ainvoke(self, _payload, *, config):
            return {"messages": [source_message]}

    async def fake_prepare_agent(*args, **kwargs):
        return FakeAgent(), [], {"configurable": {}}

    class FakeLangfuseContext:
        trace = None

        def configure_config(self, config):
            return config

        def flush(self):
            pass

    mock_hooks = MagicMock()
    mock_hooks.run_pre = AsyncMock()
    mock_hooks.run_post = AsyncMock()
    mock_hooks.run_failure = AsyncMock()
    monkeypatch.setattr("app.agent_runtime.agent_stream_runner._prepare_agent", fake_prepare_agent)
    monkeypatch.setattr(
        "app.agent_runtime.agent_stream_runner.build_langfuse_run_context",
        lambda *_args, **_kwargs: FakeLangfuseContext(),
    )
    monkeypatch.setattr("app.agent_runtime.agent_stream_runner.hooks", mock_hooks)

    result = await execute_agent_invoke(
        _cfg(
            user_id="00000000-0000-0000-0000-000000000001",
            secret_values=secret_values,
        ),
        [{"role": "user", "content": "scheduled"}],
    )

    assert result == expected_content
    assert source_message.content == raw_content
    assert mock_hooks.run_post.await_args.args[1].output == expected_content


@pytest.mark.asyncio
async def test_execute_agent_invoke_normalizes_redacted_content_blocks_without_mutating_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invoke keeps its promised string return contract for provider content blocks."""
    from app.agent_runtime.agent_stream_runner import execute_agent_invoke

    secret = "invoke-block-secret-42"
    source_content = [
        {"type": "text", "text": f"Visible token={secret}"},
        {"type": "reasoning", "text": "private"},
    ]
    source_message = SimpleNamespace(content=source_content)

    class FakeAgent:
        async def ainvoke(self, _payload, *, config):
            return {"messages": [source_message]}

    async def fake_prepare_agent(*args, **kwargs):
        return FakeAgent(), [], {"configurable": {}}

    class FakeLangfuseContext:
        trace = None

        def configure_config(self, config):
            return config

        def flush(self):
            pass

    monkeypatch.setattr("app.agent_runtime.agent_stream_runner._prepare_agent", fake_prepare_agent)
    monkeypatch.setattr(
        "app.agent_runtime.agent_stream_runner.build_langfuse_run_context",
        lambda *_args, **_kwargs: FakeLangfuseContext(),
    )

    result = await execute_agent_invoke(
        _cfg(secret_values={secret}),
        [{"role": "user", "content": "scheduled"}],
    )

    assert result == "Visible token=<redacted>"
    assert source_message.content == source_content
