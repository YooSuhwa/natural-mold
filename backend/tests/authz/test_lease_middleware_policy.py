"""Only a closed middleware set is accepted by the lease capability census."""

from __future__ import annotations

import importlib
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any, cast

import pytest
from deepagents.backends import StateBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from langchain.agents.middleware import (
    AgentMiddleware,
    HumanInTheLoopMiddleware,
    LLMToolEmulator,
    TodoListMiddleware,
    ToolRetryMiddleware,
)
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

from app.agent_runtime.lease_bound_tool import LeaseBoundToolMiddleware
from app.agent_runtime.lease_middleware_policy import (
    INNER_TOOL_WRAPPERS,
    LEASE_MIDDLEWARE_ALLOWLIST,
)
from app.agent_runtime.runtime_effective_capabilities import (
    CapabilityCensusError,
    build_effective_census,
)
from tests.authz import prepared_run_testkit as kit


def _census(middleware: list[Any]):
    return build_effective_census(
        "root",
        effective_tool_names=[],
        selected_tools=(),
        middleware=middleware,
        backends={},
        providers=(),
    )


def _guard() -> LeaseBoundToolMiddleware:
    empty = _census([])
    lease = replace(kit.lease(kit.binding(), "token"), descriptor_ids=frozenset(), tool_access={})
    return LeaseBoundToolMiddleware(lease, empty, session_factory=cast(Any, lambda: None))


class _InnerToolWrapper(AgentMiddleware):
    async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        return await handler(request)


class _CustomFilesystem(FilesystemMiddleware):
    pass


def test_allowlist_names_resolve_to_installed_classes() -> None:
    for qualname in LEASE_MIDDLEWARE_ALLOWLIST | INNER_TOOL_WRAPPERS:
        module, _, name = qualname.rpartition(".")
        assert isinstance(getattr(importlib.import_module(module), name), type), qualname
    assert INNER_TOOL_WRAPPERS <= LEASE_MIDDLEWARE_ALLOWLIST


def test_default_deep_agent_and_app_guard_middleware_are_accepted() -> None:
    accepted = [
        TodoListMiddleware(),
        FilesystemMiddleware(backend=StateBackend()),
        PatchToolCallsMiddleware(),
        AnthropicPromptCachingMiddleware(),
        HumanInTheLoopMiddleware(interrupt_on={}),
        _guard(),
        FilesystemMiddleware(backend=StateBackend()),  # allowed inside the guard
    ]
    census = _census(accepted)
    assert [d.kind for d in census.descriptors] == ["middleware"] * len(accepted)


@pytest.mark.parametrize(
    "factory",
    [
        ToolRetryMiddleware,
        lambda: LLMToolEmulator(model=FakeMessagesListChatModel(responses=[])),
        _InnerToolWrapper,
        lambda: _CustomFilesystem(backend=StateBackend()),
    ],
)
def test_retry_emulator_and_unknown_middleware_reject(factory: Callable[[], Any]) -> None:
    with pytest.raises(CapabilityCensusError) as rejected:
        _census([TodoListMiddleware(), factory()])
    assert rejected.value.code == "capability_middleware_unsupported"


def test_tool_wrappers_inside_the_guard_reject_unless_allowed(monkeypatch) -> None:
    inner = _InnerToolWrapper()
    qualname = f"{type(inner).__module__}.{type(inner).__qualname__}"
    monkeypatch.setattr(
        "app.agent_runtime.lease_middleware_policy.LEASE_MIDDLEWARE_ALLOWLIST",
        LEASE_MIDDLEWARE_ALLOWLIST | {qualname},
    )
    assert _census([inner, _guard()]).participant == "root"  # outside the guard: accepted
    with pytest.raises(CapabilityCensusError) as inside:
        _census([_guard(), inner])
    assert inside.value.code == "capability_middleware_unsupported"
    assert inside.value.subject == qualname
    with pytest.raises(CapabilityCensusError) as twice:
        _census([_guard(), _guard()])
    assert twice.value.code == "capability_middleware_unsupported"
