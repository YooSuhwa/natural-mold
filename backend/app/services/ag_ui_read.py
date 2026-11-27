"""Read-only public chat streams backed by short, scoped durable-journal reads."""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from typing import Any

import orjson
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.database import tenant_session
from app.exceptions import AppError
from app.models.conversation import Conversation
from app.services import conversation_stream_service
from app.services.ag_ui_journal import JournalPage, read_journal_page
from app.services.ag_ui_read_access import require_read_conversation
from app.services.ag_ui_read_cursor import CursorPosition, InvalidReadCursor, parse_cursor
from app.services.ag_ui_read_pending import CurrentReadInterrupts, current_read_interrupts
from app.services.ag_ui_read_projection import CommittedFrame, CommittedProjection, FrameBatch
from app.services.ag_ui_read_target import ReadTarget, resolve_read_target, snapshot_required
from app.services.ag_ui_read_wake import committed_run_changed
from app.services.ag_ui_tool_continuations import ContinuationCache
from app.services.chat.secrets import collect_conversation_secret_values


@dataclass(frozen=True, slots=True)
class ReadRequest:
    request: Request
    context: OrgContext
    conversation_id: uuid.UUID
    public_id: str


class _Reader:
    def __init__(self, source: ReadRequest) -> None:
        self.source = source
        self.projection: CommittedProjection | None = None
        self.target: ReadTarget | None = None
        self.secrets: tuple[str, ...] = ()
        self.continuation_cache = ContinuationCache()
        self.checked_at = 0.0
        self.pending = CurrentReadInterrupts()
        self.trace_status: str | None = None

    async def _can_continue(
        self, db: AsyncSession, conversation: Conversation, target: ReadTarget
    ) -> bool:
        previous = self.target
        if previous is None or previous.run is None or target.run is None:
            return False
        before, after = previous.visibility, target.visibility
        if (
            previous.run.id != target.run.id
            or previous.public_id != target.public_id
            or previous.version != target.version
            or previous.parent_public_id != target.parent_public_id
            or not set(previous.tool_continuations) <= set(target.tool_continuations)
            or (before.allow_legacy_details and not after.allow_legacy_details)
            or not before.tool_call_ids <= after.tool_call_ids
            or not before.namespaces <= after.namespaces
            or not before.bindings <= after.bindings
            or not before.historical_bindings <= after.historical_bindings
            or not previous.artifacts <= target.artifacts
            or not previous.widget_bindings <= target.widget_bindings
        ):
            return False
        if previous.scope.branch_key == target.scope.branch_key:
            return True
        from app.services.ag_ui_read_branch import accepts_fork_handoff

        return await accepts_fork_handoff(db, conversation, self.source.context, target.run)

    async def _refresh_projection(
        self,
        db: AsyncSession,
        conversation: Conversation,
        target: ReadTarget,
        secrets: tuple[str, ...],
    ) -> list[CommittedFrame]:
        old = self.projection
        if (
            old is None
            or target.run is None
            or not await self._can_continue(db, conversation, target)
        ):
            raise snapshot_required()
        fresh = CommittedProjection(target, secrets)
        if not old.frame_count and not old.source_ordinal:
            self.projection = fresh
            return []
        # A new permission can change how prior source rows map onto messages.
        # Replace the projector only if everything the SDK saw is still its exact
        # prefix. Newly visible suffix frames can then be sent once, in order.
        digest = hashlib.sha256()
        compared = 0
        extra: list[CommittedFrame] = []
        while True:
            remaining = old.source_ordinal - fresh.source_ordinal
            if remaining:
                page = await read_journal_page(
                    db,
                    conversation=conversation,
                    physical_run_id=target.run.id,
                    after=fresh.source_ordinal,
                    limit=min(128, remaining),
                    secret_values=secrets,
                )
                if not page.entries or page.generation != old.generation:
                    raise snapshot_required()
            else:
                page = JournalPage(old.generation, (), True, False, None)
            # The terminal is emitted only after the real current tail is read.
            batch = fresh.fold(target, replace(page, has_more=True), CurrentReadInterrupts())
            for frame in batch.frames:
                if compared < old.frame_count:
                    compared += 1
                    digest.update(frame.prefix_record())
                else:
                    extra.append(frame)
                    if len(extra) > 256:
                        raise snapshot_required()
            if fresh.source_ordinal == old.source_ordinal:
                break
        if compared != old.frame_count or digest.digest() != old.prefix_digest.digest():
            raise snapshot_required()
        self.projection = fresh
        return extra

    async def check_delivery(self, frame: CommittedFrame) -> None:
        # ASGI's send can block for a slow reader after a batch is prepared.
        kind = frame.event["type"]
        protected_frame = (
            "subagentRunId" in frame.event
            or kind.startswith(("TOOL_", "SUBAGENT_", "ACTIVITY_", "STATE_"))
            or frame.kind == "terminal"
            or (self.target is not None and self.target.delegated)
        )
        if not protected_frame and time.monotonic() - self.checked_at < 1:
            return
        source = self.source
        async with tenant_session(
            source.context, maker=conversation_stream_service.async_session
        ) as db:
            conversation = await require_read_conversation(
                db,
                request=source.request,
                context=source.context,
                conversation_id=source.conversation_id,
            )
            current = await resolve_read_target(
                db,
                conversation,
                source.context,
                source.public_id,
                continuation_cache=self.continuation_cache,
            )
            if self.target is None or (
                current.scope != self.target.scope
                and not await self._can_continue(db, conversation, current)
            ):
                raise snapshot_required()
            # Another tab can consume an approval after the final batch is projected.
            if (
                frame.kind == "terminal"
                and current.run is not None
                and current.run.status == "interrupted"
                and await current_read_interrupts(db, conversation, source.context, current)
                != self.pending
            ):
                raise snapshot_required()
            self.checked_at = time.monotonic()

    async def poll(self) -> FrameBatch:
        source = self.source
        async with tenant_session(
            source.context, maker=conversation_stream_service.async_session
        ) as db:
            conversation = await require_read_conversation(
                db,
                request=source.request,
                context=source.context,
                conversation_id=source.conversation_id,
            )
            target = await resolve_read_target(
                db,
                conversation,
                source.context,
                source.public_id,
                continuation_cache=self.continuation_cache,
            )
            extra: list[CommittedFrame] = []
            initial = self.projection is None or (
                self.target is not None and self.target.run is None and target.run is not None
            )
            if initial or (self.projection is not None and self.projection.scope != target.scope):
                self.secrets = tuple(
                    await collect_conversation_secret_values(db, conversation, source.context)
                )
                if initial:
                    self.projection = CommittedProjection(target, self.secrets)
                else:
                    extra = await self._refresh_projection(db, conversation, target, self.secrets)
            if self.projection is None:
                raise snapshot_required()
            self.target = target
            page = None
            pending = CurrentReadInterrupts()
            if target.run is not None:
                page = await read_journal_page(
                    db,
                    conversation=conversation,
                    physical_run_id=target.run.id,
                    after=self.projection.source_ordinal,
                    limit=32,
                    secret_values=self.secrets,
                )
                if not page.has_more:
                    pending = await current_read_interrupts(
                        db, conversation, source.context, target
                    )
            self.trace_status = page.trace_status if page else None
            self.pending = pending
            batch = self.projection.fold(target, page, pending)
            self.checked_at = time.monotonic()
            return FrameBatch(extra + batch.frames, batch.more, batch.terminal) if extra else batch

    async def wait_for_change(self, delay: float) -> None:
        target, projection = self.target, self.projection
        interval = 0.15
        if target is None or target.run is None or projection is None:
            await asyncio.sleep(min(delay, 0.25))
            return
        # Empty polls still reauthorize at least once per second. Between them a
        # single indexed SELECT wakes a different replica as soon as the writer
        # commits. A busy token stream never triggers one ACL rebuild per tick.
        loop = asyncio.get_running_loop()
        until = loop.time() + delay
        while (remaining := until - loop.time()) > interval:
            await asyncio.sleep(interval)
            async with tenant_session(
                self.source.context, maker=conversation_stream_service.async_session
            ) as db:
                if await committed_run_changed(
                    db,
                    self.source.context,
                    target.run,
                    projection.generation,
                    projection.source_ordinal,
                    self.trace_status,
                ):
                    return
        await asyncio.sleep(max(0.0, remaining))

    def parse(self, token: str) -> CursorPosition:
        if self.target is None:
            raise snapshot_required()
        try:
            return parse_cursor(token, self.target.scope)
        except InvalidReadCursor as exc:
            raise snapshot_required() from exc


