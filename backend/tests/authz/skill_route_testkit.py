"""Shared fixtures for the D-03 skill-route authorization tests."""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

import pytest
from fastapi import FastAPI

from app.authz.client import CheckQuery, Consistency, ListQuery
from app.authz.fake import FakeTupleStore
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.models.authz import ResourceGrant
from app.models.skill import Skill
from app.skills.creation import TextSkillCreation
from tests.authz.authz_service_testkit import RecordingClient
from tests.authz.unit_scope_fixture import (
    TEST_ORG_ID,
    TEST_TENANT_ID,
    create_test_text_skill,
    unit_context,
)
from tests.conftest import TEST_USER_ID, TestSession
from tests.test_skills import _skill_md

ME = f"user:{TEST_USER_ID}"
ORG = f"organization:{TEST_ORG_ID}"
SKILL_GETS = (
    "",
    "/content",
    "/feedback",
    "/usage",
    "/files",
    "/revisions",
    "/evaluations",
    "/evaluations/version-stats",
    "/credential-requirements",
)


@pytest.fixture(name="use_authz")
def use_authz_fixture(test_app: FastAPI) -> Iterator[list[AuthzService]]:
    holder: list[AuthzService] = []

    async def override() -> AuthzService:
        return holder[0]

    test_app.dependency_overrides[get_authz] = override
    yield holder
    test_app.dependency_overrides.pop(get_authz, None)


async def _skill(owner=TEST_USER_ID, name="shared"):
    async with TestSession() as db:
        skill = await create_test_text_skill(
            db,
            TextSkillCreation(name=name, slug=None, description="d", content=_skill_md(name)),
            unit_context(owner),
        )
        await db.commit()
        return skill.id


def _enforce(*relations: tuple[str, str], unavailable: bool = False) -> AuthzService:
    allowed = frozenset((ME, relation, obj) for relation, obj in relations)
    return AuthzService(RecordingClient(allowed, unavailable=unavailable), mode="enforce")


async def _share(skill_id, *, org=TEST_ORG_ID) -> None:
    """The DB grant that makes a non-owned skill a candidate (``candidates.py``)."""
    async with TestSession() as db:
        db.add(
            ResourceGrant(
                org_id=org,
                tenant_id=TEST_TENANT_ID,
                resource_type="skill",
                resource_id=skill_id,
                relation="viewer",
                principal_type="user",
                principal_id=TEST_USER_ID,
            )
        )
        await db.commit()


async def _move_skill(skill_id, **values) -> None:
    async with TestSession() as db:
        skill = await db.get(Skill, skill_id)
        assert skill is not None
        for key, value in values.items():
            setattr(skill, key, value)
        await db.commit()


def _all_skill_grants(obj: str) -> tuple[tuple[str, str], ...]:
    perms = ("can_use", "can_view", "can_edit", "can_delete")
    return tuple((perm, obj) for perm in perms)


def _triples(store: FakeTupleStore) -> set[tuple[str, str, str]]:
    return {(t.user, t.relation, t.object) for t in store.stored()}


@dataclass
class OwnerModelStore(FakeTupleStore):
    """Tuple store that answers checks like the skill model for owners only.

    ``owner`` + ``org`` tuples imply every skill permission; ``cap_create_skill`` is
    granted to the caller up front. Enough to show read-your-writes after create.
    """

    def _allowed(self, query: CheckQuery) -> bool:
        if query.relation == "cap_create_skill":
            return query.object == ORG
        stored = {(t.user, t.relation, t.object) for t in self.tuples.values()}
        return (query.user, "owner", query.object) in stored and (
            ORG,
            "org",
            query.object,
        ) in stored

    async def check(self, query: CheckQuery, *, consistency: Consistency = "MINIMIZE_LATENCY"):
        return self._allowed(query)

    async def batch_check(
        self, queries: Sequence[CheckQuery], *, consistency: Consistency = "MINIMIZE_LATENCY"
    ) -> tuple[bool, ...]:
        return tuple(self._allowed(query) for query in queries)

    async def list_objects(self, query: ListQuery) -> tuple[str, ...]:
        return ()
