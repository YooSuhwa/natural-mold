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
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.memory_agent_access import shared_memory_agent_ids
from app.authz.memory_scope import recallable_memory_record_predicate
from app.authz.org_context import OrgContext
from app.authz.scoping import same_private_scope
from app.config import settings
from app.jobs import registry
from app.jobs import service as jobs
from app.jobs.context import JobContext
from app.models.conversation import Conversation
from app.models.job import TERMINAL_STATES, Job
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
    from app.services import memory_consolidation  # it imports this module

    await memory_consolidation.maybe_schedule(db, context, record)
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


def backfill_key(model: str, dimensions: int, generation: int) -> str:
    """One company-wide job per model choice (a new choice is a new generation)."""
    return f"all:{model}@{dimensions}:{generation}"


def _choice(embedder: memory_embedder.MemoryEmbedder) -> str:
    return backfill_key(
        memory_embedder.vector_model_key(embedder),
        embedder.dimensions,
        getattr(embedder, "generation", 0),
    )


async def enqueue_backfill(
    db: AsyncSession, context: OrgContext, embedder: memory_embedder.MemoryEmbedder
) -> None:
    key = _choice(embedder)
    legacy = backfill_key(embedder.model, embedder.dimensions, getattr(embedder, "generation", 0))
    choices = dict(
        (
            await db.execute(
                select(Job.idempotency_key, Job.state).where(
                    Job.tenant_id == context.tenant_id,
                    Job.type == INDEX_JOB,
                    Job.idempotency_key.in_({key, legacy}),
                )
            )
        )
        .tuples()
        .all()
    )
    if any(state not in TERMINAL_STATES for state in choices.values()):
        return
    job_key = key
    payload: dict[str, Any] = {"all": True}
    group = None
    if key in choices:
        # Recall observed a missing or invalid vector after an earlier pass. Keep the
        # completed job's audit history and coalesce concurrent requests on the next number.
        group = memory_embedder.text_hash(key)
        repair_number = Job.payload["repair_number"].as_integer()
        latest = (
            await db.execute(
                select(Job.state, repair_number)
                .where(
                    Job.tenant_id == context.tenant_id,
                    Job.type == INDEX_JOB,
                    Job.resource_id == group,
                )
                .order_by(repair_number.desc())
                .limit(1)
            )
        ).first()
        if latest is not None and latest[0] not in TERMINAL_STATES:
            return
        number = latest[1] + 1 if latest is not None else 1
        payload.update(choice=key, repair_number=number)
        job_key = f"all:repair:{group}:{number}"

    # A prepared lease owns the parent transaction. A duplicate is harmless and must
    # not enter an ORM savepoint, which would invalidate that transaction's lock proof.
    insert = sqlite_insert if db.get_bind().dialect.name == "sqlite" else postgres_insert
    statement = insert(Job).values(
        tenant_id=context.tenant_id,
        org_id=context.org_id,
        type=INDEX_JOB,
        actor_id=None,
        payload=payload,
        resource_id=group,
        idempotency_key=job_key,
        run_after=jobs.now(),
    )
    await db.execute(
        statement.on_conflict_do_nothing(index_elements=["tenant_id", "type", "idempotency_key"])
    )


async def _still_selected(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    org_id: uuid.UUID,
    expected: memory_embedder.MemoryEmbedder,
    *,
    lock: bool = False,
) -> memory_embedder.MemoryEmbedder | None:
    """Check the current org permission, endpoint and model-choice generation.

    Hold the policy rows through publication/cleanup, never through an embedding request.
    """
    if isinstance(expected, memory_embedder.OpenAIShapedEmbedder) and expected.registry_id:
        from app.services.memory_embedding_access import org_embedder_for

        current = await org_embedder_for(
            db, tenant_id, org_id, expected.registry_id, generation=expected.generation, lock=lock
        )
    else:
        current = await memory_embedder.resolve_embedder(tenant_id, org_id=org_id)
    return current if current is not None and _choice(current) == _choice(expected) else None


async def _vector_rows(
    db: AsyncSession,
    records: list[MemoryRecord],
    model_key: str,
    dimensions: int,
) -> dict[uuid.UUID, MemoryEmbedding]:
    rows = await db.scalars(
        select(MemoryEmbedding)
        .where(
            MemoryEmbedding.tenant_id == records[0].tenant_id,
            MemoryEmbedding.org_id == records[0].org_id,
            MemoryEmbedding.record_id.in_([record.id for record in records]),
            MemoryEmbedding.model == model_key,
            MemoryEmbedding.dimensions == dimensions,
        )
        .execution_options(populate_existing=True)
    )
    return {row.record_id: row for row in rows}


