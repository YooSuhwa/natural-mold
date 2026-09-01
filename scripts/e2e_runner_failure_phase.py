"""Finite, secret-free Playwright failure-phase annotation parsing."""

from __future__ import annotations

from enum import StrEnum
from typing import Final, Never


class FailurePhase(StrEnum):
    SETUP_AGENT = "setup_agent"
    OPEN_DRAFT = "open_draft"
    VERIFY_DRAFT_ROUTE = "verify_draft_route"
    INSTALL_PROMPT_OBSERVER = "install_prompt_observer"
    SUBMIT_PROMPT = "submit_prompt"
    WAIT_DRAFT_PROMOTION = "wait_draft_promotion"
    WAIT_PROMPT = "wait_prompt"
    WAIT_ASK_USER_CARD = "wait_ask_user_card"
    VERIFY_PROMPT_STABILITY = "verify_prompt_stability"
    SELECT_OPTION = "select_option"
    SUBMIT_DECISION = "submit_decision"
    WAIT_FINAL_RESPONSE = "wait_final_response"
    VERIFY_FINAL_PROMPT = "verify_final_prompt"
    VERIFY_ERROR_COLLECTORS = "verify_error_collectors"
    CLEANUP_PARENT_AGENT = "cleanup_parent_agent"
    CLEANUP_CHILD_AGENT = "cleanup_child_agent"
    COMPLETE = "complete"


FAILURE_PHASE_ANNOTATION_TYPE: Final = "moldy.failure-phase.v1"
FAILURE_PHASE_BY_VALUE: Final[dict[str, FailurePhase]] = {
    phase.value: phase for phase in FailurePhase
}


class FailurePhaseError(ValueError):
    """Stable rejection that never reflects an untrusted annotation."""


def _fail() -> Never:
    raise FailurePhaseError("invalid_failure_phase")


def _phase(value: object) -> FailurePhase | None:
    return FAILURE_PHASE_BY_VALUE.get(value) if isinstance(value, str) else None


def extract_failure_phase(result: object) -> FailurePhase | None:
    """Read one exact reserved result annotation without retaining raw metadata."""
    if not isinstance(result, dict) or "annotations" not in result:
        return None
    annotations = result.get("annotations")
    if not isinstance(annotations, list):
        _fail()
    phase: FailurePhase | None = None
    for annotation in annotations:
        if (
            not isinstance(annotation, dict)
            or annotation.get("type") != FAILURE_PHASE_ANNOTATION_TYPE
        ):
            continue
        if set(annotation) != {"type", "description"} or phase is not None:
            _fail()
        parsed = _phase(annotation.get("description"))
        if parsed is None:
            _fail()
        phase = parsed
    return phase


def parse_failure_phase(value: object) -> FailurePhase:
    """Validate the optional persisted scalar when it is present."""
    phase = _phase(value)
    if phase is None:
        _fail()
    return phase
