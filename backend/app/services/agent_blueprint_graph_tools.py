"""Read-only Tool matching with explicit same-request candidate simulation."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.scoping import resource_candidates
from app.error_codes import marketplace_invalid_package
from app.models.tool import Tool
from app.services.agent_blueprint_graph_types import (
    GraphRequest,
    ToolDraft,
    ToolPlan,
    credential_key,
    dependency_mapping,
    mapping,
    optional_text,
    sequence,
)
from app.tools.registry import registry as tool_registry


async def plan_tools(db: AsyncSession, request: GraphRequest) -> ToolPlan:
    rows = sequence(request.capabilities.get("tools"))
    keys: set[str] = set()
    for raw in rows:
        row = dependency_mapping(raw, "tool")
        key = str(row.get("definition_key") or "")
        if not key:
            raise marketplace_invalid_package("tool dependency is missing definition_key")
        if not key.startswith("builtin:") and tool_registry.get(key) is None:
            raise marketplace_invalid_package(f"unknown tool definition_key: {key}")
        keys.add(key)
    existing = (
        (
            await db.execute(
                resource_candidates(
                    select(
                        Tool.id,
                        Tool.user_id,
                        Tool.definition_key,
                        Tool.name,
                        Tool.description,
                        Tool.parameters,
                        Tool.credential_id,
                        Tool.enabled,
                    )
                    .where(Tool.definition_key.in_(keys), Tool.enabled.is_(True))
                    .order_by(Tool.user_id.is_not(None).desc(), Tool.name.asc()),
                    Tool,
                    request.context,
                )
            )
        ).all()
        if keys
        else []
    )
    pool = [
        ToolDraft(
            tool_id,
            user_id,
            key,
            name,
            description,
            parameters or {},
            credential_id,
            enabled,
        )
        for tool_id, user_id, key, name, description, parameters, credential_id, enabled in existing
    ]
    ids = []
    creations: list[ToolDraft] = []
    for raw in rows:
        row = mapping(raw)
        key = str(row.get("definition_key"))
        name = str(row.get("name") or key)
        parameters = mapping(row.get("parameters"))
        credential_id = request.bindings.get(credential_key("tool", key))
        scoped = [t for t in pool if t.definition_key == key and t.parameters == parameters]
        selected = None
        if credential_id is not None:
            selected = next(
                (
                    t
                    for t in scoped
                    if t.user_id == request.context.user_id
                    and t.name == name
                    and t.credential_id == credential_id
                ),
                None,
            )
        else:
            if row.get("name"):
                selected = next((t for t in scoped if t.name == str(row["name"])), None)
            if selected is None:
                selected = next(iter(scoped), None)
        if selected is not None:
            ids.append(selected.id)
            continue
        if request.body.dependency_strategy == "reuse_existing" or key.startswith("builtin:"):
            raise marketplace_invalid_package(f"missing tool dependency: {name}")
        draft = ToolDraft(
            uuid4(),
            request.context.user_id,
            key,
            name,
            optional_text(row.get("description")),
            parameters,
            credential_id,
            True if credential_id is not None else bool(row.get("enabled", True)),
        )
        creations.append(draft)
        ids.append(draft.id)
        if draft.enabled:
            pool.append(draft)
    return ToolPlan(tuple(ids), tuple(creations))
