"""``If-Match`` names an instant: JSON ``Z`` and ETag ``+00:00`` forms are equivalent."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.exceptions import AppError
from app.models.organization import Organization
from app.services.orgs import versioning as org_versioning
from app.services.tenants.versioning import etag, parse_version, require_current_version

INSTANT = datetime(2026, 10, 18, 3, 4, 5, 123456, tzinfo=UTC)
ACCEPTED = [
    etag(INSTANT),
    '"2026-10-18T03:04:05.123456Z"',
    'W/"2026-10-18T03:04:05.123456Z"',
    '"2026-10-18T12:04:05.123456+09:00"',
    "2026-10-18T03:04:05.123456Z",
    '"2026-10-18T03:04:05.123456"',  # naive (aiosqlite) values are UTC
]
REJECTED = [
    None,
    "",
    '"',
    "malformed",
    '"x"',
    '"unbalanced',
    'unbalanced"',
    '"2026-10-18T03:04:05Z"',
]


@pytest.mark.parametrize("supplied", ACCEPTED)
def test_equivalent_forms_match_the_same_instant(supplied: str) -> None:
    assert parse_version(supplied) == INSTANT
    require_current_version(INSTANT, supplied)
    require_current_version(INSTANT.replace(tzinfo=None), supplied)
    org_versioning.require_current_version(Organization(updated_at=INSTANT), supplied)


@pytest.mark.parametrize("supplied", REJECTED)
def test_absent_malformed_or_other_instant_is_stale(supplied: str | None) -> None:
    with pytest.raises(AppError) as caught:
        require_current_version(INSTANT, supplied)
    assert caught.value.code == "stale_version"
    with pytest.raises(AppError):
        org_versioning.require_current_version(Organization(updated_at=INSTANT), supplied)


def test_neighbouring_microsecond_is_stale() -> None:
    other = (INSTANT + timedelta(microseconds=1)).astimezone(timezone(timedelta(hours=9)))
    with pytest.raises(AppError):
        require_current_version(INSTANT, f'"{other.isoformat()}"')
