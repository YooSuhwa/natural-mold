"""Metadata-only organization restore (internal I22).

Restore clears only the organization archive metadata. It never touches
``is_default``, triggers, deployments, runs, inputs or envelopes: archived
work stays terminal and paused/disabled entry points stay that way until an
administrator explicitly re-enables each one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.authz.error_codes import authorization_error
from app.models.organization import Organization

RESTORE_WINDOW = timedelta(days=30)


def restore_archive_metadata(organization: Organization, now: datetime | None = None) -> None:
    """Require an archived row inside the window, then clear its archive stamp."""
    archived_at = organization.archived_at
    if organization.status != "archived" or archived_at is None:
        raise authorization_error("organization_unavailable")
    if archived_at.tzinfo is None:
        archived_at = archived_at.replace(tzinfo=UTC)
    if (now or datetime.now(UTC)) - archived_at > RESTORE_WINDOW:
        raise authorization_error("organization_restore_expired")
    organization.status = "active"
    organization.archived_at = None
