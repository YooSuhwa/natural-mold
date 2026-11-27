"""Bounded comparisons of the delivered keyword block with a policy-approved hybrid block.

Live requests have no gold relevance labels. Selection overlap, rendered token estimates
and wall time are observable here; the independent labelled evaluation measures quality.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import deque
from contextvars import Context
from datetime import UTC, datetime
from functools import lru_cache
from uuid import UUID

from sqlalchemy import select

from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.config import settings
from app.database import bind_tenant
from app.models.conversation_run import ConversationRun
from app.services import memory_embedder, memory_recall, memory_service
from app.services.memory_digest import memory_digest

logger = logging.getLogger(__name__)
# No queue: overload only drops optional comparisons. No request waits for a slot.
_MAX_TASKS = 4
_tasks: set[asyncio.Task[None]] = set()


@lru_cache(maxsize=128)
def _recent_latencies(trial_id: str) -> deque[float]:
    """Last 256 completed comparisons for each of the 128 most recent trials per worker."""
    return deque(maxlen=256)


def _log(status: str, trial_id: str, **metrics: int | float) -> None:
    fields = {"status": status, "trial_id": trial_id, **metrics}
    logger.info("memory recall shadow %s", json.dumps(fields, separators=(",", ":")), extra=fields)


def _trial(embedder: memory_embedder.MemoryEmbedder | None) -> tuple[str, int, float] | None:
    if (
        isinstance(embedder, memory_embedder.OpenAIShapedEmbedder)
        and embedder.recall_mode == "shadow"
        and embedder.registry_id is not None
        and embedder.recall_floor is not None
        and 0 < embedder.recall_floor <= 1
    ):
        return (
            memory_embedder.vector_model_key(embedder),
            embedder.generation,
            embedder.recall_floor,
        )
    return None


def schedule(
    sessions: memory_recall.SessionFactory,
    context: OrgContext,
    root_id: UUID,
    filters: memory_recall.Filters,
    query: memory_recall.Query,
    embedder: memory_embedder.MemoryEmbedder | None,
    delivered: memory_recall.RunRecall,
) -> None:
    """Snapshot just the actual selection and model identity after the keyword run commits."""
    trial = _trial(embedder)
    if trial is None:
        return
    # The model key includes the registry, provider, endpoint, name and dimensions.
    # Hash the exact trial before the text-normalizing, tenant-keyed digest. Workers
    # using the same active master key can aggregate the same trial's observations.
    identity = json.dumps((str(context.org_id), *trial), separators=(",", ":"))
    trial_id = memory_digest(
        context.tenant_id, "recall-shadow-v1-" + memory_embedder.text_hash(identity)
    )
    if len(_tasks) >= _MAX_TASKS:
        _log("busy", trial_id)
        return
    task = asyncio.create_task(
        _compare(
            sessions,
            context,
            root_id,
            filters,
            query,
            trial,
            trial_id,
            {record.id for record in delivered.records},
            memory_recall.estimate_tokens(delivered.block),
        ),
        name="memory-recall-shadow",
        context=Context(),
    )
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _compare(
    sessions: memory_recall.SessionFactory,
    context: OrgContext,
    root_id: UUID,
    filters: memory_recall.Filters,
    query: memory_recall.Query,
    trial: tuple[str, int, float],
    trial_id: str,
    keyword_ids: set[UUID],
    keyword_tokens: int,
) -> None:
    started = time.perf_counter()
    deadline = (
        settings.memory_recall_statement_timeout_ms / 1000
        + settings.memory_embedding_timeout_seconds
        + 0.5
    )
    try:
        async with asyncio.timeout(deadline), sessions() as db:
            await bind_tenant(db, context)
            run = await db.scalar(
                select(ConversationRun).where(
                    ConversationRun.id == root_id, private_scope_predicate(ConversationRun, context)
                )
            )
            if run is None or (filters.agent_id is not None and filters.agent_id != run.agent_id):
                _log("policy_changed", trial_id)
                return
            policy = await memory_service.resolve_effective_policy(
                db, context=context, agent_id=run.agent_id, persist_defaults=False
            )
            if not policy.read_enabled or policy.allowed_scopes not in ("both", filters.scopes):
                _log("policy_changed", trial_id)
                return
            # A removed or changed input cannot be sent from a captured foreground query.
            if memory_recall.Query.of(await memory_recall.run_inputs(db, run)) != query:
                _log("policy_changed", trial_id)
                return
            # Model resolution uses its own scoped session. Return the ledger connection
            # before that lookup and the provider request, including on single-slot pools.
            await db.rollback()
            current = await memory_embedder.resolve_embedder(
                context.tenant_id, org_id=context.org_id
            )
            if current is None or _trial(current) != trial:
                _log("policy_changed", trial_id)
                return
            vectors = await current.embed(
                [query.text()], seconds=settings.memory_embedding_timeout_seconds
            )
            now = datetime.now(UTC).replace(tzinfo=None)
            ranked = await memory_recall.rank(
                db,
                context,
                query,
                filters,
                embedder=current,
                query_vectors=vectors,
                now=now,
                candidate_floor=trial[2],
            )
            if ranked.fallback is not None:
                _log("unavailable", trial_id)
                return
            hybrid = memory_recall.select_within_budget(ranked.items, now)
            hybrid_ids = {item.record.id for item in hybrid.items}
            hybrid_tokens = memory_recall.estimate_tokens(
                memory_recall.render_block(hybrid.lines, now)
            )
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        recent_ms = _recent_latencies(trial_id)
        recent_ms.append(elapsed)
        _log(
            "completed",
            trial_id,
            keyword_count=len(keyword_ids),
            hybrid_count=len(hybrid_ids),
            overlap_count=len(keyword_ids & hybrid_ids),
            keyword_tokens=keyword_tokens,
            hybrid_tokens=hybrid_tokens,
            added_tokens=hybrid_tokens - keyword_tokens,
            latency_ms=elapsed,
            latency_p95_ms=sorted(recent_ms)[math.ceil(0.95 * len(recent_ms)) - 1],
            latency_window_count=len(recent_ms),
        )
    except TimeoutError:
        _log("timeout", trial_id)
    except Exception:  # availability of a comparison can never change the delivered block
        _log("unavailable", trial_id)
