"""Fold committed journal rows into the authorized public event stream for a run."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import orjson
from ag_ui.core import BaseEvent

from app.agent_runtime.ag_ui_v1 import AgUiV1Projector
from app.agent_runtime.protocol_events import ProtocolInterrupt
from app.models.conversation_run import RUN_TERMINAL_STATUSES
from app.services.ag_ui_journal import JournalPage
from app.services.ag_ui_read_cursor import CursorKind, CursorPosition, frame_cursor, matches_source
from app.services.ag_ui_read_target import ReadTarget, snapshot_required


@dataclass(frozen=True, slots=True)
class CommittedFrame:
    id: str
    event: dict[str, Any]
    ordinal: int
    index: int
    kind: CursorKind
    source_id: str
    generation: str | None

    def prefix_record(self) -> bytes:
        # Scope-bound cursors may change after a new file/child is admitted. The
        # public content and its committed position must still agree byte for byte.
        return (
            orjson.dumps(
                (self.kind, self.ordinal, self.index, self.source_id, self.event),
                option=orjson.OPT_SORT_KEYS,
            )
            + b"\n"
        )

    def matches(self, cursor: CursorPosition) -> bool:
        return (cursor.kind, cursor.ordinal, cursor.frame) == (
            self.kind,
            self.ordinal,
            self.index,
        ) and matches_source(cursor, source_id=self.source_id, generation=self.generation)


@dataclass(frozen=True, slots=True)
class FrameBatch:
    frames: list[CommittedFrame]
    more: bool
    terminal: bool


class CommittedProjection:
    def __init__(self, target: ReadTarget, secrets: Iterable[str]) -> None:
        self.scope = target.scope
        self.source_ordinal = 0
        self.generation: str | None = None
        self._projector = AgUiV1Projector(
            thread_id=target.scope.thread_id,
            run_id=target.public_id,
            source_run_id=str(target.run.id) if target.run else None,
            parent_run_id=target.parent_public_id,
            secret_values=secrets,
            child_visibility=target.visibility,
            readable_artifact_versions=target.artifacts,
            defer_terminal=True,
        )
        self.prefix_digest = hashlib.sha256()
        self.frame_count = 0
        self._started = False
        self._terminal = False
        self._terminal_witness: str | None = None
        self._waiting_for_final: float | None = None

    @property
    def child_task_handles(self) -> dict[str, str]:
        return self._projector.child_task_handles

    def _frame(
        self, payload: dict[str, Any], kind: CursorKind, ordinal: int, index: int, source: str
    ) -> CommittedFrame:
        cursor = frame_cursor(
            self.scope,
            ordinal=ordinal,
            frame=index,
            source_id=source,
            generation=self.generation,
            kind=kind,
        )
        frame = CommittedFrame(cursor, payload, ordinal, index, kind, source, self.generation)
        self.prefix_digest.update(frame.prefix_record())
        self.frame_count += 1
        return frame

    def _frames(
        self, events: Iterable[BaseEvent], kind: CursorKind, ordinal: int, source: str
    ) -> list[CommittedFrame]:
        return [
            self._frame(
                evt.model_dump(by_alias=True, mode="json", exclude_none=True),
                kind,
                ordinal,
                index,
                source,
            )
            for index, evt in enumerate(events)
        ]

    def fold(
        self,
        target: ReadTarget,
        page: JournalPage | None,
        pending: Sequence[ProtocolInterrupt],
    ) -> FrameBatch:
        if self._terminal or self.scope != target.scope:
            raise snapshot_required()
        if target.run is None:
            # An unclaimed queue entry owns no graph execution.
            if target.input_status not in {"canceled", "failed"}:
                return FrameBatch([], False, False)
            self._terminal = True
            frame = self._frame(
                {
                    "type": "RUN_ERROR",
                    "code": "AG_UI_INPUT_UNAVAILABLE",
                    "message": "The queued input ended before a run was started.",
                },
                "terminal",
                1,
                0,
                str(target.input_id),
            )
            return FrameBatch([frame], False, True)

        if page is None or page.gap:
            raise snapshot_required()
        if self.generation is not None and self.generation != page.generation:
            raise snapshot_required()
        self.generation = page.generation
        if target.run.status == "queued" and not page.entries and not self._started:
            return FrameBatch([], False, False)
        frames: list[CommittedFrame] = []
        if not self._started:
            self._started = True
            frames.extend(self._frames(self._projector.start(), "head", 0, "start"))

        for entry in page.entries:
            if entry.ordinal != self.source_ordinal + 1:
                raise snapshot_required()
            output = [frame for event in entry.events for frame in self._projector.project(event)]
            frames.extend(self._frames(output, "event", entry.ordinal, entry.source_id))
            self.source_ordinal = entry.ordinal
            if entry.terminal_status is not None:
                self._terminal_witness = entry.terminal_status

        terminal = target.run.status in RUN_TERMINAL_STATUSES and not page.has_more
        if terminal and target.run.status == "completed" and self._terminal_witness != "completed":
            if page.trace_status == "streaming":
                # The owner decision may commit on another connection before a
                # final trace batch. Bound the wait even on a cold polling tab.
                if self._waiting_for_final is None:
                    self._waiting_for_final = time.monotonic()
                completed_at = target.run.completed_at
                age = (
                    (datetime.now(UTC) - completed_at.replace(tzinfo=UTC)).total_seconds()
                    if completed_at is not None
                    else 0
                )
                terminal = max(age, time.monotonic() - self._waiting_for_final) >= 5
            if terminal:
                # A plausible text prefix and completed DB row are insufficient
                # proof that the reader received the final authoritative output.
                raise snapshot_required()
        if terminal:
            frames.extend(
                self._frames(
                    self._projector.finish(
                        target.run.status, interrupts=pending, error_code=target.run.error_code
                    ),
                    "terminal",
                    self.source_ordinal + 1,
                    "finish",
                )
            )
            self._terminal = True
        return FrameBatch(frames, page.has_more, terminal)
