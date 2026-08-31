from __future__ import annotations

from copy import deepcopy

from app.agent_runtime.protocol_egress import project_and_redact_protocol_data


def test_legacy_memory_event_separates_live_and_shared_views() -> None:
    payload = {
        "id": "proposal-1",
        "scope": "user",
        "content": "private preference",
        "reason": "private reason",
    }
    original = deepcopy(payload)

    live = project_and_redact_protocol_data(
        "memory_proposed",
        payload,
        redact_memory=False,
    )
    shared = project_and_redact_protocol_data("memory_proposed", payload)

    assert live["content"] == "private preference"
    assert live["reason"] == "private reason"
    assert shared["content"] == "<redacted>"
    assert shared["reason"] == "<redacted>"
    assert payload == original


def test_nested_legacy_and_canonical_memory_events_are_redacted() -> None:
    payload = [
        {
            "id": "legacy-memory-1",
            "event": "memory_saved",
            "data": {"id": "memory-1", "content": "saved body", "reason": "saved reason"},
        },
        {
            "method": "custom",
            "data": {
                "name": "moldy.memory_recalled",
                "payload": {"memories": [{"id": "memory-2", "scope": "user", "content": "recall"}]},
            },
        },
    ]

    shared = project_and_redact_protocol_data("share_traces", payload)

    assert shared[0]["data"]["content"] == "<redacted>"
    assert shared[0]["data"]["reason"] == "<redacted>"
    assert shared[1]["data"]["payload"]["memories"][0]["content"] == "<redacted>"


def test_memory_recalled_compatibility_envelopes_are_redacted() -> None:
    direct = project_and_redact_protocol_data(
        "custom:moldy.memory_recalled",
        {"payload": {"memories": [{"id": "m1", "content": "direct private body"}]}},
    )
    stored = project_and_redact_protocol_data(
        "protocol_replay",
        {
            "method": "custom:moldy.memory_recalled",
            "seq": 1,
            "namespace": [],
            "data": {"payload": {"memories": [{"id": "m2", "content": "stored private body"}]}},
        },
    )
    wire = project_and_redact_protocol_data(
        "protocol_replay",
        {
            "type": "event",
            "method": "custom:moldy.memory_recalled",
            "seq": 2,
            "params": {
                "namespace": [],
                "data": {"payload": {"memories": [{"id": "m3", "content": "wire private body"}]}},
            },
        },
    )

    assert direct["payload"]["memories"][0]["content"] == "<redacted>"
    assert stored["data"]["payload"]["memories"][0]["content"] == "<redacted>"
    assert wire["params"]["data"]["payload"]["memories"][0]["content"] == "<redacted>"


def test_canonical_stored_and_wire_memory_events_are_redacted() -> None:
    stored = {
        "id": "stored-memory-1",
        "seq": 7,
        "method": "custom",
        "namespace": [],
        "data": {
            "name": "memory_saved",
            "payload": {
                "scope": "user",
                "content": "stored private body",
                "reason": "stored private reason",
            },
        },
        "run_id": "run-1",
        "thread_id": "thread-1",
    }
    wire = {
        "type": "event",
        "method": "custom",
        "seq": 8,
        "params": {
            "namespace": [],
            "data": {
                "name": "moldy.memory_recalled",
                "payload": {"memories": [{"id": "m4", "content": "wire private body"}]},
            },
        },
    }

    stored_safe = project_and_redact_protocol_data("traces", [stored])[0]
    wire_safe = project_and_redact_protocol_data("protocol_replay", wire)

    assert stored_safe["data"]["payload"]["content"] == "<redacted>"
    assert stored_safe["data"]["payload"]["reason"] == "<redacted>"
    assert wire_safe["params"]["data"]["payload"]["memories"][0]["content"] == "<redacted>"


def test_nested_legacy_memory_event_in_state_is_redacted_without_mutation() -> None:
    state = {
        "wrapped": {
            "event": "memory_saved",
            "data": {"content": "private state body", "reason": "private state reason"},
        }
    }
    original = deepcopy(state)

    safe = project_and_redact_protocol_data("values", state)

    assert safe["wrapped"]["data"]["content"] == "<redacted>"
    assert safe["wrapped"]["data"]["reason"] == "<redacted>"
    assert state == original


def test_nested_custom_prefixed_memory_name_is_redacted_without_mutation() -> None:
    state = {
        "name": "custom:moldy.memory_recalled",
        "payload": {"memories": [{"id": "m5", "content": "private nested name body"}]},
    }
    original = deepcopy(state)

    safe = project_and_redact_protocol_data("values", state)

    assert safe["name"] == "custom:moldy.memory_recalled"
    assert safe["payload"]["memories"][0]["content"] == "<redacted>"
    assert state == original


def test_nested_custom_method_params_wrapper_is_redacted_without_mutation() -> None:
    state = {
        "wrapped": {
            "method": "custom:moldy.memory_recalled",
            "params": {
                "data": {
                    "payload": {"memories": [{"id": "m6", "content": "nested wire private body"}]}
                }
            },
        }
    }
    original = deepcopy(state)

    safe = project_and_redact_protocol_data("values", state)

    content = safe["wrapped"]["params"]["data"]["payload"]["memories"][0]["content"]
    assert content == "<redacted>"
    assert state == original


def test_non_event_data_with_memory_words_is_preserved() -> None:
    ordinary = {
        "report_kind": "memory_saved",
        "content": "ordinary report",
        "reason": "ordinary diagnostic",
    }
    original = deepcopy(ordinary)

    safe = project_and_redact_protocol_data("values", ordinary)

    assert safe == original
    assert ordinary == original
