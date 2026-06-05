from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.credential_resolution import resolve_llm_api_key_for_agent
from app.agent_runtime.executor import AgentConfig, _prepare_runtime_components
from app.agent_runtime.identity import (
    AgentRunIdentity,
    AgentRunSource,
    derive_child_agent_run_identity,
    make_agent_runtime_name,
)
from app.models.agent import Agent
from app.services import chat_service


def _uuid_from_config(value: str | None, *, fallback: str | None = None) -> uuid.UUID:
    raw = value or fallback
    if not raw:
        raise ValueError("parent AgentConfig is missing identity fields")
    return uuid.UUID(str(raw))


def _parent_identity_from_config(
    cfg: AgentConfig,
    *,
    is_trigger_mode: bool,
) -> AgentRunIdentity:
    agent_id = _uuid_from_config(cfg.agent_id)
    owner_user_id = _uuid_from_config(cfg.agent_owner_user_id, fallback=cfg.user_id)
    caller_user_id = uuid.UUID(str(cfg.caller_user_id)) if cfg.caller_user_id else None
    credential_subject_user_id = _uuid_from_config(
        cfg.credential_subject_user_id,
        fallback=cfg.user_id,
    )
    runtime_name = cfg.agent_runtime_name or make_agent_runtime_name(agent_id)
    return AgentRunIdentity(
        agent_id=agent_id,
        agent_owner_user_id=owner_user_id,
        caller_user_id=caller_user_id,
        credential_subject_user_id=credential_subject_user_id,
        identity_mode=cfg.identity_mode or "fixed",
        runtime_name=runtime_name,
        source=AgentRunSource.TRIGGER if is_trigger_mode else AgentRunSource.CHAT,
    )


def _subagent_description(agent: Agent) -> str:
    pieces = [f"Display name: {agent.name}."]
    if agent.description:
        pieces.append(f"Role: {agent.description}")
    pieces.append(
        "Use this subagent when the parent task matches this role. "
        "Return one concise final report to the parent agent."
    )
    return "\n".join(pieces)


def _subagent_system_prompt(agent: Agent) -> str:
    return (
        chat_service.build_effective_prompt(agent)
        + "\n\n"
        + "You are running as an ephemeral subagent. "
        + "Complete the delegated task autonomously and return one final report. "
        + "If required information is missing, state exactly what the parent should ask the user."
    )


async def build_subagents_config(
    parent_agent: Agent,
    *,
    db: AsyncSession,
    parent_cfg: AgentConfig,
    is_trigger_mode: bool,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Convert one-hop linked child agents into Deep Agents SubAgent specs."""

    parent_identity = _parent_identity_from_config(
        parent_cfg,
        is_trigger_mode=is_trigger_mode,
    )
    subagents: list[dict[str, Any]] = []
    display_names: dict[str, str] = {}

    for link in parent_agent.sub_agent_links:
        child = link.sub_agent
        if child is None:
            continue
        if child.model is None:
            raise ValueError(f"linked subagent {child.id} has no model bound")

        child_identity = derive_child_agent_run_identity(parent_identity, child)
        child_api_key = await resolve_llm_api_key_for_agent(
            db,
            child,
            identity=child_identity,
        )
        child_tools_config = await chat_service.build_tools_config(
            child,
            db=db,
            conversation_id=parent_cfg.thread_id,
            identity=child_identity,
        )
        child_cfg = AgentConfig(
            provider=child.model.provider,
            model_name=child.model.model_name,
            api_key=child_api_key,
            base_url=child.model.base_url,
            system_prompt=_subagent_system_prompt(child),
            tools_config=child_tools_config,
            thread_id=parent_cfg.thread_id,
            model_params=child.model_params,
            middleware_configs=child.middleware_configs,
            agent_skills=chat_service.build_agent_skills(child) or None,
            agent_id=str(child.id),
            agent_name=child.name,
            provider_api_keys={child.model.provider: child_api_key} if child_api_key else None,
            user_id=str(child.user_id),
            model_id=str(child.model.id),
            llm_credential_id=(str(child.llm_credential_id) if child.llm_credential_id else None),
            agent_owner_user_id=str(child_identity.agent_owner_user_id),
            caller_user_id=(
                str(child_identity.caller_user_id) if child_identity.caller_user_id else None
            ),
            credential_subject_user_id=str(child_identity.credential_subject_user_id),
            identity_mode=child_identity.identity_mode,
            agent_runtime_name=child_identity.runtime_name,
        )
        components = await _prepare_runtime_components(
            child_cfg,
            is_trigger_mode=is_trigger_mode,
            include_ask_user=False,
            include_agent_memory_file=False,
        )

        spec: dict[str, Any] = {
            "name": child_identity.runtime_name,
            "description": _subagent_description(child),
            "system_prompt": components.system_prompt,
            "model": components.model,
            "tools": components.tools,
        }
        if components.middleware:
            spec["middleware"] = components.middleware
        if components.skills_sources is not None:
            spec["skills"] = components.skills_sources
        if components.permissions:
            spec["permissions"] = components.permissions
        if components.interrupt_on is not None:
            spec["interrupt_on"] = components.interrupt_on

        subagents.append(spec)
        display_names[child_identity.runtime_name] = child.name

    return subagents, display_names
