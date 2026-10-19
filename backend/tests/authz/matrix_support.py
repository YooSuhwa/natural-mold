"""Table-driven D-09 permission matrix: actor x mode -> status contract (plan 8.4, ch. 9).

A family test seeds one resource *owned by the caller*, ``place``s it for an actor and
asks ``expected`` what the route must answer. Actors (the caller is always the test
user, an ordinary member unless a case says ``super``):

* ``owner``    - the caller owns it; in enforce mode OpenFGA holds both permissions;
* ``granted``  - another member owns it, shared to the caller (grant row + both tuples);
* ``visible``  - shared, but OpenFGA holds only ``min_visible`` (403 when it differs);
* ``stranger`` - another member owns it, no grant, no tuple (hidden: 404);
* ``foreign``  - the caller's own row in a sibling organization, every tuple held
  (another organization's resource is 404 in every mode).

Modes: ``legacy`` (pre-P3 answers), ``enforce`` (fake decisions) and ``down`` (enforce
with OpenFGA unavailable: 503 ``authz_unavailable`` unless the row is out of scope).
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from functools import cache
from typing import Final, Literal
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import AsyncClient, Response
from pydantic import JsonValue

from app.authz import runtime
from app.authz.policies import CandidatesPolicy, ResourcePolicy, RoutePolicy, policy_of
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.database import Base
from app.dependencies import CurrentUser, get_current_user
from app.main import create_app
from app.models.authz import ResourceGrant
from tests.authz.authz_service_testkit import RecordingClient
from tests.authz.d04_support import OTHER_ORG_ID, OTHER_USER_ID
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession

Mode = Literal["legacy", "enforce", "down"]
Actor = Literal["owner", "granted", "visible", "stranger", "foreign"]
MODES: Final[tuple[Mode, ...]] = ("legacy", "enforce", "down")
ACTORS: Final[tuple[Actor, ...]] = ("owner", "granted", "visible", "stranger", "foreign")
ME: Final = f"user:{TEST_USER_ID}"
ORG: Final = f"organization:{TEST_ORG_ID}"


@dataclass(frozen=True, slots=True)
class Target:
    """A seeded row: its ORM table, authz object and the route path values."""

    table: str
    type_: str
    id: UUID
    path: Mapping[str, str]
    grant_relation: str = "viewer"
    owner_column: str = "user_id"


@dataclass(frozen=True, slots=True)
class Probe:
    """One representative route. ``known`` pins documented deviations from the generic
    table (``(actor, mode) -> status``); each use cites the plan/progress entry."""

    route: str
    body: object = None
    ok: int = 200
    known: Mapping[tuple[str, str], int] = field(default_factory=dict)
    gate: ResourcePolicy | None = None  # an in-route re-check ("추가 확인") on the target

    def __str__(self) -> str:  # readable parametrize ids
        return self.route


@cache
def _policies() -> Mapping[str, RoutePolicy]:
    found: dict[str, RoutePolicy] = {}
    for route in create_app().routes:
        if isinstance(route, APIRoute) and (policy := policy_of(route.endpoint)) is not None:
            found.update({f"{method} {route.path}": policy for method in route.methods})
    return found


def policy(route: str) -> RoutePolicy:
    """The policy the live app declares for ``"METHOD /path"``."""
    return _policies()[route]


def res_policy(route: str) -> ResourcePolicy | CandidatesPolicy:
    found = policy(route)
    assert isinstance(found, ResourcePolicy | CandidatesPolicy), (route, found)
    return found


PRIVATE_ACTORS: Final[tuple[Actor, ...]] = ("owner", "stranger", "foreign")


def cases(probes: tuple[Probe, ...], actors: tuple[Actor, ...] = ACTORS) -> list[tuple]:
    return [(probe, actor, mode) for probe in probes for actor in actors for mode in MODES]


async def place(target: Target, actor: Actor) -> None:
    """Move the caller-owned seed row to where ``actor`` finds it."""
    if actor == "owner":
        return
    table = Base.metadata.tables[target.table]
    values: dict[str, object] = (
        {"org_id": OTHER_ORG_ID} if actor == "foreign" else {target.owner_column: OTHER_USER_ID}
    )
    async with TestSession() as db:
        await db.execute(table.update().where(table.c.id == target.id).values(**values))
        if actor in ("granted", "visible"):
            db.add(
                ResourceGrant(
                    org_id=TEST_ORG_ID,
                    tenant_id=TEST_TENANT_ID,
                    resource_type=target.type_,
                    resource_id=target.id,
                    relation=target.grant_relation,
                    principal_type="user",
                    principal_id=TEST_USER_ID,
                )
            )
        await db.commit()


def tuples(found: RoutePolicy, actor: Actor, obj: str) -> set[tuple[str, str, str]]:
    """OpenFGA answers for ``actor`` on ``obj`` under the route's policy."""
    if actor == "stranger" or not isinstance(found, ResourcePolicy | CandidatesPolicy):
        return set()
    minimum = found.min_visible if isinstance(found, ResourcePolicy) else found.permission
    return {(ME, minimum, obj)} | (set() if actor == "visible" else {(ME, found.permission, obj)})


