"""Shared seeding and AuthzService swaps for the D-02 agent route tests."""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from fastapi import FastAPI

from app.authz.client import AuthzUnavailable, CheckQuery, Consistency, ListQuery
from app.authz.fake import FakeAuthz, FakeTupleStore
from app.authz.runtime import get_authz
from app.authz.service import AuthzService, ShadowSample
from app.dependencies import CurrentUser
from app.models.agent import Agent
from app.models.authz import ResourceGrant
from app.models.model import Model
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, ensure_unit_scope
from tests.conftest import TEST_USER_ID, TestSession

OTHER_USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000000d2")
ME = f"user:{TEST_USER_ID}"
ORG = f"organization:{TEST_ORG_ID}"


@dataclass
class AgentOwnerStore(FakeTupleStore):
    """Answers like the agent model for owners only (owner + org tuples => every
    permission) and grants ``cap_create_agent``: no access until the outbox writes."""

    def _allowed(self, query: CheckQuery) -> bool:
        if query.relation == "cap_create_agent":
            return query.object == ORG
        stored = {(t.user, t.relation, t.object) for t in self.tuples.values()}
        return {(query.user, "owner", query.object), (ORG, "org", query.object)} <= stored

    async def check(self, query: CheckQuery, *, consistency: Consistency = "MINIMIZE_LATENCY"):
        return self._allowed(query)

    async def batch_check(
        self, queries: Sequence[CheckQuery], *, consistency: Consistency = "MINIMIZE_LATENCY"
    ) -> tuple[bool, ...]:
        return tuple(self._allowed(query) for query in queries)

    async def list_objects(self, query: ListQuery) -> tuple[str, ...]:
        return ()


class DownAuthz:
    """OpenFGA outage: every call is unavailable."""

    async def check(self, query: CheckQuery, *, consistency: Consistency = "MINIMIZE_LATENCY"):
        raise AuthzUnavailable("check")

    async def batch_check(
        self, queries: Sequence[CheckQuery], *, consistency: Consistency = "MINIMIZE_LATENCY"
    ):
        raise AuthzUnavailable("batch_check")

    async def list_objects(self, query: ListQuery):
        raise AuthzUnavailable("list_objects")


def enforce(*allowed: tuple[str, str, uuid.UUID], types=frozenset({"agent"})) -> AuthzService:
    tuples = frozenset((user, perm, f"agent:{agent}") for user, perm, agent in allowed)
    return AuthzService(FakeAuthz(allowed=tuples), mode="enforce", enforce_types=types)


def shadow(samples: list[ShadowSample]) -> AuthzService:
    return AuthzService(FakeAuthz(), mode="shadow", shadow_hook=samples.append)


@contextmanager
def authz_override(app: FastAPI, service: AuthzService) -> Iterator[None]:
    app.dependency_overrides[get_authz] = lambda: service
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_authz, None)


async def seed_agent(*, owner: uuid.UUID, name: str = "Agent", grant_to_me: bool = False) -> str:
    """An agent of ``owner`` in the unit org; optionally shared to the test user."""
    async with TestSession() as db:
        for user_id in {owner, TEST_USER_ID}:
            await ensure_unit_scope(
                db, CurrentUser(id=user_id, email=f"{user_id}@t.io", name="U", is_super_user=False)
            )
        model = Model(provider="openai", model_name=f"m-{uuid.uuid4().hex[:6]}", display_name="M")
        db.add(model)
        await db.flush()
        agent = Agent(
            user_id=owner,
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            name=name,
            system_prompt="secret prompt",
            model_id=model.id,
            opener_questions=["hi?"],
        )
        db.add(agent)
        await db.flush()
        if grant_to_me:
            db.add(
                ResourceGrant(
                    org_id=TEST_ORG_ID,
                    tenant_id=TEST_TENANT_ID,
                    resource_type="agent",
                    resource_id=agent.id,
                    relation="consumer",
                    principal_type="user",
                    principal_id=TEST_USER_ID,
                )
            )
        await db.commit()
        return str(agent.id)
