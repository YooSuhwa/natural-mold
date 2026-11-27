"""A connected reader survives new grants and stops before delivering revoked material."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import anyio
import orjson
import pytest
from fastapi import Request
from fastapi.responses import StreamingResponse
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.protocol_events import StoredProtocolEvent, stored_protocol_event
from app.auth.jwt import create_access_token
from app.child_tasks.persistence import begin_child_task
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.models.conversation_run import ConversationRun
from app.services import ag_ui_read, conversation_run_service, trace_storage
from tests.authz.test_child_task_authorization import ChildCase
from tests.authz.test_child_task_authorization import child_case as child_case

pytestmark = pytest.mark.asyncio

CALL = "call-research"
CHILD = ("tools:parent", "task:research")


@dataclass
class LiveTurn:
    db: AsyncSession
    case: ChildCase
    file_id: UUID = field(default_factory=uuid4)
    version_id: UUID = field(default_factory=uuid4)
    seq: int = 0

    @property
    def conversation_id(self) -> UUID:
        return self.case.graph.conversation_ids[0]

    def event(
        self, method: str, data: dict[str, Any], namespace: tuple[str, ...] = ()
    ) -> StoredProtocolEvent:
        self.seq += 1
        return stored_protocol_event(
            run_id=str(self.case.run_id),
            thread_id=str(self.conversation_id),
            seq=self.seq,
            method=method,
            data=data,
            namespace=list(namespace),
        )

    def message(self, text: str, namespace: tuple[str, ...] = ()) -> StoredProtocolEvent:
        return self.event(
            "messages", {"id": f"text-{self.seq}", "type": "ai", "content": text}, namespace
        )

    def file_notice(self) -> StoredProtocolEvent:
        return self.event(
            "custom:file_event",
            {
                "conversation_id": str(self.conversation_id),
                "run_id": str(self.case.run_id),
                "id": str(self.file_id),
                "version_id": str(self.version_id),
                "op": "created",
                "description": "private caption",
            },
        )

    async def append(self, *events: StoredProtocolEvent) -> None:
        await trace_storage.append_events(
            self.db,
            conversation_id=self.conversation_id,
            assistant_msg_id=str(self.case.run_id),
            events_chunk=[dict(event) for event in events],
        )
        await self.db.commit()

    async def grant_file(self) -> ConversationArtifact:
        ctx = self.case.graph.context
        row = ConversationArtifact(
            id=self.file_id,
            user_id=ctx.user_id,
            org_id=ctx.org_id,
            tenant_id=ctx.tenant_id,
            agent_id=self.case.graph.agent_id,
            conversation_id=self.conversation_id,
            assistant_msg_id=str(self.case.run_id),
            logical_path="report.txt",
            display_name="Report",
            mime_type="text/plain",
            size_bytes=1,
            sha256="a" * 64,
        )
        self.db.add(row)
        await self.db.flush()
        self.db.add(
            ArtifactVersion(
                id=self.version_id,
                artifact_id=self.file_id,
                version_number=1,
                object_key="report.txt",
                original_filename="report.txt",
                size_bytes=1,
                sha256="a" * 64,
            )
        )
        await self.db.commit()
        return row

    async def grant_child(self) -> None:
        task_id = await begin_child_task(
            self.db,
            context=self.case.graph.context,
            conversation_id=self.conversation_id,
            run_id=self.case.run_id,
            tool_call_id=CALL,
            child_agent_id=self.case.child_id,
            role="research",
            checkpoint_ns="|".join(CHILD),
        )
        assert task_id is not None
        await self.db.commit()

    def child_events(self) -> list[StoredProtocolEvent]:
        return [
            self.event(
                "messages",
                {
                    "id": "issuer",
                    "type": "ai",
                    "tool_calls": [
                        {"name": "task", "id": CALL, "args": {"description": "research"}}
                    ],
                },
            ),
            self.event(
                "lifecycle",
                {
                    "event": "started",
                    "namespace": list(CHILD),
                    "cause": {"type": "toolCall", "tool_call_id": CALL},
                },
            ),
            self.message("child answer", CHILD),
            self.event("lifecycle", {"event": "completed"}, CHILD),
        ]

    async def finish(self) -> None:
        run = await self.db.get(ConversationRun, self.case.run_id)
        assert run
        await self.append(self.event("lifecycle", {"event": "completed"}))
        await trace_storage.finalize_turn(self.db, assistant_msg_id=str(run.id), status="completed")
        await conversation_run_service.transition_run(self.db, run, "completed")
        await self.db.commit()

    async def attach(self) -> AsyncIterator[dict[str, Any]]:
        ctx = self.case.graph.context
        token = create_access_token(ctx.user_id)

        async def receive() -> dict[str, Any]:
            await anyio.sleep_forever()
            raise AssertionError("no disconnect")

        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/",
                "headers": [(b"authorization", f"Bearer {token}".encode())],
            },
            receive=receive,
        )
        response = await ag_ui_read.ag_ui_run_response(
            request, ctx, self.conversation_id, str(self.case.run_id)
        )
        return _events(response)


async def _events(response: StreamingResponse) -> AsyncIterator[dict[str, Any]]:
    async for chunk in response.body_iterator:
        block = chunk.encode() if isinstance(chunk, str) else bytes(chunk)
        for line in block.splitlines():
            if line.startswith(b"data: "):
                yield orjson.loads(line.removeprefix(b"data: "))


async def _through(reader: AsyncIterator[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    prefix = []
    async for event in reader:
        prefix.append(event)
        if event.get("delta") == text:
            return prefix
    pytest.fail(f"Stream ended before {text!r}")


async def test_new_child_and_file_continue_live_after_identical_prefix(
    db: AsyncSession,
    child_case: ChildCase,
) -> None:
    turn = LiveTurn(db, child_case)
    await turn.append(turn.message("initial"))
    stream = await turn.attach()
    prefix = await _through(stream, "initial")

    await turn.grant_child()
    await turn.append(*turn.child_events())
    prefix += await _through(stream, "child answer")
    await turn.grant_file()
    await turn.append(turn.file_notice(), turn.message("final"))
    await turn.finish()
    all_events = prefix + [event async for event in stream]

    cold = await turn.attach()
    assert all_events == [event async for event in cold]
    assert [e["delta"] for e in all_events if e["type"] == "TEXT_MESSAGE_CONTENT"] == [
        "initial",
        "child answer",
        "final",
    ]
    assert [e["type"] for e in all_events].count("RUN_STARTED") == 1
    assert all_events[-1]["type"] == "RUN_FINISHED"
    [started] = [e for e in all_events if e["type"] == "SUBAGENT_STARTED"]
    assert started["parentToolCallId"] == CALL and started["parentMessageId"] == "issuer"
    [card] = [e for e in all_events if e.get("activityType") == "moldy.file_event"]
    assert card["content"]["version_id"] == str(turn.version_id)
    assert b"private caption" not in orjson.dumps(all_events)


async def test_new_grant_changes_already_read_prefix_and_requires_fresh_snapshot(
    db: AsyncSession,
    child_case: ChildCase,
) -> None:
    turn = LiveTurn(db, child_case)
    await turn.append(turn.file_notice(), turn.message("after invisible file"))
    stream = await turn.attach()
    prefix = await _through(stream, "after invisible file")
    assert not any(e.get("activityType") == "moldy.file_event" for e in prefix)

    await turn.grant_file()
    await turn.finish()
    remaining = [event async for event in stream]
    assert remaining[-1]["type"] == "RUN_ERROR"
    assert remaining[-1]["code"] == "AG_UI_SNAPSHOT_REQUIRED"
    assert str(turn.file_id).encode() not in orjson.dumps(remaining)

    cold = await turn.attach()
    recovered = [event async for event in cold]
    assert [
        e["content"]["id"] for e in recovered if e.get("activityType") == "moldy.file_event"
    ] == [str(turn.file_id)]
    assert recovered[-1]["type"] == "RUN_FINISHED"


@pytest.mark.parametrize("revoked", ["child", "file"])
async def test_revoked_scope_stops_before_next_committed_batch(
    db: AsyncSession,
    child_case: ChildCase,
    revoked: str,
) -> None:
    turn = LiveTurn(db, child_case)
    await turn.grant_child()
    file = await turn.grant_file()
    await turn.append(turn.message("initial"))
    stream = await turn.attach()
    await _through(stream, "initial")

    if revoked == "child":
        await db.execute(
            delete(AgentSubAgentLink).where(AgentSubAgentLink.sub_agent_id == child_case.child_id)
        )
    else:
        file.status = "deleted"
    await db.commit()
    await turn.append(turn.message("unsent"), *turn.child_events(), turn.file_notice())
    await turn.finish()
    remaining = [event async for event in stream]
    assert remaining[-1]["type"] == "RUN_ERROR"
    assert remaining[-1]["code"] == "AG_UI_SNAPSHOT_REQUIRED"
    body = orjson.dumps(remaining)
    assert b"child answer" not in body and b"unsent" not in body
    assert str(turn.file_id).encode() not in body


async def test_slow_reader_revalidates_before_delivering_prefetched_file(
    db: AsyncSession,
    child_case: ChildCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turn = LiveTurn(db, child_case)
    file = await turn.grant_file()
    await turn.append(turn.file_notice())
    await turn.finish()
    now = [0.0]
    monkeypatch.setattr(ag_ui_read, "time", SimpleNamespace(monotonic=lambda: now[0]))
    stream = await turn.attach()
    assert (await anext(stream))["type"] == "RUN_STARTED"

    file.status = "deleted"
    await db.commit()
    now[0] += 60
    after_revocation = [event async for event in stream]
    assert after_revocation[-1]["type"] == "RUN_ERROR"
    assert after_revocation[-1]["code"] == "AG_UI_SNAPSHOT_REQUIRED"
    assert str(turn.file_id).encode() not in orjson.dumps(after_revocation)
