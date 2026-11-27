"""Read durable receipts for steers on a single root conversation run.

The caller supplies the owner-gated conversation saver or the executing graph's
lease-bound saver. A conversation may have newer runs and sibling branches. The
root checkpoint must therefore belong to this run and tenant as well as its
thread. A middleware task's pending writes do not prove the graph committed its
state update. Within the independent, non-delta receipt channel, staged_ids
records preparation. Only ids records consumption by the root model.
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from typing import Any
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple

from app.agent_runtime.lease_checkpoint_saver import LeaseCheckpointSaver
from app.schemas.conversation_graph_input import STEER_RECEIPTS_CHANNEL
from app.tenancy.checkpoint_audit import CHECKPOINT_TENANT_KEY

logger = logging.getLogger(__name__)


async def run_checkpoint_for_steers(
    conversation_id: UUID,
    run_id: UUID,
    *,
    saver: BaseCheckpointSaver[Any],
    tenant_id: UUID,
) -> CheckpointTuple | None:
    """Latest committed root snapshot for this exact run, or no positive evidence."""
    thread_id = str(conversation_id)
    root: RunnableConfig = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    identity = {"moldy_run_id": str(run_id), CHECKPOINT_TENANT_KEY: str(tenant_id)}
    try:
        if isinstance(saver, LeaseCheckpointSaver):
            # Filtered history is forbidden on a lease. Its latest root tuple is
            # either one of the lease's own writes or the seed of a previous run.
            saved = await saver.aget_tuple(root)
        else:
            # alist filters in PostgreSQL and orders newest first. Consume the
            # one-row iterator to release its cursor before processing the tuple.
            matches = [item async for item in saver.alist(root, filter=identity, limit=1)]
            saved = matches[0] if len(matches) == 1 else None
    except Exception as exc:  # noqa: BLE001 - storage failure cannot acknowledge a delivery
        logger.warning("Steer checkpoint unavailable for run %s (%s)", run_id, type(exc).__name__)
        return None
    if saved is None:
        return None
    stored = saved.config.get("configurable", {})
    if (
        stored.get("thread_id") != thread_id
        or stored.get("checkpoint_ns") != ""
        or not saved.checkpoint.get("id")
        or stored.get("checkpoint_id") != saved.checkpoint["id"]
        or any(saved.metadata.get(key) != value for key, value in identity.items())
    ):
        return None
    return saved


async def confirmed_steer_ids(
    conversation_id: UUID,
    run_id: UUID,
    steer_ids: Collection[UUID],
    *,
    saver: BaseCheckpointSaver[Any],
    tenant_id: UUID,
) -> frozenset[UUID]:
    """Intersect receipts with durable directive rows the caller has scoped to this run.

    A missing receipt is inconclusive for an already claimed directive, including
    if the checkpointer is unavailable, compacted or the worker stopped mid-step.
    """
    if not steer_ids:
        return frozenset()
    saved = await run_checkpoint_for_steers(
        conversation_id, run_id, saver=saver, tenant_id=tenant_id
    )
    if saved is None:
        return frozenset()
    channels = saved.checkpoint.get("channel_values")
    if not isinstance(channels, dict):
        return frozenset()
    receipts = channels.get(STEER_RECEIPTS_CHANNEL)
    if not isinstance(receipts, dict) or receipts.get("run_id") != str(run_id):
        return frozenset()
    ids = receipts.get("ids")
    if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
        return frozenset()
    recorded = set(ids)
    return frozenset(item for item in steer_ids if str(item) in recorded)
