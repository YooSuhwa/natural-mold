"""Marketplace support for Agent Blueprint resources."""

from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.error_codes import (
    agent_not_found,
    marketplace_acl_required,
    marketplace_invalid_package,
    marketplace_invalid_visibility,
    marketplace_item_not_found,
    marketplace_manage_forbidden,
)
from app.marketplace.access import can_manage_item
from app.marketplace.payloads import canonical_json_bytes, canonical_json_hash
from app.marketplace.publish_common import (
    clean_mapping as _clean_mapping,
)
from app.marketplace.publish_common import (
    contains_credential_placeholder as _contains_credential_placeholder,
)
from app.marketplace.publish_common import (
    create_acl as _create_acl,
)
from app.marketplace.publish_common import (
    next_version_number as _next_version_number,
)
from app.marketplace.publish_common import (
    now as _now,
)
from app.marketplace.publish_common import (
    raise_if_agent_base_url_has_literal_secret as _raise_if_agent_base_url_has_literal_secret,
)
from app.marketplace.publish_common import (
    raise_if_mcp_config_has_literal_secrets as _raise_if_mcp_config_has_literal_secrets,
)
from app.marketplace.publish_common import (
    raise_if_payload_has_secrets as _raise_if_payload_has_secrets,
)
from app.marketplace.publish_common import (
    slugify,
    upsert_publication_link,
)
from app.marketplace.schemas import PublishAgentIn
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.marketplace import (
    MarketplaceItem,
    MarketplaceItemACL,
    MarketplaceVersion,
)
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.model import Model
from app.models.skill import AgentSkillLink
from app.models.tool import AgentToolLink, Tool

if TYPE_CHECKING:
    from app.dependencies import CurrentUser


def _slugify(value: str) -> str:
    return slugify(value, fallback="agent")


def _safe_key(prefix: str, value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_]+", "_", value.strip().lower()).strip("_")
    return f"{prefix}_{cleaned or 'credential'}"


def _model_payload(model: Model | None) -> dict[str, Any] | None:
    if model is None:
        return None
    return {
        "provider": model.provider,
        "model_name": model.model_name,
        "display_name": model.display_name,
        "base_url": model.base_url,
    }


def _fallback_model_ids(agent: Agent) -> list[uuid.UUID]:
    ids: list[uuid.UUID] = []
    for raw in agent.model_fallback_list or []:
        try:
            ids.append(uuid.UUID(str(raw)))
        except (TypeError, ValueError):
            continue
    return ids


def _credential_requirement(
    *,
    key: str,
    definition_key: str,
    label: str,
    description: str,
) -> dict[str, Any]:
    return {
        "key": key,
        "definition_key": definition_key,
        "required": True,
        "label": label,
        "description": description,
        "fields": [],
        "injection": "config",
        "scope": "user",
    }


def _dedupe_requirements(requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for requirement in requirements:
        key = (str(requirement["key"]), str(requirement["definition_key"]))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(requirement)
    return deduped


def _tool_payload(link: AgentToolLink) -> dict[str, Any]:
    tool = link.tool
    return {
        "name": tool.name,
        "description": tool.description,
        "definition_key": tool.definition_key,
        "parameters": _clean_mapping(tool.parameters),
        "enabled": tool.enabled,
        "is_system": tool.is_system,
    }


def _skill_payload(link: AgentSkillLink) -> dict[str, Any]:
    skill = link.skill
    return {
        "name": skill.name,
        "slug": skill.slug,
        "description": skill.description,
        "kind": skill.kind,
        "version": skill.version,
        "origin_kind": skill.origin_kind,
    }


def _mcp_tool_payload(link: AgentMcpToolLink) -> dict[str, Any]:
    tool = link.mcp_tool
    server = tool.server
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema or {},
        "server": {
            "name": server.name,
            "description": server.description,
            "transport": server.transport,
            "url": server.url,
            "command": server.command,
            "args": list(server.args or []),
            "env_vars": _clean_mapping(server.env_vars),
            "headers": _clean_mapping(server.headers),
        },
    }


def _subagent_payload(link: AgentSubAgentLink) -> dict[str, Any]:
    sub_agent = link.sub_agent
    return {
        "name": sub_agent.name,
        "description": sub_agent.description,
        "position": link.position,
    }


