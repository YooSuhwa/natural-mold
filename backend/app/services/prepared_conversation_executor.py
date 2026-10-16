"""Prepared-lease stream executors for owned conversation runs (Task 10, I16/I19).

Maps the four known stream executors to their prepared-lease variants and
shapes the pre-marker stream input. The returned executor has the worker's
``executor_fn(cfg, input_payload, **stream_kwargs)`` signature but streams the
exact activated lease (``prepared_dispatch``) with the resolver's secret-free
metadata config; event persistence is admitted only for the exact bound run
through the I14 write capability while the row is still active and owned, and
spend/audit are booked after the lease terminalized (``prepared_run_accounting``).
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.types import Command
from sqlalchemy import select

from app.agent_runtime import agent_stream_runner, langgraph_agent_stream_runner
from app.agent_runtime.prepared_run_dispatch import USE_LEASE_MESSAGES, PreparedRunHandle
from app.agent_runtime.prepared_run_persistence import PreparedRunPersistence
from app.agent_runtime.prepared_run_write_capability import (
    PreparedRunWriteCapability,
    RunWriteBinding,
)
from app.agent_runtime.runtime_config import AgentConfig, PreparedRunBinding
from app.config import settings
from app.database import tenant_session
from app.models.conversation_run import ConversationRun
from app.services.prepared_run_accounting import book_prepared_usage

logger = logging.getLogger(__name__)

Executor = Callable[..., AsyncGenerator[str, None]]


@dataclass(frozen=True, slots=True)
class PreparedRunner:
    langgraph: bool
    resume: bool


def _known_runners() -> dict[Any, PreparedRunner]:
    legacy, v3 = agent_stream_runner, langgraph_agent_stream_runner
    return {
        legacy.execute_agent_stream: PreparedRunner(langgraph=False, resume=False),
        legacy.resume_agent_stream: PreparedRunner(langgraph=False, resume=True),
        v3.execute_agent_stream_langgraph: PreparedRunner(langgraph=True, resume=False),
        v3.resume_agent_stream_langgraph: PreparedRunner(langgraph=True, resume=True),
    }


def prepared_runner_for(executor_fn: Any) -> PreparedRunner | None:
    """The prepared variant of a known executor, or ``None`` (flag off / custom)."""

    if not settings.prepared_run_lease_enabled:
        return None
    return _known_runners().get(executor_fn)


def prepared_run_root() -> Path:
    """Run scratch/output root; must stay outside the runtime data root."""

    return Path(tempfile.gettempdir()) / "moldy-prepared-runs"


def stream_shape(runner: PreparedRunner, payload: Any) -> tuple[tuple[Any, ...], Any]:
    """``(messages_history, stream_input)`` built before the marker."""

    if runner.resume:
        return (), Command(resume=payload)
    if isinstance(payload, dict):
        return (), payload
    return tuple(payload), USE_LEASE_MESSAGES


def gated_persist(
    binding: PreparedRunBinding, callback: Any, *, run_id: Any, worker_instance_id: str
) -> Any:
    """Admit event persistence only for the stream's run if it is the bound run
    (I14 capability) and only while the row is still active and owned."""

    if callback is None:
        return None
    capability = PreparedRunWriteCapability(
        RunWriteBinding(
            run_id=binding.run_id,
            conversation_id=binding.conversation_id,
            user_id=binding.user_id,
            tenant_id=binding.tenant_id,
            org_id=binding.org_id,
            output_root=prepared_run_root() / str(binding.run_id) / "output",
        )
    )
    from app.services.conversation_stream_persistence import finalize_trace

    async def while_owned(events_chunk: list[dict[str, Any]]) -> None:
        if await _row_owned(binding, worker_instance_id):
            await callback(events_chunk)
        else:
            logger.warning("prepared run events dropped: row not owned run_id=%s", binding.run_id)

    persistence = PreparedRunPersistence(capability, lambda _c, _r: while_owned, finalize_trace)
    return persistence.events_callback(run_id)


async def _row_owned(binding: PreparedRunBinding, worker_instance_id: str) -> bool:
    from app.services import conversation_stream_service as stream_service

    run_id = binding.run_id
    async with tenant_session(binding, maker=stream_service.async_session) as db:
        row = (
            await db.execute(
                select(ConversationRun.is_active, ConversationRun.worker_instance_id).where(
                    ConversationRun.id == run_id
                )
            )
        ).one_or_none()
    return row is not None and bool(row[0]) and row[1] == worker_instance_id


def prepared_executor(
    runner: PreparedRunner,
    handle: PreparedRunHandle,
    stream_input: Any,
    metadata: AgentConfig,
    worker_instance_id: str,
) -> Executor:
    async def execute(
        _cfg: AgentConfig, _payload: Any, *, run_metrics: Any = None, **kwargs: Any
    ) -> AsyncGenerator[str, None]:
        kwargs["persist_callback"] = gated_persist(
            handle.binding,
            kwargs.get("persist_callback"),
            run_id=kwargs.get("run_id"),
            worker_instance_id=worker_instance_id,
        )
        usage: dict[str, Any] = {}
        extra = {"resume": True} if runner.resume else None
        if runner.langgraph:
            stream = langgraph_agent_stream_runner._run_prepared_langgraph_stream(  # noqa: SLF001
                metadata,
                handle,
                stream_input=stream_input,
                run_metrics=run_metrics,
                usage_sink=usage,
                **kwargs,
            )
        else:
            stream = agent_stream_runner._run_prepared_agent_stream(  # noqa: SLF001
                metadata,
                handle,
                stream_input=stream_input,
                hook_metadata_extra=extra,
                usage_sink=usage,
                **kwargs,
            )
        started, error = time.monotonic(), None
        try:
            async for chunk in stream:
                yield chunk
        except BaseException as exc:
            error = exc
            raise
        finally:
            if not isinstance(error, GeneratorExit):
                await asyncio.shield(
                    book_prepared_usage(
                        metadata,
                        handle.binding,
                        usage=usage,
                        started=started,
                        error=error,
                        stream_errors=kwargs.get("error_sink") or [],
                        hook_metadata_extra=extra,
                    )
                )

    return execute
