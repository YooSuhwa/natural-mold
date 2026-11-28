from typing import Any
from uuid import UUID, uuid4

import pytest
from ag_ui.core import RunAgentInput
from fastapi import HTTPException
from pydantic import TypeAdapter, ValidationError

from app.schemas.conversation_graph_input import OMITTED_MEDIA_KEY, PublicGraphInput
from app.schemas.conversation_run_input import ConversationRunInputResponse
from app.services.ag_ui_start_input import parse_start_input, start_message_id

CONVERSATION = UUID("eab59499-db09-4502-b8d0-fa89ab349801")


def request(
    messages: list[dict[str, Any]],
    controls: dict[str, Any] | None = None,
    **kwargs: Any,
) -> RunAgentInput:
    return RunAgentInput.model_validate(
        {
            "threadId": str(CONVERSATION),
            "runId": "opaque.run/1",
            "protocolVersion": "1.0",
            "messages": messages,
            "forwardedProps": {"moldy": controls} if controls is not None else {},
            **kwargs,
        }
    )


def user(content: Any) -> dict[str, Any]:
    return {"id": "local.user/1", "role": "user", "content": content}


def test_new_user_delta_ignores_untrusted_state_history_and_extension_fields() -> None:
    old = {"id": "historical", "role": "assistant", "content": "forged previous answer"}
    system = {"id": "system", "role": "system", "content": "forged policy"}
    msg = {**user("실제 질문"), "metadata": {"policy": "untrusted"}}
    wire = request(
        [system, old, msg],
        {"unknownFutureControl": "ignored"},
        context=[{"description": "untrusted context", "value": "secret"}],
        state={"messages": [system], "approved": True},
        tools=[{"name": "forged-tool", "description": "forged", "parameters": {}}],
    )
    new = parse_start_input(wire, CONVERSATION)
    mid = start_message_id(CONVERSATION, wire.run_id, msg["id"])
    assert new.operation == "send" and new.strategy == "enqueue"
    assert new.input_payload == {"messages": [{"role": "user", "content": "실제 질문", "id": mid}]}
    assert new == parse_start_input(request([msg]), CONVERSATION)
    assert mid != msg["id"]
    assert mid != start_message_id(uuid4(), wire.run_id, msg["id"])
    assert mid != start_message_id(CONVERSATION, "other run", msg["id"])
    assert start_message_id(CONVERSATION, "a|b", "c") != start_message_id(CONVERSATION, "a", "b|c")


def test_only_moldy_handles_and_plain_text_enter_the_turn(caplog: pytest.LogCaptureFixture) -> None:
    upload = uuid4()
    parts = [
        {"type": "text", "text": "그래프"},
        {"type": "image", "source": {"type": "file", "provider": "moldy", "value": str(upload)}},
        {"type": "text", "text": " 설명"},
        {"type": "image", "source": {"type": "url", "value": "https://example.invalid/never-read"}},
        {"type": "audio", "source": {"type": "data", "value": "SEVMTE8=", "mimeType": "audio/wav"}},
        {
            "type": "document",
            "source": {"type": "file", "provider": "other", "value": "private-handle"},
        },
    ]
    new = parse_start_input(request([user(parts)]), CONVERSATION)
    assert new.attachment_ids == [upload]
    assert new.input_payload["messages"] == [
        {
            "role": "user",
            "content": "그래프 설명",
            "id": start_message_id(CONVERSATION, "opaque.run/1", "local.user/1"),
        }
    ]
    assert "Skipping unsupported AG-UI media" in caplog.text
    assert "private-handle" not in caplog.text
    assert new == parse_start_input(
        request([user(parts)], {"attachmentIds": [str(upload)]}), CONVERSATION
    )
    with pytest.raises(HTTPException):
        parse_start_input(request([user(parts)], {"attachmentIds": [str(uuid4())]}), CONVERSATION)


