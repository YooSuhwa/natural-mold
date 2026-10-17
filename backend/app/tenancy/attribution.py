"""Who ran, for whom, on whose account, and who pays (plan 11.10, Q8, Q22; F-14).

One accepted run records, separately (11.6 "따로 기록"):

* ``caller_user_id`` — the requester and the billed user (Q8): the chat caller (owner or
  a shared-agent runner), the trigger owner for schedules, the key owner for Agent API.
  ``conversation_runs.user_id`` already holds it; ``token_usages.caller_user_id`` copies it.
* ``actor_agent_id`` — the agent that ran (``agent_id`` on runs/usage, Q22 ``agent:A``).
* ``resource_owner_user_id`` — that agent's owner.
* ``on_behalf_of_user_id`` — Q22: the caller for ``per_user`` agents, the owner for ``fixed``.
* ``credential_subject_user_id`` — whose connections the run uses (the "계정 주인", 11.1).
* ``run_source`` (11.1 principal vocabulary) and ``invoker_type`` (Q22 audit vocabulary).

Cost goes to the organization and the caller (and the agent axis); subagent model calls
are part of the root run's usage, so they bill the root caller (Q8). Each subagent's own
identity is kept on its ``conversation_run_agents`` participant row.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import InstanceState

from app.authz.org_context import OrgContext

if TYPE_CHECKING:
    from app.models.agent import Agent

InvokerType = Literal["user", "schedule", "api", "subagent"]
ExecutionSource = Literal["chat", "trigger", "api"]

_SOURCES: dict[str, tuple[str, InvokerType]] = {
    "chat": ("CHAT", "user"),
    "trigger": ("TRIGGER", "schedule"),
    "api": ("API", "api"),
}
PER_USER = "per_user"
_USAGE_COPIED = (
    "resource_owner_user_id",
    "on_behalf_of_user_id",
    "credential_subject_user_id",
    "run_source",
    "invoker_type",
)


@dataclass(frozen=True, slots=True)
class RunAttribution:
    org_id: uuid.UUID
    tenant_id: uuid.UUID
    run_source: str
    invoker_type: InvokerType
    caller_user_id: uuid.UUID
    actor_agent_id: uuid.UUID
    resource_owner_user_id: uuid.UUID
    on_behalf_of_user_id: uuid.UUID
    credential_subject_user_id: uuid.UUID

    def run_columns(self) -> dict[str, object]:
        """``conversation_runs`` columns (``user_id``/``agent_id`` are set by the caller)."""
        return {
            "org_id": self.org_id,
            "tenant_id": self.tenant_id,
            "run_source": self.run_source,
            "invoker_type": self.invoker_type,
            "resource_owner_user_id": self.resource_owner_user_id,
            "on_behalf_of_user_id": self.on_behalf_of_user_id,
            "credential_subject_user_id": self.credential_subject_user_id,
        }

    def participant_columns(self) -> dict[str, object]:
        """``conversation_run_agents`` identity columns for this agent."""
        return {
            "invoker_type": self.invoker_type,
            "resource_owner_user_id": self.resource_owner_user_id,
            "on_behalf_of_user_id": self.on_behalf_of_user_id,
            "credential_subject_user_id": self.credential_subject_user_id,
        }


def _acting_user(agent: Agent, caller: uuid.UUID | None) -> uuid.UUID:
    """``per_user`` acts as the human caller; ``fixed`` (and callerless runs) as the owner."""
    return caller if agent.identity_mode == PER_USER and caller is not None else agent.user_id


def attribute_run(source: ExecutionSource, context: OrgContext, agent: Agent) -> RunAttribution:
    """The root run. ``context.user_id`` is the requester (caller, trigger or key owner)."""
    run_source, invoker = _SOURCES[source]
    # Triggers and API keys run as the agent (fixed); only a chat caller is a human caller.
    human = context.user_id if source == "chat" else None
    acting = _acting_user(agent, human)
    return RunAttribution(
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        run_source=run_source,
        invoker_type=invoker,
        caller_user_id=context.user_id,
        actor_agent_id=agent.id,
        resource_owner_user_id=agent.user_id,
        on_behalf_of_user_id=acting,
        credential_subject_user_id=acting,
    )


def attribute_child(parent: RunAttribution, child: Agent) -> RunAttribution:
    """A subagent: same caller and bill, its own owner and acting user (identity rules)."""
    human = parent.caller_user_id if parent.invoker_type == "user" else None
    acting = _acting_user(child, human)
    return RunAttribution(
        org_id=parent.org_id,
        tenant_id=parent.tenant_id,
        run_source="SUBAGENT",
        invoker_type="subagent",
        caller_user_id=parent.caller_user_id,
        actor_agent_id=child.id,
        resource_owner_user_id=child.user_id,
        on_behalf_of_user_id=acting,
        credential_subject_user_id=acting,
    )


def loaded(run: object, name: str) -> Any:
    """An attribute already loaded on the run row; never a lazy load (``load_only`` rows
    used by recovery/projection paths raise on unloaded columns)."""
    state = cast(InstanceState[Any], sa_inspect(run))
    return state.dict.get(name)


class AttributionNotLoadedError(RuntimeError):
    """A usage write got a run row without its requester/organization loaded."""


def usage_columns(run: object) -> dict[str, object]:
    """``token_usages`` attribution copied from its run row (written at acceptance).

    The requester and organization must be loaded: a ``load_only`` row without them
    would silently book unattributed usage, so this raises instead.
    """
    state = cast(InstanceState[Any], sa_inspect(run))
    missing = [name for name in ("user_id", "org_id") if name not in state.dict]
    if missing:
        raise AttributionNotLoadedError(f"run attribution not loaded: {', '.join(missing)}")
    names = ("org_id", "tenant_id", *_USAGE_COPIED)
    return {"caller_user_id": loaded(run, "user_id"), **{n: loaded(run, n) for n in names}}


def run_attribution_metadata(run: object) -> dict[str, str]:
    """Q22 audit split read back from an attributed run row (empty for older rows)."""
    if loaded(run, "invoker_type") is None:
        return {}
    values = {"actor_agent_id": loaded(run, "agent_id")}
    values.update({name: loaded(run, name) for name in _USAGE_COPIED})
    return {key: str(value) for key, value in values.items() if value is not None}


__all__ = [
    "AttributionNotLoadedError",
    "ExecutionSource",
    "InvokerType",
    "RunAttribution",
    "attribute_child",
    "attribute_run",
    "loaded",
    "run_attribution_metadata",
    "usage_columns",
]
