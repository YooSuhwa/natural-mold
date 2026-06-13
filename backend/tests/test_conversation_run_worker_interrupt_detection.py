from __future__ import annotations

from app.agent_runtime import event_names
from app.services.conversation_run_interrupts import has_interrupt_events, interrupt_id_from_events


def test_legacy_interrupt_event_marks_worker_interrupted() -> None:
    events = [
        {
            "event": event_names.INTERRUPT,
            "data": {"interrupt_id": "legacy-1", "value": "approve?"},
        }
    ]

    assert has_interrupt_events(events)
    assert interrupt_id_from_events(events) == "legacy-1"


def test_protocol_input_requested_event_marks_worker_interrupted() -> None:
    events = [
        {
            "method": "input.requested",
            "data": {"interrupt_id": "intr-1", "payload": {"question": "approve?"}},
        }
    ]

    assert has_interrupt_events(events)
    assert interrupt_id_from_events(events) == "intr-1"
