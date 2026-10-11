"""Explicit organization and identity inputs for runtime credential resolution."""

from dataclasses import dataclass

from app.agent_runtime.identity import AgentRunIdentity
from app.authz.org_context import OrgContext


@dataclass(frozen=True, slots=True)
class AgentCredentialAccess:
    context: OrgContext
    identity: AgentRunIdentity
