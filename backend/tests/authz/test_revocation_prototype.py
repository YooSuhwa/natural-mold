"""Stale FGA allows must not survive a committed revocation marker."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.authz.client import AuthzUnavailable, CheckQuery
from app.authz.fake import FakeAuthz
from tests.authz.revocation_prototype import check_with_overlay


@pytest.mark.parametrize("scope", ["grant", "membership", "group_member", "role", "capability"])
async def test_committed_marker_blocks_stale_allow_until_finalized(scope: str) -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    query = CheckQuery(user="user:u", relation="can_run", object="agent:a")
    fga = FakeAuthz(frozenset({("user:u", "can_run", "agent:a")}))
    try:
        async with AsyncSession(engine) as db:
            await db.execute(
                text(
                    "CREATE TABLE revocation_prototype (org_id text, subject text, object text, "
                    "scope text, finalized_at text)"
                )
            )
            assert await check_with_overlay(db, fga, query, "org:o")
            # Given: source revocation is committed but FGA still returns allow.
            await db.execute(
                text(
                    "INSERT INTO revocation_prototype "
                    "VALUES ('org:o', 'user:u', 'agent:a', :scope, NULL)"
                ),
                {"scope": scope},
            )
            await db.commit()
            # When / Then: only the affected subject and organization are blocked.
            assert await fga.check(query)
            assert not await check_with_overlay(db, fga, query, "org:o")
            assert await check_with_overlay(db, fga, query, "org:other")
            await db.execute(text("UPDATE revocation_prototype SET finalized_at = 'done'"))
            await db.commit()
            assert await check_with_overlay(db, fga, query, "org:o")
    finally:
        await engine.dispose()


async def test_revocation_storage_failure_never_becomes_allow() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with AsyncSession(engine) as db:
            query = CheckQuery(user="user:u", relation="can_run", object="agent:a")
            fga = FakeAuthz(frozenset({("user:u", "can_run", "agent:a")}))
            # Given: the marker table is unavailable; When / Then: fail closed.
            with pytest.raises(AuthzUnavailable, match="revocation_overlay"):
                await check_with_overlay(db, fga, query, "org:o")
    finally:
        await engine.dispose()
