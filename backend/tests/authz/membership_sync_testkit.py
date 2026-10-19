"""Membership tuple sync oracle: the drained store must equal ``expected_tuples()`` (7.2.1).

``reconciled_store`` starts from the reconciled world (as ``authz bootstrap`` would), the
event under test runs, ``drain`` applies its outbox rows through ``process_outbox`` and
``assert_parity`` compares the store with a fresh snapshot: the plan's C-04 completion
condition "DB change -> outbox -> expected_tuples() == OpenFGA Read".
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update

from app.authz.client import CheckQuery, Consistency, ListQuery
from app.authz.fake import FakeTupleStore
from app.authz.outbox import process_outbox
from app.authz.tuple_snapshot import load_snapshot
from app.authz.tuple_types import RelationTuple
from app.authz.tuples import expected_tuples
from app.models.authz import AuthzOutbox, AuthzRevocation
from tests.conftest import TestSession


async def expected_now() -> set[RelationTuple]:
    async with TestSession() as db:
        return set(expected_tuples(await load_snapshot(db), now=datetime.now(UTC)))


async def reconciled_store(store: FakeTupleStore | None = None) -> FakeTupleStore:
    """The reconciled world; rows queued by fixture setup (which also edits membership
    rows directly) are settled by that reconcile, so they are marked processed."""
    found = store if store is not None else FakeTupleStore()
    found.tuples = {(t.user, t.relation, t.object): t for t in await expected_now()}
    async with TestSession() as db:
        await db.execute(
            update(AuthzOutbox)
            .where(AuthzOutbox.processed_at.is_(None))
            .values(processed_at=datetime.now(UTC))
        )
        await db.commit()
    return found


async def drain(store: FakeTupleStore, *, now: datetime | None = None) -> int:
    """Apply every due row (``now`` past a failed row's backoff retries it)."""
    processed = 0
    while (run := await process_outbox(TestSession, store, now=now)).processed:
        processed += len(run.processed)
    return processed


async def assert_parity(store: FakeTupleStore) -> None:
    await drain(store)
    expected = await expected_now()
    missing, extra = expected - store.stored(), store.stored() - expected
    assert (missing, extra) == (set(), set()), f"missing={missing} extra={extra}"


async def pending_rows() -> int:
    async with TestSession() as db:
        rows = await db.scalars(select(AuthzOutbox.id).where(AuthzOutbox.processed_at.is_(None)))
        return len(list(rows))


async def outbox_rows() -> int:
    async with TestSession() as db:
        return len(list(await db.scalars(select(AuthzOutbox.id))))


async def markers(subject: str) -> list[tuple[str, UUID, bool]]:
    """(scope, org, finalized) of every marker naming ``subject``."""
    async with TestSession() as db:
        rows = await db.execute(
            select(
                AuthzRevocation.scope, AuthzRevocation.org_id, AuthzRevocation.finalized_at
            ).where(AuthzRevocation.subject == subject)
        )
        return sorted((scope, org, done is not None) for scope, org, done in rows.all())


async def delete_marker_orgs(obj: str) -> set[UUID]:
    """Organizations of the markers that source the queued deletes of ``obj``."""
    async with TestSession() as db:
        found = await db.scalars(
            select(AuthzRevocation.org_id)
            .join(AuthzOutbox, AuthzOutbox.source_id == AuthzRevocation.id)
            .where(AuthzOutbox.op == "delete", AuthzOutbox.tuple_key.endswith(f" {obj}"))
        )
        return set(found)


def user_tuples(store: FakeTupleStore, user_id: UUID) -> set[tuple[str, str]]:
    """(relation, object) of the user's stored tuples."""
    return {(t.relation, t.object) for t in store.stored() if t.user == f"user:{user_id}"}


class MemberStore(FakeTupleStore):
    """``llm_model#can_use`` as model.fga evaluates it for organization fanout:
    ``organization:O#member consumer`` plus the user's membership (member, builder or
    admin imply member). ``failing_objects`` also fails reads, so an outage is total."""

    def _member_orgs(self, user: str) -> set[str]:
        return {
            t.object
            for t in self.stored()
            if t.user == user
            and t.object.startswith("organization:")
            and t.relation in ("member", "builder", "admin")
        }

    def _allowed(self, query: CheckQuery) -> bool:
        if query.relation != "can_use" or not query.object.startswith("llm_model:"):
            return False
        consumers = {
            t.user.removesuffix("#member")
            for t in self.stored()
            if t.object == query.object and t.relation == "consumer"
        }
        return bool(consumers & self._member_orgs(query.user))

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