def _build_credential_requirements(agent: Agent) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []

    if agent.llm_credential is not None:
        requirements.append(
            _credential_requirement(
                key="llm",
                definition_key=agent.llm_credential.definition_key,
                label="LLM credential",
                description="Credential used by the agent model",
            )
        )

    for link in agent.tool_links:
        tool = link.tool
        if tool.credential is None:
            continue
        requirements.append(
            _credential_requirement(
                key=_safe_key("tool", tool.definition_key),
                definition_key=tool.credential.definition_key,
                label=f"{tool.name} credential",
                description=f"Credential used by {tool.name}",
            )
        )

    for link in agent.mcp_tool_links:
        server = link.mcp_tool.server
        if server.credential is None:
            continue
        requirements.append(
            _credential_requirement(
                key=_safe_key("mcp", server.name),
                definition_key=server.credential.definition_key,
                label=f"{server.name} MCP credential",
                description=f"Credential used by the {server.name} MCP server",
            )
        )

    return _dedupe_requirements(requirements)


def build_agent_spec_payload(
    agent: Agent,
    *,
    fallback_models: list[Model] | None = None,
) -> dict[str, Any]:
    """Build a portable, secret-free Agent Blueprint marketplace payload."""

    tools = [_tool_payload(link) for link in agent.tool_links]
    skills = [_skill_payload(link) for link in agent.skill_links]
    mcp_tools = [_mcp_tool_payload(link) for link in agent.mcp_tool_links]
    subagents = [_subagent_payload(link) for link in agent.sub_agent_links]
    credential_requirements = _build_credential_requirements(agent)
    fallback_payloads = [
        payload
        for model in (fallback_models or [])
        if (payload := _model_payload(model)) is not None
    ]

    payload: dict[str, Any] = {
        "schema_version": 1,
        "resource": "agent_blueprint",
        "name": agent.name,
        "description": agent.description,
        "agent": {
            "name": agent.name,
            "description": agent.description,
            "system_prompt": agent.system_prompt,
            "identity_mode": agent.identity_mode,
            "model": _model_payload(agent.model),
            "model_fallbacks": fallback_payloads,
            "model_params": agent.model_params or {},
            "middleware_configs": agent.middleware_configs or [],
            "opener_questions": agent.opener_questions or [],
        },
        "capabilities": {
            "tools": tools,
            "skills": skills,
            "mcp_tools": mcp_tools,
            "subagents": subagents,
        },
        "setup": {
            "required_credentials": credential_requirements,
            "warnings": [],
            "blocked_dependencies": [],
        },
    }
    _raise_if_payload_has_secrets(payload)
    return payload


def _dependency_requirements(payload: dict[str, Any]) -> list[dict[str, Any]]:
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, dict):
        return []

    requirements: list[dict[str, Any]] = []
    for tool in capabilities.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        requirements.append(
            {
                "type": "tool",
                "definition_key": tool.get("definition_key"),
                "name": tool.get("name"),
                "required": True,
            }
        )
    for skill in capabilities.get("skills") or []:
        if not isinstance(skill, dict):
            continue
        requirements.append(
            {
                "type": "skill",
                "name": skill.get("name"),
                "slug": skill.get("slug"),
                "kind": skill.get("kind"),
                "required": True,
            }
        )
    for mcp_tool in capabilities.get("mcp_tools") or []:
        if not isinstance(mcp_tool, dict):
            continue
        server = mcp_tool.get("server") if isinstance(mcp_tool.get("server"), dict) else {}
        requirements.append(
            {
                "type": "mcp_tool",
                "name": mcp_tool.get("name"),
                "server_name": server.get("name") if isinstance(server, dict) else None,
                "transport": server.get("transport") if isinstance(server, dict) else None,
                "required": True,
            }
        )
    return requirements


def _execution_profile(payload: dict[str, Any]) -> dict[str, Any]:
    capabilities = payload.get("capabilities") if isinstance(payload, dict) else {}
    mcp_tools = capabilities.get("mcp_tools") if isinstance(capabilities, dict) else []
    tools = capabilities.get("tools") if isinstance(capabilities, dict) else []
    requires_network = any(
        isinstance(row, dict)
        and isinstance(row.get("server"), dict)
        and row["server"].get("transport") in {"sse", "streamable_http"}
        for row in (mcp_tools or [])
    )
    return {
        "support_level": "ready_python",
        "runners": ["deepagents"],
        "requires_network": requires_network,
        "tool_dependencies": [
            str(row.get("definition_key"))
            for row in (tools or [])
            if isinstance(row, dict) and row.get("definition_key")
        ],
    }


