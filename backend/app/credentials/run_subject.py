"""Who a run acts as for credential resolution (plan 11.1, 11.3; F-05).

``account_user_id`` is whose connections a ``per_user`` resource uses: the caller for
caller-identity runs, the agent owner for fixed-identity runs (triggers and API keys
only allow fixed agents, so they always use the owner's connection). ``principal`` is
the 11.1 decision subject for resource ``can_use``: ``agent:A`` for fixed runs (its
delegations), ``user:caller`` for caller-identity runs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.agent_runtime.identity import AGENT_IDENTITY_PER_USER, AgentRunIdentity
from app.authz.org_context import OrgContext
from app.authz.principal import Principal, RunSource
from app.models.agent import Agent

_SOURCES: dict[str, RunSource] = {
    "chat": "CHAT",
    "trigger": "TRIGGER",
    "channel": "CHANNEL",
    "subagent": "SUBAGENT",
}


@dataclass(frozen=True, slots=True)
class RunSubject:
    context: OrgContext
    account_user_id: uuid.UUID
    principal: Principal
    # Read-only resolution (response masking): no decision, no error for a missing
    # connection, no OAuth refresh or row lock (11.3 table, ``chat/secrets.py``).
    read_only: bool = False

    @property
    def caller_identity(self) -> bool:
        return self.principal.kind == "user"


def subject_for_agent(
    agent: Agent,
    context: OrgContext,
    identity: AgentRunIdentity | None,
    *,
    read_only: bool = False,
) -> RunSubject:
    """No identity: a trigger-style run as the owner (fixed)."""
    if identity is None:
        source: RunSource = "TRIGGER"
        account, caller, mode = agent.user_id, None, "fixed"
    else:
        source = _SOURCES.get(str(identity.source), "CHAT")
        account, caller = identity.credential_subject_user_id, identity.caller_user_id
        mode = identity.identity_mode
    if mode == AGENT_IDENTITY_PER_USER and caller is not None:
        principal = Principal("user", caller, context.org_id, run_source=source)
    else:
        principal = Principal(
            "agent", agent.id, context.org_id, run_source=source, acting_user_id=caller
        )
    return RunSubject(context, account, principal, read_only)


def subject_for_caller(context: OrgContext, user_id: uuid.UUID | None = None) -> RunSubject:
    """A user's own direct use (tool test run, MCP Apps call): their connections."""
    account = user_id or context.user_id
    return RunSubject(context, account, Principal("user", account, context.org_id))


__all__ = ["RunSubject", "subject_for_agent", "subject_for_caller"]
