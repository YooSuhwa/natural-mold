"""Trigger-mode ``AgentConfig`` construction shared by legacy and prepared triggers.

The legacy invoke path (``trigger_execution_invoke``) and the prepared-lease
trigger resolver (``services.prepared_trigger_resolver``, Task 10 I18) build the
exact same root config from already-resolved values, so both entries agree on
every field. No lookups happen here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agent_runtime.identity import AgentRunIdentity
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.runtime_policy import ResolvedRuntimePolicy
from app.authz.org_context import OrgContext
from app.authz.runtime_access_planner import RuntimeAccessPlan
from app.models.agent import Agent


@dataclass(frozen=True, slots=True)
class TriggerConfigSource:
    agent: Agent
    context: OrgContext
    identity: AgentRunIdentity
    effective_prompt: str
    agent_skills: list[dict[str, Any]]
    tools_config: list[dict[str, Any]]
    thread_id: str
    runtime_policy: ResolvedRuntimePolicy
    api_key: str | None
    fallback_chain: list[dict[str, Any]] | None
    access_plan: RuntimeAccessPlan | None = None  # F-06: children follow the parent plan


def build_trigger_agent_config(source: TriggerConfigSource) -> AgentConfig:
    agent, identity, api_key = source.agent, source.identity, source.api_key
    model = agent.model
    if model is None:
        raise ValueError("agent has no model bound")
    return AgentConfig(
        org_context=source.context,
        provider=model.provider,
        model_name=model.model_name,
        api_key=api_key,
        base_url=model.base_url,
        system_prompt=source.effective_prompt,
        tools_config=source.tools_config,
        thread_id=source.thread_id,
        runtime_policy=source.runtime_policy,
        model_params=agent.model_params,
        middleware_configs=agent.middleware_configs,
        agent_skills=source.agent_skills or None,
        agent_id=str(agent.id),
        agent_name=agent.name,
        provider_api_keys={model.provider: api_key} if api_key else None,
        context_window=model.context_window,
        user_id=str(agent.user_id),
        model_id=str(model.id),
        llm_credential_id=(
            str(agent.llm_credential.id) if agent.llm_credential is not None else None
        ),
        model_fallback_chain=source.fallback_chain,
        agent_owner_user_id=str(agent.user_id),
        caller_user_id=None,
        credential_subject_user_id=str(identity.credential_subject_user_id),
        identity_mode=identity.identity_mode,
        agent_runtime_name=identity.runtime_name,
        access_plan=source.access_plan,
    )
