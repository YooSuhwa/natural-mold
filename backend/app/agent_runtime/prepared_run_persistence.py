"""Capability-gated persistence adapter for a prepared run (parent Task 10, I14).

The service layer injects the actual writers (event append, trace finalize);
this adapter only admits exact bound identifiers and rooted artifact paths, so
post-marker code holds no generic session and cannot address another run.

Scope split: events/chunks, trace (incl. metrics/usage carried by the trace
finalizer), artifacts and attachments are gated here; checkpoint descendants
are gated by ``LeaseCheckpointSaver`` (I11); run/input/envelope terminal fields
go through ``prepared_terminal_writer`` on rows the caller locked. I15 wires
the concrete writers and the branch-leaf update through this adapter.

Chat/queue wiring (I16/I19): mid-stream event chunks go through
``events_callback`` with the stream's own run ID, and are additionally dropped
once the run row is no longer active and owned by the streaming worker. The
owning worker's *post-terminal, same-run/same-thread* writes are explicit
exemptions that do not pass through this adapter: the terminal run
transition with metrics/usage, trace finalization, artifact
finalization/linking (the recorder is scoped to the run's conversation and
assistant message), attachment ``message_id`` backfill for the IDs submitted
with this run, branch-leaf activation for edit/regenerate, and the
spend/audit booking (``prepared_run_accounting``). They run after the lease
terminalized, outside ``post_marker_execution``, keyed by the bound run ID.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agent_runtime.prepared_run_write_capability import PreparedRunWriteCapability

EventsCallback = Callable[[list[dict[str, Any]]], Awaitable[None]]
PersistFactory = Callable[[uuid.UUID, str], EventsCallback]
TraceFinalizer = Callable[..., Awaitable[None]]


@dataclass(frozen=True, slots=True)
class PreparedRunPersistence:
    capability: PreparedRunWriteCapability
    persist_factory: PersistFactory
    trace_finalizer: TraceFinalizer

    def events_callback(self, run_id: uuid.UUID | str) -> EventsCallback:
        exact = self.capability.require_run(run_id)
        conversation_id = self.capability.binding.conversation_id
        inner = self.persist_factory(conversation_id, str(exact))

        async def callback(events_chunk: list[dict[str, Any]]) -> None:
            await inner(events_chunk)

        return callback

    async def finalize_trace(self, run_id: uuid.UUID | str, **kwargs: Any) -> None:
        exact = self.capability.require_run(run_id)
        await self.trace_finalizer(self.capability.binding.conversation_id, str(exact), **kwargs)

    def artifact_path(self, relative: str) -> Path:
        return self.capability.output_path(relative)

    def attachments(self, attachment_ids: Sequence[uuid.UUID | str]) -> tuple[uuid.UUID, ...]:
        return tuple(self.capability.require_attachment(item) for item in attachment_ids)
