"""Validate canonical command identities across raw HTTP and the Python SDK."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from ag_ui.core import RunAgentInput
from fastapi.responses import StreamingResponse
from httpx import AsyncClient
from sqlalchemy import func, select

from app.exceptions import ValidationError
from app.models.ag_ui_command_receipt import AgUiCommandReceipt
from app.models.conversation_run import ConversationRun
from app.services.ag_ui_request_digest import ag_ui_request_digest
from tests.test_ag_ui_pending_checkpoint import PausedChat, ask, question
from tests.test_ag_ui_pending_checkpoint import chat as chat


def test_wire_and_sdk_hash_the_same_canonical_values_including_extra_fields() -> None:
    request = {
        "runId": "한국어",
        "threadId": "t",
        "messages": [{"id": "u", "role": "user", "content": "é", "name": None}],
        "futureExtension": {"β": None, "one": [True, ""]},
        "resume": [{"interruptId": "a", "status": "cancelled", "metadata": None}],
    }
    digest = ag_ui_request_digest(request)
    sdk = RunAgentInput.model_validate(request)
    assert ag_ui_request_digest(sdk) == digest
    assert ag_ui_request_digest(dict(reversed(request.items()))) == digest
    assert ag_ui_request_digest(request | {"runId": "한국어2"}) != digest
    assert ag_ui_request_digest(request | {"futureExtension": None}) != digest
    omitted = {"interruptId": "a", "status": "cancelled"}
    cancelled = request | {"resume": [omitted]}
    explicit = request | {"resume": [omitted | {"payload": None}]}
    assert ag_ui_request_digest(cancelled) != ag_ui_request_digest(explicit)
    for body in (explicit, cancelled):
        assert ag_ui_request_digest(RunAgentInput.model_validate(body)) == ag_ui_request_digest(
            body
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), "\ud800", {1: "a"}])
def test_non_json_values_never_get_normalized_to_a_valid_receipt(value: Any) -> None:
    raw = {"threadId": "t", "runId": "run", "messages": [], "forwardedProps": {"ext": value}}
    for source in (raw, RunAgentInput.model_validate(raw)):
        with pytest.raises(ValidationError) as error:
            ag_ui_request_digest(source)
        assert error.value.code == "AG_UI_INVALID_INPUT"


async def test_cancelled_receipt_replay_cannot_introduce_a_null_payload_or_non_json(
    client: AsyncClient, chat: PausedChat, monkeypatch: pytest.MonkeyPatch
) -> None:
    paused = await chat.graph(ask=ask(question("Choose"))).ainvoke({"answers": []}, chat.config())
    (slot,) = paused["__interrupt__"]
    await chat.set_reported_interrupt(slot.id)
    endpoint = f"/api/conversations/{chat.conversation.id}/ag-ui/runs"
    entry = {"interruptId": slot.id, "status": "cancelled"}
    command = {
        "runId": "sdk-replay",
        "threadId": str(chat.conversation.id),
        "messages": [],
        "resume": [entry],
    }
    # Hold the accepted continuation at the dispatch boundary to verify admission counts.
    dispatch = AsyncMock()
    monkeypatch.setattr("app.services.ag_ui_deferred_delivery.dispatch_ag_ui_run", dispatch)
    attach = AsyncMock(side_effect=lambda *_: StreamingResponse(iter([])))
    monkeypatch.setattr("app.routers.conversation_ag_ui_commands.ag_ui_run_response", attach)
    explicit = command | {"resume": [entry | {"payload": None}]}
    invalid = await client.post(endpoint, json=explicit)
    assert invalid.status_code == 422, invalid.text
    accepted = await client.post(endpoint, json=command)
    assert accepted.status_code == 200, accepted.text
    replayed = await client.post(endpoint, json=dict(reversed(command.items())))
    assert replayed.status_code == 200, replayed.text
    mismatch = await client.post(endpoint, json=explicit)
    assert mismatch.status_code == 409, mismatch.text
    assert mismatch.json()["error"]["code"] == "AG_UI_RUN_ID_CONFLICT"

    for non_json in ("\ud800", float("nan")):
        encoded = json.dumps(command | {"forwardedProps": {"extension": non_json}})
        refused = await client.post(
            endpoint, content=encoded, headers={"Content-Type": "application/json"}
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["code"] == "AG_UI_INVALID_INPUT"
    dispatch.assert_awaited_once()
    assert attach.await_count == 2
    assert await chat.db.scalar(select(func.count()).select_from(AgUiCommandReceipt)) == 1
    resumes = (
        select(func.count())
        .select_from(ConversationRun)
        .where(ConversationRun.parent_run_id == chat.run.id)
    )
    assert await chat.db.scalar(resumes) == 1
