"""Current private Blueprint presentation without flushing caller-staged writes."""

from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal
from uuid import UUID

from pydantic import JsonValue, TypeAdapter
from sqlalchemy import JSON, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.catalog_scope import installation_child_scope_predicate
from app.authz.org_context import OrgContext
from app.authz.private_actor import require_private_actor
from app.authz.scoping import private_scope_predicate
from app.models.agent_blueprint import AgentBlueprint
from app.models.marketplace import MarketplaceInstallation


@dataclass(frozen=True, slots=True)
class BlueprintPresentation:
    """Detached persisted fields; no identity-map refresh can consume staged edits."""

    id: UUID
    name: str
    description: str | None
    icon_id: str | None
    tags: tuple[str, ...] | None
    categories: tuple[str, ...] | None
    spec: dict[str, JsonValue] | None
    spec_hash: str
    source_marketplace_item_id: UUID | None
    source_marketplace_version_id: UUID | None
    installation_id: UUID | None
    install_status: Literal["active", "needs_setup", "disabled", "uninstalled"]
    is_dirty: bool
    created_agent_count: int
    created_at: datetime
    updated_at: datetime


_PRESENTATION: Final = TypeAdapter(BlueprintPresentation)


async def read_blueprint_presentations(
    db: AsyncSession, context: OrgContext, blueprint_id: UUID | None
) -> tuple[BlueprintPresentation, ...]:
    """Decide actor and root/installation scope before any caller SQL effects.

    A None target selects the list, omitting publisher spec at the SQL boundary.
    Detail keeps the historical one-row limit; both paths use the same joins.
    """
    with db.no_autoflush:
        await require_private_actor(db, context)
        blueprint = AgentBlueprint
        installation = MarketplaceInstallation
        spec = blueprint.spec if blueprint_id is not None else literal(None, type_=JSON)
        query = (
            select(
                blueprint.id,
                blueprint.name,
                blueprint.description,
                blueprint.icon_id,
                blueprint.tags,
                blueprint.categories,
                spec.label("spec"),
                blueprint.spec_hash,
                blueprint.source_marketplace_item_id,
                blueprint.source_marketplace_version_id,
                installation.id.label("installation_id"),
                func.coalesce(installation.install_status, blueprint.install_status).label(
                    "install_status"
                ),
                or_(blueprint.is_dirty, func.coalesce(installation.is_dirty, False)).label(
                    "is_dirty"
                ),
                blueprint.created_agent_count,
                blueprint.created_at,
                blueprint.updated_at,
            )
            .outerjoin(
                installation,
                (installation.installed_agent_blueprint_id == blueprint.id)
                & (installation.install_status != "uninstalled")
                & private_scope_predicate(installation, context)
                & installation_child_scope_predicate(context),
            )
            .where(
                private_scope_predicate(blueprint, context),
                blueprint.install_status != "uninstalled",
            )
            .execution_options(autoflush=False)
        )
        if blueprint_id is not None:
            query = query.where(blueprint.id == blueprint_id).limit(1)
        else:
            query = query.order_by(blueprint.updated_at.desc())
        rows = (await db.execute(query)).mappings().all()
        return tuple(_PRESENTATION.validate_python(dict(row)) for row in rows)
