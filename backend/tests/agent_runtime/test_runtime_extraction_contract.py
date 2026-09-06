"""Focused contracts for the runtime implementation extraction."""

from __future__ import annotations

import inspect
import uuid
from pathlib import Path
from typing import Any, get_type_hints

import pytest
from deepagents.backends import StateBackend
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.agent_runtime import deep_agent_factory
from app.agent_runtime import runtime_component_builder as builder
from app.agent_runtime.offload_storage import OffloadIdentity, ScopedOffloadStorage

_EXTRACTED_IMPLEMENTATION_MODULES = {
    "app.agent_runtime.deep_agent_factory",
    "app.agent_runtime.runtime_preparation",
    "app.agent_runtime.runtime_preparation_skill_builder",
    "app.agent_runtime.runtime_preparation_support",
}


def _scoped_backend(tmp_path: Path):
    return ScopedOffloadStorage(
        data_dir=tmp_path,
        identity=OffloadIdentity(
            owner_id="owner",
            conversation_id="conversation",
            run_id="run",
        ),
    ).for_actor(uuid.UUID("11111111-1111-4111-8111-111111111111"))


def test_factory_legacy_wrappers_keep_exact_signatures() -> None:
    expected = {
        "_middleware_name": "(middleware: 'Any') -> 'str | None'",
        "_build_moldy_filesystem_middleware": (
            "(*, backend: 'Any', permissions: "
            "'list[FilesystemPermission] | None') -> 'FilesystemMiddleware'"
        ),
        "_with_moldy_deepagents_compatibility": (
            "(middleware: 'list[Any] | tuple[Any, ...] | None', *, backend: 'Any', "
            "permissions: 'list[FilesystemPermission] | None') -> 'list[Any]'"
        ),
        "_actor_backend": "(backend: 'Any', actor_id: 'uuid.UUID | str | None') -> 'Any'",
        "_with_actor_summarization": (
            "(middleware: 'list[Any]', *, model: 'BaseChatModel', backend: 'Any') -> 'list[Any]'"
        ),
        "_normalize_declarative_subagents": (
            "(subagents: 'list[dict[str, Any]] | None', *, model: 'BaseChatModel', "
            "tools: 'list[BaseTool]', backend: 'Any', permissions: "
            "'list[FilesystemPermission] | None', interrupt_on: "
            "'dict[str, Any] | bool | None', skills: 'list[str] | None') -> "
            "'list[dict[str, Any]]'"
        ),
        "build_agent": (
            "(model: 'BaseChatModel', tools: 'list[BaseTool]', system_prompt: 'str', *, "
            "middleware: 'list | None' = None, interrupt_on: "
            "'dict[str, Any] | bool | None' = None, checkpointer: 'Any | None' = None, "
            "store: 'Any | None' = None, backend: 'Any | None' = None, skills: "
            "'list[str] | None' = None, memory: 'list[str] | None' = None, permissions: "
            "'list[FilesystemPermission] | None' = None, name: 'str | None' = None, "
            "subagents: 'list[dict[str, Any]] | None' = None, runtime_policy: "
            "'ResolvedRuntimePolicy | None' = None) -> 'Any'"
        ),
    }

    assert {name: str(inspect.signature(getattr(builder, name))) for name in expected} == expected


def test_extracted_implementation_symbols_do_not_leak_into_public_facade_surface() -> None:
    leaked = {
        name: getattr(value, "__module__", None)
        for name, value in vars(builder).items()
        if not name.startswith("_")
        and getattr(value, "__module__", None) in _EXTRACTED_IMPLEMENTATION_MODULES
    }

    assert leaked == {}


def test_factory_bindings_use_explicit_non_variadic_protocols() -> None:
    binding_hints = get_type_hints(deep_agent_factory.DeepAgentFactoryBindings)
    create_protocol = binding_hints["create_deep_agent"]
    create_signature = inspect.signature(create_protocol.__call__)
    middleware_hints = get_type_hints(deep_agent_factory.with_moldy_deepagents_compatibility_impl)
    middleware_protocol = middleware_hints["build_filesystem_middleware"]
    middleware_signature = inspect.signature(middleware_protocol.__call__)

    assert all(
        parameter.kind is not inspect.Parameter.VAR_KEYWORD
        for parameter in create_signature.parameters.values()
    )
    assert all(
        parameter.kind is not inspect.Parameter.VAR_KEYWORD
        for parameter in middleware_signature.parameters.values()
    )
    subagent_hint = str(get_type_hints(create_protocol.__call__)["subagents"])
    subagent_union = str(deep_agent_factory._DeepAgentSubagent.__value__)
    assert "_DeepAgentSubagent" in subagent_hint
    assert all(name in subagent_union for name in ("SubAgent", "CompiledSubAgent", "AsyncSubAgent"))
    assert "cast(Any" not in inspect.getsource(deep_agent_factory.build_agent_impl)


