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
from dataclasses import dataclass, replace
from typing import Any, Final

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.audit_actions import MEMORY_VECTOR_MODEL_UPDATE
from app.authz.company_catalog import company_tier
from app.authz.org_context import OrgContext
from app.exceptions import AppError, ValidationError
from app.jobs import service as jobs
from app.models.job import Job
from app.models.memory import MemoryEmbedding, MemoryRecord
from app.models.model import Model
from app.models.organization import Tenant
from app.services import audit_service, memory_embedder, memory_index, memory_recall_policy
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
        & Model.provider.in_(memory_embedder.OPENAI_SHAPED)
        & Model.is_visible.is_(True)
        & or_(Model.tenant_id.is_(None), Model.tenant_id == tenant_id)
    )


async def candidates(db: AsyncSession, tenant_id: uuid.UUID) -> list[Model]:
    rows = await db.scalars(
        select(Model).where(_usable(tenant_id)).order_by(Model.display_name, Model.id)
    )
    tenant = await db.get(Tenant, tenant_id)
    return [model for model in rows if tenant and _company_offers(tenant, model)]


def _company_offers(tenant: Tenant, model: Model) -> bool:
    source = "platform" if model.tenant_id is None else "company"
    return tenant.status == "active" and company_tier(tenant).offers(
        "model", model.id, source, available=model.is_visible
    )


def bind_embedder(
    tenant: Tenant,
    model: Model,
    *,
    api_key: str | None,
    key_base_url: str | None,
    org_id: uuid.UUID | None = None,
) -> memory_embedder.OpenAIShapedEmbedder | None:
    """Bind the effective credential endpoint, report and exact tenant trial selection."""
    base_url = model.base_url or key_base_url
    if not base_url:
        base_url = {"openai": _DEFAULT_BASE_URL, "openrouter": "https://openrouter.ai/api/v1"}.get(
            model.provider
        )
    if not base_url or model.provider not in memory_embedder.OPENAI_SHAPED:
        return None
    result = memory_embedder.OpenAIShapedEmbedder(
        model=model.model_name,
        dimensions=int(model.embedding_dimensions or 0),
        api_key=api_key,
        base_url=base_url.rstrip("/"),
        generation=generation(tenant.settings),
        provider=model.provider,
        registry_id=model.id,
    )
    floor = memory_recall_policy.passing_floor(
        tenant_id=tenant.id,
        model_id=model.id,
        provider=model.provider,
        base_url=base_url,
        model_name=model.model_name,
        dimensions=result.dimensions,
    )
    trial = _trial_settings(tenant, org_id)
    mode: memory_recall_policy.RecallMode = "off"
    if floor is not None and trial.get("qualification") == _qualification(result, floor):
        mode = memory_recall_policy.requested_mode(trial)
    return replace(result, recall_mode=mode, recall_floor=floor)


def _trial_settings(tenant: Tenant, org_id: uuid.UUID | None) -> dict[str, Any]:
    trials = _memory_settings(tenant.settings).get("recall_trials")
    trial = trials.get(str(org_id)) if isinstance(trials, dict) and org_id else None
    return trial if isinstance(trial, dict) else {}


def _qualification(embedder: memory_embedder.OpenAIShapedEmbedder, floor: float) -> str:
    model_key = memory_embedder.vector_model_key(embedder)
    return f"{memory_recall_policy.CORPUS_SHA256}:{model_key}:{floor!r}"


async def _configured_model(
    db: AsyncSession, tenant: Tenant, model: Model | None
) -> memory_embedder.OpenAIShapedEmbedder | None:
    if model is None or not _company_offers(tenant, model):
        return None
    try:
        api_key, key_base_url = await open_model_key(
            db, credential_id=model.default_credential_id, provider=model.provider, tenant=tenant
        )
    except Exception:  # noqa: BLE001 - an unavailable credential leaves recall on keywords
        return None
    return bind_embedder(tenant, model, api_key=api_key, key_base_url=key_base_url)


async def embedder_for(
    db: AsyncSession, tenant: Tenant, *, org_id: uuid.UUID | None = None
) -> memory_embedder.OpenAIShapedEmbedder | None:
    """The chosen model with its credential: the model's default credential (a platform
    credential, or one of the company's shared ones), else the platform credential of its
    provider while the company may use platform keys."""
    model_id = chosen_model_id(tenant.settings)
    if model_id is None:
        return None
    if org_id is not None:
        from app.services.memory_embedding_access import org_embedder_for

        return await org_embedder_for(
            db, tenant.id, org_id, model_id, generation=generation(tenant.settings)
        )
    model = await db.scalar(select(Model).where(Model.id == model_id, _usable(tenant.id)))
    return await _configured_model(db, tenant, model)


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
    recall_mode: memory_recall_policy.RecallMode
    quality_floor: float | None


