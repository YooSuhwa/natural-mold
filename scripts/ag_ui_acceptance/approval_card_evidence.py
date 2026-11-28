"""Read failure-only categorical receipts for the two ordinary approval-card checks."""

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

_ATTACHMENT = "approval-card-observations"
_CASES = {
    ("hitl-approval.spec.ts", "rejecting an execute_in_skill approval skips the tool"),
    ("tool-approval.spec.ts", "a conversation grant lifts the card for later calls"),
}
Count = Annotated[int, Field(ge=0, le=10_000)]
Status = Annotated[int, Field(ge=100, le=599)]


class _CardObservation(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, strict=True, extra="forbid")

    schema_version: Annotated[int, Field(ge=1, le=1)]
    post_count: Count
    post_status: Status | None
    terminal: Literal["none", "success", "interrupt", "cancelled", "error", "unknown"]
    terminal_slot_count: Count
    terminal_approval_count: Count
    snapshot_count: Count
    snapshot_status: Status | None
    durable: Literal["none", "active", "interrupted", "idle", "unavailable"]
    durable_slot_count: Count
    durable_approval_count: Count
    durable_read_only_count: Count
    same_slots: bool | None
    same_run: bool | None
    has_checkpoint: bool
    grant_read_status: Status | None
    approve_visible: bool
    grant_visible: bool


def approval_card_observations(
    filename: str,
    title: str,
    group: str,
    result: dict,
    owned_root: Path,
    read_body: Callable[[dict, Path, str], bytes],
) -> dict:
    if (
        (filename, title) not in _CASES
        or group not in {"ordinary-regressions-ag_ui", "ordinary-regressions-langgraph_v3"}
        or result.get("status") not in {"failed", "timedOut"}
    ):
        return {}
    raw_attachments = result.get("attachments")
    if not isinstance(raw_attachments, list):
        return {}
    attachments = [
        item
        for item in raw_attachments
        if isinstance(item, dict) and item.get("name") == _ATTACHMENT
    ]
    if len(attachments) != 1 or attachments[0].get("contentType") != "application/json":
        return {}
    try:
        raw = read_body(attachments[0], owned_root, "approval")
        receipt = _CardObservation.model_validate_json(raw)
    except (KeyError, OSError, TypeError, ValueError):
        return {}
    return {_ATTACHMENT: receipt.model_dump(by_alias=True)}
