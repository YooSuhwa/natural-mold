"""Characterize the public Deep Agents runtime facade without compiling a graph."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from deepagents.backends import StateBackend
from deepagents.middleware.filesystem import FilesystemPermission
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.tools import StructuredTool

from app.agent_runtime import executor, runtime_config
from app.agent_runtime import runtime_component_builder as builder
from app.agent_runtime.runtime_config import AgentConfig
from tests.agent_runtime.runtime_contract_helpers import (
    JSONValue,
    assert_contract_matches,
)

_FIXTURE_NAME = "runtime_facade_v1.json"
_PATCH_POINTS = (
    "create_deep_agent",
    "create_chat_model",
    "build_agent",
    "convert_to_langchain_messages",
    "create_tool_for_runtime",
    "_build_model_candidates",
    "_load_memory_context",
    "_memory_write_policy_for_run",
    "_DATA_DIR",
)


@dataclass(frozen=True, slots=True)
class _NamedMiddleware:
    name: str


def _contract_tool_runner() -> str:
    """Return the deterministic fixture payload for the runtime boundary."""

    return "contract"


_CONTRACT_TOOL = StructuredTool.from_function(
    _contract_tool_runner,
    name="tool-a",
    description="Deterministic contract fixture tool.",
)


def _annotation_name(annotation: object) -> str:
    """Normalize annotations without leaking module-object addresses."""

    if annotation is inspect.Signature.empty:
        return "empty"
    if isinstance(annotation, str):
        return annotation
    module = getattr(annotation, "__module__", None)
    qualname = getattr(annotation, "__qualname__", None)
    if isinstance(module, str) and isinstance(qualname, str):
        return f"{module}.{qualname}"
    return str(annotation)


def _signature_shape(target: Callable[..., object]) -> dict[str, JSONValue]:
    """Return a stable description of an exported callable's interface."""

    signature = inspect.signature(target)
    parameters: list[JSONValue] = []
    for parameter in signature.parameters.values():
        default = (
            "required"
            if parameter.default is inspect.Signature.empty
            else "none"
            if parameter.default is None
            else f"value:{type(parameter.default).__name__}"
        )
        parameters.append(
            {
                "name": parameter.name,
                "kind": parameter.kind.name.lower(),
                "annotation": _annotation_name(parameter.annotation),
                "default": default,
            }
        )
    return {"parameters": parameters, "return": _annotation_name(signature.return_annotation)}


def _permissions_shape(permissions: list[FilesystemPermission] | None) -> list[JSONValue]:
    """Serialize permission policy fields consumed by the filesystem middleware."""

    return [
        {"operations": list(rule.operations), "paths": list(rule.paths), "mode": rule.mode}
        for rule in permissions or []
    ]


def _subagent_shape(
    spec: dict[str, Any],
    *,
    parent_backend: StateBackend,
    opaque: dict[str, Any],
) -> dict[str, JSONValue]:
    """Project only the stable subagent fields owned by the build boundary."""

    middleware = spec.get("middleware", [])
    return {
        "name": str(spec["name"]),
        "middleware": [str(item.name) for item in middleware],
        "tools": [str(tool.name) for tool in spec.get("tools", [])],
        "permissions": _permissions_shape(spec.get("permissions")),
        "has_skills": "skills" in spec,
        "backend_is_parent": getattr(middleware[0], "backend", None) is parent_backend
        if middleware
        else False,
        "opaque_identity_preserved": spec is opaque,
        "graph_id": spec.get("graph_id") if isinstance(spec.get("graph_id"), str) else None,
    }


