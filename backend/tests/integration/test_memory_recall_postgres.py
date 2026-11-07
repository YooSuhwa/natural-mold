"""Memory recall on real PostgreSQL with RLS on and a non-owner app role (순번 15, PR-B2).

The i22 tables (vectors, recall records, profile documents) are company rows: another
company's session sees none of them, and recall runs under its statement time limit.
Lane only (``-m integration``).
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import database
from app.authz.org_context import OrgContext
from app.config import settings
from app.database import tenant_session
from app.models.memory import (
    MemoryEmbedding,
    MemoryProfileDoc,
    MemoryRecall,
    MemoryRecallRun,
    MemoryRecord,
)
from app.models.user import User
from app.services import memory_embedder, memory_recall
from app.text.korean_terms import FALLBACK_ANALYZER, fallback_terms
from tests.authz.i06_rls_lab import RlsLab, async_url, rls_lab

pytestmark = pytest.mark.integration
Maker = async_sessionmaker[database.ShieldedAsyncSession]


@pytest.fixture(scope="module")
def lab() -> Iterator[RlsLab]:
    with rls_lab() as value:
        yield value


@pytest.fixture(autouse=True)
def rls_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "db_rls_enabled", True)


@pytest.fixture
async def app_maker(lab: RlsLab) -> AsyncIterator[Maker]:
    engine = create_async_engine(async_url(lab.app_url), pool_size=4)
    try:
        yield async_sessionmaker(engine, class_=database.ShieldedAsyncSession)
    finally:
        await engine.dispose()


def _person(lab: RlsLab, tenant: UUID) -> OrgContext:
    org, user = uuid4(), uuid4()
    with lab.admin.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO organizations (id, tenant_id, name, slug, status, is_default, "
                "settings, authz_epoch, created_at, updated_at) VALUES (:id, :tenant, 'Org', "
                ":slug, 'active', false, '{}', 0, now(), now())"
            ),
            {"id": org, "tenant": tenant, "slug": f"o-{org.hex[:10]}"},
        )
        connection.execute(
            sa.insert(User).values(
                id=user, email=f"{user.hex[:10]}@test.com", name="P", is_active=True
            )
        )
    return OrgContext(org_id=org, tenant_id=tenant, user_id=user)


def _memories(lab: RlsLab, who: OrgContext, texts: list[str]) -> list[UUID]:
    ids = [uuid4() for _ in texts]
    with lab.admin.begin() as connection:
        for record_id, text in zip(ids, texts, strict=True):
            connection.execute(
                sa.insert(MemoryRecord).values(
                    id=record_id,
                    org_id=who.org_id,
                    tenant_id=who.tenant_id,
                    user_id=who.user_id,
                    scope="user",
                    content=text,
                    store_path="/memories/user",
                    search_tokens=" ".join(fallback_terms(text)),
                    search_tokens_analyzer=FALLBACK_ANALYZER,
                )
            )
    return ids


async def test_another_company_sees_none_of_the_recall_rows(lab: RlsLab, app_maker: Maker) -> None:
    a = _person(lab, lab.tenant_a)
    [record_id] = _memories(lab, a, ["배포 전에는 세션 설정을 확인한다"])
    scoped = {"org_id": a.org_id, "tenant_id": a.tenant_id, "user_id": a.user_id}
    with lab.admin.begin() as connection:
        connection.execute(
            sa.insert(MemoryEmbedding).values(
                id=uuid4(),
                org_id=a.org_id,
                tenant_id=a.tenant_id,
                record_id=record_id,
                user_id=a.user_id,
                model="m",
                dimensions=3,
                text_hash="h",
                vector=memory_embedder.to_bytes(memory_embedder.normalized([1, 0, 0])),
            )
        )
        connection.execute(
            sa.insert(MemoryRecallRun).values(
                id=uuid4(), run_id="run-a", mode="fresh", tokens=1, dropped=0, **scoped
            )
        )
        connection.execute(
            sa.insert(MemoryRecall).values(
                id=uuid4(), run_id="run-a", record_id=record_id, path="auto", **scoped
            )
        )
        connection.execute(
            sa.insert(MemoryProfileDoc).values(
                id=uuid4(), kind="user_profile", lines=[{"id": "1", "text": "x"}], **scoped
            )
        )
    tables = (MemoryEmbedding, MemoryRecallRun, MemoryRecall, MemoryProfileDoc)
    b = _person(lab, lab.tenant_b)
    async with tenant_session(b, maker=app_maker) as db:
        for model in tables:
            assert await db.scalar(sa.select(sa.func.count()).select_from(model)) == 0
    async with tenant_session(a, maker=app_maker) as db:
        for model in tables:
            assert await db.scalar(sa.select(sa.func.count()).select_from(model)) == 1


async def test_recall_ranks_on_postgres_within_its_time_limit(
    lab: RlsLab, app_maker: Maker
) -> None:
    who = _person(lab, lab.tenant_a)
    filler = [f"프로젝트 {i}번 회의는 매주 {i % 7}요일에 한다" for i in range(1000)]
    [wanted] = _memories(lab, who, ["배포 전에는 세션 설정을 꼭 확인한다"])
    _memories(lab, who, filler)
    query = memory_recall.Query.of(["세션을 확인했나요?"])
    filters = memory_recall.Filters(scopes="user", agent_id=None)
    timings = []
    for _ in range(5):
        async with tenant_session(who, maker=app_maker) as db:
            started = time.perf_counter()
            await memory_recall._set_statement_timeout(db)  # noqa: SLF001
            ranked = await memory_recall.rank(db, who, query, filters, embedder=None)
            timings.append(time.perf_counter() - started)
        assert [item.record.id for item in ranked.items][:1] == [wanted]
    # The measured value is reported by the lane; the limit itself is the database's.
    print(f"recall over 1001 memories: best {min(timings) * 1000:.0f} ms")
