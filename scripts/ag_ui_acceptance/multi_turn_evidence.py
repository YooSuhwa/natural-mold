"""Retain bounded multi-turn diagnostics independently of browser assertions."""

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

TURN_ATTACHMENT = "chat-turn-observations"
TURN_TITLE = "a multi-turn conversation keeps both exchanges"

type Count = Annotated[int, Field(ge=0, le=64)]
type HttpStatus = Annotated[int, Field(ge=0, le=0)] | Annotated[int, Field(ge=100, le=599)]


class _Row(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, extra="forbid", strict=True)


class _Command(_Row):
    wire: Literal["ag_ui", "v3"]
    strategy: Literal["normal", "enqueue", "interrupt", "other"]
    status: HttpStatus
    request_slot: Count
    physical_slot: Count
    input_slot: Count


class _Message(_Row):
    role: Literal["user", "assistant", "other"]
    slot: Count
    has_content: bool


class _Run(_Row):
    physical_slot: Count
    status: Literal[
        "queued",
        "running",
        "completed",
        "interrupted",
        "failed",
        "canceling",
        "canceled",
        "stale",
        "other",
    ]
    source: Literal["chat", "start", "edit", "regenerate", "resume", "other"]


class _Input(_Row):
    slot: Count
    physical_slot: Count
    status: Literal["pending", "claimed", "failed", "canceled", "other"]


class _TurnObservation(_Row):
    stage: Literal[
        "starting", "first-saved", "second-sent", "second-visible", "both-saved", "both-restored"
    ]
    commands: Annotated[list[_Command], Field(max_length=8)]
    messages_status: HttpStatus
    history_status: HttpStatus
    inputs_status: HttpStatus
    stored: Annotated[list[_Message], Field(max_length=16)]
    runs: Annotated[list[_Run], Field(max_length=8)]
    items: Annotated[list[_Input], Field(max_length=8)]
    queue_paused: bool | None
    rendered_users: Count
    rendered_assistants: Count


def multi_turn_observations(
    filename: str,
    title: str,
    group: str,
    result: dict,
    owned_root: Path,
    read_body: Callable[[dict, Path, str], bytes],
) -> dict:
    if (
        filename != "chat-interactions.spec.ts"
        or title != TURN_TITLE
        or group not in {"ordinary-regressions-ag_ui", "ordinary-regressions-langgraph_v3"}
    ):
        return {}
    attachments = result.get("attachments")
    if not isinstance(attachments, list):
        return {}
    matches = [
        item
        for item in attachments
        if isinstance(item, dict) and item.get("name") == TURN_ATTACHMENT
    ]
    if len(matches) != 1 or matches[0].get("contentType") != "application/json":
        return {}
    try:
        raw = read_body(matches[0], owned_root, "multi-turn")
        observed = _TurnObservation.model_validate_json(raw)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        return {}
    return {TURN_ATTACHMENT: observed.model_dump(mode="json", by_alias=True)}