def _sse(frame: CommittedFrame) -> bytes:
    return b"id: " + frame.id.encode("ascii") + b"\ndata: " + orjson.dumps(frame.event) + b"\n\n"


def _end_read(code: str) -> bytes:
    payload = {"type": "RUN_ERROR", "code": code, "message": "Reload the authorized chat snapshot."}
    return b"data: " + orjson.dumps(payload) + b"\n\n"


def _headers(target: ReadTarget) -> dict[str, str]:
    headers = {
        "Cache-Control": "no-store",
        "X-Accel-Buffering": "no",
        "X-Stream-Protocol": "ag-ui/1.0",
    }
    if (
        target.public_id.isascii()
        and target.public_id.isprintable()
        and target.public_id == target.public_id.strip()
    ):
        headers["X-Run-Id"] = target.public_id
    if target.run is not None:
        headers["X-Moldy-Run-Id"] = str(target.run.id)
    if target.input_id is not None:
        headers["X-Moldy-Input-Id"] = str(target.input_id)
    if target.input_status is not None:
        headers["X-Moldy-Input-Status"] = target.input_status
    if target.position is not None:
        headers["X-Moldy-Input-Position"] = str(target.position)
    if target.revision is not None:
        headers["X-Moldy-Input-Revision"] = str(target.revision)
    return headers


