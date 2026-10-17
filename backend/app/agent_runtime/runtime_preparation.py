"""Pre-graph runtime component preparation.

The compatibility facade passes a fresh immutable binding snapshot on every
call.  This keeps its established monkeypatch surface effective without a
reverse import from the implementation boundary.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool

from app.agent_runtime.runtime.connection_guard import with_connection_guard
from app.agent_runtime.runtime.shared_memo import load_shared_memo, memo_governed
from app.agent_runtime.runtime_config import AgentConfig, RuntimeComponents
from app.agent_runtime.runtime_hydration_guard import assert_pre_marker
from app.agent_runtime.runtime_policy_capabilities import STORED_RESERVED_TOOL_NAMES
from app.agent_runtime.runtime_preparation_support import RuntimePreparationBindings

_STORED_FILESYSTEM_INTERRUPT_NAMES = frozenset(
    {"delete", "execute", "execute_in_skill", "shell", "write_file", "edit_file"}
)


def _without_stored_filesystem_interrupts(
    interrupt_on: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if interrupt_on is None:
        return None
    retained = {
        tool_name: config
        for tool_name, config in interrupt_on.items()
        if tool_name not in _STORED_FILESYSTEM_INTERRUPT_NAMES
    }
    return retained or None


async def prepare_runtime_components_impl(
    cfg: AgentConfig,
    *,
    is_trigger_mode: bool,
    include_ask_user: bool,
    include_agent_memory_file: bool,
    timings: dict[str, int] | None = None,
    run_id: str | None = None,
    scope_offload_backend: bool = False,
    bindings: RuntimePreparationBindings,
) -> RuntimeComponents:
    """Build standard or trigger components before Deep Agents graph creation."""
    assert_pre_marker("prepare_runtime_components_impl")

    last_mark = bindings.perf_counter()

    def mark_timing(name: str) -> None:
        nonlocal last_mark
        if timings is None:
            return
        now = bindings.perf_counter()
        timings[name] = int((now - last_mark) * 1000)
        last_mark = now

    system_prompt = bindings.system_prompt_with_temporal_context(cfg.system_prompt)
    stored_policy = cfg.runtime_policy.source == "stored"
    filesystem_mode = cfg.runtime_policy.effective.filesystem.mode
    if not stored_policy or filesystem_mode == "artifact_write":
        system_prompt += "\n\n" + bindings.artifact_file_instruction_prompt(cfg.thread_id)
    if include_ask_user and not is_trigger_mode:
        system_prompt += "\n\n" + bindings.interactive_tool_instruction_prompt()
    model_candidates = bindings.build_model_candidates(cfg)
    model = model_candidates[0]
    mark_timing("model_ms")

    langchain_tools: list[BaseTool] = []
    mcp_configs: list[dict[str, Any]] = []
    runtime_tool_configs = [
        *cfg.tools_config,
        *bindings.build_skill_dependency_tool_configs(
            agent_skills=cfg.agent_skills or [],
            existing_tool_configs=cfg.tools_config,
            user_id=cfg.user_id,
            agent_id=cfg.agent_id,
        ),
    ]
    for tool_config in runtime_tool_configs:
        if tool_config.get("mcp_server_url"):
            mcp_configs.append(tool_config)
            continue
        tool = bindings.create_tool_for_runtime(tool_config)
        if tool is not None:
            langchain_tools.append(tool)

    langchain_tools.extend(await bindings.build_mcp_tools(mcp_configs))
    bindings.append_temporal_tools(langchain_tools)
    bindings.append_e2e_scripted_search_tool(langchain_tools)
    bindings.append_e2e_ui_data_demo_tool(langchain_tools)

    memory_write_policy = await bindings.memory_write_policy_for_run(
        cfg,
        is_trigger_mode=is_trigger_mode,
    )
    memory_tools_enabled = cfg.user_id is not None and memory_write_policy != "off"
    # F-09 (11.8): under ``agent`` enforcement no run writes or mounts AGENTS.md (the
    # memo reaches the prompt framed as reference); a runner of someone else's agent may
    # propose to it when it is switched on. Legacy: unchanged.
    governed = memo_governed()
    shared_run = bool(cfg.agent_owner_user_id) and cfg.agent_owner_user_id != cfg.user_id
    memo_proposals = (
        governed
        and shared_run
        and memory_tools_enabled
        and (await load_shared_memo(cfg, None)).enabled
    )
    if memory_tools_enabled:
        memory_user_id = cfg.user_id
        assert memory_user_id is not None  # noqa: S101 - narrowed by memory_tools_enabled
        langchain_tools.extend(
            bindings.build_memory_tools(
                context=cfg.org_context,
                agent_id=cfg.agent_id,
                conversation_id=cfg.thread_id,
                is_trigger_mode=is_trigger_mode,
                agent_memo_proposals=memo_proposals,
            )
        )
    mark_timing("tools_ms")

    configured_middleware_types = {
        str(config.get("type")) for config in (cfg.middleware_configs or []) if config.get("type")
    }
    filtered_middleware = [
        config
        for config in (cfg.middleware_configs or [])
        if config.get("type") not in bindings.deepagent_builtin_types
    ]
    resolved_middleware = bindings.resolve_middleware_model_params(
        filtered_middleware,
        cfg.provider_api_keys or {},
    )
    middleware = bindings.build_default_reliability_middleware(
        model_candidates,
        configured_types=configured_middleware_types,
    )
    middleware += bindings.build_middleware_instances(resolved_middleware)
    middleware += bindings.get_provider_middleware(cfg.provider)
    mark_timing("middleware_ms")

    backend = (
        bindings.scoped_runtime_backend(cfg, run_id=run_id)
        if scope_offload_backend
        else bindings.state_backend()
    )

    skills_sources: list[str] | None = None
    if cfg.agent_skills:
        skill_ctx = bindings.build_skill_runtime_context(
            cfg,
            data_dir=bindings.data_dir,
            output_root=bindings.conversation_output_dir,
        )
        if cfg.user_id and not stored_policy:
            async with bindings.runtime_db_session_factory(cfg.org_context) as runtime_db:
                await bindings.resolve_runtime_credentials(skill_ctx, db=runtime_db, cfg=cfg)
            bindings.add_skill_secrets_to_run(skill_ctx, cfg)
        skills_virtual_prefix = (
            f"/runtime/{cfg.thread_id}/agents/{cfg.agent_runtime_name}/skills/"
            if cfg.agent_runtime_name
            else f"/runtime/{cfg.thread_id}/skills/"
        )
        skills_sources = [skills_virtual_prefix]
        if stored_policy:
            system_prompt += (
                "\n\n## 스킬 사용 규칙\n"
                "스킬을 사용할 때는 반드시 read_file 도구로 SKILL.md를 먼저 읽고 "
                "그 안의 지시를 직접 따르세요. "
                "task 도구의 subagent_type에 스킬 이름을 넣지 마세요. "
                "task 도구를 사용할 때 subagent_type은 task 도구 설명에 표시된 "
                "available subagent types 중 하나여야 합니다."
            )
        else:
            langchain_tools.append(bindings.create_skill_execute_tool(skill_ctx))
            system_prompt += (
                "\n\n## 스킬 사용 규칙\n"
                "스킬을 사용할 때는 반드시 read_file 도구로 SKILL.md를 먼저 읽고 "
                "그 안의 지시를 직접 따르세요. "
                "스크립트 실행이 필요하면 execute_in_skill 도구를 사용하세요. "
                "task 도구의 subagent_type에 스킬 이름을 넣지 마세요. "
                "task 도구를 사용할 때 subagent_type은 task 도구 설명에 표시된 "
                "available subagent types 중 하나여야 합니다.\n"
                "스크립트 실행 후 OUTPUT_FILES에 이미지가 있으면 "
                "![image](/api/conversations/"
                + cfg.thread_id
                + "/files/<파일명>) 형식으로 표시하세요."
            )
        skills_block = bindings.build_skills_prompt(cfg.agent_skills)
        if skills_block:
            skills_block = skills_block.replace("/skills/", skills_virtual_prefix)
            system_prompt += "\n" + skills_block

    memory_sources: list[str] | None = None
    if include_agent_memory_file and cfg.agent_id and not governed:
        (bindings.data_dir / "agents" / cfg.agent_id).mkdir(parents=True, exist_ok=True)
        memory_sources = [f"/agents/{cfg.agent_id}/AGENTS.md"]

    if memory_tools_enabled:
        system_prompt += "\n\n" + bindings.memory_tool_instruction_prompt()

    if include_agent_memory_file:
        memory_prompt, recalled_memories = await bindings.load_memory_context(cfg)
        if memory_prompt:
            system_prompt += "\n\n" + memory_prompt
        if recalled_memories:
            cfg.recalled_memories = recalled_memories

    if stored_policy:
        permissions = bindings.build_stored_filesystem_permissions(
            thread_id=cfg.thread_id,
            agent_id=cfg.agent_id,
            user_id=cfg.user_id,
            selected_skill_slugs=bindings.selected_skill_slugs(cfg.agent_skills),
            agent_runtime_name=cfg.agent_runtime_name,
            include_agent_memory_file=include_agent_memory_file and not governed,
            mode=filesystem_mode,
        )
        langchain_tools = [
            tool for tool in langchain_tools if tool.name not in STORED_RESERVED_TOOL_NAMES
        ]
    else:
        permissions = bindings.build_filesystem_permissions(
            thread_id=cfg.thread_id,
            agent_id=cfg.agent_id,
            user_id=cfg.user_id,
            selected_skill_slugs=bindings.selected_skill_slugs(cfg.agent_skills),
            agent_runtime_name=cfg.agent_runtime_name,
            agent_memory_file=not governed,
        )

    if include_ask_user and not is_trigger_mode:
        langchain_tools.append(bindings.ask_user_tool)

    interrupt_on = bindings.build_interrupt_on_policy(
        cfg.middleware_configs,
        langchain_tools,
        include_ask_user=any(tool.name == "ask_user" for tool in langchain_tools),
        is_trigger_mode=is_trigger_mode,
    )
    if stored_policy:
        interrupt_on = _without_stored_filesystem_interrupts(interrupt_on)
    mark_timing("skills_filesystem_ms")

    # F-07 (11.4): root runs interrupt (triggers/invoke API raise); children only refuse.
    mode = (
        "refuse" if not include_agent_memory_file else ("raise" if is_trigger_mode else "interrupt")
    )
    middleware = with_connection_guard(middleware, cfg, langchain_tools, mode=mode)
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
