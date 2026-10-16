"""Shared seeds for the D-04 tool/MCP/credential/model route conversions (plan 9.5)."""

from collections.abc import Iterator, Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.client import AuthzUnavailable, CheckQuery, Consistency, ListQuery
from app.authz.fake import FakeAuthz, FakeTupleStore
from app.authz.runtime import get_authz
from app.authz.service import AuthzService, ShadowSample
from app.authz.service_types import AuthzClient, AuthzMode
from app.dependencies import CurrentUser
from app.models.authz import ResourceGrant
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.organization import Organization
from app.models.tool import Tool
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, ensure_unit_scope
from tests.conftest import TEST_USER_ID, override_get_current_user

OTHER_USER_ID = uuid5(NAMESPACE_URL, "moldy/d04-other-user")
OTHER_ORG_ID = uuid5(NAMESPACE_URL, "moldy/d04-other-organization")
ME = f"user:{TEST_USER_ID}"


class Unavailable:
    """OpenFGA down: every call fails like the SDK adapter's budget exhaustion."""

    async def check(
        self, query: CheckQuery, *, consistency: Consistency = "MINIMIZE_LATENCY"
    ) -> bool:
        raise AuthzUnavailable("check")

    async def batch_check(
        self, queries: Sequence[CheckQuery], *, consistency: Consistency = "MINIMIZE_LATENCY"
    ) -> tuple[bool, ...]:
        raise AuthzUnavailable("batch_check")

    async def list_objects(self, query: ListQuery) -> tuple[str, ...]:
        raise AuthzUnavailable("list_objects")


def allow(*pairs: tuple[str, str]) -> frozenset[tuple[str, str, str]]:
    """``(permission, object)`` pairs held by the test user."""
    return frozenset((ME, permission, obj) for permission, obj in pairs)


class AuthzSwitch:
    def __init__(self, app: FastAPI) -> None:
        self._app = app
        self.samples: list[ShadowSample] = []

    def use(
        self,
        allowed: frozenset[tuple[str, str, str]] = frozenset(),
        *,
        mode: AuthzMode = "enforce",
        client: AuthzClient | None = None,
    ) -> AuthzService:
        service = AuthzService(
            client or FakeAuthz(allowed), mode=mode, shadow_hook=self.samples.append
        )

        async def override() -> AuthzService:
            return service

        self._app.dependency_overrides[get_authz] = override
        return service


@pytest.fixture
def authz(test_app: FastAPI) -> Iterator[AuthzSwitch]:
    switch = AuthzSwitch(test_app)
    yield switch
    test_app.dependency_overrides.pop(get_authz, None)


async def seed_scope(db: AsyncSession) -> None:
    """The test user and a second member of the same organization, plus a sibling org."""
    await ensure_unit_scope(db, await override_get_current_user())
    other = CurrentUser(id=OTHER_USER_ID, email="other@test.com", name="Other", is_super_user=False)
    await ensure_unit_scope(db, other)
    if await db.get(Organization, OTHER_ORG_ID) is None:
        db.add(
            Organization(
                id=OTHER_ORG_ID, tenant_id=TEST_TENANT_ID, name="Sibling org", slug="d04-sibling"
            )
        )
    await db.commit()


async def add_tool(
    db: AsyncSession, *, owner: UUID = TEST_USER_ID, org: UUID = TEST_ORG_ID, name: str = "t"
) -> Tool:
    tool = Tool(
        user_id=owner,
        org_id=org,
        tenant_id=TEST_TENANT_ID,
        definition_key="http_request",
        name=name,
        parameters={},
    )
    db.add(tool)
    await db.commit()
    return tool


async def add_server(
    db: AsyncSession, *, owner: UUID = TEST_USER_ID, name: str = "srv"
) -> tuple[McpServer, McpTool]:
    server = McpServer(
        user_id=owner,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        name=name,
        transport="streamable_http",
        url="https://mcp.example.test",
    )
    db.add(server)
    await db.flush()
    tool = McpTool(server_id=server.id, name=f"{name}-tool", input_schema={})
    db.add(tool)
    await db.commit()
    return server, tool


async def grant_to_me(db: AsyncSession, resource_type: str, resource_id: UUID) -> None:
    db.add(
        ResourceGrant(
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            resource_type=resource_type,
            resource_id=resource_id,
            relation="viewer",
            principal_type="user",
            principal_id=TEST_USER_ID,
        )
    )
    await db.commit()


class OwnerStore(FakeTupleStore):
    """Tuple store answering like model.fga for owners: ``owner`` + ``org`` imply every
    permission; the create capabilities are held up front (read-your-writes tests)."""

    def _allowed(self, query: CheckQuery) -> bool:
        if query.relation.startswith("cap_"):
            return query.object == f"organization:{TEST_ORG_ID}"
        stored = {(t.user, t.relation, t.object) for t in self.tuples.values()}
        owner = (query.user, "owner", query.object) in stored
        return owner and (f"organization:{TEST_ORG_ID}", "org", query.object) in stored

    async def check(
        self, query: CheckQuery, *, consistency: Consistency = "MINIMIZE_LATENCY"
    ) -> bool:
        return self._allowed(query)

    async def batch_check(
        self, queries: Sequence[CheckQuery], *, consistency: Consistency = "MINIMIZE_LATENCY"
    ) -> tuple[bool, ...]:
        return tuple(self._allowed(query) for query in queries)

    async def list_objects(self, query: ListQuery) -> tuple[str, ...]:
        return ()
