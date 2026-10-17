from __future__ import annotations

import uuid
from typing import Any, Literal, cast

import httpx
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.middleware_registry import get_middleware_registry
from app.agent_runtime.runtime_policy import resolve_runtime_policy
from app.authz.agent_delegations import commit_and_apply
from app.authz.agent_lifecycle_scope import (
    AgentLifecycleAuthorization,
    authorize_agent_lifecycle,
)
from app.authz.org_context import OrgContext
from app.authz.policies import CANDIDATES, CAP, CATALOG, GOVERNANCE, RES, authz_policy
from app.authz.resource_lifecycle import on_resource_created
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.dependencies import CurrentUser, get_current_user, get_db, get_org_context, verify_csrf
from app.error_codes import agent_not_found, image_not_found
from app.exceptions import AppError, ExternalServiceError, ValidationError
from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent
from app.schemas.agent import (
    AgentBrief,
    AgentCreate,
    AgentResponse,
    AgentUpdate,
    GenerateImageResponse,
    McpToolBrief,
    ToolBrief,
)
from app.schemas.agent_access import AgentDetail, AgentListItem, AgentSummaryItem
from app.schemas.agent_lifecycle import (
    AgentLifecycleMetadataResponse,
    AgentPurgeReceiptResponse,
    AgentPurgeRequest,
    AgentRestoreResponse,
)
from app.schemas.skill import SkillBrief
from app.services import (
    agent_access_views,
    agent_service,
    audit_service,
    image_service,
)
from app.services.agent_execution_owner import notify_local_owner
from app.services.agent_image_paths import build_agent_image_url, resolve_agent_image_path
from app.services.agent_lifecycle import archive_agent, list_archived_agents, restore_agent
from app.services.agent_purge_service import purge_agent
from app.services.agent_purge_types import AgentPurgeCommand
from app.services.agent_update_service import linked_targets
from app.services.conversation_run_worker import get_run_task_registry
from app.services.image_preview import get_or_create_image_preview_async
from app.tools.registry import registry as tool_registry

router = APIRouter(prefix="/api/agents", tags=["agents"])
middleware_router = APIRouter(tags=["middlewares"])


def _sub_agent_image_url(sub: Agent) -> str | None:
    """Compute image_url for a sub-agent (mirrors _agent_to_response logic)."""
    return build_agent_image_url(sub.id, updated_at=sub.updated_at, image_path=sub.image_path)


def _require_standard_profile(agent: Agent) -> None:
    """히든 런타임 에이전트(skill builder 등)는 변조 불가 — enumeration-safe 404.

    404로 통일해 존재 여부 oracle을 만들지 않는다 (없음/숨김 응답 동일).
    GET 단건은 빌더 챗 서피스가 에이전트 메타를 읽어야 하므로 막지 않는다.
    """
    if agent.runtime_profile != AGENT_RUNTIME_PROFILE_STANDARD:
        raise agent_not_found()


def _tool_icon_id(definition_key: str) -> str | None:
    """도구 registry 정의에서 icon_id를 해석. Tool ORM에는 icon_id 컬럼이 없고
    definition_key → 메모리 registry 정의가 ground truth다."""
    definition = tool_registry.get(definition_key)
    return definition.icon_id if definition is not None else None


def _agent_to_response(agent: Agent) -> AgentResponse:
    """Convert Agent model to AgentResponse with tool configs."""
    resolved_runtime_policy = resolve_runtime_policy(agent.runtime_policy)
    fallback_ids: list[uuid.UUID] = []
    if agent.model_fallback_list:
        for raw in agent.model_fallback_list:
            try:
                fallback_ids.append(uuid.UUID(str(raw)))
            except (TypeError, ValueError):
                # Skip malformed legacy entries; surface only valid UUIDs.
                continue
    return AgentResponse(
        id=agent.id,
        runtime_name=agent.runtime_name,
        identity_mode=agent.identity_mode,
        name=agent.name,
        description=agent.description,
        system_prompt=agent.system_prompt,
        # ``agent.model`` may be None when the FK target was deleted (legacy
        # rows from before the m18 wipe). The schema accepts None and the
        # frontend prompts re-binding instead of crashing the agents list.
        model=agent.model if agent.model is not None else None,
        tools=[
            ToolBrief(
                id=link.tool.id,
                name=link.tool.name,
                icon_id=_tool_icon_id(link.tool.definition_key),
            )
            for link in agent.tool_links
        ],
        mcp_tools=[
            McpToolBrief(
                id=link.mcp_tool.id,
                name=link.mcp_tool.name,
                server_id=link.mcp_tool.server_id,
            )
            for link in agent.mcp_tool_links
        ],
        skills=[
            SkillBrief(
                id=link.skill_id,
                name=link.skill.name,
                slug=link.skill.slug,
                kind=cast(Literal["text", "package"], link.skill.kind),
                description=link.skill.description,
            )
            for link in agent.skill_links
        ],
        sub_agents=[
            AgentBrief(
                id=link.sub_agent.id,
                name=link.sub_agent.name,
                description=link.sub_agent.description,
                image_url=_sub_agent_image_url(link.sub_agent),
            )
            for link in agent.sub_agent_links
        ],
        required_link_ids=[target for link, target in linked_targets(agent) if link.required],
        middleware_configs=agent.middleware_configs or [],
        runtime_policy=(
            resolved_runtime_policy.effective if agent.runtime_policy is not None else None
        ),
        runtime_policy_effective=resolved_runtime_policy.effective,
        runtime_policy_source=resolved_runtime_policy.source,
        status=agent.status,
        is_favorite=agent.is_favorite,
        model_params=agent.model_params,
        opener_questions=agent.opener_questions,
        model_fallback_ids=fallback_ids,
        image_url=build_agent_image_url(
            agent.id,
            updated_at=agent.updated_at,
            image_path=agent.image_path,
        ),
        template_id=agent.template_id,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
        # Set by ``list_agents`` (max conversations.updated_at). For single-row
        # endpoints the attribute is missing → schema defaults to None and the
        # sidebar falls back to ``updated_at`` for sort.
        last_used_at=getattr(agent, "_last_used_at", None),
        unread_count=getattr(agent, "_unread_count", 0),
    )


