"""Materialization service for installed Agent Blueprints."""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.middleware_registry import MIDDLEWARE_REGISTRY
from app.error_codes import (
    marketplace_credential_required,
    marketplace_invalid_package,
    marketplace_item_not_found,
    model_not_found,
)
from app.marketplace.schemas import CreateAgentFromBlueprintIn
from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent
from app.models.agent_blueprint import AgentBlueprint
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.model import Model
from app.models.skill import Skill
from app.models.tool import Tool
from app.schemas.agent import AgentCreate
from app.services import agent_service
from app.tools.registry import registry as tool_registry


async def _resolve_model_id(
    db: AsyncSession,
    *,
    spec: dict[str, Any],
    requested_model_id: uuid.UUID | None,
) -> uuid.UUID:
    if requested_model_id is not None:
        # The override is caller-supplied input. A missing OR operator-hidden
        # (``is_visible=False``) model is rejected uniformly with
        # ``marketplace_invalid_package`` (422) so the response can't be used
        # as an enumeration oracle (existence vs visibility vs other publish
        # failures all 4xx the same way) and a hidden model can't be forced.
        model = await db.get(Model, requested_model_id)
        if model is None or not model.is_visible:
            raise marketplace_invalid_package("requested model is not available")
        return model.id

    agent_spec = spec.get("agent") if isinstance(spec.get("agent"), dict) else {}
    model_spec = agent_spec.get("model") if isinstance(agent_spec.get("model"), dict) else {}
    model_id = await _resolve_model_descriptor(db, model_spec=model_spec)
    if model_id is not None:
        return model_id

    raise model_not_found()


async def _resolve_model_descriptor(
    db: AsyncSession,
    *,
    model_spec: dict[str, Any],
) -> uuid.UUID | None:
    preferred_id = model_spec.get("preferred_model_id")
    if preferred_id:
        try:
            model = await db.get(Model, uuid.UUID(str(preferred_id)))
        except ValueError:
            model = None
        # ``preferred_model_id`` rides inside the publisher-controlled spec,
        # so it must not silently pull an operator-hidden model into a
        # materialized agent. Only honor a visible model; otherwise fall
        # through to provider/model_name matching below.
        if model is not None and model.is_visible:
            return model.id

    provider = model_spec.get("provider")
    model_name = model_spec.get("model_name")
    base_url = model_spec.get("base_url")
    if provider and model_name:
        stmt = select(Model).where(
            Model.provider == str(provider),
            Model.model_name == str(model_name),
            Model.is_visible.is_(True),
        )
        if base_url is None:
            stmt = stmt.where(Model.base_url.is_(None))
        else:
            stmt = stmt.where(Model.base_url == str(base_url))
        model = (await db.execute(stmt.limit(1))).scalar_one_or_none()
        if model is not None:
            return model.id

    return None


async def _resolve_model_fallback_ids(
    db: AsyncSession,
    *,
    spec: dict[str, Any],
    requested_model_fallback_ids: list[uuid.UUID] | None,
) -> list[uuid.UUID] | None:
    if requested_model_fallback_ids is not None:
        return requested_model_fallback_ids

    agent_spec = spec.get("agent") if isinstance(spec.get("agent"), dict) else {}
    rows = (
        agent_spec.get("model_fallbacks")
        if isinstance(agent_spec.get("model_fallbacks"), list)
        else []
    )
    fallback_ids: list[uuid.UUID] = []
    for row in rows:
        if not isinstance(row, dict):
            raise marketplace_invalid_package("model fallback is malformed")
        model_id = await _resolve_model_descriptor(db, model_spec=row)
        if model_id is None:
            raise model_not_found()
        if model_id not in fallback_ids:
            fallback_ids.append(model_id)
    return fallback_ids or None


def _capabilities(spec: dict[str, Any]) -> dict[str, Any]:
    value = spec.get("capabilities")
    return value if isinstance(value, dict) else {}


