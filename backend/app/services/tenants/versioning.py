"""Timestamp ETag helpers shared by tenant and organization administration."""

from datetime import UTC, datetime, timedelta

from app.authz.error_codes import authorization_error


def utc_timestamp(value: datetime) -> datetime:
    """Normalize database timestamps before comparing or exposing an ETag."""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def etag(value: datetime) -> str:
    """Format the product's quoted timestamp ETag."""

    return f'"{utc_timestamp(value).isoformat()}"'


def parse_version(supplied: str | None) -> datetime | None:
    """Parse an ``If-Match`` timestamp: optional ``W/``, balanced quotes, ``Z`` or offset.

    JSON bodies serialize ``updated_at`` with ``Z`` while the ``ETag`` uses ``+00:00``;
    both name the same instant. Naive values are read as UTC like database timestamps.
    """

    if supplied is None:
        return None
    value = supplied.strip()
    value = value.removeprefix("W/")
    if value[:1] == '"' or value[-1:] == '"':
        if len(value) < 2 or value[0] != '"' or value[-1] != '"':
            return None
        value = value[1:-1]
    try:
        return utc_timestamp(datetime.fromisoformat(value))
    except ValueError:
        return None


def require_current_version(current: datetime, supplied: str | None) -> None:
    """Reject absent, malformed, or stale optimistic-lock values with product 409 semantics."""

    if parse_version(supplied) != utc_timestamp(current):
        raise authorization_error("stale_version")


def bump_timestamp(current: datetime) -> datetime:
    """Return a timestamp newer than the persisted value even within one clock tick."""

    previous = utc_timestamp(current)
    return max(datetime.now(UTC), previous + timedelta(microseconds=1))
