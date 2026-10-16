"""Agent usage route decision (plan 9.6, D-07): ``agent#can_view_config``.

The legacy route answers every caller (it has no session dependency at all), so the
legacy verdict is ``ALLOW``; closing that gap in legacy mode belongs to D-10. Legacy
returns before reading the session (zero added queries). Outside legacy the session and
organization are resolved here, not through ``Depends``, so they are read only then;
``shadow`` answers like legacy and samples when an organization resolves; only an
enforced ``agent`` type turns a missing session or organization into its error and a
denial into the 8.4 response.
"""

from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_access import agent_object
from app.authz.principal import Principal
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.authz.service_types import Verdict
from app.dependencies import get_current_user_optional, get_db, get_org_context
from app.error_codes import agent_not_found
from app.exceptions import AppError
from app.models.agent import Agent


async def agent_usage_gate(
    agent_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    authz: AuthzService = Depends(get_authz),
) -> None:
    if authz.mode == "legacy":
        return
    enforced = authz.enforces("agent")
    user = await get_current_user_optional(request, db)
    if user is None:
        if enforced:
            raise AppError(code="not_authenticated", message="인증이 필요합니다", status=401)
        return
    try:
        ctx = await get_org_context(request, user, db)
    except (AppError, HTTPException):
        if enforced:
            raise
        return
    org_id = await db.scalar(
        select(Agent.org_id).where(
            Agent.id == agent_id, Agent.org_id == ctx.org_id, Agent.tenant_id == ctx.tenant_id
        )
    )
    await authz.require(
        Principal.for_user(ctx),
        "can_view_config",
        agent_object(agent_id),
        min_visible="can_run",
        resource_org_id=org_id,
        legacy=Verdict.ALLOW,
        not_found=agent_not_found(),
    )
