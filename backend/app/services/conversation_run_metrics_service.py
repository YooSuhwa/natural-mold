from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.run_metrics import RunMetricsSnapshot
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_metrics import ConversationRunMetrics
from app.services.chat.usage import save_token_usage


class RunMetricsPersistenceError(RuntimeError):
    """Raised when an atomic insert cannot be read back in the same transaction."""

    def __init__(self, run_id: uuid.UUID) -> None:
        self.run_id = run_id
        super().__init__(f"conversation run metrics unavailable after insert: {run_id}")


async def persist_run_metrics(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    snapshot: RunMetricsSnapshot,
) -> ConversationRunMetrics:
    """Atomically preserve the first terminal snapshot stored for ``run_id``."""
    values = {
        "run_id": run_id,
        "terminal_state": snapshot.terminal_state,
        "elapsed_ms": snapshot.elapsed_ms,
        "ttft_ms": snapshot.ttft_ms,
        "generation_ms": snapshot.generation_ms,
        "tokens_per_second": snapshot.tokens_per_second,
        "prompt_tokens": snapshot.prompt_tokens,
        "completion_tokens": snapshot.completion_tokens,
        "cache_creation_tokens": snapshot.cache_creation_tokens,
        "cache_read_tokens": snapshot.cache_read_tokens,
        "estimated_cost": (
            Decimal(str(snapshot.estimated_cost)) if snapshot.estimated_cost is not None else None
        ),
        "usage_complete": snapshot.usage_complete,
        "root_tool_calls": snapshot.root_tool_calls,
        "descendant_tool_calls": snapshot.descendant_tool_calls,
        "root_subagent_calls": snapshot.root_subagent_calls,
        "descendant_subagent_calls": snapshot.descendant_subagent_calls,
        "activity_json": [activity.to_persistence_payload() for activity in snapshot.activity],
        "activity_truncated": snapshot.activity_truncated,
    }
    dialect_name = db.get_bind().dialect.name
    if dialect_name == "postgresql":
        statement = postgresql_insert(ConversationRunMetrics).values(values)
    else:
        statement = sqlite_insert(ConversationRunMetrics).values(values)
    await db.execute(statement.on_conflict_do_nothing(index_elements=["run_id"]))
    row = await db.get(ConversationRunMetrics, run_id, populate_existing=True)
    if row is None:
        raise RunMetricsPersistenceError(run_id)
    return row


async def persist_run_metrics_and_usage(
    db: AsyncSession,
    *,
    run: ConversationRun,
    snapshot: RunMetricsSnapshot,
    model_name: str,
) -> ConversationRunMetrics:
    """Persist the frozen snapshot and its deduplicated usage in caller transaction."""
    metrics = await persist_run_metrics(db, run_id=run.id, snapshot=snapshot)
    if metrics.prompt_tokens is not None and metrics.completion_tokens is not None:
        await save_token_usage(
            db,
            conversation_id=run.conversation_id,
            agent_id=run.agent_id,
            model_name=model_name,
            prompt_tokens=metrics.prompt_tokens,
            completion_tokens=metrics.completion_tokens,
            total_tokens=metrics.prompt_tokens + metrics.completion_tokens,
            estimated_cost=(
                float(metrics.estimated_cost) if metrics.estimated_cost is not None else None
            ),
            run_id=run.id,
            commit=False,
        )
    return metrics
