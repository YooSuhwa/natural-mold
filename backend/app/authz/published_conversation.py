"""Read scope for rendering one verified public conversation share."""

from dataclasses import dataclass
from uuid import UUID

from app.authz.org_context import OrgContext


@dataclass(frozen=True, slots=True, kw_only=True)
class PublishedConversationContext(OrgContext):
    published_conversation_id: UUID
