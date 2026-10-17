"""Agent readiness check routes (plan 12.3; G-01).

* ``GET /api/agents/{agent_id}/readiness?source=CHAT``: ``RES(agent, can_run)`` — the run
  loader's 404 (hidden) / 403 ``permission_required`` (visible only); never cached.
* ``POST /api/agents/readiness-summary``: ``CANDIDATES(agent, can_run)`` — at most 50 ids;
  agents the caller may not run are left out (cached the same way); 60 s cache per caller
  and agent; ``SUMMARY_RATE`` per caller (slowapi limiter). A read, but
  POST with a body, so it keeps the CSRF guard like every other POST.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.policies import CANDIDATES, RES, authz_policy
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.dependencies import CurrentUser, get_current_user, get_db, get_org_context, verify_csrf
from app.rate_limit import limiter
from app.schemas.readiness import (
    AgentReadinessResponse,
    ReadinessSummary,
    ReadinessSummaryRequest,
)
from app.services import readiness_service

router = APIRouter(prefix="/api/agents", tags=["agents"])
SUMMARY_RATE = "60/minute"  # dashboard refreshes, per caller (each call checks <= 50 agents)


def caller_rate_key(request: Request) -> str:
    actor: object = getattr(request.state, "current_user", None)
    if isinstance(actor, CurrentUser):
        return f"readiness-summary:{actor.id}"
    return "readiness-summary:unauthenticated"


@router.post(
    "/readiness-summary",
    response_model=dict[uuid.UUID, ReadinessSummary],
)
@authz_policy(CANDIDATES("agent", "can_run"))
@limiter.limit(SUMMARY_RATE, key_func=caller_rate_key)
async def readiness_summary(
    request: Request,
    data: ReadinessSummaryRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
):
    return await readiness_service.summaries(db, authz, context, data.agent_ids)


@router.get(
    "/{agent_id}/readiness",
    response_model=AgentReadinessResponse,
    response_model_exclude_none=True,
)
@authz_policy(RES("agent", "can_run", param="agent_id", min_visible="can_run"))
async def get_agent_readiness(
    agent_id: uuid.UUID,
    source: Literal["CHAT"] = Query("CHAT"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
):
    readiness = await readiness_service.assess_agent(db, authz, context, agent_id)
    return readiness_service.present(readiness)
