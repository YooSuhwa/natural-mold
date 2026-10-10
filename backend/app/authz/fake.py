"""Explicit authorization fixture for database/route tests."""

from collections.abc import Sequence
from dataclasses import dataclass

from app.authz.client import CheckQuery, Consistency, ListQuery


@dataclass(frozen=True, slots=True)
class FakeAuthz:
    """A deterministic permission set, sharing the production client's contract."""

    allowed: frozenset[tuple[str, str, str]] = frozenset()

    async def check(
        self, query: CheckQuery, *, consistency: Consistency = "MINIMIZE_LATENCY"
    ) -> bool:
        return (query.user, query.relation, query.object) in self.allowed

    async def batch_check(
        self, queries: Sequence[CheckQuery], *, consistency: Consistency = "MINIMIZE_LATENCY"
    ) -> tuple[bool, ...]:
        return tuple(
            (query.user, query.relation, query.object) in self.allowed for query in queries
        )

    async def list_objects(self, query: ListQuery) -> tuple[str, ...]:
        return tuple(
            sorted(
                resource
                for subject, permission, resource in self.allowed
                if subject == query.user
                and permission == query.relation
                and resource.startswith(f"{query.type}:")
            )
        )
