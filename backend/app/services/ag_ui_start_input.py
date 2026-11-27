"""Translate the public AG-UI start request to a single server-owned chat turn."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid5

from ag_ui.core import FileSource, RunAgentInput, TextPart, UserMessage
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.models.conversation_run_input import JsonValue
from app.schemas.chat_resource_context import ChatResourceContextRequest
from app.schemas.conversation_graph_input import OMITTED_MEDIA_KEY
from app.services.conversation_run_queue_payload import validate_queue_input_payload

logger = logging.getLogger(__name__)
OMITTED_MEDIA_SUFFIX = ":media-omitted"

type StartOperation = Literal["send", "edit", "regenerate", "retry"]
type QueueStrategy = Literal["enqueue", "interrupt"]


class _StartControls(BaseModel):
    # Ignore extensions we do not understand. Only these fields affect execution.
    model_config = ConfigDict(extra="ignore")

    operation: StartOperation = "send"
    strategy: QueueStrategy = "enqueue"
    checkpoint_id: str | None = Field(default=None, alias="checkpointId", min_length=1)
    expected_head: str | None = Field(default=None, alias="expectedHead", min_length=1)
    retry_input_id: UUID | None = Field(default=None, alias="retryInputId")
    attachment_ids: list[UUID] | None = Field(default=None, alias="attachmentIds")
    resource_context: ChatResourceContextRequest | None = Field(
        default=None, alias="resourceContext"
    )


@dataclass(frozen=True, slots=True)
class AgUiStartInput:
    operation: StartOperation
    strategy: QueueStrategy
    checkpoint_id: str | None
    expected_head: str | None
    retry_input_id: UUID | None
    attachment_ids: list[UUID]
    input_payload: dict[str, JsonValue]


def start_message_id(conversation_id: UUID, public_run_id: str, public_message_id: str) -> str:
    """Return a replay-stable graph message ID scoped to this command and conversation."""
    name = json.dumps(["ag-ui-user", public_run_id, public_message_id], separators=(",", ":"))
    return str(uuid5(conversation_id, name))


def validate_chat_text(text: str) -> None:
    if "\x00" in text:
        raise HTTPException(422, "Chat text cannot contain NUL characters")


def _controls(props: object) -> _StartControls:
    if not isinstance(props, dict) or "moldy" not in props:
        return _StartControls()
    try:
        return _StartControls.model_validate(props["moldy"])
    except ValidationError as exc:
        raise HTTPException(422, "Invalid AG-UI chat controls") from exc


def _user_content(message: UserMessage) -> tuple[str, list[UUID], bool]:
    if isinstance(message.content, str):
        return message.content, [], False
    texts: list[str] = []
    uploads: list[UUID] = []
    skipped = False
    for part in message.content:
        if isinstance(part, TextPart):
            texts.append(part.text)
        elif isinstance(part.source, FileSource) and part.source.provider == "moldy":
            try:
                uploads.append(UUID(part.source.value))
            except ValueError as exc:
                raise HTTPException(422, "Invalid Moldy attachment ID") from exc
        else:
            # Foreign file handles are opaque. Neither these nor URL/inline media
            # are dereferenced by the chat command or forwarded to the graph.
            skipped = True
    if skipped:
        logger.warning("Skipping unsupported AG-UI media in user message")
    return "".join(texts), uploads, skipped


def parse_start_input(run_input: RunAgentInput, conversation_id: UUID) -> AgUiStartInput:
    if run_input.thread_id != str(conversation_id):
        raise HTTPException(422, "AG-UI thread ID must match the conversation")
    if not run_input.run_id.strip() or run_input.resume:
        raise HTTPException(422, "Expected an AG-UI start request")
    controls = _controls(run_input.forwarded_props)
    fork = controls.operation in {"edit", "regenerate"}
    if fork != (controls.checkpoint_id is not None):
        raise HTTPException(422, "A checkpoint is required only for edit and regenerate")
    if not fork and controls.expected_head is not None:
        raise HTTPException(422, "Only a fork can name an expected branch head")
    if controls.operation != "send" and controls.strategy == "interrupt":
        raise HTTPException(422, "Only a new message can interrupt an active run")
    if (controls.operation == "retry") != (controls.retry_input_id is not None):
        raise HTTPException(422, "A stored input ID is required only for retry")
    attachment_ids = list(dict.fromkeys(controls.attachment_ids or []))
    payload: dict[str, JsonValue] = {}
    if controls.operation in {"send", "edit"}:
        last = run_input.messages[-1] if run_input.messages else None
        if (
            not isinstance(last, UserMessage)
            or last.subagent_run_id is not None
            or not last.id.strip()
        ):
            raise HTTPException(422, "The last message must be a root user message with an ID")
        text, media_ids, skipped = _user_content(last)
        validate_chat_text(text)
        if media_ids:
            if controls.attachment_ids is not None and set(attachment_ids) != set(media_ids):
                raise HTTPException(422, "Message attachments do not match the chat controls")
            if controls.attachment_ids is None:
                attachment_ids = list(dict.fromkeys(media_ids))
        message: dict[str, JsonValue] = {
            "role": "user",
            "id": start_message_id(conversation_id, run_input.run_id, last.id),
            "content": text,
        }
        if not text.strip() and not attachment_ids and not controls.resource_context:
            if not skipped:
                raise HTTPException(422, "A chat turn requires text, media, or resource context")
            # Retain the empty user turn and only the fact that media was skipped.
            message.update(content="", additional_kwargs={OMITTED_MEDIA_KEY: True})
        payload["messages"] = [message]
        if controls.resource_context is not None:
            payload["resource_context"] = controls.resource_context.model_dump(
                mode="json", exclude_none=True
            )["resources"]
    elif attachment_ids or controls.resource_context is not None:
        raise HTTPException(422, "Retry and regenerate use server-stored input")
    validate_queue_input_payload(
        {
            **payload,
            "attachmentIds": [str(uid) for uid in attachment_ids],
            "checkpointId": controls.checkpoint_id,
            "expectedHead": controls.expected_head,
        }
    )
    return AgUiStartInput(
        operation=controls.operation,
        strategy=controls.strategy,
        checkpoint_id=controls.checkpoint_id,
        expected_head=controls.expected_head,
        retry_input_id=controls.retry_input_id,
        attachment_ids=attachment_ids,
        input_payload=payload,
    )