async def _scoped_agent_response(
    db: AsyncSession, agent_id: uuid.UUID, context: OrgContext
) -> AgentResponse:
    presentation = await agent_service.get_agent_projection(db, agent_id, context)
    if presentation is None:
        raise agent_not_found()
    return _agent_to_response(presentation.agent).model_copy(
        update={"unread_count": presentation.unread_count}
    )


async def _present(
    db: AsyncSession, authz: AuthzService, context: OrgContext, agent_id: uuid.UUID
) -> AgentDetail:
    return await agent_access_views.present_agent(db, authz, context, agent_id, _agent_to_response)


@router.get("", response_model=list[AgentListItem])
@authz_policy(CANDIDATES("agent", "can_run"))
async def list_agents(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
):
    agents = await agent_service.list_agents(db, context)
    if authz.mode == "legacy":
        return [_agent_to_response(a) for a in agents]
    return await agent_access_views.list_agent_views(db, authz, context, agents, _agent_to_response)


@router.get("/summary", response_model=list[AgentSummaryItem])
@authz_policy(CANDIDATES("agent", "can_run"))
async def list_agent_summaries(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
):
    summaries = await agent_service.list_agent_summaries(db, context)
    if authz.mode == "legacy":
        return summaries
    return await agent_access_views.list_summary_views(db, authz, context, summaries)


@router.get("/archived", response_model=list[AgentLifecycleMetadataResponse])
@authz_policy(GOVERNANCE)
async def list_archived_agent_metadata(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[AgentLifecycleMetadataResponse]:
    return await list_archived_agents(db, user)


@router.post("", response_model=AgentDetail, status_code=201)
@authz_policy(CAP("cap_create_agent"))
async def create_agent(
    data: AgentCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
):
    await authz.require_cap(context, "cap_create_agent", legacy=True)
    agent = await agent_service.create_agent(db, data, context)
    await audit_service.record_self_event(
        db,
        user,
        action="agent.create",
        target_type="agent",
        target_id=agent.id,
        target_name=agent.name,
        request=request,
        metadata={
            "model_id": str(data.model_id),
            "tool_count": len(data.tool_ids),
            "mcp_tool_count": len(data.mcp_tool_ids),
            "skill_count": len(data.skill_ids),
            "sub_agent_count": len(data.sub_agent_ids),
            "system_prompt_changed": True,
        },
    )
    created = await on_resource_created(db, agent, mode=authz.mode)
    await commit_and_apply(db, authz, created)  # with F-02 delegations (8.6 item 2)
    return await _present(db, authz, context, agent.id)


@router.get("/{agent_id}", response_model=AgentDetail)
@authz_policy(RES("agent", "can_run", param="agent_id", min_visible="can_run"))
async def get_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
):
    return await _present(db, authz, context, agent_id)


@router.put("/{agent_id}", response_model=AgentDetail)
@authz_policy(RES("agent", "can_edit", param="agent_id", min_visible="can_run"))
async def update_agent(
    agent_id: uuid.UUID,
    data: AgentUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
):
    agent = await agent_access_views.authorized_agent(db, authz, context, agent_id, "can_edit")
    _require_standard_profile(agent)
    updated = await agent_service.update_agent(db, agent, data, context=context)
    changed_fields = sorted(data.model_fields_set - {"system_prompt"})
    await audit_service.record_self_event(
        db,
        user,
        action="agent.update",
        target_type="agent",
        target_id=updated.id,
        target_name=updated.name,
        request=request,
        metadata={
            "changed_fields": changed_fields,
            "system_prompt_changed": "system_prompt" in data.model_fields_set,
        },
    )
    await commit_and_apply(db, authz)  # F-02 delegation tuples right after commit
    return await _present(db, authz, context, updated.id)


