"""Keep a read-only record of what the user saw before a model stream failed."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from io import StringIO
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent_runtime.protocol_egress import project_and_redact_protocol_data
from app.agent_runtime.stream_retry_policy import PartialModelStreamError
from app.marketplace.redaction import prepare_secret_values

PARTIAL_STREAM_ERROR = PartialModelStreamError.code
PARTIAL_OUTPUT_KEY = "partial_model_output"
_MAX_TEXT = 16_384
_MAX_TOOLS = 32

type ToolState = Literal["unknown", "started", "completed"]


class PartialTool(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=128)
    state: ToolState


class PartialOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(max_length=_MAX_TEXT)
    tools: list[PartialTool] = Field(max_length=_MAX_TOOLS)
    truncated: bool


def _public(value: str, secret_values: Collection[str]) -> str:
    return project_and_redact_protocol_data("messages", value, secret_values=secret_values)


def read_partial_output(value: object, secret_values: Collection[str]) -> dict[str, Any] | None:
    try:
        output = PartialOutput.model_validate(value)
    except ValidationError:
        return None
    # Only strings intended for display go through value replacement. State
    # machine labels remain well-typed even if a credential equals their name.
    text = _public(output.text, secret_values)
    return {
        "text": text[:_MAX_TEXT],
        "tools": [
            {"name": _public(t.name, secret_values)[:128], "state": t.state} for t in output.tools
        ],
        "truncated": output.truncated or len(text) > _MAX_TEXT,
    }


def capture_partial_output(
    events: Sequence[Mapping[str, Any]],
    *,
    run_id: str,
    thread_id: str,
    secret_values: Collection[str],
) -> PartialOutput:
    """Project the unfinished root answer and the observed state of its tool calls."""
    secrets = prepare_secret_values(secret_values)
    # Look beyond the display boundary to mask a known value spanning the cut.
    read_limit = _MAX_TEXT + max((len(s) for s in secrets), default=0)
    text = StringIO()
    tools: dict[str, PartialTool] = {}
    clipped_tools = False
    ai = False
    for event in events:
        data = event.get("data")
        if not isinstance(data, Mapping) or event.get("namespace") not in (None, [], ()):
            continue
        if event.get("run_id", run_id) != run_id or event.get("thread_id", thread_id) != thread_id:
            continue
        method = event.get("method")
        kind = data.get("event") if method is not None else event.get("event")
        if not isinstance(kind, str):
            continue
        name = call_id = None
        state: ToolState = "unknown"
        delta = None
        if method == "messages":
            if kind in {"message-start", "message-finish"}:
                ai = kind == "message-start" and data.get("role") == "ai"
                text = StringIO()  # completed messages already live in the checkpoint
            elif ai and kind == "content-block-delta":
                block = data.get("delta")
                if isinstance(block, Mapping) and block.get("type") == "text-delta":
                    delta = block.get("text")
            elif ai and kind in {"content-block-start", "content-block-finish"}:
                block = data.get("content")
                if isinstance(block, Mapping) and block.get("type") in (
                    "tool_call_chunk",
                    "tool_call",
                ):
                    call_id, name = block.get("id"), block.get("name")
        elif method == "tools" or method is None:
            if method is None and kind == "content_delta":
                delta = data.get("delta")
            if kind in {
                "tool-started",
                "tool-finished",
                "tool-error",
                "tool_call_start",
                "tool_call_result",
            }:
                call_id, name = data.get("tool_call_id"), data.get("tool_name") or data.get("name")
                if kind == "tool-started":
                    state = "started"
                elif kind in {"tool-finished", "tool_call_result"}:
                    output = data.get("output")
                    statuses = (
                        data.get("status"),
                        output.get("status") if isinstance(output, Mapping) else None,
                    )
                    returned = all(
                        status in (None, "complete", "completed", "success") for status in statuses
                    )
                    state = "completed" if returned and not data.get("is_error") else "unknown"
                    if kind == "tool_call_result":
                        text = StringIO()
        if isinstance(delta, str) and text.tell() <= read_limit:
            text.write(delta[: read_limit + 1 - text.tell()])
        if not isinstance(call_id, str) or not call_id or len(call_id) > 256:
            continue
        if prior := tools.get(call_id):
            # Model tool-call fragments describe intent, never a new dispatch.
            if method != "messages" and kind != "tool_call_start":
                prior.state = state
        elif isinstance(name, str) and 0 < len(name) <= 128:
            if len(tools) < _MAX_TOOLS:
                tools[call_id] = PartialTool(name=_public(name, secrets)[:128], state=state)
            else:
                clipped_tools = True

    raw = text.getvalue()
    cut = min(_MAX_TEXT, len(raw))
    # Moving a cut before one credential may enter an overlapping credential.
    # The cut strictly decreases until no full known value straddles it.
    if cut < len(raw):
        previous = -1
        while previous != cut:
            previous = cut
            for secret in secrets:
                crossing = raw.find(secret, max(0, cut - len(secret) + 1), cut + len(secret))
                if 0 <= crossing < cut < crossing + len(secret):
                    cut = crossing
    safe = _public(raw[:cut], secrets)
    return PartialOutput(
        text=safe[:_MAX_TEXT],
        tools=list(tools.values()),
        truncated=clipped_tools or cut < len(raw) or len(safe) > _MAX_TEXT,
    )
