"""Company policy per tool or MCP server (tool-governance-plan 3.4, 3.10).

Company administrators (``can_manage_tenant``, as the guardrail policy) switch a tool off,
give it a default approval mode and restrict the modes anything below may use. A save is
validated as the run will read it (``tool_risk.tool_policy_from_json``), names only a tool
this company can see, and is audited with before/after values. ``impact`` lists the agent
settings a change would stop applying, before it is saved.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_policy import ToolApprovalToolV1, resolve_runtime_policy
from app.authz.audit_actions import RESOURCE_POLICY_UPDATED
from app.exceptions import NotFoundError, ValidationError
from app.guardrails import policy as guardrail_policy
from app.guardrails.tool_risk import (
    MAX_TOOL_POLICIES,
    InvalidToolRiskError,
    ToolPolicy,
    level_of,
    tool_policy_from_json,
)
from app.models.agent import Agent
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.organization import Tenant
from app.models.resource_policy import ResourcePolicy
from app.models.tool import Tool
from app.schemas.guardrails import (
    ToolPolicyImpactAgent,
    ToolPolicyImpactOut,
    ToolPolicyOut,
    ToolPolicySaved,
    ToolPolicyTarget,
    ToolPolicyUpdate,
    ToolPolicyWarning,
)
from app.services.guardrails_admin import CompanyMutation, _audit, _company_admin
from app.tenancy.admin_context import (
    TenantAdminContext,
    TenantAdminTarget,
    load_tenant_admin_context,
)
from app.tools.keys import builtin_key, server_key
from app.tools.risk import (
    ToolRiskLevel,
    execute_in_skill_risk,
    mcp_tool_risk,
    risk_from_tool_config,
)

NOT_FOUND: Final = "TOOL_POLICY_TARGET_NOT_FOUND"
INVALID: Final = "TOOL_POLICY_INVALID"
IMPACT_LIMIT: Final = 200
_BUILTIN_DEFINITIONS: Final = (
    "builtin:web_search",
    "builtin:web_scraper",
    "builtin:current_datetime",
    "builtin:resolve_relative_date",
)
_BUILTIN_NAMES: Final = ("write_file", "edit_file", "execute")


def _visible(model: Any, tenant_id: uuid.UUID) -> Any:
    """Rows of this company, and the installation's own (system) rows."""
    return or_(model.tenant_id == tenant_id, model.is_system.is_(True))


def _mcp_level(tool: McpTool, server: McpServer) -> ToolRiskLevel:
    return mcp_tool_risk(
        tool.name,
        metadata=tool.annotations_json,
        config={"url": server.url, "name": tool.name, "description": tool.description},
    ).risk_level


def _builtins() -> list[ToolPolicyTarget]:
    from app.agent_runtime.tool_factory import create_builtin_tool

    found: dict[str, ToolPolicyTarget] = {}
    for definition in _BUILTIN_DEFINITIONS:
        tool = create_builtin_tool(definition)
        if tool is not None:
            found[tool.name] = ToolPolicyTarget(
                key=builtin_key(tool.name),
                name=tool.name,
                kind="builtin",
                risk_level=level_of(tool).value,
            )
    for name in _BUILTIN_NAMES:
        found[name] = ToolPolicyTarget(
            key=builtin_key(name), name=name, kind="builtin", risk_level=level_of(name).value
        )
    found["execute_in_skill"] = ToolPolicyTarget(
        key=builtin_key("execute_in_skill"),
        name="execute_in_skill",
        kind="builtin",
        risk_level=execute_in_skill_risk().risk_level.value,
    )
    return sorted(found.values(), key=lambda item: item.name)


async def _admin(db: AsyncSession, context: TenantAdminContext) -> uuid.UUID:
    """Reads that list the company's tools and agents are for its administrators too."""
    fresh = await load_tenant_admin_context(
        db, context.actor, TenantAdminTarget(context.tenant.id, permission="can_manage_tenant")
    )
    return fresh.tenant.id


async def targets(db: AsyncSession, context: TenantAdminContext) -> list[ToolPolicyTarget]:
    """Everything a policy of this company can name."""
    return await _targets(db, await _admin(db, context))


