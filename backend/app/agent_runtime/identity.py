from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from app.exceptions import ValidationError

if TYPE_CHECKING:
    from app.models.agent import Agent

AGENT_IDENTITY_FIXED = "fixed"
AGENT_IDENTITY_PER_USER = "per_user"
AGENT_IDENTITY_MODES = {AGENT_IDENTITY_FIXED, AGENT_IDENTITY_PER_USER}
AGENT_RUNTIME_NAME_RE = re.compile(r"^agent_[a-f0-9]{8}$")


class AgentIdentityError(ValidationError):
    """Raised when an agent run cannot produce a valid execution identity."""

    def __init__(self, message: str) -> None:
        super().__init__("AGENT_IDENTITY_INVALID", message)


class AgentRunSource(StrEnum):
    CHAT = "chat"
    TRIGGER = "trigger"
    CHANNEL = "channel"
    SUBAGENT = "subagent"


@dataclass(frozen=True)
class AgentRunIdentity:
    agent_id: uuid.UUID
    agent_owner_user_id: uuid.UUID
    caller_user_id: uuid.UUID | None
    credential_subject_user_id: uuid.UUID
    identity_mode: str
    runtime_name: str
    source: AgentRunSource


def _coerce_uuid(value: uuid.UUID | str, *, field_name: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise AgentIdentityError(f"{field_name} must be a UUID") from exc


def make_agent_runtime_name(agent_id: uuid.UUID) -> str:
    return f"agent_{agent_id.hex[:8]}"


def validate_identity_mode(value: str) -> str:
    if value not in AGENT_IDENTITY_MODES:
        raise AgentIdentityError(f"unknown agent identity mode '{value}'")
    return value


def validate_runtime_name(value: str) -> str:
    if not AGENT_RUNTIME_NAME_RE.fullmatch(value):
        raise AgentIdentityError("agent runtime_name must match 'agent_<8 lowercase hex chars>'")
    return value


def resolve_agent_run_identity(
    *,
    agent_id: uuid.UUID,
    agent_owner_user_id: uuid.UUID,
    runtime_name: str,
    identity_mode: str,
    source: AgentRunSource,
    caller_user_id: uuid.UUID | None,
) -> AgentRunIdentity:
    validate_runtime_name(runtime_name)
    validate_identity_mode(identity_mode)

    if identity_mode == AGENT_IDENTITY_FIXED:
        subject = agent_owner_user_id
    elif source == AgentRunSource.CHAT and caller_user_id is not None:
        subject = caller_user_id
    else:
        raise AgentIdentityError("per_user identity requires an authenticated chat caller")

    return AgentRunIdentity(
        agent_id=agent_id,
        agent_owner_user_id=agent_owner_user_id,
        caller_user_id=caller_user_id,
        credential_subject_user_id=subject,
        identity_mode=identity_mode,
        runtime_name=runtime_name,
        source=source,
    )


def derive_child_agent_run_identity(
    parent_identity: AgentRunIdentity,
    child_agent: Agent,
) -> AgentRunIdentity:
    """Derive the execution identity for a Deep Agents custom subagent."""

    child_agent_id = _coerce_uuid(child_agent.id, field_name="child agent id")
    child_owner_user_id = _coerce_uuid(
        child_agent.user_id,
        field_name="child agent owner user id",
    )
    runtime_name = validate_runtime_name(
        child_agent.runtime_name or make_agent_runtime_name(child_agent_id)
    )
    identity_mode = validate_identity_mode(child_agent.identity_mode)

    if identity_mode == AGENT_IDENTITY_FIXED:
        subject = child_owner_user_id
    elif parent_identity.caller_user_id is not None:
        subject = parent_identity.caller_user_id
    else:
        raise AgentIdentityError(
            "per_user subagent identity requires an authenticated parent caller"
        )

    return AgentRunIdentity(
        agent_id=child_agent_id,
        agent_owner_user_id=child_owner_user_id,
        caller_user_id=parent_identity.caller_user_id,
        credential_subject_user_id=subject,
        identity_mode=identity_mode,
        runtime_name=runtime_name,
        source=AgentRunSource.SUBAGENT,
    )