def test_factory_wrappers_preserve_legacy_patch_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []
    compatible = object()
    normalized = object()
    result = object()

    def compatibility(
        middleware: list[Any] | tuple[Any, ...] | None,
        *,
        backend: Any,
        permissions: Any,
    ) -> Any:
        calls.append(("compatibility", middleware))
        assert backend is not None
        assert permissions is None
        return compatible

    def normalize(subagents: Any, **kwargs: Any) -> Any:
        calls.append(("normalize", subagents))
        assert kwargs["backend"] is not None
        return normalized

    def create(**kwargs: Any) -> object:
        calls.append(("create", kwargs))
        assert set(kwargs) == {
            "model",
            "tools",
            "system_prompt",
            "middleware",
            "interrupt_on",
            "checkpointer",
            "store",
            "backend",
            "skills",
            "memory",
            "permissions",
            "name",
            "subagents",
        }
        assert kwargs["middleware"] is compatible
        assert kwargs["subagents"] is normalized
        return result

    monkeypatch.setattr(builder, "_with_moldy_deepagents_compatibility", compatibility)
    monkeypatch.setattr(builder, "_normalize_declarative_subagents", normalize)
    monkeypatch.setattr(builder, "create_deep_agent", create)

    assert builder.build_agent(FakeListChatModel(responses=["done"]), [], "prompt") is result
    assert [name for name, _value in calls] == ["compatibility", "normalize", "create"]


def test_subagent_wrapper_resolves_nested_helpers_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    backend = StateBackend()

    def actor_backend(value: Any, actor_id: Any) -> Any:
        calls.append(f"actor:{actor_id}")
        assert value is backend
        return value

    def compatibility(
        middleware: Any,
        *,
        backend: Any,
        permissions: Any,
    ) -> list[Any]:
        del middleware, permissions
        calls.append("compatibility")
        return [backend]

    def summarization(
        middleware: list[Any],
        *,
        model: Any,
        backend: Any,
    ) -> list[Any]:
        del model, backend
        calls.append("summarization")
        return middleware

    monkeypatch.setattr(builder, "_actor_backend", actor_backend)
    monkeypatch.setattr(builder, "_with_moldy_deepagents_compatibility", compatibility)
    monkeypatch.setattr(builder, "_with_actor_summarization", summarization)

    normalized = builder._normalize_declarative_subagents(
        [{"name": "child", "description": "child", "system_prompt": "help"}],
        model=FakeListChatModel(responses=["done"]),
        tools=[],
        backend=backend,
        permissions=None,
        interrupt_on=None,
        skills=None,
    )

    assert [spec["name"] for spec in normalized] == ["general-purpose", "child"]
    assert calls.count("compatibility") == 2
    assert calls.count("summarization") == 2
    assert sum(item.startswith("actor:") for item in calls) == 2


def test_factory_preserves_opaque_subagent_identity_and_non_deleting_middleware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    runnable = object()
    compiled = {"name": "compiled", "description": "compiled", "runnable": runnable}
    remote = {"name": "remote", "description": "remote", "graph_id": "graph"}

    def create(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(builder, "create_deep_agent", create)
    builder.build_agent(
        FakeListChatModel(responses=["done"]),
        [],
        "prompt",
        subagents=[compiled, remote],
    )

    assert next(spec for spec in captured["subagents"] if spec["name"] == "compiled") is compiled
    assert next(spec for spec in captured["subagents"] if spec["name"] == "remote") is remote
    assert [item.name for item in captured["middleware"]] == [
        "FilesystemMiddleware",
        "TodoListMiddleware",
    ]
    assert tuple(tool.name for tool in captured["middleware"][0].tools) == (
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
        "execute",
    )


def test_subagent_wrapper_returns_actor_scoped_backend(tmp_path: Path) -> None:
    parent = _scoped_backend(tmp_path)
    actor = uuid.UUID("22222222-2222-4222-8222-222222222222")

    child = builder._actor_backend(parent, actor)

    assert child is not parent
    assert child.artifacts_root != parent.artifacts_root


def test_factory_rejects_missing_actor_identity_without_calling_upstream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream_calls = 0

    def create(**_kwargs: Any) -> object:
        nonlocal upstream_calls
        upstream_calls += 1
        return object()

    monkeypatch.setattr(builder, "create_deep_agent", create)

    with pytest.raises(
        ValueError,
        match="declarative subagent is missing its trusted actor identity",
    ):
        builder.build_agent(
            FakeListChatModel(responses=["done"]),
            [],
            "prompt",
            backend=_scoped_backend(tmp_path),
            subagents=[{"name": "child", "description": "child", "system_prompt": "help"}],
        )

    assert upstream_calls == 0
