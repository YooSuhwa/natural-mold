from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.routers.conversation_agent_protocol_contracts import AgentCommandParams
from app.routers.conversation_agent_protocol_interrupts import ThreadInterrupt


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


@dataclass(frozen=True, slots=True)
class ResumeValidationError:
    code: str
    message: str


def build_resume_payload(params: AgentCommandParams) -> ResumePayload:
    if params.responses is not None:
        submitted = tuple(
            SubmittedInterruptResponse(
                interrupt_id=entry.interrupt_id,
                namespace=tuple(entry.namespace),
                response=entry.response,
            )
            for entry in params.responses
        )
        return ResumePayload(
            input_payload={entry.interrupt_id: entry.response for entry in params.responses},
            interrupt_id=submitted[0].interrupt_id,
            submitted=submitted,
        )
    submitted = (
        (
            SubmittedInterruptResponse(
                interrupt_id=params.interrupt_id,
                namespace=tuple(params.namespace or []),
                response=params.response,
            ),
        )
        if params.interrupt_id
        else ()
    )
    return ResumePayload(
        input_payload=params.response,
        interrupt_id=params.interrupt_id,
        submitted=submitted,
    )


def validate_resume_payload(
    resume: ResumePayload,
    pending_interrupts: Sequence[ThreadInterrupt],
) -> ResumeValidationError | None:
    if not pending_interrupts:
        return None

    pending_by_id = {interrupt["id"]: tuple(interrupt["ns"]) for interrupt in pending_interrupts}
    seen_ids: set[str] = set()
    for submitted in resume.submitted:
        if submitted.interrupt_id in seen_ids:
            return ResumeValidationError(
                code="STALE_INTERRUPT",
                message="input.respond contains duplicate interrupt_id",
            )
        seen_ids.add(submitted.interrupt_id)
        pending_namespace = pending_by_id.get(submitted.interrupt_id)
        if pending_namespace is None:
            return ResumeValidationError(
                code="STALE_INTERRUPT",
                message="input.respond interrupt_id is not pending",
            )
        if submitted.namespace != pending_namespace:
            return ResumeValidationError(
                code="STALE_INTERRUPT",
                message="input.respond namespace does not match the pending interrupt",
            )
    return None


def resume_run_interrupt_id(
    resume: ResumePayload,
    parent_interrupt_id: str | None,
) -> str | None:
    if parent_interrupt_id is not None:
        for submitted in resume.submitted:
            if submitted.interrupt_id == parent_interrupt_id:
                return parent_interrupt_id
    return resume.interrupt_id
