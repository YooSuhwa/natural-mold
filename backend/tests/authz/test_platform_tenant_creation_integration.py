"""PostgreSQL uniqueness races for atomic company creation."""

from __future__ import annotations

from collections.abc import Iterator
from contextvars import ContextVar
from typing import Literal, assert_never
from uuid import UUID

import anyio
import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.platform import tenant_creation
from tests.authz.platform_tenant_creation_native_fixture import (
    NativeCreationDatabase,
    NativeCreationFacts,
    creation_facts,
    native_owned_creation,
)

pytestmark = pytest.mark.integration
pytest_plugins = ["tests.authz.platform_tenant_creation_native_fixture"]

Writer = Literal["first", "second"]
Collision = Literal["slug", "namespace"]
_WRITER: ContextVar[Writer | None] = ContextVar("task12_writer", default=None)
_IDS: ContextVar[Iterator[UUID] | None] = ContextVar("task12_ids", default=None)
_FLUSH_COUNT: ContextVar[int] = ContextVar("task12_flush_count", default=0)


class NativeConstraintBarrier:
    """Hold the first unique-key owner until PostgreSQL proves the second is blocked."""

    def __init__(self) -> None:
        self.first_flushed = anyio.Event()
        self.second_started = anyio.Event()
        self.release_first = anyio.Event()
        self.first_pid: int | None = None
        self.second_pid: int | None = None

    async def flush(self, db: AsyncSession) -> None:
        count = _FLUSH_COUNT.get() + 1
        _FLUSH_COUNT.set(count)
        writer = _WRITER.get()
        if writer is None or count != 1:
            await _ORIGINAL_FLUSH(db)
            return
        pid = await db.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(pid, int)
        match writer:
            case "first":
                self.first_pid = pid
                await _ORIGINAL_FLUSH(db)
                self.first_flushed.set()
                await self.release_first.wait()
            case "second":
                self.second_pid = pid
                self.second_started.set()
                await _ORIGINAL_FLUSH(db)
            case unreachable:
                assert_never(unreachable)

    async def prove_second_blocked(self, database: NativeCreationDatabase) -> None:
        with anyio.fail_after(10):
            await self.second_started.wait()
            assert self.first_pid is not None and self.second_pid is not None
            while True:
                async with database.observer() as db:
                    blockers = await db.scalar(
                        text("SELECT pg_blocking_pids(:pid)"), {"pid": self.second_pid}
                    )
                    lock_wait = await db.scalar(
                        text(
                            "SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"
                        ),
                        {"pid": self.second_pid},
                    )
                if blockers and self.first_pid in blockers and lock_wait is True:
                    return
                await anyio.sleep(0.01)


_ORIGINAL_FLUSH = AsyncSession.flush


def _owned_uuid4() -> UUID:
    ids = _IDS.get()
    assert ids is not None
    return next(ids)


def _payload(slug: str) -> dict[str, str | int | bool]:
    return {
        "name": "Native Task 12 Company",
        "slug": slug,
        "first_admin_email": "task12-first-admin@example.com",
        "first_organization_name": "Native Task 12 Organization",
        "member_limit": 4,
        "monthly_spend_limit": "50.00",
        "concurrent_run_limit": 2,
        "allow_system_models": False,
    }


async def _create(
    client: AsyncClient,
    *,
    writer: Writer,
    ids: tuple[UUID, UUID, UUID],
    slug: str,
) -> Response:
    writer_token = _WRITER.set(writer)
    ids_token = _IDS.set(iter(ids))
    count_token = _FLUSH_COUNT.set(0)
    try:
        return await client.post("/api/admin/tenants", json=_payload(slug))
    finally:
        _FLUSH_COUNT.reset(count_token)
        _IDS.reset(ids_token)
        _WRITER.reset(writer_token)


@pytest.mark.parametrize(
    ("collision", "expected_status", "expected_code"),
    [
        pytest.param("slug", 409, "duplicate_tenant_slug", id="duplicate-slug"),
        pytest.param("namespace", 500, "tenant_namespace_collision", id="namespace-collision"),
    ],
)
async def test_authoritative_unique_constraint_has_one_complete_winner(
    native_creation_database: NativeCreationDatabase,
    monkeypatch: pytest.MonkeyPatch,
    collision: Collision,
    expected_status: int,
    expected_code: str,
) -> None:
    database = native_creation_database
    actor_id = UUID("12000000-0000-4000-8000-000000000001")
    first_ids = (
        UUID("12000000-0000-4000-8000-000000000011"),
        UUID("12000000-0000-4000-8000-000000000012"),
        UUID("12000000-0000-4000-8000-000000000013"),
    )
    second_ids = (
        UUID("12000000-0000-4000-8000-000000000021"),
        UUID("12000000-0000-4000-8000-000000000022"),
        UUID("12000000-0000-4000-8000-000000000023"),
    )
    tenant_ids = (first_ids[0], second_ids[0])
    barrier = NativeConstraintBarrier()
    monkeypatch.setattr(tenant_creation, "uuid4", _owned_uuid4)

    async def coordinated_flush(db: AsyncSession) -> None:
        await barrier.flush(db)

    monkeypatch.setattr(AsyncSession, "flush", coordinated_flush)
    match collision:
        case "slug":
            first_slug, second_slug = "task12-race", "task12-race"
        case "namespace":
            first_slug, second_slug = "task12-namespace-a", "task12-namespace-b"
            namespace = "tenants/12000000-0000-4000-8000-000000000099"
            monkeypatch.setattr(
                tenant_creation, "tenant_secrets_namespace", lambda _tenant_id: namespace
            )
        case unreachable:
            assert_never(unreachable)

    async with (
        native_owned_creation(database, actor_id=actor_id, tenant_ids=tenant_ids),
        database.client(database.primary, actor_id) as first_client,
        database.client(database.competing, actor_id) as second_client,
    ):
        responses: dict[Writer, Response] = {}

        async def submit_first() -> None:
            responses["first"] = await _create(
                first_client, writer="first", ids=first_ids, slug=first_slug
            )

        async def submit_second() -> None:
            responses["second"] = await _create(
                second_client, writer="second", ids=second_ids, slug=second_slug
            )

        with anyio.fail_after(30):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(submit_first)
                await barrier.first_flushed.wait()
                tasks.start_soon(submit_second)
                await barrier.prove_second_blocked(database)
                barrier.release_first.set()

        assert responses["first"].status_code == 201
        assert responses["second"].status_code == expected_status
        assert responses["second"].json()["error"]["code"] == expected_code
        assert "tenants/" not in responses["second"].text
        assert await creation_facts(database.observer, first_ids[0]) == NativeCreationFacts(
            1, 1, 1, 1, 0, 0, 0, 0
        )
        assert await creation_facts(database.observer, second_ids[0]) == NativeCreationFacts(
            0, 0, 0, 0, 0, 0, 0, 0
        )
