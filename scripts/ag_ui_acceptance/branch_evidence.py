"""Finite diagnostics for the two ordinary chat branch journeys."""

from collections.abc import Callable
from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

BRANCH_ATTACHMENT = "chat-branch-observation"
BRANCH_TITLES = {
    "regenerating an assistant reply forks a sibling branch and the picker navigates",
    "editing a user message forks a new branch",
}


def _http_status_or_unobserved(value: int) -> int:
    if value != 0 and not 100 <= value <= 599:
        raise ValueError("Invalid HTTP status")
    return value


type HttpStatus = Annotated[int, AfterValidator(_http_status_or_unobserved)]
type Count = Annotated[int, Field(ge=0, le=64)]


class BranchObservation(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, extra="forbid", strict=True)

    stage: Annotated[int, Field(ge=0, le=8)]
    send_status: HttpStatus
    edit_status: HttpStatus
    regenerate_status: HttpStatus
    snapshot_count: Count
    sibling_count: Count
    selected_leaf_listed: bool
    has_checkpoint: bool


def branch_observation(
    filename: str,
    title: str,
    group: str,
    result: dict,
    owned_root: Path,
    read_body: Callable[[dict, Path, str], bytes],
) -> dict:
    if (
        filename != "chat-interactions.spec.ts"
        or title not in BRANCH_TITLES
        or group not in {"ordinary-regressions-ag_ui", "ordinary-regressions-langgraph_v3"}
    ):
        return {}
    attachments = result.get("attachments")
    if not isinstance(attachments, list):
        return {}
    matching = [
        a for a in attachments if isinstance(a, dict) and a.get("name") == BRANCH_ATTACHMENT
    ]
    if len(matching) != 1 or matching[0].get("contentType") != "application/json":
        return {}
    try:
        body = read_body(matching[0], owned_root, "branch")
        summary = BranchObservation.model_validate_json(body)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        return {}
    return {BRANCH_ATTACHMENT: summary.model_dump(by_alias=True)}
