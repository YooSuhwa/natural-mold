"""Spend/audit booking for prepared runs, outside the graph (parent Task 10, I16/I19).

Prepared dispatch runs the frozen graph with the DB-writing hook framework
disabled (those writes are outside the I14 run write capability). Once the
lease has terminalized, the worker books the same ``agent_invoke`` lifecycle
the legacy stream body would have (``SpendHook`` spend entry, ``AuditHook``
credential audit row, any custom hook) by reusing the registered hooks with
the legacy ``HookContext``/``HookResult`` construction.

Capability scope: the hook context is built only from the resolver's
secret-free metadata config and must name the lease's exact user and agent;
anything else books nothing. Booking never fails the run.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from typing import Any

from app.agent_runtime.agent_stream_hooks import _hook_ctx_for_agent, _hook_result_from_usage
from app.agent_runtime.runtime_config import AgentConfig, PreparedRunBinding
from app.agent_runtime.streaming import StreamErrorRecord

logger = logging.getLogger(__name__)


async def book_prepared_usage(
    cfg: AgentConfig,
    binding: PreparedRunBinding,
    *,
    usage: dict[str, Any],
    started: float,
    error: BaseException | None,
    stream_errors: Sequence[StreamErrorRecord],
    hook_metadata_extra: dict[str, Any] | None = None,
) -> bool:
    """Run the legacy post/failure hook lifecycle once; ``True`` when booked."""

    from app.hooks import hooks

    ctx = _hook_ctx_for_agent(cfg)
    if ctx is None or (ctx.user_id, ctx.agent_id) != (binding.user_id, binding.agent_id):
        return False
    if hook_metadata_extra:
        ctx.metadata.update(hook_metadata_extra)
    result = _hook_result_from_usage(int((time.monotonic() - started) * 1000), usage)
    try:
        if isinstance(error, asyncio.CancelledError):
            if not usage:
                return False
            await hooks.run_post(ctx, result)
        elif isinstance(error, Exception):
            await hooks.run_failure(ctx, error)
        elif error is not None:
            return False
        elif stream_errors:
            await hooks.run_failure(ctx, stream_errors[0].error)
        else:
            await hooks.run_post(ctx, result)
    except Exception:  # noqa: BLE001 - booking must never fail the run
        logger.warning("prepared run usage booking failed run_id=%s", binding.run_id, exc_info=True)
        return False
    return True