async def collect_runtime_facade_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Observe facade exports plus deterministic parent/child build wiring."""

    exported = sorted(executor.__all__)
    signatures = {
        name: _signature_shape(getattr(executor, name))
        for name in exported
        if callable(getattr(executor, name))
    }
    captured: list[dict[str, Any]] = []

    def capture_deep_agent(**kwargs: Any) -> str:
        captured.append(kwargs)
        return "contract-agent"

    monkeypatch.setattr(builder, "create_deep_agent", capture_deep_agent)
    backend = StateBackend()
    model = FakeListChatModel(responses=["contract"])
    parent_permissions = [
        FilesystemPermission(operations=["read"], paths=["/contract/parent/**"], mode="allow")
    ]
    child_permissions = [
        FilesystemPermission(operations=["write"], paths=["/contract/child/**"], mode="interrupt")
    ]
    input_middleware = [
        _NamedMiddleware("FilesystemMiddleware"),
        _NamedMiddleware("TodoListMiddleware"),
        _NamedMiddleware("retained-first"),
        _NamedMiddleware("retained-second"),
    ]
    opaque = {"name": "remote", "description": "opaque", "graph_id": "contract-remote"}
    declarative = {
        "name": "child",
        "description": "declarative",
        "system_prompt": "contract-child",
        "permissions": child_permissions,
        "middleware": input_middleware,
    }
    builder.build_agent(model, [_CONTRACT_TOOL], "<base>", backend=backend)
    builder.build_agent(
        model,
        [_CONTRACT_TOOL],
        "<base>",
        middleware=input_middleware,
        interrupt_on={"tool-a": {"allowed_decisions": ["approve"]}},
        backend=backend,
        skills=["/contract/skills"],
        permissions=parent_permissions,
        subagents=[declarative, opaque],
    )
    defaults, configured = captured
    configured_subagents = configured["subagents"]

    async def no_mcp_tools(_configs: list[dict[str, Any]]) -> list[Any]:
        return []

    async def memory_policy(*_args: Any, **_kwargs: Any) -> str:
        return "off"

    monkeypatch.setattr(builder, "_build_model_candidates", lambda _cfg: [model])
    monkeypatch.setattr(builder, "_build_mcp_tools", no_mcp_tools)
    monkeypatch.setattr(builder, "_append_temporal_tools", lambda tools: None)
    monkeypatch.setattr(builder, "_append_e2e_scripted_search_tool", lambda tools: None)
    monkeypatch.setattr(builder, "_append_e2e_ui_data_demo_tool", lambda tools: None)
    monkeypatch.setattr(builder, "_memory_write_policy_for_run", memory_policy)
    monkeypatch.setattr(
        builder,
        "_build_default_reliability_middleware",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(builder, "get_provider_middleware", lambda _provider: [])
    monkeypatch.setattr(
        builder,
        "_system_prompt_with_temporal_context",
        lambda prompt: f"<temporal>{prompt}</temporal>",
    )
    monkeypatch.setattr(
        builder,
        "_artifact_file_instruction_prompt",
        lambda _thread_id: "<artifact>",
    )
    monkeypatch.setattr(builder, "_interactive_tool_instruction_prompt", lambda: "<interactive>")
    prepared = await builder._prepare_runtime_components(
        AgentConfig("provider", "model", None, None, "<base>", [], "contract-thread"),
        is_trigger_mode=False,
        include_ask_user=True,
        include_agent_memory_file=False,
    )

    return {
        "executor_exports": exported,
        "signatures": signatures,
        "patch_points": {
            "runtime_component_builder": {name: hasattr(builder, name) for name in _PATCH_POINTS},
            "runtime_config": {"_DATA_DIR": hasattr(runtime_config, "_DATA_DIR")},
        },
        "build_agent": {
            "keyword_keys": sorted(configured),
            "defaults_none_keyword_keys": sorted(
                key for key, value in defaults.items() if value is None
            ),
            "configured_none_keyword_keys": sorted(
                key for key, value in configured.items() if value is None
            ),
            "middleware_names": [item.name for item in configured["middleware"]],
            "filesystem_tools": [tool.name for tool in configured["middleware"][0].tools],
            "permissions": _permissions_shape(configured["permissions"]),
            "subagents": [
                _subagent_shape(spec, parent_backend=backend, opaque=opaque)
                for spec in configured_subagents
            ],
        },
        "prepared": {
            "prompt_markers": [
                marker
                for marker in ("<temporal>", "<base>", "<artifact>", "<interactive>")
                if marker in prepared.system_prompt
            ],
            "permissions": _permissions_shape(prepared.permissions),
        },
    }


@pytest.mark.asyncio
async def test_runtime_facade_matches_reviewed_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """Given the current facade, it retains the reviewed public build contract."""

    manifest = await collect_runtime_facade_contract(monkeypatch)

    assert_contract_matches(_FIXTURE_NAME, manifest)
