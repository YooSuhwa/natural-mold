"""Virtual history and Deep Agents spill-pointer projection contracts."""

from __future__ import annotations

import hashlib

import pytest

from app.agent_runtime.offload_protocol_projection import project_offload_egress_data
from app.agent_runtime.offload_storage_types import OffloadKind, logical_offload_id

_OWNER = "a" * 32
_CONVERSATION = "b" * 32
_ACTOR = "c" * 32


@pytest.mark.parametrize(
    "suffix",
    [
        "session_0123456789abcdef0123456789abcdef.md",
        "media/0123456789abcdef.png",
    ],
)
def test_exact_virtual_history_reference_preserves_context(suffix: str) -> None:
    path = f"/.moldy-offload/{_OWNER}/{_CONVERSATION}/{_ACTOR}/conversation_history/{suffix}"
    raw = f"summary={path}; artifact=/conversations/report.md"

    projected = project_offload_egress_data(raw)

    assert projected == (
        f"summary={logical_offload_id(OffloadKind.HISTORY, path)}; "
        "artifact=/conversations/report.md"
    )


def test_exact_legacy_history_media_reference_is_projected() -> None:
    path = "/conversation_history/media/0123456789abcdef.webp"

    projected = project_offload_egress_data(f"media={path}")

    assert projected == f"media={logical_offload_id(OffloadKind.HISTORY, path)}"


def _tool_pointer(path: str, message_id: str = "call.a/b\\c") -> str:
    return (
        f"Tool result too large, the result of this tool call {message_id} was saved in the "
        f"filesystem at this path: {path}\n\n"
        "You can read the result from the filesystem by using the read_file tool."
    )


@pytest.mark.parametrize("scoped", [True, False])
def test_deep_agents_tool_pointer_requires_containing_tool_call_id(scoped: bool) -> None:
    leaf = "call_a_b_c"
    path = (
        f"/.moldy-offload/{_OWNER}/{_CONVERSATION}/{_ACTOR}/large_tool_results/{leaf}"
        if scoped
        else f"/large_tool_results/{leaf}"
    )
    raw = {
        "type": "tool",
        "tool_call_id": "call.a/b\\c",
        "content": _tool_pointer(path),
    }

    projected = project_offload_egress_data(raw)

    assert path not in projected["content"]
    assert logical_offload_id(OffloadKind.SPILL, path) in projected["content"]


def test_tool_pointer_projects_inside_content_blocks() -> None:
    path = "/large_tool_results/call_a_b_c"
    raw = {
        "tool_call_id": "call.a/b\\c",
        "content_blocks": [{"type": "text", "text": _tool_pointer(path)}],
    }

    projected = project_offload_egress_data(raw)

    assert logical_offload_id(OffloadKind.SPILL, path) in projected["content_blocks"][0]["text"]


def test_nested_non_tool_mapping_cannot_borrow_parent_tool_call_id() -> None:
    path = "/large_tool_results/call_a_b_c"
    raw = {
        "tool_call_id": "call.a/b\\c",
        "metadata": {"text": _tool_pointer(path)},
        "content_blocks": [{"type": "text", "text": _tool_pointer(path)}],
    }

    projected = project_offload_egress_data(raw)

    assert projected["metadata"]["text"] == "internal_reference_redacted"
    assert logical_offload_id(OffloadKind.SPILL, path) in projected["content_blocks"][0]["text"]


def test_tool_pointer_mapping_id_mismatch_redacts_without_secret_hash_oracle() -> None:
    path = f"/.moldy-offload/{_OWNER}/{_CONVERSATION}/{_ACTOR}/large_tool_results/call_a_b_c"
    secret = "OPAQUE_SECRET"
    raw = {
        "tool_call_id": "different-id",
        "content": f"{_tool_pointer(path)} secret={secret}",
    }

    projected = project_offload_egress_data(raw)

    assert projected["content"] == "internal_reference_redacted"
    assert secret not in projected["content"]
    assert hashlib.sha256(secret.encode()).hexdigest()[:24] not in projected["content"]


@pytest.mark.parametrize(
    "path",
    [
        f"/.moldy-offload/{_OWNER}/{_CONVERSATION}/{_ACTOR}/large_tool_results/call_a_b_c",
        "/large_tool_results/call_a_b_c",
    ],
)
def test_spill_path_in_arbitrary_prose_fails_closed(path: str) -> None:
    projected = project_offload_egress_data(f"prefix {path} secret=OPAQUE")

    assert projected == "internal_reference_redacted"
    assert "spill_" not in projected


def test_scoped_virtual_spill_without_a_tool_message_is_never_hashed() -> None:
    path = f"/.moldy-offload/{_OWNER}/{_CONVERSATION}/{_ACTOR}/large_tool_results/call_a_b_c"

    assert project_offload_egress_data(path) == "internal_reference_redacted"


def test_invalid_history_media_reference_fails_closed() -> None:
    path = "/conversation_history/media/not-a-server-file.png"

    assert project_offload_egress_data(path) == "internal_reference_redacted"
