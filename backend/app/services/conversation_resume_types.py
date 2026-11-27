"""Interrupt responses and metadata shared by conversation run transports."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.agent_runtime.protocol_events import ProtocolInterrupt as ThreadInterrupt
from app.services.conversation_resume_decisions import auth_decision_record


@dataclass(frozen=True, slots=True)
class SubmittedInterruptResponse:
    interrupt_id: str
    namespace: tuple[str, ...]
    response: Any


@dataclass(frozen=True, slots=True)
class ResumePayload:
    input_payload: Any
    interrupt_id: str | None
    submitted: tuple[SubmittedInterruptResponse, ...]


def resume_decision_metadata(
    resume: ResumePayload, pending_interrupts: Sequence[ThreadInterrupt]
) -> dict[str, Any]:
    """Run metadata recording ``auth_required`` answers (retry/skip/cancel, attempt)."""

    values = {interrupt["id"]: interrupt["value"] for interrupt in pending_interrupts}
    records = [
        record
        for submitted in resume.submitted
        if (
            record := auth_decision_record(
                submitted.interrupt_id, submitted.response, values.get(submitted.interrupt_id)
            )
        )
        is not None
    ]
    return {"resume_decisions": records} if records else {}


def resume_run_interrupt_id(
    resume: ResumePayload,
    parent_interrupt_id: str | None,
) -> str | None:
    if parent_interrupt_id is not None:
        for submitted in resume.submitted:
            if submitted.interrupt_id == parent_interrupt_id:
                return parent_interrupt_id
    return resume.interrupt_id


__all__ = [
    "ResumePayload",
    "SubmittedInterruptResponse",
    "ThreadInterrupt",
    "resume_decision_metadata",
    "resume_run_interrupt_id",
]
