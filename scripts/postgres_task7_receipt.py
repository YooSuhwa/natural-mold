"""Validate and preserve Task 7 history evidence before owned-root cleanup."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Final, Literal, TypedDict
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

TASK7_HISTORY_NODE_ID: Final = (
    "tests/integration/test_conversation_history_snapshot.py::"
    "test_retained_history_materializes_after_owner_membership_loss"
)
TASK7_HISTORY_RECEIPT_NAME: Final = "task7-conversation-history-receipt.json"


class Task7HistoryReceipt(TypedDict):
    checkpoint_count: int
    event_count: int
    checksum: str
    secret_hits: int
    source_agent_present: bool
    owner_membership_status: str
    provider_invocations: int


class _SelectedNodesReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    selected_node_ids: tuple[str, ...]


class _Task7HistoryReceiptPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    manifest_id: UUID
    checkpoint_count: Annotated[int, Field(strict=True, gt=0)]
    event_count: Annotated[int, Field(strict=True, ge=0)]
    checksum: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    secret_hits: Literal[0]
    source_agent_present: Literal[True]
    owner_membership_status: Literal["removed"]
    provider_invocations: Literal[0]


class Task7ReceiptError(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def __str__(self) -> str:
        return self.reason


def capture_task7_history_receipt(
    run_root: Path, pytest_receipt: Path, *, child_succeeded: bool
) -> Task7HistoryReceipt | None:
    if not child_succeeded:
        return None
    try:
        selected = _SelectedNodesReceipt.model_validate_json(
            pytest_receipt.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValidationError) as error:
        raise Task7ReceiptError("pytest_receipt_selected_nodes_invalid") from error
    if TASK7_HISTORY_NODE_ID not in selected.selected_node_ids:
        return None
    receipt_path = run_root / TASK7_HISTORY_RECEIPT_NAME
    if not receipt_path.is_file():
        raise Task7ReceiptError("task7_history_receipt_missing")
    try:
        payload = _Task7HistoryReceiptPayload.model_validate_json(
            receipt_path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValidationError) as error:
        raise Task7ReceiptError("task7_history_receipt_invalid") from error
    return {
        "checkpoint_count": payload.checkpoint_count,
        "event_count": payload.event_count,
        "checksum": payload.checksum,
        "secret_hits": payload.secret_hits,
        "source_agent_present": payload.source_agent_present,
        "owner_membership_status": payload.owner_membership_status,
        "provider_invocations": payload.provider_invocations,
    }