def _validated_middleware_configs(agent_spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate publisher-controlled middleware configs against the catalog.

    Unknown keys are rejected with ``marketplace_invalid_package`` (naming
    the offending keys) instead of leaking a 500 from the AgentCreate
    pydantic validator.
    """

    configs = agent_spec.get("middleware_configs")
    if configs is None:
        return []
    if not isinstance(configs, list):
        raise marketplace_invalid_package("middleware configs are malformed")
    unknown: list[str] = []
    for row in configs:
        if not isinstance(row, dict):
            raise marketplace_invalid_package("middleware config is malformed")
        key = str(row.get("type") or "")
        if key not in MIDDLEWARE_REGISTRY:
            unknown.append(key or "<missing type>")
    if unknown:
        raise marketplace_invalid_package(
            f"unknown middleware keys: {', '.join(sorted(set(unknown)))}"
        )
    return configs


def _safe_key(prefix: str, value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_]+", "_", value.strip().lower()).strip("_")
    return f"{prefix}_{cleaned or 'credential'}"


def _tool_parameters_from_spec(row: dict[str, Any]) -> dict[str, Any]:
    if isinstance(row.get("parameters"), dict):
        return dict(row["parameters"])
    return {}


def _tool_parameters_match(tool: Tool, expected: dict[str, Any]) -> bool:
    return (tool.parameters or {}) == expected


def _first_tool_with_parameters(
    tools: list[Tool],
    expected: dict[str, Any],
) -> Tool | None:
    for tool in tools:
        if _tool_parameters_match(tool, expected):
            return tool
    return None


def _credential_requirements(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    setup = spec.get("setup") if isinstance(spec.get("setup"), dict) else {}
    rows = (
        setup.get("required_credentials")
        if isinstance(setup.get("required_credentials"), list)
        else []
    )
    return {str(row.get("key")): row for row in rows if isinstance(row, dict) and row.get("key")}


def _coerce_uuid_bindings(values: dict[str, Any] | None) -> dict[str, uuid.UUID]:
    bindings: dict[str, uuid.UUID] = {}
    for key, raw_id in (values or {}).items():
        try:
            bindings[str(key)] = uuid.UUID(str(raw_id))
        except (TypeError, ValueError):
            continue
    return bindings


def _merged_credential_bindings(
    *,
    blueprint: AgentBlueprint,
    body: CreateAgentFromBlueprintIn,
) -> dict[str, uuid.UUID]:
    merged = _coerce_uuid_bindings(blueprint.credential_bindings)
    merged.update({str(key): value for key, value in body.credential_bindings.items()})
    return merged


async def _validate_credential_bindings(
    db: AsyncSession,
    *,
    spec: dict[str, Any],
    bindings: dict[str, uuid.UUID],
    user_id: uuid.UUID,
) -> dict[str, uuid.UUID]:
    requirements = _credential_requirements(spec)
    missing = [
        key
        for key, requirement in requirements.items()
        if requirement.get("required") and key not in bindings
    ]
    if missing:
        raise marketplace_credential_required(
            f"missing required credential bindings: {', '.join(missing)}"
        )

    validated: dict[str, uuid.UUID] = {}
    for key, credential_id in bindings.items():
        requirement = requirements.get(key)
        if requirement is None:
            continue
        credential = await db.get(Credential, credential_id)
        if credential is None or credential.user_id != user_id:
            raise marketplace_credential_required(f"invalid credential binding: {key}")
        expected_definition = requirement.get("definition_key")
        if expected_definition and credential.definition_key != expected_definition:
            raise marketplace_credential_required(f"credential definition mismatch: {key}")
        validated[key] = credential.id
    return validated


async def _resolve_tool_ids(
    db: AsyncSession,
    *,
    capabilities: dict[str, Any],
    user_id: uuid.UUID,
    body: CreateAgentFromBlueprintIn,
    credential_bindings: dict[str, uuid.UUID],
) -> list[uuid.UUID]:
    """Resolve blueprint tool dependencies to user-visible ``Tool`` rows.

    Blueprint specs are publisher-controlled input — a new ``Tool`` row is
    only created when the ``definition_key`` exists in the operator-defined
    tool registry. ``builtin:*`` keys never get user-owned copies: they must
    match an existing (system) tool row or the materialization is rejected.
    """

    rows = capabilities.get("tools") if isinstance(capabilities.get("tools"), list) else []
    if not rows:
        return []

    definition_keys: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise marketplace_invalid_package("tool dependency is malformed")
        definition_key = row.get("definition_key")
        if not definition_key:
            raise marketplace_invalid_package("tool dependency is missing definition_key")
        key = str(definition_key)
        if not key.startswith("builtin:") and tool_registry.get(key) is None:
            raise marketplace_invalid_package(f"unknown tool definition_key: {key}")
        definition_keys.add(key)

    # Single IN query (anti-N+1) — per-row matching happens in memory.
    # Order mirrors the previous per-row query: user tools before system
    # tools, then by name.
    pool: list[Tool] = list(
        (
            await db.execute(
                select(Tool)
                .where(Tool.definition_key.in_(definition_keys))
                .where(Tool.visible_to(user_id))
                .where(Tool.enabled.is_(True))
                .order_by(Tool.user_id.is_not(None).desc(), Tool.name.asc())
            )
        )
        .scalars()
        .all()
    )

    tool_ids: list[uuid.UUID] = []
    for row in rows:
        definition_key = str(row.get("definition_key"))
        is_builtin = definition_key.startswith("builtin:")
        credential_id = credential_bindings.get(_safe_key("tool", definition_key))
        name = str(row.get("name") or definition_key)
        expected_parameters = _tool_parameters_from_spec(row)
        scoped = [tool for tool in pool if tool.definition_key == definition_key]

        if credential_id is not None:
            existing_copy = _first_tool_with_parameters(
                [
                    tool
                    for tool in scoped
                    if tool.user_id == user_id
                    and tool.name == name
                    and tool.credential_id == credential_id
                ],
                expected_parameters,
            )
            if existing_copy is not None:
                tool_ids.append(existing_copy.id)
                continue

            if body.dependency_strategy == "reuse_existing" or is_builtin:
                raise marketplace_invalid_package(f"missing tool dependency: {name}")

            tool = Tool(
                id=uuid.uuid4(),
                user_id=user_id,
                is_system=False,
                definition_key=definition_key,
                name=name,
                description=row.get("description"),
                parameters=expected_parameters,
                credential_id=credential_id,
                enabled=True,
            )
            db.add(tool)
            await db.flush()
            pool.append(tool)
            tool_ids.append(tool.id)
            continue

        requested_name = row.get("name")
        if requested_name:
            named = _first_tool_with_parameters(
                [tool for tool in scoped if tool.name == str(requested_name)],
                expected_parameters,
            )
            if named is not None:
                tool_ids.append(named.id)
                continue
        tool = _first_tool_with_parameters(scoped, expected_parameters)
        if tool is not None:
            tool_ids.append(tool.id)
            continue

        if body.dependency_strategy == "reuse_existing" or is_builtin:
            raise marketplace_invalid_package(f"missing tool dependency: {name}")

        existing_copy = _first_tool_with_parameters(
            [
                tool
                for tool in scoped
                if tool.user_id == user_id and tool.name == name and tool.credential_id is None
            ],
            expected_parameters,
        )
        if existing_copy is not None:
            tool_ids.append(existing_copy.id)
            continue

        tool = Tool(
            id=uuid.uuid4(),
            user_id=user_id,
            is_system=False,
            definition_key=definition_key,
            name=name,
            description=row.get("description"),
            parameters=expected_parameters,
            credential_id=None,
            enabled=bool(row.get("enabled", True)),
        )
        db.add(tool)
        await db.flush()
        if tool.enabled:
            pool.append(tool)
        tool_ids.append(tool.id)
    return tool_ids


async def _resolve_skill_ids(
    db: AsyncSession,
    *,
    capabilities: dict[str, Any],
    user_id: uuid.UUID,
) -> list[uuid.UUID]:
    rows = capabilities.get("skills") if isinstance(capabilities.get("skills"), list) else []
    if not rows:
        return []

    slugs: set[str] = set()
    names: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise marketplace_invalid_package("skill dependency is malformed")
        if row.get("slug"):
            slugs.add(str(row["slug"]))
        if row.get("name"):
            names.add(str(row["name"]))

    # Single IN query (anti-N+1) — per-row matching happens in memory.
    filters = []
    if slugs:
        filters.append(Skill.slug.in_(slugs))
    if names:
        filters.append(Skill.name.in_(names))
    candidates: list[Skill] = []
    if filters:
        candidates = list(
            (await db.execute(select(Skill).where(Skill.user_id == user_id).where(or_(*filters))))
            .scalars()
            .all()
        )

    skill_ids: list[uuid.UUID] = []
    for row in rows:
        slug = row.get("slug")
        name = row.get("name")
        kind = row.get("kind")
        label = str(name or slug or "unnamed skill")
        skill: Skill | None = None
        if slug:
            skill = next((s for s in candidates if s.slug == str(slug)), None)
        if skill is None and name:
            skill = next(
                (
                    s
                    for s in candidates
                    if s.name == str(name) and (not kind or s.kind == str(kind))
                ),
                None,
            )
        if skill is None:
            raise marketplace_invalid_package(f"missing skill dependency: {label}")
        skill_ids.append(skill.id)
    return skill_ids


def _credential_id_for_requirement(
    *,
    credential_bindings: dict[str, uuid.UUID],
    key: str,
) -> uuid.UUID | None:
    return credential_bindings.get(key)


def _normalized_mapping(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _normalized_list(value: Any) -> list[Any]:
    return list(value or []) if isinstance(value, list) else []


def _same_optional_text(left: Any, right: Any) -> bool:
    left_text = str(left) if left is not None else None
    right_text = str(right) if right is not None else None
    return left_text == right_text


def _mcp_server_matches_spec(server: McpServer, server_spec: dict[str, Any]) -> bool:
    transport = str(server_spec.get("transport") or "streamable_http")
    return (
        server.transport == transport
        and _same_optional_text(server.url, server_spec.get("url"))
        and _same_optional_text(server.command, server_spec.get("command"))
        and _normalized_list(server.args) == _normalized_list(server_spec.get("args"))
        and _normalized_mapping(server.env_vars) == _normalized_mapping(server_spec.get("env_vars"))
        and _normalized_mapping(server.headers) == _normalized_mapping(server_spec.get("headers"))
    )


async def _find_or_create_mcp_server(
    db: AsyncSession,
    *,
    server_spec: dict[str, Any],
    user_id: uuid.UUID,
    body: CreateAgentFromBlueprintIn,
    credential_bindings: dict[str, uuid.UUID],
) -> McpServer | None:
    name = str(server_spec.get("name") or "").strip()
    if not name:
        return None

    credential_id = _credential_id_for_requirement(
        credential_bindings=credential_bindings,
        key=_safe_key("mcp", name),
    )
    server: McpServer | None = None
    if body.dependency_strategy != "always_new":
        empty_credential_server: McpServer | None = None
        candidates = (
            (
                await db.execute(
                    select(McpServer).where(
                        McpServer.user_id == user_id,
                        McpServer.name == name,
                    )
                )
            )
            .scalars()
            .all()
        )
        for candidate in candidates:
            if not _mcp_server_matches_spec(candidate, server_spec):
                continue
            if credential_id is None or candidate.credential_id == credential_id:
                server = candidate
                break
            if (
                candidate.credential_id is None
                and body.dependency_strategy != "reuse_existing"
                and empty_credential_server is None
            ):
                empty_credential_server = candidate
        if server is None and empty_credential_server is not None:
            server = empty_credential_server
    if server is not None:
        if (
            credential_id is not None
            and server.credential_id is None
            and body.dependency_strategy != "reuse_existing"
        ):
            server.credential_id = credential_id
        return server
    if body.dependency_strategy == "reuse_existing":
        return server

    server = McpServer(
        id=uuid.uuid4(),
        user_id=user_id,
        name=name,
        description=server_spec.get("description"),
        transport=str(server_spec.get("transport") or "streamable_http"),
        url=server_spec.get("url"),
        command=server_spec.get("command"),
        args=list(server_spec.get("args") or []),
        env_vars=dict(server_spec.get("env_vars") or {}),
        headers=dict(server_spec.get("headers") or {}),
        credential_id=credential_id,
        status="unknown",
        is_system=False,
    )
    db.add(server)
    await db.flush()
    return server


async def _resolve_mcp_tool_ids(
    db: AsyncSession,
    *,
    capabilities: dict[str, Any],
    user_id: uuid.UUID,
    body: CreateAgentFromBlueprintIn,
    credential_bindings: dict[str, uuid.UUID],
) -> list[uuid.UUID]:
    tool_ids: list[uuid.UUID] = []
    rows = capabilities.get("mcp_tools") if isinstance(capabilities.get("mcp_tools"), list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        server_spec = row.get("server") if isinstance(row.get("server"), dict) else {}
        if not name or not isinstance(server_spec, dict):
            continue
        server = await _find_or_create_mcp_server(
            db,
            server_spec=server_spec,
            user_id=user_id,
            body=body,
            credential_bindings=credential_bindings,
        )
        if server is None:
            raise marketplace_invalid_package(f"missing MCP server dependency: {name}")
        tool = (
            await db.execute(
                select(McpTool).where(McpTool.server_id == server.id, McpTool.name == name).limit(1)
            )
        ).scalar_one_or_none()
        if tool is None:
            tool = McpTool(
                id=uuid.uuid4(),
                server_id=server.id,
                name=name,
                description=row.get("description"),
                input_schema=row.get("input_schema") or {},
                enabled=True,
            )
            db.add(tool)
            await db.flush()
        tool_ids.append(tool.id)
    return tool_ids


async def _resolve_sub_agent_ids(
    db: AsyncSession,
    *,
    capabilities: dict[str, Any],
    user_id: uuid.UUID,
) -> list[uuid.UUID]:
    """Resolve subagent dependencies by **name** against the user's agents.

    MVP limitation: name-based matching can silently link the wrong agent
    when the user owns several agents sharing a name (we pick the most
    recently updated one). Design D4 defines blueprint-reference based
    resolution as the long-term direction — replace this matching when
    blueprint references land instead of extending it.
    """

    rows = capabilities.get("subagents") if isinstance(capabilities.get("subagents"), list) else []
    sorted_rows = sorted(
        rows,
        key=lambda row: int(row.get("position") or 0) if isinstance(row, dict) else 0,
    )
    names: list[str] = []
    for row in sorted_rows:
        if not isinstance(row, dict):
            raise marketplace_invalid_package("subagent dependency is malformed")
        name = str(row.get("name") or "").strip()
        if not name:
            raise marketplace_invalid_package("subagent dependency is missing a name")
        names.append(name)
    if not names:
        return []

    # Single IN query (anti-N+1); first row per name is the most recent.
    agents = (
        (
            await db.execute(
                select(Agent)
                .where(
                    Agent.user_id == user_id,
                    Agent.name.in_(set(names)),
                    # 히든 런타임 에이전트("스킬 빌더" 등)가 이름 충돌로 일반
                    # 에이전트의 서브에이전트로 조용히 결선되는 것을 차단.
                    Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD,
                )
                .order_by(Agent.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    by_name: dict[str, Agent] = {}
    for agent in agents:
        by_name.setdefault(agent.name, agent)

    sub_agent_ids: list[uuid.UUID] = []
    for name in names:
        sub_agent = by_name.get(name)
        if sub_agent is None:
            raise marketplace_invalid_package(f"missing subagent dependency: {name}")
        sub_agent_ids.append(sub_agent.id)
    return sub_agent_ids


async def _materialize_dependency_ids(
    db: AsyncSession,
    *,
    spec: dict[str, Any],
    user_id: uuid.UUID,
    body: CreateAgentFromBlueprintIn,
    credential_bindings: dict[str, uuid.UUID],
) -> tuple[list[uuid.UUID], list[uuid.UUID], list[uuid.UUID], list[uuid.UUID]]:
    capabilities = _capabilities(spec)
    tool_ids = await _resolve_tool_ids(
        db,
        capabilities=capabilities,
        user_id=user_id,
        body=body,
        credential_bindings=credential_bindings,
    )
    skill_ids = await _resolve_skill_ids(db, capabilities=capabilities, user_id=user_id)
    mcp_tool_ids = await _resolve_mcp_tool_ids(
        db,
        capabilities=capabilities,
        user_id=user_id,
        body=body,
        credential_bindings=credential_bindings,
    )
    sub_agent_ids = await _resolve_sub_agent_ids(
        db,
        capabilities=capabilities,
        user_id=user_id,
    )
    return tool_ids, skill_ids, mcp_tool_ids, sub_agent_ids


async def create_agent_from_blueprint(
    db: AsyncSession,
    *,
    blueprint_id: uuid.UUID,
    user_id: uuid.UUID,
    body: CreateAgentFromBlueprintIn,
) -> Agent:
    blueprint = await db.get(AgentBlueprint, blueprint_id)
    # ``install_status == "uninstalled"`` blueprints are hidden from the
    # list/detail endpoints — keep create-agent consistent (same 404) so the
    # gallery and this action agree and no enumeration oracle opens up.
    if (
        blueprint is None
        or blueprint.user_id != user_id
        or blueprint.install_status == "uninstalled"
    ):
        raise marketplace_item_not_found()

    spec = blueprint.spec or {}
    credential_bindings = await _validate_credential_bindings(
        db,
        spec=spec,
        bindings=_merged_credential_bindings(blueprint=blueprint, body=body),
        user_id=user_id,
    )
    agent_spec = spec.get("agent") if isinstance(spec.get("agent"), dict) else {}
    model_id = await _resolve_model_id(
        db,
        spec=spec,
        requested_model_id=body.model_id,
    )
    model_fallback_ids = await _resolve_model_fallback_ids(
        db,
        spec=spec,
        requested_model_fallback_ids=body.model_fallback_ids,
    )
    tool_ids, skill_ids, mcp_tool_ids, sub_agent_ids = await _materialize_dependency_ids(
        db,
        spec=spec,
        user_id=user_id,
        body=body,
        credential_bindings=credential_bindings,
    )
    data = AgentCreate(
        name=body.name or agent_spec.get("name") or blueprint.name,
        description=agent_spec.get("description") or blueprint.description,
        system_prompt=agent_spec.get("system_prompt") or "",
        model_id=model_id,
        model_params=agent_spec.get("model_params"),
        middleware_configs=_validated_middleware_configs(agent_spec),
        opener_questions=agent_spec.get("opener_questions"),
        model_fallback_ids=model_fallback_ids,
        identity_mode=agent_spec.get("identity_mode") or "per_user",
        tool_ids=tool_ids,
        skill_ids=skill_ids,
        mcp_tool_ids=mcp_tool_ids,
        sub_agent_ids=sub_agent_ids,
    )
    agent = await agent_service.create_agent(db, data, user_id)
    if credential_bindings.get("llm") is not None:
        agent.llm_credential_id = credential_bindings["llm"]
    blueprint.created_agent_count += 1
    await db.flush()
    return agent


__all__ = ["create_agent_from_blueprint"]
