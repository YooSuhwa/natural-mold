"""Explicit authorization fixture for database/route tests."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from app.authz.client import AuthzUnavailable, CheckQuery, Consistency, ListQuery
from app.authz.tuple_types import RelationTuple


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


@dataclass(slots=True)
class FakeTupleStore:
    """In-memory OpenFGA store for outbox tests; same idempotency as the real client.

    ``fail_calls`` makes the next N calls unavailable; ``failing_objects`` makes every
    call touching those objects unavailable. A write that changes the condition of a
    stored key is refused like OpenFGA does, so callers must delete first.
    """

    tuples: dict[tuple[str, str, str], RelationTuple] = field(default_factory=dict)
    fail_calls: int = 0
    failing_objects: set[str] = field(default_factory=set)
    calls: list[tuple[str, RelationTuple]] = field(default_factory=list)

    def _guard(self, operation: str, tuples: Sequence[RelationTuple]) -> None:
        if self.fail_calls > 0 or any(t.object in self.failing_objects for t in tuples):
            self.fail_calls = max(self.fail_calls - 1, 0)
            raise AuthzUnavailable(operation)
        self.calls.extend((operation, item) for item in tuples)

    async def write(self, tuples: Sequence[RelationTuple]) -> None:
        self._guard("write", tuples)
        for item in tuples:
            stored = self.tuples.setdefault((item.user, item.relation, item.object), item)
            if stored != item:
                raise AuthzUnavailable("write")

    async def delete(self, tuples: Sequence[RelationTuple]) -> None:
        self._guard("delete", tuples)
        for item in tuples:
            self.tuples.pop((item.user, item.relation, item.object), None)

    def stored(self) -> set[RelationTuple]:
        return set(self.tuples.values())
