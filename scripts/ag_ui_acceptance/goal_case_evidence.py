"""Project only the two scoped attachments from the goal browser journey."""

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, StrictInt
from pydantic.alias_generators import to_camel

GOAL_CASE_FILE = "chat-goal-steer-notice.spec.ts"
GOAL_CASE_TITLE = (
    "a goal edited during a tool call delivers one verified result after a fresh sign-in"
)
_LIVE = "goal-live-child-proof"
_JOURNEY = "goal-journey-proof"
_RUN_STATES = (
    "queued",
    "running",
    "interrupted",
    "canceling",
    "canceled",
    "completed",
    "failed",
    "stale",
)


def _known_run_state(value: object) -> str | None:
    return value if isinstance(value, str) and value in _RUN_STATES else None


def _missing_response(value: object) -> object:
    return None if type(value) is int and value == 0 else value


HttpStatus = Annotated[StrictInt, Field(ge=100, le=599)]
ObservedStatus = Annotated[HttpStatus | None, BeforeValidator(_missing_response)]
Count = Annotated[StrictInt, Field(ge=0, le=2**31 - 1)]


class _GoalProof(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, strict=True, extra="ignore")

    conversation_id: UUID
    goal_id: UUID
    original_run: UUID


class _LiveChild(_GoalProof):
    home_org_id: UUID
    scoped_goal_status: ObservedStatus
    scoped_run_status: ObservedStatus
    scoped_snapshot_status: ObservedStatus
    run_status: Annotated[str | None, BeforeValidator(_known_run_state)] = None
    child_count: Count
    bound_task_calls: Count
    eligible_briefs: Count
    pill_count: Count
    expected_brief_visible: StrictBool
    expected_brief_on_parent: StrictBool
    steer_button_visible: StrictBool


class _Journey(_GoalProof):
    org_id: UUID
    artifact_id: UUID
    steer_id: UUID
    steer_status: Literal["applied"]
    goal_version: Annotated[StrictInt, Field(ge=1, le=2**31 - 1)]
    automatic_followups: Count
    fresh_login: StrictBool
    anonymous_status_before_login: HttpStatus
    same_single_notice_after_login: StrictBool
    verified_run: UUID
    version_id: UUID
    sha256: Annotated[str, Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")]
    notice_id: UUID
    completion_notices: Count
    foreign_goal_notices: Count
    foreign_version_status: HttpStatus
    foreign_destination_status: HttpStatus


def goal_case_observations(
    result: dict,
    owned_root: Path,
    read_body: Callable[[dict, Path, str], bytes],
) -> dict:
    attachments = result.get("attachments", [])
    if not isinstance(attachments, list):
        return {}
    observed = {}
    for name, schema in ((_LIVE, _LiveChild), (_JOURNEY, _Journey)):
        matches = [a for a in attachments if isinstance(a, dict) and a.get("name") == name]
        if len(matches) != 1 or matches[0].get("contentType") != "application/json":
            continue
        try:
            body = read_body(matches[0], owned_root, "goal")
            evidence = schema.model_validate_json(body)
        except (KeyError, OSError, TypeError, ValueError, RuntimeError):
            continue
        observed[name] = evidence.model_dump(mode="json", by_alias=True)

    if journey := observed.get(_JOURNEY):
        live = observed.get(_LIVE)
        if (
            not live
            or live["homeOrgId"] != journey["orgId"]
            or any(live[key] != journey[key] for key in ("conversationId", "goalId", "originalRun"))
        ):
            del observed[_JOURNEY]
    return observed
