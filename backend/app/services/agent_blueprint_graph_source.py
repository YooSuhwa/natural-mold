"""Persisted Blueprint projections bypass stale identity-map decision fields."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.scoping import private_scoped
from app.error_codes import marketplace_item_not_found
from app.models.agent_blueprint import AgentBlueprint
from app.services.agent_blueprint_graph_types import JsonObject


@dataclass(frozen=True, slots=True)
class BlueprintSource:
    row: AgentBlueprint
    spec: JsonObject
    name: str
    description: str | None
    credential_bindings: dict[str, str] | None
    created_agent_count: int


async def read_blueprint(
    db: AsyncSession,
    blueprint_id: UUID,
    context: OrgContext,
) -> BlueprintSource:
    result = (
        await db.execute(
            private_scoped(
                select(
                    AgentBlueprint,
                    AgentBlueprint.spec,
                    AgentBlueprint.name,
                    AgentBlueprint.description,
                    AgentBlueprint.credential_bindings,
                    AgentBlueprint.created_agent_count,
                    AgentBlueprint.install_status,
                ).where(AgentBlueprint.id == blueprint_id),
                AgentBlueprint,
                context,
            )
        )
    ).one_or_none()
    if result is None:
        raise marketplace_item_not_found()
    row, spec, name, description, bindings, count, status = result
    if status == "uninstalled":
        raise marketplace_item_not_found()
    return BlueprintSource(
        row, TypeAdapter(JsonObject).validate_python(spec or {}), name, description, bindings, count
    )
