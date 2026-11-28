from __future__ import annotations

from typing import Any
from uuid import UUID, uuid5

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from app.agent_runtime.attachment_inputs import (
    ATTACHMENT_REFS_KEY,
    AttachmentInputMiddleware,
    AttachmentInputs,
)
from app.schemas.conversation_graph_input import STEER_INPUT_MARKER
from app.services.ag_ui_snapshot_history import SnapshotMessageRef
from app.services.ag_ui_snapshot_messages import legacy_user_messages, public_checkpoint_message

CHAT = UUID("62389977-f90d-4788-a0bb-83500832946b")
RUN = UUID("16036494-9440-40e5-98a4-bce69223c6e7")


def _message(value: BaseMessage | dict[str, Any], index: int = 8) -> dict[str, Any] | None:
    ref = SnapshotMessageRef(value, index, "checkpoint", None)
    return public_checkpoint_message(ref, CHAT, secret_values={"project-canary-value"})


@pytest.mark.parametrize(
    "saved",
    [
        SystemMessage(content="private"),
        {"role": "developer", "content": "private"},
        {"role": "arbitrary", "content": "private"},
        HumanMessage(name="moldy_recalled_memories", content="private"),
        {"type": "human", "name": "moldy_conversation_goal", "content": "private"},
        HumanMessage(content="private", additional_kwargs={"lc_source": "summarization"}),
        HumanMessage(content="private", response_metadata={STEER_INPUT_MARKER: True}),
        {"type": "human", "content": "private", "response_metadata": {STEER_INPUT_MARKER: True}},
        AIMessage(content=[{"type": "reasoning", "text": "private"}]),
        AIMessage(content='{"tools":["private internal selector result"]}'),
        ToolMessage(content="private", name="ToolSelectionResponse", tool_call_id="internal-call"),
    ],
)
def test_internal_roles_and_model_only_messages_are_skipped(saved: Any) -> None:
    assert _message(saved) is None


def test_committed_content_and_message_identity_survive_restoration() -> None:
    saved = HumanMessage(content="ToolSelectionResponse라는 이름도 본문에 쓸 수 있어요")
    assert _message(saved) == {
        "id": str(uuid5(CHAT, "8")),
        "role": "user",
        "content": saved.content,
    }
    saved_ai = AIMessage(
        id="runtime-id-7",
        content=[
            {"type": "text", "text": "완료 "},
            {"type": "text", "text": "project-canary-value"},
            {"type": "thinking", "text": "private thought"},
            {"type": "provider-extra", "text": "private unknown block"},
            {"type": "image_url", "image_url": "data:image/png;base64,hidden"},
        ],
        response_metadata={"private": "model-side metadata"},
    )
    msg = _message(saved_ai)
    assert msg and set(msg) == {"id", "role", "content"}
    assert msg["id"] == "runtime-id-7" and msg["role"] == "assistant"
    assert msg["content"].startswith("완료 ")
    assert not any(x in msg["content"] for x in ("project-canary-value", "private", "data:"))
    assert _message(saved_ai.model_dump()) == msg


def test_attachment_model_call_caption_is_absent_from_live_and_retained_history() -> None:
    original = HumanMessage(
        id="user-image-turn",
        content="여기에 뭐가 보여?",
        additional_kwargs={
            ATTACHMENT_REFS_KEY: [
                {
                    "id": "revoked-file",
                    "filename": "private-file.png",
                    "mime_type": "image/png",
                    "form": "image_description",
                }
            ]
        },
    )
    middleware = AttachmentInputMiddleware(AttachmentInputs(files={}, supports_vision=False))
    model_input = middleware._expand([original], {(0, 0): "private machine-written caption"})
    assert model_input and "private machine-written caption" in str(model_input[0].content)
    expected = {"id": "user-image-turn", "role": "user", "content": original.content}
    assert _message(original) == expected
    assert _message(original.model_dump()) == expected


def test_checkpoint_tools_restore_only_linkage_and_never_private_arguments_or_files() -> None:
    ai = AIMessage(
        id="tool-parent",
        content="",
        tool_calls=[
            {"name": "ToolSelectionResponse", "id": "internal-call", "args": {"secret": "hidden"}},
            {"name": "read_file", "id": "read-call", "args": {"path": "/private/storage"}},
        ],
    )
    assert _message(ai) == {
        "id": "tool-parent",
        "role": "assistant",
        "content": "",
        "toolCalls": [
            {
                "id": "read-call",
                "type": "function",
                "function": {"name": "read_file", "arguments": "{}"},
            },
        ],
    }
    tool = ToolMessage(
        id="tool-result",
        content="private pinned artifact bytes",
        tool_call_id="read-call",
        artifact={"version_id": "old-version", "url": "/api/notifications/private-id/result"},
        additional_kwargs={"mcp_app": {"html": "private markup"}},
    )
    public = {"id": "tool-result", "role": "tool", "toolCallId": "read-call", "content": ""}
    assert _message(tool) == public
    assert _message(tool.model_dump()) == public


def _legacy(raw: dict[str, Any]) -> list[dict[str, Any]]:
    return legacy_user_messages(raw, conversation_id=CHAT, physical_run_id=RUN, secret_values=())


def test_legacy_history_only_reconstructs_the_last_genuine_user_input() -> None:
    data: dict[str, Any] = {
        "id": str(RUN),
        "input": {
            "messages": [
                {"role": "system", "content": "prompt"},
                {"role": "user", "content": "unverified history"},
                {"role": "assistant", "content": "unverified response"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "요약해줘"},
                        {"type": "reasoning", "text": "hidden"},
                    ],
                },
            ]
        },
    }
    start = {"event": "message_start", "data": data}
    assert _legacy(start) == [
        {"id": f"legacy:{RUN}:input:3", "role": "user", "content": "요약해줘"}
    ]
    for key, value in [
        ("thread_id", "unrelated-chat"),
        ("run_id", "foreign-run"),
        ("namespace", ["task:child"]),
        ("checkpoint_ns", "task:child"),
    ]:
        assert _legacy({**start, key: value}) == []
        assert _legacy({**start, "data": {**data, key: value}}) == []
    assert _legacy({**start, "data": {**data, "id": "unrelated-run"}}) == []
    data["input"]["messages"][-1]["id"] = "client-submitted-id"
    assert _legacy(start)[0]["id"] == "client-submitted-id"
    assert _legacy({**start, "event": "message_end"}) == []


@pytest.mark.parametrize(
    "payload",
    [
        {"resume": {"choice": "private approval payload"}},
        {"messages": []},
        {
            "messages": [
                {"role": "user", "content": "old"},
                {"role": "assistant", "content": "draft"},
            ]
        },
        {"messages": [{"content": "unknown user"}]},
        {"messages": [{"type": "human", "name": "moldy_recalled_memories", "content": "private"}]},
    ],
)
def test_legacy_resume_internal_or_missing_input_is_omitted(payload: Any) -> None:
    assert _legacy({"event": "message_start", "data": {"input": payload}}) == []
