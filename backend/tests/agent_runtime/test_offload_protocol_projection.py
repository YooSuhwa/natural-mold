"""Structural contracts for the public offload egress projection."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent_runtime.offload_protocol_projection import project_offload_egress_data
from app.agent_runtime.offload_storage_types import OffloadKind, logical_offload_id

_OWNER = "a" * 32
_CONVERSATION = "b" * 32
_ACTOR = "c" * 32
_SESSION = "session_0123456789abcdef0123456789abcdef.md"


def test_structural_summary_projects_valid_history_and_keeps_raw_state() -> None:
    path = f"/.moldy-offload/{_OWNER}/{_CONVERSATION}/{_ACTOR}/conversation_history/{_SESSION}"
    raw = {
        "_summarization_event": {
            "file_path": path,
            "history_id": "spoofed",
            "cutoff_index": 4,
        }
    }

    projected = project_offload_egress_data(raw)

    assert projected == {
        "_summarization_event": {
            "history_id": logical_offload_id(OffloadKind.HISTORY, path),
            "cutoff_index": 4,
        }
    }
    assert raw["_summarization_event"]["file_path"] == path
    assert raw["_summarization_event"]["history_id"] == "spoofed"


def test_projected_summary_is_idempotent() -> None:
    path = f"/.moldy-offload/{_OWNER}/{_CONVERSATION}/{_ACTOR}/conversation_history/{_SESSION}"
    raw = {"_summarization_event": {"file_path": path, "cutoff_index": 4}}

    first = project_offload_egress_data(raw)
    second = project_offload_egress_data(first)

    assert second == first


def test_replayed_structural_summary_is_idempotent_without_trusting_spill_ids() -> None:
    path = f"/.moldy-offload/{_OWNER}/{_CONVERSATION}/{_ACTOR}/conversation_history/{_SESSION}"
    stored = {
        "values": {
            "_summarization_event": {"file_path": path, "cutoff_index": 4},
        }
    }

    first = project_offload_egress_data(stored)
    replayed = project_offload_egress_data(first)

    assert replayed == first


@pytest.mark.parametrize(
    "reserved",
    [
        {"history_id": "history_not-valid"},
        {"spill_id": "spill_0123456789abcdef01234567"},
        {
            "history_id": "history_0123456789abcdef01234567",
            "spill_id": "spill_0123456789abcdef01234567",
        },
    ],
)
def test_projected_summary_rejects_invalid_or_wrong_reserved_ids(
    reserved: dict[str, str],
) -> None:
    projected = project_offload_egress_data({"_summarization_event": reserved})

    assert projected == {"_summarization_event": {"internal_reference_redacted": True}}


def test_ordinary_mapping_preserves_reserved_names_and_public_offload_path() -> None:
    raw = {
        "history_id": "customer-order-history-42",
        "spill_id": "domain-value",
        "offload_path": "/public/audit/report.json",
        "value": "keep",
    }

    first = project_offload_egress_data(raw)
    second = project_offload_egress_data(first)

    assert first == second == raw
    assert first is not raw


def test_nested_ordinary_tool_payload_preserves_reserved_names() -> None:
    raw = {
        "tool_call_id": "tool-1",
        "content": {
            "api_payload": {
                "history_id": "customer-order-history-42",
                "spill_id": "domain-value",
                "offload_path": "/public/audit/report.json",
            }
        },
    }

    projected = project_offload_egress_data(raw)

    assert projected == raw
    assert projected is not raw


def test_internal_marker_offload_path_fails_closed_and_discards_reserved_ids() -> None:
    projected = project_offload_egress_data(
        {
            "offload_path": "/private/.moldy-internal/offload/spill/unknown",
            "spill_id": "spoofed",
        }
    )

    assert projected == {"internal_reference_redacted": True}


def test_replayed_compaction_done_marker_preserves_only_a_valid_history_id() -> None:
    raw = {
        "name": "moldy.compaction",
        "payload": {"state": "done", "history_id": "history_0123456789abcdef01234567"},
    }

    first = project_offload_egress_data(raw)
    replayed = project_offload_egress_data(first)

    assert replayed == first == raw


def test_compaction_marker_ignores_spoofed_or_path_backed_history_ids() -> None:
    projected = project_offload_egress_data(
        {
            "name": "moldy.compaction",
            "payload": {
                "state": "done",
                "history_id": "history_0123456789abcdef01234567",
                "file_path": "/unknown/.moldy-internal/offload/history/secret",
            },
        }
    )

    assert projected["payload"] == {"state": "done", "file_path": "internal_reference_redacted"}


def test_structural_spill_supports_exact_deep_agents_sanitized_leaf() -> None:
    path = f"/.moldy-offload/{_OWNER}/{_CONVERSATION}/{_ACTOR}/large_tool_results/call_a_b_c"

    projected = project_offload_egress_data(
        {"offload_path": path, "spill_id": "spoofed", "status": "stored"}
    )

    assert projected == {
        "spill_id": logical_offload_id(OffloadKind.SPILL, path),
        "status": "stored",
    }


def test_summary_file_path_rejects_a_valid_spill_reference() -> None:
    spill_path = f"/.moldy-offload/{_OWNER}/{_CONVERSATION}/{_ACTOR}/large_tool_results/call_a_b_c"

    projected = project_offload_egress_data(
        {"_summarization_event": {"file_path": spill_path, "spill_id": "spoofed"}}
    )

    assert projected == {"_summarization_event": {"internal_reference_redacted": True}}


@pytest.mark.parametrize(
    "value",
    [
        "conversation_history/foo",
        "docs/conversation_history/foo",
        "/docs/conversation_history/foo",
        "/conversations/report.md",
        "An artifact lives at /conversations/report.md.",
    ],
)
def test_ordinary_paths_and_prose_are_byte_identical(value: str) -> None:
    assert project_offload_egress_data(value) == value


def test_default_root_is_read_at_call_time(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.agent_runtime import runtime_config

    path = tmp_path / f".moldy-internal/offload/history/{'a' * 32}/{'b' * 32}/{'c' * 32}"
    monkeypatch.setattr(runtime_config, "_DATA_DIR", tmp_path)

    projected = project_offload_egress_data(str(path))

    assert projected == logical_offload_id(OffloadKind.HISTORY, str(path))
