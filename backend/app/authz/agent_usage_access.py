"""Agent usage route decision (plan 9.6, D-07/D-10): ``agent#can_view_config``.

D-10 closed the legacy gap: the route used to answer every caller without a session.
It now requires an authenticated user and an organization (``get_org_context``: 401 /
the existing context errors), and the legacy verdict is the owner-scoped agent row
(foreign or missing → ``AGENT_NOT_FOUND``). ``shadow`` answers that and samples; an
enforced ``agent`` type lets OpenFGA decide (grantees with ``can_view_config`` pass,
runners get 403 ``permission_required``, OpenFGA down is 503).
"""

from uuid import UUID

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_access import require_agent
from app.authz.org_context import OrgContext
from app.authz.runtime import get_authz
from app.authz.scoping import private_scope_predicate
from app.authz.service import AuthzService
from app.dependencies import get_db, get_org_context
from app.error_codes import agent_not_found
from app.models.agent import Agent


async def agent_usage_gate(
    agent_id: UUID,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
) -> None:
    legacy = await db.scalar(
        select(Agent)
        .where(Agent.id == agent_id, private_scope_predicate(Agent, context))
        .execution_options(autoflush=False)
    )
    await require_agent(
        db,
        authz,
        context,
        agent_id,
        "can_view_config",
        legacy=legacy,
        not_found=agent_not_found(),
    )
