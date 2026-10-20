"""Private organization candidates and explicit platform catalog rows for management."""

from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.authz.org_context import OrgContext
from app.authz.private_actor import (
    PrivateResourceActor,
    require_private_actor,
    resolve_private_actor,
)
from app.authz.scoping import private_scope_predicate, resource_candidates
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.agent_blueprint import AgentBlueprint
from app.models.marketplace import MarketplaceInstallation, MarketplaceItem, MarketplaceItemACL
from app.models.mcp_server import McpServer
from app.models.skill import Skill


async def load_catalog_management_candidate(
    db: AsyncSession, item_id: UUID, context: OrgContext
) -> MarketplaceItem | None:
    """Candidate scope only; callers must still enforce catalog management authority."""
    await resolve_private_actor(
        db, PrivateResourceActor(context.user_id, context.org_id, context.tenant_id)
    )
    # ACL rows are eager-loaded: ``can_manage_item`` reads them for non-owners, and a lazy
    # load under the async session raised MissingGreenlet (500) instead of 403 (D-05).
    return await db.scalar(
        resource_candidates(
            select(MarketplaceItem)
            .where(MarketplaceItem.id == item_id)
            .options(selectinload(MarketplaceItem.acl_entries)),
            MarketplaceItem,
            context,
        ).execution_options(populate_existing=True)
    )


def catalog_view_candidate_predicate(context: OrgContext) -> ColumnElement[bool]:
    """Scope private ownership/ACL candidates while retaining public/platform catalog views."""
    acl = (
        select(MarketplaceItemACL.item_id)
        .where(
            MarketplaceItemACL.item_id == MarketplaceItem.id,
            MarketplaceItemACL.user_id == context.user_id,
        )
        .exists()
    )

    return or_(
        private_scope_predicate(MarketplaceItem, context),
        and_(
            MarketplaceItem.is_system.is_(True),
            MarketplaceItem.org_id.is_(None),
            MarketplaceItem.tenant_id.is_(None),
        ),
        and_(
            MarketplaceItem.status == "published",
            MarketplaceItem.visibility.in_(("public", "unlisted")),
            # Public reach ends at the company boundary, as enforced mode decides it
            # (personal-tenant signups put strangers in separate companies).
            or_(
                MarketplaceItem.tenant_id == context.tenant_id,
                MarketplaceItem.tenant_id.is_(None),
            ),
            # E-09 (10.2): a pending or rejected company publication stays inside the
            # publishing organization; untouched (``none``) rows keep the legacy reach.
            or_(
                MarketplaceItem.tenant_publication.in_(("none", "approved")),
                and_(
                    MarketplaceItem.org_id == context.org_id,
                    MarketplaceItem.tenant_id == context.tenant_id,
                ),
            ),
        ),
        and_(
            MarketplaceItem.org_id == context.org_id,
            MarketplaceItem.tenant_id == context.tenant_id,
            MarketplaceItem.visibility == "restricted",
            acl,
        ),
    )


def installation_child_scope_predicate(context: OrgContext) -> ColumnElement[bool]:
    """Every referenced private root must remain in the installer's exact context."""
    return and_(
        or_(
            MarketplaceInstallation.installed_skill_id.is_(None),
            select(Skill.id)
            .where(
                Skill.id == MarketplaceInstallation.installed_skill_id,
                private_scope_predicate(Skill, context),
            )
            .correlate(MarketplaceInstallation)
            .exists(),
        ),
        or_(
            MarketplaceInstallation.installed_mcp_server_id.is_(None),
            select(McpServer.id)
            .where(
                McpServer.id == MarketplaceInstallation.installed_mcp_server_id,
                private_scope_predicate(McpServer, context),
            )
            .correlate(MarketplaceInstallation)
            .exists(),
        ),
        or_(
            MarketplaceInstallation.installed_agent_blueprint_id.is_(None),
            select(AgentBlueprint.id)
            .where(
                AgentBlueprint.id == MarketplaceInstallation.installed_agent_blueprint_id,
                private_scope_predicate(AgentBlueprint, context),
            )
            .correlate(MarketplaceInstallation)
            .exists(),
        ),
        or_(
            MarketplaceInstallation.installed_agent_id.is_(None),
            select(Agent.id)
            .where(
                Agent.id == MarketplaceInstallation.installed_agent_id,
                private_scope_predicate(Agent, context),
            )
            .correlate(MarketplaceInstallation)
            .exists(),
        ),
    )


async def load_private_installation(
    db: AsyncSession, installation_id: UUID, context: OrgContext
) -> MarketplaceInstallation | None:
    try:
        await require_private_actor(db, context)
    except AppError:
        return None
    return await db.scalar(
        select(MarketplaceInstallation)
        .where(
            MarketplaceInstallation.id == installation_id,
            private_scope_predicate(MarketplaceInstallation, context),
            installation_child_scope_predicate(context),
        )
        .with_for_update()
        .execution_options(populate_existing=True, autoflush=False)
    )