def expected(probe: Probe, actor: Actor, mode: Mode) -> int:
    """The 8.4 contract for a ``RES`` route (or a route re-checking ``probe.gate``)."""
    found = probe.gate or res_policy(probe.route)
    assert isinstance(found, ResourcePolicy)
    if (actor, mode) in probe.known:
        return probe.known[actor, mode]
    if actor == "foreign":
        return 404
    if mode == "down":
        return 503
    if mode == "legacy":
        return probe.ok if actor == "owner" else 404
    if actor == "visible" and found.permission != found.min_visible:
        return 403
    return 404 if actor == "stranger" else probe.ok


def assert_contract(response: Response, status: int) -> None:
    assert response.status_code == status, response.text
    if status in (403, 404, 503):
        code = response.json()["error"]["code"]
        if status == 503:
            assert code == "authz_unavailable"
        elif status == 404:
            assert code.endswith("NOT_FOUND"), code
        else:
            assert code in ("permission_required", "capability_required", "forbidden"), code


class Matrix:
    """Installs the mode's ``AuthzService`` (route + service level) and the caller."""

    def __init__(self, app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
        self._app, self._monkeypatch = app, monkeypatch
        self.as_user(super_user=False)

    def use(self, mode: Mode, allowed: set[tuple[str, str, str]] | None = None) -> None:
        client = RecordingClient(allowed=frozenset(allowed or ()), unavailable=mode == "down")
        service = AuthzService(client, mode="legacy" if mode == "legacy" else "enforce")
        self._app.dependency_overrides[get_authz] = lambda: service
        self._monkeypatch.setattr(runtime, "_current", service)

    def as_user(self, *, super_user: bool) -> None:
        user = CurrentUser(
            id=TEST_USER_ID, email="test@test.com", name="T", is_super_user=super_user
        )

        async def current() -> CurrentUser:
            return user

        self._app.dependency_overrides[get_current_user] = current

    async def call(
        self,
        client: AsyncClient,
        probe: Probe,
        target: Target | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Response:
        method, path = probe.route.split(" ", 1)
        url = path.format(**(target.path if target else {}))
        return await client.request(method, url, json=probe.body, headers=headers)

    async def check(
        self,
        client: AsyncClient,
        probe: Probe,
        actor: Actor,
        mode: Mode,
        target: Target,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """Place ``target`` for ``actor``, install ``mode`` and assert the RES contract."""
        await place(target, actor)
        found = probe.gate or policy(probe.route)
        self.use(mode, tuples(found, actor, f"{target.type_}:{target.id}"))
        response = await self.call(client, probe, target, headers)
        assert_contract(response, expected(probe, actor, mode))

    async def check_private(
        self,
        client: AsyncClient,
        probe: Probe,
        actor: Actor,
        mode: Mode,
        target: Target,
        allowed: set[tuple[str, str, str]] | None = None,
    ) -> None:
        """``PRIVATE``: only my row of the current organization; ``allowed`` holds the
        tuples of an in-route re-check (else OpenFGA must never be consulted)."""
        assert policy(probe.route).kind == "PRIVATE", probe.route
        await place(target, actor)
        self.use(mode, allowed)
        status = probe.known.get((actor, mode), probe.ok if actor == "owner" else 404)
        assert_contract(await self.call(client, probe, target), status)

    async def check_list(
        self,
        client: AsyncClient,
        route: str,
        actor: Actor,
        mode: Mode,
        target: Target,
        id_key: str = "id",
    ) -> None:
        """A ``CANDIDATES`` page lists the row only where 8.10 says it is a confirmed candidate."""
        await place(target, actor)
        self.use(mode, tuples(policy(route), actor, f"{target.type_}:{target.id}"))
        response = await self.call(client, Probe(route), target)
        if mode == "down" and actor in ("owner", "granted", "visible"):
            assert_contract(response, 503)
            return
        assert response.status_code == 200, response.text
        listed = str(target.id) in listed_ids(response.json(), id_key)
        shown = ("owner",) if mode == "legacy" else ("owner", "granted", "visible")
        assert listed == (actor in shown), (actor, mode)


def listed_ids(body: JsonValue, id_key: str = "id") -> set[str]:
    rows = body.get("items", []) if isinstance(body, dict) else body
    assert isinstance(rows, list)
    return {str(row[id_key]) for row in rows if isinstance(row, dict)}


@pytest.fixture
def matrix(test_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> Iterator[Matrix]:
    previous = test_app.dependency_overrides.get(get_current_user)
    yield Matrix(test_app, monkeypatch)
    test_app.dependency_overrides.pop(get_authz, None)
    if previous is not None:
        test_app.dependency_overrides[get_current_user] = previous
