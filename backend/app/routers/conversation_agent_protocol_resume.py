from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from fastapi.responses import JSONResponse

from app.routers.conversation_agent_protocol_contracts import (
    AgentCommandParams,
    AgentCommandRequest,
    command_error,
)
from app.routers.conversation_resume_decisions import decision_problem
from app.services.conversation_resume_types import (
    ResumePayload,
    SubmittedInterruptResponse,
    ThreadInterrupt,
    resume_decision_metadata,
    resume_run_interrupt_id,
)

__all__ = [
    "ResumePayload",
    "ResumeValidationError",
    "SubmittedInterruptResponse",
    "ThreadInterrupt",
    "build_resume_payload",
    "resume_decision_metadata",
    "resume_run_interrupt_id",
    "validate_resume_payload",
]


@dataclass(frozen=True, slots=True)
class ResumeValidationError:
    code: str
    message: str
    status_code: int = 200

    def response(self, command: AgentCommandRequest) -> JSONResponse:
        return command_error(
            command, code=self.code, message=self.message, status_code=self.status_code
        )


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
    values = {interrupt["id"]: interrupt["value"] for interrupt in pending_interrupts}
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
        # G-06: the answer must be allowed by the pending interrupt (11.5).
        problem = decision_problem(submitted.response, values[submitted.interrupt_id])
        if problem is not None:
            return ResumeValidationError(*problem)
    return None
