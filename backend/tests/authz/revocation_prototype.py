"""A-10 deny-overlay prototype; production source models are introduced in P2."""

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.client import AuthzUnavailable, CheckQuery
from app.authz.fake import FakeAuthz


async def check_with_overlay(db: AsyncSession, fga: FakeAuthz, query: CheckQuery, org: str) -> bool:
    if not await fga.check(query):
        return False
    try:
        pending = await db.scalar(
            text(
                "SELECT COUNT(*) FROM revocation_prototype WHERE finalized_at IS NULL "
                "AND org_id = :org AND subject = :subject "
                "AND (scope IN ('membership', 'group_member', 'role', 'capability') "
                "OR object = :object)"
            ),
            {"org": org, "subject": query.user, "object": query.object},
        )
    except SQLAlchemyError as error:
        raise AuthzUnavailable("revocation_overlay") from error
    return pending == 0
