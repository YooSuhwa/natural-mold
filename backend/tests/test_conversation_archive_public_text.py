from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.protocol_events import stored_protocol_event
from app.agent_runtime.protocol_persistence import persistable_protocol_event
from app.agent_runtime.run_secrets import reset_run_secrets, set_run_secrets
from app.models.message_event import MessageEvent
from app.services.conversation_archive_public_text import load_archive_public_text
from app.services.trace_storage import append_events
from tests.authz.agent_execution_runtime_fixture import seed_runtime_run


@pytest.mark.asyncio
async def test_evidence_uses_completed_root_public_text_after_credential_rotation(
    db: AsyncSession,
) -> None:
    run, _agent, conversation = await seed_runtime_run()
    secret = "old-credential-value-for-an-archive-test"
    raw = f"처리 값은 {secret}입니다."
    run_id, thread_id = str(run.id), str(conversation.id)
    message_id = "lc_run--model-call"

    def event(seq: int, data: dict, *, namespace: list[str] | None = None) -> dict:
        return persistable_protocol_event(
            stored_protocol_event(
                run_id=run_id,
                thread_id=thread_id,
                seq=seq,
                method="messages",
                data={**data, "metadata": {"run_id": "model-call", "langgraph_node": "model"}},
                namespace=namespace,
            )
        )

    token = set_run_secrets({secret})
    try:
        start = event(0, {"event": "message-start", "role": "ai", "id": message_id})
        block = event(
            1,
            {"event": "content-block-finish", "index": 0, "content": {"type": "text", "text": raw}},
        )
        child = event(
            2,
            {
                "event": "content-block-finish",
                "index": 1,
                "content": {"type": "text", "text": "child text"},
            },
            namespace=["child"],
        )
        finish = event(3, {"event": "message-finish"})
    finally:
        reset_run_secrets(token)
    db.add(MessageEvent(conversation_id=conversation.id, assistant_msg_id=run_id, events=[start]))
    await db.flush()
    await append_events(
        db,
        conversation_id=conversation.id,
        assistant_msg_id=run_id,
        events_chunk=[block, child, finish],
        status="completed",
    )
    await db.flush()

    rotated = set_run_secrets({"different-current-value"})
    try:
        evidence = await load_archive_public_text(db, conversation)
    finally:
        reset_run_secrets(rotated)
    assert evidence.approved_excerpt("assistant", message_id, raw) == "처리 값은 <redacted>입니다."
    assert evidence.approved_excerpt("assistant", message_id, "별개의 내용") is None
    assert evidence.approved_excerpt("assistant", str(uuid4()), raw) is None


@pytest.mark.asyncio
async def test_user_excerpt_requires_visible_preview_and_never_includes_unconfirmed_suffix(
    db: AsyncSession,
) -> None:
    run, _agent, conversation = await seed_runtime_run()
    saved = await db.merge(run)
    preview = "가" * 500
    saved.input_preview = preview
    saved.source = "start"
    await db.flush()
    evidence = await load_archive_public_text(db, conversation)
    assert evidence.approved_excerpt("user", None, preview + "unknown") == preview
    assert evidence.approved_excerpt("user", None, "unknown") is None
