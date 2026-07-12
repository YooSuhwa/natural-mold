"""Runtime component builder — 에이전트 실행 컴포넌트 조립 오케스트레이터.

BE-S10: 모델 후보/폴백(``runtime.models``), 신뢰성 미들웨어
(``runtime.reliability``), HiTL 인터럽트 정책(``runtime.interrupts``),
프롬프트 블록(``runtime.prompts``), 장기 기억 컨텍스트
(``runtime.memory_context``) 클러스터는 ``app.agent_runtime.runtime``
패키지로 분리됐고, 이 모듈이 기존 표면을 그대로 재-export 한다.

Patch-contract notes (tests/test_executor.py, tests/test_model_fallback.py,
tests/test_hitl_middleware.py, tests/test_skill_builder_*.py):

- ``create_chat_model`` 은 이 모듈 attribute 로 남아야 한다 — 테스트가
  ``runtime_component_builder.create_chat_model`` 을 patch 하고,
  ``runtime.models`` 의 함수들이 call-time 에 이 모듈을 경유해 조회한다.
- ``_build_model_candidates`` / ``_load_memory_context`` /
  ``_memory_write_policy_for_run`` 은 이 모듈 binding 을 patch 하는 테스트가
  있으므로, 잔류 함수들은 모듈 global(재-export binding)로 호출한다.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, cast

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from deepagents.middleware.filesystem import FilesystemPermission
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from app.agent_runtime.filesystem_permissions import build_filesystem_permissions
from app.agent_runtime.mcp_tool_loader import _build_mcp_tools
from app.agent_runtime.message_utils import convert_to_langchain_messages
from app.agent_runtime.middleware_registry import (
    DEEPAGENT_BUILTIN_TYPES,
    build_middleware_instances,
    get_provider_middleware,
)
from app.agent_runtime.model_factory import create_chat_model as create_chat_model
from app.agent_runtime.run_secrets import add_run_secrets, collect_secret_values
from app.agent_runtime.runtime.interrupts import _build_interrupt_on_policy
from app.agent_runtime.runtime.interrupts import (
    _default_interrupt_on_from_tools as _default_interrupt_on_from_tools,
)
from app.agent_runtime.runtime.memory_context import (
    _RECALLED_MEMORY_PREVIEW_CHARS as _RECALLED_MEMORY_PREVIEW_CHARS,
)
from app.agent_runtime.runtime.memory_context import (
    _load_memory_context,
    _memory_write_policy_for_run,
)
from app.agent_runtime.runtime.memory_context import (
    _parse_uuid as _parse_uuid,
)
from app.agent_runtime.runtime.memory_context import (
    _recalled_memory_briefs as _recalled_memory_briefs,
)
from app.agent_runtime.runtime.models import (
    _MIDDLEWARE_MODEL_FIELDS as _MIDDLEWARE_MODEL_FIELDS,
)
from app.agent_runtime.runtime.models import (
    MiddlewareModelCredentialRequiredError as MiddlewareModelCredentialRequiredError,
)
from app.agent_runtime.runtime.models import (
    _build_model_candidates,
    _resolve_middleware_model_params,
)
from app.agent_runtime.runtime.models import (
    _build_model_with_fallback as _build_model_with_fallback,
)
from app.agent_runtime.runtime.models import (
    _is_retryable_model_error as _is_retryable_model_error,
)
from app.agent_runtime.runtime.models import (
    _model_chain as _model_chain,
)
from app.agent_runtime.runtime.models import (
    _model_constructor_params as _model_constructor_params,
)
from app.agent_runtime.runtime.prompts import (
    _artifact_file_instruction_prompt,
    _interactive_tool_instruction_prompt,
    _memory_tool_instruction_prompt,
    _system_prompt_with_temporal_context,
)
from app.agent_runtime.runtime.reliability import (
    EmptyContentRetryMiddleware as EmptyContentRetryMiddleware,
)
from app.agent_runtime.runtime.reliability import (
    _build_default_reliability_middleware,
)
from app.agent_runtime.runtime.reliability import (
    _has_visible_ai_content as _has_visible_ai_content,
)
from app.agent_runtime.runtime_config import _DATA_DIR, AgentConfig, RuntimeComponents
from app.agent_runtime.skill_builder.chat_prompt import load_skill_builder_prompt
from app.agent_runtime.skill_builder.tools import build_skill_builder_tools
from app.agent_runtime.skill_executor import _create_skill_execute_tool
from app.agent_runtime.skill_tool_dependencies import build_skill_dependency_tool_configs
from app.agent_runtime.tool_factory import create_builtin_tool, create_tool_for_runtime
from app.agent_runtime.tools.ask_user import ask_user as ask_user_tool
from app.agent_runtime.tools.memory import build_memory_tools
from app.config import settings
from app.marketplace.skill_runtime import build_skill_runtime_context, resolve_runtime_credentials

logger = logging.getLogger(__name__)

_TEMPORAL_BUILTIN_TOOL_KEYS = (
    "builtin:current_datetime",
    "builtin:resolve_relative_date",
)


def build_agent(
    model: BaseChatModel,
    tools: list[BaseTool],
    system_prompt: str,
    *,
    middleware: list | None = None,
    interrupt_on: dict[str, Any] | bool | None = None,
    checkpointer: Any | None = None,
    store: Any | None = None,
    backend: Any | None = None,
    skills: list[str] | None = None,
    memory: list[str] | None = None,
    permissions: list[FilesystemPermission] | None = None,
    name: str | None = None,
    subagents: list[dict[str, Any]] | None = None,
) -> Any:
    """Build a moldy agent. Returns CompiledStateGraph."""
    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=middleware or (),
        interrupt_on=interrupt_on,  # type: ignore[arg-type]  # bool/dict 양쪽 지원
        checkpointer=checkpointer,
        store=store,
        backend=backend,
        skills=skills,
        memory=memory,
        permissions=permissions,
        name=name,
        subagents=cast(Any, subagents),
    )


def _configured_recursion_limit(cfg: AgentConfig) -> int | None:
    raw = (cfg.model_params or {}).get("recursion_limit")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _append_temporal_tools(tools: list[BaseTool]) -> None:
    """Ensure date/time grounding tools are always available to agents."""

    existing = {tool.name for tool in tools}
    for key in _TEMPORAL_BUILTIN_TOOL_KEYS:
        tool = create_builtin_tool(key)
        if tool is None or tool.name in existing:
            continue
        tools.append(tool)
        existing.add(tool.name)


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
    existing = {tool.name for tool in tools}
    tool = create_builtin_tool("builtin:e2e_scripted_search")
    if tool is None or tool.name in existing:
        return
    tools.append(tool)


def _append_e2e_ui_data_demo_tool(tools: list[BaseTool]) -> None:
    """Attach the deterministic generative-UI demo tool for E2E only.

    Gated by ``e2e_scripted_model_enabled`` so real deployments never see it.
    Lets the ``E2E_UI_DATA_DEMO`` fixture drive one ``moldy.ui_data`` event
    (``demo_note``) end-to-end without any product behavior change.
    """

    if not settings.e2e_scripted_model_enabled:
        return
    existing = {tool.name for tool in tools}
    tool = create_builtin_tool("builtin:e2e_ui_data_demo")
    if tool is None or tool.name in existing:
        return
    tools.append(tool)


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

    for descriptor in getattr(skill_ctx, "descriptors", {}).values():
        for binding in getattr(descriptor, "credential_bindings", {}).values():
            decrypted = getattr(binding, "decrypted", None)
            add_run_secrets(decrypted)
            cfg.secret_values.update(collect_secret_values(decrypted))


def _selected_skill_slugs(agent_skills: list[dict[str, Any]] | None) -> list[str]:
    if not agent_skills:
        return []
    slugs: list[str] = []
    for raw in agent_skills:
        slug = raw.get("slug")
        if isinstance(slug, str) and slug:
            slugs.append(slug)
    return slugs


async def _prepare_skill_builder_components(
    cfg: AgentConfig,
    *,
    is_trigger_mode: bool,
    include_ask_user: bool,
) -> RuntimeComponents:
    """``runtime_profile='skill_builder'`` 전용 분기 (스펙 AD-3).

    표준 경로와의 차이: 코드 정의 프롬프트(``skill_builder/prompt.md``)로 교체,
    ``tools_config`` 루프·``execute_in_skill``·memory 도구·subagents·스킬 마운트
    전부 생략, 빌더 도구(validate_skill/generate_evals) append, 드래프트
    워크스페이스를 쓰기 가능 마운트. ``ask_user``/temporal 도구는 유지(명료화
    질문). 모델은 ``resolve_agent_context`` 가 이미 System LLM(text_primary)로
    재해석해 cfg에 채워 둔 값을 그대로 쓴다 (ADR-019).
    """

    workspace_path = cfg.draft_workspace_path or ""
    system_prompt = _system_prompt_with_temporal_context(load_skill_builder_prompt(workspace_path))
    model_candidates = _build_model_candidates(cfg)
    model = model_candidates[0]

    langchain_tools: list[BaseTool] = []
    if cfg.skill_builder_session_id and workspace_path:
        from app.database import async_session as _session_factory

        langchain_tools.extend(
            build_skill_builder_tools(
                session_id=cfg.skill_builder_session_id,
                workspace_path=workspace_path,
                session_factory=_session_factory,
                user_id=cfg.user_id,
                agent_id=cfg.agent_id,
                credential_subject_user_id=cfg.credential_subject_user_id,
                include_runtime_tools=True,
                consented_tools=cfg.skill_builder_consented_tools,
            )
        )
    _append_temporal_tools(langchain_tools)

    middleware = _build_default_reliability_middleware(
        model_candidates,
        configured_types=set(),
    )
    middleware += get_provider_middleware(cfg.provider)

    backend = FilesystemBackend(root_dir=str(_DATA_DIR), virtual_mode=True)
    permissions = build_filesystem_permissions(
        thread_id=cfg.thread_id,
        agent_id=cfg.agent_id,
        user_id=cfg.user_id,
        selected_skill_slugs=[],
        agent_runtime_name=cfg.agent_runtime_name,
        draft_workspace_path=workspace_path or None,
    )

    if include_ask_user and not is_trigger_mode:
        system_prompt += "\n\n" + _interactive_tool_instruction_prompt()
        langchain_tools.append(ask_user_tool)

    interrupt_on = _build_interrupt_on_policy(
        None,
        langchain_tools,
        include_ask_user=any(t.name == "ask_user" for t in langchain_tools),
        is_trigger_mode=is_trigger_mode,
    )
    if interrupt_on:
        # AD-3 과승인 방지 — 드래프트 점진 편집이 빌더의 핵심 UX이고 파일
        # 도구는 M2 권한으로 워크스페이스에 스코프되어 있으므로, deepagents
        # 기본 정책의 write_file/edit_file 승인 카드는 제외한다.
        for fs_tool_name in ("write_file", "edit_file"):
            interrupt_on.pop(fs_tool_name, None)
        # AD-4 세션 동의 — 동의된 도구는 정책에서 제외 (finalize_skill 불가,
        # requires_network 재검증은 resolve_agent_context 가 수행).
        from app.agent_runtime.skill_builder.tools import SESSION_CONSENT_ELIGIBLE_TOOLS

        for consented in cfg.skill_builder_consented_tools or []:
            if consented in SESSION_CONSENT_ELIGIBLE_TOOLS:
                interrupt_on.pop(consented, None)

    return RuntimeComponents(
        model_candidates=model_candidates,
        model=model,
        tools=langchain_tools,
        middleware=middleware,
        system_prompt=system_prompt,
        skills_sources=None,
        backend=backend,
        memory_sources=None,
        permissions=permissions,
        interrupt_on=interrupt_on or None,
    )


async def _prepare_runtime_components(
    cfg: AgentConfig,
    *,
    is_trigger_mode: bool,
    include_ask_user: bool,
    include_agent_memory_file: bool,
    timings: dict[str, int] | None = None,
) -> RuntimeComponents:
    """Build reusable Deep Agents runtime pieces for a parent or child agent."""

    if cfg.runtime_profile == "skill_builder":
        return await _prepare_skill_builder_components(
            cfg,
            is_trigger_mode=is_trigger_mode,
            include_ask_user=include_ask_user,
        )

    last_mark = time.perf_counter()

    def mark_timing(name: str) -> None:
        nonlocal last_mark
        if timings is None:
            return
        now = time.perf_counter()
        timings[name] = int((now - last_mark) * 1000)
        last_mark = now

    system_prompt = _system_prompt_with_temporal_context(cfg.system_prompt)
    system_prompt += "\n\n" + _artifact_file_instruction_prompt(cfg.thread_id)
    if include_ask_user and not is_trigger_mode:
        system_prompt += "\n\n" + _interactive_tool_instruction_prompt()
    model_candidates = _build_model_candidates(cfg)
    model = model_candidates[0]
    mark_timing("model_ms")

    langchain_tools: list[BaseTool] = []
    mcp_configs: list[dict] = []
    runtime_tool_configs = [
        *cfg.tools_config,
        *build_skill_dependency_tool_configs(
            agent_skills=cfg.agent_skills or [],
            existing_tool_configs=cfg.tools_config,
            user_id=cfg.user_id,
            agent_id=cfg.agent_id,
        ),
    ]

    for tc in runtime_tool_configs:
        if tc.get("mcp_server_url"):
            mcp_configs.append(tc)
            continue
        tool = create_tool_for_runtime(tc)
        if tool is not None:
            langchain_tools.append(tool)

    langchain_tools.extend(await _build_mcp_tools(mcp_configs))
    _append_temporal_tools(langchain_tools)
    _append_e2e_scripted_search_tool(langchain_tools)
    _append_e2e_ui_data_demo_tool(langchain_tools)

    memory_write_policy = await _memory_write_policy_for_run(
        cfg,
        is_trigger_mode=is_trigger_mode,
    )
    memory_tools_enabled = cfg.user_id is not None and memory_write_policy != "off"
    if memory_tools_enabled:
        memory_user_id = cfg.user_id
        assert memory_user_id is not None  # noqa: S101 — guarded by memory_tools_enabled (type narrowing)
        langchain_tools.extend(
            build_memory_tools(
                user_id=memory_user_id,
                agent_id=cfg.agent_id,
                conversation_id=cfg.thread_id,
                is_trigger_mode=is_trigger_mode,
            )
        )
    mark_timing("tools_ms")

    configured_mw_types = {
        str(c.get("type")) for c in (cfg.middleware_configs or []) if c.get("type")
    }
    filtered_mw = [
        c for c in (cfg.middleware_configs or []) if c.get("type") not in DEEPAGENT_BUILTIN_TYPES
    ]
    resolved_mw = _resolve_middleware_model_params(filtered_mw, cfg.provider_api_keys or {})
    middleware = _build_default_reliability_middleware(
        model_candidates,
        configured_types=configured_mw_types,
    )
    middleware += build_middleware_instances(resolved_mw)
    middleware += get_provider_middleware(cfg.provider)
    mark_timing("middleware_ms")

    backend = FilesystemBackend(root_dir=str(_DATA_DIR), virtual_mode=True)

    skills_sources: list[str] | None = None
    if cfg.agent_skills:
        skill_ctx = build_skill_runtime_context(
            cfg,
            data_dir=_DATA_DIR,
            output_root=Path(settings.conversation_output_dir),
        )
        if cfg.user_id:
            from app.database import async_session as _async_session_factory

            async with _async_session_factory() as _runtime_db:
                await resolve_runtime_credentials(skill_ctx, db=_runtime_db, cfg=cfg)
            # ADR-021 — union the just-resolved skill credential plaintext into
            # the run-scoped redaction set (lazy path). No-op when the run
            # ContextVar is unset (DB-free tests, trigger mode). Works for the
            # parent run; subagents share the same in-place set object.
            _add_skill_secrets_to_run(skill_ctx, cfg)
        skills_virtual_prefix = (
            f"/runtime/{cfg.thread_id}/agents/{cfg.agent_runtime_name}/skills/"
            if cfg.agent_runtime_name
            else f"/runtime/{cfg.thread_id}/skills/"
        )
        skills_sources = [skills_virtual_prefix]
        langchain_tools.append(_create_skill_execute_tool(skill_ctx))
        system_prompt += (
            "\n\n## 스킬 사용 규칙\n"
            "스킬을 사용할 때는 반드시 read_file 도구로 SKILL.md를 먼저 읽고 "
            "그 안의 지시를 직접 따르세요. "
            "스크립트 실행이 필요하면 execute_in_skill 도구를 사용하세요. "
            "task 도구의 subagent_type에 스킬 이름을 넣지 마세요. "
            "task 도구를 사용할 때 subagent_type은 task 도구 설명에 표시된 "
            "available subagent types 중 하나여야 합니다.\n"
            "스크립트 실행 후 OUTPUT_FILES에 이미지가 있으면 "
            "![image](/api/conversations/" + cfg.thread_id + "/files/<파일명>) 형식으로 표시하세요."
        )

        from app.skills.prompt import build_skills_prompt

        skills_block = build_skills_prompt(cfg.agent_skills)
        if skills_block:
            skills_block = skills_block.replace("/skills/", skills_virtual_prefix)
            system_prompt += "\n" + skills_block

    memory_sources: list[str] | None = None
    if include_agent_memory_file and cfg.agent_id:
        (_DATA_DIR / "agents" / cfg.agent_id).mkdir(parents=True, exist_ok=True)
        memory_sources = [f"/agents/{cfg.agent_id}/AGENTS.md"]

    if memory_tools_enabled:
        system_prompt += "\n\n" + _memory_tool_instruction_prompt()

    if include_agent_memory_file:
        memory_prompt, recalled_memories = await _load_memory_context(cfg)
        if memory_prompt:
            system_prompt += "\n\n" + memory_prompt
        if recalled_memories:
            # 러너가 stream head에서 moldy.memory_recalled 이벤트로 방출한다.
            # (subagent_display_names와 같은 cfg-경유 계약.)
            cfg.recalled_memories = recalled_memories

    permissions = build_filesystem_permissions(
        thread_id=cfg.thread_id,
        agent_id=cfg.agent_id,
        user_id=cfg.user_id,
        selected_skill_slugs=_selected_skill_slugs(cfg.agent_skills),
        agent_runtime_name=cfg.agent_runtime_name,
    )

    if include_ask_user and not is_trigger_mode:
        langchain_tools.append(ask_user_tool)

    interrupt_on = _build_interrupt_on_policy(
        cfg.middleware_configs,
        langchain_tools,
        include_ask_user=any(t.name == "ask_user" for t in langchain_tools),
        is_trigger_mode=is_trigger_mode,
    )
    mark_timing("skills_filesystem_ms")

    return RuntimeComponents(
        model_candidates=model_candidates,
        model=model,
        tools=langchain_tools,
        middleware=middleware,
        system_prompt=system_prompt,
        skills_sources=skills_sources,
        backend=backend,
        memory_sources=memory_sources,
        permissions=permissions,
        interrupt_on=interrupt_on,
    )


async def _prepare_agent(
    cfg: AgentConfig,
    *,
    messages_history: list[dict[str, str]],
    is_trigger_mode: bool = False,
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

    components = await _prepare_runtime_components(
        cfg,
        is_trigger_mode=is_trigger_mode,
        include_ask_user=not is_trigger_mode,
        include_agent_memory_file=True,
        timings=timings,
    )
    last_mark = time.perf_counter()

    # 5. 에이전트 빌드 — create_deep_agent + checkpointer
    from app.agent_runtime.checkpointer import get_checkpointer

    build_started = time.perf_counter()
    agent = build_agent(
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
    )
    timings["build_agent_ms"] = int((time.perf_counter() - build_started) * 1000)
    last_mark = time.perf_counter()

    lc_messages = convert_to_langchain_messages(messages_history)
    mark_timing("messages_ms")
    config: dict[str, Any] = {"configurable": {"thread_id": cfg.thread_id}}
    recursion_limit = _configured_recursion_limit(cfg)
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
