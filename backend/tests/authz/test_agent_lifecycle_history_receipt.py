"""Contracts for preserving Task 7 history evidence before lane cleanup."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_ROOT.parent / "scripts"))

import postgres_test_runner  # noqa: E402

TASK7_NODE_ID = (
    "tests/integration/test_conversation_history_snapshot.py::"
    "test_retained_history_materializes_after_owner_membership_loss"
)


def _write_pytest_receipt(path: Path, selected_node_ids: list[str]) -> None:
    path.write_text(json.dumps({"selected_node_ids": selected_node_ids}), encoding="utf-8")


def _valid_history_receipt() -> dict[str, str | int | bool]:
    return {
        "manifest_id": "196ccdaa-1691-4dfe-81c7-9750d5cb3112",
        "checkpoint_count": 3,
        "event_count": 2,
        "checksum": "a" * 64,
        "secret_hits": 0,
        "source_agent_present": True,
        "owner_membership_status": "removed",
        "provider_invocations": 0,
    }


def test_task7_receipt_is_optional_when_history_node_was_not_selected(tmp_path: Path) -> None:
    # Given a successful legacy lane whose pytest receipt does not select Task 7.
    pytest_receipt = tmp_path / "pytest-receipt.json"
    _write_pytest_receipt(pytest_receipt, ["tests/example.py::test_other"])

    # When the runner checks for Task 7 evidence before cleanup.
    captured = postgres_test_runner._capture_task7_history_receipt(
        tmp_path, pytest_receipt, child_succeeded=True
    )

    # Then the old lane remains compatible without inventing a receipt.
    assert captured is None


def test_task7_receipt_is_required_after_selected_node_succeeds(tmp_path: Path) -> None:
    # Given a successful child that selected Task 7 but wrote no history receipt.
    pytest_receipt = tmp_path / "pytest-receipt.json"
    _write_pytest_receipt(pytest_receipt, [TASK7_NODE_ID])

    # When the runner captures evidence before deleting the owned run root.
    with pytest.raises(RuntimeError, match="^task7_history_receipt_missing$"):
        postgres_test_runner._capture_task7_history_receipt(
            tmp_path, pytest_receipt, child_succeeded=True
        )


def test_task7_receipt_does_not_mask_selected_child_failure(tmp_path: Path) -> None:
    # Given a failed child that selected Task 7 but could not publish its receipt.
    pytest_receipt = tmp_path / "pytest-receipt.json"
    _write_pytest_receipt(pytest_receipt, [TASK7_NODE_ID])

    # When the runner preserves the primary child failure.
    captured = postgres_test_runner._capture_task7_history_receipt(
        tmp_path, pytest_receipt, child_succeeded=False
    )

    # Then missing secondary evidence does not replace that failure.
    assert captured is None


def test_task7_receipt_rejects_malformed_selected_evidence(tmp_path: Path) -> None:
    # Given selected Task 7 evidence with a nonzero secret count.
    pytest_receipt = tmp_path / "pytest-receipt.json"
    _write_pytest_receipt(pytest_receipt, [TASK7_NODE_ID])
    malformed = _valid_history_receipt() | {"secret_hits": 1}
    (tmp_path / "task7-conversation-history-receipt.json").write_text(
        json.dumps(malformed), encoding="utf-8"
    )

    # When the runner parses the receipt trust boundary.
    with pytest.raises(RuntimeError, match="^task7_history_receipt_invalid$"):
        postgres_test_runner._capture_task7_history_receipt(
            tmp_path, pytest_receipt, child_succeeded=True
        )


def test_task7_receipt_rejects_invalid_json(tmp_path: Path) -> None:
    # Given selected Task 7 evidence that is not valid JSON.
    pytest_receipt = tmp_path / "pytest-receipt.json"
    _write_pytest_receipt(pytest_receipt, [TASK7_NODE_ID])
    (tmp_path / "task7-conversation-history-receipt.json").write_text("{invalid", encoding="utf-8")

    # When the runner parses the receipt trust boundary.
    with pytest.raises(RuntimeError, match="^task7_history_receipt_invalid$"):
        postgres_test_runner._capture_task7_history_receipt(
            tmp_path, pytest_receipt, child_succeeded=True
        )


def test_task7_receipt_captures_only_secret_free_summary(tmp_path: Path) -> None:
    # Given the exact Task 7 receipt schema and an extra manifest identifier.
    pytest_receipt = tmp_path / "pytest-receipt.json"
    _write_pytest_receipt(pytest_receipt, [TASK7_NODE_ID])
    receipt = _valid_history_receipt()
    (tmp_path / "task7-conversation-history-receipt.json").write_text(
        json.dumps(receipt), encoding="utf-8"
    )

    # When the runner captures the evidence before cleanup.
    captured = postgres_test_runner._capture_task7_history_receipt(
        tmp_path, pytest_receipt, child_succeeded=True
    )

    # Then only counts, checksum, and zero/provider/ownership status proofs survive.
    assert captured == {
        "checkpoint_count": 3,
        "event_count": 2,
        "checksum": "a" * 64,
        "secret_hits": 0,
        "source_agent_present": True,
        "owner_membership_status": "removed",
        "provider_invocations": 0,
    }