async def _eligible_records(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    org_id: uuid.UUID,
    ids: list[uuid.UUID],
    *,
    lock: bool = False,
) -> list[MemoryRecord]:
    """Check current owner, surviving parents, approval and validity before reading content."""
    identities = await db.execute(
        select(MemoryRecord.id, MemoryRecord.user_id, MemoryRecord.agent_id, Conversation.agent_id)
        .outerjoin(
            Conversation,
            and_(
                Conversation.id == MemoryRecord.source_conversation_id,
                same_private_scope(Conversation, MemoryRecord),
            ),
        )
        .where(
            MemoryRecord.id.in_(ids),
            MemoryRecord.tenant_id == tenant_id,
            MemoryRecord.org_id == org_id,
        )
    )
    groups: dict[tuple[uuid.UUID, frozenset[uuid.UUID]], list[uuid.UUID]] = {}
    for record_id, owner, agent_id, source_agent in identities:
        parents = frozenset(value for value in (agent_id, source_agent) if value is not None)
        groups.setdefault((owner, parents), []).append(record_id)
    if not groups:
        return []
    predicates = []
    for (owner, parents), record_ids in groups.items():
        context = OrgContext(tenant_id=tenant_id, org_id=org_id, user_id=owner)
        shared: set[uuid.UUID] = set()
        for parent in parents:
            shared.update(await shared_memory_agent_ids(db, parent, context))
        predicates.append(
            MemoryRecord.id.in_(record_ids) & recallable_memory_record_predicate(context, shared)
        )
    query = (
        select(MemoryRecord)
        .where(
            or_(*predicates),
            MemoryRecord.status.in_(_INDEXED_STATUSES),
            or_(
                MemoryRecord.valid_until.is_(None),
                MemoryRecord.valid_until > datetime.now(UTC).replace(tzinfo=None),
            ),
            or_(
                MemoryRecord.scope == "user",
                and_(MemoryRecord.scope == "agent", MemoryRecord.agent_id.is_not(None)),
            ),
        )
        .order_by(MemoryRecord.id)
        .execution_options(populate_existing=True)
    )
    if lock:
        query = query.with_for_update()
    return list(await db.scalars(query))


async def _embed_changes(
    db: AsyncSession, records: list[MemoryRecord], embedder: memory_embedder.MemoryEmbedder
) -> dict[uuid.UUID, tuple[str, bytes]]:
    tenant_id, org_id = records[0].tenant_id, records[0].org_id
    model_key = memory_embedder.vector_model_key(embedder)
    width = embedder.dimensions
    current = await _vector_rows(db, records, model_key, width)
    digests = {r.id: memory_embedder.text_hash(r.content) for r in records}
    stale = [
        r.id
        for r in records
        if (row := current.get(r.id)) is None
        or row.text_hash != digests[r.id]
        or len(row.vector) != width * 4
        or not memory_embedder.valid_vector(memory_embedder.from_bytes(row.vector), width)
    ]
    updates: dict[uuid.UUID, tuple[str, bytes]] = {}
    for start in range(0, len(stale), _BATCH):
        authorized = await _still_selected(db, tenant_id, org_id, embedder)
        if authorized is None:
            break
        live = await _eligible_records(db, tenant_id, org_id, stale[start : start + _BATCH])
        chunk = [r for r in live if memory_embedder.text_hash(r.content) == digests[r.id]]
        if not chunk:
            continue
        vectors = await authorized.embed([r.content for r in chunk], seconds=30.0)
        for record, vector in zip(chunk, vectors, strict=True):
            updates[record.id] = digests[record.id], memory_embedder.to_bytes(vector)
    return updates


