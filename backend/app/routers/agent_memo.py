"""Shared operating memo of an agent and its proposals (plan 9.7 table, 11.8; F-09).

Reading the memo and proposing to it need ``agent#can_run``; changing it (text or the
per-agent on/off switch), listing and deciding proposals need ``agent#can_edit``.
Proposals need the memo switched on (409 ``AGENT_MEMO_DISABLED``) and at most 20
pending per user (409 ``AGENT_MEMO_PROPOSAL_LIMIT``). Legacy mode: the owner only.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.memory_agent_access import memo_agent
from app.authz.org_context import OrgContext
from app.authz.policies import RES, authz_policy
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.dependencies import CurrentUser, get_current_user, get_db, get_org_context, verify_csrf
from app.error_codes import agent_not_found, memory_proposal_not_found
from app.schemas.agent_memo import (
    AgentMemoOut,
    AgentMemoProposalCreate,
    AgentMemoProposalDecision,
    AgentMemoProposalOut,
    AgentMemoUpdate,
)
from app.services import agent_memo_service, memory_service

router = APIRouter(tags=["memory"])
_BASE = "/api/agents/{agent_id}/memo"
_RUN = RES("agent", "can_run", param="agent_id", min_visible="can_run")
_EDIT = RES("agent", "can_edit", param="agent_id", min_visible="can_run")


@router.get(_BASE, response_model=AgentMemoOut)
@authz_policy(_RUN)
async def get_agent_memo(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
) -> AgentMemoOut:
    agent = await memo_agent(db, authz, context, agent_id, "can_run")
    enabled = await agent_memo_service.memo_enabled(db, agent.id)
    return AgentMemoOut(
        agent_id=agent.id, content=agent_memo_service.read_memo(agent.id), enabled=enabled
    )


@router.put(_BASE, response_model=AgentMemoOut)
@authz_policy(_EDIT)
async def update_agent_memo(
    agent_id: uuid.UUID,
    payload: AgentMemoUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> AgentMemoOut:
    agent = await memo_agent(db, authz, context, agent_id, "can_edit")
    content, enabled = await agent_memo_service.update_memo(
        db, actor=context, agent=agent, content=payload.content, enabled=payload.enabled
    )
    return AgentMemoOut(agent_id=agent_id, content=content, enabled=enabled)


@router.get(f"{_BASE}/proposals", response_model=list[AgentMemoProposalOut])
@authz_policy(_EDIT)
async def list_agent_memo_proposals(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
) -> list[AgentMemoProposalOut]:
    agent = await memo_agent(db, authz, context, agent_id, "can_edit")
    rows = await agent_memo_service.list_memo_proposals(db, agent)
    return [AgentMemoProposalOut.model_validate(row) for row in rows]


@router.post(f"{_BASE}/proposals", response_model=AgentMemoProposalOut, status_code=201)
@authz_policy(_RUN)
async def create_agent_memo_proposal(
    agent_id: uuid.UUID,
    payload: AgentMemoProposalCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> AgentMemoProposalOut:
    agent = await memo_agent(db, authz, context, agent_id, "can_run")
    proposal = await memory_service.create_agent_memo_proposal(
        db,
        context=context,
        agent_id=agent.id,
        content=payload.content,
        reason=payload.reason,
        conversation_id=payload.conversation_id,
    )
    if proposal is None:  # e.g. an archived agent: no new memory references it
        raise agent_not_found()
    return AgentMemoProposalOut.model_validate(proposal)


@router.post(f"{_BASE}/proposals/{{proposal_id}}/decision", response_model=AgentMemoProposalOut)
@authz_policy(_EDIT)
async def decide_agent_memo_proposal(
    agent_id: uuid.UUID,
    proposal_id: uuid.UUID,
    payload: AgentMemoProposalDecision,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> AgentMemoProposalOut:
    agent = await memo_agent(db, authz, context, agent_id, "can_edit")
    decided = await agent_memo_service.decide_memo_proposal(
        db,
        actor=context,
        agent=agent,
        proposal_id=proposal_id,
        approve=payload.decision == "approve",
        content=payload.content,
    )
    if decided is None:
        raise memory_proposal_not_found()
    return AgentMemoProposalOut.model_validate(decided)
