"""Explicit private targets for conversation mutations."""

from dataclasses import dataclass
from uuid import UUID

from app.authz.org_context import OrgContext
from app.authz.platform_scope import PlatformScope
from app.schemas.conversation import ConversationUpdate


@dataclass(frozen=True, slots=True)
class ConversationMutation:
    conversation_id: UUID
    context: OrgContext
    changes: ConversationUpdate


@dataclass(frozen=True, slots=True)
class ConversationAutoTitle:
    conversation_id: UUID
    context: OrgContext
    content: str


@dataclass(frozen=True, slots=True)
class ConversationBranchMutation:
    conversation_id: UUID
    context: OrgContext
    checkpoint_id: str | None


@dataclass(frozen=True, slots=True)
class ConversationPromotion:
    conversation_id: UUID
    context: OrgContext
    title_from_content: str | None


@dataclass(frozen=True, slots=True)
class ConversationPolicyTarget:
    conversation_id: UUID
    context: OrgContext
    current_trigger_run_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class DraftConversationCleanup:
    scope: PlatformScope
    retention_hours: int
