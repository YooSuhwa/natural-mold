"""Materialization service for installed Agent Blueprints."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.middleware_registry import MIDDLEWARE_REGISTRY
from app.agent_runtime.runtime_policy import RuntimePolicyV1
from app.authz.company_catalog import model_scope
from app.authz.org_context import OrgContext
from app.authz.private_actor import PrivateResourceActor, resolve_private_actor
from app.authz.scoping import private_scoped
from app.error_codes import (
    marketplace_credential_required,
    marketplace_invalid_package,
    model_not_found,
)
from app.marketplace.runtime_policy import parse_portable_runtime_policy
from app.marketplace.schemas import CreateAgentFromBlueprintIn
from app.models.agent import Agent
from app.models.credential import Credential
from app.models.model import Model
from app.schemas.agent import AgentCreate, MiddlewareConfigEntry
from app.services import agent_service
from app.services.agent_blueprint_graph import apply_graph, plan_graph, resolve_subagents
from app.services.agent_blueprint_graph_source import BlueprintSource, read_blueprint
from app.services.agent_blueprint_graph_types import GraphRequest, JsonObject, mapping


async def _resolve_model_id(
    db: AsyncSession,
    *,
    spec: dict[str, Any],
    requested_model_id: uuid.UUID | None,
    tenant_id: uuid.UUID | None = None,  # caller company: platform + its models (E-07)
) -> uuid.UUID:
    if requested_model_id is not None:
        # The override is caller-supplied input. A missing OR operator-hidden
        # (``is_visible=False``) model is rejected uniformly with
        # ``marketplace_invalid_package`` (422) so the response can't be used
        # as an enumeration oracle (existence vs visibility vs other publish
        # failures all 4xx the same way) and a hidden model can't be forced.
        model_id = await db.scalar(
            select(Model.id).where(
                Model.id == requested_model_id,
                Model.is_visible.is_(True),
                model_scope(tenant_id),
            )
        )
        if model_id is None:
            raise marketplace_invalid_package("requested model is not available")
        return model_id

    raw_agent_spec = spec.get("agent")
    agent_spec = raw_agent_spec if isinstance(raw_agent_spec, dict) else {}
    raw_model_spec = agent_spec.get("model")
    model_spec = raw_model_spec if isinstance(raw_model_spec, dict) else {}
    model_id = await _resolve_model_descriptor(db, model_spec=model_spec, tenant_id=tenant_id)
    if model_id is not None:
        return model_id

    raise model_not_found()


async def _resolve_model_descriptor(
    db: AsyncSession,
    *,
    model_spec: dict[str, Any],
    tenant_id: uuid.UUID | None = None,
) -> uuid.UUID | None:
    preferred_id = model_spec.get("preferred_model_id")
    if preferred_id:
        try:
            model_id = await db.scalar(
                select(Model.id).where(
                    Model.id == uuid.UUID(str(preferred_id)),
                    Model.is_visible.is_(True),
                    model_scope(tenant_id),
                )
            )
        except ValueError:
            model_id = None
        # ``preferred_model_id`` rides inside the publisher-controlled spec,
        # so it must not silently pull an operator-hidden model into a
        # materialized agent. Only honor a visible model; otherwise fall
        # through to provider/model_name matching below.
        if model_id is not None:
            return model_id

    provider = model_spec.get("provider")
    model_name = model_spec.get("model_name")
    base_url = model_spec.get("base_url")
    if provider and model_name:
        stmt = select(Model.id).where(
            Model.provider == str(provider),
            Model.model_name == str(model_name),
            Model.is_visible.is_(True),
            model_scope(tenant_id),
        )
        if base_url is None:
            stmt = stmt.where(Model.base_url.is_(None))
        else:
            stmt = stmt.where(Model.base_url == str(base_url))
        model_id = await db.scalar(stmt.limit(1))
        if model_id is not None:
            return model_id

    return None


async def _resolve_model_fallback_ids(
    db: AsyncSession,
    *,
    spec: dict[str, Any],
    requested_model_fallback_ids: list[uuid.UUID] | None,
    tenant_id: uuid.UUID | None = None,
) -> list[uuid.UUID] | None:
    if requested_model_fallback_ids is not None:
        return requested_model_fallback_ids

    raw_agent_spec = spec.get("agent")
    agent_spec = raw_agent_spec if isinstance(raw_agent_spec, dict) else {}
    raw_rows = agent_spec.get("model_fallbacks")
    rows = raw_rows if isinstance(raw_rows, list) else []
    fallback_ids: list[uuid.UUID] = []
    for row in rows:
        if not isinstance(row, dict):
            raise marketplace_invalid_package("model fallback is malformed")
        model_id = await _resolve_model_descriptor(db, model_spec=row, tenant_id=tenant_id)
        if model_id is None:
            raise model_not_found()
        if model_id not in fallback_ids:
            fallback_ids.append(model_id)
    return fallback_ids or None


def _validated_middleware_configs(agent_spec: dict[str, Any]) -> list[MiddlewareConfigEntry]:
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
    validated: list[MiddlewareConfigEntry] = []
    unknown: list[str] = []
    for row in configs:
        if not isinstance(row, dict):
            raise marketplace_invalid_package("middleware config is malformed")
        key = str(row.get("type") or "")
        if key not in MIDDLEWARE_REGISTRY:
            unknown.append(key or "<missing type>")
            continue
        validated.append(MiddlewareConfigEntry.model_validate(row))
    if unknown:
        raise marketplace_invalid_package(
            f"unknown middleware keys: {', '.join(sorted(set(unknown)))}"
        )
    return validated


def _validated_runtime_policy(agent_spec: dict[str, Any]) -> RuntimePolicyV1 | None:
    """Parse the optional portable runtime policy from a blueprint spec."""
    if "runtime_policy" not in agent_spec or agent_spec["runtime_policy"] is None:
        return None
    return parse_portable_runtime_policy(agent_spec["runtime_policy"])


def _credential_requirements(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_setup = spec.get("setup")
    setup = raw_setup if isinstance(raw_setup, dict) else {}
    raw_rows = setup.get("required_credentials")
    rows = raw_rows if isinstance(raw_rows, list) else []
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
    blueprint: BlueprintSource,
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
    context: OrgContext,
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
        credential = (
            await db.execute(
                private_scoped(
                    select(Credential.id, Credential.definition_key).where(
                        Credential.id == credential_id,
                        Credential.status == "active",
                        Credential.is_system.is_(False),
                    ),
                    Credential,
                    context,
                )
            )
        ).one_or_none()
        if credential is None:
            raise marketplace_credential_required(f"invalid credential binding: {key}")
        expected_definition = requirement.get("definition_key")
        credential_id, definition_key = credential
        if expected_definition and definition_key != expected_definition:
            raise marketplace_credential_required(f"credential definition mismatch: {key}")
        validated[key] = credential_id
    return validated


async def _resolve_sub_agent_ids(
    db: AsyncSession,
    *,
    capabilities: JsonObject,
    context: OrgContext,
) -> list[uuid.UUID]:
    return await resolve_subagents(db, capabilities, context)


async def create_agent_from_blueprint(
    db: AsyncSession,
    *,
    blueprint_id: uuid.UUID,
    context: OrgContext,
    body: CreateAgentFromBlueprintIn,
) -> Agent:
    # Decision phase: refresh the actor before any private read, and suppress
    # autoflush until the complete dependency graph and Agent payload are valid.
    with db.no_autoflush:
        actor_context = await resolve_private_actor(
            db, PrivateResourceActor(context.user_id, context.org_id, context.tenant_id)
        )
        source = await read_blueprint(db, blueprint_id, actor_context)
        blueprint = source.row
        spec = source.spec
        credential_bindings = await _validate_credential_bindings(
            db,
            spec=spec,
            bindings=_merged_credential_bindings(blueprint=source, body=body),
            context=actor_context,
        )
        agent_spec = mapping(spec.get("agent"))
        tenant_id = actor_context.tenant_id
        model_id = await _resolve_model_id(
            db, spec=spec, requested_model_id=body.model_id, tenant_id=tenant_id
        )
        model_fallback_ids = await _resolve_model_fallback_ids(
            db,
            spec=spec,
            requested_model_fallback_ids=body.model_fallback_ids,
            tenant_id=tenant_id,
        )
        if model_fallback_ids:
            valid_fallbacks = set(
                await db.scalars(
                    select(Model.id).where(Model.id.in_(model_fallback_ids), model_scope(tenant_id))
                )
            )
            invalid = [
                str(model_id) for model_id in model_fallback_ids if model_id not in valid_fallbacks
            ]
            if invalid:
                raise HTTPException(
                    status_code=400, detail=f"Unknown model_fallback_ids: {invalid}"
                )
        capabilities = mapping(spec.get("capabilities"))
        graph = await plan_graph(
            db, GraphRequest(capabilities, actor_context, body, credential_bindings)
        )
        data = AgentCreate.model_validate(
            {
                "name": body.name or agent_spec.get("name") or source.name,
                "description": agent_spec.get("description") or source.description,
                "system_prompt": agent_spec.get("system_prompt") or "",
                "model_id": model_id,
                "model_params": agent_spec.get("model_params"),
                "middleware_configs": _validated_middleware_configs(agent_spec),
                "opener_questions": agent_spec.get("opener_questions"),
                "model_fallback_ids": model_fallback_ids,
                "identity_mode": agent_spec.get("identity_mode") or "per_user",
                "runtime_policy": _validated_runtime_policy(agent_spec),
                "tool_ids": list(graph.tools.ids),
                "skill_ids": list(graph.skill_ids),
                "mcp_tool_ids": list(graph.mcp.ids),
                "sub_agent_ids": list(graph.sub_agent_ids),
            }
        )
    await apply_graph(db, graph)
    agent = await agent_service.create_agent(db, data, actor_context)
    if credential_bindings.get("llm") is not None:
        agent.llm_credential_id = credential_bindings["llm"]
    blueprint.created_agent_count = source.created_agent_count + 1
    await db.flush()
    return agent


__all__ = ["create_agent_from_blueprint"]
