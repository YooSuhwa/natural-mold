"""Restore a checkpoint's MCP App only for its visible, currently authorized issuance."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID, uuid4

import pytest
from ag_ui.core import ActivitySnapshotEvent
from langchain_core.messages import AIMessage, ToolMessage

from app.agent_runtime import ag_ui_v1_activities, ag_ui_v1_issuance
from app.agent_runtime.message_utils import parse_msg_id
from app.agent_runtime.protocol_events import stored_protocol_event
from app.schemas.ag_ui_snapshot import AgUiSnapshotMessageLink
from app.schemas.mcp_apps import McpAppArtifact
from app.services.ag_ui_snapshot_assembly import AssembledSnapshot, SnapshotAssembly
from app.services.ag_ui_snapshot_history import SnapshotHistory, SnapshotMessageRef
from app.services.ag_ui_snapshot_messages import public_checkpoint_message
from app.services.ag_ui_snapshot_widgets import restore_snapshot_widgets
from tests.test_ag_ui_snapshot_assembly import PublicReplay

_CALL = "fixture-call"
_ISSUER = "model-message"
_PUBLIC_RUN = "public-run"


@dataclass
class _Case:
    thread: UUID
    physical: UUID
    binding: UUID
    tool: ToolMessage
    history: SnapshotHistory
    merged: AssembledSnapshot
    grants: dict[UUID, frozenset[tuple[str, str]]]

    def restore(self) -> None:
        restore_snapshot_widgets(
            conversation_id=self.thread,
            history=self.history,
            merged=self.merged,
            public_run_ids={self.physical: _PUBLIC_RUN},
            widget_bindings=self.grants,
        )

    @property
    def cards(self) -> list[dict[str, Any]]:
        return [row for row in self.merged.messages if row.get("activityType") == "moldy.mcp_app"]


def _case(
    *, thread: UUID | None = None, suffix: str = "", offset: int = 0, previous: str | None = None
) -> _Case:
    thread = thread or uuid4()
    physical, binding = uuid4(), uuid4()
    issuer_id, result_id = _ISSUER + suffix, "saved-tool-response" + suffix
    issuer_cp, result_cp = "issuer-checkpoint" + suffix, "result-checkpoint" + suffix
    issuer = AIMessage(
        id=issuer_id,
        content="",
        tool_calls=[{"type": "tool_call", "name": "fixture", "id": _CALL, "args": {}}],
    )
    widget = McpAppArtifact(
        binding_id=binding,
        run_id=physical,
        resource_uri="ui://fixture/private",
        tool_meta={"ui": {"resourceUri": "ui://fixture/private"}},
        result_meta={"private": "metadata"},
    )
    result = ToolMessage(
        id=result_id,
        content="Private model result",
        tool_call_id=_CALL,
        name="fixture",
        artifact={
            "mcp_app": widget.model_dump(mode="json", exclude_none=True),
            "structured_content": {"html": "<p>private widget result</p>"},
        },
    )
    refs = (
        SnapshotMessageRef(issuer, offset, issuer_cp, previous),
        SnapshotMessageRef(result, offset + 1, result_cp, issuer_cp),
    )
    history = SnapshotHistory(
        checkpoint_id=result_cp,
        messages=refs,
        source="checkpoint",
        lineage_checkpoint_ids=(issuer_cp, result_cp),
        checkpoint_run_ids={issuer_cp: physical, result_cp: physical},
    )
    checkpoint = []
    for ref, raw_id, parent in zip(refs, [issuer_id, result_id], [None, issuer_id], strict=True):
        row = public_checkpoint_message(ref, thread, secret_values=())
        assert row is not None
        link = AgUiSnapshotMessageLink(
            message_id=row["id"],
            physical_run_id=str(physical),
            run_id=_PUBLIC_RUN + suffix,
            ui_message_id=str(parse_msg_id(raw_id, thread, ref.original_index)),
            checkpoint_id=ref.checkpoint_id,
            parent_checkpoint_id=ref.parent_checkpoint_id,
            parent_message_id=parent,
        )
        checkpoint.append((row, link))
    assembly = SnapshotAssembly(checkpoint, thread_id=str(thread))
    assembly.add_run(physical, _PUBLIC_RUN + suffix, PublicReplay())
    merged = assembly.finish()
    return _Case(
        thread,
        physical,
        binding,
        result,
        history,
        merged,
        {physical: frozenset({(_CALL, str(binding))})},
    )


def test_checkpoint_widget_reuses_live_id_and_emits_only_a_scoped_pointer() -> None:
    case = _case()
    call = case.merged.messages[1]["toolCallId"]
    event = stored_protocol_event(
        run_id=str(case.physical),
        thread_id=str(case.thread),
        seq=7,
        method="messages",
        data=case.tool.model_dump(mode="json"),
    )
    activities = ag_ui_v1_activities.PublicActivities(
        readable_widget_bindings=case.grants[case.physical]
    )
    frames = activities.project(
        event, run_id=_PUBLIC_RUN, public_message_id=_ISSUER, public_tool_call_id=call
    )
    assert len(frames) == 1 and isinstance(frames[0], ActivitySnapshotEvent)
    live = frames[0]
    case.restore()
    case.restore()
    assert len(case.cards) == 1
    card = case.cards[0]
    assert card["id"] == live.message_id
    assert (
        card["content"]
        == live.content
        == {
            "run_id": str(case.physical),
            "binding_id": str(case.binding),
            "tool_call_id": _CALL,
            "public_tool_call_id": call,
            "public_message_id": _ISSUER,
        }
    )
    links = [link for link in case.merged.message_links if link.message_id == card["id"]]
    assert len(links) == 1
    assert links[0].parent_tool_call_id == call
    assert links[0].parent_message_id == _ISSUER
    assert links[0].run_id == _PUBLIC_RUN
    assert links[0].physical_run_id == str(case.physical)
    assert not links[0].subagent_run_id


@pytest.mark.parametrize(
    "mismatch",
    [
        "grant",
        "public-alias",
        "physical-link",
        "sibling-checkpoint",
        "result-call",
        "forged-binding",
        "forged-run",
        "no-binding",
        "error-result",
        "model-declared-binding",
        "duplicate-issuer",
        "duplicate-result",
    ],
)
def test_checkpoint_widget_requires_one_visible_issuance_and_result(mismatch: str) -> None:
    case = _case()
    issuer, result = case.history.messages
    if mismatch == "grant":
        case.grants = {uuid4(): frozenset({(_CALL, str(case.binding))})}
    elif mismatch == "public-alias":
        case.merged.message_links[1].run_id = "different-user-facing-run"
    elif mismatch == "physical-link":
        case.merged.message_links[1].physical_run_id = str(uuid4())
    elif mismatch == "sibling-checkpoint":
        case.history = replace(
            case.history, messages=(issuer, replace(result, checkpoint_id="sibling"))
        )
    elif mismatch == "result-call":
        case.merged.messages[1]["toolCallId"] = ag_ui_v1_issuance.public_tool_call_id(
            _PUBLIC_RUN, None, "elsewhere", _CALL
        )
    elif mismatch in {"forged-binding", "forged-run"}:
        key = "binding_id" if mismatch == "forged-binding" else "run_id"
        case.tool.artifact["mcp_app"][key] = str(uuid4())
    elif mismatch == "no-binding":
        case.tool.artifact = None
    elif mismatch == "error-result":
        case.tool.status = "error"
    elif mismatch == "model-declared-binding":
        case.tool.artifact = {
            "mcp_app": {"binding_id": str(case.binding), "run_id": str(case.physical)}
        }
    elif mismatch == "duplicate-issuer":
        other = AIMessage(
            id="another-issuer",
            content="",
            tool_calls=[{"name": "fixture", "id": _CALL, "args": {}}],
        )
        case.history = replace(
            case.history, messages=(issuer, replace(issuer, message=other), result)
        )
    else:
        other = case.tool.model_copy(update={"id": "another-result"})
        case.history = replace(
            case.history, messages=(issuer, result, replace(result, message=other))
        )
    case.restore()
    assert case.cards == []
    assert len(case.merged.message_links) == 2


def test_withdrawn_widget_grant_removes_previously_merged_activity_and_link() -> None:
    case = _case()
    case.restore()
    assert len(case.cards) == 1
    mid = case.cards[0]["id"]
    case.grants.clear()
    case.restore()
    assert case.cards == []
    assert all(link.message_id != mid for link in case.merged.message_links)
    assert [row["role"] for row in case.merged.messages] == ["assistant", "tool"]


def test_reused_raw_call_in_two_physical_runs_rehydrates_only_the_authorized_issuer() -> None:
    first = _case()
    second = _case(
        thread=first.thread, suffix="-second", offset=2, previous=first.history.checkpoint_id
    )
    history = replace(
        second.history,
        messages=first.history.messages + second.history.messages,
        lineage_checkpoint_ids=first.history.lineage_checkpoint_ids
        + second.history.lineage_checkpoint_ids,
        checkpoint_run_ids=first.history.checkpoint_run_ids | second.history.checkpoint_run_ids,
    )
    first.merged.messages.extend(second.merged.messages)
    first.merged.message_links.extend(second.merged.message_links)
    restore_snapshot_widgets(
        conversation_id=first.thread,
        history=history,
        merged=first.merged,
        public_run_ids={first.physical: _PUBLIC_RUN, second.physical: _PUBLIC_RUN + "-second"},
        widget_bindings=second.grants,
    )
    assert len(first.cards) == 1
    assert first.cards[0]["content"]["run_id"] == str(second.physical)
    assert first.cards[0]["content"]["binding_id"] == str(second.binding)
    assert first.cards[0]["content"][
        "public_tool_call_id"
    ] == ag_ui_v1_issuance.public_tool_call_id(
        _PUBLIC_RUN + "-second", None, _ISSUER + "-second", _CALL
    )
