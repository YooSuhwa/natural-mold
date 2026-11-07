"""Retention policies (plan 6.2 "보존 정책", 10.7, 14.1; E-08).

``organizations.settings.retention``: ``conversation_days`` (``None`` = unlimited,
30–3650), ``agent_trash_days`` (default 30, 7–90), ``removed_member_days`` (default 30,
7–3650). ``tenants.settings.audit_retention_days`` (default 365, 90–3650). Absent or
unreadable stored values fall back to the defaults, so a damaged setting never deletes
more than the documented default would.
"""

from datetime import datetime, timedelta
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

ConversationDays = Annotated[int, Field(ge=30, le=3650)]
AgentTrashDays = Annotated[int, Field(ge=7, le=90)]
RemovedMemberDays = Annotated[int, Field(ge=7, le=3650)]
AuditRetentionDays = Annotated[int, Field(ge=90, le=3650)]
# Memory policy plan 15 (Q5): experience memories. ``None`` = unlimited; ``"default"`` = the
# tenant kind's default (company 365 days, personal unlimited).
ExperienceDays = Annotated[int, Field(ge=30, le=3650)]
EXPERIENCE_DEFAULTS: Final = {"company": 365, "personal": None}
ORG_SETTING: Final = "retention"
AUDIT_SETTING: Final = "audit_retention_days"
DEFAULT_AUDIT_DAYS: Final = 365


class OrgRetention(BaseModel):
    """The stored organization policy; also the full-replacement ``PUT`` body fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    conversation_days: ConversationDays | None = None
    agent_trash_days: AgentTrashDays = 30
    removed_member_days: RemovedMemberDays = 30
    experience_memory_days: ExperienceDays | Literal["default"] | None = "default"

    def as_setting(self) -> dict[str, JsonValue]:
        return {
            "conversation_days": self.conversation_days,
            "agent_trash_days": self.agent_trash_days,
            "removed_member_days": self.removed_member_days,
            "experience_memory_days": self.experience_memory_days,
        }


class OrgRetentionUpdate(OrgRetention):
    """``PUT /api/orgs/{org_id}/retention``; a shortening needs ``confirm=true``."""

    confirm: bool = False


class RetentionPreview(BaseModel):
    """What the next sweep would delete under the submitted (unsaved) policy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    conversations: int = 0
    agents: int = 0
    removed_member_conversations: int = 0
    removed_member_memories: int = 0
    audit_events: int = 0
    # Guardrail detection rows age out with the audit period (guardrails/events.py).
    guardrail_events: int = 0
    # Plan 15 (5.7): experience memories past their period, revoked memories past 30 days.
    expired_memories: int = 0


class OrgRetentionView(OrgRetention):
    updated_at: datetime


class OrgRetentionWrite(BaseModel):
    """``saved=false`` with a ``preview``: nothing changed, resend with ``confirm=true``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    saved: bool
    preview: RetentionPreview | None = None
    policy: OrgRetentionView


def org_retention(settings: dict[str, JsonValue] | None) -> OrgRetention:
    raw = (settings or {}).get(ORG_SETTING)
    if not isinstance(raw, dict):
        return OrgRetention()
    values: dict[str, JsonValue] = {}
    for name in OrgRetention.model_fields:
        if name in raw:
            try:  # one damaged field keeps its default, the others stay
                OrgRetention.model_validate({name: raw[name]})
            except ValidationError:
                continue
            values[name] = raw[name]
    return OrgRetention.model_validate(values)


def audit_retention_days(settings: dict[str, JsonValue] | None) -> int:
    raw = (settings or {}).get(AUDIT_SETTING)
    if isinstance(raw, int) and not isinstance(raw, bool) and 90 <= raw <= 3650:
        return raw
    return DEFAULT_AUDIT_DAYS


def shorter(new: int | None, old: int | None) -> bool:
    """``None`` is unlimited: any finite period is shorter than it."""
    if new is None:
        return False
    return old is None or new < old


def experience_days(org_settings: dict[str, JsonValue] | None, kind: str) -> int | None:
    """The organization's experience-memory period, or the tenant kind's default (Q5)."""
    value = org_retention(org_settings).experience_memory_days
    if value == "default":
        return EXPERIENCE_DEFAULTS.get(kind, 365)
    return value


def trash_delete_at(archived_at: datetime, settings: dict[str, JsonValue] | None) -> datetime:
    """When the sweep purges an agent that entered the trash at ``archived_at``."""
    return archived_at + timedelta(days=org_retention(settings).agent_trash_days)
