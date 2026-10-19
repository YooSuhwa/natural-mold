"""Conversation AgentConfig resolution extracted from ``conversation_stream_service``."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.credential_access import AgentCredentialAccess
from app.agent_runtime.identity import (
    AgentRunSource,
    make_agent_runtime_name,
    resolve_agent_run_identity,
)
from app.agent_runtime.run_secrets import collect_cfg_secret_values
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.runtime_policy import (
    SKILL_BUILDER_RUNTIME_POLICY,
    ResolvedRuntimePolicy,
    RuntimePolicySnapshotError,
)
from app.agent_runtime.subagents import build_subagents_config
from app.authz.conversation_scope import require_private_conversation
from app.authz.org_context import OrgContext
from app.dependencies import CurrentUser
from app.error_codes import agent_not_found, conversation_not_found
from app.exceptions import ConflictError
from app.models.agent import AGENT_RUNTIME_PROFILE_SKILL_BUILDER
from app.services import chat_service
from app.services.authorized_runtime import prepare_authorized_runtime
from app.services.conversation_runtime_policy import snapshot_from_conversation
from app.services.conversation_stream_protocol import stream_attr
from app.services.runtime_links import runnable_agent


async def resolve_agent_context(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    user: CurrentUser,
    *,
    context: OrgContext,
    checkpoint_id: str | None = None,
    include_subagents: bool = True,
) -> AgentConfig:
    """conversation + agent -> AgentConfig; ``include_subagents=False`` skips legacy children."""

    await require_private_conversation(db, conversation_id, context)
    conv = await chat_service.get_owned_conversation_with_agent(db, conversation_id, context)
    if not conv or (conv.user_id, conv.org_id, conv.tenant_id) != (
        context.user_id,
        context.org_id,
        context.tenant_id,
    ):
        raise conversation_not_found()

    agent = conv.agent
    if agent is None:
        raise agent_not_found()

    agent = await runnable_agent(db, agent, context)  # F-03: no broken links (10.6)

    try:
        runtime_policy = snapshot_from_conversation(conv)
    except RuntimePolicySnapshotError as exc:
        raise ConflictError(exc.code, exc.code) from None
    if runtime_policy is None:
        raise ConflictError(
            "RUNTIME_POLICY_SNAPSHOT_INVALID",
            "RUNTIME_POLICY_SNAPSHOT_INVALID",
        )

    if agent.runtime_profile == AGENT_RUNTIME_PROFILE_SKILL_BUILDER:
        return await _resolve_skill_builder_agent_context(
            db,
            conv,
            agent,
            user,
            runtime_policy=SKILL_BUILDER_RUNTIME_POLICY,
            context=context,
            checkpoint_id=checkpoint_id,
        )

    if agent.model is None:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=422,
            detail=(
                "agent has no model bound — open agent settings and pick a model before chatting."
            ),
        )

    identity = resolve_agent_run_identity(
        agent_id=agent.id,
        agent_owner_user_id=agent.user_id,
        runtime_name=agent.runtime_name or make_agent_runtime_name(agent.id),
        identity_mode=agent.identity_mode,
        source=AgentRunSource.CHAT,
        caller_user_id=user.id,
    )

    api_key = await stream_attr("resolve_llm_api_key_for_agent")(
        db, agent, AgentCredentialAccess(context, identity)
    )
    base_url = agent.model.base_url

    runtime = await prepare_authorized_runtime(
        agent,
        path="chat",
        context=context,
        db=db,
        identity=identity,
        conversation_id=str(conversation_id),
    )

    fallback_chain = await stream_attr("resolve_fallback_chain")(db, agent.model_fallback_list)
    provider_api_keys: dict[str, str | None] | None = (
        {agent.model.provider: api_key} if api_key else None
    )

    effective_prompt = stream_attr("with_user_display_name_context")(
        chat_service.build_effective_prompt(agent),
        user,
    )

    cfg = AgentConfig(
        org_context=context,
        provider=agent.model.provider,
        model_name=agent.model.model_name,
        api_key=api_key,
        base_url=base_url,
        system_prompt=effective_prompt,
        tools_config=runtime.tools_config,
        access_plan=runtime.plan,
        thread_id=str(conversation_id),
        runtime_policy=runtime_policy,
        model_params=agent.model_params,
        middleware_configs=agent.middleware_configs,
        agent_skills=runtime.agent_skills or None,
        agent_id=str(agent.id),
        agent_name=agent.name,
        provider_api_keys=provider_api_keys,
        cost_per_input_token=(
            float(agent.model.cost_per_input_token) if agent.model.cost_per_input_token else None
        ),
        cost_per_output_token=(
            float(agent.model.cost_per_output_token) if agent.model.cost_per_output_token else None
        ),
        context_window=agent.model.context_window,
        user_id=str(user.id),
        model_id=str(agent.model.id) if agent.model else None,
        llm_credential_id=(
            str(agent.llm_credential.id) if agent.llm_credential is not None else None
        ),
        model_fallback_chain=fallback_chain,
        checkpoint_id=checkpoint_id,
        agent_owner_user_id=str(agent.user_id),
        caller_user_id=str(user.id),
        credential_subject_user_id=str(identity.credential_subject_user_id),
        identity_mode=identity.identity_mode,
        agent_runtime_name=identity.runtime_name,
    )
    if include_subagents:
        cfg.subagents_config, cfg.subagent_display_names = await build_subagents_config(
            agent,
            db=db,
            parent_cfg=cfg,
            is_trigger_mode=False,
        )
    # ``.update`` (not ``=``) so the subagent secrets already unioned into the
    # set by ``build_subagents_config`` above survive (ADR-021 H1).
    cfg.secret_values.update(collect_cfg_secret_values(cfg))
    return cfg


async def _resolve_skill_builder_agent_context(
    db: AsyncSession,
    conv: Any,
    agent: Any,
    user: CurrentUser,
    *,
    context: OrgContext,
    runtime_policy: ResolvedRuntimePolicy,
    checkpoint_id: str | None,
) -> AgentConfig:
    """히든 빌더 에이전트의 대화 → AgentConfig (스펙 AD-1/AD-3/AD-5).

    모델은 seed 시점 FK가 아니라 **항상** System LLM(text_primary)로 재해석하고
    (ADR-019), 빌더 세션을 conversation_id 역참조로 찾아 드래프트 워크스페이스
    경로와 ``moldy.skill_draft`` stream-head 페이로드를 cfg에 싣는다.
    """

    from app.error_codes import system_llm_not_configured
    from app.services import skill_draft_workspace
    from app.services.skill_builder_run_access import load_skill_builder_run_session
    from app.services.system_credential_resolver import (
        SystemModelNotConfiguredError,
        resolve_system_model,
    )

    if user.id != context.user_id:
        raise conversation_not_found()
    await require_private_conversation(db, conv.id, context)

    # F-13: session owner + target decision (skill can_edit / cap_create_skill).
    session = await load_skill_builder_run_session(db, conv.id, context)
    if session is None:
        # 빌더 대화인데 매칭 세션이 없거나 남의 세션 — 접근 불가로 통일
        # (enumeration-safe 404).
        raise conversation_not_found()

    if session.draft_workspace_path is not None and (
        session.draft_workspace_path != skill_draft_workspace.workspace_storage_path(session.id)
    ):
        raise conversation_not_found()

    try:
        resolved = await resolve_system_model(db, "text_primary")
    except SystemModelNotConfiguredError as exc:
        raise system_llm_not_configured() from exc

    if not session.draft_workspace_path:
        # start v2가 항상 채우지만, 방어적으로 재생성 (멱등).
        session.draft_workspace_path = skill_draft_workspace.create_workspace(session.id)
        await db.flush()

    # AD-4 — 세션 동의를 정책 제외 목록으로 스레딩. 드래프트가 지금
    # ``requires_network`` 상태면 기록된 동의가 있어도 무시한다 (동의 이후
    # 드래프트가 네트워크 요구로 바뀌었을 수 있음 — 매 resolve 재검증).
    from app.agent_runtime.skill_builder.tools import SESSION_CONSENT_ELIGIBLE_TOOLS

    consented_tools: list[str] = []
    consent_offer_tools: list[str] = []
    if not skill_draft_workspace.draft_requires_network(session.draft_workspace_path):
        consented_tools = sorted(
            name for name in (session.tool_consents or {}) if name in SESSION_CONSENT_ELIGIBLE_TOOLS
        )
        # 아직 동의되지 않은 eligible 도구 → 승인 카드에 동의 옵션 노출.
        consent_offer_tools = sorted(SESSION_CONSENT_ELIGIBLE_TOOLS - set(consented_tools))

    identity = resolve_agent_run_identity(
        agent_id=agent.id,
        agent_owner_user_id=agent.user_id,
        runtime_name=agent.runtime_name or make_agent_runtime_name(agent.id),
        identity_mode=agent.identity_mode,
        source=AgentRunSource.CHAT,
        caller_user_id=user.id,
    )

    cfg = AgentConfig(
        org_context=context,
        provider=resolved.provider,
        model_name=resolved.model_name,
        api_key=resolved.api_key,
        base_url=resolved.base_url,
        # 자리표시자 — ``_prepare_skill_builder_components`` 가 prompt.md로 교체.
        system_prompt=agent.system_prompt,
        tools_config=[],
        thread_id=str(conv.id),
        runtime_policy=runtime_policy,
        agent_id=str(agent.id),
        agent_name=agent.name,
        user_id=str(user.id),
        model_id=str(agent.model_id) if agent.model_id else None,
        checkpoint_id=checkpoint_id,
        agent_owner_user_id=str(agent.user_id),
        caller_user_id=str(user.id),
        credential_subject_user_id=str(identity.credential_subject_user_id),
        identity_mode=identity.identity_mode,
        agent_runtime_name=identity.runtime_name,
        runtime_profile="skill_builder",
        skill_builder_session_id=str(session.id),
        draft_workspace_path=session.draft_workspace_path,
        skill_draft_brief=skill_draft_workspace.build_skill_draft_brief(session),
        skill_builder_consented_tools=consented_tools or None,
        skill_builder_consent_offer_tools=consent_offer_tools or None,
    )
    cfg.secret_values.update(collect_cfg_secret_values(cfg))
    return cfg
