"""Resolve an MCP App's current credential grant without accessing secret values."""

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.private_actor import require_private_actor
from app.authz.run_decisions import fresh_run_decision_scope
from app.authz.scoping import private_scope_predicate
from app.credentials.company_use import company_credential_usable
from app.credentials.run_resolution import (
    CREDENTIAL_POLICY_COLUMNS,
    USABLE_STATUSES,
    RunCredentialResolver,
)
from app.credentials.run_subject import subject_for_caller
from app.exceptions import AppError
from app.mcp.apps_binding_store import AuthorizedBinding, McpAppsError
from app.models.credential import Credential


async def credential_for_context(db: AsyncSession, context: AuthorizedBinding) -> Credential | None:
    """Select only the credential identity and policy metadata used by this invocation."""
    server, subject = context.server, context.binding.credential_subject_user_id
    org = context.org_context
    if subject != org.user_id:
        raise McpAppsError("mcp_app_not_found", "MCP App context was not found", 404)
    if server.credential_mode == "per_user":
        resolver = RunCredentialResolver(db, subject_for_caller(org, subject))
        bound = await resolver.bound(server, "mcp_server", metadata_only=True)
        if bound is None:
            raise McpAppsError(
                "credential_binding_required", "connect your own account to this server", 409
            )
        return bound
    if server.credential_id is None:
        return None

    await require_private_actor(db, org)
    credential = await db.scalar(
        select(Credential)
        .options(CREDENTIAL_POLICY_COLUMNS)
        .where(
            Credential.id == server.credential_id,
            or_(
                and_(private_scope_predicate(Credential, org), Credential.is_system.is_(False)),
                and_(
                    Credential.scope == "tenant",
                    Credential.tenant_id == org.tenant_id,
                    Credential.user_id.is_(None),
                    Credential.org_id.is_(None),
                ),
            ),
        )
        .execution_options(autoflush=False, populate_existing=True)
    )
    if credential is not None and (
        credential.user_id == subject
        or await company_credential_usable(
            db, credential, user_id=subject, org_id=org.org_id, tenant_id=org.tenant_id
        )
    ):
        return credential
    raise McpAppsError("mcp_app_auth_unavailable", "MCP authentication is unavailable", 404)


async def binding_connection_visible(db: AsyncSession, binding: AuthorizedBinding) -> bool:
    """Prove current connection eligibility without opening or refreshing credentials."""
    with db.no_autoflush, fresh_run_decision_scope(consistency="HIGHER_CONSISTENCY") as decisions:
        try:
            credential = await credential_for_context(db, binding)
            if credential is None:
                return binding.server.credential_mode == "none"
            if credential.status not in USABLE_STATUSES:
                return False
            if decisions.answers:
                # The user's connection can change while the remote grant is checked.
                proved = (credential.id, credential.user_id)
                decisions.seal()
                current = await credential_for_context(db, binding)
                return (
                    current is not None
                    and current.status in USABLE_STATUSES
                    and (current.id, current.user_id) == proved
                )
            return True
        except (AppError, McpAppsError):
            return False
