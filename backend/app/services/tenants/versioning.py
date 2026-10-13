"""Timestamp ETag helpers shared by tenant and organization administration."""

from datetime import UTC, datetime, timedelta

from app.authz.error_codes import authorization_error


def utc_timestamp(value: datetime) -> datetime:
    """Normalize database timestamps before comparing or exposing an ETag."""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def etag(value: datetime) -> str:
    """Format the product's quoted timestamp ETag."""

    return f'"{utc_timestamp(value).isoformat()}"'


def require_current_version(current: datetime, supplied: str | None) -> None:
    """Reject absent, malformed, or stale optimistic-lock values with product 409 semantics."""

    if supplied is None:
        raise authorization_error("stale_version")
    try:
        expected = datetime.fromisoformat(supplied.strip('"'))
    except ValueError as error:
        raise authorization_error("stale_version") from error
    if utc_timestamp(expected) != utc_timestamp(current):
        raise authorization_error("stale_version")


def bump_timestamp(current: datetime) -> datetime:
    """Return a timestamp newer than the persisted value even within one clock tick."""

    previous = utc_timestamp(current)
    return max(datetime.now(UTC), previous + timedelta(microseconds=1))
