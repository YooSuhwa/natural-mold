"""E-03 catalog fanout fixtures: platform catalog rows, a model-faithful ``can_use`` probe."""

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.fake import FakeTupleStore
from app.models.mcp_server import McpServer
from app.models.model import Model
from app.models.organization import OrganizationMember
from app.models.tool import Tool
from tests.conftest import make_user


@pytest.fixture(autouse=True)
async def physical_model_scope(db: AsyncSession) -> AsyncGenerator[None, None]:
    """m81 owns ``models.tenant_id`` physically; the ORM mapping is B-08."""
    await db.execute(text("ALTER TABLE models ADD COLUMN tenant_id UUID"))
    await db.commit()
    try:
        yield
    finally:
        await db.rollback()
        await db.execute(text("ALTER TABLE models DROP COLUMN tenant_id"))
        await db.commit()


@dataclass(frozen=True, slots=True)
class Catalog:
    tool: UUID
    server: UUID
    model: UUID
    hidden_model: UUID

    def objects(self) -> dict[str, str]:
        return {
            "tool": f"tool:{self.tool}",
            "mcp_server": f"mcp_server:{self.server}",
            "model": f"llm_model:{self.model}",
        }


async def seed_catalog(db: AsyncSession) -> Catalog:
    """Platform rows; system MCP servers keep the seeding operator as ``user_id``."""
    operator = await make_user(db, is_super_user=True)
    tool = Tool(name="Web", definition_key="builtin:current_datetime", is_system=True)
    server = McpServer(
        user_id=operator.id, is_system=True, name="sys", transport="stdio", command="x"
    )
    model = Model(provider="openai", model_name="visible", display_name="Visible")
    hidden = Model(provider="openai", model_name="hidden", display_name="Hidden", is_visible=False)
    db.add_all([tool, server, model, hidden])
    await db.commit()
    return Catalog(tool.id, server.id, model.id, hidden.id)


async def active_members(db: AsyncSession, org_id: UUID) -> list[UUID]:
    found = await db.scalars(
        select(OrganizationMember.user_id).where(
            OrganizationMember.org_id == org_id, OrganizationMember.status == "active"
        )
    )
    return list(found)


def can_use(store: FakeTupleStore, org_id: UUID, obj: str) -> bool:
    """``can_use`` of a member of ``org_id`` on a platform row, as model.fga evaluates it.

    Platform rows carry no org/owner tuples, so ``consumer`` only comes from
    ``organization:O#member`` and ``in_boundary`` only from ``allowed_org`` (``llm_model``
    has no boundary: ``can_use = consumer``).
    """
    keys = {(t.user, t.relation, t.object) for t in store.stored()}
    consumer = (f"organization:{org_id}#member", "consumer", obj) in keys
    bounded = obj.startswith("llm_model:") or (f"organization:{org_id}", "allowed_org", obj) in keys
    return consumer and bounded