async def _stream(reader: _Reader, batch: FrameBatch) -> AsyncIterator[bytes]:
    delay, last_heartbeat = 0.10, 0.0
    while True:
        try:
            for frame in batch.frames:
                await reader.check_delivery(frame)
                yield _sse(frame)
            if batch.terminal:
                return
            if not batch.more:
                if time.monotonic() - last_heartbeat >= 10:
                    yield b": connected\n\n"
                    last_heartbeat = time.monotonic()
                await reader.wait_for_change(delay)
            if await reader.source.request.is_disconnected():
                return
            batch = await reader.poll()
        except (HTTPException, AppError) as exc:
            code = (
                "AG_UI_SNAPSHOT_REQUIRED"
                if isinstance(exc, AppError) and exc.code == "AG_UI_SNAPSHOT_REQUIRED"
                else "AG_UI_READ_DENIED"
            )
            yield _end_read(code)
            return
        delay = 0.10 if batch.frames else min(1.0, delay * 1.5)


async def ag_ui_run_response(
    request: Request,
    context: OrgContext,
    conversation_id: uuid.UUID,
    public_run_id: str,
    *,
    after: str | None = None,
) -> StreamingResponse:
    reader = _Reader(ReadRequest(request, context, conversation_id, public_run_id))
    # Preflight scope before response.start so an inaccessible resource is a real 404.
    first = await reader.poll()
    if after is not None:
        # The normal SDK starts a new lifecycle verifier per connection; never feed
        # it a suffix that begins in the middle of a message or tool invocation.
        cursor = reader.parse(after)
        current = first
        while not any(frame.matches(cursor) for frame in current.frames):
            if not current.more:
                raise snapshot_required()
            current = await reader.poll()
        # Validation may have consumed later pages. Always restart the SDK
        # verifier with a newly authorized full stream and RUN_STARTED.
        if current is not first:
            reader = _Reader(reader.source)
            first = await reader.poll()
    if reader.target is None:
        raise snapshot_required()
    return StreamingResponse(
        _stream(reader, first), media_type="text/event-stream", headers=_headers(reader.target)
    )


async def read_frame_page(
    request: Request,
    context: OrgContext,
    conversation_id: uuid.UUID,
    public_run_id: str,
    *,
    after: str | None,
    limit: int,
) -> dict[str, Any]:
    reader = _Reader(ReadRequest(request, context, conversation_id, public_run_id))
    batch = await reader.poll()
    position = reader.parse(after) if after is not None else None
    found = position is None
    frames: list[dict[str, Any]] = []
    while True:
        for frame in batch.frames:
            if not found and position is not None:
                if frame.matches(position):
                    found = True
                elif frame.ordinal > position.ordinal:
                    raise snapshot_required()
                continue
            if len(frames) == limit:
                return {
                    "frames": frames,
                    "cursor": frames[-1]["id"],
                    "hasMore": True,
                    "terminal": False,
                }
            frames.append({"id": frame.id, "event": frame.event})
        if not batch.more:
            if not found:
                raise snapshot_required()
            return {
                "frames": frames,
                "cursor": frames[-1]["id"] if frames else after,
                "hasMore": False,
                "terminal": batch.terminal,
            }
        batch = await reader.poll()
