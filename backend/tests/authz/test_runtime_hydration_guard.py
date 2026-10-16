"""Every named and aliased preparation seam fails closed during lease execution."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from app.agent_runtime import checkpointer, executor, mcp_strict_loader
from app.agent_runtime import runtime_component_builder as builder
from app.agent_runtime.runtime import memory_context
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.runtime_hydration_guard import (
    HydrationDeniedError,
    post_marker_execution,
)
from app.agent_runtime.runtime_preparation import prepare_runtime_components_impl
from app.agent_runtime.runtime_preparation_bindings import _runtime_preparation_bindings
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID


def _cfg() -> AgentConfig:
    return AgentConfig(
        org_context=unit_context(TEST_USER_ID),
        provider="provider",
        model_name="model",
        api_key=None,
        base_url=None,
        system_prompt="p",
        tools_config=[],
        thread_id="guard-thread",
    )


async def _never(*_args: Any) -> list[Any]:
    raise AssertionError("discovery must not run")


def _resolver() -> Any:
    return _runtime_preparation_bindings().resolve_runtime_credentials


def _seams() -> dict[str, Callable[[], Awaitable[Any] | Any]]:
    cfg = _cfg()
    return {
        "_prepare_agent": lambda: executor._prepare_agent(cfg, messages_history=[]),
        "_prepare_runtime_components": lambda: builder._prepare_runtime_components(
            cfg, is_trigger_mode=False, include_ask_user=False, include_agent_memory_file=False
        ),
        "prepare_runtime_components_impl": lambda: prepare_runtime_components_impl(
            cfg,
            is_trigger_mode=False,
            include_ask_user=False,
            include_agent_memory_file=False,
            bindings=_runtime_preparation_bindings(),
        ),
        "_build_mcp_tools": lambda: executor._build_mcp_tools([{"name": "x"}]),
        "load_selected_mcp_tools_strict": lambda: mcp_strict_loader.load_selected_mcp_tools_strict(
            [], discover=_never, call_timeout_seconds=1.0
        ),
        "_load_memory_context": lambda: memory_context._load_memory_context(cfg),
        "get_checkpointer": checkpointer.get_checkpointer,
        "resolve_runtime_credentials": lambda: _resolver()(None, None),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("seam", sorted(_seams()))
async def test_named_and_alias_seams_are_denied_after_marker(seam: str) -> None:
    call = _seams()[seam]

    async def invoke() -> None:
        result = call()
        if hasattr(result, "__await__"):
            await result

    with post_marker_execution("run-1"), pytest.raises(HydrationDeniedError) as denied:
        await invoke()
    assert denied.value.code == "post_marker_hydration_denied"
    assert denied.value.seam == seam


def test_guard_is_scoped_to_the_lease_execution() -> None:
    from app.agent_runtime.runtime_hydration_guard import active_lease_run, assert_pre_marker

    assert active_lease_run() is None
    assert_pre_marker("anything")
    with post_marker_execution("run-2"):
        assert active_lease_run() == "run-2"
    assert active_lease_run() is None
