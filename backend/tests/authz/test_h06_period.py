"""H-06: the counted month and ``resets_at`` agree (UTC-day aggregates, company time zone)."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_spend_user import DailySpendUser
from app.tenancy.limit_usage import period_of, spend_used
from tests.authz.h03_support import TEST_ORG_ID, TEST_TENANT_ID, TEST_USER_ID, seed_world

SEOUL = {"timezone": "Asia/Seoul"}
NEW_YORK = {"timezone": "America/New_York"}


@pytest.mark.parametrize(
    ("settings", "now", "start", "end", "key"),
    [
        # Seoul: local Oct 1 00:00 = Sep 30 15:00 UTC → whole UTC days from Oct 1.
        (
            SEOUL,
            datetime(2026, 10, 17, 3, tzinfo=UTC),
            date(2026, 10, 1),
            date(2026, 11, 1),
            "2026-10",
        ),
        # Local Nov 1 03:00 KST is still Oct 31 UTC: October's count is not over yet.
        (
            SEOUL,
            datetime(2026, 10, 31, 18, tzinfo=UTC),
            date(2026, 10, 1),
            date(2026, 11, 1),
            "2026-10",
        ),
        # New York: local Oct 1 00:00 = Oct 1 04:00 UTC → counted from Oct 2.
        (
            NEW_YORK,
            datetime(2026, 10, 1, 12, tzinfo=UTC),
            date(2026, 9, 2),
            date(2026, 10, 2),
            "2026-09",
        ),
        (
            NEW_YORK,
            datetime(2026, 10, 2, 0, tzinfo=UTC),
            date(2026, 10, 2),
            date(2026, 11, 2),
            "2026-10",
        ),
        (
            None,
            datetime(2026, 12, 31, 16, tzinfo=UTC),
            date(2026, 12, 1),
            date(2027, 1, 1),
            "2026-12",
        ),
    ],
)
def test_period_counts_whole_utc_days_and_resets_when_the_next_count_starts(
    settings: dict[str, str] | None, now: datetime, start: date, end: date, key: str
) -> None:
    period = period_of(settings, now)
    assert (period.start, period.end, period.key) == (start, end, key)
    assert period.resets_at == datetime(end.year, end.month, end.day, tzinfo=UTC)
    assert period.resets_at > now >= datetime(start.year, start.month, start.day, tzinfo=UTC)


@pytest.mark.asyncio
async def test_rows_outside_the_window_are_not_counted(db: AsyncSession) -> None:
    await seed_world(db)
    period = period_of(SEOUL, datetime(2026, 10, 17, tzinfo=UTC))
    for day, cost in ((date(2026, 9, 30), "7"), (date(2026, 10, 1), "2"), (date(2026, 11, 1), "5")):
        db.add(
            DailySpendUser(
                tenant_id=TEST_TENANT_ID,
                org_id=TEST_ORG_ID,
                user_id=TEST_USER_ID,
                date=day,
                total_cost_usd=Decimal(cost),
                request_count=1,
            )
        )
    await db.commit()
    assert await spend_used(db, "tenant", TEST_TENANT_ID, period) == Decimal(2)
