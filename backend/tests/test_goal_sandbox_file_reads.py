from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from starlette.types import Message, Scope

from app.authz.org_context import OrgContext
from app.services import goal_sandbox_file_reads as reads
from tests.artifact_test_helpers import seed_artifact_conversation

Activity = tuple[list[str], asyncio.Event]


@pytest.fixture
def owner() -> OrgContext:
    return OrgContext(org_id=uuid4(), tenant_id=uuid4(), user_id=uuid4())


@pytest.fixture
def activity(monkeypatch: pytest.MonkeyPatch) -> Activity:
    events: list[str] = []
    token = uuid4()
    renewed = asyncio.Event()

    @asynccontextmanager
    async def session(context: OrgContext) -> AsyncIterator[AsyncMock]:
        yield AsyncMock()

    async def acquire(db: AsyncMock, **scope: Any) -> tuple[UUID, ...]:
        events.append("acquire")
        assert scope["ttl"] == reads._READ_TTL
        return (token,)

    async def renew(db: AsyncMock, **scope: Any) -> int:
        assert scope["tokens"] == (token,)
        events.append("renew")
        renewed.set()
        return 1

    async def release(db: AsyncMock, **scope: Any) -> None:
        assert scope["tokens"] == (token,)
        events.append("release")

    monkeypatch.setattr(reads.settings, "goal_sandbox_enabled", True)
    monkeypatch.setattr(reads, "tenant_session", session)
    monkeypatch.setattr(reads.leases, "acquire_conversation_reads", acquire)
    monkeypatch.setattr(reads.leases, "renew_conversation_reads", renew)
    monkeypatch.setattr(reads.leases, "release_conversation_reads", release)
    monkeypatch.setattr(reads, "_RENEW_SECONDS", 0.01)
    return events, renewed


@pytest.mark.asyncio
async def test_slow_range_read_renews_and_releases_on_receiver_failure(
    owner: OrgContext, activity: Activity, tmp_path: Path
) -> None:
    events, renewed = activity
    path = tmp_path / "result.txt"
    path.write_text("abcdef")
    response = reads.ConversationFileResponse(
        path, media_type="text/plain", headers={}, context=owner, conversation_id=uuid4()
    )
    scope: Scope = {
        "type": "http",
        "asgi": {"spec_version": "2.4"},
        "method": "GET",
        "headers": [(b"range", b"bytes=2-4")],
        "extensions": {"http.response.pathsend": {}},
    }

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            assert message["status"] == 206
        else:
            assert message["type"] == "http.response.body"
            assert message["body"] == b"cde"
            await asyncio.wait_for(renewed.wait(), timeout=3)
            assert "release" not in events
            raise OSError("closed by receiver")

    async def receive() -> Message:
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    with pytest.raises(OSError, match="closed by receiver"):
        await response(scope, receive, send)
    assert events[0] == "acquire"
    assert events[-1] == "release"


@pytest.mark.asyncio
async def test_file_lease_expires_or_releases_on_task_cancel(
    owner: OrgContext, activity: Activity
) -> None:
    events, _ = activity
    entered = asyncio.Event()

    async def read_forever() -> None:
        async with reads.hold_goal_sandbox_reads(owner, uuid4()):
            entered.set()
            await asyncio.Future()

    task = asyncio.create_task(read_forever())
    await asyncio.wait_for(entered.wait(), timeout=3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events[-1] == "release"


@pytest.mark.asyncio
async def test_disabled_and_untracked_files_create_no_heartbeat(
    owner: OrgContext, activity: Activity, monkeypatch: pytest.MonkeyPatch
) -> None:
    events, _ = activity
    monkeypatch.setattr(reads.settings, "goal_sandbox_enabled", False)
    async with reads.hold_goal_sandbox_reads(owner, uuid4()) as holding:
        assert not holding
    assert not events

    monkeypatch.setattr(reads.settings, "goal_sandbox_enabled", True)
    acquire = AsyncMock(return_value=())
    monkeypatch.setattr(reads.leases, "acquire_conversation_reads", acquire)
    conversation_id = uuid4()
    async with reads.hold_goal_sandbox_reads(owner, conversation_id) as holding:
        assert not holding
    assert not events
    received = acquire.await_args
    assert received is not None
    assert received.kwargs == {
        "tenant_id": owner.tenant_id,
        "org_id": owner.org_id,
        "user_id": owner.user_id,
        "conversation_id": conversation_id,
        "ttl": reads._READ_TTL,
    }


@pytest.mark.asyncio
async def test_owner_file_route_acquires_after_resolving_an_accessible_file(
    activity: Activity,
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, _ = activity
    monkeypatch.setattr(reads.settings, "conversation_output_dir", str(tmp_path))
    conversation_id, _ = await seed_artifact_conversation()
    output_dir = tmp_path / str(conversation_id)
    output_dir.mkdir()
    (output_dir / "report.txt").write_text("hello")

    missing = await client.get(f"/api/conversations/{conversation_id}/files/missing.txt")
    assert missing.status_code == 404
    assert not events

    downloaded = await client.get(f"/api/conversations/{conversation_id}/files/report.txt")
    assert downloaded.status_code == 200
    assert downloaded.text == "hello"
    assert events[0] == "acquire"
    assert events[-1] == "release"
