"""Skill Builder-specific pre-graph runtime preparation."""

from __future__ import annotations

from langchain_core.tools import BaseTool

from app.agent_runtime.runtime_config import AgentConfig, RuntimeComponents
from app.agent_runtime.runtime_preparation_support import RuntimePreparationBindings


async def prepare_skill_builder_components_impl(
    cfg: AgentConfig,
    *,
    is_trigger_mode: bool,
    include_ask_user: bool,
    run_id: str | None = None,
    scope_offload_backend: bool = False,
    bindings: RuntimePreparationBindings,
) -> RuntimeComponents:
    """Build the isolated Skill Builder prompt, tools, and filesystem policy."""

    workspace_path = cfg.draft_workspace_path or ""
    system_prompt = bindings.system_prompt_with_temporal_context(
        bindings.load_skill_builder_prompt(workspace_path)
    )
    model_candidates = bindings.build_model_candidates(cfg)
    model = model_candidates[0]

    langchain_tools: list[BaseTool] = []
    if cfg.skill_builder_session_id and workspace_path:
        langchain_tools.extend(
            bindings.build_skill_builder_tools(
                session_id=cfg.skill_builder_session_id,
                workspace_path=workspace_path,
                session_factory=bindings.runtime_db_session_factory,
                user_id=cfg.user_id,
                agent_id=cfg.agent_id,
                credential_subject_user_id=cfg.credential_subject_user_id,
                include_runtime_tools=True,
                consented_tools=cfg.skill_builder_consented_tools,
            )
        )
    bindings.append_temporal_tools(langchain_tools)

    middleware = bindings.build_default_reliability_middleware(
        model_candidates,
        configured_types=set(),
    )
    middleware += bindings.get_provider_middleware(cfg.provider)

    backend = (
        bindings.scoped_runtime_backend(cfg, run_id=run_id)
        if scope_offload_backend
        else bindings.state_backend()
    )
    permissions = bindings.build_filesystem_permissions(
        thread_id=cfg.thread_id,
        agent_id=cfg.agent_id,
        user_id=cfg.user_id,
        selected_skill_slugs=[],
        agent_runtime_name=cfg.agent_runtime_name,
        draft_workspace_path=workspace_path or None,
    )

    if include_ask_user and not is_trigger_mode:
        system_prompt += "\n\n" + bindings.interactive_tool_instruction_prompt()
        langchain_tools.append(bindings.ask_user_tool)

    interrupt_on = bindings.build_interrupt_on_policy(
        None,
        langchain_tools,
        include_ask_user=any(tool.name == "ask_user" for tool in langchain_tools),
        is_trigger_mode=is_trigger_mode,
    )
    if interrupt_on:
        for filesystem_tool_name in ("write_file", "edit_file"):
            interrupt_on.pop(filesystem_tool_name, None)
        for consented in cfg.skill_builder_consented_tools or []:
            if consented in bindings.session_consent_eligible_tools:
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
