"""Shared fixtures for the AuthzService tests: a recording fake client and helpers."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.authz.client import AuthzUnavailable, CheckQuery, Consistency, ListQuery
from app.authz.fake import FakeAuthz
from app.authz.principal import Principal
from app.authz.service import AuthzService, Verdict

ORG = uuid4()


OTHER_ORG = uuid4()


USER = uuid4()


AGENT = f"agent:{uuid4()}"


NO_IP = "0.0.0.0"  # noqa: S104 - plan 7.3 sentinel


NOW = datetime(2026, 10, 17, 3, 4, 5, tzinfo=UTC)


@dataclass
class RecordingClient:
    """FakeAuthz plus query recording and an optional outage."""

    allowed: frozenset[tuple[str, str, str]] = frozenset()
    unavailable: bool = False
    queries: list[CheckQuery | ListQuery] = field(default_factory=list)

    def _fake(self) -> FakeAuthz:
        if self.unavailable:
            raise AuthzUnavailable("check")
        return FakeAuthz(self.allowed)

    async def check(
        self, query: CheckQuery, *, consistency: Consistency = "MINIMIZE_LATENCY"
    ) -> bool:
        self.queries.append(query)
        return await self._fake().check(query)

    async def batch_check(
        self, queries: Sequence[CheckQuery], *, consistency: Consistency = "MINIMIZE_LATENCY"
    ) -> tuple[bool, ...]:
        self.queries.extend(queries)
        return await self._fake().batch_check(queries)

    async def list_objects(self, query: ListQuery) -> tuple[str, ...]:
        self.queries.append(query)
        return await self._fake().list_objects(query)


def user(org: UUID = ORG) -> Principal:
    return Principal(kind="user", id=USER, org_id=org)


def grants(*relations: str, obj: str = AGENT) -> frozenset[tuple[str, str, str]]:
    return frozenset((f"user:{USER}", relation, obj) for relation in relations)


def service(
    client: RecordingClient,
    mode: str = "enforce",
    *,
    types: frozenset[str] = frozenset(),
    hook: object = None,
) -> AuthzService:
    return AuthzService(client, mode=mode, enforce_types=types, shadow_hook=hook, clock=lambda: NOW)  # type: ignore[arg-type]


async def require(svc: AuthzService, *, org: UUID | None = ORG, legacy=Verdict.ALLOW, **kw):
    await svc.require(
        user(),
        "can_edit",
        AGENT,
        min_visible="can_run",
        resource_org_id=org,
        legacy=legacy,
        **kw,
    )