async def _targets(db: AsyncSession, tenant_id: uuid.UUID) -> list[ToolPolicyTarget]:
    result: list[ToolPolicyTarget] = []
    servers = list(
        await db.scalars(
            select(McpServer).where(_visible(McpServer, tenant_id)).order_by(McpServer.name)
        )
    )
    by_id = {server.id: server for server in servers}
    for server in servers:
        result.append(
            ToolPolicyTarget(key=f"mcp_server:{server.id}", name=server.name, kind="mcp_server")
        )
    if by_id:
        tools = await db.scalars(
            select(McpTool).where(McpTool.server_id.in_(by_id)).order_by(McpTool.name, McpTool.id)
        )
        for tool in tools:
            server = by_id[tool.server_id]
            result.append(
                ToolPolicyTarget(
                    key=f"mcp:{tool.id}",
                    name=tool.name,
                    kind="mcp_tool",
                    server_key=server_key(server.id),
                    source=server.name,
                    risk_level=_mcp_level(tool, server).value,
                )
            )
    rows = await db.scalars(select(Tool).where(_visible(Tool, tenant_id)).order_by(Tool.name))
    for row in rows:
        if row.definition_key.startswith("builtin:"):
            continue  # named by its built-in key below
        result.append(
            ToolPolicyTarget(
                key=f"tool:{row.id}",
                name=row.name,
                kind="tool",
                risk_level=risk_from_tool_config(
                    {"definition_key": row.definition_key}
                ).risk_level.value,
            )
        )
    return [*result, *_builtins()]


async def _target(db: AsyncSession, tenant_id: uuid.UUID, key: str) -> ToolPolicyTarget:
    """The one target ``key`` names; a key of another company is not found, like a typo."""
    kind, _, raw = key.partition(":")
    missing = NotFoundError(NOT_FOUND, "no such tool")
    if kind == "builtin":
        found = next((item for item in _builtins() if item.key == key), None)
        if found is None:
            raise missing
        return found
    try:
        row_id = uuid.UUID(raw)
    except ValueError:
        raise missing from None
    if kind == "mcp_server":
        server = await db.scalar(
            select(McpServer).where(McpServer.id == row_id, _visible(McpServer, tenant_id))
        )
        if server is None:
            raise missing
        return ToolPolicyTarget(key=key, name=server.name, kind="mcp_server")
    if kind == "mcp":
        pair = (
            await db.execute(
                select(McpTool, McpServer)
                .join(McpServer, McpServer.id == McpTool.server_id)
                .where(McpTool.id == row_id, _visible(McpServer, tenant_id))
            )
        ).first()
        if pair is None:
            raise missing
        tool, server = pair
        return ToolPolicyTarget(
            key=key,
            name=tool.name,
            kind="mcp_tool",
            server_key=server_key(server.id),
            source=server.name,
            risk_level=_mcp_level(tool, server).value,
        )
    if kind == "tool":
        row = await db.scalar(select(Tool).where(Tool.id == row_id, _visible(Tool, tenant_id)))
        if row is None or row.definition_key.startswith("builtin:"):
            raise missing
        return ToolPolicyTarget(
            key=key,
            name=row.name,
            kind="tool",
            risk_level=risk_from_tool_config(
                {"definition_key": row.definition_key}
            ).risk_level.value,
        )
    raise missing


def _out(row: ResourcePolicy) -> ToolPolicyOut:
    return ToolPolicyOut(
        resource_key=row.resource_key,
        resource_name=row.resource_name,
        enabled=row.enabled,
        default_mode=row.default_mode,  # type: ignore[arg-type]
        allowed_modes=row.allowed_modes,  # type: ignore[arg-type]
        rule=row.rule,
        updated_at=row.updated_at,
    )


async def list_policies(db: AsyncSession, tenant_id: uuid.UUID) -> list[ToolPolicyOut]:
    rows = await db.scalars(
        select(ResourcePolicy)
        .where(ResourcePolicy.tenant_id == tenant_id, ResourcePolicy.org_id.is_(None))
        .order_by(ResourcePolicy.resource_name, ResourcePolicy.resource_key)
    )
    return [_out(row) for row in rows]


def validated(payload: ToolPolicyUpdate) -> ToolPolicy:
    """The policy as a run will read it; raises 422 on anything a run would refuse."""
    allowed = list(dict.fromkeys(payload.allowed_modes)) if payload.allowed_modes else None
    default, rule = payload.default_mode, payload.rule
    if allowed is not None and default is None:
        if len(allowed) > 1:
            # Without one, a tool nobody configured has no mode the company allows.
            raise ValidationError(INVALID, "restricted modes need a default mode")
        default = allowed[0]  # type: ignore[assignment]
    if default == "when" or rule is not None:
        try:
            setting = ToolApprovalToolV1.model_validate({"mode": default, "rule": rule})
        except PydanticValidationError as exc:
            raise ValidationError(INVALID, "a when default has a rule over the args") from exc
        rule = setting.rule.model_dump(mode="json") if setting.rule else None
    try:
        return tool_policy_from_json(
            {
                "enabled": payload.enabled,
                "default_mode": default,
                "allowed_modes": allowed,
                "rule": rule,
            }
        )
    except InvalidToolRiskError as exc:
        raise ValidationError(INVALID, str(exc)) from exc


