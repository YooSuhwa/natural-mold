from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from ag_ui.core import RunAgentInput
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput, JsonValue
from app.schemas.conversation_graph_input import OMITTED_MEDIA_KEY
from app.services import conversation_run_queue_service as queue
from app.services import conversation_run_queue_worker as worker
from app.services.ag_ui_retry import load_retry_start
from app.services.ag_ui_start_input import OMITTED_MEDIA_SUFFIX
from app.services.chat_resource_context_payload import frozen_resource_context_from_payload
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID
from tests.integration._seed import seed_conversation_with_agent
from tests.test_ag_ui_start import _submit, _wire
from tests.test_chat_resource_context_support import seed_context_attachment


async def _accept(db: AsyncSession, wire: RunAgentInput) -> ConversationRunInput:
    accepted = await _submit(db, wire)
    assert accepted.input_id is not None
    await db.commit()
    queued = await db.get(ConversationRunInput, accepted.input_id)
    assert queued is not None
    return queued


def _url(queued: ConversationRunInput) -> str:
    return f"/api/conversations/{queued.conversation_id}/run-inputs/{queued.id}"


async def test_native_edit_preserves_bound_message_uploads_and_rechecks_refs(
    client: AsyncClient, db: AsyncSession, tmp_path: Path
) -> None:
    cid = await seed_conversation_with_agent()
    attachment = await seed_context_attachment(db, cid, tmp_path)
    await db.commit()
    wire = _wire(cid).model_copy(
        update={
            "forwarded_props": {
                "moldy": {
                    "attachmentIds": [str(attachment.id)],
                    "resourceContext": {"resources": [{"kind": "file", "id": str(attachment.id)}]},
                }
            }
        }
    )
    queued = await _accept(db, wire)
    await run_in_threadpool(Path(attachment.storage_path).write_text, "updated source")
    native = {
        "messages": [
            {
                "id": str(queued.id),
                "role": "user",
                "content": [{"type": "text", "text": "new"}, {"type": "text", "text": " text"}],
                "metadata": {},
            }
        ]
    }
    edited = await client.patch(_url(queued), json={"expected_revision": 1, "input": native})
    assert edited.status_code == 200, edited.text
    assert edited.json()["revision"] == 2
    assert edited.json()["input_payload"] == {
        "messages": [{"id": str(queued.id), "role": "user", "content": "new text"}]
    }
    assert edited.json()["attachment_ids"] == [str(attachment.id)]
    assert edited.json()["resource_context"] == [{"kind": "file", "id": str(attachment.id)}]
    await db.refresh(queued)
    frozen = frozen_resource_context_from_payload(queued.input_payload)
    assert frozen is not None and frozen.resources[0].text == "updated source"
    stale = await client.patch(_url(queued), json={"expected_revision": 1, "input": native})
    assert stale.status_code == 409
    before = queued.input_payload
    await run_in_threadpool(Path(attachment.storage_path).unlink)
    lost = await client.patch(_url(queued), json={"expected_revision": 2, "input": native})
    assert lost.status_code == 404
    await db.refresh(queued)
    assert queued.revision == 2 and queued.input_payload == before


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"state": {"messages": []}}, {"role": "user", "content": "edit"}),
        ({}, {"role": "system", "content": "unexpected"}),
        ({}, {"role": "tool", "content": "unexpected", "tool_call_id": "old"}),
        ({}, {"role": "user", "content": "edited", "id": str(uuid4())}),
        ({}, {"role": "user", "content": "edited", "subagentRunId": "child"}),
        ({}, {"role": "user", "content": "edited", "metadata": {"tool_calls": []}}),
        ({}, {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}),
        ({}, {"role": "user", "content": "literal\x00nul"}),
        ({}, {"role": "user", "content": "", "additional_kwargs": {OMITTED_MEDIA_KEY: True}}),
    ],
)
async def test_ag_ui_queue_patch_rejects_incompatible_payload_without_mutation(
    client: AsyncClient, db: AsyncSession, extra: dict[str, Any], message: dict[str, Any]
) -> None:
    queued = await _accept(db, _wire(await seed_conversation_with_agent()))
    original = queued.input_payload
    rejected = await client.patch(
        _url(queued), json={"expected_revision": 1, "input": {"messages": [message], **extra}}
    )
    assert rejected.status_code == 422, rejected.text
    await db.refresh(queued)
    assert queued.revision == 1 and queued.input_payload == original


