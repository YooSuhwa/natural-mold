"""S15→S17: the exit preview says whether "transfer now" can work for the caller."""

from collections.abc import Sequence

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.client import CheckQuery, Consistency
from app.authz.fake import FakeAuthz
from tests.authz.d04_support import AuthzSwitch, Unavailable, allow, authz
from tests.authz.e01_support import OTHER_USER_ID, add_agent, add_tool
from tests.authz.e02_support import MEMBERS, seed_admin

__all__ = ["authz"]


class CountingAuthz:
    """FakeAuthz that records the size of every BatchCheck."""

    def __init__(self, allowed: frozenset[tuple[str, str, str]]) -> None:
        self.inner = FakeAuthz(allowed)
        self.batches: list[int] = []

    def __getattr__(self, name: str) -> object:
        return getattr(self.inner, name)

    async def batch_check(
        self, queries: Sequence[CheckQuery], *, consistency: Consistency = "MINIMIZE_LATENCY"
    ) -> tuple[bool, ...]:
        self.batches.append(len(queries))
        return await self.inner.batch_check(queries, consistency=consistency)


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed_admin(db)


async def _flags(client: AsyncClient) -> dict[str, bool]:
    response = await client.get(f"{MEMBERS}/{OTHER_USER_ID}/exit-preview")
    assert response.status_code == 200, response.text
    return {e["resource_id"]: e["can_transfer_now"] for e in response.json()["owned"]}


async def test_transfer_now_follows_the_callers_can_govern(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    agent = await add_agent(db, owner=OTHER_USER_ID)
    tool = await add_tool(db, owner=OTHER_USER_ID)
    authz.use(mode="legacy")  # legacy: only the owner governs; an admin cannot transfer now
    assert set((await _flags(client)).values()) == {False}
    counting = CountingAuthz(allow(("can_govern", f"agent:{agent.id}")))
    authz.use(client=counting)  # type: ignore[arg-type]
    assert await _flags(client) == {str(agent.id): True, str(tool.id): False}
    assert len(counting.batches) == 1  # one BatchCheck for every owned resource
    assert counting.batches[0] >= 2


async def test_outage_turns_the_shortcut_off_without_failing_the_preview(
    client: AsyncClient, db: AsyncSession, authz: AuthzSwitch
) -> None:
    await add_agent(db, owner=OTHER_USER_ID)
    authz.use(client=Unavailable())
    assert set((await _flags(client)).values()) == {False}
