"""Pre-run chat transcripts have no executable identity and no child namespace."""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.conversation import Conversation
from app.models.message_event import MessageEvent
from app.services import trace_storage
from app.services.ag_ui_snapshot_legacy import read_unlinked_legacy_transcript
from tests.authz.unit_scope_fixture import unit_context
from tests.test_conversation_run_message_links import _create_run, _seed_conversation


def _trace(key: UUID | str, *items: tuple[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"id": f"{key}-{number}", "event": kind, "data": data}
        for number, (kind, data) in enumerate(items, 1)
    ]


def _start(key: UUID | str, content: Any) -> tuple[str, dict[str, Any]]:
    return "message_start", {
        "id": str(key),
        "role": "assistant",
        "input": {"messages": [{"role": "user", "content": content}]},
    }


def _save(
    chat: Conversation, key: UUID | str, frames: list[dict[str, Any]], at: datetime
) -> MessageEvent:
    return MessageEvent(
        conversation_id=chat.id,
        assistant_msg_id=str(key),
        events=frames,
        created_at=at,
        status="completed",
    )


@pytest.mark.asyncio
async def test_read_old_groups_in_order_without_runs_or_tool_bodies(db: AsyncSession) -> None:
    agent, chat = await _seed_conversation(db)
    run = await _create_run(db, agent=agent, conversation=chat)
    before = datetime(2026, 5, 1)
    later = before + timedelta(hours=1)
    first, second = uuid4(), uuid4()
    # The earliest emitter had a completed answer even when there was no input copy.
    old = _trace(
        first, ("message_start", {"id": str(first)}), ("message_end", {"content": "first answer"})
    )
    # Later SSE encoders retained the submitted user text. An old tool often had no call ID.
    newer = _trace(
        second,
        _start(second, "second question"),
        ("tool_call_result", {"tool_name": "read_file", "result": "private file bytes"}),
        ("content_delta", {"delta": "stale draft"}),
        ("message_end", {"content": "second answer"}),
    )
    db.add_all(
        [
            _save(chat, second, newer, later),
            _save(chat, first, old, before),
            _save(
                chat,
                run.id,
                _trace(run.id, ("message_end", {"content": "separate physical run"})),
                later,
            ),
            _save(
                chat,
                "arbitrary",
                _trace("arbitrary", ("message_end", {"content": "unsupported writer"})),
                later,
            ),
        ]
    )
    await db.commit()

    statements: list[str] = []

    def observe(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement.split()[0].upper())

    event.listen(db.get_bind(), "before_cursor_execute", observe)
    try:
        ctx = unit_context(chat.user_id)
        rows = await read_unlinked_legacy_transcript(db, chat, ctx, source="legacy")
        assert rows == await read_unlinked_legacy_transcript(db, chat, ctx, source="legacy")
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", observe)
    assert set(statements) == {"SELECT"}
    assert [(msg["role"], msg["content"]) for msg, _ in rows] == [
        ("assistant", "first answer"),
        ("user", "second question"),
        ("assistant", "second answer"),
    ]
    assert [link.created_at for _, link in rows] == [before, later, later]
    assert [link.parent_message_id for _, link in rows] == [None, None, rows[1][0]["id"]]
    for msg, link in rows:
        assert set(msg) == {"id", "content", "role"}
        assert link.message_id == msg["id"] and link.run_id is None and link.physical_run_id is None


@pytest.mark.asyncio
async def test_ambiguous_delegate_file_caption_wrong_namespace_and_partial_turn(
    db: AsyncSession,
) -> None:
    _, chat = await _seed_conversation(db)
    ctx = unit_context(chat.user_id)
    child, files, namespaced, partial = (uuid4() for _ in range(4))
    at = datetime(2026, 5, 1)
    db.add_all(
        [
            _save(
                chat,
                child,
                _trace(
                    child,
                    _start(child, "delegate"),
                    (
                        "tool_call_start",
                        {"tool_name": "task", "parameters": {"instruction": "private"}},
                    ),
                    ("message_end", {"content": "unattributed child draft"}),
                ),
                at,
            ),
            _save(
                chat,
                files,
                _trace(
                    files,
                    _start(files, [{"type": "text", "text": "private expanded file caption"}]),
                ),
                at,
            ),
            _save(
                chat,
                namespaced,
                _trace(
                    namespaced,
                    _start(namespaced, "unproven turn"),
                    ("message_end", {"subagentRunId": "child", "content": "child text"}),
                ),
                at,
            ),
        ]
    )
    # A durable prefix without message_end cannot certify an assistant response.
    await trace_storage.append_events(
        db,
        conversation_id=chat.id,
        assistant_msg_id=str(partial),
        events_chunk=_trace(partial, ("content_delta", {"delta": "partial draft"})),
    )
    await db.commit()
    rows = await read_unlinked_legacy_transcript(db, chat, ctx, source="legacy")
    assert [message["content"] for message, _ in rows] == ["delegate"]


@pytest.mark.asyncio
async def test_unlinked_transcript_needs_owned_unbranched_legacy_chat(db: AsyncSession) -> None:
    _, chat = await _seed_conversation(db)
    key = uuid4()
    db.add(_save(chat, key, _trace(key, _start(key, "private turn")), datetime(2026, 5, 1)))
    await db.commit()
    ctx = unit_context(chat.user_id)
    with pytest.raises(AppError) as caught:
        await read_unlinked_legacy_transcript(db, chat, unit_context(uuid4()), source="legacy")
    assert caught.value.status == 404
    for source in ("checkpoint", "retained"):
        assert not await read_unlinked_legacy_transcript(db, chat, ctx, source=source)
    chat.active_branch_checkpoint_id = "different-checkpoint"
    await db.commit()
    assert not await read_unlinked_legacy_transcript(db, chat, ctx, source="legacy")
    chat.active_branch_checkpoint_id = None
    chat.source = "api"
    await db.commit()
    assert not await read_unlinked_legacy_transcript(db, chat, ctx, source="legacy")
    chat.source = "ui"
    chat.agent_id = None
    chat.agent_purged_at = datetime(2026, 6, 1)
    chat.agent_lifecycle_state = "purged"
    await db.commit()
    assert not await read_unlinked_legacy_transcript(db, chat, ctx, source="legacy")


@pytest.mark.asyncio
async def test_unlinked_chunk_pages_redact_complete_turn_and_drop_broken_tail(
    db: AsyncSession,
) -> None:
    _, chat = await _seed_conversation(db)
    ctx = unit_context(chat.user_id)
    good, broken = uuid4(), uuid4()
    secret = "test-only-legacy-secret"
    for key in (good, broken):
        stream = _trace(
            key,
            _start(key, f"request {secret}"),
            *(("content_delta", {"delta": "piece"}) for _ in range(128)),
            ("message_end", {"content": f"answer {secret}"}),
        )
        if key == broken:
            stream[-1]["id"] = f"{key}-135"
        for part in (stream[:100], stream[100:]):
            await trace_storage.append_events(
                db, conversation_id=chat.id, assistant_msg_id=str(key), events_chunk=part
            )
        await trace_storage.finalize_turn(db, assistant_msg_id=str(key))
    await db.commit()
    rows = await read_unlinked_legacy_transcript(
        db, chat, ctx, source="legacy", secret_values=[secret]
    )
    assert [message["role"] for message, _ in rows] == ["user", "assistant"]
    assert rows[0][0]["content"].startswith("request ") and rows[1][0]["content"].startswith(
        "answer "
    )
    assert secret not in repr(rows)
