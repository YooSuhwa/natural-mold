"""Runtime component and agent preparation behind the legacy facade.

Collaborators that tests patch on ``runtime_component_builder`` are resolved
through the live facade at call time.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Sequence
from typing import Any

from langchain_core.tools import BaseTool

from app.agent_runtime.message_utils import RuntimeMessage
from app.agent_runtime.offload_storage import ScopedOffloadBackend
from app.agent_runtime.run_secrets import add_run_secrets, collect_secret_values
from app.agent_runtime.runtime_config import AgentConfig, RuntimeComponents
from app.agent_runtime.runtime_facade_bindings import runtime_attr
from app.agent_runtime.runtime_preparation import (
    prepare_runtime_components_impl as _prepare_runtime_components_impl,
)
from app.agent_runtime.runtime_preparation_skill_builder import (
    prepare_skill_builder_components_impl as _prepare_skill_builder_components_impl,
)
from app.agent_runtime.runtime_preparation_support import (
    add_skill_secrets_to_run_impl as _add_skill_secrets_to_run_impl,
)
from app.agent_runtime.runtime_preparation_support import (
    append_builtin_tools_impl as _append_builtin_tools_impl,
)
from app.agent_runtime.runtime_preparation_support import (
    configured_recursion_limit_impl as _configured_recursion_limit_impl,
)
from app.agent_runtime.runtime_preparation_support import (
    scoped_runtime_backend_impl as _scoped_runtime_backend_impl,
)
from app.agent_runtime.runtime_preparation_support import (
    selected_skill_slugs_impl as _selected_skill_slugs_impl,
)
from app.agent_runtime.tool_factory import create_builtin_tool
from app.config import settings

# Preserve the facade's logger name for ``agent_prepare_timing`` records.
logger = logging.getLogger("app.agent_runtime.runtime_component_builder")

_TEMPORAL_BUILTIN_TOOL_KEYS = (
    "builtin:current_datetime",
    "builtin:resolve_relative_date",
)
_DB_FREE_ACTOR_ID = uuid.UUID("00000000-0000-4000-8000-000000000000")


def _scoped_runtime_backend(
    cfg: AgentConfig,
    *,
    run_id: str | None,
) -> ScopedOffloadBackend:
    """Build the trusted owner/conversation/run/actor storage boundary."""
    return _scoped_runtime_backend_impl(
        cfg,
        run_id=run_id,
        data_dir=runtime_attr("_DATA_DIR"),
        db_free_actor_id=_DB_FREE_ACTOR_ID,
    )


def _configured_recursion_limit(cfg: AgentConfig) -> int | None:
    return _configured_recursion_limit_impl(cfg)


def _append_temporal_tools(tools: list[BaseTool]) -> None:
    """Ensure date/time grounding tools are always available to agents."""

    _append_builtin_tools_impl(
        tools,
        keys=_TEMPORAL_BUILTIN_TOOL_KEYS,
        create_builtin_tool=create_builtin_tool,
    )


def _append_e2e_scripted_search_tool(tools: list[BaseTool]) -> None:
    """Attach the deterministic scripted ``tavily_search`` tool for E2E only.

    Gated by ``e2e_scripted_model_enabled`` (which already refuses to run in
    production), so real deployments never see this tool. Mirrors
    ``_append_temporal_tools`` so the E2E search-group fixture
    (``E2E_SEARCH_GROUP``) can emit consecutive search calls whose results the
    frontend aggregates into domain badges + a source count — without any
    network call or product behavior change.
    """

    if not settings.e2e_scripted_model_enabled:
        return
    _append_builtin_tools_impl(
        tools,
        keys=("builtin:e2e_scripted_search",),
        create_builtin_tool=create_builtin_tool,
    )


def _append_e2e_ui_data_demo_tool(tools: list[BaseTool]) -> None:
    """Attach the deterministic generative-UI demo tool for E2E only.

    Gated by ``e2e_scripted_model_enabled`` so real deployments never see it.
    Lets the ``E2E_UI_DATA_DEMO`` fixture drive one ``moldy.ui_data`` event
    (``demo_note``) end-to-end without any product behavior change.
    """

    if not settings.e2e_scripted_model_enabled:
        return
    _append_builtin_tools_impl(
        tools,
        keys=("builtin:e2e_ui_data_demo",),
        create_builtin_tool=create_builtin_tool,
    )


def _add_skill_secrets_to_run(skill_ctx: Any, cfg: AgentConfig) -> None:
    """ADR-021 — union resolved skill credential plaintext into the run set.

    After ``resolve_runtime_credentials`` populates
    ``descriptor.credential_bindings[*].decrypted`` (``dict[str, str]``), feed
    those plaintext values to the run-scoped redaction set. ``add_run_secrets``
    is a no-op when the run ContextVar is unset.

    Also union into ``cfg.secret_values`` — not just the ContextVar copy that
    ``set_run_secrets`` installs. The run worker masks error_message *after* the
    ContextVar is reset, reading ``cfg.secret_values`` (see
    ``conversation_run_worker._redact_run_error_message``), so lazy skill
    credentials must land there too or they'd escape error_message redaction.
    """

    _add_skill_secrets_to_run_impl(
        skill_ctx,
        cfg,
        add_run_secrets=add_run_secrets,
        collect_secret_values=collect_secret_values,
    )


def _selected_skill_slugs(agent_skills: list[dict[str, Any]] | None) -> list[str]:
    return _selected_skill_slugs_impl(agent_skills)


async def _prepare_skill_builder_components(
    cfg: AgentConfig,
    *,
    is_trigger_mode: bool,
    include_ask_user: bool,
    run_id: str | None = None,
    scope_offload_backend: bool = False,
) -> RuntimeComponents:
    """Build the isolated Skill Builder runtime through fresh facade bindings."""

    return await _prepare_skill_builder_components_impl(
        cfg,
        is_trigger_mode=is_trigger_mode,
        include_ask_user=include_ask_user,
        run_id=run_id,
        scope_offload_backend=scope_offload_backend,
        bindings=runtime_attr("_runtime_preparation_bindings")(),
    )


async def _prepare_runtime_components(
    cfg: AgentConfig,
    *,
    is_trigger_mode: bool,
    include_ask_user: bool,
    include_agent_memory_file: bool,
    timings: dict[str, int] | None = None,
    run_id: str | None = None,
    scope_offload_backend: bool = False,
) -> RuntimeComponents:
    """Build reusable Deep Agents runtime pieces for a parent or child agent."""
    if cfg.runtime_profile == "skill_builder":
        return await runtime_attr("_prepare_skill_builder_components")(
            cfg,
            is_trigger_mode=is_trigger_mode,
            include_ask_user=include_ask_user,
            run_id=run_id,
            scope_offload_backend=scope_offload_backend,
        )
    return await _prepare_runtime_components_impl(
        cfg,
        is_trigger_mode=is_trigger_mode,
        include_ask_user=include_ask_user,
        include_agent_memory_file=include_agent_memory_file,
        timings=timings,
        run_id=run_id,
        scope_offload_backend=scope_offload_backend,
        bindings=runtime_attr("_runtime_preparation_bindings")(),
    )


async def _prepare_agent(
    cfg: AgentConfig,
    *,
    messages_history: Sequence[RuntimeMessage],
    is_trigger_mode: bool = False,
    run_id: str | None = None,
) -> tuple[Any, list, dict]:
    """에이전트 빌드 + 설정. stream/invoke 공용.

    ``is_trigger_mode=True`` 는 트리거(invoke) 모드 indicator — 사용자가 없으므로
    (a) ``ask_user`` 도구 미주입(호출 시 영원히 hang), (b) HiTL ``interrupt_on``
    을 None 으로 강제 override 하여 위험 도구 승인 게이트도 자동 통과.
    """
    prepare_started = time.perf_counter()
    last_mark = prepare_started
    timings: dict[str, int] = {}

    def mark_timing(name: str) -> None:
        nonlocal last_mark
        now = time.perf_counter()
        timings[name] = int((now - last_mark) * 1000)
        last_mark = now

    components = await runtime_attr("_prepare_runtime_components")(
        cfg,
        is_trigger_mode=is_trigger_mode,
        include_ask_user=not is_trigger_mode,
        include_agent_memory_file=True,
        timings=timings,
        run_id=run_id,
        scope_offload_backend=run_id is not None,
    )
    last_mark = time.perf_counter()

    # 5. 에이전트 빌드 — create_deep_agent + checkpointer
    from app.agent_runtime.checkpointer import get_checkpointer

    build_started = time.perf_counter()
    agent = runtime_attr("build_agent")(
        components.model,
        components.tools,
        components.system_prompt,
        middleware=components.middleware or None,
        interrupt_on=components.interrupt_on,
        checkpointer=get_checkpointer(),
        backend=components.backend,
        skills=components.skills_sources,
        memory=components.memory_sources,
        permissions=components.permissions,
        name=cfg.agent_runtime_name or f"agent_{cfg.thread_id[:8]}",
        subagents=cfg.subagents_config,
        runtime_policy=cfg.runtime_policy,
    )
    timings["build_agent_ms"] = int((time.perf_counter() - build_started) * 1000)
    last_mark = time.perf_counter()

    lc_messages = runtime_attr("convert_to_langchain_messages")(messages_history)
    mark_timing("messages_ms")
    configurable: dict[str, Any] = {"thread_id": cfg.thread_id}
    if run_id is not None:
        configurable["moldy_run_id"] = run_id
    config: dict[str, Any] = {"configurable": configurable}
    recursion_limit = runtime_attr("_configured_recursion_limit")(cfg)
    if recursion_limit is not None:
        config["recursion_limit"] = recursion_limit
    if cfg.checkpoint_id:
        # LangGraph time-travel: invoking with an explicit checkpoint_id forks
        # a new branch from that point. The new run's checkpoints chain back to
        # this id, and `alist` reveals both branches as siblings of the parent.
        config["configurable"]["checkpoint_id"] = cfg.checkpoint_id

    timings["total_ms"] = int((time.perf_counter() - prepare_started) * 1000)
    timing_payload = " ".join(f"{key}={value}" for key, value in timings.items())
    log_message = (
        "agent_prepare_timing "
        f"agent_id={cfg.agent_id} thread_id={cfg.thread_id} "
        f"tools={len(components.tools)} skills={len(cfg.agent_skills or [])} "
        f"{timing_payload}"
    )
    logger.debug(log_message)
    if timings["total_ms"] >= 250:
        logger.info(log_message)

    return agent, lc_messages, config
