"""Installed Agent Blueprint API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_delegations import commit_and_apply, sync_agent_delegations
from app.authz.blueprint_read_scope import read_blueprint_presentations
from app.authz.org_context import OrgContext
from app.authz.organization_execution_prefix import mark_org_writer
from app.authz.policies import PRIVATE, authz_policy
from app.authz.resource_lifecycle import on_resource_created
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.dependencies import CurrentUser, get_current_user, get_db, get_org_context, verify_csrf
from app.error_codes import marketplace_item_not_found
from app.marketplace.schemas import AgentBlueprintOut, CreateAgentFromBlueprintIn
from app.routers.agents import _scoped_agent_response
from app.schemas.agent import AgentResponse
from app.services import agent_blueprint_service, audit_service

router = APIRouter(prefix="/api/agent-blueprints", tags=["agent-blueprints"])


@router.get("", response_model=list[AgentBlueprintOut])
@authz_policy(PRIVATE("agent_blueprints"))
async def list_agent_blueprints(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
) -> list[AgentBlueprintOut]:
    rows = await read_blueprint_presentations(db, context, None)
    return [AgentBlueprintOut.model_validate(row, from_attributes=True) for row in rows]


@router.get("/{blueprint_id}", response_model=AgentBlueprintOut)
@authz_policy(PRIVATE("agent_blueprints", param="blueprint_id"))
async def get_agent_blueprint(
    blueprint_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
) -> AgentBlueprintOut:
    rows = await read_blueprint_presentations(db, context, blueprint_id)
    if not rows:
        raise marketplace_item_not_found()
    return AgentBlueprintOut.model_validate(rows[0], from_attributes=True)


@router.post("/{blueprint_id}/create-agent", response_model=AgentResponse, status_code=201)
# PRIVATE blueprint + CAP(cap_create_agent) inside (plan 9.1).
@authz_policy(PRIVATE("agent_blueprints", param="blueprint_id"))
async def create_agent_from_blueprint(
    blueprint_id: uuid.UUID,
    body: CreateAgentFromBlueprintIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> AgentResponse:
    await authz.require_cap(context, "cap_create_agent", legacy=True)
    mark_org_writer(db)  # F-02: prefix takes the org row FOR UPDATE (delegation writes)
    agent = await agent_blueprint_service.create_agent_from_blueprint(
        db,
        blueprint_id=blueprint_id,
        context=context,
        body=body,
    )
    await audit_service.record_self_event(
        db,
        user,
        action="agent_blueprint.create_agent",
        target_type="agent",
        target_id=agent.id,
        target_name=agent.name,
        request=request,
        metadata={"blueprint_id": str(blueprint_id)},
    )
    if agent.llm_credential_id is not None:  # F-02: the blueprint's bound model credential
        agent.llm_credential_delegated_by = context.user_id
        added = {("credential", agent.llm_credential_id)}
        await sync_agent_delegations(db, context, agent.id, added=added)
    created = await on_resource_created(db, agent, mode=authz.mode)
    await commit_and_apply(db, authz, created)  # read-your-writes before responding (8.6)
    return await _scoped_agent_response(db, agent.id, context)
