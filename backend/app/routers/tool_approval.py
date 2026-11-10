"""Tool approval: the effective table and conversation grants (approval-modes-plan 3.5, Q5).

The settings themselves are part of the agent's runtime policy (saved with the agent). The
effective table calls the same ``tool_modes.decide`` a run uses. Grants belong to the
conversation's owner (ownership gate after CSRF on mutations, 404 for anyone else).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import tool_grants, tool_modes
from app.authz.org_context import OrgContext
from app.authz.policies import PRIVATE, RES, authz_policy
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_db,
    get_org_context,
    owned_conversation,
    verify_csrf,
)
from app.error_codes import agent_not_found
from app.guardrails.policy import resolve_tool_risk
from app.guardrails.tool_risk import DEFAULT_ACTIONS
from app.schemas.tool_approval import (
    ToolApprovalCell,
    ToolApprovalTool,
    ToolApprovalView,
    ToolGrantCreate,
    ToolGrantOut,
)
from app.services import agent_access_views, chat_service
from app.tools.keys import ambiguous_names, resolve

router = APIRouter(tags=["tool-approval"])
CONVERSATION = PRIVATE("conversations", param="conversation_id")
_COMPANY_LAYERS = frozenset({"company_block", "company_always", "company_forced"})


async def _run_agent(db: AsyncSession, context: OrgContext, agent_id: uuid.UUID):  # type: ignore[no-untyped-def]
    agent = await chat_service.get_run_agent_with_tools(db, agent_id, context)
    if agent is None:
        raise agent_not_found()
    return agent


@router.get("/api/agents/{agent_id}/tool-approval", response_model=ToolApprovalView)
@authz_policy(RES("agent", "can_view_config", param="agent_id", min_visible="can_run"))
async def get_tool_approval(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
):
    """The settings, the company cap and every tool × path as a run decides it."""
    await agent_access_views.authorized_agent(db, authz, context, agent_id, "can_view_config")
    agent = await _run_agent(db, context, agent_id)
    company = await resolve_tool_risk(db, context.tenant_id, agent.runtime_profile)
    settings = tool_grants.settings_of(agent)
    legacy = tool_modes.legacy_always_from(agent.middleware_configs)
    tools = await tool_grants.agent_tools(db, agent, context)
    cells = tool_modes.effective(tools, settings=settings, company=company, legacy_always=legacy)
    refs = [ref for ref, _ in tools]
    twice = ambiguous_names(refs)
    shown, unresolved = tool_grants.keyed_settings(settings, refs)
    rows: list[ToolApprovalTool] = []
    for ref, level in tools:
        mine = [cell for cell in cells if cell.tool == ref.key]
        rows.append(
            ToolApprovalTool(
                key=ref.key,
                name=ref.name,
                source=ref.source,
                level=level.value,
                locked=any(cell.layer in _COMPANY_LAYERS for cell in mine),
                grantable=tool_modes.grantable(
                    ref, level, settings=settings, company=company, ambiguous=ref.name in twice
                ),
                cells=[
                    ToolApprovalCell(
                        path=cell.path,
                        kind=cell.kind,
                        layer=cell.layer,
                        reason=cell.reason,
                        delivery=cell.delivery,
                        rule=cell.rule.model_dump(mode="json", exclude_none=True)
                        if cell.rule
                        else None,
                    )
                    for cell in mine
                ],
            )
        )
    return ToolApprovalView(
        settings=shown.model_dump(mode="json", exclude_none=True) if shown else None,
        unresolved=unresolved,
        company_actions=dict(company.actions if company else DEFAULT_ACTIONS),
        company_always_ask=sorted(company.always_ask) if company else [],
        tools=rows,
    )


@router.get(
    "/api/conversations/{conversation_id}/tool-grants",
    response_model=list[ToolGrantOut],
    dependencies=[Depends(owned_conversation)],
)
@authz_policy(CONVERSATION)
async def list_tool_grants(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
):
    return await tool_grants.list_live(db, conversation_id)


@router.post(
    "/api/conversations/{conversation_id}/tool-grants",
    response_model=ToolGrantOut,
    status_code=201,
)
@authz_policy(CONVERSATION)
async def create_tool_grant(
    conversation_id: uuid.UUID,
    body: ToolGrantCreate,
    _csrf: None = Depends(verify_csrf),
    conversation: tool_grants.Conversation = Depends(owned_conversation),
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
):
    """Allow a tool for this conversation (within the company cap, plan Q5)."""
    if conversation.agent_id is None:
        raise agent_not_found()
    agent = await _run_agent(db, context, conversation.agent_id)
    company = await resolve_tool_risk(db, context.tenant_id, agent.runtime_profile)
    row = await tool_grants.grant(
        db, context, conversation, body.tool, agent=agent, company=company
    )
    await db.commit()
    return row


@router.delete("/api/conversations/{conversation_id}/tool-grants/{tool}", status_code=204)
@authz_policy(CONVERSATION)
async def revoke_tool_grant(
    conversation_id: uuid.UUID,
    tool: str,
    _csrf: None = Depends(verify_csrf),
    conversation: tool_grants.Conversation = Depends(owned_conversation),
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
) -> Response:
    ref = None
    if conversation.agent_id is not None:
        agent = await chat_service.get_run_agent_with_tools(db, conversation.agent_id, context)
        if agent is not None:
            tools = await tool_grants.agent_tools(db, agent, context)
            ref = resolve([item for item, _ in tools], tool)
    await tool_grants.revoke(db, context, conversation, tool, ref=ref)
    await db.commit()
    return Response(status_code=204)
