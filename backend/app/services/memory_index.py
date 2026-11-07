"""Keyword terms and vectors for memory recall (memory policy plan 15, 5.4-11 and 5.5).

A saved memory gets its Korean terms in the write transaction (Nori within its time limit,
else the suffix list) and a ``memory.index`` job. The job embeds the memory with the
``embedding`` model and moves suffix-list terms to Nori once OpenSearch answers. A
``{"all": true}`` job does the same for a whole company: recall enqueues it, once per
model, when it finds memories without a vector of the model in use (new model, or memories
saved before vectors existed), and it removes vectors of other models when done.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Final

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.config import settings
from app.jobs import registry
from app.jobs import service as jobs
from app.jobs.context import JobContext
from app.models.memory import MemoryEmbedding, MemoryRecord
from app.services import memory_embedder
from app.text import korean_terms

logger = logging.getLogger(__name__)

INDEX_JOB: Final = "memory.index"
_BATCH: Final = 64
# Memories recall reads: active ones, and superseded ones for ``include_history``.
_INDEXED_STATUSES: Final = ("active", "superseded")


async def set_terms(record: MemoryRecord) -> None:
    terms = await korean_terms.korean_terms(record.content)
    record.search_tokens = terms.joined()
    record.search_tokens_analyzer = terms.analyzer


async def on_saved(db: AsyncSession, context: OrgContext, record: MemoryRecord) -> None:
    """Terms now, vector and Nori terms in the background (called before the commit)."""
    await set_terms(record)
    digest = memory_embedder.text_hash(record.content)[:16]
    await jobs.enqueue(
        db,
        context,
        jobs.EnqueueRequest(
            type=INDEX_JOB,
            payload={"record_ids": [str(record.id)]},
            resource_id=str(record.id),
            idempotency_key=f"{record.id}:{digest}",
        ),
        actor_id=None,
    )


async def enqueue_backfill(
    db: AsyncSession, context: OrgContext, embedder: memory_embedder.MemoryEmbedder
) -> None:
    await jobs.enqueue(
        db,
        context,
        jobs.EnqueueRequest(
            type=INDEX_JOB,
            payload={"all": True},
            idempotency_key=f"all:{embedder.model}@{embedder.dimensions}",
        ),
        actor_id=None,
    )


async def index_records(
    db: AsyncSession,
    records: list[MemoryRecord],
    embedder: memory_embedder.MemoryEmbedder | None,
) -> dict[str, int]:
    """Embed records lacking a current vector, and move suffix terms to Nori if it answers."""
    retokenized = embedded = 0
    nori_up = bool(settings.opensearch_url)
    for record in records:
        if nori_up and record.search_tokens_analyzer != korean_terms.NORI_ANALYZER:
            terms = await korean_terms.nori_terms(record.content, timeout_ms=2000)
            if terms is None:
                nori_up = False
            else:
                record.search_tokens = " ".join(terms)
                record.search_tokens_analyzer = korean_terms.NORI_ANALYZER
                retokenized += 1
    if embedder is not None and records:
        ids = [record.id for record in records]
        current = {
            row.record_id: row
            for row in await db.scalars(
                select(MemoryEmbedding).where(
                    MemoryEmbedding.record_id.in_(ids),
                    MemoryEmbedding.model == embedder.model,
                    MemoryEmbedding.dimensions == embedder.dimensions,
                )
            )
        }
        stale = [
            record
            for record in records
            if (row := current.get(record.id)) is None
            or row.text_hash != memory_embedder.text_hash(record.content)
        ]
        for start in range(0, len(stale), _BATCH):
            chunk = stale[start : start + _BATCH]
            vectors = await embedder.embed([r.content for r in chunk], seconds=30.0)
            for record, vector in zip(chunk, vectors, strict=True):
                row = current.get(record.id)
                if row is None:
                    db.add(
                        MemoryEmbedding(
                            org_id=record.org_id,
                            tenant_id=record.tenant_id,
                            record_id=record.id,
                            user_id=record.user_id,
                            model=embedder.model,
                            dimensions=embedder.dimensions,
                            text_hash=memory_embedder.text_hash(record.content),
                            vector=memory_embedder.to_bytes(vector),
                        )
                    )
                else:
                    row.text_hash = memory_embedder.text_hash(record.content)
                    row.vector = memory_embedder.to_bytes(vector)
                embedded += 1
    return {"retokenized": retokenized, "embedded": embedded}


async def _run(context: JobContext) -> dict[str, Any]:
    embedder = await memory_embedder.resolve_embedder(context.tenant_id)
    totals = {"retokenized": 0, "embedded": 0}
    async with context.session() as db:
        query = select(MemoryRecord).where(MemoryRecord.status.in_(_INDEXED_STATUSES))
        if not context.payload.get("all"):
            ids = [uuid.UUID(str(value)) for value in context.payload.get("record_ids", [])]
            query = query.where(MemoryRecord.id.in_(ids))
        records = list(await db.scalars(query.order_by(MemoryRecord.created_at)))
        for start in range(0, len(records), _BATCH):
            context.check_canceled()
            done = await index_records(db, records[start : start + _BATCH], embedder)
            for key, value in done.items():
                totals[key] += value
            await db.commit()
            await context.progress(min(99, round(100 * (start + _BATCH) / len(records))))
        if context.payload.get("all") and embedder is not None:
            # One model at a time (5.5): vectors of any other model are never compared.
            await db.execute(
                delete(MemoryEmbedding).where(
                    (MemoryEmbedding.model != embedder.model)
                    | (MemoryEmbedding.dimensions != embedder.dimensions)
                )
            )
            await db.commit()
    return totals


def register_jobs() -> None:
    if registry.get(INDEX_JOB) is None:
        registry.register(registry.JobType(INDEX_JOB, _run, lease_seconds=300))


__all__ = [
    "INDEX_JOB",
    "enqueue_backfill",
    "index_records",
    "on_saved",
    "register_jobs",
    "set_terms",
]
