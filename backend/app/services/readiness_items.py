"""Per-link 12.1 results for the readiness check (plan 12.1; G-01).

Each link of one agent becomes ``ReadinessItem``s, checked in this order:

* ``no_permission``: the planner denied the link for the 11.1 subject, or it is broken.
* ``unavailable``: tool or MCP tool disabled, MCP server ``unreachable``/``disabled``,
  skill or subagent row gone/archived, model missing or not visible.
* credentials (no decryption): ``shared`` resources use the owner's credential,
  ``per_user`` ones the run account's binding (F-05); a missing, ``auth_needed`` or
  ``expired`` one is ``connect_by_me`` when it is the caller's to connect, else
  ``owner_must_connect``; an inactive system credential (operator-managed) is
  ``unavailable``. A per-user binding the run could not use (F-05 checks) asks
  the caller to connect again.
* otherwise ``ready``.

MCP tool links are reported per server (one item per server and result).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select

from app.authz.credential_binding_scope import account_bindings, binding_of
from app.authz.readiness_verdicts import LinkVerdicts
from app.credentials.run_resolution import RunCredentialResolver
from app.models.agent import Agent
from app.models.credential import Credential
from app.models.resource_credential_binding import ResourceCredentialBinding as Binding

Result = Literal["ready", "no_permission", "connect_by_me", "owner_must_connect", "unavailable"]
BLOCKING = frozenset({"no_permission", "owner_must_connect", "unavailable"})
_DOWN_SERVER = frozenset({"unreachable", "disabled"})


@dataclass(slots=True)
class ReadinessItem:
    kind: str  # llm_model | tool | mcp_server | skill | sub_agent
    agent_id: UUID
    required: bool
    result: Result
    id: UUID | None = None
    name: str | None = None
    credential: dict[str, Any] | None = None
    action: dict[str, str] | None = field(default=None)


def action_for(result: str, definition_key: str | None) -> dict[str, str] | None:
    if result == "no_permission":
        return {"type": "request_access", "via": "agent_manager"}
    if result == "owner_must_connect":
        return {"type": "notify_owner"}
    if result == "connect_by_me":
        return {"type": "oauth_start" if "oauth" in (definition_key or "") else "connect"}
    return None


def operator_managed(row: Any, credential: Credential | None) -> bool:
    """A platform row's missing credential, or any system credential (``is_system``)."""
    if credential is not None:
        return bool(credential.is_system)
    return bool(row.is_system) and row.org_id is None and row.tenant_id is None


