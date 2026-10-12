"""Complete dependency read-plan followed by a single explicit apply boundary."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_execution_scope import AgentExecutionTarget, load_agent_tree_for_execution
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.error_codes import marketplace_invalid_package
from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.skill import Skill
from app.models.tool import Tool
from app.services.agent_blueprint_graph_tools import plan_tools
from app.services.agent_blueprint_graph_types import (
    GraphPlan,
    GraphRequest,
    JsonObject,
    dependency_mapping,
    mapping,
    position,
    sequence,
)
from app.services.agent_blueprint_mcp_plan import plan_mcp


async def resolve_skills(db: AsyncSession, request: GraphRequest) -> tuple[UUID, ...]:
    rows = sequence(request.capabilities.get("skills"))
    parsed = []
    for raw in rows:
        row = dependency_mapping(raw, "skill")
        parsed.append(row)
    slugs = {str(r["slug"]) for r in parsed if r.get("slug")}
    names = {str(r["name"]) for r in parsed if r.get("name")}
    candidates = (
        (
            await db.execute(
                select(Skill.id, Skill.slug, Skill.name, Skill.kind).where(
                    private_scope_predicate(Skill, request.context),
                    or_(Skill.slug.in_(slugs), Skill.name.in_(names)),
                )
            )
        ).all()
        if slugs or names
        else []
    )
    ids = []
    for row in parsed:
        slug, name, kind = row.get("slug"), row.get("name"), row.get("kind")
        selected = next(
            (
                sid
                for sid, candidate_slug, _, _ in candidates
                if slug and candidate_slug == str(slug)
            ),
            None,
        )
        if selected is None and name:
            selected = next(
                (
                    sid
                    for sid, _, candidate_name, candidate_kind in candidates
                    if candidate_name == str(name) and (not kind or candidate_kind == str(kind))
                ),
                None,
            )
        if selected is None:
            raise marketplace_invalid_package(
                f"missing skill dependency: {name or slug or 'unnamed skill'}"
            )
        ids.append(selected)
    return tuple(ids)


async def resolve_subagents(
    db: AsyncSession,
    capabilities: JsonObject,
    context: OrgContext,
) -> list[UUID]:
    rows = sorted(
        sequence(capabilities.get("subagents")),
        key=lambda raw: position(mapping(raw).get("position")),
    )
    names = []
    for raw in rows:
        row = dependency_mapping(raw, "subagent")
        name = str(row.get("name") or "").strip()
        if not name:
            raise marketplace_invalid_package("subagent dependency is missing a name")
        names.append(name)
    agents = (
        (
            await db.execute(
                select(Agent.id, Agent.name)
                .where(
                    private_scope_predicate(Agent, context),
                    Agent.name.in_(set(names)),
                    Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD,
                )
                .order_by(Agent.updated_at.desc())
            )
        ).all()
        if names
        else []
    )
    by_name: dict[str, UUID] = {}
    for agent_id, agent_name in agents:
        by_name.setdefault(agent_name, agent_id)
    ids = []
    for name in names:
        selected = by_name.get(name)
        if selected is None:
            raise marketplace_invalid_package(f"missing subagent dependency: {name}")
        ids.append(selected)
    for agent_id in sorted(set(ids), key=lambda item: item.bytes):
        await load_agent_tree_for_execution(db, AgentExecutionTarget(agent_id, context))
    return ids


async def plan_graph(db: AsyncSession, request: GraphRequest) -> GraphPlan:
    subagents = await resolve_subagents(db, request.capabilities, request.context)
    tools = await plan_tools(db, request)
    skills = await resolve_skills(db, request)
    mcp = await plan_mcp(db, request)
    return GraphPlan(request.context, tools, skills, mcp, tuple(subagents))


async def apply_graph(db: AsyncSession, plan: GraphPlan) -> None:
    """The only dependency mutation boundary; use the final preallocated IDs."""
    scope = plan.context
    for draft in plan.tools.creations:
        db.add(
            Tool(
                id=draft.id,
                user_id=scope.user_id,
                org_id=scope.org_id,
                tenant_id=scope.tenant_id,
                is_system=False,
                definition_key=draft.definition_key,
                name=draft.name,
                description=draft.description,
                parameters=draft.parameters,
                credential_id=draft.credential_id,
                enabled=draft.enabled,
            )
        )
    for server in plan.mcp.servers:
        db.add(
            McpServer(
                id=server.id,
                user_id=scope.user_id,
                org_id=scope.org_id,
                tenant_id=scope.tenant_id,
                is_system=False,
                name=server.name,
                description=server.description,
                transport=server.transport,
                url=server.url,
                command=server.command,
                args=list(server.args),
                env_vars=server.env_vars,
                headers=server.headers,
                credential_id=server.credential_id,
                status="unknown",
            )
        )
    for tool in plan.mcp.tools:
        db.add(
            McpTool(
                id=tool.id,
                server_id=tool.server_id,
                name=tool.name,
                description=tool.description,
                input_schema=tool.input_schema,
                enabled=True,
            )
        )
    for assignment in plan.mcp.assignments:
        await db.execute(
            update(McpServer)
            .where(McpServer.id == assignment.server_id)
            .values(credential_id=assignment.credential_id)
            .execution_options(autoflush=False)
        )
    await db.flush()
