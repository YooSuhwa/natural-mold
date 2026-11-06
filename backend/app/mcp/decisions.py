"""The record of each MCP tool call decision (plan 8.2 "판정 기록", 27; decision D10).

Every call an agent makes to an MCP tool, on the default and the prepared-lease path alike,
leaves one ``mcp.tool_call`` audit event: who it ran for (user, agent, conversation, run),
the tool and server, the reviewed definition it was shown (the policy version, D11), its
risk level, whether it went through the gateway, and the outcome:

* ``success``: the server ran it.
* ``failure``: the server or the connection failed (``reason_code`` names which).
* ``denied``: the gateway refused it for this run (``mcp_gateway_refused``) or the server
  refused the credential (``mcp_auth_required``).

Only argument names are kept, never their values. Writes are best effort in their own
company session: a record that cannot be written is logged and never fails the run.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from app.database import SessionTenant, tenant_session
from app.services import audit_service

logger = logging.getLogger(__name__)

ACTION: Final = "mcp.tool_call"
Outcome = Literal["success", "failure", "denied"]


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class DecisionContext:
    tenant_id: uuid.UUID
    org_id: uuid.UUID | None
    user_id: uuid.UUID | None
    agent_id: uuid.UUID | None
    conversation_id: str | None
    server_id: str | None
    tool_id: str | None
    tool_name: str
    reviewed_fingerprint: str | None
    risk_level: str | None
    gateway: bool

    @classmethod
    def from_config(
        cls, config: Mapping[str, Any], *, risk_level: str | None, gateway: bool
    ) -> DecisionContext | None:
        """``None`` for a config without a company (nothing to record under)."""
        tenant_id = _uuid(config.get("tenant_id"))
        if tenant_id is None:
            return None
        return cls(
            tenant_id=tenant_id,
            org_id=_uuid(config.get("org_id")),
            user_id=_uuid(config.get("user_id")),
            agent_id=_uuid(config.get("agent_id")),
            conversation_id=str(config["conversation_id"])
            if config.get("conversation_id")
            else None,
            server_id=str(config["mcp_server_id"]) if config.get("mcp_server_id") else None,
            tool_id=str(config["mcp_tool_id"]) if config.get("mcp_tool_id") else None,
            tool_name=str(config.get("mcp_tool_name") or config.get("name") or ""),
            reviewed_fingerprint=config.get("mcp_reviewed_fingerprint") or None,
            risk_level=risk_level,
            gateway=gateway,
        )


async def record(
    context: DecisionContext | None,
    *,
    outcome: Outcome,
    reason_code: str | None,
    arguments: Mapping[str, Any],
    attempts: int,
    run_id: str | None,
) -> None:
    if context is None:
        return
    try:
        async with tenant_session(SessionTenant(context.tenant_id, context.user_id)) as db:
            await audit_service.record_event(
                db,
                actor_type="agent" if context.agent_id is not None else "user",
                actor_user_id=context.user_id,
                action=ACTION,
                target_type="mcp_tool",
                target_id=context.tool_id,
                target_name_snapshot=context.tool_name,
                outcome=outcome,
                reason_code=reason_code,
                run_id=run_id,
                tenant_id=context.tenant_id,
                org_id=context.org_id,
                metadata={
                    "server_id": context.server_id,
                    "agent_id": str(context.agent_id) if context.agent_id else None,
                    "conversation_id": context.conversation_id,
                    "reviewed_fingerprint": context.reviewed_fingerprint,
                    "risk_level": context.risk_level,
                    "gateway": context.gateway,
                    "argument_names": sorted(str(name) for name in arguments),
                    "attempts": attempts,
                },
            )
            await db.commit()
    except Exception:  # noqa: BLE001 - records never fail the run
        logger.warning("MCP decision record not written for %s", context.tool_name, exc_info=True)


__all__ = ["ACTION", "DecisionContext", "Outcome", "record"]