class LinkAssessor:
    def __init__(self, db: Any, authz: Any, agent: Agent, verdicts: LinkVerdicts) -> None:
        self.agent, self.verdicts = agent, verdicts
        self.caller = verdicts.subject.context.user_id
        self.db = db
        self.resolver = RunCredentialResolver(db, verdicts.subject, authz, decided=verdicts.decided)

    def item(self, kind: str, required: bool, result: Result, row: Any = None) -> ReadinessItem:
        name = getattr(row, "display_name", None) or getattr(row, "name", None)
        rid = getattr(row, "id", None)
        return ReadinessItem(kind, self.agent.id, required, result, rid, name)

    def finish(self, item: ReadinessItem, credential: dict[str, Any] | None) -> ReadinessItem:
        item.credential = credential if item.result != "ready" else None
        key = (credential or {}).get("definition_key")
        item.action = action_for(item.result, key)
        if item.credential is not None:
            item.credential = {k: v for k, v in item.credential.items() if k != "definition_key"}
        return item

    async def credential(self, row: Any, kind: str) -> tuple[Result, dict[str, Any] | None]:
        """The resource's credential state for the run account (no decryption)."""
        if row.credential_mode == "per_user":
            account = self.verdicts.subject.account_user_id
            credential = await self.db.scalar(
                select(Credential)
                .join(Binding, Binding.credential_id == Credential.id)
                .where(account_bindings(self.verdicts.subject.context, account))
                .where(binding_of(kind, row.id))
            )
            status = "missing" if credential is None else credential.status
            if status == "active" and await self.resolver.bound(row, kind) is None:
                status = "unusable"
        else:
            if row.credential_id is None:
                return "ready", None
            credential = row.credential
            account = self.verdicts.subject.account_user_id if row.is_system else row.user_id
            status = "missing" if credential is None else credential.status
        info = {
            "mode": row.credential_mode,
            "status": status,
            "definition_key": row.credential_definition_key
            or getattr(credential, "definition_key", None),
        }
        if credential is not None and credential.user_id == self.caller:
            info["credential_id"] = str(credential.id)
        if status == "active":
            return "ready", info
        if row.credential_mode != "per_user" and operator_managed(row, credential):
            return "unavailable", None  # an operator's system credential: admin check
        return ("connect_by_me" if account == self.caller else "owner_must_connect"), info

    async def model(self) -> ReadinessItem:
        model = self.agent.model
        item = self.item("llm_model", True, "ready", model)
        if model is None or not model.is_visible:
            item.result = "unavailable"
        elif self.verdicts.model_denied:
            item.result = "no_permission"
        info = None
        credential = self.agent.llm_credential
        if item.result == "ready" and credential is not None and credential.status != "active":
            if credential.is_system:
                item.result = "unavailable"
                return self.finish(item, None)
            mine = credential.user_id == self.caller
            item.result = "connect_by_me" if mine else "owner_must_connect"
            info = {"mode": "shared", "status": credential.status}
        return self.finish(item, info)

    async def tool(self, link: Any) -> ReadinessItem:
        tool = link.tool
        required = self.verdicts.required_of("tool", link.tool_id, link.required)
        item = self.item("tool", required, "ready", tool)
        item.id = item.id or link.tool_id
        info = None
        if self.verdicts.reason("tool", link.tool_id):
            item.result = "no_permission"
        elif tool is None or not tool.enabled:
            item.result = "unavailable"
        else:
            item.result, info = await self.credential(tool, "tool")
        return self.finish(item, info)

    def skill(self, link: Any) -> ReadinessItem:
        required = self.verdicts.required_of("skill", link.skill_id, link.required)
        item = self.item("skill", required, "ready", link.skill)
        item.id = item.id or link.skill_id
        if self.verdicts.reason("skill", link.skill_id):
            item.result = "no_permission"
        elif link.skill is None:
            item.result = "unavailable"
        return self.finish(item, None)

    def sub_agent(self, link: Any) -> ReadinessItem:
        child = link.sub_agent
        required = self.verdicts.required_of("agent", link.sub_agent_id, link.required)
        item = self.item("sub_agent", required, "ready", child)
        item.id = item.id or link.sub_agent_id
        if self.verdicts.reason("agent", link.sub_agent_id):
            item.result = "no_permission"
        elif child is None or child.archived_at is not None:
            item.result = "unavailable"
        return self.finish(item, None)

    async def mcp_servers(self) -> list[ReadinessItem]:
        """One item per (server, result); ``required`` when any of its links is."""
        grouped: dict[tuple[UUID, str], ReadinessItem] = {}
        states: dict[UUID, tuple[Result, dict[str, Any] | None]] = {}
        for link in self.agent.mcp_tool_links:
            mcp_tool = link.mcp_tool
            server = getattr(mcp_tool, "server", None)
            info = None
            if self.verdicts.reason("mcp_tool", link.mcp_tool_id):
                result: Result = "no_permission"
            elif server is None or not mcp_tool.enabled or server.status in _DOWN_SERVER:
                result = "unavailable"
            else:
                if server.id not in states:
                    states[server.id] = await self.credential(server, "mcp_server")
                result, info = states[server.id]
            required = self.verdicts.required_of("mcp_tool", link.mcp_tool_id, link.required)
            key = (server.id if server is not None else link.mcp_tool_id, result)
            if key in grouped:
                grouped[key].required = grouped[key].required or required
                continue
            item = self.item("mcp_server", required, result, server)
            item.id = key[0]
            grouped[key] = self.finish(item, info)
        return list(grouped.values())


__all__ = ["BLOCKING", "LinkAssessor", "ReadinessItem", "Result", "action_for"]
