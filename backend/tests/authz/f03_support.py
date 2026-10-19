"""Fixtures for the F-03 delegation validator tests (plan 10.6).

``GraphFake`` is an OpenFGA stand-in that is both the decision client and the tuple
writer: grants applied through the outbox land in it, revoked ones are deleted, and a
user holding a share relation on an object passes that object's use permission. So a
revocation followed by the validator behaves like the real chain (grant revoked →
tuple deleted → delegator check fails), without hand-editing the allowed set.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_delegations import added_keys, sync_agent_delegations
from app.authz.client import AuthzUnavailable, CheckQuery, Consistency, ListQuery
from app.authz.fake import FakeTupleStore
from app.authz.grants import GrantRequest, GrantService, GrantTarget
from app.authz.resource_lifecycle import LifecycleChange, apply_lifecycle
from app.authz.service import AuthzService
from app.authz.service_types import AuthzMode
from app.models.authz import ResourceGrant
from app.models.tool import AgentToolLink
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession

ME = f"user:{TEST_USER_ID}"
SHARE_RELATIONS = {"viewer", "consumer", "editor", "manager"}
USE = {"can_use", "can_run", "can_invoke"}
TYPES = frozenset({"tool", "skill", "mcp_tool", "mcp_server", "agent", "credential"})


@dataclass(slots=True)
class GraphFake(FakeTupleStore):
    allowed: set[tuple[str, str, str]] = field(default_factory=set)
    consistencies: list[str] = field(default_factory=list)
    unavailable: int = 0  # the next N batch checks fail like OpenFGA being down

    def _ok(self, query: CheckQuery) -> bool:
        if (query.user, query.relation, query.object) in self.allowed:
            return True
        return query.relation in USE and any(
            (query.user, relation, query.object) in self.tuples for relation in SHARE_RELATIONS
        )

    async def check(
        self, query: CheckQuery, *, consistency: Consistency = "MINIMIZE_LATENCY"
    ) -> bool:
        return self._ok(query)

    async def batch_check(
        self, queries: Sequence[CheckQuery], *, consistency: Consistency = "MINIMIZE_LATENCY"
    ) -> tuple[bool, ...]:
        self.consistencies.append(consistency)
        if self.unavailable:
            self.unavailable -= 1
            raise AuthzUnavailable("batch_check")
        return tuple(self._ok(query) for query in queries)

    async def list_objects(self, query: ListQuery) -> tuple[str, ...]:
        return ()


def enforce(
    monkeypatch: pytest.MonkeyPatch, fake: GraphFake, *, mode: AuthzMode = "enforce"
) -> AuthzService:
    """The process service (``runtime_authz``) over ``fake``; outbox applies use tests' DB."""
    service = AuthzService(fake, mode=mode, enforce_types=TYPES)
    monkeypatch.setattr("app.authz.runtime._current", service)
    monkeypatch.setattr("app.database.async_session", TestSession)
    return service


async def share(
    db: AsyncSession, authz: AuthzService, owner: UUID, kind: str, rid: UUID, to: UUID
) -> ResourceGrant:
    """``owner`` shares ``kind:rid`` to user ``to`` (consumer), applied to the fake."""
    target, request = GrantTarget(kind, rid), GrantRequest("consumer", "user", to)
    change = await GrantService().grant(db, unit_context(owner), target, request)
    await db.commit()
    await apply_lifecycle(LifecycleChange(change.outbox_ids), authz)
    return change.grant


async def attach_tool(
    db: AsyncSession, authz: AuthzService, agent_id: UUID, tool_id: UUID, *, required: bool
) -> None:
    """The test user attaches ``tool_id`` (F-02 writer: link, delegation grant, tuples)."""
    db.add(
        AgentToolLink(
            agent_id=agent_id, tool_id=tool_id, delegated_by=TEST_USER_ID, required=required
        )
    )
    await db.flush()
    added = await added_keys(db, tool_ids=[tool_id])
    change = await sync_agent_delegations(db, unit_context(TEST_USER_ID), agent_id, added=added)
    await db.commit()
    await apply_lifecycle(change, authz)


async def link_state(agent_id: UUID, tool_id: UUID) -> tuple[str, str | None]:
    async with TestSession() as check:
        link = await check.get(AgentToolLink, (agent_id, tool_id))
        assert link is not None
        return link.link_status, link.broken_reason


async def delegation_state(agent_id: UUID, kind: str, rid: UUID) -> list[str]:
    async with TestSession() as check:
        rows = await check.scalars(
            select(ResourceGrant.state).where(
                ResourceGrant.principal_id == agent_id,
                ResourceGrant.resource_type == kind,
                ResourceGrant.resource_id == rid,
                ResourceGrant.grant_kind == "delegation",
            )
        )
        return sorted(rows)
