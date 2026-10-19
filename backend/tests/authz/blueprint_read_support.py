"""Typed constructors for isolated Blueprint read regressions."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.models.agent_blueprint import AgentBlueprint
from app.models.tool import Tool


def blueprint_root(context: OrgContext, name: str, updated: datetime) -> AgentBlueprint:
    return AgentBlueprint(
        id=uuid4(),
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        name=name,
        description="Persisted description",
        icon_id="bot",
        tags=["fixture"],
        categories=["read"],
        spec_hash="b" * 64,
        spec={"agent": {"name": name}},
        created_at=updated,
        updated_at=updated,
    )


def pending_tool(db: AsyncSession, context: OrgContext) -> Tool:
    tool = Tool(
        id=uuid4(),
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        name="Caller-staged writer",
        definition_key="http_request",
        parameters={},
    )
    db.add(tool)
    return tool
