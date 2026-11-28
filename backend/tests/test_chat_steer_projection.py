"""The steer delivery ledger is private graph state; clients read its public receipts API."""

from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from langchain_core.messages import HumanMessage
from pydantic import ValidationError

from app.agent_runtime.ag_ui_adapter import moldy_event_to_ag_ui_events
from app.agent_runtime.langgraph_event_projection import project_stable_event
from app.agent_runtime.langgraph_protocol_adapter import (
    adapt_stream_mode_chunk,
    adapt_v3_protocol_event,
)
from app.agent_runtime.protocol_egress import project_and_redact_protocol_data
from app.agent_runtime.protocol_events import to_protocol_wire_event
from app.routers.conversation_agent_protocol_contracts import AgentCommandParams, UpdateStateRequest
from app.schemas.conversation_graph_input import PRIVATE_STEER_CHANNELS
from app.schemas.conversation_run_input import ConversationRunInputEditRequest
from app.services.conversation_history_projection import sanitize_checkpoint_payload


def _contains_private_state(value: Any) -> bool:
    if isinstance(value, Mapping):
        return not PRIVATE_STEER_CHANNELS.isdisjoint(value) or any(
            _contains_private_state(v) for v in value.values()
        )
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return any(_contains_private_state(v) for v in value)
    return value == "internal-receipt-id"


@pytest.mark.parametrize("channel", sorted(PRIVATE_STEER_CHANNELS))
@pytest.mark.parametrize("method", ["values", "updates", "checkpoints"])
@pytest.mark.parametrize("v3", [True, False])
def test_streams_and_replay_omit_private_receipts(method: str, v3: bool, channel: str) -> None:
    state = {
        "progress": "researching",
        channel: {"run_id": "run", "ids": ["internal-receipt-id"]},
    }
    data = {"node": state, "messages": [{"type": "human", "content": "국내 사례만"}]}
    if v3:
        event = adapt_v3_protocol_event(
            {"method": method, "seq": 1, "params": {"data": data}},
            run_id="run",
            thread_id="chat",
        )
    else:
        event = adapt_stream_mode_chunk((method, data), run_id="run", thread_id="chat", seq=1)
    projected = project_stable_event(event, next_seq=1)
    assert projected.wire_event["data"] == {
        "node": {"progress": "researching"},
        "messages": [{"type": "human", "content": "국내 사례만"}],
    }
    public = to_protocol_wire_event(projected.wire_event)
    for view in (
        projected.persistable_event,
        moldy_event_to_ag_ui_events(public, thread_id="chat", run_id="run"),
        project_and_redact_protocol_data("protocol_replay", [to_protocol_wire_event(event)]),
    ):
        assert not _contains_private_state(view)
    assert state[channel] == {"run_id": "run", "ids": ["internal-receipt-id"]}


@pytest.mark.parametrize("channel", sorted(PRIVATE_STEER_CHANNELS))
def test_private_channel_is_removed_before_masking_dynamic_secret_keys(channel: str) -> None:
    data = {channel: {"ids": ["internal-receipt-id"]}}
    projected = project_and_redact_protocol_data("values", data, secret_values=(channel[6:12],))
    assert projected == {}


@pytest.mark.parametrize("channel", sorted(PRIVATE_STEER_CHANNELS))
def test_retained_checkpoint_preserves_messages_and_public_channels(channel: str) -> None:
    text = '{"' + channel + '": "example of authored JSON"}'
    checkpoint = sanitize_checkpoint_payload(
        {"progress": "done", channel: {"ids": ["internal-receipt-id"]}},
        [HumanMessage(content=text, id="message-id")],
        (),
    )
    assert checkpoint.channel_values == {"progress": "done"}
    assert isinstance(checkpoint.messages[0], dict)
    assert checkpoint.messages[0]["content"] == text
    assert checkpoint.messages[0]["id"] == "message-id"


@pytest.mark.parametrize("channel", sorted(PRIVATE_STEER_CHANNELS))
@pytest.mark.parametrize(
    ("model", "field", "extra"),
    [
        (AgentCommandParams, "input", {}),
        (UpdateStateRequest, "values", {}),
        (ConversationRunInputEditRequest, "input", {"expected_revision": 1}),
    ],
)
def test_public_graph_fields_reserve_only_top_level_receipts(
    model: type[AgentCommandParams | UpdateStateRequest | ConversationRunInputEditRequest],
    field: str,
    extra: dict[str, Any],
    channel: str,
) -> None:
    with pytest.raises(ValidationError, match="managed by the runtime"):
        model.model_validate({**extra, field: {channel: None}})

    ordinary = {"messages": [], "user_data": {channel: "example"}}
    validated = model.model_validate({**extra, field: ordinary})
    assert getattr(validated, field) == ordinary