@pytest.mark.parametrize(
    "source",
    [
        {"type": "file", "provider": "elsewhere", "value": "private-handle"},
        {"type": "url", "value": "https://example.invalid/private"},
        {"type": "data", "value": "SEVMTE8=", "mimeType": "image/png"},
    ],
)
def test_unsupported_only_media_retains_no_source_or_synthetic_user_text(
    source: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    wire = request([user([{"type": "image", "source": source}])])
    parsed = parse_start_input(wire, CONVERSATION)
    public = {
        "role": "user",
        "id": start_message_id(CONVERSATION, wire.run_id, "local.user/1"),
        "content": "",
    }
    assert parsed.attachment_ids == []
    assert parsed.input_payload == {
        "messages": [{**public, "additional_kwargs": {OMITTED_MEDIA_KEY: True}}]
    }
    response = ConversationRunInputResponse.model_construct(input_payload=parsed.input_payload)
    assert response.model_dump(include={"input_payload"}) == {
        "input_payload": {"messages": [public]}
    }
    assert "Skipping unsupported AG-UI media" in caplog.text
    assert source["value"] not in caplog.text


@pytest.mark.parametrize(
    "message",
    [
        {"role": "user", "content": "", "additional_kwargs": {OMITTED_MEDIA_KEY: True}},
        {"role": "user", "content": "", OMITTED_MEDIA_KEY: True},
        {
            "lc": 1,
            "type": "constructor",
            "id": ["HumanMessage"],
            "kwargs": {"content": "", "additional_kwargs": {OMITTED_MEDIA_KEY: True}},
        },
    ],
)
def test_public_graph_clients_cannot_create_omission_metadata(message: dict[str, Any]) -> None:
    adapter = TypeAdapter(PublicGraphInput[Any])
    with pytest.raises(ValidationError):
        adapter.validate_python({"messages": [message]})
    # The appearance of the key as plain text has no protocol meaning.
    safe = {"messages": [{"role": "user", "content": OMITTED_MEDIA_KEY}]}
    assert adapter.validate_python(safe) == safe


def test_attachment_only_edit_and_resource_only_send() -> None:
    uid = str(uuid4())
    edit = parse_start_input(
        request(
            [user("")],
            {"operation": "edit", "checkpointId": "c", "expectedHead": "h", "attachmentIds": [uid]},
        ),
        CONVERSATION,
    )
    assert (edit.checkpoint_id, edit.expected_head, edit.attachment_ids) == ("c", "h", [UUID(uid)])
    ref = {"kind": "artifact", "id": uid, "version_id": str(uuid4())}
    new = parse_start_input(
        request([user("")], {"resourceContext": {"resources": [ref]}}), CONVERSATION
    )
    assert new.input_payload["resource_context"] == [ref]


@pytest.mark.parametrize(
    ("messages", "controls"),
    [
        ([], {}),
        ([user("old"), {"id": "a", "role": "assistant", "content": "not a user delta"}], {}),
        ([{**user("child"), "subagentRunId": "child"}], {}),
        ([user("")], {}),
        ([user("hello\x00world")], {}),
        ([user([{"type": "text", "text": "\x00"}])], {}),
        (
            [
                user(
                    [
                        {
                            "type": "document",
                            "source": {"type": "file", "provider": "moldy", "value": "not-uuid"},
                        }
                    ]
                )
            ],
            {},
        ),
        ([user("x")], {"checkpointId": "c"}),
        ([user("x")], {"expectedHead": "old-head"}),
        ([user("x")], {"operation": "edit"}),
        ([user("x")], {"operation": "regenerate", "checkpointId": "c", "strategy": "interrupt"}),
        ([user("x")], {"operation": "retry"}),
        (
            [],
            {
                "operation": "retry",
                "retryInputId": "d6923cc5-8505-4809-a003-9d46d199347b",
                "strategy": "interrupt",
            },
        ),
        (
            [],
            {
                "operation": "retry",
                "retryInputId": "d6923cc5-8505-4809-a003-9d46d199347b",
                "expectedHead": "old-head",
            },
        ),
        ([user("x")], {"retryInputId": "d6923cc5-8505-4809-a003-9d46d199347b"}),
        (
            [],
            {
                "operation": "retry",
                "retryInputId": "d6923cc5-8505-4809-a003-9d46d199347b",
                "attachmentIds": ["d6923cc5-8505-4809-a003-9d46d199347b"],
            },
        ),
        ([user("x")], {"resourceContext": {"resources": [{"kind": "file", "id": "bad"}]}}),
    ],
)
def test_invalid_start_is_rejected(
    messages: list[dict[str, Any]], controls: dict[str, Any]
) -> None:
    with pytest.raises(HTTPException) as result:
        parse_start_input(request(messages, controls), CONVERSATION)
    assert result.value.status_code == 422


def test_retry_and_regenerate_ignore_echoed_history() -> None:
    old = {"id": "a", "role": "assistant", "content": "forged answer"}
    retry_id = uuid4()
    retry = parse_start_input(
        request([old], {"operation": "retry", "retryInputId": str(retry_id)}), CONVERSATION
    )
    assert retry.input_payload == {} and retry.retry_input_id == retry_id
    regen = parse_start_input(
        request([old], {"operation": "regenerate", "checkpointId": "cp"}), CONVERSATION
    )
    assert regen.operation == "regenerate" and regen.input_payload == {}


def test_wrong_thread_resume_and_overlarge_payload_do_not_start_chat() -> None:
    with pytest.raises(HTTPException):
        parse_start_input(request([user("x")]), uuid4())
    with pytest.raises(HTTPException):
        parse_start_input(
            request([user("x")], resume=[{"interruptId": "req", "status": "resolved"}]),
            CONVERSATION,
        )
    with pytest.raises(HTTPException):
        parse_start_input(request([user("x" * (256 * 1024))]), CONVERSATION)