@router.patch("/{agent_id}/favorite", response_model=AgentDetail)
@authz_policy(RES("agent", "can_run", param="agent_id", min_visible="can_run"))
async def toggle_favorite(
    agent_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
):
    agent = await agent_access_views.authorized_agent(db, authz, context, agent_id, "can_run")
    _require_standard_profile(agent)
    updated, favorite = await agent_access_views.toggle_favorite(db, context, agent)
    await audit_service.record_self_event(
        db,
        user,
        action="agent.favorite",
        target_type="agent",
        target_id=updated.id,
        target_name=updated.name,
        request=request,
        metadata={"is_favorite": favorite},
    )
    await db.commit()
    return await _present(db, authz, context, updated.id)


@router.delete("/{agent_id}", status_code=204)
@authz_policy(GOVERNANCE)
async def delete_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
) -> None:
    try:
        await authorize_agent_lifecycle(
            db,
            user,
            AgentLifecycleAuthorization(agent_id=agent_id, action="archive", context=context),
        )
    except AppError as error:
        if error.code != "AGENT_LIFECYCLE_PERMISSION_REQUIRED":
            raise
        raise agent_not_found() from error
    result = await archive_agent(db, user, agent_id)
    await commit_and_apply(db)  # F-02: the archived subagent links' delegation revokes
    registry = get_run_task_registry()
    for intent in result.cancel_intents:
        notify_local_owner(intent, registry)
    if result.disabled_trigger_ids:
        from app.scheduler import remove_trigger_job

        for trigger_id in result.disabled_trigger_ids:
            remove_trigger_job(trigger_id)


@router.post("/{agent_id}/restore", response_model=AgentRestoreResponse)
@authz_policy(GOVERNANCE)
async def restore_archived_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> AgentRestoreResponse:
    response = await restore_agent(db, user, agent_id)
    await db.commit()
    return response


@router.post("/{agent_id}/purge", response_model=AgentPurgeReceiptResponse)
@authz_policy(GOVERNANCE)
async def purge_archived_agent(
    agent_id: uuid.UUID,
    data: AgentPurgeRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> AgentPurgeReceiptResponse:
    receipt = await purge_agent(
        AgentPurgeCommand(
            db=db,
            actor=user,
            agent_id=agent_id,
            confirmation_name=data.confirmation_name,
        )
    )
    response.status_code = 200 if receipt.state == "completed" else 202
    return receipt


@router.post("/{agent_id}/image", response_model=GenerateImageResponse)
@authz_policy(RES("agent", "can_edit", param="agent_id", min_visible="can_run"))
async def generate_agent_image(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
):
    agent = await agent_access_views.authorized_agent(db, authz, context, agent_id, "can_edit")
    _require_standard_profile(agent)
    try:
        image_url = await image_service.generate_agent_image(db, agent)
    except ValueError as e:
        raise ValidationError("IMAGE_GEN_CONFIG", str(e)) from e
    except (httpx.HTTPStatusError, RuntimeError) as e:
        raise ExternalServiceError("IMAGE_GEN_FAILED", str(e)) from e
    return GenerateImageResponse(image_url=image_url)


@router.get("/{agent_id}/image")
@authz_policy(RES("agent", "can_run", param="agent_id", min_visible="can_run"))
async def get_agent_image(
    agent_id: uuid.UUID,
    variant: Literal["original", "preview"] = Query("original"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
):
    agent = await agent_access_views.authorized_agent(
        db, authz, context, agent_id, "can_run", not_found=image_not_found()
    )
    if not agent.image_path:
        target = resolve_agent_image_path(None, agent_id=agent.id)
    else:
        target = resolve_agent_image_path(agent.image_path, agent_id=agent.id)
    if target is None:
        # Agent has no readable image — silent 204 keeps the browser console
        # clean (AgentAvatar falls back to the BotIcon). Do not mutate the DB
        # from this read path: a transient worktree/CWD mismatch must not erase
        # an otherwise valid avatar reference.
        return Response(status_code=204)
    if variant == "preview":
        preview = await get_or_create_image_preview_async(
            target,
            cache_dir=target.parent / ".previews",
            cache_name=f"agent-{agent.id}",
        )
        if preview is not None:
            return FileResponse(
                preview,
                media_type="image/webp",
                headers={"Cache-Control": "public, max-age=31536000, immutable"},
            )
    media_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
    media = media_map.get(target.suffix, "image/png")
    return FileResponse(str(target), media_type=media)


@middleware_router.get("/api/middlewares")
@authz_policy(CATALOG)
async def list_middlewares() -> list[dict[str, Any]]:
    """Return the available middleware catalog.

    deepagents가 자동 추가하는 빌트인 미들웨어는 제외한다.
    """
    return get_middleware_registry(exclude_builtin=True)
