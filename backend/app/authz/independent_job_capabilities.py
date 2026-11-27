"""Live authority for the frozen bindings of an independent job's root agent.

The job stores only the canonical SHA-256. Reuse the ordinary runtime's grant,
credential and governance decisions without opening secrets, refreshing OAuth,
contacting tool servers, or performing any of the delegated actions.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.credential_access import AgentCredentialAccess
from app.agent_runtime.credential_resolution import llm_credential_delegated
from app.agent_runtime.identity import (
    AgentRunSource,
    make_agent_runtime_name,
    resolve_agent_run_identity,
)
from app.authz import tool_blocks
from app.authz.catalog_fanout import enabled_entries
from app.authz.company_catalog import company_tier, stored_policy
from app.authz.org_catalog_queries import CatalogEntry
from app.authz.org_context import OrgContext
from app.authz.private_actor import require_private_actor
from app.authz.registration_state import registered
from app.authz.runtime_access_planner import RuntimeAccessPlan, RuntimeAccessPlanner
from app.authz.scoping import private_scope_predicate
from app.config import settings
from app.credentials.company_use import (
    company_credential_usable,
    company_credentials_for,
    designated_to,
    is_company_credential,
)
from app.credentials.run_resolution import RunCredentialResolver
from app.credentials.service import PROVIDER_TO_DEFINITION_KEY
from app.error_codes import agent_not_found, mcp_server_not_found, tool_not_found
from app.exceptions import AppError
from app.guardrails import policy
from app.guardrails.run import guardrail_versions
from app.mcp import review
from app.models.agent import Agent
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.organization import Organization, Tenant
from app.models.tool import Tool
from app.services.chat.run_agents import get_run_agent_with_tools


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _credential(credential: Credential) -> dict[str, Any]:
    return {
        "id": credential.id,
        "owner": credential.user_id,
        "definition": credential.definition_key,
        "allowed_hosts": sorted(credential.allowed_hosts or ()),
        "status": credential.status,
        "cipher_version": _digest(credential.data_encrypted),
    }


async def _llm_credentials(
    db: AsyncSession, child: Agent, access: AgentCredentialAccess
) -> list[dict[str, Any]]:
    """Pin every eligible tier: a failed decryption can advance to the next tier at prepare time."""
    subject = replace(access.context, user_id=access.identity.credential_subject_user_id)
    if subject.user_id != access.context.user_id:
        await require_private_actor(db, subject)
    model = child.model
    candidates: list[Credential] = []

    async def eligible_company(row: Credential) -> bool:
        usable = await company_credential_usable(
            db, row, user_id=subject.user_id, org_id=subject.org_id, tenant_id=subject.tenant_id
        )
        if usable and not await designated_to(db, row, subject.org_id):
            raise AppError(
                "independent_credential_unavailable",
                "조직에서 모델 연결을 사용할 수 없습니다",
                status=409,
            )
        return usable

    bound = child.llm_credential
    if bound:
        owned = (bound.user_id, bound.org_id, bound.tenant_id) == (
            subject.user_id,
            subject.org_id,
            subject.tenant_id,
        )
        if (
            not bound.is_system
            and bound.status == "active"
            and (owned or await llm_credential_delegated(bound, child, access))
        ):
            candidates.append(bound)

    if model.default_credential_id is not None:
        row = await db.get(Credential, model.default_credential_id, populate_existing=True)
        if row:
            owned = not row.is_system and (row.user_id, row.org_id, row.tenant_id) == (
                subject.user_id,
                subject.org_id,
                subject.tenant_id,
            )
            if owned and row.status != "active":
                # The default resolver may otherwise try to open an inactive personal key.
                raise AppError(
                    "independent_credential_unavailable",
                    "모델 연결을 사용할 수 없습니다",
                    status=409,
                )
            if owned or await eligible_company(row):
                candidates.append(row)

    key = PROVIDER_TO_DEFINITION_KEY.get(model.provider)
    if key:
        personal = await db.scalar(
            select(Credential)
            .where(
                private_scope_predicate(Credential, subject),
                Credential.is_system.is_(False),
                Credential.definition_key == key,
                Credential.status == "active",
            )
            .order_by(Credential.created_at.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        )
        if personal:
            candidates.append(personal)
        for company in await company_credentials_for(db, subject.tenant_id, key):
            if await eligible_company(company):
                candidates.append(company)
    return [_credential(row) for row in candidates]


async def _connection(
    resolver: RunCredentialResolver, resource: Tool | McpServer, kind: str
) -> dict[str, Any] | None:
    missing = tool_not_found() if isinstance(resource, Tool) else mcp_server_not_found()
    await resolver.require_resource(resource, kind, missing)
    # The ordinary resolver separates identity/grant selection from opening the credential.
    row = await resolver.credential_for(resource, kind)
    resolver.raise_missing()
    if (
        row is not None
        and is_company_credential(row, resolver.subject.context.tenant_id)
        and (
            resolver.db is None
            or not await designated_to(resolver.db, row, resolver.subject.context.org_id)
        )
    ):
        raise AppError(
            "independent_credential_unavailable",
            "조직에서 도구 연결을 사용할 수 없습니다",
            status=409,
        )
    return _credential(row) if row is not None else None


async def _tool_bindings(
    db: AsyncSession, plan: RuntimeAccessPlan, context: OrgContext
) -> dict[str, object]:
    resolver = RunCredentialResolver(db, plan.subject, decided=plan.decided)
    tools = {}
    for link in plan.tool_links:
        tool = link.tool
        if tool is None or not tool.enabled or not registered(tool):
            continue
        tools[str(tool.id)] = [
            tool.user_id,
            tool.name,
            tool.description,
            tool.definition_key,
            tool.parameters,
            tool.approval_default,
            tool.credential_mode,
            link.link_status,
            await _connection(resolver, tool, "tool"),
        ]

    mcp = {}
    linked = [link for link in plan.mcp_tool_links if link.mcp_tool is not None]
    blocked = (
        await tool_blocks.blocked_tool_ids(
            db, context.org_id, context.user_id, [link.mcp_tool.id for link in linked]
        )
        if linked
        else set()
    )
    servers: dict[str, Any] = {}
    for link in linked:
        item = link.mcp_tool
        server = item.server
        if (
            item.id in blocked
            or not review.is_usable(item)
            or server is None
            or not registered(server)
        ):
            continue
        sid = str(server.id)
        if sid not in servers:
            servers[sid] = [
                server.user_id,
                server.name,
                server.transport,
                server.url,
                server.command,
                server.args,
                server.headers,
                server.env_vars,
                server.registration_fingerprint,
                server.approval_default,
                server.credential_mode,
                await _connection(resolver, server, "mcp_server"),
            ]
        mcp[str(item.id)] = [
            sid,
            item.name,
            item.description,
            review.approved_definition(item),
            item.approved_fingerprint,
            item.approval_default,
            link.link_status,
        ]
    skills = {
        str(link.skill.id): [
            link.skill.user_id,
            link.skill.slug,
            link.skill.content_hash,
            link.skill.current_revision_id,
            link.skill.version,
            link.skill.execution_profile,
            link.skill.approval_default,
            link.skill.credential_requirements,
            link.config,
            link.link_status,
        ]
        for link in plan.skill_links
        if link.skill is not None
    }
    return {"tools": tools, "servers": servers, "mcp": mcp, "skills": skills}


async def _check_catalog(db: AsyncSession, plan: RuntimeAccessPlan, context: OrgContext) -> None:
    model = plan.agent.model
    tenant = await db.get(Tenant, context.tenant_id, populate_existing=True)
    org = await db.get(Organization, context.org_id, populate_existing=True)
    if tenant is None or org is None or org.tenant_id != tenant.id or model.kind != "chat":
        raise agent_not_found()
    entries = [
        CatalogEntry(
            "model",
            model.id,
            model.display_name,
            "company" if model.tenant_id else "platform",
            available=model.is_visible,
        )
    ]
    for link in plan.tool_links:
        if link.tool and link.tool.enabled and registered(link.tool) and link.tool.is_system:
            entries.append(CatalogEntry("tool", link.tool.id, link.tool.name, "platform"))
    for link in plan.mcp_tool_links:
        tool = link.mcp_tool
        if tool and review.is_usable(tool) and tool.server and tool.server.is_system:
            entries.append(CatalogEntry("mcp_server", tool.server.id, tool.server.name, "platform"))
    for link in plan.skill_links:
        if link.skill and link.skill.is_system:
            entries.append(CatalogEntry("skill", link.skill.id, link.skill.name, "platform"))
    admitted = enabled_entries(
        entries, company_tier(tenant), stored_policy(org.settings), context.org_id
    )
    if len(admitted) != len(entries) or any(
        link.link_status != "active"
        for link in (*plan.tool_links, *plan.mcp_tool_links, *plan.skill_links)
    ):
        raise AppError(
            "independent_capability_revoked",
            "이 작업에서 사용할 수 없는 모델이나 도구가 있습니다",
            status=409,
        )


async def independent_capability_fingerprint(
    db: AsyncSession, child: Agent, context: OrgContext
) -> str:
    """Compare the same server-derived authority at handoff, materialization and each send."""
    agent = await get_run_agent_with_tools(db, child.id, context)
    if agent is None or agent.archived_at is not None:
        raise agent_not_found()
    model = agent.model
    if model is None or model.tenant_id not in (None, context.tenant_id):
        raise agent_not_found()
    identity = resolve_agent_run_identity(
        agent_id=agent.id,
        agent_owner_user_id=agent.user_id,
        runtime_name=agent.runtime_name or make_agent_runtime_name(agent.id),
        identity_mode=agent.identity_mode,
        source=AgentRunSource.CHAT,
        caller_user_id=context.user_id,
    )
    plan = await RuntimeAccessPlanner(context, identity).plan(agent)
    await _check_catalog(db, plan, context)
    access = AgentCredentialAccess(context, identity)
    rules = await policy.resolve(db, context.tenant_id, agent.id, agent.runtime_profile)
    risk = await policy.resolve_tool_risk(db, context.tenant_id, agent.runtime_profile)
    return _digest(
        {
            "schema": 1,
            "actor": [
                context.tenant_id,
                context.org_id,
                context.user_id,
                plan.subject.account_user_id,
            ],
            "agent": [
                agent.id,
                agent.user_id,
                agent.identity_mode,
                agent.runtime_profile,
                agent.system_prompt,
                agent.model_params,
                agent.middleware_configs,
                agent.model_fallback_list,
            ],
            "model": [
                model.id,
                model.provider,
                model.model_name,
                model.kind,
                model.base_url,
                model.tenant_id,
                model.is_visible,
                model.default_credential_id,
            ],
            "credentials": await _llm_credentials(db, agent, access),
            "bindings": await _tool_bindings(db, plan, context),
            "guardrails": guardrail_versions(rules, risk),
            "always_ask": sorted(risk.always_ask) if risk else [],
            "outbound_hosts": [
                settings.credential_http_allowed_hosts,
                settings.outbound_private_allowed_hosts,
            ],
        }
    )
