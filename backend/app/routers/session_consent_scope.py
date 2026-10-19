"""Captured private target and parsed command payload for session consent."""

from dataclasses import dataclass
from uuid import UUID

from app.authz.org_context import OrgContext
from app.routers.conversation_agent_protocol_interrupts import ThreadInterrupt
from app.routers.conversation_agent_protocol_resume import ResumePayload


@dataclass(frozen=True, slots=True)
class SessionConsentTarget:
    conversation_id: UUID
    context: OrgContext


@dataclass(frozen=True, slots=True)
class SessionConsentPayload:
    resume: ResumePayload
    pending_interrupts: list[ThreadInterrupt]