async def _counts(
    db: AsyncSession, tenant_id: uuid.UUID, embedder: memory_embedder.OpenAIShapedEmbedder | None
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
    if embedder is None:
        return int(total or 0), 0, int(await db.scalar(vectors) or 0)
    same = (MemoryEmbedding.model == memory_embedder.vector_model_key(embedder)) & (
        MemoryEmbedding.dimensions == embedder.dimensions
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


async def status(
    db: AsyncSession, tenant: Tenant, *, org_id: uuid.UUID | None = None
) -> VectorStatus:
    model_id = chosen_model_id(tenant.settings)
    model = (
        await db.scalar(select(Model).where(Model.id == model_id, _usable(tenant.id)))
        if model_id is not None
        else None
    )
    embedder = await _configured_model(db, tenant, model)
    total, embedded, other = await _counts(db, tenant.id, embedder)
    if org_id is not None:
        embedder = await embedder_for(db, tenant, org_id=org_id)
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
        floor_measured=embedder is not None and embedder.recall_floor is not None,
        recall_mode=embedder.recall_mode if embedder else "off",
        quality_floor=embedder.recall_floor if embedder else None,
    )


async def change(
    db: AsyncSession,
    tenant: Tenant,
    context: OrgContext,
    *,
    model_id: uuid.UUID | None,
    confirm_count: int,
    drop_vectors: bool = False,
    recall_mode: memory_recall_policy.RecallMode | None = None,
) -> VectorStatus:
    """Choose a model (re-embed ``confirm_count`` memories) or turn vectors off."""
    await db.refresh(tenant, with_for_update=True)
    previous = chosen_model_id(tenant.settings)
    if recall_mode is not None and model_id != previous:
        raise ValidationError(
            "MEMORY_VECTOR_SELECTION_CHANGED",
            "선택한 모델이 바뀌었습니다. 현재 설정을 확인해 주세요",
        )
    model = None
    if model_id is not None:
        model = await db.scalar(select(Model).where(Model.id == model_id, _usable(tenant.id)))
        # Withdrawing an existing opt-in remains possible after catalog revocation.
        clearing_trial = model_id == previous and recall_mode == "off"
        if (model is None or not _company_offers(tenant, model)) and not clearing_trial:
            raise AppError(
                code="MEMORY_VECTOR_MODEL_NOT_FOUND",
                message="이 회사에서 쓸 수 있는 임베딩 모델이 아닙니다",
                status=404,
            )
    embedder = await _configured_model(db, tenant, model)
    total, embedded, _other = await _counts(db, tenant.id, embedder)
    pending = total - embedded if model is not None else 0
    if model_id != previous and confirm_count != pending:
        raise ValidationError(
            "MEMORY_VECTOR_COUNT_CHANGED",
            f"다시 임베딩할 기억 수가 바뀌었습니다({pending}개). 새로 확인해 주세요",
        )
    memory = _memory_settings(tenant.settings)
    trial = _trial_settings(tenant, context.org_id)
    previous_mode = memory_recall_policy.requested_mode(trial)
    mode_changed = False
    trials = memory.get("recall_trials", {})
    if model_id != previous:
        memory.pop("recall_trials", None)
    elif recall_mode is not None:
        qualified = None
        if recall_mode in ("shadow", "serve"):
            approved = await embedder_for(db, tenant, org_id=context.org_id)
            if approved is None or approved.recall_floor is None:
                raise ValidationError(
                    "MEMORY_RECALL_NOT_QUALIFIED",
                    "이 조직에서 사용할 수 있고 독립 평가를 통과한 모델이 필요합니다",
                )
            qualified = _qualification(approved, approved.recall_floor)
        mode_changed = previous_mode != recall_mode or trial.get("qualification") != qualified
        if mode_changed:
            trials = dict(trials) if isinstance(trials, dict) else {}
            if qualified is None:
                trials.pop(str(context.org_id), None)
            else:
                trials[str(context.org_id)] = {
                    "recall_mode": recall_mode,
                    "qualification": qualified,
                }
            memory["recall_trials"] = trials
    dropped = 0
    if model_id is None and drop_vectors:
        result = await db.execute(
            delete(MemoryEmbedding).where(MemoryEmbedding.tenant_id == tenant.id)
        )
        dropped = int(getattr(result, "rowcount", 0) or 0)
    if model_id != previous:
        memory[MODEL_KEY] = str(model_id) if model_id is not None else None
        memory[GENERATION_KEY] = generation(tenant.settings) + 1
        if embedder is not None:
            await jobs.enqueue(
                db,
                context,
                jobs.EnqueueRequest(
                    type=memory_index.INDEX_JOB,
                    payload={"all": True},
                    idempotency_key=memory_index.backfill_key(
                        memory_embedder.vector_model_key(embedder),
                        embedder.dimensions,
                        memory[GENERATION_KEY],
                    ),
                ),
                actor_id=context.user_id,
            )
    if model_id != previous or mode_changed:
        tenant.settings = {**(tenant.settings or {}), "memory": memory}
    if model_id != previous or dropped or mode_changed:
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
                **(
                    {"recall_mode": recall_mode, "previous_recall_mode": previous_mode}
                    if mode_changed
                    else {}
                ),
            },
            tenant_id=tenant.id,
            org_id=context.org_id,
        )
    await db.commit()
    await db.refresh(tenant)
    return await status(db, tenant, org_id=context.org_id)


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
