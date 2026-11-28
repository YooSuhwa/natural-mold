"""A purged thread's public transcript comes from its selected retained root lineage."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictError
from app.models.conversation import Conversation
from app.models.conversation_history import (
    ConversationHistoryCheckpoint,
    ConversationHistoryManifest,
    JsonValue,
)
from app.services.ag_ui_snapshot_retained import read_retained_snapshot_history
from tests.conversation_replay_retained_fixture import LegacyReplaySeed, seed_legacy_purged_replay

_ROOT = "legacy-custom-checkpoint"
_QUESTION: dict[str, JsonValue] = {"type": "human", "content": "[REDACTED]"}


async def _seed(db: AsyncSession) -> tuple[Conversation, ConversationHistoryManifest]:
    conversation_id = uuid4()
    manifest = await seed_legacy_purged_replay(
        db, LegacyReplaySeed(conversation_id, "archive", {"event": "trace-only"})
    )
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    return conversation, manifest


def _checkpoint(
    conversation: Conversation,
    manifest: ConversationHistoryManifest,
    checkpoint_id: str,
    parent: str,
    messages: list[JsonValue],
    *,
    namespace: str = "",
    active: bool = False,
    todos: list[JsonValue] | None = None,
) -> ConversationHistoryCheckpoint:
    return ConversationHistoryCheckpoint(
        conversation_id=conversation.id,
        manifest_id=manifest.id,
        checkpoint_id=checkpoint_id,
        parent_checkpoint_id=parent,
        checkpoint_namespace=namespace,
        channel_values={"internal": "channel values", "todos": todos},
        messages=messages,
        branch={"active": active, "parent_checkpoint_id": parent},
        trace={"private": "execution trace"},
        payload_sha256="0" * 64,
    )


@pytest.mark.parametrize("selected", ["a-start", None])
async def test_selected_retained_ancestor_restores_only_its_latest_root_leaf(
    db: AsyncSession, selected: str | None
) -> None:
    conversation, manifest = await _seed(db)
    root = await db.scalar(
        select(ConversationHistoryCheckpoint).where(
            ConversationHistoryCheckpoint.manifest_id == manifest.id
        )
    )
    assert root is not None
    root.branch = {"active": False, "parent_checkpoint_id": None}
    conversation.active_branch_checkpoint_id = selected
    first: list[JsonValue] = [
        _QUESTION,
        {"type": "ai", "id": "answer", "content": "first answer"},
    ]
    final: list[JsonValue] = [*first, {"type": "human", "id": "follow", "content": "follow-up"}]
    stale: list[JsonValue] = [{"content": "ancestor task", "status": "completed"}]
    secret = "fixture-only-retained-todo-secret"
    leaf_todos: list[JsonValue] = [
        {
            "content": f"check {secret} now",
            "status": "in_progress",
            "provider_metadata": {"private": "internal"},
        }
    ]
    db.add_all(
        [
            _checkpoint(conversation, manifest, "a-start", _ROOT, first, active=True, todos=stale),
            _checkpoint(conversation, manifest, "a-end", "a-start", final, todos=leaf_todos),
            _checkpoint(
                conversation,
                manifest,
                "z-other",
                _ROOT,
                [{"content": "other branch"}],
                todos=[{"content": "sibling task", "status": "pending"}],
            ),
            _checkpoint(
                conversation,
                manifest,
                "zz-child",
                "a-start",
                [{"content": "child transcript"}],
                namespace="tools:child",
                todos=[{"content": "child task", "status": "pending"}],
            ),
        ]
    )
    manifest.checkpoint_count = 5
    await db.flush()

    statements: list[str] = []
    channel_params: list[tuple[object, ...]] = []

    def capture(*, statement: str, parameters: tuple[object, ...], **_: object) -> None:
        statements.append(statement.strip().lower())
        if ".channel_values" in statement.lower():
            channel_params.append(parameters)

    connection = (await db.connection()).sync_connection
    event.listen(connection, "before_cursor_execute", capture, named=True)
    try:
        history = await read_retained_snapshot_history(db, conversation, secret_values=(secret,))
    finally:
        event.remove(connection, "before_cursor_execute", capture)

    assert history.source == "retained"
    assert history.checkpoint_id == "a-end"
    assert history.lineage_checkpoint_ids == (_ROOT, "a-start", "a-end")
    assert history.has_branches
    (todo,) = history.todos
    assert todo.keys() == {"id", "content", "status"}
    assert todo["id"] == "todo-1" and todo["status"] == "in_progress"
    assert todo["content"].startswith("check ") and todo["content"].endswith(" now")
    assert secret not in todo["content"]
    assert [ref.message for ref in history.messages] == final
    assert [ref.original_index for ref in history.messages] == [0, 1, 2]
    assert [(ref.checkpoint_id, ref.parent_checkpoint_id) for ref in history.messages] == [
        (_ROOT, None),
        ("a-start", _ROOT),
        ("a-end", "a-start"),
    ]
    assert statements and all(query.startswith(("select", "with")) for query in statements)
    sql = "\n".join(statements)
    assert "conversation_history_events" not in sql
    assert ".trace" not in sql
    assert sql.count(".channel_values") == 1
    assert "json_extract(conversation_history_checkpoints.channel_values," in sql
    (todo_bindings,) = channel_params
    assert '$."todos"' in todo_bindings and "a-end" in todo_bindings
    assert not {"a-start", "z-other", "zz-child"}.intersection(todo_bindings)


@pytest.mark.parametrize("candidate", ["missing", "child", "wrong-conversation", "wrong-manifest"])
async def test_unavailable_explicit_root_never_falls_back(db: AsyncSession, candidate: str) -> None:
    conversation, manifest = await _seed(db)
    other, other_manifest = await _seed(db)
    hidden: list[JsonValue] = [{"type": "ai", "content": "outside selected history"}]
    db.add_all(
        [
            _checkpoint(conversation, manifest, "child", _ROOT, hidden, namespace="tools:child"),
            _checkpoint(other, manifest, "wrong-conversation", _ROOT, hidden),
            _checkpoint(conversation, other_manifest, "wrong-manifest", _ROOT, hidden),
        ]
    )
    conversation.active_branch_checkpoint_id = candidate
    await db.flush()
    with pytest.raises(ConflictError):
        await read_retained_snapshot_history(db, conversation)


async def test_incomplete_retained_manifest_fails_closed(db: AsyncSession) -> None:
    conversation, manifest = await _seed(db)
    manifest.state = "building"
    await db.flush()
    with pytest.raises(ConflictError) as failure:
        await read_retained_snapshot_history(db, conversation)
    assert failure.value.code == "CONVERSATION_HISTORY_INCOMPLETE"


async def test_child_namespace_leaves_do_not_mark_root_transcript_as_branched(
    db: AsyncSession,
) -> None:
    conversation, manifest = await _seed(db)
    child = _checkpoint(
        conversation, manifest, "child-leaf", _ROOT, [{"content": "child"}], namespace="tools:one"
    )
    db.add(child)
    manifest.checkpoint_count = 2
    await db.flush()
    snapshot = await read_retained_snapshot_history(db, conversation)
    assert snapshot.lineage_checkpoint_ids == (_ROOT,)
    assert not snapshot.has_branches