async def index_records(
    db: AsyncSession,
    records: list[MemoryRecord],
    embedder: memory_embedder.MemoryEmbedder | None,
) -> dict[str, int]:
    """Index one organization's memories in batches after refreshing the selected policy."""
    if not records:
        return {"retokenized": 0, "embedded": 0}
    tenant_id, org_id = records[0].tenant_id, records[0].org_id
    if any(r.tenant_id != tenant_id or r.org_id != org_id for r in records):
        raise ValueError("index batches must belong to one organization")
    token_updates: dict[uuid.UUID, tuple[str, str]] = {}
    if settings.opensearch_url:
        for record in records:
            if record.search_tokens_analyzer == korean_terms.NORI_ANALYZER:
                continue
            live = await _eligible_records(db, tenant_id, org_id, [record.id])
            if not live:
                continue
            content = live[0].content
            terms = await korean_terms.nori_terms(content, timeout_ms=2000)
            if terms is None:
                break
            token_updates[record.id] = (memory_embedder.text_hash(content), " ".join(terms))
    vectors = await _embed_changes(db, records, embedder) if embedder is not None else {}
    done = {"retokenized": 0, "embedded": 0}
    changed = list(token_updates.keys() | vectors.keys())
    if not changed:
        return done

    # Re-read under lock after all outbound requests, including concurrent edits/deletions.
    live = await _eligible_records(db, tenant_id, org_id, changed, lock=True)
    if not live:
        return done
    if vectors and embedder is not None:
        current = await _still_selected(db, tenant_id, org_id, embedder, lock=True)
        if current is not None:
            key = memory_embedder.vector_model_key(current)
            rows = await _vector_rows(db, live, key, current.dimensions)
            for record in live:
                update = vectors.get(record.id)
                if update is None or memory_embedder.text_hash(record.content) != update[0]:
                    continue
                row = rows.get(record.id)
                if row is None:
                    row = MemoryEmbedding(
                        org_id=org_id,
                        tenant_id=tenant_id,
                        record_id=record.id,
                        user_id=record.user_id,
                        model=key,
                        dimensions=current.dimensions,
                    )
                    db.add(row)
                row.text_hash, row.vector = update
                done["embedded"] += 1
    for record in live:
        tokens = token_updates.get(record.id)
        if tokens is not None and memory_embedder.text_hash(record.content) == tokens[0]:
            record.search_tokens = tokens[1]
            record.search_tokens_analyzer = korean_terms.NORI_ANALYZER
            done["retokenized"] += 1
    return done


async def _run(context: JobContext) -> dict[str, Any]:
    totals = {"retokenized": 0, "embedded": 0}
    whole_company = bool(context.payload.get("all"))
    async with context.session() as db:
        query = select(MemoryRecord.org_id, MemoryRecord.id).where(
            MemoryRecord.tenant_id == context.tenant_id, MemoryRecord.status.in_(_INDEXED_STATUSES)
        )
        if not whole_company:
            ids = [uuid.UUID(str(value)) for value in context.payload.get("record_ids", [])]
            query = query.where(MemoryRecord.org_id == context.org_id, MemoryRecord.id.in_(ids))
        by_org: dict[uuid.UUID, list[uuid.UUID]] = {}
        for org_id, record_id in await db.execute(query.order_by(MemoryRecord.id)):
            by_org.setdefault(org_id, []).append(record_id)
        job_key = None
        if whole_company:
            job_key = await db.scalar(
                select(Job.idempotency_key).where(
                    Job.tenant_id == context.tenant_id,
                    Job.org_id == context.org_id,
                    Job.id == context.job_id,
                    Job.type == INDEX_JOB,
                )
            )
            for org_id in await db.scalars(
                select(MemoryEmbedding.org_id)
                .where(MemoryEmbedding.tenant_id == context.tenant_id)
                .distinct()
            ):
                by_org.setdefault(org_id, [])

        choice = context.payload.get("choice")
        if job_key and job_key.startswith("all:v2:"):
            choice = job_key
        total = sum(len(ids) for ids in by_org.values())
        processed = 0
        for org_id, ids in by_org.items():
            expected = await memory_embedder.resolve_embedder(context.tenant_id, org_id=org_id)
            if choice and (expected is None or _choice(expected) != choice):
                continue
            for start in range(0, len(ids), _BATCH):
                context.check_canceled()
                record_ids = ids[start : start + _BATCH]
                records = await _eligible_records(db, context.tenant_id, org_id, record_ids)
                done = await index_records(db, records, expected)
                for key, value in done.items():
                    totals[key] += value
                await db.commit()
                processed += len(record_ids)
                await context.progress(min(99, round(100 * processed / total)))
            if whole_company and expected is not None:
                context.check_canceled()
                if await _still_selected(db, context.tenant_id, org_id, expected, lock=True):
                    await db.execute(
                        delete(MemoryEmbedding).where(
                            MemoryEmbedding.tenant_id == context.tenant_id,
                            MemoryEmbedding.org_id == org_id,
                            (MemoryEmbedding.model != memory_embedder.vector_model_key(expected))
                            | (MemoryEmbedding.dimensions != expected.dimensions),
                        )
                    )
                await db.commit()
    return totals


def register_jobs() -> None:
    if registry.get(INDEX_JOB) is None:
        registry.register(registry.JobType(INDEX_JOB, _run, lease_seconds=300))


__all__ = [
    "INDEX_JOB",
    "backfill_key",
    "enqueue_backfill",
    "index_records",
    "on_saved",
    "register_jobs",
    "set_terms",
]
