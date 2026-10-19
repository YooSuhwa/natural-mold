"""Historical timestamp defaults, independent of mutable live models."""

from datetime import UTC, datetime


def now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def now_aware() -> datetime:
    return datetime.now(UTC)
