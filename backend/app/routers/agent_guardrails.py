"""An agent's guardrail choice (guardrail-minimum-plan.md §3.4): ``agent#can_edit``."""

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.memory_agent_access import agent_edit_context
from app.authz.org_context import OrgContext
from app.authz.policies import RES, authz_policy
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.dependencies import CurrentUser, get_current_user, get_db, get_org_context, verify_csrf
from app.schemas.guardrails import AgentGuardrailOut, AgentGuardrailUpdate
from app.services import guardrails_admin

router = APIRouter(tags=["agents"])


@router.get("/api/agents/{agent_id}/guardrails", response_model=AgentGuardrailOut)
@authz_policy(RES("agent", "can_edit", param="agent_id", min_visible="can_run"))
async def get_agent_guardrails(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
) -> AgentGuardrailOut:
    scope = await agent_edit_context(db, authz, context, agent_id)
    return await guardrails_admin.binding_view(db, agent_id, scope)


@router.put("/api/agents/{agent_id}/guardrails", response_model=AgentGuardrailOut)
@authz_policy(RES("agent", "can_edit", param="agent_id", min_visible="can_run"))
async def put_agent_guardrails(
    agent_id: uuid.UUID,
    payload: AgentGuardrailUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> AgentGuardrailOut:
    scope = await agent_edit_context(db, authz, context, agent_id)
    return await guardrails_admin.update_binding(
        db, agent_id, scope, payload, actor=user, request=request
    )
