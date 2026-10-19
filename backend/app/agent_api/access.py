"""Fresh private deployment boundaries for request and detached Agent API work."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent_api.dependencies import ApiKeyPrincipal, refresh_api_key_principal
from app.authz.agent_api_access import require_can_deploy
from app.authz.agent_api_scope import private_deployment_predicate
from app.exceptions import NotFoundError
from app.models.agent_api import AgentDeployment


@dataclass(frozen=True, slots=True)
class AuthorizedDeployment:
    principal: ApiKeyPrincipal
    deployment: AgentDeployment


def ensure_key_can_access_deployment(
    principal: ApiKeyPrincipal, deployment: AgentDeployment
) -> None:
    if (deployment.user_id, deployment.org_id, deployment.tenant_id) != (
        principal.user_id,
        principal.context.org_id,
        principal.context.tenant_id,
    ):
        raise NotFoundError("AGENT_DEPLOYMENT_NOT_FOUND", "deployment not found")
    if principal.key.allow_all_deployments:
        return
    allowed = {link.deployment_id for link in principal.key.deployment_links}
    if deployment.id not in allowed:
        raise NotFoundError("AGENT_DEPLOYMENT_NOT_FOUND", "deployment not found")


async def authorize_deployment(
    db: AsyncSession, principal: ApiKeyPrincipal, deployment_id: UUID
) -> AuthorizedDeployment:
    principal = await refresh_api_key_principal(db, principal)
    deployment = await db.scalar(
        select(AgentDeployment)
        .where(
            AgentDeployment.id == deployment_id,
            AgentDeployment.status == "active",
            private_deployment_predicate(principal.context),
        )
        .options(selectinload(AgentDeployment.agent))
        .execution_options(populate_existing=True, autoflush=False)
    )
    if deployment is None:
        raise NotFoundError("AGENT_DEPLOYMENT_NOT_FOUND", "deployment not found")
    ensure_key_can_access_deployment(principal, deployment)
    await require_can_deploy(
        principal.context,
        deployment.agent_id,
        not_found=NotFoundError("AGENT_DEPLOYMENT_NOT_FOUND", "deployment not found"),
        opaque=True,
    )
    return AuthorizedDeployment(principal=principal, deployment=deployment)
