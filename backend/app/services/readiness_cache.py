"""The readiness summary cache and its invalidation (plan 12.3; G-01).

``POST /api/agents/readiness-summary`` keeps each (organization, caller, agent) summary for
``TTL_SECONDS`` (60 s) in this process. Any committed change that can move a 12.1 result
clears the whole cache: grants and their outbox apply (``resource_grants``,
``authz_outbox`` — ``processed_at`` is set once the tuples landed), memberships, per-user
connections, credential status, MCP server/tool status, tool/model/skill/agent rows,
link status (broken links) and catalog policy (``organizations``/``tenants`` settings).
Detection is session-wide: ORM inserts/deletes of those classes, updates of the listed
columns, and bulk ``DELETE``/``INSERT`` or ``UPDATE`` of a listed column on their tables
mark the session; ``after_commit`` clears. Limits: other processes rely on the TTL; a
summary computed concurrently with a commit may be stored just after the clear and stay
up to the TTL; OpenFGA changes not written through the outbox (e.g. a manual tuple
repair) also wait for the TTL. The detail ``GET /readiness`` is never cached.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from sqlalchemy import event, inspect
from sqlalchemy.orm import ORMExecuteState, Session

from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.authz import AuthzOutbox, ResourceGrant
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.model import Model
from app.models.organization import Organization, OrganizationMember, Tenant
from app.models.organization_group import GroupMember
from app.models.resource_credential_binding import ResourceCredentialBinding
from app.models.skill import AgentSkillLink, Skill
from app.models.tool import AgentToolLink, Tool

TTL_SECONDS = 60.0
MAX_ENTRIES = 10_000
_FLAG = "readiness.invalidate"
_LINK = frozenset({"required", "link_status"})
_CREDENTIALED = frozenset(
    {"credential_id", "credential_mode", "credential_definition_key", "user_id"}
)
# Class -> the columns whose UPDATE can move a 12.1 result (``None``: any column). Inserts
# and deletes of these classes always count; e.g. ``health_status``, ``last_pinged_at``,
# ``last_seen_at``, ``last_used_at`` or ``updated_at`` alone never flush the cache.
WATCHED: dict[type, frozenset[str] | None] = {
    Agent: frozenset(
        {"identity_mode", "model_id", "llm_credential_id", "archived_at", "user_id", "org_id"}
    ),
    AgentToolLink: _LINK,
    AgentMcpToolLink: _LINK,
    AgentSkillLink: _LINK,
    AgentSubAgentLink: _LINK,
    AuthzOutbox: frozenset({"processed_at"}),  # tuples landed in OpenFGA
    Credential: frozenset({"status", "user_id", "org_id", "definition_key", "is_system"}),
    GroupMember: None,
    McpServer: _CREDENTIALED | {"status", "url"},
    McpTool: frozenset({"enabled", "server_id"}),
    Model: frozenset({"is_visible", "default_credential_id"}),
    Organization: frozenset({"status", "settings"}),  # settings: catalog policy
    OrganizationMember: None,
    ResourceCredentialBinding: None,
    ResourceGrant: None,
    Skill: frozenset({"user_id", "org_id"}),
    Tenant: frozenset({"status", "settings"}),
    Tool: _CREDENTIALED | {"enabled"},
}
_TABLES = {getattr(cls, "__tablename__", ""): cols for cls, cols in WATCHED.items()}
Key = tuple[UUID | None, UUID | None, UUID, UUID]
HIDDEN: dict[str, Any] = {}  # cached "not runnable for this caller" (left out of the body)
_entries: dict[Key, tuple[float, dict[str, Any]]] = {}


def get(key: Key) -> dict[str, Any] | None:
    hit = _entries.get(key)
    if hit is None or hit[0] <= time.monotonic():
        _entries.pop(key, None)
        return None
    return hit[1]  # may be ``HIDDEN`` (identity-compared by callers)


def put(key: Key, value: dict[str, Any]) -> None:
    if len(_entries) >= MAX_ENTRIES:
        _entries.clear()
    _entries[key] = (time.monotonic() + TTL_SECONDS, value)


def invalidate_all() -> None:
    _entries.clear()


def _changed(row: object) -> bool:
    columns = WATCHED.get(type(row), frozenset())
    if columns is None:
        return True
    state = inspect(row)
    if state is None:
        return True
    return any(state.attrs[name].history.has_changes() for name in columns)


@event.listens_for(Session, "before_flush")
def _mark_flush(session: Session, _context: object, _instances: object) -> None:
    created = (*session.new, *session.deleted)
    if any(type(row) in WATCHED for row in created) or any(
        type(row) in WATCHED and _changed(row) for row in session.dirty
    ):
        session.info[_FLAG] = True


@event.listens_for(Session, "do_orm_execute")
def _mark_statement(state: ORMExecuteState) -> None:
    if state.is_select:
        return
    table = getattr(state.statement, "table", None)
    name = getattr(table, "name", None)
    if name not in _TABLES:
        return
    columns = _TABLES[name]
    values = getattr(state.statement, "_values", None) if state.is_update else None
    if columns is None or not state.is_update or not values:
        state.session.info[_FLAG] = True  # insert/delete, or values not inspectable
        return
    names = {getattr(key, "key", None) or getattr(key, "name", str(key)) for key in values}
    if names & columns:
        state.session.info[_FLAG] = True


@event.listens_for(Session, "after_commit")
def _clear_on_commit(session: Session) -> None:
    if session.info.pop(_FLAG, False):
        invalidate_all()


__all__ = ["HIDDEN", "TTL_SECONDS", "WATCHED", "get", "invalidate_all", "put"]
