"""R10 F1 blueprint pre-effect witness."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.marketplace.schemas import CreateAgentFromBlueprintIn
from app.models.agent_blueprint import AgentBlueprint
from app.models.model import Model
from app.models.organization import OrganizationMember
from app.models.tool import Tool
from app.services import agent_blueprint_service

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


async def test_blueprint_denies_revoked_before_dependency_flush(
    db: AsyncSession, private_scopes: PrivateScopes
) -> None:
    own = private_scopes.first
    model = Model(provider="openai", model_name="fixture", display_name="Fixture")
    blueprint = AgentBlueprint(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="Scoped blueprint",
        spec_hash="a" * 64,
        spec={
            "schema_version": 1,
            "resource": "agent_blueprint",
            "agent": {"name": "Scoped blueprint", "system_prompt": "Fixture"},
            "capabilities": {
                "tools": [
                    {
                        "name": "F1 revoked dependency",
                        "definition_key": "http_request",
                        "parameters": {"url": "https://example.invalid"},
                    }
                ]
            },
            "setup": {"required_credentials": []},
        },
    )
    db.add_all([model, blueprint])
    await db.commit()
    await db.execute(
        update(OrganizationMember)
        .where(OrganizationMember.org_id == own.org_id, OrganizationMember.user_id == own.user_id)
        .values(status="suspended")
    )
    await db.commit()
    before = await db.scalar(select(func.count()).select_from(Tool))
    denied = False
    try:
        await agent_blueprint_service.create_agent_from_blueprint(
            db,
            blueprint_id=blueprint.id,
            context=own,
            body=CreateAgentFromBlueprintIn(model_id=model.id),
        )
    except AppError:
        denied = True
    staged = await db.scalar(select(func.count()).select_from(Tool))
    # Service contract is before any effect: a caller can still commit its session.
    await db.commit()
    persisted = await db.scalar(select(func.count()).select_from(Tool))
    assert denied and staged == before and persisted == before