def warnings_for(target: ToolPolicyTarget, policy: ToolPolicy) -> list[ToolPolicyWarning]:
    """Allowed, but said out loud: a tool that changes things will run without asking."""
    if policy.enabled is False or policy.default_mode != "never":
        return []
    if target.risk_level == ToolRiskLevel.READ_ONLY.value:
        return []
    return [ToolPolicyWarning(code="risky_without_asking", risk_level=target.risk_level)]


async def _row(db: AsyncSession, tenant_id: uuid.UUID, key: str) -> ResourcePolicy | None:
    return await db.scalar(
        select(ResourcePolicy).where(
            ResourcePolicy.tenant_id == tenant_id,
            ResourcePolicy.org_id.is_(None),
            ResourcePolicy.resource_key == key,
        )
    )


async def save(
    mutation: CompanyMutation, context: TenantAdminContext, key: str, payload: ToolPolicyUpdate
) -> ToolPolicySaved:
    db = mutation.db
    tenant: Tenant = await _company_admin(mutation, context)
    target = await _target(db, tenant.id, key)
    policy = validated(payload)
    row = await _row(db, tenant.id, key)
    before = guardrail_policy.row_policy(row).to_json() if row is not None else None
    if policy.is_empty:
        if row is not None:
            await db.delete(row)
        after = None
    else:
        if row is None:
            existing = await guardrail_policy.tool_policies(db, tenant.id)
            if len(existing) >= MAX_TOOL_POLICIES:
                raise ValidationError(INVALID, f"at most {MAX_TOOL_POLICIES} tool policies")
            row = ResourcePolicy(tenant_id=tenant.id, org_id=None, resource_key=key)
            db.add(row)
        stored = policy.to_json()
        row.resource_name = target.name[:200]
        row.enabled = stored["enabled"]
        row.default_mode = stored["default_mode"]
        row.allowed_modes = stored["allowed_modes"]
        row.rule = stored["rule"]
        row.updated_by = mutation.actor.id
        after = stored
    warnings = [] if after is None else warnings_for(target, policy)
    if before != after:
        await _audit(
            db,
            mutation.request,
            mutation.actor,
            tenant.id,
            RESOURCE_POLICY_UPDATED,
            ("resource_policy", key, target.name),
            before,
            {**after, "warnings": [w.code for w in warnings]} if after is not None else None,
        )
    await db.flush()
    saved = None
    if after is not None and row is not None:
        await db.refresh(row)  # ``updated_at`` is set by the database on an update
        saved = _out(row)
    await db.commit()
    return ToolPolicySaved(policy=saved, warnings=warnings)


async def impact(
    db: AsyncSession, context: TenantAdminContext, key: str, payload: ToolPolicyUpdate
) -> ToolPolicyImpactOut:
    """The agent settings that stop applying if this policy is saved.

    A server policy reaches each of its tools unless the tool has its own row; a setting
    saved by name counts when it names this tool.
    """
    tenant_id = await _admin(db, context)
    target = await _target(db, tenant_id, key)
    policy = validated(payload)
    names: dict[str, str] = {key: target.name}
    if target.kind == "mcp_server":
        own = await guardrail_policy.tool_policies(db, tenant_id)
        tools = await db.scalars(
            select(McpTool).where(McpTool.server_id == uuid.UUID(key.partition(":")[2]))
        )
        names = {
            f"mcp:{tool.id}": tool.name
            for tool in tools
            if (found := own.get(f"mcp:{tool.id}")) is None
            or (found.enabled is None and found.allowed is None)
        }
    by_name = {name: tool_key for tool_key, name in names.items()}
    hits: list[ToolPolicyImpactAgent] = []
    agents = await db.scalars(
        select(Agent)
        .where(
            Agent.tenant_id == tenant_id,
            Agent.runtime_profile == "standard",
            Agent.archived_at.is_(None),
            Agent.runtime_policy.is_not(None),
        )
        .order_by(Agent.name, Agent.id)
    )
    for agent in agents:
        try:
            approval = resolve_runtime_policy(agent.runtime_policy).effective.tool_approval
        except (PydanticValidationError, ValueError):
            continue
        settings = approval.tools if approval is not None else {}
        for named, setting in settings.items():
            tool_key = named if named in names else by_name.get(named)
            if tool_key is None:
                continue
            if policy.enabled is False:
                reason = "disabled"
            elif policy.allowed is not None and setting.mode not in policy.allowed:
                reason = "mode_not_allowed"
            else:
                continue
            hits.append(
                ToolPolicyImpactAgent(
                    id=agent.id,
                    name=agent.name,
                    tool_key=tool_key,
                    tool_name=names[tool_key],
                    mode=setting.mode,
                    reason=reason,  # type: ignore[arg-type]
                )
            )
    return ToolPolicyImpactOut(
        agents=hits[:IMPACT_LIMIT], total=len(hits), warnings=warnings_for(target, policy)
    )
