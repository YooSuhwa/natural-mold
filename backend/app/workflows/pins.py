"""Agent settings fingerprints for workflow publishing (workflow-plan Q1).

Publishing records, for each agent node, a hash of everything that changes how that agent
runs: its run-relevant columns and the rows of its settings tables (tools, skills with
their content hash, MCP tools, subagents, guardrail binding, memory settings). A run
compares the live hash with the pinned one and refuses an agent that changed, so a
workflow never runs on settings nobody published.

Display-only columns and bookkeeping (names, timestamps, who delegated, link health) are
left out. Every table that cascades from ``agents`` must be classified in
``PINNED_TABLES`` or ``UNPINNED_TABLES``; a test fails when a new one is neither, so a
future settings table (knowledge folders, approval modes) cannot be silently skipped.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable, Mapping
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Base

AGENT_COLUMNS: Final = (
    "system_prompt",
    "identity_mode",
    "model_id",
    "llm_credential_id",
    "model_params",
    "middleware_configs",
    "runtime_policy",
    "model_fallback_list",
    "runtime_profile",
)
# table -> (agent key column, columns left out of the hash)
PINNED_TABLES: Final[dict[str, tuple[str, frozenset[str]]]] = {
    "agent_tools": ("agent_id", frozenset({"delegated_by", "broken_reason", "link_status"})),
    "agent_skills": ("agent_id", frozenset({"delegated_by", "broken_reason", "link_status"})),
    "agent_mcp_tools": ("agent_id", frozenset({"delegated_by", "broken_reason", "link_status"})),
    "agent_subagents": (
        "parent_agent_id",
        frozenset({"delegated_by", "broken_reason", "link_status", "created_at"}),
    ),
    "agent_guardrail_bindings": ("agent_id", frozenset({"updated_by", "updated_at"})),
    "agent_memory_settings": ("agent_id", frozenset({"created_at", "updated_at"})),
}
# Rows that hang off an agent but do not change how one run of it behaves.
UNPINNED_TABLES: Final = frozenset(
    {
        "agent_deployments",
        "agent_triggers",
        "agent_user_preferences",
        # Chat-only tools: a workflow step never gets them (workflow-tool-plan T4).
        "agent_workflows",
        "daily_spend_agent",
        "marketplace_installations",
        "marketplace_publication_links",
        "memory_consolidation_runs",
        "memory_profile_docs",
        "memory_proposals",
        "memory_records",
    }
)


def _canonical(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda i: str(i[0]))}
    if isinstance(value, list | tuple):
        return [_canonical(v) for v in value]
    if isinstance(value, int | float | str | bool) or value is None:
        return value
    return str(value)


def _rows(rows: Iterable[Mapping[str, Any]], leave_out: frozenset[str]) -> list[dict[str, Any]]:
    cleaned = [
        {key: _canonical(value) for key, value in row.items() if key not in leave_out}
        for row in rows
    ]
    return sorted(cleaned, key=lambda item: json.dumps(item, sort_keys=True))


async def agent_fingerprints(
    db: AsyncSession, agent_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """``{agent_id: sha256}`` for the agents that exist; missing ids are left out."""
    ids = sorted(set(agent_ids))
    if not ids:
        return {}
    agents = Base.metadata.tables["agents"]
    columns = [agents.c.id, *(agents.c[name] for name in AGENT_COLUMNS)]
    found = (await db.execute(sa.select(*columns).where(agents.c.id.in_(ids)))).all()
    parts: dict[uuid.UUID, dict[str, Any]] = {
        row.id: {"agent": {name: _canonical(getattr(row, name)) for name in AGENT_COLUMNS}}
        for row in found
    }
    for name, (key, leave_out) in PINNED_TABLES.items():
        table = Base.metadata.tables[name]
        rows = (await db.execute(sa.select(table).where(table.c[key].in_(parts)))).mappings()
        by_agent: dict[uuid.UUID, list[Mapping[str, Any]]] = {agent_id: [] for agent_id in parts}
        for row in rows.all():
            by_agent[row[key]].append(dict(row))
        for agent_id, agent_rows in by_agent.items():
            parts[agent_id][name] = _rows(agent_rows, leave_out | {key})
    await _skill_hashes(db, parts)
    return {
        agent_id: hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()
        ).hexdigest()
        for agent_id, payload in parts.items()
    }


async def _skill_hashes(db: AsyncSession, parts: dict[uuid.UUID, dict[str, Any]]) -> None:
    """A skill's content changes what the agent does even when its link row does not."""
    skill_ids = {
        uuid.UUID(link["skill_id"])
        for payload in parts.values()
        for link in payload.get("agent_skills", [])
    }
    if not skill_ids:
        return
    skills = Base.metadata.tables["skills"]
    rows = (
        await db.execute(
            sa.select(skills.c.id, skills.c.content_hash).where(skills.c.id.in_(skill_ids))
        )
    ).all()
    hashes = {str(row.id): row.content_hash for row in rows}
    for payload in parts.values():
        for link in payload.get("agent_skills", []):
            link["skill_content_hash"] = hashes.get(link["skill_id"])


__all__ = ["AGENT_COLUMNS", "PINNED_TABLES", "UNPINNED_TABLES", "agent_fingerprints"]
