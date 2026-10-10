"""Catalog inventory selects only global system resources and this company's models."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import Uuid, column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mcp_server import McpServer
from app.models.model import Model
from app.models.organization import Tenant
from app.models.skill import Skill
from app.models.tool import Tool
from app.schemas.org_catalog import CatalogKind


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    kind: CatalogKind
    id: UUID
    name: str
    source: Literal["platform", "company"]
    available: bool = True


async def company_catalog_settings(db: AsyncSession, tenant_id: UUID) -> Tenant:
    row = await db.scalar(
        select(Tenant)
        .where(Tenant.id == tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        from app.authz.error_codes import authorization_error

        raise authorization_error("not_found")
    return row


async def catalog_entries(db: AsyncSession, tenant_id: UUID) -> tuple[CatalogEntry, ...]:
    # m81 already owns this physical column; B-08 will promote its ORM mapping.
    company_column = column("tenant_id", Uuid())
    models = await db.execute(
        select(Model.id, Model.display_name, Model.is_visible, company_column).where(
            or_(company_column.is_(None), company_column == tenant_id)
        )
    )
    entries = [
        CatalogEntry("model", key, name, "company" if company else "platform", visible)
        for key, name, visible, company in models
    ]
    tools = await db.execute(
        select(Tool.id, Tool.name).where(Tool.is_system.is_(True), Tool.user_id.is_(None))
    )
    entries.extend(CatalogEntry("tool", key, name, "platform") for key, name in tools)
    skills = await db.execute(
        select(Skill.id, Skill.name).where(Skill.is_system.is_(True), Skill.user_id.is_(None))
    )
    entries.extend(CatalogEntry("skill", key, name, "platform") for key, name in skills)
    servers = await db.execute(
        select(McpServer.id, McpServer.name).where(
            McpServer.is_system.is_(True), McpServer.user_id.is_(None)
        )
    )
    entries.extend(CatalogEntry("mcp_server", key, name, "platform") for key, name in servers)
    return tuple(sorted(entries, key=lambda item: (item.kind, item.name.casefold(), item.id)))
