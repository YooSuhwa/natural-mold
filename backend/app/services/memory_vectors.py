"""A company's memory vector model (memory policy plan 15, Q14, 5.5).

The company memory policy (``tenants.settings["memory"]``) names one registered embedding
model (``models.kind = 'embedding'``) or none; none is the default for company and
personal tenants alike. Choosing a model re-embeds the company's memories in the
background (``memory.index`` ``{"all": true}``), keyword recall carries on meanwhile, and
the previous model's vectors are removed when the job finishes. Turning it off keeps the
vectors unless the person asks to remove them. The caller confirms how many memories
will be embedded again, so a change made on a stale screen is refused.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.audit_actions import MEMORY_VECTOR_MODEL_UPDATE
from app.authz.org_context import OrgContext
from app.exceptions import AppError, ValidationError
from app.jobs import service as jobs
from app.models.job import Job
from app.models.memory import MemoryEmbedding, MemoryRecord
from app.models.model import Model
from app.models.organization import Tenant
from app.services import audit_service, memory_embedder, memory_index
from app.services.system_credential_resolver import open_model_key

MODEL_KEY: Final = "embedding_model_id"
# Bumped on every change, so choosing a model again starts a new re-embedding job.
GENERATION_KEY: Final = "embedding_generation"
_DEFAULT_BASE_URL: Final = "https://api.openai.com/v1"
_INDEXED: Final = ("active", "superseded")


def _memory_settings(tenant_settings: dict[str, Any] | None) -> dict[str, Any]:
    stored = (tenant_settings or {}).get("memory")
    return dict(stored) if isinstance(stored, dict) else {}


def chosen_model_id(tenant_settings: dict[str, Any] | None) -> uuid.UUID | None:
    value = _memory_settings(tenant_settings).get(MODEL_KEY)
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


def generation(tenant_settings: dict[str, Any] | None) -> int:
    value = _memory_settings(tenant_settings).get(GENERATION_KEY)
    return value if isinstance(value, int) else 0


def _usable(tenant_id: uuid.UUID) -> Any:
    """Embedding models the company may pick: visible platform models and its own."""
    return (
        (Model.kind == "embedding")
        & Model.is_visible.is_(True)
        & or_(Model.tenant_id.is_(None), Model.tenant_id == tenant_id)
    )


async def candidates(db: AsyncSession, tenant_id: uuid.UUID) -> list[Model]:
    rows = await db.scalars(
        select(Model).where(_usable(tenant_id)).order_by(Model.display_name, Model.id)
    )
    return list(rows)


async def embedder_for(db: AsyncSession, tenant: Tenant) -> memory_embedder.MemoryEmbedder | None:
    """The chosen model with its credential: the model's default credential (a platform
    credential, or one of the company's shared ones), else the platform credential of its
    provider while the company may use platform keys."""
    model_id = chosen_model_id(tenant.settings)
    if model_id is None:
        return None
    model = await db.scalar(select(Model).where(Model.id == model_id, _usable(tenant.id)))
    if model is None or model.provider not in memory_embedder.OPENAI_SHAPED:
        return None
    api_key, key_base_url = await open_model_key(
        db, credential_id=model.default_credential_id, provider=model.provider, tenant=tenant
    )
    return memory_embedder.OpenAIShapedEmbedder(
        model=model.model_name,
        dimensions=int(model.embedding_dimensions or 0),
        api_key=api_key,
        base_url=model.base_url or key_base_url or _DEFAULT_BASE_URL,
        generation=generation(tenant.settings),
    )


async def companies_using(db: AsyncSession, model_id: uuid.UUID) -> list[uuid.UUID]:
    """Companies whose memory policy names ``model_id`` (its delete waits for them)."""
    rows = await db.execute(select(Tenant.id, Tenant.settings))
    return [tenant_id for tenant_id, stored in rows if chosen_model_id(stored) == model_id]


@dataclass(frozen=True, slots=True)
class VectorModel:
    id: uuid.UUID
    display_name: str
    model_name: str
    provider: str
    dimensions: int
    source: str  # platform | company

    @classmethod
    def of(cls, model: Model) -> VectorModel:
        return cls(
            id=model.id,
            display_name=model.display_name,
            model_name=model.model_name,
            provider=model.provider,
            dimensions=int(model.embedding_dimensions or 0),
            source="platform" if model.tenant_id is None else "company",
        )


@dataclass(frozen=True, slots=True)
class VectorStatus:
    model: VectorModel | None
    candidates: list[VectorModel]
    total: int  # memories recall may embed (active and replaced ones)
    embedded: int  # of those, with a vector of the chosen model
    other_vectors: int  # vectors of any other model (removed after a re-embedding)
    job_state: str | None
    job_progress: int | None
    floor_measured: bool


async def _counts(
    db: AsyncSession, tenant_id: uuid.UUID, model: Model | None
) -> tuple[int, int, int]:
    total = await db.scalar(
        select(func.count())
        .select_from(MemoryRecord)
        .where(MemoryRecord.tenant_id == tenant_id, MemoryRecord.status.in_(_INDEXED))
    )
    vectors = (
        select(func.count())
        .select_from(MemoryEmbedding)
        .where(MemoryEmbedding.tenant_id == tenant_id)
    )
    if model is None:
        return int(total or 0), 0, int(await db.scalar(vectors) or 0)
    same = (MemoryEmbedding.model == model.model_name) & (
        MemoryEmbedding.dimensions == model.embedding_dimensions
    )
    embedded = await db.scalar(
        select(func.count())
        .select_from(MemoryEmbedding)
        .join(MemoryRecord, MemoryRecord.id == MemoryEmbedding.record_id)
        .where(
            MemoryEmbedding.tenant_id == tenant_id,
            same,
            MemoryRecord.status.in_(_INDEXED),
        )
    )
    other = await db.scalar(vectors.where(~same))
    return int(total or 0), int(embedded or 0), int(other or 0)


async def status(db: AsyncSession, tenant: Tenant) -> VectorStatus:
    from app.services.memory_recall import VECTOR_FLOORS

    model_id = chosen_model_id(tenant.settings)
    model = (
        await db.scalar(select(Model).where(Model.id == model_id, _usable(tenant.id)))
        if model_id is not None
        else None
    )
    total, embedded, other = await _counts(db, tenant.id, model)
    job = await db.scalar(
        select(Job)
        .where(
            Job.tenant_id == tenant.id,
            Job.type == memory_index.INDEX_JOB,
            Job.idempotency_key.like("all:%"),
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    return VectorStatus(
        model=VectorModel.of(model) if model is not None else None,
        candidates=[VectorModel.of(row) for row in await candidates(db, tenant.id)],
        total=total,
        embedded=embedded,
        other_vectors=other,
        job_state=job.state if job is not None else None,
        job_progress=job.progress if job is not None else None,
        floor_measured=model is not None
        and f"{model.model_name}@{model.embedding_dimensions}" in VECTOR_FLOORS,
    )


async def change(
    db: AsyncSession,
    tenant: Tenant,
    context: OrgContext,
    *,
    model_id: uuid.UUID | None,
    confirm_count: int,
    drop_vectors: bool = False,
) -> VectorStatus:
    """Choose a model (re-embed ``confirm_count`` memories) or turn vectors off."""
    previous = chosen_model_id(tenant.settings)
    model = None
    if model_id is not None:
        model = await db.scalar(select(Model).where(Model.id == model_id, _usable(tenant.id)))
        if model is None:
            raise AppError(
                code="MEMORY_VECTOR_MODEL_NOT_FOUND",
                message="이 회사에서 쓸 수 있는 임베딩 모델이 아닙니다",
                status=404,
            )
    total, embedded, _other = await _counts(db, tenant.id, model)
    pending = total - embedded if model is not None else 0
    if model_id != previous and confirm_count != pending:
        raise ValidationError(
            "MEMORY_VECTOR_COUNT_CHANGED",
            f"다시 임베딩할 기억 수가 바뀌었습니다({pending}개). 새로 확인해 주세요",
        )
    dropped = 0
    if model_id is None and drop_vectors:
        result = await db.execute(
            delete(MemoryEmbedding).where(MemoryEmbedding.tenant_id == tenant.id)
        )
        dropped = int(getattr(result, "rowcount", 0) or 0)
    if model_id != previous:
        memory = _memory_settings(tenant.settings)
        memory[MODEL_KEY] = str(model_id) if model_id is not None else None
        memory[GENERATION_KEY] = generation(tenant.settings) + 1
        tenant.settings = {**(tenant.settings or {}), "memory": memory}
        if model is not None:
            await jobs.enqueue(
                db,
                context,
                jobs.EnqueueRequest(
                    type=memory_index.INDEX_JOB,
                    payload={"all": True},
                    idempotency_key=memory_index.backfill_key(
                        model.model_name,
                        int(model.embedding_dimensions or 0),
                        memory[GENERATION_KEY],
                    ),
                ),
                actor_id=context.user_id,
            )
    if model_id != previous or dropped:
        await audit_service.record_event(
            db,
            actor_type="user",
            actor_user_id=context.user_id,
            owner_user_id=None,
            action=MEMORY_VECTOR_MODEL_UPDATE,
            target_type="tenant",
            target_id=tenant.id,
            outcome="success",
            metadata={
                "model_id": str(model_id) if model_id else None,
                "previous_model_id": str(previous) if previous else None,
                "reembed": pending,
                "dropped_vectors": dropped,
            },
            tenant_id=tenant.id,
            org_id=context.org_id,
        )
    await db.commit()
    await db.refresh(tenant)
    return await status(db, tenant)


__all__ = [
    "GENERATION_KEY",
    "MODEL_KEY",
    "VectorModel",
    "VectorStatus",
    "candidates",
    "change",
    "chosen_model_id",
    "companies_using",
    "embedder_for",
    "generation",
    "status",
]
