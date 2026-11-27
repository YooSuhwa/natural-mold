"""Verify the native and middleware approval paths against their held snapshot receipts."""

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

READINESS_FILE = "ag-ui-approval-readiness.spec.ts"
READINESS_GROUP = "standard-client-regressions"
_HELD_SNAPSHOT = "once after the durable approval snapshot arrives"
READINESS_REASONS = {
    f"submits the first native answer {_HELD_SNAPSHOT}": "ask_user",
    f"submits the first middleware-backed answer {_HELD_SNAPSHOT}": "tool_approval",
}
_ATTACHMENT = "approval-readiness"
type Count = Annotated[int, Field(ge=0, le=200)]
type HttpStatus = Annotated[int, Field(ge=0, le=0)] | Annotated[int, Field(ge=100, le=599)]
type Reason = Literal[
    "none", "ask_user", "tool_approval", "auth_required", "input_required", "other"
]


class _ReadinessReceipt(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, strict=True, extra="forbid")

    selected_requests: Count
    targeted_requests: Count
    intercepted_reads: Count
    successful_reads: Count
    interrupted_reads: Count
    malformed_reads: Count
    held_reads: Count
    resume_posts_before_release: Count
    resume_posts: Count
    completed_resume_runs: Count
    saved_answers: Count
    last_http_status: HttpStatus
    terminal_reason: Reason
    last_snapshot_reason: Reason
    same_thread: bool
    same_run: bool
    same_slot: bool
    same_physical_run: bool
    same_action_contract: bool
    has_checkpoint: bool
    actionable: bool
    locked_before_release: bool
    effect_verified: bool

    def confirms(self, expected_reason: str) -> bool:
        return (
            self.selected_requests >= 1
            and self.intercepted_reads >= 1
            and self.successful_reads >= 1
            and self.interrupted_reads >= 1
            and self.held_reads >= 1
            and self.last_http_status == 200
            and self.terminal_reason == self.last_snapshot_reason == expected_reason
            and self.same_thread
            and self.same_run
            and self.same_slot
            and self.same_physical_run
            and self.same_action_contract
            and self.has_checkpoint
            and self.actionable
            and self.locked_before_release
            and self.resume_posts_before_release == 0
            and self.resume_posts == self.completed_resume_runs == self.saved_answers == 1
            and self.effect_verified
        )


def verify_readiness_receipt(
    result: dict,
    expected_reason: str,
    root: Path,
    read_body: Callable[[dict, Path, str], bytes],
) -> tuple[dict, list[str]]:
    attachments = result.get("attachments")
    matches = (
        [
            entry
            for entry in attachments
            if isinstance(entry, dict) and entry.get("name") == _ATTACHMENT
        ]
        if isinstance(attachments, list)
        else []
    )
    if len(matches) != 1 or matches[0].get("contentType") != "application/json":
        return {}, ["missing_or_invalid_approval_readiness"]
    try:
        receipt = _ReadinessReceipt.model_validate_json(read_body(matches[0], root, _ATTACHMENT))
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        return {}, ["missing_or_invalid_approval_readiness"]
    observations = {_ATTACHMENT: receipt.model_dump(mode="json", by_alias=True)}
    return observations, [] if receipt.confirms(expected_reason) else [
        "incomplete_approval_readiness"
    ]
