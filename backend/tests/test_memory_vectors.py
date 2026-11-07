"""Memory policy plan 15, Q14: embedding models are a model kind; memory picks one."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError, ValidationError
from app.models.audit_event import AuditEvent
from app.models.job import Job
from app.models.memory import MemoryEmbedding
from app.models.model import Model
from app.models.organization import Tenant
from app.schemas.agent import AgentUpdate
from app.schemas.model import ModelCreate, ModelUpdate
from app.services import (
    memory_embedder,
    memory_index,
    memory_vectors,
    memory_writes,
    model_discovery,
    model_service,
)
from app.services.agent_binding_service import _validate_agent_binding_ids
from app.services.memory_service import MemoryShape
from tests.authz.tenant_invitation_fixture import grant_tenant_admin
from tests.authz.unit_scope_fixture import TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID, seed_agent


def _embedding(**kw: Any) -> ModelCreate:
    values: dict[str, Any] = {
        "provider": "openai_compatible",
        "model_name": "text-embedding-3-large",
        "display_name": "Embedding large",
        "kind": "embedding",
        "embedding_dimensions": 1024,
        "base_url": "http://litellm.test/v1",
    }
    values.update(kw)
    return ModelCreate(**values)


@pytest.mark.parametrize(
    ("kind", "dims", "default"),
    [("embedding", None, False), ("chat", 1024, False), ("embedding", 1024, True)],
)
def test_a_model_kind_carries_the_right_fields(kind: str, dims: int | None, default: bool) -> None:
    with pytest.raises(HTTPException) as caught:
        model_service.check_kind(kind, dims, is_default=default)
    assert caught.value.status_code == 422


@pytest.mark.asyncio
async def test_chat_pickers_never_list_or_bind_an_embedding_model(db: AsyncSession) -> None:
    _user, chat, agent = await seed_agent(db)
    embedding = await model_service.create_model(db, data=_embedding())
    await db.commit()
    context = unit_context(TEST_USER_ID)

    chat_rows = await model_service.list_models(db, context=context)
    assert [row["id"] for row in chat_rows] == [chat.id]
    embedding_rows = await model_service.list_models(db, context=context, kind="embedding")
    assert [(row["id"], row["embedding_dimensions"]) for row in embedding_rows] == [
        (embedding.id, 1024)
    ]
    assert len(await model_service.list_models(db, context=context, kind=None)) == 2
    assert await model_service.resolve_model(db, "Embedding large", strict=True) is None
    for update in (
        AgentUpdate(model_id=embedding.id),
        AgentUpdate(model_fallback_ids=[embedding.id]),
    ):
        with pytest.raises(HTTPException) as caught:
            await _validate_agent_binding_ids(db, update, context)
        assert caught.value.status_code == 400
    assert agent.model_id == chat.id


@pytest.mark.asyncio
async def test_a_model_in_use_keeps_its_kind(db: AsyncSession) -> None:
    _user, chat, _agent = await seed_agent(db)
    with pytest.raises(HTTPException) as caught:
        await model_service.update_model(
            db, model=chat, data=ModelUpdate(kind="embedding", embedding_dimensions=1024)
        )
    assert caught.value.status_code == 409


async def _memories(db: AsyncSession, count: int) -> None:
    for i in range(count):
        result = await memory_writes.write_memory(
            db,
            context=unit_context(TEST_USER_ID),
            write=memory_writes.MemoryWrite(
                scope="user", content=f"기억 {i}번 내용", shape=MemoryShape()
            ),
        )
        assert result.record is not None


async def _tenant(db: AsyncSession) -> Tenant:
    tenant = await db.get(Tenant, TEST_TENANT_ID)
    assert tenant is not None
    return tenant


@pytest.mark.asyncio
async def test_choosing_a_model_needs_the_confirmed_count_and_starts_a_reembedding(
    db: AsyncSession,
) -> None:
    await seed_agent(db)
    model = await model_service.create_model(db, data=_embedding())
    await db.commit()
    await _memories(db, 3)
    context = unit_context(TEST_USER_ID)
    tenant = await _tenant(db)

    before = await memory_vectors.status(db, tenant)
    assert before.model is None and before.total == 3 and before.embedded == 0
    assert [row.id for row in before.candidates] == [model.id]

    with pytest.raises(ValidationError) as caught:
        await memory_vectors.change(db, tenant, context, model_id=model.id, confirm_count=2)
    assert caught.value.code == "MEMORY_VECTOR_COUNT_CHANGED"
    assert memory_vectors.chosen_model_id((await _tenant(db)).settings) is None

    after = await memory_vectors.change(db, tenant, context, model_id=model.id, confirm_count=3)
    assert after.model is not None and after.model.id == model.id
    assert not after.floor_measured  # no measured floor yet: keywords only
    key = memory_index.backfill_key("text-embedding-3-large", 1024, 1)
    job = await db.scalar(select(Job).where(Job.idempotency_key == key))
    assert job is not None and job.payload == {"all": True}
    assert after.job_state == job.state

    # Choosing the same model again changes nothing; another choice is a new generation.
    again = await memory_vectors.change(db, tenant, context, model_id=model.id, confirm_count=0)
    assert memory_vectors.generation((await _tenant(db)).settings) == 1 and again.model


@pytest.mark.asyncio
async def test_turning_vectors_off_keeps_or_drops_them_and_is_audited(db: AsyncSession) -> None:
    await seed_agent(db)
    model = await model_service.create_model(db, data=_embedding())
    await db.commit()
    await _memories(db, 1)
    context = unit_context(TEST_USER_ID)
    tenant = await _tenant(db)
    await memory_vectors.change(db, tenant, context, model_id=model.id, confirm_count=1)
    record_id = await db.scalar(select(memory_writes.MemoryRecord.id))
    db.add(
        MemoryEmbedding(
            org_id=context.org_id,
            tenant_id=TEST_TENANT_ID,
            record_id=record_id,
            user_id=TEST_USER_ID,
            model="text-embedding-3-large",
            dimensions=1024,
            text_hash="h",
            vector=b"\x00\x00\x80?",
        )
    )
    await db.commit()
    assert (await memory_vectors.status(db, tenant)).embedded == 1

    kept = await memory_vectors.change(db, tenant, context, model_id=None, confirm_count=0)
    assert kept.model is None and kept.other_vectors == 1
    dropped = await memory_vectors.change(
        db, tenant, context, model_id=None, confirm_count=0, drop_vectors=True
    )
    assert dropped.other_vectors == 0
    rows = list(
        await db.scalars(
            select(AuditEvent)
            .where(AuditEvent.action == "memory.vector_model_update")
            .order_by(AuditEvent.created_at)
        )
    )
    assert [row.event_metadata for row in rows] == [
        {"model_id": str(model.id), "previous_model_id": None, "reembed": 1, "dropped_vectors": 0},
        {"model_id": None, "previous_model_id": str(model.id), "reembed": 0, "dropped_vectors": 0},
        {"model_id": None, "previous_model_id": None, "reembed": 0, "dropped_vectors": 1},
    ]
    assert all(
        (row.target_type, str(row.target_id), row.outcome)
        == ("tenant", str(TEST_TENANT_ID), "success")
        for row in rows
    )


@pytest.mark.asyncio
async def test_another_companys_model_cannot_be_chosen_or_resolved(db: AsyncSession) -> None:
    await seed_agent(db)
    other = Tenant(id=uuid.uuid4(), name="Other", slug="other", secrets_namespace="tenants/o")
    db.add(other)
    await db.flush()
    foreign = Model(
        tenant_id=other.id,
        provider="openai_compatible",
        model_name="other-embed",
        display_name="Other embed",
        kind="embedding",
        embedding_dimensions=768,
    )
    db.add(foreign)
    await db.commit()
    tenant = await _tenant(db)
    with pytest.raises(AppError) as caught:
        await memory_vectors.change(
            db, tenant, unit_context(TEST_USER_ID), model_id=foreign.id, confirm_count=0
        )
    assert caught.value.status == 404
    tenant.settings = {"memory": {"embedding_model_id": str(foreign.id)}}
    assert await memory_vectors.embedder_for(db, tenant) is None


@pytest.mark.asyncio
async def test_the_embedder_uses_the_model_and_its_providers_platform_key(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_agent(db)
    model = await model_service.create_model(db, data=_embedding(embedding_dimensions=512))
    tenant = await _tenant(db)
    tenant.settings = {"memory": {"embedding_model_id": str(model.id), "embedding_generation": 4}}
    await db.commit()
    from app.services import system_credential_resolver as resolver

    find = AsyncMock(return_value=object())
    monkeypatch.setattr(resolver.credential_service, "find_system_by_definition", find)
    monkeypatch.setattr(resolver, "open_values", AsyncMock(return_value={"api_key": "k-test"}))
    embedder = await memory_vectors.embedder_for(db, tenant)
    assert isinstance(embedder, memory_embedder.OpenAIShapedEmbedder)
    assert (embedder.model, embedder.dimensions, embedder.generation) == (
        "text-embedding-3-large",
        512,
        4,
    )
    assert embedder.base_url == "http://litellm.test/v1" and embedder.api_key == "k-test"
    find.assert_awaited_once()
    resolved = await memory_embedder.resolve_embedder(TEST_TENANT_ID)
    assert resolved is not None and resolved.dimensions == 512


@pytest.mark.asyncio
async def test_a_memory_vector_model_cannot_be_deleted(db: AsyncSession) -> None:
    await seed_agent(db)
    model = await model_service.create_model(db, data=_embedding())
    tenant = await _tenant(db)
    tenant.settings = {"memory": {"embedding_model_id": str(model.id)}}
    await db.commit()
    with pytest.raises(HTTPException) as caught:
        await model_service.ensure_model_unused(db, model.id)
    assert caught.value.status_code == 409


@pytest.mark.parametrize(
    ("kind", "names"),
    [("chat", ["gpt-5.4-mini"]), ("embedding", ["text-embedding-3-large"])],
)
@pytest.mark.asyncio
async def test_discovery_lists_one_kind(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, kind: str, names: list[str]
) -> None:
    found = [
        model_discovery.DiscoveredModel(
            model_name=name, display_name=name, provider="openai_compatible", source="manual"
        )
        for name in ("gpt-5.4-mini", "text-embedding-3-large")
    ]

    async def fake(_data: dict[str, Any]) -> list[model_discovery.DiscoveredModel]:
        return list(found)

    monkeypatch.setitem(model_discovery._DISPATCH, "openai_compatible", (fake, "openai_compatible"))  # noqa: SLF001
    monkeypatch.setattr(model_discovery, "open_values", AsyncMock(return_value={}))
    credential: Any = SimpleNamespace(definition_key="openai_compatible")
    result = await model_discovery.discover_from_credential(db, credential, kind=kind)
    assert [m.model_name for m in result] == names
    # Embedding models are only asked of providers with an OpenAI embeddings API.
    credential.definition_key = "anthropic"
    if kind == "embedding":
        assert await model_discovery.discover_from_credential(db, credential, kind=kind) == []


@pytest.mark.asyncio
async def test_vector_routes_show_status_and_need_a_company_admin_to_change(
    client: AsyncClient, db: AsyncSession
) -> None:
    await seed_agent(db)
    model = await model_service.create_model(db, data=_embedding())
    await db.commit()
    shown = await client.get("/api/me/memory-vector")
    assert shown.status_code == 200, shown.text
    body = shown.json()
    assert body["model"] is None and body["can_manage"] is False
    assert [c["id"] for c in body["candidates"]] == [str(model.id)]
    refused = await client.put(
        "/api/me/memory-vector", json={"model_id": str(model.id), "confirm_count": 0}
    )
    assert refused.status_code == 403
    await grant_tenant_admin(db, TEST_TENANT_ID, TEST_USER_ID)
    chosen = await client.put(
        "/api/me/memory-vector", json={"model_id": str(model.id), "confirm_count": 0}
    )
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["model"]["dimensions"] == 1024 and chosen.json()["can_manage"] is True
    listed = await client.get("/api/models?kind=embedding")
    assert [row["id"] for row in listed.json()] == [str(model.id)]
    assert str(model.id) not in [row["id"] for row in (await client.get("/api/models")).json()]
    assert await db.scalar(select(func.count()).select_from(Job)) == 1


@pytest.mark.asyncio
async def test_the_database_refuses_an_embedding_model_without_dimensions(db: AsyncSession) -> None:
    from sqlalchemy.exc import IntegrityError

    db.add(Model(provider="openai", model_name="e", display_name="e", kind="embedding"))
    with pytest.raises(IntegrityError):
        await db.flush()