@pytest.mark.parametrize("corruption", ["extra_role", "replaced_id", "forged_omission"])
async def test_old_stored_command_cannot_dispatch_modified_authority(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, corruption: str
) -> None:
    cid = await seed_conversation_with_agent()
    queued = await _accept(db, _wire(cid))
    item: dict[str, JsonValue] = {"role": "user", "content": "text", "id": str(queued.id)}
    messages: list[JsonValue] = [item]
    if corruption == "extra_role":
        messages.insert(0, {"role": "system", "content": "not an accepted turn"})
    elif corruption == "replaced_id":
        item["id"] = str(uuid4())
    else:
        item.update(content="", additional_kwargs={OMITTED_MEDIA_KEY: True})
    queued.input_payload = {"messages": messages}
    await db.commit()

    async def unexpected_dispatch(**_kwargs: Any) -> None:
        pytest.fail("worker launched a modified stored command")

    monkeypatch.setattr(worker, "start_conversation_run", unexpected_dispatch)
    await worker.dispatch_next_for_conversation(cid)
    await db.refresh(queued)
    assert queued.status == "failed"
    failed = await db.scalar(select(ConversationRun).where(ConversationRun.id == queued.run_id))
    assert failed is not None and failed.status == "failed"


async def test_omitted_media_requires_server_grant_through_edit_and_retry(
    client: AsyncClient, db: AsyncSession
) -> None:
    cid = await seed_conversation_with_agent()
    raw = _wire(cid).model_dump(by_alias=True, mode="json")
    raw["messages"][-1]["content"] = [
        {"type": "image", "source": {"type": "url", "value": "https://example.invalid/image"}}
    ]
    queued = await _accept(db, RunAgentInput.model_validate(raw))
    assert queued.client_request_id.endswith(OMITTED_MEDIA_SUFFIX)
    edited = await client.patch(
        _url(queued),
        json={
            "expected_revision": 1,
            "input": {"messages": [{"role": "user", "content": [], "metadata": {}}]},
        },
    )
    assert edited.status_code == 200, edited.text
    assert OMITTED_MEDIA_KEY not in edited.text
    await db.refresh(queued)
    assert queued.input_payload == {
        "messages": [
            {
                "id": str(queued.id),
                "role": "user",
                "content": "",
                "additional_kwargs": {OMITTED_MEDIA_KEY: True},
            }
        ]
    }
    conversation = await db.get(Conversation, cid)
    assert conversation is not None
    run = ConversationRun(
        conversation_id=cid,
        agent_id=queued.agent_id,
        user_id=queued.user_id,
        status="failed",
        source="chat",
        org_id=conversation.org_id,
        tenant_id=conversation.tenant_id,
    )
    db.add(run)
    await db.flush()
    queued.status, queued.run_id = "failed", run.id
    await db.commit()
    retry, attachments = await load_retry_start(
        db,
        context=unit_context(TEST_USER_ID),
        conversation=conversation,
        input_id=queued.id,
        public_run_id="later-retry",
    )
    turns = retry["messages"]
    assert isinstance(turns, list) and isinstance(turns[0], dict)
    assert turns[0]["content"] == "" and turns[0]["id"] != str(queued.id)
    assert turns[0]["additional_kwargs"] == {OMITTED_MEDIA_KEY: True} and not attachments


async def test_legacy_queue_editor_keeps_graph_input_for_unbound_row(
    client: AsyncClient, db: AsyncSession
) -> None:
    cid = await seed_conversation_with_agent()
    queued = await queue.enqueue_input(
        db,
        conversation_id=cid,
        context=unit_context(TEST_USER_ID),
        client_request_id="agui:still-a-legacy-request",
        source="chat",
        input_payload={"messages": [{"role": "user", "content": "original"}]},
        attachment_ids=[],
        checkpoint_id=None,
        priority=0,
    )
    await db.commit()
    payload = {"messages": [{"role": "user", "content": "edited", "metadata": {"panel": "open"}}]}
    result = await client.patch(_url(queued), json={"expected_revision": 1, "input": payload})
    assert result.status_code == 200, result.text
    assert result.json()["input_payload"] == payload


async def test_patch_fails_when_original_upload_loses_conversation_link(
    client: AsyncClient, db: AsyncSession, tmp_path: Path
) -> None:
    cid = await seed_conversation_with_agent()
    upload = await seed_context_attachment(db, cid, tmp_path)
    await db.commit()
    queued = await _accept(
        db,
        _wire(cid).model_copy(
            update={"forwarded_props": {"moldy": {"attachmentIds": [str(upload.id)]}}}
        ),
    )
    upload.conversation_id = None
    await db.commit()
    result = await client.patch(
        _url(queued),
        json={"expected_revision": 1, "input": {"messages": [{"role": "user", "content": "edit"}]}},
    )
    assert result.status_code == 404
    await db.refresh(queued)
    assert queued.revision == 1
