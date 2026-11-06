"""Reading an Agent API run back: ``GET /v1/runs/{id}`` and the run response shape.

A wait call of an inbox deployment can answer ``waiting`` (17.3); the caller reads the
outcome here once the deployment owner decided. The key reads only its owner's runs of
the deployments it may call, with ``read`` or ``invoke`` scope (the scope that started it).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent_api.access import ensure_key_can_access_deployment
from app.agent_api.dependencies import ApiKeyPrincipal, refresh_api_key_principal
from app.approvals.api_runs import pending_for
from app.authz.agent_api_access import require_can_deploy
from app.authz.agent_api_scope import api_run_of_key_owner
from app.exceptions import ForbiddenError, NotFoundError
from app.models.agent_api import AgentApiRun
from app.schemas.agent_api import AgentRunError, AgentRunPendingApproval, AgentRunResponse

_READ_SCOPES = frozenset({"read", "invoke"})


def _not_found() -> NotFoundError:
    return NotFoundError("AGENT_API_RUN_NOT_FOUND", "run not found")


async def run_response(
    db: AsyncSession, run: AgentApiRun, *, agent_id: str, thread_id: str | None
) -> AgentRunResponse:
    pending = await pending_for(db, run.id) if run.status == "waiting" else []
    return AgentRunResponse(
        id=run.public_id,
        thread_id=thread_id,
        agent_id=agent_id,
        status=run.status,
        output=run.output,
        created_at=run.created_at,
        finished_at=run.finished_at,
        pending_approvals=[
            AgentRunPendingApproval(id=item.id, tools=item.tools, expires_at=item.expires_at)
            for item in pending
        ],
        error=(
            AgentRunError(code=run.error_code, message=run.error_message)
            if run.error_code
            else None
        ),
    )


async def get_run_for_public_id(
    db: AsyncSession, *, principal: ApiKeyPrincipal, run_id: str
) -> AgentApiRun:
    principal = await refresh_api_key_principal(db, principal)
    if not _READ_SCOPES & set(principal.key.scopes or []):
        raise ForbiddenError("AGENT_API_SCOPE_REQUIRED", "API key requires 'read' scope")
    run = (
        await db.execute(
            select(AgentApiRun)
            .where(
                AgentApiRun.public_id == run_id,
                api_run_of_key_owner(principal.user_id, principal.context.tenant_id),
            )
            .options(selectinload(AgentApiRun.deployment), selectinload(AgentApiRun.thread))
            .execution_options(populate_existing=True, autoflush=False)
        )
    ).scalar_one_or_none()
    if run is None or run.deployment is None:
        raise _not_found()
    try:
        ensure_key_can_access_deployment(principal, run.deployment)
    except NotFoundError:
        raise _not_found() from None
    await require_can_deploy(
        principal.context, run.deployment.agent_id, not_found=_not_found(), opaque=True
    )
    return run


__all__ = ["get_run_for_public_id", "run_response"]
