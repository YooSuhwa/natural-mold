"""Locked policy/version mutations retain a strictly increasing UTC timestamp."""

from datetime import UTC, datetime, timedelta

from app.authz.error_codes import authorization_error
from app.models.organization import Organization


def utc_timestamp(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def etag(value: datetime) -> str:
    return f'"{utc_timestamp(value).isoformat()}"'


def require_current_version(organization: Organization, supplied: str | None) -> None:
    if supplied is None:
        raise authorization_error("stale_version")
    try:
        expected = datetime.fromisoformat(supplied.strip('"'))
    except ValueError as error:
        raise authorization_error("stale_version") from error
    if utc_timestamp(expected) != utc_timestamp(organization.updated_at):
        raise authorization_error("stale_version")


def bump_version(organization: Organization) -> None:
    previous = utc_timestamp(organization.updated_at)
    organization.updated_at = max(datetime.now(UTC), previous + timedelta(microseconds=1))
    organization.authz_epoch += 1
