"""Persisted private identity for attaching a Skill builder chat runtime."""

from dataclasses import dataclass
from uuid import UUID

from app.authz.org_context import OrgContext


@dataclass(frozen=True, slots=True)
class SkillBuilderChatAttachment:
    session_id: UUID
    conversation_id: UUID
    draft_workspace_path: str
    context: OrgContext


@dataclass(frozen=True, slots=True)
class SkillBuilderToolTarget:
    session_id: UUID
    context: OrgContext
    workspace_path: str
    agent_id: UUID | None
