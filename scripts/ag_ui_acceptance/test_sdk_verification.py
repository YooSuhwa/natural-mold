"""Acceptance receipt regressions for historical captures that omit the request."""

# ruff: noqa: S101 -- pytest assertion statements.
import json
from pathlib import Path

import pytest
from sdk_verification import _receipt, typescript_receipts


@pytest.mark.parametrize(
    ("filename", "name", "status"),
    [
        ("http-start-transcript.json", "start", "failed"),
        ("http-recover-transcript.json", "recover", "passed"),
        ("ag-ui-approval-single-continuation.json", "single-continuation", "failed"),
    ],
)
def test_only_read_recovery_can_omit_input(
    tmp_path: Path, filename: str, name: str, status: str
) -> None:
    capture = tmp_path / filename
    capture.write_text(
        json.dumps({"expect": {"threadId": "thread", "runId": "run"}, "events": [{}]})
    )
    actual = _receipt(
        capture,
        {
            "name": name,
            "outcome": "success",
            "frames": 1,
            "requestChecked": False,
            "interruptIds": [],
            "toolCallIds": [],
            "messageIds": ["opaque-fixture-text"],
        },
    )
    assert actual["status"] == status
    assert actual["sdk_stream_checked"]
    assert not actual["request_checked"]
    assert "opaque-fixture-text" not in json.dumps(actual)


def test_empty_capture_directory_fails_with_all_requested_names(tmp_path: Path) -> None:
    receipt = typescript_receipts(tmp_path, tmp_path, {"PATH": ""}, tmp_path)
    assert receipt["status"] == "failed"
    assert len(receipt["missing"]) == 8
    assert receipt["requested"] == receipt["missing"]
    assert json.loads((tmp_path / "sdk-transcripts.json").read_bytes()) == receipt
