"""Index rows and jobs stay within the selecting tenant, organization and model identity."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs import service as jobs
from app.jobs.context import JobContext
from app.models.job import Job
from app.models.memory import MemoryEmbedding, MemoryRecord
from app.models.organization import Organization, Tenant
from app.services import memory_embedder, memory_index, memory_recall, memory_vectors
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID, make_model, seed_agent
from tests.sqlite_test_engine import TestSession


def test_vector_identity_tracks_the_registered_producer() -> None:
    model = memory_embedder.OpenAIShapedEmbedder(
        model="same-name",
        dimensions=3,
        api_key="dummy",
        base_url="https://example.test/a",
        registry_id=uuid.uuid4(),
        provider="openai_compatible",
    )
    key = memory_embedder.vector_model_key(model)
    assert key.startswith("v2:") and len(key) == 67
    for change in (
        {"model": "different"},
        {"dimensions": 2},
        {"provider": "litellm"},
        {"base_url": "https://example.test/b"},
        {"registry_id": uuid.uuid4()},
    ):
        assert key != memory_embedder.vector_model_key(replace(model, **change))
    assert key == memory_embedder.vector_model_key(
        replace(
            model,
            base_url=model.base_url + "/",
            api_key="rotated",
            generation=2,
        )
    )
    assert memory_embedder.vector_model_key(replace(model, registry_id=None)) == "same-name"


def _record(
    db: AsyncSession, tenant_id: uuid.UUID, org_id: uuid.UUID, content: str
) -> MemoryRecord:
    record = MemoryRecord(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        org_id=org_id,
        user_id=TEST_USER_ID,
        scope="user",
        content=content,
        store_path="memory/" + content,
    )
    db.add(record)
    return record


def _row(record: MemoryRecord, model: str, *, data: bytes = b"old") -> MemoryEmbedding:
    return MemoryEmbedding(
        record_id=record.id,
        tenant_id=record.tenant_id,
        org_id=record.org_id,
        user_id=TEST_USER_ID,
        model=model,
        dimensions=3,
        text_hash=memory_embedder.text_hash(record.content),
        vector=data,
    )


def _job(payload: dict[str, Any], *, job_id: uuid.UUID | None = None) -> JobContext:
    return JobContext(
        job_id=job_id or uuid.uuid4(),
        tenant_id=TEST_TENANT_ID,
        org_id=TEST_ORG_ID,
        actor_id=None,
        type=memory_index.INDEX_JOB,
        attempt=1,
        payload=payload,
        session=TestSession,
        _progress=AsyncMock(),
    )


async def _selected_model(db: AsyncSession) -> tuple[Tenant, memory_embedder.OpenAIShapedEmbedder]:
    await seed_agent(db)
    model = await make_model(
        db,
        provider="openai_compatible",
        model_name="same-name",
        display_name="embedding",
        base_url="https://embed.test/v1",
        kind="embedding",
        embedding_dimensions=3,
    )
    tenant = await db.get(Tenant, TEST_TENANT_ID)
    assert tenant is not None
    tenant.settings = {"memory": {"embedding_model_id": str(model.id), "embedding_generation": 1}}
    await db.commit()
    embedder = await memory_vectors.embedder_for(db, tenant)
    assert isinstance(embedder, memory_embedder.OpenAIShapedEmbedder)
    return tenant, embedder


@pytest.mark.asyncio
async def test_index_dispatch_and_cleanup_scope_with_rls_off(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant, model = await _selected_model(db)
    allowed, denied, outside_org, outside_tenant = [uuid.uuid4() for _ in range(4)]
    tenant.settings = {
        **tenant.settings,
        "model_orgs": {str(model.registry_id): [str(TEST_ORG_ID), str(allowed)]},
    }
    db.add(Tenant(id=outside_tenant, slug="other", name="other", secrets_namespace="other"))
    await db.flush()
    for org_id, tenant_id in (
        (allowed, tenant.id),
        (denied, tenant.id),
        (outside_org, outside_tenant),
    ):
        db.add(Organization(id=org_id, tenant_id=tenant_id, name=str(org_id), slug=str(org_id)))
    await db.flush()
    own = _record(db, tenant.id, TEST_ORG_ID, "own")
    sibling = _record(db, tenant.id, allowed, "sibling")
    blocked = _record(db, tenant.id, denied, "denied")
    foreign = _record(db, outside_tenant, outside_org, "foreign")
    await db.flush()
    model_key = memory_embedder.vector_model_key(model)
    db.add_all(
        [
            _row(own, model.model),
            _row(sibling, model_key),
            _row(blocked, model.model),
            _row(foreign, model.model),
        ]
    )
    await db.commit()
    sent: list[str] = []

    async def embed(_self: object, texts: list[str], *, seconds: float) -> list[np.ndarray]:
        sent.extend(texts)
        return [memory_embedder.normalized([1, 0, 0]) for _ in texts]

    monkeypatch.setattr(memory_embedder.OpenAIShapedEmbedder, "embed", embed)
    targeted = _job({"record_ids": [str(r.id) for r in (own, sibling, blocked, foreign)]})
    assert (await memory_index._run(targeted))["embedded"] == 1
    assert sent == ["own"]

    await memory_index.enqueue_backfill(db, unit_context(TEST_USER_ID), model)
    job = (await db.scalars(select(Job).where(Job.type == memory_index.INDEX_JOB))).one()
    assert job.idempotency_key == memory_index.backfill_key(model_key, 3, 1)
    await db.commit()
    assert (await memory_index._run(_job(job.payload, job_id=job.id)))["embedded"] == 1
    assert sent == ["own", "sibling"]
    rows = (await db.execute(select(MemoryEmbedding.record_id, MemoryEmbedding.model))).all()
    assert set(rows) == {
        (own.id, model_key),
        (sibling.id, model_key),
        (blocked.id, model.model),
        (foreign.id, model.model),
    }


@pytest.mark.parametrize("during", ["before_claim", "request", "progress"])
@pytest.mark.asyncio
async def test_changed_selection_protects_the_new_models_rows(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, during: str
) -> None:
    tenant, model = await _selected_model(db)
    next_model = await make_model(
        db,
        provider="openai_compatible",
        model_name=model.model,
        display_name="next",
        base_url="https://next.test/v1",
        kind="embedding",
        embedding_dimensions=3,
    )
    record = _record(db, tenant.id, TEST_ORG_ID, "first")
    _record(db, tenant.id, TEST_ORG_ID, "second")
    await db.flush()
    next_key = memory_embedder.vector_model_key(
        replace(
            model,
            registry_id=next_model.id,
            base_url=next_model.base_url,
        )
    )
    db.add(_row(record, next_key))
    await memory_index.enqueue_backfill(db, unit_context(TEST_USER_ID), model)
    job = (await db.scalars(select(Job).where(Job.type == memory_index.INDEX_JOB))).one()
    await db.commit()
    monkeypatch.setattr(memory_index, "_BATCH", 1)
    calls = 0

    async def change_model() -> None:
        tenant.settings = {
            "memory": {
                "embedding_model_id": str(next_model.id),
                "embedding_generation": 2,
            }
        }
        await db.commit()

    async def embed(_self: object, texts: list[str], *, seconds: float) -> list[np.ndarray]:
        nonlocal calls
        calls += 1
        if during == "request":
            await change_model()
        return [memory_embedder.normalized([0, 1, 0]) for _ in texts]

    monkeypatch.setattr(memory_embedder.OpenAIShapedEmbedder, "embed", embed)
    context = _job(job.payload, job_id=job.id)
    if during == "before_claim":
        await change_model()
    if during == "progress":

        async def progress(percent: int, note: str | None) -> None:
            await change_model()

        context._progress = progress
    total = await memory_index._run(context)
    assert calls == int(during != "before_claim") and total["embedded"] == int(during == "progress")
    keys = set(await db.scalars(select(MemoryEmbedding.model)))
    assert next_key in keys


@pytest.mark.asyncio
async def test_revoke_during_nori_excludes_the_record_from_dispatch(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tenant, _model = await _selected_model(db)
    revoked = _record(db, TEST_TENANT_ID, TEST_ORG_ID, "revoked-during-tokenization")
    unapproved = _record(db, TEST_TENANT_ID, TEST_ORG_ID, "partial-approval")
    unapproved.approved_by = TEST_USER_ID
    withdrawn = _record(db, TEST_TENANT_ID, TEST_ORG_ID, "legacy-rejected")
    withdrawn.flags = {"legacy_rejected": "rejected"}
    valid = _record(db, TEST_TENANT_ID, TEST_ORG_ID, "valid-legacy")
    await db.commit()
    analyzed: list[str] = []
    sent: list[str] = []

    async def nori(text: str, *, timeout_ms: int) -> tuple[str, ...]:
        analyzed.append(text)
        if text == revoked.content:
            revoked.revoked_at = datetime.now(UTC).replace(tzinfo=None)
            await db.commit()
        return ("term",)

    async def embed(_self: object, texts: list[str], *, seconds: float) -> list[np.ndarray]:
        sent.extend(texts)
        return [memory_embedder.normalized([0, 1, 0]) for _ in texts]

    monkeypatch.setattr(memory_index.settings, "opensearch_url", "http://nori.test")
    monkeypatch.setattr(memory_index.korean_terms, "nori_terms", nori)
    monkeypatch.setattr(memory_embedder.OpenAIShapedEmbedder, "embed", embed)
    assert await memory_index._run(_job({"all": True})) == {"embedded": 1, "retokenized": 1}
    assert set(analyzed) == {revoked.content, valid.content}
    assert sent == [valid.content]
    assert list(await db.scalars(select(MemoryEmbedding.record_id))) == [valid.id]


@pytest.mark.asyncio
async def test_missing_projection_coalesces_repairs_after_a_completed_generation(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant, model = await _selected_model(db)
    context = unit_context(TEST_USER_ID)
    floor = 0.8
    monkeypatch.setattr(memory_vectors.memory_recall_policy, "passing_floor", lambda **_: floor)
    await memory_vectors.change(
        db, tenant, context, model_id=model.registry_id, confirm_count=0, recall_mode="serve"
    )
    selected = await memory_vectors.embedder_for(db, tenant, org_id=context.org_id)
    assert (
        isinstance(selected, memory_embedder.OpenAIShapedEmbedder)
        and selected.recall_mode == "serve"
    )
    model = selected
    record = _record(db, tenant.id, TEST_ORG_ID, "repair")
    await db.flush()
    unit = memory_embedder.normalized([1, 0, 0])
    embedding = AsyncMock(return_value=[unit])
    monkeypatch.setattr(memory_embedder.OpenAIShapedEmbedder, "embed", embedding)
    # A pre-upgrade generation may have a raw model-name backfill in the same queue.
    legacy = await jobs.enqueue(
        db,
        context,
        jobs.EnqueueRequest(
            type=memory_index.INDEX_JOB,
            payload={"all": True},
            idempotency_key=memory_index.backfill_key(model.model, 3, 1),
        ),
        actor_id=None,
    )
    await db.commit()

    async def read_projection() -> str | None:
        ranked = await memory_recall.rank(
            db,
            context,
            memory_recall.Query.of(["repair"]),
            memory_recall.Filters(scopes="user", agent_id=None),
            embedder=model,
            query_vectors=[unit],
        )
        await db.commit()
        return ranked.fallback

    async def queued() -> list[Job]:
        return list(await db.scalars(select(Job).where(Job.state.in_(("queued", "running")))))

    assert await read_projection() == "index_incomplete"
    assert await queued() == [legacy]
    db.add(_row(record, model.model))
    legacy.state = "succeeded"
    await db.commit()
    assert await read_projection() == "index_incomplete"
    (base,) = await queued()
    assert base.payload == {"all": True} and base.id != legacy.id
    assert (await memory_index._run(_job(base.payload, job_id=base.id)))["embedded"] == 1
    base.state = "succeeded"
    await db.commit()
    assert await read_projection() is None

    for number in (1, 2):
        row = (
            await db.scalars(select(MemoryEmbedding).execution_options(populate_existing=True))
        ).one()
        assert row.vector == memory_embedder.to_bytes(unit)
        row.vector = b"corrupt"
        await db.commit()
        assert await read_projection() == "index_incomplete"
        (repair,) = await queued()
        assert repair.payload == {
            "all": True,
            "choice": base.idempotency_key,
            "repair_number": number,
        }
        assert await read_projection() == "index_incomplete"
        repair.state = "running"
        await db.commit()
        assert await read_projection() == "index_incomplete"
        assert [j.id for j in await queued()] == [repair.id]
        assert (await memory_index._run(_job(repair.payload, job_id=repair.id)))["embedded"] == 1
        repair.state = "succeeded"
        await db.commit()
        assert await read_projection() is None
        assert not await queued()

    assert embedding.await_count == 3