async def _load_agent(db: AsyncSession, *, agent_id: uuid.UUID, user_id: uuid.UUID) -> Agent:
    result = await db.execute(
        select(Agent)
        .where(Agent.id == agent_id, Agent.user_id == user_id)
        .options(
            selectinload(Agent.model),
            selectinload(Agent.llm_credential),
            selectinload(Agent.tool_links)
            .selectinload(AgentToolLink.tool)
            .selectinload(Tool.credential),
            selectinload(Agent.skill_links).selectinload(AgentSkillLink.skill),
            selectinload(Agent.mcp_tool_links)
            .selectinload(AgentMcpToolLink.mcp_tool)
            .selectinload(McpTool.server)
            .selectinload(McpServer.credential),
            selectinload(Agent.sub_agent_links),
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise agent_not_found()
    return agent


async def _load_fallback_models(db: AsyncSession, *, agent: Agent) -> list[Model]:
    fallback_ids = _fallback_model_ids(agent)
    if not fallback_ids:
        return []
    rows = (
        await db.execute(select(Model).where(Model.id.in_(fallback_ids)))
    ).scalars().all()
    by_id = {model.id: model for model in rows}
    return [by_id[model_id] for model_id in fallback_ids if model_id in by_id]


def _reject_non_portable_skill_dependencies(agent: Agent, *, visibility: str) -> None:
    if visibility == "private" or not agent.skill_links:
        return
    names = ", ".join(link.skill.name for link in agent.skill_links if link.skill)
    raise marketplace_invalid_package(
        "Agent Blueprints with skill dependencies cannot be shared "
        f"outside private visibility yet: {names or 'unknown skill'}"
    )


def _reject_non_portable_subagent_dependencies(agent: Agent, *, visibility: str) -> None:
    if visibility == "private" or not agent.sub_agent_links:
        return
    names = ", ".join(
        link.sub_agent.name for link in agent.sub_agent_links if link.sub_agent
    )
    raise marketplace_invalid_package(
        "Agent Blueprints with subagent dependencies cannot be shared "
        f"outside private visibility yet: {names or 'unknown subagent'}"
    )


def _reject_public_stdio_mcp_dependencies(agent: Agent, *, visibility: str) -> None:
    if visibility not in {"public", "unlisted"}:
        return
    names = [
        link.mcp_tool.server.name
        for link in agent.mcp_tool_links
        if link.mcp_tool and link.mcp_tool.server.transport == "stdio"
    ]
    if not names:
        return
    raise marketplace_invalid_package(
        "Agent Blueprints with stdio MCP dependencies can only be shared "
        f"privately or with explicit ACL: {', '.join(names)}"
    )


def _reject_unbound_mcp_credential_templates(agent: Agent) -> None:
    names = [
        link.mcp_tool.server.name
        for link in agent.mcp_tool_links
        if link.mcp_tool
        and link.mcp_tool.server.credential is None
        and (
            _contains_credential_placeholder(link.mcp_tool.server.headers)
            or _contains_credential_placeholder(link.mcp_tool.server.env_vars)
        )
    ]
    if not names:
        return
    raise marketplace_invalid_package(
        "Agent Blueprints with MCP credential templates require bound credentials: "
        f"{', '.join(names)}"
    )


def _reject_mcp_literal_secrets(agent: Agent) -> None:
    """Apply the MCP env_vars/headers/args allowlist gate to every embedded
    MCP server so an agent blueprint can't smuggle a literal secret that
    the standalone MCP publish path already rejects."""

    seen: set[uuid.UUID] = set()
    for link in agent.mcp_tool_links:
        if not link.mcp_tool:
            continue
        server = link.mcp_tool.server
        if server.id in seen:
            continue
        seen.add(server.id)
        _raise_if_mcp_config_has_literal_secrets(
            server_name=server.name,
            env_vars=_clean_mapping(server.env_vars),
            headers=_clean_mapping(server.headers),
            args=list(server.args or []),
        )


def _reject_model_base_url_secrets(agent: Agent, fallback_models: list[Model]) -> None:
    """Apply the allowlist gate to the agent model + fallback ``base_url``s
    so a credential-bearing endpoint URL can't leak via the blueprint."""

    if agent.model is not None:
        _raise_if_agent_base_url_has_literal_secret(
            label=f"Agent '{agent.name}' model",
            base_url=agent.model.base_url,
        )
    for model in fallback_models:
        _raise_if_agent_base_url_has_literal_secret(
            label=f"Agent '{agent.name}' fallback model '{model.display_name}'",
            base_url=model.base_url,
        )


async def publish_agent(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    user: CurrentUser,
    body: PublishAgentIn,
) -> MarketplaceItem:
    agent = await _load_agent(db, agent_id=agent_id, user_id=user.id)

    if body.visibility not in ("private", "restricted", "public", "unlisted"):
        raise marketplace_invalid_visibility(
            f"unsupported publish visibility: {body.visibility}"
        )
    if body.visibility == "restricted" and not body.acl_user_ids:
        raise marketplace_acl_required()
    _reject_non_portable_skill_dependencies(agent, visibility=body.visibility)
    _reject_non_portable_subagent_dependencies(agent, visibility=body.visibility)
    _reject_public_stdio_mcp_dependencies(agent, visibility=body.visibility)
    _reject_unbound_mcp_credential_templates(agent)
    _reject_mcp_literal_secrets(agent)
    fallback_models = await _load_fallback_models(db, agent=agent)
    _reject_model_base_url_secrets(agent, fallback_models)

    item: MarketplaceItem | None = None
    if body.item_id is not None:
        item = await db.get(MarketplaceItem, body.item_id)
        if item is None:
            raise marketplace_item_not_found()
        if not can_manage_item(item, user):
            raise marketplace_manage_forbidden()
        if item.resource_type != "agent":
            raise marketplace_invalid_package(
                "marketplace item is not an Agent item"
            )
    else:
        slug = _slugify(body.name)
        item = (
            await db.execute(
                select(MarketplaceItem)
                .where(MarketplaceItem.owner_user_id == user.id)
                .where(MarketplaceItem.resource_type == "agent")
                .where(MarketplaceItem.slug == slug)
                .limit(1)
            )
        ).scalar_one_or_none()
        if item is None:
            item = MarketplaceItem(
                id=uuid.uuid4(),
                resource_type="agent",
                owner_user_id=user.id,
                is_system=False,
                is_listed=False,
                name=body.name,
                slug=slug,
                description=body.description,
                icon_id=body.icon_id,
                visibility=body.visibility,
                status="draft",
                moderation_status="approved",
                source_kind="user",
                tags=list(body.tags) or None,
                categories=list(body.categories) or None,
            )
            db.add(item)
            await db.flush()
        elif not can_manage_item(item, user):
            raise marketplace_manage_forbidden()

    payload = build_agent_spec_payload(agent, fallback_models=fallback_models)
    content_bytes = canonical_json_bytes(payload)
    content_hash = canonical_json_hash(payload)

    existing_version = (
        await db.execute(
            select(MarketplaceVersion)
            .where(MarketplaceVersion.item_id == item.id)
            .where(MarketplaceVersion.content_hash == content_hash)
            .order_by(MarketplaceVersion.version_number.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if existing_version is not None:
        version = existing_version
    else:
        version_number = await _next_version_number(db, item.id)
        credential_requirements = payload["setup"]["required_credentials"]
        version = MarketplaceVersion(
            id=uuid.uuid4(),
            item_id=item.id,
            version_label=f"agent-{version_number}",
            version_number=version_number,
            resource_type="agent",
            payload_kind="agent_spec",
            payload=payload,
            storage_path=None,
            content_hash=content_hash,
            size_bytes=len(content_bytes),
            credential_requirements=credential_requirements,
            dependency_requirements=_dependency_requirements(payload),
            execution_profile=_execution_profile(payload),
            release_notes=body.release_notes,
            created_by=user.id,
        )
        db.add(version)
        await db.flush()

    item.latest_version_id = version.id
    item.visibility = body.visibility
    item.status = "published"
    item.published_at = _now()
    item.updated_at = _now()
    if body.item_id is None:
        item.name = body.name
        item.slug = _slugify(body.name)
        item.description = body.description
        item.icon_id = body.icon_id
        item.tags = list(body.tags) or None
        item.categories = list(body.categories) or None
    else:
        if body.description is not None:
            item.description = body.description
        if body.icon_id is not None:
            item.icon_id = body.icon_id
        if body.tags:
            item.tags = list(body.tags)
        if body.categories:
            item.categories = list(body.categories)

    if body.visibility == "restricted":
        await _create_acl(
            db, item=item, user_ids=list(body.acl_user_ids), permission="install"
        )
    else:
        existing_acl = (
            await db.execute(
                select(MarketplaceItemACL).where(MarketplaceItemACL.item_id == item.id)
            )
        ).scalars().all()
        for row in existing_acl:
            await db.delete(row)

    await upsert_publication_link(
        db,
        item=item,
        user_id=user.id,
        resource_type="agent",
        source_agent_id=agent.id,
    )
    await db.flush()
    return item


__all__ = ["build_agent_spec_payload", "publish_agent"]
