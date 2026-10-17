"""Stored linked children for the prepared-run adapter (parent Task 10).

Resolved inside ``prepare_inputs(db, run)`` on the caller's locked session:
each child gets its own derived run identity, LLM key and tool configs exactly
like the legacy ``build_subagents_config``; its secrets are collected with the
same ``collect_cfg_secret_values`` over a child ``AgentConfig`` (LLM key,
provider keys, tool/MCP credentials, base-URL userinfo). Shapes the lease
cannot carry fail with ``prepared_run_unsupported:child:<name>:<reason>``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.credential_access import AgentCredentialAccess
from app.agent_runtime.credential_resolution import resolve_llm_api_key_for_agent
from app.agent_runtime.identity import derive_child_agent_run_identity
from app.agent_runtime.prepared_run_input_builder import PreparedChildSource
from app.agent_runtime.prepared_run_input_policy import (
    configured_middleware_types,
    lease_model_spec,
    unsupported,
)
from app.agent_runtime.run_secrets import collect_cfg_secret_values
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.runtime_preparation_support import RuntimePreparationBindings
from app.agent_runtime.subagents import (
    _parent_identity_from_config,
    _subagent_description,
    _subagent_system_prompt,
)
from app.error_codes import agent_not_found
from app.models.agent import Agent
from app.services.authorized_runtime import (
    authorized_skills,
    bind_authorized_runtime,
    plan_authorized_runtime,
)


@dataclass
class PreparedChildren:
    children: list[PreparedChildSource] = field(default_factory=list)
    secret_values: set[str] = field(default_factory=set)
    reference_ids: list[str] = field(default_factory=list)
    versions: dict[str, str] = field(default_factory=dict)


async def resolve_prepared_children(
    db: AsyncSession,
    agent: Agent,
    cfg: AgentConfig,
    bindings: RuntimePreparationBindings,
    *,
    is_trigger_mode: bool,
    write_policy: Callable[..., Awaitable[str]],
) -> PreparedChildren:
    """Resolve stored one-hop linked children under the held locks."""

    context = cfg.org_context
    parent_identity = _parent_identity_from_config(cfg, is_trigger_mode=is_trigger_mode)
    out = PreparedChildren()
    parent_plan = cfg.access_plan  # F-06: the links the parent's run may bind
    links = parent_plan.sub_agent_links if parent_plan is not None else agent.sub_agent_links
    for link in links:
        child = link.sub_agent
        if child is None:
            continue
        if parent_plan is None and (child.user_id, child.org_id, child.tenant_id) != (
            context.user_id,
            context.org_id,
            context.tenant_id,
        ):
            raise agent_not_found()
        identity = derive_child_agent_run_identity(parent_identity, child)
        subject = f"child:{identity.runtime_name}:"
        if child.model is None:
            raise unsupported(f"{subject}child_model_missing")
        plan = await plan_authorized_runtime(child, context=context, identity=identity, root=False)
        if authorized_skills(plan):
            raise unsupported(f"{subject}child_skills")
        configured = configured_middleware_types(
            child.middleware_configs, bindings.deepagent_builtin_types
        )
        if configured:
            raise unsupported(f"{subject}middleware:{configured[0]}")
        if bindings.get_provider_middleware(child.model.provider):
            raise unsupported(f"{subject}provider_middleware:{child.model.provider}")
        api_key = await resolve_llm_api_key_for_agent(
            db, child, AgentCredentialAccess(context, identity)
        )
        child_runtime = await bind_authorized_runtime(
            plan, path="subagent", db=db, identity=identity, conversation_id=cfg.thread_id
        )
        tool_configs = child_runtime.tools_config
        child_cfg = AgentConfig(
            org_context=context,
            provider=child.model.provider,
            model_name=child.model.model_name,
            api_key=api_key,
            base_url=child.model.base_url,
            system_prompt="",
            tools_config=tool_configs,
            thread_id=cfg.thread_id,
            runtime_policy=cfg.runtime_policy,
            agent_id=str(child.id),
            user_id=str(child.user_id),
            provider_api_keys={child.model.provider: api_key} if api_key else None,
        )
        policy = await write_policy(child_cfg, db, is_trigger_mode=is_trigger_mode)
        if policy != "off":
            raise unsupported(f"{subject}memory_tools")
        out.secret_values |= collect_cfg_secret_values(child_cfg)
        if child.llm_credential_id:
            out.reference_ids.append(f"llm_credential:{child.llm_credential_id}")
        updated = child.updated_at.isoformat() if child.updated_at else ""
        out.versions[f"child:{child.id}:updated_at"] = updated
        out.children.append(
            PreparedChildSource(
                runtime_name=identity.runtime_name,
                display_name=child.name,
                description=_subagent_description(child),
                base_prompt=_subagent_system_prompt(child),
                model=lease_model_spec(
                    provider=child.model.provider,
                    model_name=child.model.model_name,
                    api_key=api_key,
                    base_url=child.model.base_url,
                    model_params=child.model_params,
                    context_window=child.model.context_window,
                    subject=subject,
                ),
                tool_configs=tuple(tool_configs),
                middleware_configs=tuple(child.middleware_configs or ()),
            )
        )
    return out
